# scripts/qa/metrics_smoke_strict.sh  (ACTUALIZADO: inspección de headers con GET)
#!/usr/bin/env bash
set -euo pipefail
ENDPOINTS=(
  "http://localhost:9911/metrics"  # sidecar risk
  "http://localhost:9912/metrics"  # sidecar executor
)
echo "[metrics-smoke-strict] Checking strict Content-Type + body..."
for ep in "${ENDPOINTS[@]}"; do
  headers=$(curl -s -D - -H "Accept: text/plain; version=0.0.4" -o /dev/null "$ep" || true)
  code=$(printf "%s" "$headers" | awk '/^HTTP/{print $2}' | tail -1)
  ct=$(printf "%s" "$headers" | awk -F': ' 'tolower($1)=="content-type"{print $2}' | tr -d '\r')
  if [ "$code" != "200" ]; then echo "❌ $ep no responde 200"; exit 1; fi
  if ! echo "$ct" | grep -qi "text/plain; *version=0.0.4"; then
    echo "❌ $ep Content-Type no es 0.0.4 -> '$ct'"; exit 2
  fi
  head=$(curl -s "$ep" | head -n 2)
  if ! echo "$head" | grep -Eq '^# (HELP|TYPE)'; then
    echo "❌ $ep cuerpo no parece Prometheus -> '$head'"; exit 3
  fi
  echo "✅ $ep OK (CT=$ct)"
done
echo "✅ metrics-smoke-strict OK"
