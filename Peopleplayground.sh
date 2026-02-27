#!/usr/bin/env bash
set -euo pipefail

# ====== CONFIG ======
WINEPREFIX="${WINEPREFIX:-$HOME/wined}"
STEAM_DIR="$WINEPREFIX/drive_c/Program Files (x86)/Steam"
GAME_NAME="People Playground"
GAME_EXE="People Playground.exe"
GAME_DIR="$STEAM_DIR/steamapps/common/$GAME_NAME"
DXVK_BIN="${DXVK_BIN:-$HOME/dxvk-release/bin}"

# Optional backup restore location
DXVK_BACKUP_SYSTEM32="$WINEPREFIX/dxvk-backup/system32"

# ====== HELPERS ======
die() {
  echo "Error: $*" >&2
  exit 1
}

need_file() {
  [[ -f "$1" ]] || die "Missing file: $1"
}

need_dir() {
  [[ -d "$1" ]] || die "Missing directory: $1"
}

check_prereqs() {
  command -v wine >/dev/null 2>&1 || die "wine not found in PATH"
  need_dir "$WINEPREFIX"
  need_dir "$STEAM_DIR"
  need_dir "$GAME_DIR"
  need_dir "$DXVK_BIN"
  need_file "$DXVK_BIN/dxgi.dll"
  need_file "$DXVK_BIN/d3d11.dll"
  need_file "$DXVK_BIN/d3d10core.dll"
  need_file "$GAME_DIR/$GAME_EXE"
}

kill_steam() {
  pkill -f "steamwebhelper.exe|steam.exe|SteamService.exe" 2>/dev/null || true
  wineserver -k 2>/dev/null || true
}

remove_global_dxvk_overrides() {
  export WINEPREFIX
  wine reg delete "HKCU\\Software\\Wine\\DllOverrides" /v dxgi /f >/dev/null 2>&1 || true
  wine reg delete "HKCU\\Software\\Wine\\DllOverrides" /v d3d11 /f >/dev/null 2>&1 || true
  wine reg delete "HKCU\\Software\\Wine\\DllOverrides" /v d3d10core /f >/dev/null 2>&1 || true
}

restore_system32_if_backup_exists() {
  export WINEPREFIX
  if [[ -d "$DXVK_BACKUP_SYSTEM32" ]]; then
    cp -f "$DXVK_BACKUP_SYSTEM32/dxgi.dll"      "$WINEPREFIX/drive_c/windows/system32/" 2>/dev/null || true
    cp -f "$DXVK_BACKUP_SYSTEM32/d3d11.dll"     "$WINEPREFIX/drive_c/windows/system32/" 2>/dev/null || true
    cp -f "$DXVK_BACKUP_SYSTEM32/d3d10core.dll" "$WINEPREFIX/drive_c/windows/system32/" 2>/dev/null || true
  fi
}

copy_dxvk_to_game() {
  cp -f "$DXVK_BIN/dxgi.dll"      "$GAME_DIR/"
  cp -f "$DXVK_BIN/d3d11.dll"     "$GAME_DIR/"
  cp -f "$DXVK_BIN/d3d10core.dll" "$GAME_DIR/"
}

steam_running() {
  pgrep -f "steam.exe" >/dev/null 2>&1
}

print_status() {
  echo "WINEPREFIX: $WINEPREFIX"
  echo "STEAM_DIR : $STEAM_DIR"
  echo "GAME_DIR  : $GAME_DIR"
  echo "GAME_EXE  : $GAME_EXE"
  echo "DXVK_BIN  : $DXVK_BIN"
  echo
  if steam_running; then
    echo "Steam status: running"
  else
    echo "Steam status: not running"
  fi
}

# ====== COMMANDS ======
cmd_setup() {
  check_prereqs
  echo "Preparing prefix for $GAME_NAME..."
  kill_steam
  remove_global_dxvk_overrides
  restore_system32_if_backup_exists
  copy_dxvk_to_game
  echo "Done."
  echo "Steam will run without global DXVK."
  echo "$GAME_NAME will use local DXVK from: $GAME_DIR"
}

cmd_steam() {
  check_prereqs
  export WINEPREFIX
  unset WINEDLLOVERRIDES || true
  unset DXVK_LOG_PATH || true
  unset DXVK_LOG_LEVEL || true

  cd "$STEAM_DIR"
  echo "Starting Steam..."
  exec wine steam.exe -no-cef-sandbox -vgui
}

cmd_game() {
  check_prereqs
  export WINEPREFIX
  export WINEDLLOVERRIDES="dxgi,d3d11,d3d10core=n,b"
  export DXVK_LOG_PATH="${DXVK_LOG_PATH:-$HOME/dxvk-logs}"
  export DXVK_LOG_LEVEL="${DXVK_LOG_LEVEL:-info}"

  mkdir -p "$DXVK_LOG_PATH"
  copy_dxvk_to_game

  if ! steam_running; then
    echo "Steam is not running."
    echo "Start Steam first with:"
    echo "  $0 steam"
    exit 1
  fi

  cd "$GAME_DIR"
  echo "Starting $GAME_NAME with DXVK..."
  exec wine "$GAME_EXE"
}

usage() {
  cat <<EOF
Usage: $(basename "$0") <setup|steam|game|status>

  setup   Remove global DXVK hooks, restore system32 if backup exists, copy DXVK only to $GAME_NAME
  steam   Start Steam safely without DXVK
  game    Start $GAME_NAME with per-game DXVK
  status  Show current paths and whether Steam is running

Examples:
  $(basename "$0") setup
  $(basename "$0") steam
  $(basename "$0") game
EOF
}

# ====== MAIN ======
case "${1:-}" in
  setup)  cmd_setup ;;
  steam)  cmd_steam ;;
  game)   cmd_game ;;
  status) check_prereqs; print_status ;;
  *)      usage; exit 1 ;;
esac
