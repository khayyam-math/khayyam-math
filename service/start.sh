#!/usr/bin/env bash
# Launch the SeVim HTTP shim. Binds to loopback only.
set -eu
cd "$(dirname "$0")/.."

# Override via the environment to point at any Python interpreter with
# sevim + uvicorn importable; defaults to whatever `python3` resolves to.
VENV_PY="${SEVIM_PY:-python3}"
LOG="${SEVIM_SERVICE_LOG:-/tmp/sevim_service.log}"

nohup "$VENV_PY" -m uvicorn service.app:app \
  --host 127.0.0.1 --port 8003 --log-level info \
  > "$LOG" 2>&1 &

echo "launched PID $!"
echo "log: $LOG"
echo "probe: curl -s http://127.0.0.1:8003/health"
