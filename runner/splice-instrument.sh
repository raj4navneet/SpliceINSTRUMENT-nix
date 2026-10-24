#!/usr/bin/env bash
set -euo pipefail

UID_NUM="$(id -u)"
LOCK_FILE="/run/user/${UID_NUM}/splice-instruments.lock"
exec 200>"$LOCK_FILE"

# Prevent duplicate launches from racing or killing the active session
if ! flock -n 200; then
  echo "Splice Instruments is already running."
  exit 0
fi

# Configuration paths
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/splice-instruments"
WINEPREFIX="${WINEPREFIX:-$DATA_DIR}"
SHARE_DIR="${SPLICE_SHARE_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"

# Ensure isolated prefix exists
if [ ! -d "$WINEPREFIX/drive_c/Program Files/Splice/Splice INSTRUMENT" ]; then
  if [ -d "$HOME/backups/wine/wine-working-prefix-20261024_2020" ]; then
    echo "==> Bootstrapping isolated prefix at $WINEPREFIX from verified backup..."
    mkdir -p "$WINEPREFIX"
    cp -a "$HOME/backups/wine/wine-working-prefix-20261024_2020"/* "$WINEPREFIX/" 2>/dev/null || true
  elif [ -d "$HOME/.wine/drive_c/Program Files/Splice/Splice INSTRUMENT" ]; then
    echo "==> Bootstrapping isolated prefix at $WINEPREFIX from ~/.wine..."
    mkdir -p "$WINEPREFIX"
    cp -a "$HOME/.wine"/* "$WINEPREFIX/" 2>/dev/null || true
  fi
fi

WINE_BIN="${WINE_BIN:-wine}"
WINESERVER_BIN="${WINESERVER_BIN:-wineserver}"
WINEBOOT_BIN="${WINEBOOT_BIN:-wineboot}"

cleanup() {
  local exit_code=$?
  if [ -x "$(command -v "$WINESERVER_BIN")" ]; then
    WINEPREFIX="$WINEPREFIX" "$WINESERVER_BIN" -k 2>/dev/null || true
  fi
  flock -u 200 2>/dev/null || true
  exit "$exit_code"
}

trap cleanup EXIT INT TERM

# Export DXVK DirectComposition dummy swapchain & Chromium sandbox flags
export DXVK_CONFIG="dxgi.enableDummyCompositionSwapchain=True"
export WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS="--no-sandbox --disable-gpu-sandbox --disable-gpu --disable-gpu-compositing --disable-direct-composition --disable-features=RendererCodeIntegrity"

# Audio & Desktop integration
export PULSE_SERVER="unix:/run/user/${UID_NUM}/pulse/native"
export WINE_FORCE_PORTAL=1
export WINEDEBUG="-all,fixme-all"
export WINEPREFIX="$WINEPREFIX"

# Self-healing: Initialize prefix if new
if [ ! -d "$WINEPREFIX/drive_c/windows" ] && [ -x "$(command -v "$WINEBOOT_BIN")" ]; then
  echo "==> Initializing Wine prefix at $WINEPREFIX..."
  "$WINEBOOT_BIN" --init
fi

SPLICE_DIR="$WINEPREFIX/drive_c/Program Files/Splice"
SPLICE_EXE="$SPLICE_DIR/Splice INSTRUMENT/Splice INSTRUMENT.exe"

if [ ! -f "$SPLICE_EXE" ]; then
  echo "Error: Splice INSTRUMENT executable not found at $SPLICE_EXE" >&2
  echo "Please install Splice Instruments or set WINEPREFIX to an existing installation." >&2
  exit 1
fi

# Self-healing: Apply declarative registry patches from linkFarm
if [ -d "$SHARE_DIR/registry-patches" ] && [ -x "$(command -v "$WINE_BIN")" ]; then
  for regfile in "$SHARE_DIR/registry-patches"/*.reg; do
    if [ -f "$regfile" ]; then
      "$WINE_BIN" regedit /S "$regfile" 2>/dev/null || true
    fi
  done
fi

# Self-healing: Ensure AppData settings are seeded
USER_ROAMING="$WINEPREFIX/drive_c/users/$(whoami)/AppData/Roaming/com.splice.INSTRUMENT"
if [ ! -f "$USER_ROAMING/INSTRUMENT.settings" ] && [ -f "$SHARE_DIR/seed-config/INSTRUMENT.settings" ]; then
  mkdir -p "$USER_ROAMING"
  cp "$SHARE_DIR/seed-config/INSTRUMENT.settings" "$USER_ROAMING/INSTRUMENT.settings"
fi

# Self-healing: Ensure sample database includes missing IR ID 1608633 to prevent Spitfire Error #4
COMMON_IR_DB="$WINEPREFIX/drive_c/users/$(whoami)/Splice/INSTRUMENT/INSTRUMENT Common/Samples/INSTRUMENT_Common_IR.db"
if [ -f "$COMMON_IR_DB" ] && [ -x "$(command -v sqlite3)" ]; then
  sqlite3 "$COMMON_IR_DB" "INSERT OR IGNORE INTO Files (ID, Container) VALUES (1608633, 1);" 2>/dev/null || true
fi

cd "$WINEPREFIX/drive_c/Program Files/Splice/Splice INSTRUMENT"
exec "$WINE_BIN" "$SPLICE_EXE" "$@"
