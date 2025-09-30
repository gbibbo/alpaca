#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sidecar que reexpone /metrics con Content-Type estricto "text/plain; version=0.0.4; charset=utf-8"
- Soporta GET y HEAD
- Fuerza Accept hacia el origen para 0.0.4 (y acepta OpenMetrics como fallback)
- Evita problemas de IPv6/localhost: escucha en 0.0.0.0 (todas las interfaces)
"""
import argparse
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

STRICT_CT = "text/plain; version=0.0.4; charset=utf-8"
ACCEPT = ",".join([
    "application/openmetrics-text; version=1.0.0; charset=utf-8;q=1.0",
    "text/plain; version=0.0.4; charset=utf-8;q=0.9",
    "*/*;q=0.1",
])

ORIGIN_URL = None
TIMEOUT = 3.0

class Handler(BaseHTTPRequestHandler):
    server_version = "metrics-sidecar/1.0"

    def log_message(self, fmt, *args):
        # Logs mínimos al stderr (visible en out/sidecar_*.log)
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0],
                                               time.strftime("%d/%b/%Y %H:%M:%S"),
                                               fmt % args))

    def _fetch_origin(self, method: str):
        assert ORIGIN_URL, "Origin not configured"
        req = Request(ORIGIN_URL, method="GET")
        req.add_header("Accept", ACCEPT)
        # Siempre pedimos GET al origen; si es HEAD, sólo copiamos headers y no enviamos body
        try:
            with urlopen(req, timeout=TIMEOUT) as resp:
                code = resp.getcode()
                raw = resp.read() if method == "GET" else b""
                return code, raw
        except HTTPError as e:
            return e.code, b""
        except URLError as e:
            self.log_message("origin error: %s", e)
            return 502, b""

    def _respond(self, code: int, body: bytes, include_body: bool):
        self.send_response(code)
        self.send_header("Content-Type", STRICT_CT)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if include_body and code == 200:
            self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") != "/metrics":
            self.send_error(404)
            return
        code, body = self._fetch_origin("GET")
        self._respond(code, body, include_body=True)

    def do_HEAD(self):
        if self.path.rstrip("/") != "/metrics":
            self.send_error(404)
            return
        code, _ = self._fetch_origin("HEAD")
        self._respond(code, b"", include_body=False)

def main():
    global ORIGIN_URL
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="URL origen /metrics (p.ej. http://127.0.0.1:8011/metrics)")
    ap.add_argument("--port", type=int, required=True, help="Puerto local del sidecar (p.ej. 9911)")
    args = ap.parse_args()
    ORIGIN_URL = args.source

    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"[reexport] listening on :{args.port} -> {ORIGIN_URL}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()

if __name__ == "__main__":
    main()
