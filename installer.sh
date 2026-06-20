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
GPTK_DIR="${12:-}"

XQUARTZ_URL="https://github.com/XQuartz/XQuartz/releases/download/XQuartz-2.8.5/XQuartz-2.8.5.pkg"
GSTREAMER_URL="https://gstreamer.freedesktop.org/data/pkg/osx/1.28.1/gstreamer-1.0-1.28.1-universal.pkg"
DXVK_PREBUILT_URL="https://github.com/Gcenx/DXVK-macOS/releases/download/v1.10.3-20230507-repack/dxvk-macOS-async-v1.10.3-20230507-repack.tar.gz"
WINE_STABLE_URL="https://github.com/Gcenx/macOS_Wine_builds/releases/download/11.0/wine-stable-11.0-osx64.tar.xz"
DXMT_DEFAULT_URL="https://github.com/3Shain/dxmt/releases/download/v0.80/dxmt-v0.80-builtin.tar.gz"
WINE_STAGING_DEFAULT_URL="https://github.com/Gcenx/macOS_Wine_builds/releases/download/11.9/wine-staging-11.9-osx64.tar.xz"
VKD3D_DEFAULT_URL="https://github.com/mont127/CheeseInstallation/releases/download/v1.0.0/vkd3d-proton.tar.zst"
GPTK_PACKAGE_URL="https://github.com/mont127/CheeseInstallation/releases/download/v1.0.0/gptk-package.zip"

PORTABLE_BASE_URL="https://github.com/mont127/CheeseInstallation/releases/download/v1.0.0"
PORTABLE_BASE_TAG="v1.0.0"
PORTABLE_DEPS_URL="$PORTABLE_BASE_URL/macncheese_deps_arm64.zip"
PORTABLE_WINE_URL="$PORTABLE_BASE_URL/wine_arm64.tar.xz"
# Wine Devel — standalone Wine Staging 11.8 build with the OpenGL 3.2+ macdrv
# patch (winemac.so forward-compat gate forced unconditional). Independent of
# Wine D3DMetal; used for SDL3/OpenGL games (e.g. Mewgenics). Downloaded on
# opt-in install and extracted to $PORTABLE_DIR/Wine Devel.app.
WINE_DEVEL_DEFAULT_URL="$PORTABLE_BASE_URL/wine-devel.zip"
VERSION_MARKER="$PORTABLE_DIR/.mnc_versions"

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

if [ -z "${WINE_DEVEL_URL:-}" ]; then
  WINE_DEVEL_URL="$WINE_DEVEL_DEFAULT_URL"
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

# Locate the bundled portable GStreamer tarball (shipped in the .app Resources /
# repo). Mirrors locate_wine_d3dmetal_bundle's search order.
locate_gstreamer_bundle() {
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
  for c in \
    "${WINE_D3DMETAL_GSTREAMER_BUNDLE:-}" \
    "${RESOURCES_DIR:-}/gstreamer-portable.tar.xz" \
    "${script_dir}/gstreamer-portable.tar.xz" \
    "${script_dir}/../Resources/gstreamer-portable.tar.xz" \
    "${script_dir}/../../Resources/gstreamer-portable.tar.xz" \
    "/Applications/MacNdCheese Launcher.app/Contents/Resources/gstreamer-portable.tar.xz" \
    "$HOME/Applications/MacNdCheese Launcher.app/Contents/Resources/gstreamer-portable.tar.xz" \
    "$HOME/macndcheese/gstreamer-portable.tar.xz" \
    "$HOME/Library/Application Support/MacNCheese/gstreamer-portable.tar.xz"; do
    [ -n "$c" ] && [ -f "$c" ] && { printf '%s\n' "$c"; return 0; }
  done
  return 1
}

# macOS gates microphone access via TCC and only grants it to an app whose
# Info.plist declares NSMicrophoneUsageDescription. The portable Wine apps run as
# bundle id org.winehq.wine-stable.wine WITHOUT that key, so in-game mic capture
# (voice chat) is silently denied even though audio output works. Inject the key
# and re-seal (ad-hoc, top-level only — nested D3DMetal/.so sigs are untouched) so
# macOS will prompt for / allow mic access for games run under this Wine.
add_mic_usage_to_app() {
  app="$1"
  plist="$app/Contents/Info.plist"
  [ -d "$app" ] && [ -f "$plist" ] || return 0
  desc="MacNdCheese runs Windows games that use the microphone for in-game voice chat."
  /usr/libexec/PlistBuddy -c "Add :NSMicrophoneUsageDescription string '$desc'" "$plist" >/dev/null 2>&1 \
    || /usr/libexec/PlistBuddy -c "Set :NSMicrophoneUsageDescription '$desc'" "$plist" >/dev/null 2>&1 || true
  /usr/bin/codesign --force --sign - "$app" >/dev/null 2>&1 || true
  echo "  microphone usage description ensured for $(basename "$app")"
}

# Wine D3DMetal's launch path resolves freetype/fontconfig from x86_64 Homebrew
# (/usr/local/opt/...) via DYLD_FALLBACK_LIBRARY_PATH — Wine runs under Rosetta
# x86_64, so the dylibs must be x86_64 (the arm64 /opt/homebrew copies won't load).
# Nothing else installs these, so a fresh machine has no freetype on the fallback
# path and RE-Engine/D3DMetal titles (RE4) fail font init / black-screen — the
# classic "works on my machine but not theirs". Install them so /usr/local/opt is
# populated everywhere. (backend_server.py also falls back to the bundled freetype
# in the Wine D3DMetal app, so this is belt-and-suspenders.) Best-effort.
ensure_fonts_for_d3dmetal() {
  if [ -f /usr/local/opt/freetype/lib/libfreetype.6.dylib ] && [ -d /usr/local/opt/fontconfig/lib ]; then
    echo "freetype + fontconfig (x86_64) already present"
    return 0
  fi
  if [ ! -x /usr/local/bin/brew ]; then
    echo "WARNING: x86_64 Homebrew (/usr/local/bin/brew) not found — cannot install freetype/fontconfig."
    echo "         The bundled Wine D3DMetal freetype is used as a fallback, but to be safe install"
    echo "         x86_64 Homebrew, then: arch -x86_64 /usr/local/bin/brew install freetype fontconfig"
    return 0
  fi
  echo "Step: Installing x86_64 freetype + fontconfig (Wine D3DMetal font deps)..."
  if arch -x86_64 /usr/local/bin/brew install freetype fontconfig >/dev/null 2>&1; then
    echo "  Installed freetype + fontconfig (x86_64)"
  else
    echo "  WARNING: freetype/fontconfig install failed (bundled freetype fallback still applies)."
  fi
}

# Wine D3DMetal's winegstreamer.so links GStreamer via the rpath
# /Library/Frameworks/GStreamer.framework/Libraries (verified with otool). Without
# that framework, Media-Foundation video (e.g. RE-Engine intro movies) can't decode
# and the game stalls on a black screen. Ensure it is present at the system path,
# preferring the bundled portable tarball (offline) over a network pkg download.
ensure_gstreamer_for_d3dmetal() {
  if [ -d "/Library/Frameworks/GStreamer.framework/Versions/1.0/lib" ] && \
     [ -f "/Library/Frameworks/GStreamer.framework/Versions/1.0/lib/libgstreamer-1.0.0.dylib" ]; then
    echo "GStreamer runtime already present (RE-Engine intro videos will decode)"
    return 0
  fi
  echo "Step: Installing GStreamer runtime (required by Wine D3DMetal for video / MF)..."
  gst_bundle="$(locate_gstreamer_bundle || true)"
  if [ -n "$gst_bundle" ]; then
    echo "  Using bundled GStreamer: $gst_bundle"
    gst_stage="$WORK_DIR/gstreamer-unpack"
    rm -rf "$gst_stage"; mkdir -p "$gst_stage"
    if tar xJf "$gst_bundle" -C "$gst_stage" 2>/dev/null; then
      gst_fw="$(find "$gst_stage" -maxdepth 3 -type d -name 'GStreamer.framework' | head -n1)"
      if [ -n "$gst_fw" ] && [ -d "$gst_fw/Versions/1.0/lib" ]; then
        prime_sudo
        sudo_run mkdir -p /Library/Frameworks
        sudo_run rm -rf "/Library/Frameworks/GStreamer.framework"
        if sudo_run /bin/cp -R "$gst_fw" "/Library/Frameworks/GStreamer.framework"; then
          sudo_run xattr -dr com.apple.quarantine "/Library/Frameworks/GStreamer.framework" 2>/dev/null || true
          echo "  Installed bundled GStreamer.framework -> /Library/Frameworks"
          rm -rf "$gst_stage"
          return 0
        fi
        echo "  WARNING: copy of bundled GStreamer.framework failed; falling back to pkg download"
      else
        echo "  WARNING: bundled tarball had no usable GStreamer.framework; falling back to pkg download"
      fi
    else
      echo "  WARNING: could not extract bundled GStreamer tarball; falling back to pkg download"
    fi
    rm -rf "$gst_stage"
  else
    echo "  No bundled GStreamer found; downloading the runtime pkg ($GSTREAMER_URL)"
  fi
  install_pkg_url "$GSTREAMER_URL" "gstreamer-runtime.pkg"
}

write_component_version() {
  mkdir -p "$PORTABLE_DIR"
  touch "$VERSION_MARKER"
  tmpfile="${VERSION_MARKER}.tmp"
  grep -v "^${1}=" "$VERSION_MARKER" > "$tmpfile" 2>/dev/null || true
  printf '%s=%s\n' "$1" "$2" >> "$tmpfile"
  mv "$tmpfile" "$VERSION_MARKER"
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
  
  write_component_version "tools" "$PORTABLE_BASE_TAG"
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

install_portable_wine_staging() {
  echo "Step: Installing Wine Staging (latest from Gcenx/macOS_Wine_builds)..."
  mkdir -p "$PORTABLE_DIR"
  api_response=$(curl -s --connect-timeout 20 "https://api.github.com/repos/Gcenx/macOS_Wine_builds/releases/latest" 2>/dev/null || true)
  staging_url=""
  staging_tag=""
  if [ -n "$api_response" ]; then
    staging_url=$(printf '%s' "$api_response" | grep '"browser_download_url"' | grep 'wine-staging.*\.tar\.xz' | head -n1 | sed 's/.*"browser_download_url": *"\([^"]*\)".*/\1/')
    staging_tag=$(printf '%s' "$api_response" | grep '"tag_name"' | head -n1 | sed 's/.*"tag_name": *"\([^"]*\)".*/\1/')
  fi
  if [ -z "$staging_url" ]; then
    echo "GitHub API returned no usable wine-staging URL — using default fallback"
    staging_url="$WINE_STAGING_DEFAULT_URL"
    staging_tag="${staging_tag:-fallback}"
  fi
  echo "Downloading Wine Staging $staging_tag from: $staging_url"
  archive="$WORK_DIR/wine-staging.tar.xz"
  download_file "$staging_url" "$archive"
  tar -xJf "$archive" -C "$PORTABLE_DIR" || {
    echo "Failed to extract wine staging"
    exit 1
  }
  echo "Applying security signatures..."
  find "$PORTABLE_DIR" -type f -perm +111 -exec /usr/bin/codesign --force --sign - --timestamp=none {} \; 2>/dev/null || true
  write_component_version "wine_branch" "staging"
  write_component_version "wine_staging" "$staging_tag"
  add_mic_usage_to_app "$PORTABLE_DIR/Wine Staging.app"
  echo "Wine Staging $staging_tag installed to $PORTABLE_DIR"
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
  write_component_version "wine_branch" "stable"
  write_component_version "wine_stable" "$PORTABLE_BASE_TAG"
  add_mic_usage_to_app "$PORTABLE_DIR/Wine Stable.app"
  echo "Portable wine installed to $PORTABLE_DIR"
}

# Wine D3DMetal (MNC HACK 22 v3) — patched Wine 11.0 with the gs.base swap
# removed so D3D11/12 games can talk to Apple's D3DMetal framework without
# the dispatcher corrupting Apple's pthread TSD. Ships as a self-contained
# Wine D3DMetal.app bundle inside Resources/wine-d3dmetal-bundle.zip and is
# extracted to $PORTABLE_DIR/Wine D3DMetal.app on opt-in install. Not part
# of the default install path; the SwiftUI Setup tab adds it as a checkbox.
locate_wine_d3dmetal_bundle() {
  # Resolve installer script's directory robustly (works under sh, bash, dash).
  script_path="$0"
  case "$script_path" in
    /*) ;;
    *) script_path="$PWD/$script_path" ;;
  esac
  script_dir="$(cd "$(dirname "$script_path")" 2>/dev/null && pwd)" || script_dir=""

  # If we're inside a .app/Contents/Resources/, the bundle should be right there.
  # Also try the parent .app's Resources (in case we got an installer at .app/Contents/MacOS/).
  candidates="
${WINE_D3DMETAL_BUNDLE_PATH:-}
${RESOURCES_DIR:-}/wine-d3dmetal-bundle.zip
${script_dir}/wine-d3dmetal-bundle.zip
${script_dir}/../Resources/wine-d3dmetal-bundle.zip
${script_dir}/../../Resources/wine-d3dmetal-bundle.zip
/Applications/MacNdCheese Launcher.app/Contents/Resources/wine-d3dmetal-bundle.zip
$HOME/Applications/MacNdCheese Launcher.app/Contents/Resources/wine-d3dmetal-bundle.zip
$HOME/macndcheese/wine-d3dmetal-bundle.zip
$HOME/macndcheese/build/MacNdCheese Launcher.app/Contents/Resources/wine-d3dmetal-bundle.zip
$HOME/Library/Application Support/MacNCheese/wine-d3dmetal-bundle.zip
"
  # Use newline-delimited iteration to handle spaces in paths properly.
  # NOTE: process-substitution (here-doc) used instead of a pipe so the
  # `return` inside the loop affects the function, not a subshell.
  while IFS= read -r c; do
    [ -z "$c" ] && continue
    if [ -f "$c" ]; then
      printf '%s' "$c"
      return 0
    fi
  done <<EOF
$candidates
EOF

  # Last-ditch: find any wine-d3dmetal-bundle.zip in common .app install roots.
  for root in /Applications "$HOME/Applications" "$HOME/Downloads"; do
    [ -d "$root" ] || continue
    found="$(find "$root" -maxdepth 5 -name 'wine-d3dmetal-bundle.zip' -type f 2>/dev/null | head -n1)"
    if [ -n "$found" ] && [ -f "$found" ]; then
      printf '%s' "$found"
      return 0
    fi
  done

  return 1
}

install_wine_d3dmetal() {
  echo "Step: Installing Wine D3DMetal (MNC HACK 22 v3)..."
  mkdir -p "$PORTABLE_DIR"

  bundle="$(locate_wine_d3dmetal_bundle || true)"
  if [ -z "$bundle" ]; then
    echo "ERROR: wine-d3dmetal-bundle.zip not found."
    echo "Searched: Resources/, installer script dir, ~/macndcheese,"
    echo "          ~/Library/Application Support/MacNCheese, \$WINE_D3DMETAL_BUNDLE_PATH"
    exit 1
  fi
  echo "Using bundle: $bundle"

  target_app="$PORTABLE_DIR/Wine D3DMetal.app"
  rm -rf "$target_app"

  staging="$WORK_DIR/wine-d3dmetal-unpack"
  rm -rf "$staging"
  mkdir -p "$staging"

  if command -v unzip >/dev/null 2>&1; then
    unzip -q "$bundle" -d "$staging" || {
      echo "Failed to unzip wine-d3dmetal bundle"
      exit 1
    }
  elif [ -x "$SEVENZ_BIN" ]; then
    "$SEVENZ_BIN" x -y -o"$staging" "$bundle" >/dev/null || {
      echo "Failed to extract wine-d3dmetal bundle with 7z"
      exit 1
    }
  else
    echo "Neither unzip nor 7z is available to extract the bundle"
    exit 1
  fi

  unpacked_app="$(find "$staging" -maxdepth 2 -type d -name 'Wine D3DMetal.app' | head -n1)"
  if [ -z "$unpacked_app" ] || [ ! -d "$unpacked_app" ]; then
    echo "Unpacked archive did not contain 'Wine D3DMetal.app'"
    exit 1
  fi
  mv "$unpacked_app" "$target_app"

  # Ensure executables stayed executable through the zip round trip.
  chmod +x "$target_app/Contents/MacOS/wine" 2>/dev/null || true
  chmod +x "$target_app/Contents/Resources/wine-d3dmetal" 2>/dev/null || true
  find "$target_app/Contents/Resources/wine/bin" -type f -exec chmod +x {} \; 2>/dev/null || true

  # Clear quarantine so Gatekeeper does not flag every binary in the tree.
  xattr -dr com.apple.quarantine "$target_app" 2>/dev/null || true

  # Expose the wrapper on PATH-style locations the backend probes.
  mkdir -p "$PORTABLE_DIR/bin"
  # The no-shim wine-11-d3dmetal app is SELF-CONTAINED: it bundles the GPTK
  # D3DMetal d3d DLLs (d3d11/d3d12/dxgi -> libd3dshared) plus libd3dshared.dylib
  # and D3DMetal.framework under Contents/Resources/wine/lib/external. No external
  # GPTK bridge to copy, and no shim wrapper. The backend launches it via
  # `open -n` (Contents/MacOS/wine), so no PATH symlink is needed either.
  ww_dir="$target_app/Contents/Resources/wine/lib/wine/x86_64-windows"
  libext="$target_app/Contents/Resources/wine/lib/external"
  if [ -f "$ww_dir/d3d12.dll" ] && [ -f "$libext/libd3dshared.dylib" ] && [ -d "$libext/D3DMetal.framework" ]; then
    echo "Verified: bundled D3DMetal runtime present (d3d12.dll + libd3dshared.dylib + D3DMetal.framework)"
  else
    echo "WARNING: bundled D3DMetal runtime incomplete in $target_app"
    echo "         d3d12.dll=$( [ -f "$ww_dir/d3d12.dll" ] && echo yes || echo MISSING )"
    echo "         libd3dshared.dylib=$( [ -f "$libext/libd3dshared.dylib" ] && echo yes || echo MISSING )"
    echo "         D3DMetal.framework=$( [ -d "$libext/D3DMetal.framework" ] && echo yes || echo MISSING )"
  fi

  # GStreamer is REQUIRED for Media-Foundation video (RE-Engine intro movies etc.);
  # without it D3D12 titles like RE4 stall on a black screen. winegstreamer.so's
  # rpath points at /Library/Frameworks/GStreamer.framework, so install it there.
  ensure_gstreamer_for_d3dmetal || echo "WARNING: GStreamer install failed; video-gated games (RE4) may black-screen"
  ensure_fonts_for_d3dmetal || echo "WARNING: freetype/fontconfig install failed; D3DMetal titles may fail font init"
  add_mic_usage_to_app "$target_app"

  # Verify the patched ntdll landed (zero _thread_set_tsd_base syscalls).
  ntdll="$target_app/Contents/Resources/wine/lib/wine/x86_64-unix/ntdll.so"
  if [ -f "$ntdll" ]; then
    swap_count="$(otool -tV "$ntdll" 2>/dev/null | grep -c "movl.*0x3000003" || true)"
    if [ "$swap_count" = "0" ]; then
      echo "Verified: Wine D3DMetal ntdll.so has 0 gs.base swap syscalls (MNC22 v3)"
    else
      echo "WARNING: Wine D3DMetal ntdll.so still has $swap_count gs.base swap calls; expected 0"
    fi
  fi

  write_component_version "wine_d3dmetal" "11.0-mnc22v3"
  echo "Wine D3DMetal installed to $target_app"

  # Pre-warm wineboot in a dedicated D3DMetal warm-up prefix so the very
  # first cs2/Source-2 launch isn't where every wine service (services,
  # explorer, plugplay, winedevice, mscoree) goes through the in-process
  # Cocoa launcher init for the first time. After this, subsequent
  # launches in user prefixes still trigger one wineboot per fresh
  # prefix, but the cold-disk dlopen cost is paid here once.
  warmup_prefix="$PORTABLE_DIR/wine-d3dmetal-warmup"
  if [ ! -d "$warmup_prefix" ]; then
    echo "Step: Pre-warming wineboot in $warmup_prefix (one-time, ~30-60s)..."
    mkdir -p "$warmup_prefix"
    (
      export WINEPREFIX="$warmup_prefix"
      export WINE_D3DMETAL_NO_STEAM_HACK=1
      export WINE_D3DMETAL_USE_PTHREAD_SHIM=0
      export WINE_D3DMETAL_USE_PTHREAD_SELF_INTERPOSE=0
      export WINE_D3DMETAL_USE_IOKIT_OBSERVER=0
      export WINEDEBUG=-all
      export WINEDLLOVERRIDES="winemenubuilder.exe=d;mscoree=;mshtml="
      # Plan A fix: wine-d3dmetal's wineboot takes 5+ min (PATCH-018 gs.base
      # mirror writes cause __wine_unix_call_dispatcher to recurse 27 levels
      # deep on every setupapi syscall). Wine Stable wineboot does the same
      # work in ~10s. So we wineboot with Wine Stable, then at game launch
      # time wine-d3dmetal uses WINE_D3DMETAL_SKIP_WINEBOOT=1 (MNC HACK 29)
      # to attach to the already-initialised prefix without re-running.
      wine_stable_bin="$PORTABLE_DIR/Wine Stable.app/Contents/Resources/wine/bin/wine"
      if [ -x "$wine_stable_bin" ]; then
        echo "  Using Wine Stable for wineboot (~10s instead of 5+ min)"
        timeout 60 arch -x86_64 "$wine_stable_bin" wineboot --init >/dev/null 2>&1 || true
        sleep 2
        timeout 10 arch -x86_64 "$wine_stable_bin" wineserver -k >/dev/null 2>&1 || true
      else
        echo "  Wine Stable not installed yet; skipping prewarm (first launch will wineboot once)"
      fi
    ) &
    warmup_pid=$!
    # Don't block the installer UI on warmup completion — wait briefly
    # so logs show wineboot started, then detach.
    sleep 3
    if kill -0 "$warmup_pid" 2>/dev/null; then
      echo "Pre-warming wineboot in background (PID $warmup_pid) — will finish in ~30-60s"
    fi
  else
    echo "Pre-warmed wineboot prefix already present at $warmup_prefix"
  fi
}

uninstall_wine_d3dmetal() {
  echo "Step: Uninstalling Wine D3DMetal..."
  rm -rf "$PORTABLE_DIR/Wine D3DMetal.app"
  rm -f  "$PORTABLE_DIR/bin/wine-d3dmetal"
  grep -v "^wine_d3dmetal=" "$VERSION_MARKER" > "${VERSION_MARKER}.tmp" 2>/dev/null || true
  mv "${VERSION_MARKER}.tmp" "$VERSION_MARKER" 2>/dev/null || true
  echo "Wine D3DMetal removed."
}

# Wine Devel — standalone Wine Staging 11.8 with the OpenGL 3.2+ macdrv patch.
# Downloaded from the release (WINE_DEVEL_URL) and extracted to
# $PORTABLE_DIR/Wine Devel.app. Completely separate from Wine D3DMetal.
install_wine_devel() {
  echo "Step: Installing Wine Devel (Wine Staging 11.8 + OpenGL 3.2 patch)..."
  mkdir -p "$PORTABLE_DIR"

  target_app="$PORTABLE_DIR/Wine Devel.app"
  rm -rf "$target_app"

  archive="$WORK_DIR/wine-devel.zip"
  staging="$WORK_DIR/wine-devel-unpack"
  rm -rf "$staging"
  mkdir -p "$staging"

  echo "Downloading Wine Devel from $WINE_DEVEL_URL"
  download_file "$WINE_DEVEL_URL" "$archive"

  if command -v unzip >/dev/null 2>&1; then
    unzip -q "$archive" -d "$staging" || {
      echo "Failed to unzip wine-devel archive"
      exit 1
    }
  elif [ -x "$SEVENZ_BIN" ]; then
    "$SEVENZ_BIN" x -y -o"$staging" "$archive" >/dev/null || {
      echo "Failed to extract wine-devel archive with 7z"
      exit 1
    }
  else
    echo "Neither unzip nor 7z is available to extract the archive"
    exit 1
  fi

  unpacked_app="$(find "$staging" -maxdepth 2 -type d -name 'Wine Devel.app' | head -n1)"
  if [ -z "$unpacked_app" ] || [ ! -d "$unpacked_app" ]; then
    echo "Unpacked archive did not contain 'Wine Devel.app'"
    exit 1
  fi
  mv "$unpacked_app" "$target_app"

  # Ensure executables stayed executable through the zip round trip.
  chmod +x "$target_app/Contents/MacOS/wine" 2>/dev/null || true
  find "$target_app/Contents/Resources/wine/bin" -type f -exec chmod +x {} \; 2>/dev/null || true

  # Clear quarantine so Gatekeeper does not flag every binary in the tree.
  xattr -dr com.apple.quarantine "$target_app" 2>/dev/null || true

  # Verify the OpenGL 3.2 patch landed: winemac.so's forward-compat gate at
  # file offset 0x313ff must be 0xeb (jmp), not 0x75 (jne). Mirrors the
  # D3DMetal ntdll integrity check above.
  winemac="$target_app/Contents/Resources/wine/lib/wine/x86_64-unix/winemac.so"
  if [ -f "$winemac" ]; then
    gate_byte="$(xxd -s 0x313ff -l 1 -p "$winemac" 2>/dev/null || true)"
    if [ "$gate_byte" = "eb" ]; then
      echo "Verified: Wine Devel winemac.so has the OpenGL 3.2 forward-compat patch"
    else
      echo "WARNING: Wine Devel winemac.so gate byte is 0x$gate_byte; expected 0xeb (GL 3.2 patch)"
    fi
  fi

  write_component_version "wine_devel" "11.8-gl32"
  echo "Wine Devel installed to $target_app"
}

uninstall_wine_devel() {
  echo "Step: Uninstalling Wine Devel..."
  rm -rf "$PORTABLE_DIR/Wine Devel.app"
  grep -v "^wine_devel=" "$VERSION_MARKER" > "${VERSION_MARKER}.tmp" 2>/dev/null || true
  mv "${VERSION_MARKER}.tmp" "$VERSION_MARKER" 2>/dev/null || true
  echo "Wine Devel removed."
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

find_wine_win64_lib() {
  for wine_app in "Wine Staging.app" "Wine Stable.app"; do
    candidate="$PORTABLE_DIR/$wine_app/Contents/Resources/wine/lib/wine/x86_64-windows"
    if [ -d "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
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

  echo "Step: Fetching latest DXMT release from GitHub..."
  api_response=$(curl -s --connect-timeout 20 "https://api.github.com/repos/3Shain/dxmt/releases/latest" 2>/dev/null || true)
  if [ -z "$api_response" ]; then
    echo "Failed to contact GitHub API for DXMT, falling back to default URL"
    dxmt_url="$DXMT_DEFAULT_URL"
    dxmt_tag="unknown"
  else
    dxmt_url=$(printf '%s' "$api_response" | grep '"browser_download_url"' | grep '\.tar\.gz' | head -n1 | sed 's/.*"browser_download_url": *"\([^"]*\)".*/\1/')
    dxmt_tag=$(printf '%s' "$api_response" | grep '"tag_name"' | head -n1 | sed 's/.*"tag_name": *"\([^"]*\)".*/\1/')
    if [ -z "$dxmt_url" ]; then
      echo "No .tar.gz found in latest DXMT release, falling back to default URL"
      dxmt_url="$DXMT_DEFAULT_URL"
      dxmt_tag="unknown"
    fi
  fi

  echo "Step: Downloading DXMT $dxmt_tag..."
  download_file "$dxmt_url" "$archive"
  tar -xzf "$archive" -C "$unpack_dir" --strip-components=1

  # Pick the x86_64-windows directory for 64-bit PE DLLs
  win64_dir=""
  for candidate in \
    "$unpack_dir/x86_64-windows" \
    "$unpack_dir/x64-windows" \
    "$unpack_dir/x64" \
    "$unpack_dir/x86_64" \
    "$unpack_dir"; do
    if [ -f "$candidate/d3d11.dll" ] && [ -f "$candidate/dxgi.dll" ]; then
      win64_dir="$candidate"
      break
    fi
  done

  if [ -z "$win64_dir" ]; then
    echo "DXMT archive did not contain the expected x86_64 d3d11.dll and dxgi.dll"
    exit 1
  fi

  # Install Unix .so bridge files into the portable Wine lib directory
  unix64_dir=""
  for candidate in \
    "$unpack_dir/x86_64-unix" \
    "$unpack_dir/x64-unix"; do
    if [ -d "$candidate" ]; then
      unix64_dir="$candidate"
      break
    fi
  done

  # Find the portable Wine's x86_64-unix lib dir to install the .so files
  wine_unix_lib=""
  for wine_app in "Wine Staging.app" "Wine Stable.app"; do
    candidate="$PORTABLE_DIR/$wine_app/Contents/Resources/wine/lib/wine/x86_64-unix"
    if [ -d "$candidate" ]; then
      wine_unix_lib="$candidate"
      break
    fi
  done

  # Find the portable Wine lib dirs
  wine_unix_lib=""
  wine_win64_lib=""
  for wine_app in "Wine Staging.app" "Wine Stable.app"; do
    wine_base="$PORTABLE_DIR/$wine_app/Contents/Resources/wine/lib/wine"
    if [ -d "$wine_base/x86_64-unix" ]; then
      wine_unix_lib="$wine_base/x86_64-unix"
      wine_win64_lib="$wine_base/x86_64-windows"
      break
    fi
  done

  if [ -z "$wine_unix_lib" ] || [ -z "$wine_win64_lib" ]; then
    echo "ERROR: Could not find portable Wine lib dirs — install Wine Stable or Staging first"
    exit 1
  fi

  # Backup original Wine PE DLLs into a stable directory before overwriting.
  # We skip a DLL if it already looks like a DXMT file (contains "winemetal" strings),
  # which handles the case where DXMT was installed before backup logic existed.
  WINE_ORIG_BACKUP_DIR="$PORTABLE_DIR/.dxmt-wine-backups"
  mkdir -p "$WINE_ORIG_BACKUP_DIR"
  echo "Backing up original Wine DLLs to $WINE_ORIG_BACKUP_DIR..."
  for dll in d3d11.dll dxgi.dll d3d10core.dll; do
    orig="$wine_win64_lib/$dll"
    backup="$WINE_ORIG_BACKUP_DIR/$dll"
    if [ -f "$orig" ] && [ ! -f "$backup" ]; then
      # Skip if the file is already a DXMT DLL (no backup would be valid)
      if strings "$orig" 2>/dev/null | grep -qi "winemetal"; then
        echo "Skipping backup of $dll — already a DXMT DLL (no original available)"
      else
        cp -f "$orig" "$backup"
        echo "Backed up: $dll"
      fi
    fi
  done

  # This is a builtin-dll build: PE DLLs replace Wine's own in its lib directory
  echo "Installing DXMT PE DLLs into Wine x86_64-windows lib..."
  for dll in d3d11.dll dxgi.dll winemetal.dll d3d10core.dll; do
    if [ -f "$win64_dir/$dll" ]; then
      cp -f "$win64_dir/$dll" "$wine_win64_lib/$dll"
    fi
  done

  # Install the Unix bridge (.so) into Wine's x86_64-unix lib
  echo "Installing DXMT Unix bridge (winemetal.so) into Wine x86_64-unix lib..."
  cp -f "$unix64_dir"/*.so "$wine_unix_lib/" 2>/dev/null || true

  # Codesign the .so files so macOS will load them
  echo "Codesigning DXMT bridge files..."
  find "$wine_unix_lib" -name "winemetal.so" -exec /usr/bin/codesign --force --sign - --timestamp=none {} \; 2>/dev/null || true

  # Also keep a copy in DXMT_DIR so _dxmt_available() detection works
  mkdir -p "$DXMT_DIR"
  for dll in d3d11.dll dxgi.dll winemetal.dll d3d10core.dll; do
    if [ -f "$win64_dir/$dll" ]; then
      cp -f "$win64_dir/$dll" "$DXMT_DIR/$dll"
    fi
  done

  write_component_version "dxmt" "$dxmt_tag"
  echo "DXMT $dxmt_tag installed successfully"
}

install_gptk_dlls() {
  if [ -z "$GPTK_DIR" ]; then
    GPTK_DIR="$HOME/gptk"
  fi
  GPTK_DLL_DIR="$GPTK_DIR/lib/wine/x86_64-windows"
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

uninstall_wine() {
  echo "Step: Uninstalling Wine Stable..."
  rm -rf "$PORTABLE_DIR/Wine Stable.app"
  grep -v "^wine_stable=" "$VERSION_MARKER" > "${VERSION_MARKER}.tmp" 2>/dev/null || true
  mv "${VERSION_MARKER}.tmp" "$VERSION_MARKER" 2>/dev/null || true
  echo "Wine Stable removed."
}

uninstall_wine_staging() {
  echo "Step: Uninstalling Wine Staging..."
  rm -rf "$PORTABLE_DIR/Wine Staging.app"
  grep -v "^wine_staging=" "$VERSION_MARKER" > "${VERSION_MARKER}.tmp" 2>/dev/null || true
  mv "${VERSION_MARKER}.tmp" "$VERSION_MARKER" 2>/dev/null || true
  echo "Wine Staging removed."
}

uninstall_dxvk() {
  echo "Step: Uninstalling DXVK..."
  rm -f "$DXVK_INSTALL64/bin/d3d11.dll" "$DXVK_INSTALL64/bin/d3d10core.dll" 2>/dev/null || true
  rm -f "$DXVK_INSTALL32/bin/d3d11.dll" "$DXVK_INSTALL32/bin/d3d10core.dll" 2>/dev/null || true
  echo "DXVK removed."
}

uninstall_dxmt() {
  echo "Step: Uninstalling DXMT..."
  rm -f "$DXMT_DIR/d3d11.dll" "$DXMT_DIR/dxgi.dll" "$DXMT_DIR/d3d10core.dll" "$DXMT_DIR/winemetal.dll" 2>/dev/null || true
  # Restore original Wine PE DLLs
  wine_win64_lib="$(find_wine_win64_lib 2>/dev/null || true)"
  if [ -n "$wine_win64_lib" ]; then
    WINE_ORIG_BACKUP_DIR="$PORTABLE_DIR/.dxmt-wine-backups"
    has_backup=0
    for dll in d3d11.dll dxgi.dll d3d10core.dll; do
      if [ -f "$WINE_ORIG_BACKUP_DIR/$dll" ]; then
        has_backup=1
        break
      fi
    done
    if [ "$has_backup" = "1" ]; then
      echo "Restoring original Wine DLLs from backup..."
      for dll in d3d11.dll dxgi.dll d3d10core.dll; do
        backup="$WINE_ORIG_BACKUP_DIR/$dll"
        if [ -f "$backup" ]; then
          cp -f "$backup" "$wine_win64_lib/$dll"
          rm -f "$backup"
          echo "Restored: $dll"
        fi
      done
    else
      echo "No original Wine DLL backups found (DXMT was installed before backup support)."
      echo "Re-installing portable Wine to restore clean DLLs..."
      install_portable_wine_staging
    fi
    # Always remove winemetal.so from Wine's unix lib
    wine_unix_lib="$(dirname "$wine_win64_lib" | sed 's|x86_64-windows||')/x86_64-unix"
    rm -f "$wine_unix_lib/winemetal.so" 2>/dev/null || true
  fi
  grep -v "^dxmt=" "$VERSION_MARKER" > "${VERSION_MARKER}.tmp" 2>/dev/null || true
  mv "${VERSION_MARKER}.tmp" "$VERSION_MARKER" 2>/dev/null || true
  echo "DXMT removed."
}

uninstall_vkd3d() {
  echo "Step: Uninstalling VKD3D-Proton..."
  rm -rf "$VKD3D_DIR" 2>/dev/null || true
  echo "VKD3D-Proton removed."
}

WINEOPENXR_SRC_DIR="$PORTABLE_DIR/wineopenxr-src"
WINEOPENXR_REPO_URL="https://github.com/monofunc/wineopenxr"

# DXMT OpenXR fork (monofunc/dxmt @ feature/openxr): DXMT's Metal D3D11/10
# translation plus OpenXR passthrough for VR. Upstream publishes no releases, so
# this is built from source with meson + the mingw cross-file (mirroring
# build_dlls.sh). Set DXMT_OPENXR_URL to a prebuilt "-builtin" tarball to skip
# the from-source build; set MNC_WINE_BUILD_PATH to point meson at a Wine build
# tree instead of the installed portable Wine.
DXMT_OPENXR_SRC_DIR="$PORTABLE_DIR/dxmt-openxr-src"
DXMT_OPENXR_REPO_URL="https://github.com/monofunc/dxmt"
DXMT_OPENXR_BRANCH="feature/openxr"
DXMT_OPENXR_DIR="${DXMT_OPENXR_DIR:-$HOME/dxmt-openxr}"
DXMT_OPENXR_VENV="$PORTABLE_DIR/dxmt-build-venv"
# Wine source for the from-source build. dxmt's builtin DLLs link against Wine's
# dev libs (winecrt0/ntdll/dbghelp + winebuild) which the runtime Gcenx Wines do
# NOT ship, so meson needs a built Wine *source* tree (-Dwine_build_path). 11.0
# matches the runtime Wine. Override the tree with MNC_WINE_BUILD_PATH.
WINE_SOURCE_REPO="https://gitlab.winehq.org/wine/wine.git"
WINE_SOURCE_TAG="wine-11.0"

# Monado — the host OpenXR runtime. The wineopenxr bridge forwards D3D11 OpenXR
# calls to whatever runtime is registered at
# /usr/local/share/openxr/1/active_runtime.json (or XR_RUNTIME_JSON). That
# runtime gets dlopen'd INTO the x86_64 (Rosetta) Wine process, so it MUST be
# x86_64 — an arm64 Monado fails with "incompatible architecture (have 'arm64',
# need 'x86_64')". We build it x86_64 from source against the x86_64 Homebrew
# deps and register it. Override the install location with MONADO_DIR.
MONADO_SRC_DIR="$PORTABLE_DIR/monado-src"
MONADO_REPO_URL="https://gitlab.freedesktop.org/monado/monado.git"
# Pinned to the exact commit validated to build x86_64 on macOS (with the patches
# in _monado_build_from_source). Monado main periodically breaks on macOS, so we
# don't track HEAD. Override with MONADO_COMMIT to try a newer tree.
MONADO_COMMIT="${MONADO_COMMIT:-f1d2affa0350f727538cf0dc9c93e1a85f06a854}"
MONADO_RUNTIME_MANIFEST="$PORTABLE_DIR/monado/active_runtime.json"

find_wineopenxr_wine_tree() {

  for wine_app in "Wine D3DMetal.app" "Wine Staging.app" "Wine Stable.app"; do
    base="$PORTABLE_DIR/$wine_app/Contents/Resources/wine/lib/wine"
    if [ -d "$base/x86_64-windows" ] && [ -d "$base/x86_64-unix" ]; then
      echo "$base"
      return 0
    fi
  done
  return 1
}

install_wineopenxr() {
  echo "Step: Installing wineopenxr (D3D11 OpenXR bridge for macOS)..."

  wine_lib="$(find_wineopenxr_wine_tree || true)"
  if [ -z "$wine_lib" ]; then
    echo "ERROR: No portable Wine tree found. Install Wine Stable, Wine Staging,"
    echo "       or Wine D3DMetal before installing wineopenxr."
    exit 1
  fi
  echo "Targeting Wine tree: $wine_lib"

  if [ "${MNC_SUDOLESS:-0}" != "1" ]; then
    ensure_brew
    "$BREW_BIN" install cmake mingw-w64 || true
  fi

  if ! command -v cmake >/dev/null 2>&1; then
    echo "ERROR: cmake is required to build wineopenxr but was not found on PATH."
    echo "       Install it via Homebrew: brew install cmake mingw-w64"
    exit 1
  fi
  if ! command -v x86_64-w64-mingw32-gcc >/dev/null 2>&1; then
    echo "WARNING: mingw-w64 not found on PATH; the PE half of wineopenxr will"
    echo "         likely fail to build. Install with: brew install mingw-w64"
  fi


  mkdir -p "$PORTABLE_DIR"
  if [ -d "$WINEOPENXR_SRC_DIR/.git" ]; then
    echo "Updating existing wineopenxr checkout at $WINEOPENXR_SRC_DIR"
    (cd "$WINEOPENXR_SRC_DIR" && "$GIT_BIN" fetch --depth=1 origin && "$GIT_BIN" reset --hard origin/HEAD) || {
      echo "Failed to update wineopenxr clone — removing and re-cloning"
      rm -rf "$WINEOPENXR_SRC_DIR"
    }
  fi
  if [ ! -d "$WINEOPENXR_SRC_DIR/.git" ]; then
    echo "Cloning wineopenxr from $WINEOPENXR_REPO_URL"
    "$GIT_BIN" clone --recurse-submodules "$WINEOPENXR_REPO_URL" "$WINEOPENXR_SRC_DIR" || {
      echo "Failed to clone wineopenxr"
      exit 1
    }
  fi


  (cd "$WINEOPENXR_SRC_DIR" && "$GIT_BIN" submodule update --init --recursive) || {
    echo "Failed to initialise wineopenxr submodules"
    exit 1
  }


  build_dir="$WINEOPENXR_SRC_DIR/build"
  rm -rf "$build_dir"
  echo "Configuring wineopenxr (cmake -B build)..."
  (cd "$WINEOPENXR_SRC_DIR" && cmake -B build) || {
    echo "wineopenxr cmake configure failed"
    exit 1
  }
  echo "Building wineopenxr (cmake --build build)..."
  (cd "$WINEOPENXR_SRC_DIR" && cmake --build build) || {
    echo "wineopenxr build failed"
    exit 1
  }

  pe_dll="$build_dir/src/pe/wineopenxr.dll"
  unix_so="$build_dir/src/unix/wineopenxr.so"
  if [ ! -f "$pe_dll" ] || [ ! -f "$unix_so" ]; then
    echo "ERROR: wineopenxr build did not produce expected artifacts:"
    echo "       $pe_dll"
    echo "       $unix_so"
    exit 1
  fi

  echo "Installing wineopenxr.dll into $wine_lib/x86_64-windows"
  cp -f "$pe_dll"  "$wine_lib/x86_64-windows/wineopenxr.dll"
  echo "Installing wineopenxr.so into $wine_lib/x86_64-unix"
  cp -f "$unix_so" "$wine_lib/x86_64-unix/wineopenxr.so"


  manifest_src=""
  for cand in \
    "$WINEOPENXR_SRC_DIR/manifests/wineopenxr64.json" \
    "$build_dir/manifests/wineopenxr64.json"; do
    if [ -f "$cand" ]; then
      manifest_src="$cand"
      break
    fi
  done
  if [ -n "$manifest_src" ]; then
    mkdir -p "$PORTABLE_DIR/wineopenxr"
    cp -f "$manifest_src" "$PORTABLE_DIR/wineopenxr/wineopenxr64.json"
    echo "Stored OpenXR manifest at $PORTABLE_DIR/wineopenxr/wineopenxr64.json"
  else
    echo "WARNING: wineopenxr64.json not found in the source tree; per-prefix"
    echo "         registration will be skipped."
  fi


  /usr/bin/codesign --force --sign - --timestamp=none "$wine_lib/x86_64-unix/wineopenxr.so" 2>/dev/null || true

 
  if [ -n "${PREFIX_DIR:-}" ] && [ -d "$PREFIX_DIR" ]; then
    register_wineopenxr_in_prefix "$PREFIX_DIR" || true
  fi

  write_component_version "wineopenxr" "monofunc-head"
  echo "wineopenxr installed successfully"
}

register_wineopenxr_in_prefix() {
  prefix="$1"
  if [ -z "$prefix" ] || [ ! -d "$prefix" ]; then
    echo "register_wineopenxr_in_prefix: bad or missing prefix: $prefix"
    return 1
  fi

  manifest_src="$PORTABLE_DIR/wineopenxr/wineopenxr64.json"
  if [ ! -f "$manifest_src" ]; then
    echo "register_wineopenxr_in_prefix: manifest missing at $manifest_src — install wineopenxr first"
    return 1
  fi

 
  wine_bin=""
  for wine_app in "Wine D3DMetal.app" "Wine Staging.app" "Wine Stable.app"; do
    cand="$PORTABLE_DIR/$wine_app/Contents/Resources/wine/bin/wine"
    if [ -x "$cand" ]; then
      wine_bin="$cand"
      break
    fi
  done
  if [ -z "$wine_bin" ]; then
    echo "register_wineopenxr_in_prefix: no portable wine binary found"
    return 1
  fi

  echo "Registering wineopenxr in prefix: $prefix"


  sys32="$prefix/drive_c/windows/system32"
  if [ -d "$sys32" ]; then
    pe_dll_in_tree=""
    for wine_app in "Wine D3DMetal.app" "Wine Staging.app" "Wine Stable.app"; do
      cand="$PORTABLE_DIR/$wine_app/Contents/Resources/wine/lib/wine/x86_64-windows/wineopenxr.dll"
      if [ -f "$cand" ]; then pe_dll_in_tree="$cand"; break; fi
    done
    if [ -n "$pe_dll_in_tree" ]; then
      cp -f "$pe_dll_in_tree" "$sys32/wineopenxr.dll"
    fi
  fi

  mkdir -p "$prefix/drive_c/openxr"
  cp -f "$manifest_src" "$prefix/drive_c/openxr/wineopenxr64.json"


  WINEPREFIX="$prefix" arch -x86_64 "$wine_bin" reg add \
    'HKLM\Software\Khronos\OpenXR\1' /v ActiveRuntime /t REG_SZ \
    /d 'C:\openxr\wineopenxr64.json' /f >/dev/null 2>&1 || {
      echo "WARNING: failed to write OpenXR ActiveRuntime registry value in $prefix"
      return 1
    }

  echo "wineopenxr registered as the active OpenXR runtime in $prefix"
}

uninstall_wineopenxr() {
  echo "Step: Uninstalling wineopenxr..."
  for wine_app in "Wine D3DMetal.app" "Wine Staging.app" "Wine Stable.app"; do
    base="$PORTABLE_DIR/$wine_app/Contents/Resources/wine/lib/wine"
    rm -f "$base/x86_64-windows/wineopenxr.dll" 2>/dev/null || true
    rm -f "$base/x86_64-unix/wineopenxr.so" 2>/dev/null || true
  done
  rm -rf "$PORTABLE_DIR/wineopenxr" 2>/dev/null || true
  rm -rf "$WINEOPENXR_SRC_DIR" 2>/dev/null || true
  grep -v "^wineopenxr=" "$VERSION_MARKER" > "${VERSION_MARKER}.tmp" 2>/dev/null || true
  mv "${VERSION_MARKER}.tmp" "$VERSION_MARKER" 2>/dev/null || true
  echo "wineopenxr removed."
}

# DXMT-family backends target the REGULAR Wine (Staging/Stable) — NOT the
# D3DMetal wine. DXMT and D3DMetal are different, mutually-exclusive backends;
# this mirrors stock install_dxmt's target selection.
find_dxmt_wine_tree() {
  for wine_app in "Wine Staging.app" "Wine Stable.app"; do
    base="$PORTABLE_DIR/$wine_app/Contents/Resources/wine/lib/wine"
    if [ -d "$base/x86_64-windows" ] && [ -d "$base/x86_64-unix" ]; then
      echo "$base"
      return 0
    fi
  done
  return 1
}

# Resolve a built Wine 11.0 SOURCE tree for meson -Dwine_build_path. It must
# contain winecrt0/ntdll/dbghelp import libs + tools/winebuild (a runtime Wine
# does not). Order: MNC_WINE_BUILD_PATH override, then known build locations.
find_wine_build_tree() {
  if [ -n "${MNC_WINE_BUILD_PATH:-}" ] && [ -d "$MNC_WINE_BUILD_PATH" ]; then
    echo "$MNC_WINE_BUILD_PATH"
    return 0
  fi
  for c in "$HOME/src/wine-11.0/build64" "$HOME/src/wine-11.0/build" "$PORTABLE_DIR/wine-src/build64"; do
    if [ -f "$c/tools/winebuild/winebuild" ] && \
       { [ -f "$c/dlls/winecrt0/x86_64-windows/libwinecrt0.a" ] || [ -f "$c/libs/winecrt0/x86_64-windows/libwinecrt0.a" ]; }; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

# Build monofunc/dxmt (feature/openxr) from source. Encodes the full
# Apple-Silicon toolchain recipe. On success sets globals pe_src/so_src_dir/
# dxmt_tag and returns 0; returns 1 on any failure. Called as
# `_dxmt_openxr_build_from_source || exit 1` (so set -e is suspended inside —
# every critical step is guarded explicitly).
_dxmt_openxr_build_from_source() {
  echo "Step: Building monofunc/dxmt ($DXMT_OPENXR_BRANCH) from source..."

  # Build deps (skipped under MNC_SUDOLESS — the app context — where they must
  # already be present; the bundled/URL prebuilt paths avoid needing them).
  if [ "${MNC_SUDOLESS:-0}" != "1" ]; then
    ensure_brew
    "$BREW_BIN" install ninja mingw-w64 pkg-config bison flex python@3.12 >/dev/null 2>&1 || true
    # The native airconv tool is x86_64, so it needs the x86_64 (Rosetta /usr/local) LLVM 15.
    [ -d /usr/local/opt/llvm@15 ] || arch -x86_64 /usr/local/bin/brew install llvm@15 >/dev/null 2>&1 || true
  fi

  # Toolchain PATH: mingw + bison/flex (both Homebrew prefixes) ahead of the rest.
  PATH="/opt/homebrew/bin:/usr/local/bin:/usr/local/opt/bison/bin:/usr/local/opt/flex/bin:/opt/homebrew/opt/bison/bin:/opt/homebrew/opt/flex/bin:$PATH"
  export PATH

  command -v ninja >/dev/null 2>&1 || { echo "ERROR: ninja not found (brew install ninja)"; return 1; }
  command -v x86_64-w64-mingw32-gcc >/dev/null 2>&1 || { echo "ERROR: mingw-w64 not found (brew install mingw-w64)"; return 1; }

  # meson: the system meson may be the broken 1.10.1 (crashes with
  # 'KeyError: build.cpp_importstd' on dxmt's build.cpp_std default). Pin a good
  # version in an isolated venv so we never touch the user's global Python.
  MESON_BIN=""
  if [ -x "$DXMT_OPENXR_VENV/bin/meson" ] || python3 -m venv "$DXMT_OPENXR_VENV" >/dev/null 2>&1; then
    "$DXMT_OPENXR_VENV/bin/pip" install -q --disable-pip-version-check "meson==1.10.2" >/dev/null 2>&1 || true
    [ -x "$DXMT_OPENXR_VENV/bin/meson" ] && MESON_BIN="$DXMT_OPENXR_VENV/bin/meson"
  fi
  if [ -z "$MESON_BIN" ]; then
    MESON_BIN="$(command -v meson 2>/dev/null || true)"
    [ -n "$MESON_BIN" ] || { echo "ERROR: meson unavailable and venv setup failed (pip install meson==1.10.2)"; return 1; }
    [ "$("$MESON_BIN" --version 2>/dev/null)" = "1.10.1" ] && \
      echo "WARNING: meson 1.10.1 is known-broken for this build; install 1.10.2 (pip install meson==1.10.2)."
  fi
  echo "Using meson: $MESON_BIN ($("$MESON_BIN" --version 2>/dev/null))"

  # Native LLVM 15 must be x86_64 (the native airconv tool is x86_64; the arm64
  # Homebrew llvm@15 fails to link it).
  LLVM_PATH=""
  for cand in /usr/local/opt/llvm@15 /usr/local/opt/llvm; do
    [ -d "$cand" ] && { LLVM_PATH="$cand"; break; }
  done
  if [ -z "$LLVM_PATH" ]; then
    echo "ERROR: x86_64 LLVM 15 not found at /usr/local/opt/llvm@15."
    echo "       Install with: arch -x86_64 /usr/local/bin/brew install llvm@15"
    return 1
  fi
  echo "Using native LLVM (x86_64): $LLVM_PATH"

  # Metal shader compiler — ships with full Xcode + the Metal toolchain; the
  # Command Line Tools lack it. Point DEVELOPER_DIR at Xcode if needed.
  if ! xcrun --find metal >/dev/null 2>&1; then
    found_metal=0
    for xc in /Applications/Xcode.app /Applications/Xcode-*.app; do
      [ -d "$xc" ] || continue
      if DEVELOPER_DIR="$xc/Contents/Developer" xcrun --find metal >/dev/null 2>&1; then
        DEVELOPER_DIR="$xc/Contents/Developer"; export DEVELOPER_DIR; found_metal=1; break
      fi
    done
    [ "$found_metal" = "1" ] || { echo "ERROR: Metal compiler not found. Install full Xcode + Metal toolchain (CLT lacks 'metal')."; return 1; }
  fi
  echo "Metal compiler: $(xcrun --find metal 2>/dev/null)"

  # Wine 11.0 build tree (winecrt0/ntdll/dbghelp + winebuild).
  WBP="$(find_wine_build_tree || true)"
  if [ -z "$WBP" ]; then
    echo "No built Wine 11.0 source tree found — cloning + building Wine 11.0 (SLOW, ~20-40 min)..."
    wine_src="$HOME/src/wine-11.0"
    if [ ! -d "$wine_src/.git" ]; then
      "$GIT_BIN" clone "$WINE_SOURCE_REPO" --branch "$WINE_SOURCE_TAG" "$wine_src" || { echo "ERROR: wine clone failed"; return 1; }
    fi
    ( cd "$wine_src" && rm -rf build64 && mkdir build64 && cd build64 && \
      ../configure --enable-win64 --enable-archs=i386,x86_64 >/dev/null && \
      make -j"$(sysctl -n hw.ncpu)" ) || {
        echo "ERROR: Wine build failed. Set MNC_WINE_BUILD_PATH to a built Wine tree,"
        echo "       or supply a prebuilt fork via DXMT_OPENXR_URL / bundled Resources/dxmt-openxr."
        return 1
      }
    WBP="$wine_src/build64"
  fi
  echo "Using Wine build tree: $WBP"

  # Clone / update the fork via the bundled git.
  mkdir -p "$PORTABLE_DIR"
  if [ -d "$DXMT_OPENXR_SRC_DIR/.git" ]; then
    ( cd "$DXMT_OPENXR_SRC_DIR" && "$GIT_BIN" fetch --depth=1 origin "$DXMT_OPENXR_BRANCH" \
      && "$GIT_BIN" checkout "$DXMT_OPENXR_BRANCH" && "$GIT_BIN" reset --hard "origin/$DXMT_OPENXR_BRANCH" ) \
      || rm -rf "$DXMT_OPENXR_SRC_DIR"
  fi
  if [ ! -d "$DXMT_OPENXR_SRC_DIR/.git" ]; then
    "$GIT_BIN" clone --branch "$DXMT_OPENXR_BRANCH" --recurse-submodules \
      "$DXMT_OPENXR_REPO_URL" "$DXMT_OPENXR_SRC_DIR" || { echo "ERROR: fork clone failed"; return 1; }
  fi
  ( cd "$DXMT_OPENXR_SRC_DIR" && "$GIT_BIN" submodule update --init --recursive ) \
    || { echo "ERROR: submodule init failed"; return 1; }

  cross_file="$DXMT_OPENXR_SRC_DIR/build-win64.txt"
  [ -f "$cross_file" ] || { echo "ERROR: cross-file missing: $cross_file"; return 1; }

  echo "Configuring (meson setup)..."
  rm -rf "$DXMT_OPENXR_SRC_DIR/build"
  ( cd "$DXMT_OPENXR_SRC_DIR" && "$MESON_BIN" setup build \
      --cross-file "$cross_file" \
      -Dnative_llvm_path="$LLVM_PATH" \
      -Dwine_build_path="$WBP" \
      --buildtype release --strip ) || { echo "ERROR: meson configure failed"; return 1; }
  echo "Compiling (ninja)..."
  ( cd "$DXMT_OPENXR_SRC_DIR" && ninja -C build ) || { echo "ERROR: ninja build failed"; return 1; }

  pe_src="$WORK_DIR/dxmt-openxr-pe"; mkdir -p "$pe_src"
  so_src_dir="$WORK_DIR/dxmt-openxr-so"; mkdir -p "$so_src_dir"
  find "$DXMT_OPENXR_SRC_DIR/build" -name "*.dll" -exec cp -f {} "$pe_src/" \;
  find "$DXMT_OPENXR_SRC_DIR/build" -name "winemetal.so" -exec cp -f {} "$so_src_dir/" \;
  if [ ! -f "$pe_src/d3d11.dll" ] || [ ! -f "$pe_src/winemetal.dll" ]; then
    echo "ERROR: build did not produce the expected DLLs"
    return 1
  fi
  dxmt_tag="$( (cd "$DXMT_OPENXR_SRC_DIR" && "$GIT_BIN" rev-parse --short HEAD) 2>/dev/null || echo "feature-openxr")"
  return 0
}

install_dxmt_openxr() {
  echo "Step: Installing DXMT + OpenXR (monofunc/dxmt @ $DXMT_OPENXR_BRANCH)..."

  # DXMT-family -> install into the REGULAR Wine (Staging/Stable), NOT D3DMetal.
  wine_lib="$(find_dxmt_wine_tree || true)"
  if [ -z "$wine_lib" ]; then
    echo "ERROR: No regular Wine found. Install Wine Stable (or Staging) first —"
    echo "       DXMT runs under the regular Wine, not the D3DMetal wine."
    exit 1
  fi
  wine_win64_lib="$wine_lib/x86_64-windows"
  wine_unix_lib="$wine_lib/x86_64-unix"
  echo "Targeting regular Wine tree: $wine_lib"
  mkdir -p "$DXMT_OPENXR_DIR"

  script_dir="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
  pe_src=""
  so_src_dir=""
  dxmt_tag=""

  # Source priority: bundled prebuilt (drop-in) -> prebuilt URL -> from source.
  if [ -n "$script_dir" ] && [ -f "$script_dir/dxmt-openxr/d3d11.dll" ]; then
    echo "Using bundled prebuilt DXMT OpenXR DLLs: $script_dir/dxmt-openxr"
    pe_src="$script_dir/dxmt-openxr"
    so_src_dir="$script_dir/dxmt-openxr"
    dxmt_tag="bundled"
  elif [ -n "${DXMT_OPENXR_URL:-}" ]; then
    echo "Downloading prebuilt DXMT OpenXR from $DXMT_OPENXR_URL ..."
    archive="$WORK_DIR/dxmt-openxr.tar.gz"
    unpack_dir="$WORK_DIR/dxmt-openxr-unpack"
    rm -rf "$unpack_dir"; mkdir -p "$unpack_dir"
    download_file "$DXMT_OPENXR_URL" "$archive"
    tar -xzf "$archive" -C "$unpack_dir" --strip-components=1 2>/dev/null || tar -xzf "$archive" -C "$unpack_dir"
    for c in "$unpack_dir/x86_64-windows" "$unpack_dir/x64-windows" "$unpack_dir/x64" "$unpack_dir/x86_64" "$unpack_dir"; do
      if [ -f "$c/d3d11.dll" ] && [ -f "$c/dxgi.dll" ]; then pe_src="$c"; break; fi
    done
    for c in "$unpack_dir/x86_64-unix" "$unpack_dir/x64-unix" "$unpack_dir"; do
      if [ -f "$c/winemetal.so" ]; then so_src_dir="$c"; break; fi
    done
    [ -n "$pe_src" ] || { echo "ERROR: prebuilt archive missing d3d11.dll/dxgi.dll"; exit 1; }
    dxmt_tag="prebuilt"
  else
    _dxmt_openxr_build_from_source || exit 1
  fi

  # Backup stock Wine PE DLLs (shared backup dir with DXMT) before overwriting,
  # so non-DXMT backends can restore them (_restore_wine_lib_from_dxmt_backup).
  WINE_ORIG_BACKUP_DIR="$PORTABLE_DIR/.dxmt-wine-backups"
  mkdir -p "$WINE_ORIG_BACKUP_DIR"
  for dll in d3d11.dll dxgi.dll d3d10core.dll; do
    orig="$wine_win64_lib/$dll"
    backup="$WINE_ORIG_BACKUP_DIR/$dll"
    if [ -f "$orig" ] && [ ! -f "$backup" ]; then
      if strings "$orig" 2>/dev/null | grep -qi "winemetal"; then
        echo "Skipping backup of $dll (already a DXMT dll)"
      else
        cp -f "$orig" "$backup" && echo "Backed up $dll"
      fi
    fi
  done

  # Install PE DLLs into the regular Wine x86_64-windows lib AND the staging dir
  # ($DXMT_OPENXR_DIR) that backend_server _dxmt_openxr_available() checks.
  echo "Installing DXMT OpenXR PE DLLs into $wine_win64_lib ..."
  for dll in d3d11.dll dxgi.dll d3d10core.dll winemetal.dll; do
    if [ -f "$pe_src/$dll" ]; then
      cp -f "$pe_src/$dll" "$wine_win64_lib/$dll"
      cp -f "$pe_src/$dll" "$DXMT_OPENXR_DIR/$dll"
    fi
  done

  # Install + codesign the Unix bridge (winemetal.so).
  if [ -n "$so_src_dir" ] && [ -f "$so_src_dir/winemetal.so" ]; then
    echo "Installing winemetal.so into $wine_unix_lib ..."
    cp -f "$so_src_dir/winemetal.so" "$wine_unix_lib/winemetal.so"
    cp -f "$so_src_dir/winemetal.so" "$DXMT_OPENXR_DIR/winemetal.so"
    /usr/bin/codesign --force --sign - --timestamp=none "$wine_unix_lib/winemetal.so" 2>/dev/null || true
  fi

  write_component_version "dxmt_openxr" "$dxmt_tag"
  echo "DXMT OpenXR fork ($dxmt_tag) installed into regular Wine: $wine_lib"
  echo "Pick the \"DXMT + OpenXR (VR, monofunc)\" graphics backend to use it."
}

uninstall_dxmt_openxr() {
  echo "Step: Uninstalling DXMT OpenXR fork..."
  # Restore stock Wine PE DLLs and scrub the fork's winemetal artefacts from the
  # REGULAR Wine only (the fork is never installed into the D3DMetal wine). If
  # stock DXMT is also installed, re-selecting it re-syncs its DLLs on next launch
  # (_prepare_game_for_backend), so this won't permanently break it.
  backup_dir="$PORTABLE_DIR/.dxmt-wine-backups"
  for wine_app in "Wine Staging.app" "Wine Stable.app"; do
    base="$PORTABLE_DIR/$wine_app/Contents/Resources/wine/lib/wine"
    win64="$base/x86_64-windows"
    unixl="$base/x86_64-unix"
    [ -d "$win64" ] || continue
    if [ -d "$backup_dir" ]; then
      for dll in d3d11.dll dxgi.dll d3d10core.dll; do
        [ -f "$backup_dir/$dll" ] && cp -f "$backup_dir/$dll" "$win64/$dll"
      done
    fi
    rm -f "$win64/winemetal.dll" 2>/dev/null || true
    rm -f "$unixl/winemetal.so" 2>/dev/null || true
  done
  rm -rf "$DXMT_OPENXR_DIR" 2>/dev/null || true
  rm -rf "$DXMT_OPENXR_SRC_DIR" 2>/dev/null || true
  rm -rf "$DXMT_OPENXR_VENV" 2>/dev/null || true
  grep -v "^dxmt_openxr=" "$VERSION_MARKER" > "${VERSION_MARKER}.tmp" 2>/dev/null || true
  mv "${VERSION_MARKER}.tmp" "$VERSION_MARKER" 2>/dev/null || true
  echo "DXMT OpenXR fork removed."
}

# Build Monado (host OpenXR runtime) as x86_64 under Rosetta against the x86_64
# Homebrew deps. The runtime dylib is dlopen'd into the x86_64 Wine process, so
# it MUST be x86_64 (an arm64 build fails with "incompatible architecture"). On
# success sets globals monado_dylib + monado_tag and returns 0; else returns 1.
# Called as `_monado_build_from_source || exit 1` (set -e is disabled inside, so
# every critical step is guarded explicitly).
_monado_build_from_source() {
  echo "Step: Building Monado (OpenXR runtime, x86_64) from source..."

  # Deps split by role (skipped under MNC_SUDOLESS — must already be present):
  #  - LIBRARY deps (x86_64, via Rosetta Homebrew): the libs/headers the x86_64
  #    target links against.
  #  - BUILD tools (cmake/ninja, NATIVE arm64): so the compiler runs natively and
  #    cross-compiles to x86_64. Running clang under Rosetta segfaults on the
  #    Eigen-heavy files at -O3, so we must NOT wrap the build in `arch -x86_64`.
  if [ "${MNC_SUDOLESS:-0}" != "1" ]; then
    if [ ! -x /usr/local/bin/brew ]; then
      echo "ERROR: x86_64 Homebrew (/usr/local/bin/brew) is required for the x86_64 Monado deps."
      echo "       Install Rosetta Homebrew first, then re-run."
      return 1
    fi
    arch -x86_64 /usr/local/bin/brew install eigen shaderc glslang \
      vulkan-headers vulkan-loader molten-vk pkg-config >/dev/null 2>&1 || true
    if [ -x /opt/homebrew/bin/brew ]; then
      /opt/homebrew/bin/brew install cmake ninja pkg-config >/dev/null 2>&1 || true
    elif [ -n "${BREW_BIN:-}" ]; then
      "$BREW_BIN" install cmake ninja pkg-config >/dev/null 2>&1 || true
    fi
  fi

  # Native build tools first on PATH; PKG_CONFIG_PATH still points at the x86_64
  # .pc files so the target links against the /usr/local (x86_64) deps.
  PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"; export PATH
  PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:/usr/local/share/pkgconfig:${PKG_CONFIG_PATH:-}"
  export PKG_CONFIG_PATH

  for t in cmake ninja pkg-config; do
    command -v "$t" >/dev/null 2>&1 || {
      echo "ERROR: $t not found (arch -x86_64 /usr/local/bin/brew install $t)"; return 1; }
  done

  # Metal compiler — ships with full Xcode + the Metal toolchain (the Command
  # Line Tools lack 'metal'). Point DEVELOPER_DIR at Xcode if needed.
  if ! xcrun --find metal >/dev/null 2>&1; then
    found_metal=0
    for xc in /Applications/Xcode.app /Applications/Xcode-*.app; do
      [ -d "$xc" ] || continue
      if DEVELOPER_DIR="$xc/Contents/Developer" xcrun --find metal >/dev/null 2>&1; then
        DEVELOPER_DIR="$xc/Contents/Developer"; export DEVELOPER_DIR; found_metal=1; break
      fi
    done
    [ "$found_metal" = "1" ] || {
      echo "ERROR: Metal compiler not found. Install full Xcode + Metal toolchain (CLT lacks 'metal')."; return 1; }
  fi
  echo "Metal compiler: $(xcrun --find metal 2>/dev/null)"

  # Clone via the bundled git, then pin to the validated commit (shallow
  # fetch-by-SHA; falls back to the default-branch HEAD if the server refuses a
  # SHA fetch). Pinning keeps the patches below matching and avoids Monado main's
  # recurring macOS breakage.
  mkdir -p "$PORTABLE_DIR"
  if [ ! -d "$MONADO_SRC_DIR/.git" ]; then
    "$GIT_BIN" clone --depth 1 "$MONADO_REPO_URL" "$MONADO_SRC_DIR" \
      || { echo "ERROR: Monado clone failed"; return 1; }
  fi
  ( cd "$MONADO_SRC_DIR" \
      && "$GIT_BIN" fetch --depth 1 origin "$MONADO_COMMIT" \
      && "$GIT_BIN" checkout -q FETCH_HEAD ) \
    || echo "WARNING: could not pin Monado to $MONADO_COMMIT; building current checkout"
  ( cd "$MONADO_SRC_DIR" && "$GIT_BIN" submodule update --init --recursive ) || true

  # Apple clang 21's optimizer SEGFAULTS compiling Monado's Eigen math at -O2/-O3.
  # Monado's `xrt-optimized-math` INTERFACE target forces -O3 (appended AFTER our
  # -O1 Release flag, so it wins). Pin that interface to -O1 — matched by target
  # name so it survives upstream changes to the inner generator expression.
  cf="$(find "$MONADO_SRC_DIR" -name CompilerFlags.cmake 2>/dev/null | head -1)"
  if [ -n "$cf" ]; then
    sed -i '' -E 's|(target_compile_options\(xrt-optimized-math INTERFACE )[^)]*\)|\1-O1)|' "$cf" 2>/dev/null || true
    echo "Patched xrt-optimized-math to -O1 in $cf"
  fi

  # macOS portability: Monado's IPC future impl names a static function `wait`,
  # which clashes with POSIX wait() from <sys/wait.h> ("static declaration of
  # 'wait' follows non-static declaration"). Rename just that function (not the
  # xrt_future `.wait` field). The ipc_client is compiled even in-process.
  fut="$MONADO_SRC_DIR/src/xrt/ipc/client/ipc_client_future.c"
  if [ -f "$fut" ]; then
    sed -i '' -e 's|^wait(struct xrt_future|future_wait(struct xrt_future|' \
              -e 's|xft->wait = wait;|xft->wait = future_wait;|' "$fut" 2>/dev/null || true
    echo "Patched ipc_client_future.c wait() -> future_wait()"
  fi

  # GL/GLES client support is off: macOS has no EGL, and a stray XQuartz
  # libGLESv2 otherwise trips Monado's "OPENGLES requires EGL" check. The VR app
  # reaches Monado over Vulkan (DXMT → Vulkan), so GL is unnecessary.
  # CMAKE_OSX_ARCHITECTURES=x86_64 cross-compiles with the native arm64 clang.
  # -O1: Apple clang 21's optimizer SEGFAULTS compiling Eigen's x86_64 path at
  # -O2/-O3 (frontend crash on m_base.cpp); -O1 builds cleanly and the runtime
  # only orchestrates Vulkan/Metal calls, so the perf cost is negligible.
  echo "Configuring Monado (cmake, cross-compiling to x86_64)..."
  rm -rf "$MONADO_SRC_DIR/build"
  cmake -S "$MONADO_SRC_DIR" -B "$MONADO_SRC_DIR/build" -G Ninja \
    -DCMAKE_OSX_ARCHITECTURES=x86_64 \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CXX_FLAGS_RELEASE="-O1 -DNDEBUG" \
    -DCMAKE_C_FLAGS_RELEASE="-O1 -DNDEBUG" \
    -DCMAKE_PREFIX_PATH=/usr/local \
    -DVULKAN_SDK=/usr/local \
    -DXRT_HAVE_OPENGL=OFF \
    -DXRT_HAVE_OPENGLES=OFF \
    -DXRT_HAVE_EGL=OFF \
    -DXRT_MODULE_IPC=OFF \
    -DXRT_FEATURE_SERVICE=OFF \
    -DXRT_FEATURE_STEAMVR_PLUGIN=OFF \
    || { echo "ERROR: Monado cmake configure failed (see output above)"; return 1; }
  # MODULE_IPC=OFF + SERVICE=OFF build the runtime fully IN-PROCESS (the dylib
  # carries the whole runtime, no separate monado-service daemon) — simpler for
  # the wineopenxr bridge, and it drops Monado's entire IPC subdir, whose
  # Linux-only ipc_server (#error "Need port", sys/epoll.h) can't build on macOS.
  # STEAMVR_PLUGIN is unneeded for OpenXR.

  echo "Compiling Monado (native clang → x86_64; this is slow)..."
  cmake --build "$MONADO_SRC_DIR/build" -j"$(sysctl -n hw.ncpu)" \
    || { echo "ERROR: Monado build failed"; return 1; }

  monado_dylib="$MONADO_SRC_DIR/build/src/xrt/targets/openxr/libopenxr_monado.dylib"
  if [ ! -f "$monado_dylib" ]; then
    monado_dylib="$(find "$MONADO_SRC_DIR/build" -name 'libopenxr_monado*.dylib' 2>/dev/null | head -1)"
  fi
  if [ -z "$monado_dylib" ] || [ ! -f "$monado_dylib" ]; then
    echo "ERROR: build did not produce libopenxr_monado.dylib"; return 1
  fi
  # Must be x86_64-loadable (the Wine process is x86_64 under Rosetta).
  if command -v lipo >/dev/null 2>&1 && ! lipo -archs "$monado_dylib" 2>/dev/null | grep -q "x86_64"; then
    echo "ERROR: built runtime is not x86_64: $(lipo -archs "$monado_dylib" 2>/dev/null)"; return 1
  fi
  monado_tag="$( (cd "$MONADO_SRC_DIR" && "$GIT_BIN" rev-parse --short HEAD) 2>/dev/null || echo "head")"
  return 0
}

# Register a built libopenxr_monado.dylib as the host OpenXR runtime. Writes a
# manifest into PORTABLE_DIR (the launcher points XR_RUNTIME_JSON at it — robust,
# needs no system write) and best-effort installs the system-wide
# /usr/local/share/openxr/1/active_runtime.json the OpenXR loader looks for.
register_monado_runtime() {
  dylib="$1"
  if [ -z "$dylib" ] || [ ! -f "$dylib" ]; then
    echo "register_monado_runtime: bad dylib: $dylib"; return 1
  fi
  manifest_json='{
    "file_format_version" : "1.0.0",
    "runtime" : {
        "library_path" : "'"$dylib"'"
    }
}'
  mkdir -p "$PORTABLE_DIR/monado"
  printf '%s\n' "$manifest_json" > "$MONADO_RUNTIME_MANIFEST"
  echo "Wrote OpenXR runtime manifest: $MONADO_RUNTIME_MANIFEST"

  sys_dir="/usr/local/share/openxr/1"
  if mkdir -p "$sys_dir" 2>/dev/null && printf '%s\n' "$manifest_json" > "$sys_dir/active_runtime.json" 2>/dev/null; then
    echo "Registered system-wide: $sys_dir/active_runtime.json"
  else
    echo "NOTE: could not write $sys_dir/active_runtime.json — the launcher will set"
    echo "      XR_RUNTIME_JSON=$MONADO_RUNTIME_MANIFEST at launch instead."
  fi
  return 0
}

install_monado_runtime() {
  echo "Step: Installing Monado OpenXR runtime (x86_64)..."

  # The runtime is only useful with the wineopenxr bridge (which forwards D3D11
  # OpenXR to it). Warn but continue if the bridge/Wine isn't installed yet.
  if ! find_wineopenxr_wine_tree >/dev/null 2>&1; then
    echo "NOTE: no portable Wine tree found yet — install Wine + wineopenxr to use VR."
  fi

  script_dir="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
  monado_dylib=""
  monado_tag=""
  payload_src=""

  # Source priority: bundled prebuilt (app Resources, zero deps) -> prebuilt
  # MONADO_URL tarball -> from-source. Prebuilt is STRONGLY preferred: the
  # from-source path failed in the wild on testers' machines (missing deps,
  # toolchain drift — "ninja: build stopped"). The payload is self-contained:
  # libopenxr_monado.dylib + @loader_path libjpeg/libvulkan + libMoltenVK with
  # an ICD manifest the launcher points VK_DRIVER_FILES at.
  if [ -n "$script_dir" ] && [ -f "$script_dir/monado-runtime/libopenxr_monado.dylib" ]; then
    echo "Using bundled prebuilt Monado runtime: $script_dir/monado-runtime"
    payload_src="$script_dir/monado-runtime"
    monado_tag="bundled"
  elif [ -n "${MONADO_URL:-}" ]; then
    echo "Downloading prebuilt Monado runtime from $MONADO_URL ..."
    archive="$WORK_DIR/monado-runtime.tar.gz"
    unpack_dir="$WORK_DIR/monado-unpack"
    rm -rf "$unpack_dir"; mkdir -p "$unpack_dir"
    download_file "$MONADO_URL" "$archive"
    tar -xzf "$archive" -C "$unpack_dir"
    for c in "$unpack_dir/monado-runtime" "$unpack_dir"; do
      if [ -f "$c/libopenxr_monado.dylib" ]; then payload_src="$c"; break; fi
    done
    [ -n "$payload_src" ] || { echo "ERROR: prebuilt archive missing libopenxr_monado.dylib"; exit 1; }
    monado_tag="prebuilt"
  fi

  if [ -n "$payload_src" ]; then
    # Install the self-contained payload into PORTABLE_DIR/monado and register
    # that copy (no Homebrew deps needed on the target machine at all).
    mkdir -p "$PORTABLE_DIR/monado"
    cp -f "$payload_src"/*.dylib "$PORTABLE_DIR/monado/"
    [ -f "$payload_src/MoltenVK_icd.json" ] && cp -f "$payload_src/MoltenVK_icd.json" "$PORTABLE_DIR/monado/"
    # cp preserves Homebrew's read-only mode; codesign needs write permission.
    chmod -R u+w "$PORTABLE_DIR/monado"
    for f in "$PORTABLE_DIR/monado"/*.dylib; do
      /usr/bin/codesign --force --sign - --timestamp=none "$f" 2>/dev/null || true
    done
    monado_dylib="$PORTABLE_DIR/monado/libopenxr_monado.dylib"
  else
    echo "No prebuilt Monado available — falling back to the from-source build."
    _monado_build_from_source || exit 1
  fi

  register_monado_runtime "$monado_dylib" || echo "WARNING: Monado built but registration failed."

  write_component_version "monado_runtime" "$monado_tag"
  echo "Monado OpenXR runtime ($monado_tag) installed: $monado_dylib"
  echo "Pick the \"DXMT + OpenXR (VR)\" backend on a VR title to use it."
}

uninstall_monado_runtime() {
  echo "Step: Uninstalling Monado OpenXR runtime..."
  # Only remove the system-wide manifest if it points at OUR build, so a user's
  # own runtime registration is never clobbered.
  sys_file="/usr/local/share/openxr/1/active_runtime.json"
  if [ -f "$sys_file" ] && { grep -q "$MONADO_SRC_DIR" "$sys_file" 2>/dev/null \
      || grep -q "$PORTABLE_DIR/monado" "$sys_file" 2>/dev/null; }; then
    rm -f "$sys_file" 2>/dev/null || true
    echo "Removed system-wide $sys_file"
  fi
  rm -rf "$PORTABLE_DIR/monado" 2>/dev/null || true
  rm -rf "$MONADO_SRC_DIR" 2>/dev/null || true
  grep -v "^monado_runtime=" "$VERSION_MARKER" > "${VERSION_MARKER}.tmp" 2>/dev/null || true
  mv "${VERSION_MARKER}.tmp" "$VERSION_MARKER" 2>/dev/null || true
  echo "Monado OpenXR runtime removed."
}

RPC_BRIDGE_DIR="$PORTABLE_DIR/rpc-bridge"
RPC_BRIDGE_URL="https://github.com/EnderIce2/rpc-bridge/releases/latest/download/bridge.zip"

install_rpc_bridge() {
  echo "Step: Installing rpc-bridge..."
  mkdir -p "$RPC_BRIDGE_DIR"
  archive="$WORK_DIR/rpc-bridge.zip"
  curl -fsSL "$RPC_BRIDGE_URL" -o "$archive"
  unzip -o -q "$archive" -d "$RPC_BRIDGE_DIR"

  launchd_sh="$RPC_BRIDGE_DIR/launchd.sh"
  if [ -f "$launchd_sh" ]; then
    chmod +x "$launchd_sh"
    "$launchd_sh" install || true
    plist="$HOME/Library/LaunchAgents/com.enderice2.rpc-bridge.plist"
    if [ -f "$plist" ]; then
      chmod 644 "$plist"
      launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null || true
    fi
    echo "rpc-bridge LaunchAgent installed."
  fi
  echo "rpc-bridge installed."
}

uninstall_rpc_bridge() {
  echo "Step: Uninstalling rpc-bridge..."
  plist="$HOME/Library/LaunchAgents/com.enderice2.rpc-bridge.plist"
  launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || \
    launchctl unload "$plist" 2>/dev/null || true
  launchd_sh="$RPC_BRIDGE_DIR/launchd.sh"
  if [ -f "$launchd_sh" ]; then
    "$launchd_sh" remove 2>/dev/null || true
  fi
  rm -rf "$RPC_BRIDGE_DIR"
  echo "rpc-bridge removed."
}

quick_setup() {
  ensure_rosetta
  install_portable_tools
  install_portable_wine
  install_wine_unified
  install_dxmt
}

if [ "${MNC_SUDOLESS:-0}" != "1" ] && [ "$ACTION" != "init_prefix" ] && [ "$ACTION" != "quick_setup" ]; then
  prime_sudo
  start_sudo_keepalive
fi

locate_wine_unified_bundle() {
  # mirror locate_wine_d3dmetal_bundle for the unified wine zip
  script_path="$0"
  case "$script_path" in /*) ;; *) script_path="$PWD/$script_path" ;; esac
  script_dir="$(cd "$(dirname "$script_path")" 2>/dev/null && pwd)" || script_dir=""
  candidates="
${WINE_UNIFIED_BUNDLE_PATH:-}
${RESOURCES_DIR:-}/wine-unified-bundle.zip
${script_dir}/wine-unified-bundle.zip
${script_dir}/../Resources/wine-unified-bundle.zip
${script_dir}/../../Resources/wine-unified-bundle.zip
$HOME/macndcheese/wine-unified-bundle.zip
$HOME/Library/Application Support/MacNCheese/wine-unified-bundle.zip
"
  while IFS= read -r c; do
    [ -z "$c" ] && continue
    [ -f "$c" ] && { printf '%s' "$c"; return 0; }
  done <<EOF
$candidates
EOF
  for root in /Applications "$HOME/Applications" "$HOME/Downloads"; do
    [ -d "$root" ] || continue
    found="$(find "$root" -maxdepth 5 -name 'wine-unified-bundle.zip' -type f 2>/dev/null | head -n1)"
    [ -n "$found" ] && [ -f "$found" ] && { printf '%s' "$found"; return 0; }
  done
  return 1
}

install_wine_unified() {
  # install the unified wine (build64 layout) into deps
  # prefers the bundled zip so a packaged app installs offline
  # dev fallback rsyncs from WINE_UNIFIED_SRC=/path/to/build64
  echo "Step: Installing unified wine..."
  mkdir -p "$PORTABLE_DIR"
  local dst bundle src
  dst="${PORTABLE_DIR}/wine-unified"

  bundle="$(locate_wine_unified_bundle || true)"
  if [ -n "$bundle" ]; then
    echo "Using unified wine bundle: $bundle"
    rm -rf "$dst"
    mkdir -p "$dst"
    if command -v unzip >/dev/null 2>&1; then
      unzip -q "$bundle" -d "$dst" || { echo "Failed to unzip unified wine bundle"; exit 1; }
    elif [ -x "$SEVENZ_BIN" ]; then
      "$SEVENZ_BIN" x -y -o"$dst" "$bundle" >/dev/null || { echo "Failed to extract unified wine bundle"; exit 1; }
    else
      echo "Neither unzip nor 7z available to extract the bundle"; exit 1
    fi
    find "$dst" -name 'wine' -type f -exec chmod +x {} \; 2>/dev/null || true
    xattr -dr com.apple.quarantine "$dst" 2>/dev/null || true
    stage_unified_d3d_pack "$dst"
    echo "install_wine_unified: done ($(du -sh "$dst" 2>/dev/null | cut -f1))"
    return 0
  fi

  src="${WINE_UNIFIED_SRC:-/Volumes/ASAFE/D3DMETALWINEDEV/wine-11.0-clean/build64}"
  if [ ! -x "$src/loader/wine" ]; then
    echo "install_wine_unified: no bundle found and no build at $src (set WINE_UNIFIED_SRC)" >&2
    exit 1
  fi
  echo "Bundling unified wine: $src -> $dst"
  mkdir -p "$dst"
  rsync -a --delete "$src/" "$dst/"
  stage_unified_d3d_pack "$dst"
  echo "install_wine_unified: done ($(du -sh "$dst" 2>/dev/null | cut -f1))"
}

stage_unified_d3d_pack() {
  # copy the d3d DLL pack the unified loader routes to into deps/wine-unified/mnc-d3d
  # source order: env, Resources next to us, the dev steam prefix system32
  local dst d3dsrc c
  dst="$1"
  d3dsrc=""
  for c in \
    "${MNC_UNIFIED_DLL_DIR:-}" \
    "${RESOURCES_DIR:-}/mnc-d3d" \
    "$HOME/macndcheese/mnc-d3d" \
    "/Volumes/ASAFE/steam-clean2/drive_c/windows/system32"; do
    [ -n "$c" ] && [ -f "$c/d3d11.dll" ] && { d3dsrc="$c"; break; }
  done
  if [ -z "$d3dsrc" ]; then
    echo "stage_unified_d3d_pack: WARNING no d3d DLL pack found (set MNC_UNIFIED_DLL_DIR)"
    return 0
  fi
  mkdir -p "$dst/mnc-d3d"
  # winegstreamer_game.dll is the game-side MF video bridge staged + re-pointed at launch
  for f in d3d11.dll dxgi.dll d3d10core.dll d3d10.dll d3d10_1.dll d3d12.dll d3d12core.dll \
           winemetal.dll d3d11_d3dm.dll dxgi_d3dm.dll d3d10core_d3dm.dll d3d10_d3dm.dll \
           d3d12_d3dm.dll d3d11_dxvk.dll d3d10core_dxvk.dll dxgi_dxvk.dll winegstreamer_game.dll; do
    [ -f "$d3dsrc/$f" ] && cp -f "$d3dsrc/$f" "$dst/mnc-d3d/$f"
  done
  echo "stage_unified_d3d_pack: staged d3d DLL pack from $d3dsrc"

  # the d3dmetal stubs link @rpath/libd3dshared.dylib so the native runtime ships too
  local nd
  nd=""
  for c in "$d3dsrc" "${MNC_D3DMETAL_NATIVE_DIR:-}" "$HOME/D3DMetalTesting/lib/external"; do
    [ -n "$c" ] && [ -f "$c/libd3dshared.dylib" ] && { nd="$c"; break; }
  done
  if [ -n "$nd" ]; then
    cp -f "$nd/libd3dshared.dylib" "$dst/mnc-d3d/" 2>/dev/null || true
    [ -d "$nd/D3DMetal.framework" ] && cp -R "$nd/D3DMetal.framework" "$dst/mnc-d3d/" 2>/dev/null || true
    echo "stage_unified_d3d_pack: staged d3dmetal native runtime from $nd"
  else
    echo "stage_unified_d3d_pack: WARNING no libd3dshared.dylib found (d3dmetal backend needs it)"
  fi
}

uninstall_wine_unified() {
  echo "Step: Uninstalling unified wine..."
  rm -rf "$PORTABLE_DIR/wine-unified"
  echo "Unified wine removed."
}

case "$ACTION" in
  install_tools)
    install_tools
    ;;
  install_wine)
    install_wine
    ;;
  install_wine_staging)
    install_portable_wine_staging
    ;;
  install_wine_unified)
    install_wine_unified
    ;;
  uninstall_wine_unified)
    uninstall_wine_unified
    ;;
  uninstall_wine)
    uninstall_wine
    ;;
  uninstall_wine_staging)
    uninstall_wine_staging
    ;;
  install_wine_devel)
    install_wine_devel
    ;;
  uninstall_wine_devel)
    uninstall_wine_devel
    ;;
  install_dxvk)
    install_dxvk
    ;;
  uninstall_dxvk)
    uninstall_dxvk
    ;;
  uninstall_dxmt)
    uninstall_dxmt
    ;;
  uninstall_vkd3d)
    uninstall_vkd3d
    ;;
  build_dxvk64)
    install_tools
    build_dxvk64
    ;;
  build_dxvk32)
    install_tools
    build_dxvk32
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
  install_rpc_bridge)
    install_rpc_bridge
    ;;
  uninstall_rpc_bridge)
    uninstall_rpc_bridge
    ;;
  install_wineopenxr)
    install_wineopenxr
    ;;
  uninstall_wineopenxr)
    uninstall_wineopenxr
    ;;
  install_dxmt_openxr)
    install_dxmt_openxr
    ;;
  uninstall_dxmt_openxr)
    uninstall_dxmt_openxr
    ;;
  install_monado_runtime)
    install_monado_runtime
    ;;
  uninstall_monado_runtime)
    uninstall_monado_runtime
    ;;
  register_wineopenxr_prefix)
    register_wineopenxr_in_prefix "$PREFIX_DIR"
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
