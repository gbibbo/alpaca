#!/usr/bin/env bash
set -euo pipefail

export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
export BUS_BACKEND="${BUS_BACKEND:-streams}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="$ROOT_DIR/out"
mkdir -p "$OUT_DIR"

RISK_METRICS="http://127.0.0.1:8011/metrics"
EXEC_METRICS="http://127.0.0.1:8012/metrics"

echo "[e2e] ping Redis..."
redis-cli -u "$REDIS_URL" PING >/dev/null

# Limpieza puertos/servicios previos (para evitar Errno 98)
for p in 8011 8012 9911 9912; do
  lsof -tiTCP:$p -sTCP:LISTEN | xargs -r kill -9 || true
done
pkill -f "apps/risk_manager/main.py"   || true
pkill -f "apps/executor/main.py"       || true
"$ROOT_DIR/scripts/qa/sidecars_ctl.sh" stop || true

echo "[e2e] starting risk_manager..."
METRICS_PORT=8011 python -u "$ROOT_DIR/apps/risk_manager/main.py" > "$OUT_DIR/risk.log" 2>&1 &
RPID=$!

echo "[e2e] starting executor..."
METRICS_PORT=8012 python -u "$ROOT_DIR/apps/executor/main.py"   > "$OUT_DIR/executor.log" 2>&1 &
EPID=$!

echo "[e2e] waiting origin metrics endpoints..."
for url in "$RISK_METRICS" "$EXEC_METRICS"; do
  for i in {1..30}; do
    code="$(curl -s -o /dev/null -w "%{http_code}" --max-time 1 "$url" || true)"
    [[ "$code" == "200" ]] && break
    sleep 0.3
  done
  ct="$(curl -s -D - -o /dev/null "$url" | awk -F': ' 'tolower($1)=="content-type"{print tolower($2)}' | tr -d '\r')"
  echo "  $url -> code=${code:-0} CT='${ct:-?}'"
  if [[ "$code" != "200" ]]; then
    echo "❌ origin $url not ready"; exit 1
  fi
done

echo "[e2e] running simulator (GOOGL daily quick)..."
# Si no tienes simulador, esto no falla: deja un marcador en Redis y continúa
redis-cli -u "$REDIS_URL" XADD signals * type signal data '{"symbol":"GOOGL","side":"BUY","confidence":0.7}' >/dev/null || true

echo "[e2e] starting strict sidecars..."
RISK_SRC="$RISK_METRICS" EXEC_SRC="$EXEC_METRICS" "$ROOT_DIR/scripts/qa/sidecars_ctl.sh" restart

# Validación estricta Content-Type + cuerpo Prometheus en sidecars
for port in 9911 9912; do
  for i in {1..30}; do
    code="$(curl -s -o /dev/null -w "%{http_code}" --max-time 1 "http://127.0.0.1:${port}/metrics" || true)"
    [[ "$code" == "200" ]] && break
    sleep 0.3
  done
  hdr="$(curl -s -D - -o /dev/null "http://127.0.0.1:${port}/metrics" | tr -d '\r')"
  ct="$(printf "%s\n" "$hdr" | awk -F': ' 'tolower($1)=="content-type"{print tolower($2)}')"
  head="$(curl -s "http://127.0.0.1:${port}/metrics" | sed -n '1,2p')"
  if [[ "$code" == "200" && "$ct" == "text/plain; version=0.0.4; charset=utf-8" && "$head" =~ \#\ HELP ]]; then
    echo "✅ http://127.0.0.1:${port}/metrics OK ($ct)"
  else
    echo "❌ sidecar :${port} FAIL (code=$code ct='$ct')"
    echo "[debug] headers:"
    echo "$hdr"
    exit 1
  fi
done

echo "E2E PASS"
