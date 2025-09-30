# scripts/qa/check_origin_metrics.sh  (NUEVO: diagnóstico rápido del origen 8011/8012)
#!/usr/bin/env bash
set -euo pipefail
ORIGINS=(
  "http://127.0.0.1:8011/metrics"
  "http://127.0.0.1:8012/metrics"
)
echo "[check-origin] probing origin metrics endpoints..."
for ep in "${ORIGINS[@]}"; do
  headers=$(curl -s -D - -H "Accept: text/plain; version=0.0.4" -o /dev/null "$ep" || true)
  code=$(printf "%s" "$headers" | awk '/^HTTP/{print $2}' | tail -1)
  ct=$(printf "%s" "$headers" | awk -F': ' 'tolower($1)=="content-type"{print $2}' | tr -d '\r')
  head=$(curl -s "$ep" | head -n 2 | tr -d '\r')
  echo "  $ep -> code=$code CT='$ct' HEAD='$head'"
done
