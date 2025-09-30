# (PEGA AQUÍ íntegro el contenido de sitecustomize.py del bloque de arriba)
# sitecustomize.py
"""
Parche idempotente para Prometheus en procesos que llaman start_http_server()
más de una vez. Si el puerto ya está siendo usado *por este mismo proceso*, no
vuelve a bindear (evita OSError: [Errno 98] Address already in use).

Se aplica automáticamente porque Python importa `sitecustomize` al arrancar.
"""

from __future__ import annotations
import threading
from typing import Dict, Tuple, Optional
import socket

# Importamos símbolos del cliente oficial
from prometheus_client import exposition as _expo  # start_http_server usa exposition.start_wsgi_server
from prometheus_client import start_http_server as _orig_start_http_server
from prometheus_client.exposition import make_wsgi_app

# Registro global de servidores WSGI levantados en este proceso
# clave: (addr, port)  -> valor: httpd (wsgiref server)
_servers: Dict[Tuple[str, int], object] = {}

# Lock para evitar carreras si dos threads intentan levantar el mismo puerto a la vez
_servers_lock = threading.Lock()


def _start_wsgi_server_safe(port: int, addr: str, app) -> Optional[object]:
    """
    Clona el comportamiento de exposition.start_wsgi_server pero:
      - Si ya tenemos un httpd vivo en (addr, port), reutiliza y NO re-bindea.
      - Si hay OSError EADDRINUSE, asume doble arranque y NO falla.
    Devuelve el httpd (para simetría), aunque el cliente oficial no lo usa.
    """
    key = (addr, port)
    with _servers_lock:
        if key in _servers:
            # Ya existe: idempotente
            return _servers[key]

        from wsgiref.simple_server import make_server
        try:
            httpd = make_server(addr, port, app)
        except OSError as e:
            # 98 (Linux) / 48 (macOS): Address already in use
            if getattr(e, "errno", None) in (98, 48):
                # Doble arranque: silenciamos y consideramos servidor ya activo.
                return _servers.get(key)
            raise

        t = threading.Thread(
            target=httpd.serve_forever,
            name=f"metrics-server-{port}",
            daemon=True,
        )
        t.start()
        _servers[key] = httpd
        return httpd


def _start_http_server_safe(port: int, addr: str = "") -> None:
    """
    Reemplazo seguro de prometheus_client.start_http_server.
    Mismo API, pero idempotente en (addr, port).
    """
    app = make_wsgi_app()
    _start_wsgi_server_safe(port, addr or "0.0.0.0", app)


# ---- Monkey patches ---------------------------------------------------------

# 1) Parchear el nivel bajo que usa start_http_server internamente
_expo.start_wsgi_server = _start_wsgi_server_safe  # type: ignore[attr-defined]

# 2) Parchear también el helper de alto nivel por si alguien lo llama directo
import prometheus_client as _pc
_pc.start_http_server = _start_http_server_safe  # type: ignore[attr-defined]

# Nota: El cliente oficial ya expone en "text/plain; version=0.0.4; charset=utf-8"
# para el formato de texto de Prometheus 0.0.4, así que no tocamos cabeceras aquí.
# Referencia: client_python y TextFormat.CONTENT_TYPE_004 en Prometheus. 
# --- BEGIN APPEND: metrics_helpers compatibility shim ---
# Hace a lib.metrics_helpers._start_http_server_safe tolerante a "registry="
# y lo vuelve idempotente por (port, addr).
def _patch_metrics_helpers():
    import threading
    try:
        import lib.metrics_helpers as mh  # tu módulo real
    except Exception:
        return  # si aún no existe, no rompemos el arranque

    _lock = threading.Lock()
    _started = set()

    def _start_http_server_safe(port, addr="0.0.0.0", registry=None):
        # Aceptamos "registry" aunque no lo usemos; el client oficial usa REGISTRY global.
        from prometheus_client import start_http_server
        key = (port, addr)
        with _lock:
            if key in _started:
                return
            _started.add(key)
        try:
            # Firma actual documentada: start_http_server(port, addr="0.0.0.0", ...)
            start_http_server(port, addr=addr)
        except TypeError:
            # Por si hay una variante más vieja que solo acepta (port)
            start_http_server(port)

    # Parchea incondicionalmente para garantizar compatibilidad futura/pasada
    try:
        mh._start_http_server_safe = _start_http_server_safe
    except Exception:
        pass

try:
    _patch_metrics_helpers()
except Exception:
    # Nunca impidas el arranque por fallar el parche
    pass
# --- END APPEND ---
# --- BEGIN APPEND: import-hook to patch lib.metrics_helpers safely ---
# Parcha lib.metrics_helpers en el momento del import para aceptar "registry"
# y hacer idempotente el arranque del servidor de métricas.
import sys, importlib, importlib.util, importlib.abc, threading

class _MHPatchedFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    TARGET = "lib.metrics_helpers"

    def __init__(self):
        self._real_loader = None

    def find_spec(self, fullname, path, target=None):
        if fullname != self.TARGET:
            return None
        spec = importlib.util.find_spec(fullname)
        if not spec or not spec.loader:
            return None
        self._real_loader = spec.loader
        # Devolvemos un spec con este loader (nosotros) para interceptar exec_module
        return importlib.util.spec_from_loader(fullname, self, origin=spec.origin)

    def create_module(self, spec):
        # Usar semántica por defecto (None)
        return None

    def exec_module(self, module):
        # Cargar el módulo real primero
        self._real_loader.exec_module(module)
        # Luego aplicamos el parche
        self._patch_module(module)

    @staticmethod
    def _patch_module(m):
        import inspect
        try:
            # Si ya acepta "registry", no hacemos nada (idempotente)
            if 'registry' in inspect.signature(m._start_http_server_safe).parameters:
                return
        except Exception:
            pass

        from prometheus_client import start_http_server
        _lock = threading.Lock()
        _started = set()

        def _start_http_server_safe(port, addr="0.0.0.0", registry=None):
            """Wrapper compatible: ignora 'registry' y evita EADDRINUSE por doble start."""
            key = (addr, int(port))
            with _lock:
                if key in _started:
                    return
                try:
                    start_http_server(int(port), addr)  # registry ignorado a propósito
                except OSError as e:
                    # 98 = Address already in use -> lo damos por bueno
                    if getattr(e, "errno", None) != 98:
                        raise
                _started.add(key)

        m._start_http_server_safe = _start_http_server_safe

# Instalar el import hook una sola vez al inicio
if not any(isinstance(f, _MHPatchedFinder) for f in getattr(sys, "meta_path", [])):
    sys.meta_path.insert(0, _MHPatchedFinder())
# --- END APPEND ---
