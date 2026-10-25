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
  if [ -n "${APP_PID:-}" ]; then
    kill -TERM "$APP_PID" 2>/dev/null || true
  fi
  if [ -x "$(command -v "$WINESERVER_BIN")" ]; then
    WINEPREFIX="$WINEPREFIX" "$WINESERVER_BIN" -k 2>/dev/null || true
  elif [ -n "${WINESERVER:-}" ] && [ -x "$WINESERVER" ]; then
    WINEPREFIX="$WINEPREFIX" "$WINESERVER" -k 2>/dev/null || true
  elif [ -x "$(command -v wineserver)" ]; then
    WINEPREFIX="$WINEPREFIX" wineserver -k 2>/dev/null || true
  fi
  flock -u 200 2>/dev/null || true
  exit "$exit_code"
}

trap cleanup EXIT INT TERM

# Export DXVK DirectComposition dummy swapchain & Chromium sandbox flags
export DXVK_CONFIG="dxgi.enableDummyCompositionSwapchain=True"
export WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS="--no-sandbox --disable-gpu-sandbox --disable-features=RendererCodeIntegrity"

# Audio & Desktop integration (Ultra-low latency studio quantum)
export PULSE_SERVER="unix:/run/user/${UID_NUM}/pulse/native"
export PULSE_LATENCY_MSEC=10
export PIPEWIRE_LATENCY="128/48000"
export WINE_FORCE_PORTAL=1
export WINEDEBUG="-all,fixme-all"
export WINEPREFIX="$WINEPREFIX"
export WINEDLLOVERRIDES="d3d11,dxgi=n,b;mscoree,mshtml=d"

# Pre-cache wine-mono from Nix store to ensure Wine Mono dialog never triggers
if [ -d "/nix/store" ] && [ ! -e "${XDG_CACHE_HOME:-$HOME/.cache}/wine/wine-mono-10.0.0-x86.msi" ]; then
  mkdir -p "${XDG_CACHE_HOME:-$HOME/.cache}/wine"
  for mono_msi in /nix/store/*wine-mono-10.0.0-x86.msi; do
    if [ -f "$mono_msi" ]; then
      ln -sf "$mono_msi" "${XDG_CACHE_HOME:-$HOME/.cache}/wine/wine-mono-10.0.0-x86.msi" 2>/dev/null || true
      break
    fi
  done
fi

# Self-healing: Initialize prefix if new
if [ ! -d "$WINEPREFIX/drive_c/windows" ] && [ -x "$(command -v "$WINEBOOT_BIN")" ]; then
  echo "==> Initializing Wine prefix at $WINEPREFIX..."
  "$WINEBOOT_BIN" --init --nomono --nogecko
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

# Self-healing: Ensure AppData settings are seeded or upgraded to low-latency Windows Audio
USER_ROAMING="$WINEPREFIX/drive_c/users/$(whoami)/AppData/Roaming/com.splice.INSTRUMENT"
if [ ! -f "$USER_ROAMING/INSTRUMENT.settings" ] && [ -f "$SHARE_DIR/seed-config/INSTRUMENT.settings" ]; then
  mkdir -p "$USER_ROAMING" 2>/dev/null || true
  cp "$SHARE_DIR/seed-config/INSTRUMENT.settings" "$USER_ROAMING/INSTRUMENT.settings" 2>/dev/null || true
elif [ -f "$USER_ROAMING/INSTRUMENT.settings" ] && [ -w "$USER_ROAMING/INSTRUMENT.settings" ]; then
  if grep -q 'deviceType="DirectSound"' "$USER_ROAMING/INSTRUMENT.settings"; then
    echo "==> Upgrading legacy DirectSound audio configuration to low-latency Windows Audio (WASAPI)..."
    sed -i -E 's/deviceType="DirectSound"/deviceType="Windows Audio"/g' "$USER_ROAMING/INSTRUMENT.settings" 2>/dev/null || true
    sed -i -E 's/preferredBufferSize="[0-9]+"/preferredBufferSize="128"/g' "$USER_ROAMING/INSTRUMENT.settings" 2>/dev/null || true
    sed -i -E 's/defaultNumStreamCoreThreads="[0-9]+"/defaultNumStreamCoreThreads="4"/g' "$USER_ROAMING/INSTRUMENT.settings" 2>/dev/null || true
    sed -i -E 's/defaultPreloadSize="[0-9]+"/defaultPreloadSize="32768"/g' "$USER_ROAMING/INSTRUMENT.settings" 2>/dev/null || true
  fi
fi

# Self-healing: Ensure sample database includes missing IR ID 1608633 to prevent Spitfire Error #4
COMMON_IR_DB="$WINEPREFIX/drive_c/users/$(whoami)/Splice/INSTRUMENT/INSTRUMENT Common/Samples/INSTRUMENT_Common_IR.db"
if [ -f "$COMMON_IR_DB" ] && [ -w "$COMMON_IR_DB" ] && [ -x "$(command -v sqlite3)" ]; then
  chmod 644 "$COMMON_IR_DB" 2>/dev/null || true
  sqlite3 "$COMMON_IR_DB" "INSERT OR IGNORE INTO Files (ID, Container) VALUES (1608633, 1);" 2>/dev/null || true
fi

cd "$WINEPREFIX/drive_c/Program Files/Splice/Splice INSTRUMENT"

# Pin execution to Performance cores (CPUs 0-15 on hybrid Intel architectures) to prevent E-core scheduler lag
if [ -x "$(command -v taskset)" ] && [ "$(nproc 2>/dev/null || echo 1)" -gt 16 ]; then
  taskset -c 0-15 "$WINE_BIN" "$SPLICE_EXE" "$@" &
else
  "$WINE_BIN" "$SPLICE_EXE" "$@" &
fi
APP_PID=$!
wait "$APP_PID" || true
