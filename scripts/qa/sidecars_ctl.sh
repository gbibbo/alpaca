#!/usr/bin/env bash
set -euo pipefail

# Control simple de sidecars: start|stop|restart
# Usa 127.0.0.1 para evitar resolución ::1 en "localhost"
RISK_SRC="${RISK_SRC:-http://127.0.0.1:8011/metrics}"
EXEC_SRC="${EXEC_SRC:-http://127.0.0.1:8012/metrics}"
RISK_PORT="${RISK_PORT:-9911}"
EXEC_PORT="${EXEC_PORT:-9912}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="$ROOT_DIR/out"
mkdir -p "$OUT_DIR"

risk_pid_file="$OUT_DIR/sidecar_risk.pid"
exec_pid_file="$OUT_DIR/sidecar_exec.pid"

start_one() {
  local name="$1" src="$2" port="$3" pidf="$4" logf="$5"
  # readiness del origen
  local code
  code="$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 "$src" || true)"
  if [[ "$code" != "200" ]]; then
    echo "[sidecars] $name origin not ready ($src -> $code)"
    return 1
  fi
  # arrancar
  nohup python -u "$ROOT_DIR/scripts/qa/reexport_metrics.py" --source "$src" --port "$port" > "$logf" 2>&1 &
  echo $! > "$pidf"
  echo "[sidecars] started $name on :$port -> $src (pid $(cat "$pidf"))"
}

stop_one() {
  local name="$1" port="$2" pidf="$3"
  if [[ -f "$pidf" ]]; then
    local pid; pid="$(cat "$pidf" || true)"
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "[sidecars] stopping $name (pid $pid)"
      kill "$pid" || true
      sleep 0.2
    fi
    rm -f "$pidf"
  fi
  # forzar por puerto
  local pids
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN || true)"
  if [[ -n "$pids" ]]; then
    echo "[sidecars] force-kill $name on :$port ($pids)"
    kill -9 $pids || true
  fi
}

status_one() {
  local name="$1" port="$2"
  local code
  code="$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 "http://127.0.0.1:${port}/metrics" || true)"
  echo "[sidecars] $name :$port -> code=$code"
}

cmd="${1:-help}"
case "$cmd" in
  start)
    start_one "risk" "$RISK_SRC" "$RISK_PORT" "$risk_pid_file" "$OUT_DIR/sidecar_risk.log" || true
    start_one "exec" "$EXEC_SRC" "$EXEC_PORT" "$exec_pid_file" "$OUT_DIR/sidecar_exec.log" || true
    ;;
  stop)
    stop_one "risk" "$RISK_PORT" "$risk_pid_file"
    stop_one "exec" "$EXEC_PORT" "$exec_pid_file"
    ;;
  restart)
    "$0" stop
    "$0" start
    ;;
  status)
    status_one "risk" "$RISK_PORT"
    status_one "exec" "$EXEC_PORT"
    ;;
  *)
    echo "uso: $0 {start|stop|restart|status}"
    exit 1
    ;;
esac
