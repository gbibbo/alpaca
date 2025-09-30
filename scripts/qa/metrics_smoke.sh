# scripts/qa/metrics_smoke.sh
#!/usr/bin/env bash
set -euo pipefail

# Chequeo más tolerante: pasa si el cuerpo tiene formato Prometheus (# HELP/# TYPE),
# aunque el Content-Type no incluya 'version=0.0.4' (Prometheus usa content negotiation).
ENDPOINTS=(
  "http://localhost:8011/metrics"
  "http://localhost:8012/metrics"
)

echo "[metrics-smoke] Checking Prometheus format (lenient)..."
for ep in "${ENDPOINTS[@]}"; do
  code=$(curl -s -H "Accept: text/plain; version=0.0.4" -o /dev/null -w "%{http_code}" "$ep" || true)
  if [ "$code" != "200" ]; then
    echo "❌ $ep no responde 200"; exit 1
  fi

  ct=$(curl -sI "$ep" | awk -F': ' '/Content-Type/{print $2}' | tr -d '\r')
  head=$(curl -s -H "Accept: text/plain; version=0.0.4" "$ep" | head -n 3)

  if echo "$ct" | grep -qi "text/plain; *version=0.0.4"; then
    echo "  $ep -> OK (Content-Type estricto: $ct)"
  elif echo "$head" | grep -Eq '^# (HELP|TYPE)'; then
    echo "  $ep -> OK (lenient). CT='$ct' HEAD='$(echo "$head" | tr -d '\n')'"
  else
    echo "❌ $ep no parece exponer formato Prometheus. CT='$ct'"; exit 2
  fi
done

echo "✅ metrics-smoke OK"
