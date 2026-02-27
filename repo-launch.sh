#!/usr/bin/env bash
set -euo pipefail

# ====== CONFIG ======
WINEPREFIX="${WINEPREFIX:-$HOME/wined}"
STEAM_DIR="$WINEPREFIX/drive_c/Program Files (x86)/Steam"
REPO_DIR="$STEAM_DIR/steamapps/common/REPO"
DXVK_BIN="${DXVK_BIN:-$HOME/dxvk-release/bin}"

# Optional backup restore location from our earlier manual
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
  need_dir "$REPO_DIR"
  need_dir "$DXVK_BIN"
  need_file "$DXVK_BIN/dxgi.dll"
  need_file "$DXVK_BIN/d3d11.dll"
  need_file "$DXVK_BIN/d3d10core.dll"
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
    cp -f "$DXVK_BACKUP_SYSTEM32/dxgi.dll"     "$WINEPREFIX/drive_c/windows/system32/" 2>/dev/null || true
    cp -f "$DXVK_BACKUP_SYSTEM32/d3d11.dll"    "$WINEPREFIX/drive_c/windows/system32/" 2>/dev/null || true
    cp -f "$DXVK_BACKUP_SYSTEM32/d3d10core.dll" "$WINEPREFIX/drive_c/windows/system32/" 2>/dev/null || true
  fi
}

copy_dxvk_to_repo() {
  cp -f "$DXVK_BIN/dxgi.dll"      "$REPO_DIR/"
  cp -f "$DXVK_BIN/d3d11.dll"     "$REPO_DIR/"
  cp -f "$DXVK_BIN/d3d10core.dll" "$REPO_DIR/"
}

steam_running() {
  pgrep -f "steam.exe" >/dev/null 2>&1
}

print_status() {
  echo "WINEPREFIX: $WINEPREFIX"
  echo "STEAM_DIR : $STEAM_DIR"
  echo "REPO_DIR  : $REPO_DIR"
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
  echo "Preparing prefix..."
  kill_steam
  remove_global_dxvk_overrides
  restore_system32_if_backup_exists
  copy_dxvk_to_repo
  echo "Done."
  echo "Steam will run without global DXVK."
  echo "REPO will use local DXVK from: $REPO_DIR"
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

cmd_repo() {
  check_prereqs
  export WINEPREFIX
  export WINEDLLOVERRIDES="dxgi,d3d11,d3d10core=n,b"
  export DXVK_LOG_PATH="${DXVK_LOG_PATH:-$HOME/dxvk-logs}"
  export DXVK_LOG_LEVEL="${DXVK_LOG_LEVEL:-info}"

  mkdir -p "$DXVK_LOG_PATH"
  copy_dxvk_to_repo

  if ! steam_running; then
    echo "Steam is not running."
    echo "Start Steam first with:"
    echo "  $0 steam"
    exit 1
  fi

  cd "$REPO_DIR"
  echo "Starting REPO with DXVK..."
  exec wine REPO.exe
}

cmd_all() {
  cmd_setup
  echo
  echo "Starting Steam..."
  (
    export WINEPREFIX
    unset WINEDLLOVERRIDES || true
    unset DXVK_LOG_PATH || true
    unset DXVK_LOG_LEVEL || true
    cd "$STEAM_DIR"
    wine steam.exe -no-cef-sandbox -vgui
  )
}

usage() {
  cat <<EOF
Usage: $(basename "$0") <setup|steam|repo|status>

  setup   Remove global DXVK hooks, restore system32 if backup exists, copy DXVK only to REPO
  steam   Start Steam safely without DXVK
  repo    Start REPO with per-game DXVK
  status  Show current paths and whether Steam is running

Examples:
  $(basename "$0") setup
  $(basename "$0") steam
  $(basename "$0") repo
EOF
}

# ====== MAIN ======
case "${1:-}" in
  setup)  cmd_setup ;;
  steam)  cmd_steam ;;
  repo)   cmd_repo ;;
  status) check_prereqs; print_status ;;
  *)      usage; exit 1 ;;
esac
