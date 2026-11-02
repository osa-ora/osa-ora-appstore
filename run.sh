#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
ACTION="${1:-}"
PORT="${PORT:-8780}"
URL="http://127.0.0.1:${PORT}"
PID_FILE=".launcher.pid"
LOG_DIR="logs"
VENV=".venv"

usage() { echo "Usage: ./run.sh {start|stop|status}"; }
read_pid() { [[ -f "$PID_FILE" ]] && cat "$PID_FILE" || true; }
is_running() {
  local pid; pid="$(read_pid)"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && curl -fsS "$URL/api/config" >/dev/null 2>&1
}
case "$ACTION" in
  start)
    if is_running; then echo "Osa Ora App Store is already running at $URL"; python3 -c "import webbrowser; webbrowser.open('$URL')" >/dev/null 2>&1 || true; exit 0; fi
    PYTHON="${PYTHON:-python3}"
    command -v "$PYTHON" >/dev/null 2>&1 || { echo "Python 3 is required" >&2; exit 1; }
    [[ -d "$VENV" ]] || "$PYTHON" -m venv "$VENV"
    "$VENV/bin/python" -m pip install --quiet --upgrade pip
    "$VENV/bin/python" -m pip install --quiet -r requirements.txt
    mkdir -p "$LOG_DIR"
    nohup "$VENV/bin/uvicorn" app.main:app --host 127.0.0.1 --port "$PORT" >"$LOG_DIR/launcher.log" 2>&1 &
    echo $! > "$PID_FILE"
    READY=0
    for _ in {1..40}; do
      if curl -fsS "$URL/api/config" >/dev/null 2>&1; then READY=1; break; fi
      sleep 0.5
    done
    if [[ "$READY" != 1 ]]; then echo "Launcher failed to start. Check $LOG_DIR/launcher.log" >&2; exit 1; fi
    echo "Osa Ora App Store started: $URL"
    case "$(uname -s)" in
      Darwin) open "$URL" >/dev/null 2>&1 || true ;;
      Linux) if command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 & fi ;;
    esac
    ;;
  status)
    if is_running; then echo "Osa Ora App Store is running at $URL (PID $(read_pid))"; exit 0; fi
    echo "Osa Ora App Store is stopped"; exit 1
    ;;
  stop)
    PID="$(read_pid)"
    if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
      kill -TERM "$PID" 2>/dev/null || true
      for _ in {1..20}; do kill -0 "$PID" 2>/dev/null || break; sleep 0.25; done
      kill -KILL "$PID" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
    echo "Osa Ora App Store stopped"
    ;;
  *) usage; exit 2 ;;
esac
