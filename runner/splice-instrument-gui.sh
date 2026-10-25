#!/usr/bin/env bash
set -euo pipefail

PORT="${SPLICE_PORT:-38241}"
SHARE_DIR="${SPLICE_SHARE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export SPLICE_FRONTEND_DIR="${SPLICE_FRONTEND_DIR:-$SHARE_DIR/frontend}"
PROFILE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/splice-instruments/chrome-profile"

mkdir -p "$PROFILE_DIR"

SERVER_SCRIPT="$SHARE_DIR/runner/server.py"
if [ ! -f "$SERVER_SCRIPT" ] && [ -f "$SHARE_DIR/server.py" ]; then
  SERVER_SCRIPT="$SHARE_DIR/server.py"
fi

# Launch Python static server
python3 "$SERVER_SCRIPT" &
SERVER_PID=$!

cleanup() {
  kill -TERM "$SERVER_PID" 2>/dev/null || true
  wait "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Wait for server port to be ready
for i in {1..20}; do
  if nc -z 127.0.0.1 "$PORT" 2>/dev/null; then
    break
  fi
  sleep 0.1
done

# Run native Google Chrome in standalone application window mode
/run/current-system/sw/bin/google-chrome-stable \
  --app="http://127.0.0.1:${PORT}/index.html" \
  --window-size=1280,820 \
  --user-data-dir="$PROFILE_DIR" \
  --no-first-run \
  --no-default-browser-check \
  --disable-features=Translate \
  --disable-extensions \
  --disable-component-update &
CHROME_PID=$!

wait "$CHROME_PID" || true
