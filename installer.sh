#!/bin/sh
set -eu

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

ACTION="${1:-}"
PREFIX_DIR="${2:-}"
DXVK_SRC="${3:-}"
DXVK_INSTALL64="${4:-}"
DXVK_INSTALL32="${5:-}"
MESA_DIR="${6:-}"
MESA_URL="${7:-}"
DXMT_DIR="${8:-}"
DXMT_URL="${9:-}"
VKD3D_DIR="${10:-}"
VKD3D_URL="${11:-}"

XQUARTZ_URL="https://github.com/XQuartz/XQuartz/releases/download/XQuartz-2.8.5/XQuartz-2.8.5.pkg"
GSTREAMER_URL="https://gstreamer.freedesktop.org/data/pkg/osx/1.28.1/gstreamer-1.0-1.28.1-universal.pkg"
DXVK_PREBUILT_URL="https://github.com/Gcenx/DXVK-macOS/releases/download/v1.10.3-20230507-repack/dxvk-macOS-async-v1.10.3-20230507-repack.tar.gz"
WINE_STABLE_URL="https://github.com/Gcenx/macOS_Wine_builds/releases/download/11.0/wine-stable-11.0-osx64.tar.xz"
DXMT_DEFAULT_URL="https://github.com/3Shain/dxmt/releases/latest/download/dxmt.tar.gz"
VKD3D_DEFAULT_URL="https://github.com/HansKristian-Work/vkd3d-proton/releases/latest/download/vkd3d-proton-master.tar.zst"
WORK_DIR="$(mktemp -d /tmp/macncheese-installer.XXXXXX)"
BREW_BIN=""
trap 'stop_sudo_keepalive; rm -rf "$WORK_DIR"' EXIT

if [ -z "$ACTION" ]; then
  echo "Missing action"
  exit 1
fi

echo "Starting installer action: $ACTION"

if [ -x /opt/homebrew/bin/brew ]; then
  BREW_BIN="/opt/homebrew/bin/brew"
elif [ -x /usr/local/bin/brew ]; then
  BREW_BIN="/usr/local/bin/brew"
elif command -v brew >/dev/null 2>&1; then
  BREW_BIN="$(command -v brew)"
fi

if [ -n "$BREW_BIN" ]; then
  eval "$($BREW_BIN shellenv 2>/dev/null || true)"
fi

if [ -z "$DXMT_URL" ]; then
  DXMT_URL="$DXMT_DEFAULT_URL"
fi

if [ -z "$VKD3D_URL" ]; then
  VKD3D_URL="$VKD3D_DEFAULT_URL"
fi

sudo_run() {
  if [ -n "${MNC_SUDO_PASSWORD:-}" ]; then
    printf '%s\n' "$MNC_SUDO_PASSWORD" | sudo -S "$@"
  else
    sudo "$@"
  fi
}

is_admin_user() {
  if command -v dseditgroup >/dev/null 2>&1; then
    dseditgroup -o checkmember -m "$USER" admin >/dev/null 2>&1
    return $?
  fi
  groups | grep -Eq '(^| )admin( |$)'
}

require_admin() {
  if ! is_admin_user; then
    echo "This macOS user is not an Administrator. MacNCheese setup needs an admin account on a new Mac."
    exit 1
  fi
}

prime_sudo() {
  require_admin
  if [ -n "${MNC_SUDO_PASSWORD:-}" ]; then
    printf '%s\n' "$MNC_SUDO_PASSWORD" | sudo -S -k -v >/dev/null 2>&1 || {
      echo "The macOS password was rejected."
      exit 1
    }
  else
    sudo -v || {
      echo "Administrator access is required."
      exit 1
    }
  fi
}

start_sudo_keepalive() {
  if [ -n "${MNC_SUDO_PASSWORD:-}" ]; then
    (
      trap 'exit 0' TERM INT HUP
      while true; do
        printf '%s\n' "$MNC_SUDO_PASSWORD" | "$REAL_SUDO" -S -n -v >/dev/null 2>&1 || true
        sleep 20
      done
    ) >/dev/null 2>&1 &
    SUDO_KEEPALIVE_PID=$!
  fi
}

stop_sudo_keepalive() {
  if [ -n "${SUDO_KEEPALIVE_PID:-}" ]; then
    kill "$SUDO_KEEPALIVE_PID" >/dev/null 2>&1 || true
    wait "$SUDO_KEEPALIVE_PID" >/dev/null 2>&1 || true
  fi
}

ensure_clt() {
  prime_sudo
  if xcode-select -p >/dev/null 2>&1; then
    return
  fi
  echo "Xcode Command Line Tools missing, trying softwareupdate"
  touch /tmp/.com.apple.dt.CommandLineTools.installondemand.in-progress || true
  product="$(softwareupdate -l 2>/dev/null | awk -F'*' '/Command Line Tools/ {print $2}' | sed 's/^ *//' | tail -n1)"
  rm -f /tmp/.com.apple.dt.CommandLineTools.installondemand.in-progress || true
  if [ -n "$product" ]; then
    sudo_run softwareupdate -i "$product" --verbose || true
  fi
  if ! xcode-select -p >/dev/null 2>&1; then
    xcode-select --install >/dev/null 2>&1 || true
    echo "Xcode Command Line Tools are required. Finish the macOS CLT install, then retry."
    exit 1
  fi
}

ensure_brew() {
  ensure_clt
  if [ -z "$BREW_BIN" ]; then
    prime_sudo
    start_sudo_keepalive
    NONINTERACTIVE=1 CI=1 HOMEBREW_NO_ANALYTICS=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    if [ -x /opt/homebrew/bin/brew ]; then
      BREW_BIN="/opt/homebrew/bin/brew"
    elif [ -x /usr/local/bin/brew ]; then
      BREW_BIN="/usr/local/bin/brew"
    else
      echo "Homebrew install finished but brew was not found"
      exit 1
    fi
    eval "$($BREW_BIN shellenv 2>/dev/null || true)"
  fi
  echo "Using brew: $BREW_BIN"
  "$BREW_BIN" update || true
}

download_file() {
  url="$1"
  out="$2"
  curl -L --fail --retry 3 --retry-delay 2 -o "$out" "$url"
}

install_pkg_url() {
  url="$1"
  name="$2"
  pkg_path="$WORK_DIR/$name"
  echo "Installing pkg: $name"
  download_file "$url" "$pkg_path"
  prime_sudo
  sudo_run /usr/sbin/installer -pkg "$pkg_path" -target /
}

ensure_rosetta() {
  if /usr/bin/pgrep oahd >/dev/null 2>&1 || /usr/sbin/pkgutil --pkgs | grep -q com.apple.pkg.RosettaUpdateAuto; then
    echo "Rosetta already available"
  else
    echo "Installing Rosetta"
    prime_sudo
    sudo_run /usr/sbin/softwareupdate --install-rosetta --agree-to-license || true
  fi
}

install_xquartz_pkg() {
  if pkgutil --pkgs | grep -qi xquartz; then
    echo "XQuartz already installed"
  else
    install_pkg_url "$XQUARTZ_URL" "XQuartz.pkg"
  fi
}

install_gstreamer_pkg() {
  if [ -d "/Library/Frameworks/GStreamer.framework" ]; then
    echo "GStreamer runtime already installed"
  else
    install_pkg_url "$GSTREAMER_URL" "gstreamer-runtime.pkg"
  fi
}

install_tools() {
  ensure_brew
  "$BREW_BIN" install git p7zip winetricks zstd || true
}
install_vkd3d() {
  if [ -z "$VKD3D_DIR" ]; then
    echo "Missing VKD3D-Proton target directory"
    exit 1
  fi

  mkdir -p "$VKD3D_DIR"
  archive="$WORK_DIR/vkd3d-proton.tar.zst"
  unpack_dir="$WORK_DIR/vkd3d-proton"
  rm -rf "$unpack_dir"
  mkdir -p "$unpack_dir"

  echo "Downloading VKD3D-Proton from $VKD3D_URL"
  download_file "$VKD3D_URL" "$archive"

  if command -v unzstd >/dev/null 2>&1; then
    unzstd -f "$archive"
    archive_tar="${archive%.zst}"
  elif command -v zstd >/dev/null 2>&1; then
    zstd -d -f "$archive" -o "${archive%.zst}"
    archive_tar="${archive%.zst}"
  elif command -v python3 >/dev/null 2>&1; then
    python3 - <<'PY' "$archive" "${archive%.zst}"
import sys
from pathlib import Path
src = Path(sys.argv[1])
dst = Path(sys.argv[2])
try:
    import zstandard as zstd
except Exception:
    raise SystemExit("Python zstandard module is required to unpack VKD3D-Proton .tar.zst archives")
with src.open('rb') as fsrc, dst.open('wb') as fdst:
    dctx = zstd.ZstdDecompressor()
    dctx.copy_stream(fsrc, fdst)
PY
    archive_tar="${archive%.zst}"
  else
    echo "No zstd decompressor found. Install zstd via Homebrew first."
    exit 1
  fi

  tar -xf "$archive_tar" -C "$unpack_dir"

  found_dir=""
  for candidate in \
    "$unpack_dir" \
    "$unpack_dir/x64" \
    "$unpack_dir/lib" \
    "$unpack_dir/lib64" \
    "$unpack_dir/vkd3d-proton"; do
    if [ -f "$candidate/d3d12.dll" ] && [ -f "$candidate/d3d12core.dll" ]; then
      found_dir="$candidate"
      break
    fi
  done

  if [ -z "$found_dir" ]; then
    found_dir="$(find "$unpack_dir" -type f \( -name d3d12.dll -o -name d3d12core.dll \) -print | head -n1 | xargs -I{} dirname "{}" 2>/dev/null || true)"
  fi

  if [ -z "$found_dir" ] || [ ! -f "$found_dir/d3d12.dll" ] || [ ! -f "$found_dir/d3d12core.dll" ]; then
    echo "VKD3D-Proton archive did not contain the expected d3d12.dll and d3d12core.dll files"
    exit 1
  fi

  echo "Installing VKD3D-Proton into $VKD3D_DIR"
  rm -f "$VKD3D_DIR/d3d12.dll" "$VKD3D_DIR/d3d12core.dll" "$VKD3D_DIR/dxgi.dll"
  cp -f "$found_dir/d3d12.dll" "$VKD3D_DIR/d3d12.dll"
  cp -f "$found_dir/d3d12core.dll" "$VKD3D_DIR/d3d12core.dll"

  if [ -f "$found_dir/dxgi.dll" ]; then
    cp -f "$found_dir/dxgi.dll" "$VKD3D_DIR/dxgi.dll"
  fi

  echo "VKD3D-Proton installed successfully"
}

install_dxvk() {
  if [ -z "$DXVK_INSTALL64" ] || [ -z "$DXVK_INSTALL32" ]; then
    echo "Missing DXVK install paths"
    exit 1
  fi

  bin64="$DXVK_INSTALL64/bin"
  bin32="$DXVK_INSTALL32/bin"
  archive="$WORK_DIR/dxvk.tar.gz"
  extract_dir="$WORK_DIR/dxvk-prebuilt"

  mkdir -p "$bin64" "$bin32"
  echo "Downloading prebuilt DXVK..."
  download_file "$DXVK_PREBUILT_URL" "$archive"
  rm -rf "$extract_dir"
  mkdir -p "$extract_dir"
  tar -xzf "$archive" -C "$extract_dir" --strip-components=1
  cp "$extract_dir/x64/"*.dll "$bin64/"
  cp "$extract_dir/x32/"*.dll "$bin32/"
  echo "DXVK installed successfully"
}

clone_dxvk_if_missing() {
  if [ -z "$DXVK_SRC" ]; then
    echo "Missing DXVK source path"
    exit 1
  fi
  if [ ! -d "$DXVK_SRC" ]; then
    echo "Cloning DXVK-macOS into $DXVK_SRC"
    mkdir -p "$(dirname "$DXVK_SRC")"
    git clone https://github.com/Gcenx/DXVK-macOS.git "$DXVK_SRC"
  fi
  if [ ! -f "$DXVK_SRC/build-win64.txt" ] || [ ! -f "$DXVK_SRC/build-win32.txt" ]; then
    echo "DXVK cross files not found in $DXVK_SRC"
    exit 1
  fi
}

install_wine_bundle() {
  prime_sudo
  archive="$WORK_DIR/wine-stable.tar.xz"
  unpack_dir="$WORK_DIR/wine-app"
  rm -rf "$unpack_dir"
  mkdir -p "$unpack_dir"
  echo "Installing Wine bundle fallback"
  download_file "$WINE_STABLE_URL" "$archive"
  tar -xJf "$archive" -C "$unpack_dir"
  app_path="$(find "$unpack_dir" -maxdepth 2 -type d -name "Wine*.app" | head -n1)"
  if [ -z "$app_path" ]; then
    echo "Failed to unpack Wine app bundle"
    exit 1
  fi
  app_name="$(basename "$app_path")"
  sudo_run rm -rf "/Applications/$app_name"
  sudo_run cp -R "$app_path" "/Applications/$app_name"
  sudo_run xattr -dr com.apple.quarantine "/Applications/$app_name" || true
  sudo_run mkdir -p /usr/local/bin
  sudo_run ln -sf "/Applications/$app_name/Contents/Resources/wine/bin/wine" /usr/local/bin/wine
  sudo_run ln -sf "/Applications/$app_name/Contents/Resources/wine/bin/wineserver" /usr/local/bin/wineserver
}

install_wine() {
  ensure_brew
  install_xquartz_pkg
  ensure_rosetta
  install_gstreamer_pkg
  if "$BREW_BIN" list --cask wine-stable >/dev/null 2>&1; then
    echo "wine-stable cask already installed"
  elif "$BREW_BIN" info --cask wine-stable >/dev/null 2>&1; then
    echo "Installing wine-stable cask"
    "$BREW_BIN" install --cask --no-quarantine wine-stable || install_wine_bundle
  elif "$BREW_BIN" list wine >/dev/null 2>&1; then
    echo "wine formula already installed"
  elif "$BREW_BIN" info wine >/dev/null 2>&1; then
    echo "Installing wine formula"
    "$BREW_BIN" install wine || install_wine_bundle
  else
    install_wine_bundle
  fi
}

build_dxvk64() {
  clone_dxvk_if_missing
  mkdir -p "$DXVK_INSTALL64"
  rm -rf "$DXVK_INSTALL64/build.64"
  meson setup "$DXVK_INSTALL64/build.64" "$DXVK_SRC" --cross-file "$DXVK_SRC/build-win64.txt" --prefix "$DXVK_INSTALL64" --buildtype release -Denable_d3d9=false
  ninja -C "$DXVK_INSTALL64/build.64"
  ninja -C "$DXVK_INSTALL64/build.64" install
}

build_dxvk32() {
  clone_dxvk_if_missing
  mkdir -p "$DXVK_INSTALL32"
  rm -rf "$DXVK_INSTALL32/build.32"
  meson setup "$DXVK_INSTALL32/build.32" "$DXVK_SRC" --cross-file "$DXVK_SRC/build-win32.txt" --prefix "$DXVK_INSTALL32" --buildtype release -Denable_d3d9=false
  ninja -C "$DXVK_INSTALL32/build.32"
  ninja -C "$DXVK_INSTALL32/build.32" install
}

install_mesa() {
  ensure_brew
  "$BREW_BIN" install p7zip || true
  cd "$HOME"
  rm -rf mesa mesa.7z
  curl -L -o mesa.7z "$MESA_URL"
  mkdir -p mesa
  7z x mesa.7z -omesa >/dev/null
  if [ ! -d "$HOME/mesa/x64" ] && ls -1 "$HOME/mesa" | grep -q mesa3d-; then
    sub=$(ls -1 "$HOME/mesa" | grep mesa3d- | head -n1)
    if [ -d "$HOME/mesa/$sub/x64" ]; then
      rm -rf "$HOME/mesa/x64"
      cp -R "$HOME/mesa/$sub/x64" "$HOME/mesa/x64"
    fi
  fi
}

install_dxmt() {
  if [ -z "$DXMT_DIR" ]; then
    echo "Missing DXMT target directory"
    exit 1
  fi

  mkdir -p "$DXMT_DIR"
  archive="$WORK_DIR/dxmt.tar.gz"
  unpack_dir="$WORK_DIR/dxmt"
  rm -rf "$unpack_dir"
  mkdir -p "$unpack_dir"

  echo "Downloading DXMT from $DXMT_URL"
  download_file "$DXMT_URL" "$archive"
  tar -xzf "$archive" -C "$unpack_dir"

  found_dir=""
  for candidate in \
    "$unpack_dir" \
    "$unpack_dir/dxmt" \
    "$unpack_dir/bin" \
    "$unpack_dir/lib"; do
    if [ -f "$candidate/d3d11.dll" ] && [ -f "$candidate/dxgi.dll" ]; then
      found_dir="$candidate"
      break
    fi
  done

  if [ -z "$found_dir" ]; then
    found_dir="$(find "$unpack_dir" -type f \( -name d3d11.dll -o -name dxgi.dll \) -print | head -n1 | xargs -I{} dirname "{}" 2>/dev/null || true)"
  fi

  if [ -z "$found_dir" ] || [ ! -f "$found_dir/d3d11.dll" ] || [ ! -f "$found_dir/dxgi.dll" ]; then
    echo "DXMT archive did not contain the expected d3d11.dll and dxgi.dll files"
    exit 1
  fi

  echo "Installing DXMT into $DXMT_DIR"
  rm -f "$DXMT_DIR/d3d11.dll" "$DXMT_DIR/dxgi.dll"
  cp -f "$found_dir/d3d11.dll" "$DXMT_DIR/d3d11.dll"
  cp -f "$found_dir/dxgi.dll" "$DXMT_DIR/dxgi.dll"

  for extra in d3d10core.dll d3d12.dll; do
    if [ -f "$found_dir/$extra" ]; then
      cp -f "$found_dir/$extra" "$DXMT_DIR/$extra"
    fi
  done

  echo "DXMT installed successfully"
}

init_prefix() {
  if [ -z "$PREFIX_DIR" ]; then
    echo "Missing prefix path"
    exit 1
  fi
  mkdir -p "$PREFIX_DIR"
  export WINEPREFIX="$PREFIX_DIR"
  if command -v wine >/dev/null 2>&1; then
    wine wineboot
  elif [ -x /opt/homebrew/bin/wine ]; then
    /opt/homebrew/bin/wine wineboot
  elif [ -x /usr/local/bin/wine ]; then
    /usr/local/bin/wine wineboot
  elif [ -x "/Applications/Wine Stable.app/Contents/Resources/wine/bin/wine" ]; then
    "/Applications/Wine Stable.app/Contents/Resources/wine/bin/wine" wineboot
  elif [ -x "/Applications/Wine Staging.app/Contents/Resources/wine/bin/wine" ]; then
    "/Applications/Wine Staging.app/Contents/Resources/wine/bin/wine" wineboot
  else
    echo "wine not found"
    exit 1
  fi
}

quick_setup() {
  install_tools
  install_wine
  install_dxvk
  install_mesa
}

prime_sudo
start_sudo_keepalive
case "$ACTION" in
  install_tools)
    install_tools
    ;;
  install_wine)
    install_wine
    ;;
  install_dxvk)
    install_dxvk
    ;;
  build_dxvk64)
    install_tools
    build_dxvk64
    ;;
  build_dxvk32)
    install_tools
    build_dxvk32
    ;;
  install_mesa)
    install_mesa
    ;;
  install_dxmt)
    install_dxmt
    ;;
  install_vkd3d)
    install_tools
    install_vkd3d
    ;;
  init_prefix)
    init_prefix
    ;;
  quick_setup)
    quick_setup
    ;;
  *)
    echo "Unknown action: $ACTION"
    exit 1
    ;;
esac
