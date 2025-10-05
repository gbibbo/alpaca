# scripts/run_with_metrics.py
#!/usr/bin/env python3
"""
Arranca un EXPORTER Prometheus en este proceso y luego ejecuta el módulo destino.
Uso:
  python -u scripts/run_with_metrics.py --module apps.risk_manager.main --port 8011
  python -u scripts/run_with_metrics.py --module apps.executor.main     --port 8012

NOTA: start_http_server expone las métricas en la RAÍZ "/", no en "/metrics".
"""
import argparse
import os
import sys
import runpy
from prometheus_client import start_http_server

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--module", required=True, help="Módulo a ejecutar (p.ej. apps.risk_manager.main)")
    p.add_argument("--port", type=int, default=int(os.getenv("METRICS_PORT", "8011")), help="Puerto HTTP para métricas")
    args = p.parse_args()

    try:
        start_http_server(args.port)  # métricas en http://localhost:<port>/
    except OSError as e:
        print(f"[metrics] ERROR al abrir puerto {args.port}: {e}", file=sys.stderr)
        sys.exit(98)

    # Ejecuta el módulo como script en este mismo proceso
    runpy.run_module(args.module, run_name="__main__")

if __name__ == "__main__":
    main()
