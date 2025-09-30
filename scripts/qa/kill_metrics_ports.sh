# scripts/qa/kill_metrics_ports.sh  (igual que antes)
#!/usr/bin/env bash
set -euo pipefail
for p in 8011 8012 9911 9912; do
  if command -v fuser >/dev/null 2>&1; then
    fuser -k ${p}/tcp 2>/dev/null || true
  elif command -v lsof >/dev/null 2>&1; then
    kill -9 $(lsof -ti:${p}) 2>/dev/null || true
  fi
done
echo "[kill-ports] listo"
