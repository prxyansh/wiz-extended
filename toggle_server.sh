#!/usr/bin/env bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# Check for running server process on port 8899 or python server.py
PID=$(lsof -ti :8899 || pgrep -f "python.*server.py" || true)

if [ -n "$PID" ]; then
  kill -9 $PID 2>/dev/null || true
  osascript -e 'display notification "WiZ Controller server stopped." with title "WiZ Controller" subtitle "Server Offline 🔴"'
  echo "WiZ Server stopped (PID $PID)."
else
  if [ ! -f "$DIR/.venv/bin/python" ]; then
    bash "$DIR/run.sh" &
  else
    nohup "$DIR/.venv/bin/python" "$DIR/server.py" > "$DIR/server.log" 2>&1 &
  fi
  sleep 1.5
  open "http://localhost:8899"
  osascript -e 'display notification "WiZ Controller started at http://localhost:8899" with title "WiZ Controller" subtitle "Server Online 🟢"'
  echo "WiZ Server started."
fi
