#!/usr/bin/env bash
# Launch the SeVim HTTP shim. Binds to loopback only.
set -eu
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a; . ./.env; set +a
fi

VENV_PY="${SEVIM_PY:-.venv/bin/python}"
LOG="${SEVIM_SERVICE_LOG:-/tmp/sevim_service.log}"

nohup "$VENV_PY" -m uvicorn service.app:app \
  --host 127.0.0.1 --port 8003 --log-level info \
  > "$LOG" 2>&1 &

echo "launched PID $!"
echo "log: $LOG"
echo "probe: curl -s http://127.0.0.1:8003/health"
