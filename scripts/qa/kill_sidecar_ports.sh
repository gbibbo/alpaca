# scripts/qa/kill_sidecar_ports.sh  (NUEVO)
#!/usr/bin/env bash
set -euo pipefail
# Solo libera los puertos de los SIDECARS (NO toca 8011/8012)
for p in 9911 9912; do
  if command -v fuser >/dev/null 2>&1; then
    fuser -k ${p}/tcp 2>/dev/null || true
  elif command -v lsof >/dev/null 2>&1; then
    kill -9 $(lsof -ti:${p}) 2>/dev/null || true
  fi
done
echo "[kill-sidecar-ports] listo"
