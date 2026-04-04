#!/bin/sh
set -eu


PORTABLE_DIR="${HOME}/Library/Application Support/MacNCheese/deps"

export PATH="$PORTABLE_DIR/bin:/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin:$PATH"


for app in "Wine Stable.app" "Wine Staging.app"; do
  if [ -d "$PORTABLE_DIR/$app" ]; then
    WINE_APP_BIN="$PORTABLE_DIR/$app/Contents/Resources/wine/bin"
    if [ -d "$WINE_APP_BIN" ]; then
      export PATH="$WINE_APP_BIN:$PATH"
    fi
  fi
done


GIT_BIN="git"
WGET_BIN="wget"
SEVENZ_BIN="7z"
if [ -x "$PORTABLE_DIR/bin/7zz" ]; then SEVENZ_BIN="$PORTABLE_DIR/bin/7zz"; fi


if [ -x "$PORTABLE_DIR/bin/git" ]; then
  
  if "$PORTABLE_DIR/bin/git" remote-https --help >/dev/null 2>&1 || [ -f "$PORTABLE_DIR/libexec/git-core/git-remote-https" ]; then
    GIT_BIN="$PORTABLE_DIR/bin/git"
  else
   
    if command -v git >/dev/null 2>&1; then
      GIT_BIN="$(command -v git)"
    fi
  fi
fi

if [ -x "$PORTABLE_DIR/bin/wget" ]; then WGET_BIN="$PORTABLE_DIR/bin/wget"; fi
if [ -x "$PORTABLE_DIR/bin/7zz" ]; then SEVENZ_BIN="$PORTABLE_DIR/bin/7zz"; fi

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
VKD3D_DEFAULT_URL="https://github.com/mont127/CheeseInstallation/releases/download/v1.0.0/vkd3d-proton.tar.zst"
GPTK_PACKAGE_URL="https://github.com/mont127/CheeseInstallation/releases/download/v1.0.0/gptk-package.zip"

PORTABLE_BASE_URL="https://github.com/mont127/CheeseInstallation/releases/download/v1.0.0"
PORTABLE_DEPS_URL="$PORTABLE_BASE_URL/macncheese_deps_arm64.zip"
PORTABLE_WINE_URL="$PORTABLE_BASE_URL/wine_arm64.tar.xz"

# (PORTABLE_DIR and PATH handled at top)
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

if [ -n "$BREW_BIN" ] && [ "${MNC_SUDOLESS:-0}" != "1" ]; then
  eval "$($BREW_BIN shellenv 2>/dev/null || true)"
fi

if [ -z "$DXMT_URL" ]; then
  DXMT_URL="$DXMT_DEFAULT_URL"
fi

if [ -z "$VKD3D_URL" ]; then
  VKD3D_URL="$VKD3D_DEFAULT_URL"
fi

sudo_run() {
  if [ -n "${SUDO_ASKPASS:-}" ]; then
    sudo -A "$@"
  elif [ -n "${MNC_SUDO_PASSWORD:-}" ]; then
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
  if [ "${MNC_SUDOLESS:-0}" = "1" ]; then
    return 0
  fi
  if ! is_admin_user; then
    echo "This macOS user is not an Administrator. MacNCheese setup needs an admin account on a new Mac."
    exit 1
  fi
}

prime_sudo() {
  if [ "${MNC_SUDOLESS:-0}" = "1" ]; then

    return 0
  fi
  require_admin
  if [ -n "${SUDO_ASKPASS:-}" ]; then
    sudo -A -k -v >/dev/null 2>&1 || {
      echo "The macOS password was rejected."
      exit 1
    }
  elif [ -n "${MNC_SUDO_PASSWORD:-}" ]; then
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
  if [ -n "${SUDO_ASKPASS:-}" ] || [ -n "${MNC_SUDO_PASSWORD:-}" ]; then
    (
      trap 'exit 0' TERM INT HUP
      while true; do
        if [ -n "${SUDO_ASKPASS:-}" ]; then
          sudo -A -n -v >/dev/null 2>&1 || true
        else
          printf '%s\n' "$MNC_SUDO_PASSWORD" | sudo -S -n -v >/dev/null 2>&1 || true
        fi
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
  if [ "${MNC_SUDOLESS:-0}" = "1" ]; then
    if xcode-select -p >/dev/null 2>&1; then return; fi
    echo "Xcode Command Line Tools missing. Portable tools might still work if they are standalone."
    return
  fi
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
    echo "Triggering Xcode Command Line Tools GUI installer..."
    xcode-select --install >/dev/null 2>&1 || true
    echo "Waiting for Xcode Command Line Tools to be installed..."
    echo "Please complete the installation in the window that just opened."
    until xcode-select -p >/dev/null 2>&1; do
      sleep 5
    done
    echo "Xcode Command Line Tools installed successfully."
  fi
}

ensure_brew() {
  if [ "${MNC_SUDOLESS:-0}" = "1" ]; then
    return 0
  fi
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
  if [ "${MNC_SUDOLESS:-0}" != "1" ]; then
    "$BREW_BIN" update || true
  fi
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
  if [ "${MNC_SUDOLESS:-0}" = "1" ]; then
    install_portable_tools
    return
  fi
  ensure_brew
  "$BREW_BIN" install git p7zip winetricks zstd || true
}

install_portable_tools() {
  echo "Step: Installing portable tools (7-Zip, Git, Wget, Zstd)..."
  mkdir -p "$PORTABLE_DIR"
  archive="$WORK_DIR/deps.zip"
  download_file "$PORTABLE_DEPS_URL" "$archive"
  unzip -o -q "$archive" -d "$PORTABLE_DIR" || {
    echo "Failed to unzip portable tools"
    exit 1
  }
  
 
  chmod -R u+w "$PORTABLE_DIR" 2>/dev/null || true

 
  for item in macncheese_deps macncheese_deps_arm64; do
    if [ -d "$PORTABLE_DIR/$item" ]; then
     
      cp -Rf "$PORTABLE_DIR/$item/"* "$PORTABLE_DIR/"
      rm -rf "$PORTABLE_DIR/$item"
    fi
  done

    
    if [ -f "$PORTABLE_DIR/bin/7zz" ]; then
        if file "$PORTABLE_DIR/bin/7zz" | grep -qE "HTML|text"; then
            echo "Removing broken 7zz (HTML error page detected)..."
            rm -f "$PORTABLE_DIR/bin/7zz"
        fi
    fi

    if [ ! -x "$PORTABLE_DIR/bin/7zz" ] && [ ! -x "$PORTABLE_DIR/bin/7z" ]; then
        echo "7-Zip missing or broken in deps, downloading standalone 7zz..."
        mkdir -p "$PORTABLE_DIR/bin"
       
        for url in \
          "https://www.7-zip.org/a/7z2408-mac-arm.tar.xz" \
          "https://www.7-zip.org/a/7z2407-mac-arm.tar.xz" \
          "https://7-zip.org/a/7z2301-mac-arm.tar.xz" \
          "https://github.com/mont127/CheeseInstallation/releases/download/v1.0.0/7zz.tar.xz"; do
            echo "Trying: $url"
            if curl -L --fail --silent --connect-timeout 15 -o "$PORTABLE_DIR/bin/7zz_dl" "$url"; then
                file_type=$(file "$PORTABLE_DIR/bin/7zz_dl")
                echo "Downloaded file type: $file_type"
                if echo "$file_type" | grep -q "XZ compressed data"; then
                    tar -xJf "$PORTABLE_DIR/bin/7zz_dl" -C "$PORTABLE_DIR/bin" 7zz && rm -f "$PORTABLE_DIR/bin/7zz_dl"
                    [ -x "$PORTABLE_DIR/bin/7zz" ] && break
                elif echo "$file_type" | grep -Eq "Mach-O|executable"; then
                    mv "$PORTABLE_DIR/bin/7zz_dl" "$PORTABLE_DIR/bin/7zz"
                    break
                fi
            fi
            rm -f "$PORTABLE_DIR/bin/7zz_dl"
        done
        chmod +x "$PORTABLE_DIR/bin/7zz" 2>/dev/null || true
        [ -x "$PORTABLE_DIR/bin/7zz" ] && echo "Successfully installed portable 7zz"
    fi


  echo "Applying security signatures to portable tools..."
  find "$PORTABLE_DIR" -type f -perm +111 -exec /usr/bin/codesign --force --sign - --timestamp=none {} \; 2>/dev/null || true
  
  echo "Portable tools installed to $PORTABLE_DIR"
}
install_vkd3d() {
  if [ -z "$VKD3D_DIR" ]; then
    echo "Missing VKD3D-Proton target directory"
    exit 1
  fi

  # Same layout as DXVK: VKD3D_DIR/x86/ and VKD3D_DIR/x64/
  mkdir -p "$VKD3D_DIR/x86" "$VKD3D_DIR/x64"
  archive="$WORK_DIR/vkd3d-proton-archive"
  extract_dir="$WORK_DIR/vkd3d-prebuilt"

  echo "Step: Downloading and installing VKD3D-Proton DLLs..."
  download_file "$VKD3D_URL" "$archive"
  rm -rf "$extract_dir"
  mkdir -p "$extract_dir"

  # Detect format and extract accordingly
  case "$VKD3D_URL" in
    *.tar.zst)
      # Try multiple zstd paths: homebrew, system, then tar --zstd
      ZSTD_BIN=""
      if [ -x /opt/homebrew/bin/zstd ]; then
        ZSTD_BIN="/opt/homebrew/bin/zstd"
      elif [ -x /usr/local/bin/zstd ]; then
        ZSTD_BIN="/usr/local/bin/zstd"
      elif command -v zstd >/dev/null 2>&1; then
        ZSTD_BIN="$(command -v zstd)"
      fi

      if [ -n "$ZSTD_BIN" ]; then
        "$ZSTD_BIN" -d "$archive" -o "$archive.tar" && tar -xf "$archive.tar" -C "$extract_dir"
        rm -f "$archive.tar"
      else
        tar --zstd -xf "$archive" -C "$extract_dir"
      fi
      ;;
    *.tar.gz|*.tgz)
      tar -xzf "$archive" -C "$extract_dir"
      ;;
    *.zip)
      unzip -o -q "$archive" -d "$extract_dir"
      ;;
    *)
      tar -xf "$archive" -C "$extract_dir"
      ;;
  esac

  # Find the x86 dir with d3d12.dll (archive has x86/ and x64/ folders)
  found_x86=""
  for candidate in \
    "$extract_dir/x86" \
    "$extract_dir/VKD3D/x86" \
    "$extract_dir/vkd3d-proton/x86"; do
    if [ -f "$candidate/d3d12.dll" ]; then
      found_x86="$candidate"
      break
    fi
  done
  if [ -z "$found_x86" ]; then
    found_x86="$(find "$extract_dir" -path "*/x86/d3d12.dll" -print | head -n1 | xargs -I{} dirname "{}" 2>/dev/null || true)"
  fi

  # Find the x64 dir (may or may not exist)
  found_x64=""
  for candidate in \
    "$extract_dir/x64" \
    "$extract_dir/VKD3D/x64" \
    "$extract_dir/vkd3d-proton/x64"; do
    if [ -f "$candidate/d3d12.dll" ]; then
      found_x64="$candidate"
      break
    fi
  done
  if [ -z "$found_x64" ]; then
    found_x64="$(find "$extract_dir" -path "*/x64/d3d12.dll" -print | head -n1 | xargs -I{} dirname "{}" 2>/dev/null || true)"
  fi

  if [ -z "$found_x86" ] || [ ! -f "$found_x86/d3d12.dll" ]; then
    echo "VKD3D-Proton archive did not contain the expected x86/d3d12.dll"
    exit 1
  fi

  echo "Installing VKD3D-Proton into $VKD3D_DIR"
  cp -f "$found_x86/"*.dll "$VKD3D_DIR/x86/"
  if [ -n "$found_x64" ] && [ -d "$found_x64" ]; then
    cp -f "$found_x64/"*.dll "$VKD3D_DIR/x64/"
  fi
  echo "VKD3D-Proton installed successfully (x86 + x64)"
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
  echo "Step: Downloading and installing DXVK DLLs..."
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
    "$GIT_BIN" clone https://github.com/Gcenx/DXVK-macOS.git "$DXVK_SRC"
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
  if [ "${MNC_SUDOLESS:-0}" = "1" ]; then
    install_portable_wine
    return
  fi
  ensure_brew
  install_xquartz_pkg
  ensure_rosetta
  install_gstreamer_pkg
  if "$BREW_BIN" list --cask wine-stable >/dev/null 2>&1; then
    echo "wine-stable cask already installed"
  elif "$BREW_BIN" info --cask wine-stable >/dev/null 2>&1; then
    echo "Installing wine-stable cask"
    "$BREW_BIN" install --cask wine-stable || install_wine_bundle
  elif "$BREW_BIN" list wine >/dev/null 2>&1; then
    echo "wine formula already installed"
  elif "$BREW_BIN" info wine >/dev/null 2>&1; then
    echo "Installing wine formula"
    "$BREW_BIN" install wine || install_wine_bundle
  else
    install_wine_bundle
  fi
}

install_portable_wine() {
  echo "Step: Installing portable Wine environment..."
  mkdir -p "$PORTABLE_DIR"
  archive="$WORK_DIR/wine.tar.xz"
  download_file "$PORTABLE_WINE_URL" "$archive"
  tar -xJf "$archive" -C "$PORTABLE_DIR" || {
     echo "Failed to extract portable wine"
     exit 1
  }
  echo "Portable wine installed to $PORTABLE_DIR"
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
  if [ "${MNC_SUDOLESS:-0}" != "1" ]; then
    ensure_brew
  fi
  echo "Installing Mesa3D (extracted)..."
  
  
  local sevenz="7z"
  if [ -x "$PORTABLE_DIR/bin/7zz" ]; then
    sevenz="$PORTABLE_DIR/bin/7zz"
  elif command -v 7zz >/dev/null 2>&1; then
    sevenz="7zz"
  elif command -v 7z >/dev/null 2>&1; then
    sevenz="7z"
  fi

  cd "$HOME"
  rm -rf mesa mesa.7z
  curl -L -o mesa.7z "$MESA_URL"
  mkdir -p mesa
  
  if ! command -v "$sevenz" >/dev/null 2>&1 && [ ! -x "$sevenz" ]; then
    echo "ERROR: 7-Zip binary not found (tried 7zz, 7z). Cannot extract Mesa."
    exit 1
  fi
  
  "$sevenz" x -y mesa.7z -omesa >/dev/null
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

  echo "Step: Downloading and installing DXMT DLLs..."
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

install_gptk_dlls() {
  GPTK_DLL_DIR="$HOME/gptk/lib/wine/x86_64-windows"
  mkdir -p "$GPTK_DLL_DIR"

  archive="$WORK_DIR/gptk-package.zip"
  extract_dir="$WORK_DIR/gptk-package"

  echo "Step: Downloading and installing GPTK DLL package..."
  download_file "$GPTK_PACKAGE_URL" "$archive"
  rm -rf "$extract_dir"
  mkdir -p "$extract_dir"
  unzip -o -q "$archive" -d "$extract_dir"

  # Find DLLs - they may be flat or in a subfolder
  found_dir=""
  if [ -f "$extract_dir/d3d11.dll" ]; then
    found_dir="$extract_dir"
  else
    found_dir="$(find "$extract_dir" -type f -name "d3d11.dll" -print | head -n1 | xargs -I{} dirname "{}" 2>/dev/null || true)"
  fi

  if [ -z "$found_dir" ] || [ ! -f "$found_dir/d3d11.dll" ]; then
    echo "GPTK package did not contain the expected DLLs"
    exit 1
  fi

  echo "Installing GPTK DLLs into $GPTK_DLL_DIR"
  for dll in atidxx64.dll d3d10.dll d3d11.dll d3d12.dll dxgi.dll nvapi64.dll nvngx.dll; do
    if [ -f "$found_dir/$dll" ]; then
      cp -f "$found_dir/$dll" "$GPTK_DLL_DIR/$dll"
    fi
  done
  echo "GPTK DLL package installed successfully"
}

init_prefix() {
  ensure_rosetta
  if [ -z "$PREFIX_DIR" ]; then
    echo "Missing prefix path"
    exit 1
  fi
  mkdir -p "$PREFIX_DIR"
  export WINEPREFIX="$PREFIX_DIR"
  
 
  if command -v wine >/dev/null 2>&1; then
    wine wineboot
  else
   
    found_wine=""
    for app in "Wine Stable.app" "Wine Staging.app"; do
        if [ -x "$PORTABLE_DIR/$app/Contents/Resources/wine/bin/wine" ]; then
            found_wine="$PORTABLE_DIR/$app/Contents/Resources/wine/bin/wine"
            break
        fi
    done
    if [ -n "$found_wine" ]; then
        "$found_wine" wineboot
    else
        echo "wine not found"
        exit 1
    fi
  fi
}

quick_setup() {
  ensure_rosetta
  install_portable_tools
  install_portable_wine
  install_dxvk
  install_mesa
}

if [ "${MNC_SUDOLESS:-0}" != "1" ] && [ "$ACTION" != "init_prefix" ] && [ "$ACTION" != "quick_setup" ]; then
  prime_sudo
  start_sudo_keepalive
fi
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
  install_dxmt|install_d3dmetal|install_d3dmetal3)
    install_dxmt
    ;;
  install_vkd3d)
    install_tools
    install_vkd3d
    ;;
  install_gptk_dlls)
    install_gptk_dlls
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
