#!/bin/sh
set -eu



export PATH="${HOME}/Library/Application Support/MacNCheese/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

ACTION="${1:-}"
PREFIX_DIR="${2:-}"
DXVK_SRC="${3:-}"
DXVK_INSTALL64="${4:-}"
DXVK_INSTALL32="${5:-}"
MESA_DIR="${6:-}"
MESA_URL="${7:-}"
VKD3D_DIR="${8:-}"
VKD3D_URL="${9:-}"

MNC_DATA_DIR="${HOME}/Library/Application Support/MacNCheese"
MNC_BIN_DIR="${MNC_DATA_DIR}/bin"
MNC_RUNTIME_DIR="${MNC_DATA_DIR}/runtime"
REPO_BASE_URL="https://github.com/mont127/CheeseInstallation/releases/download/v1.0.0"

XQUARTZ_URL="${REPO_BASE_URL}/XQuartz-portable.tar.gz"
GSTREAMER_URL="${REPO_BASE_URL}/gstreamer-portable.tar.xz"
DXVK_PREBUILT_URL="${REPO_BASE_URL}/dxvk-macOS.tar.gz"
WINE_STABLE_URL="${REPO_BASE_URL}/wine-portable.tar.xz"
VKD3D_DEFAULT_URL="${REPO_BASE_URL}/vkd3d-proton.tar.zst"
TOOLS_URL="${REPO_BASE_URL}/tools-macos.tar.gz"

WORK_DIR="$(mktemp -d /tmp/macncheese-installer.XXXXXX)"
mkdir -p "$MNC_BIN_DIR" "$MNC_RUNTIME_DIR"

trap 'rm -rf "$WORK_DIR"' EXIT

if [ -z "$ACTION" ]; then
  echo "Missing action"
  exit 1
fi

echo "Starting installer action: $ACTION"

download_file() {
    url="$1"
    out="$2"
    expected_sha="${3:-}"
    echo "Downloading $url..."
    curl -L --fail --retry 3 --retry-delay 2 -o "$out" "$url"
    if [ -n "$expected_sha" ]; then
        actual_sha="$(shasum -a 256 "$out" | awk '{print $1}')"
        if [ "$actual_sha" != "$expected_sha" ]; then
            echo "Checksum mismatch for $out"
            rm -f "$out"
            exit 1
        fi
    fi
}

bootstrap_tools() {
    is_tool_working() {
        tool_cmd="$1"
        if ! command -v "$tool_cmd" >/dev/null 2>&1; then return 1; fi
        
        if "$tool_cmd" --help 2>&1 | grep -q "Homebrew"; then return 1; fi
        
        if [ "$tool_cmd" = "7z" ] || [ "$tool_cmd" = "7zz" ]; then
             "$tool_cmd" 2>&1 | grep -q "7-Zip" || return 1
        fi
        return 0
    }

    if ! is_tool_working "zstd"; then
        echo "Bootstrapping tools archive from GitHub into $MNC_BIN_DIR..."
        archive="$WORK_DIR/tools.tar.gz"
        download_file "$TOOLS_URL" "$archive"
        tar -xzf "$archive" -C "$MNC_BIN_DIR"
        chmod +x "$MNC_BIN_DIR/zstd" 2>/dev/null || true
    fi

    if ! is_tool_working "7z" && ! is_tool_working "7zz"; then
        echo "Bootstrapping official 7zz (statically linked) into $MNC_BIN_DIR..."
        official_7z_url="https://www.7-zip.org/a/7z2408-mac.tar.xz"
        archive="$WORK_DIR/7z-official.tar.xz"
        download_file "$official_7z_url" "$archive"
        
        tar -xJf "$archive" -C "$MNC_BIN_DIR" 7zz
        chmod +x "$MNC_BIN_DIR/7zz"
        ln -sf "$MNC_BIN_DIR/7zz" "$MNC_BIN_DIR/7z"
    fi
}

install_wine() {
    echo "Manual Wine installation is required."
    echo "Please use the 'Install Wine' button in the MacNCheese Settings or the startup pop-up."
    echo "Select a .tar.xz archive (like wine-11.3-portable.tar.xz) to proceed."
    exit 1
}

install_dxvk() {
    bootstrap_tools
    [ -z "$DXVK_INSTALL64" ] || [ -z "$DXVK_INSTALL32" ] && { echo "Missing DXVK paths"; exit 1; }
    archive="$WORK_DIR/dxvk.tar.gz"
    extract_dir="$WORK_DIR/dxvk-prebuilt"
    echo "Installing portable DXVK..."
    download_file "$DXVK_PREBUILT_URL" "$archive"
    mkdir -p "$extract_dir"
    tar -xzf "$archive" -C "$extract_dir"
    
    mkdir -p "$DXVK_INSTALL64/bin"
    cp -f "$extract_dir"/*/x64/*.dll "$DXVK_INSTALL64/bin/" 2>/dev/null || \
    cp -f "$extract_dir/x64/"*.dll "$DXVK_INSTALL64/bin/" 2>/dev/null || \
    cp -f "$extract_dir/"*.dll "$DXVK_INSTALL64/bin/" 2>/dev/null || true

    mkdir -p "$DXVK_INSTALL32/bin"
    cp -f "$extract_dir"/*/x32/*.dll "$DXVK_INSTALL32/bin/" 2>/dev/null || \
    cp -f "$extract_dir/x32/"*.dll "$DXVK_INSTALL32/bin/" 2>/dev/null || true
}

install_mesa() {
    bootstrap_tools
    archive="$WORK_DIR/mesa.7z"
    echo "Installing portable Mesa..."
    download_file "${MESA_URL:-$REPO_BASE_URL/mesa-portable.7z}" "$archive"
    mkdir -p "$MNC_RUNTIME_DIR/mesa"
    7z x "$archive" -o"$MNC_RUNTIME_DIR/mesa" -y >/dev/null
}


install_vkd3d() {
    bootstrap_tools
    [ -z "$VKD3D_DIR" ] && { echo "Missing VKD3D dir"; exit 1; }
    archive="$WORK_DIR/vkd3d.tar.zst"
    unpack_dir="$WORK_DIR/vkd3d-tmp"
    echo "Installing portable VKD3D-Proton..."
    download_file "$VKD3D_DEFAULT_URL" "$archive"
    mkdir -p "$unpack_dir"
    zstd -d "$archive" -o "$WORK_DIR/vkd3d.tar"
    tar -xf "$WORK_DIR/vkd3d.tar" -C "$unpack_dir"
    found_dir="$(find "$unpack_dir" -type f -name d3d12.dll -print | head -n1 | xargs dirname 2>/dev/null || echo "$unpack_dir")"
    mkdir -p "$VKD3D_DIR"
    cp -f "$found_dir/"*.dll "$VKD3D_DIR/"
}

build_dxvk64() {
    [ -z "$DXVK_SRC" ] || [ -z "$DXVK_INSTALL64" ] && { echo "Missing DXVK paths"; exit 1; }
    mkdir -p "$DXVK_INSTALL64/bin"
    echo "Building/Copying DXVK 64-bit..."
    if [ -d "$DXVK_SRC/x64" ]; then
        cp -f "$DXVK_SRC/x64/"*.dll "$DXVK_INSTALL64/bin/"
    else
        cp -f "$DXVK_SRC/"*.dll "$DXVK_INSTALL64/bin/"
    fi
}

build_dxvk32() {
    [ -z "$DXVK_SRC" ] || [ -z "$DXVK_INSTALL32" ] && { echo "Missing DXVK paths"; exit 1; }
    mkdir -p "$DXVK_INSTALL32/bin"
    echo "Building/Copying DXVK 32-bit..."
    if [ -d "$DXVK_SRC/x32" ]; then
        cp -f "$DXVK_SRC/x32/"*.dll "$DXVK_INSTALL32/bin/"
    else
        cp -f "$DXVK_SRC/"*.dll "$DXVK_INSTALL32/bin/"
    fi
}



find_wine_bin() {
    portable_wine="$MNC_RUNTIME_DIR/wine/bin/wine"
    portable_wine64="$MNC_RUNTIME_DIR/wine/bin/wine64"
    if [ -x "$portable_wine" ]; then
        echo "$portable_wine"
    elif [ -x "$portable_wine64" ]; then
        echo "$portable_wine64"
    else
        command -v wine64 || command -v wine || echo ""
    fi
}

find_wineserver_bin() {
    portable_ws="$MNC_RUNTIME_DIR/wine/bin/wineserver"
    if [ -x "$portable_ws" ]; then
        echo "$portable_ws"
    else
        command -v wineserver || echo ""
    fi
}

init_prefix() {
    [ -z "$PREFIX_DIR" ] && { echo "Missing prefix path" >&2; exit 1; }
    wine_bin=$(find_wine_bin)
    [ -z "$wine_bin" ] && { echo "Wine not found on system" >&2; exit 1; }
    mkdir -p "$PREFIX_DIR"
    export WINEPREFIX="$PREFIX_DIR"
    "$wine_bin" wineboot
}

clean_prefix() {
    [ -z "$PREFIX_DIR" ] && { echo "Missing prefix path" >&2; exit 1; }
    wine_bin=$(find_wine_bin)
    [ -z "$wine_bin" ] && { echo "Wine not found on system" >&2; exit 1; }
    export WINEPREFIX="$PREFIX_DIR"
    "$wine_bin" wineboot -u
}

kill_wineserver() {
    wineserver_bin=$(find_wineserver_bin)
    if [ -n "$wineserver_bin" ]; then
        "$wineserver_bin" -k
    else
        pkill -f wineserver || true
    fi
}

quick_setup() {
    install_dxvk
    install_mesa
}

case "$ACTION" in
  install_wine)    install_wine ;;
  install_dxvk)    install_dxvk ;;
  install_mesa)    install_mesa ;;
  install_vkd3d)   install_vkd3d ;;
  init_prefix)     init_prefix ;;
  clean_prefix)    clean_prefix ;;
  kill_wineserver) kill_wineserver ;;
  install_tools)   bootstrap_tools ;;
  build_dxvk64)    build_dxvk64 ;;
  build_dxvk32)    build_dxvk32 ;;

  quick_setup)     quick_setup ;;
  *) echo "Unknown action: $ACTION"; exit 1 ;;
esac
