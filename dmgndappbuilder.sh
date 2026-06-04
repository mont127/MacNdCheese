#!/bin/bash
set -eu

cd ~/macndcheese

APP_NAME="MacNdCheese Launcher"

echo "=== ${APP_NAME} SwiftUI App Builder ==="


rm -rf build dist MacNCheese.dmg assets/icon.iconset assets/MacNCheese.icns
mkdir -p build assets/icon.iconset


echo "Creating app icon..."
if [ ! -f icon.png ]; then
    echo "ERROR: icon.png not found in $(pwd)"
    exit 1
fi

file icon.png
sips -g format -g pixelWidth -g pixelHeight icon.png

rm -rf assets/icon.iconset assets/MacNCheese.icns
mkdir -p assets/icon.iconset

sips -z 16 16     icon.png --out assets/icon.iconset/icon_16x16.png
sips -z 32 32     icon.png --out assets/icon.iconset/icon_16x16@2x.png
sips -z 32 32     icon.png --out assets/icon.iconset/icon_32x32.png
sips -z 64 64     icon.png --out assets/icon.iconset/icon_32x32@2x.png
sips -z 128 128   icon.png --out assets/icon.iconset/icon_128x128.png
sips -z 256 256   icon.png --out assets/icon.iconset/icon_128x128@2x.png
sips -z 256 256   icon.png --out assets/icon.iconset/icon_256x256.png
sips -z 512 512   icon.png --out assets/icon.iconset/icon_256x256@2x.png
sips -z 512 512   icon.png --out assets/icon.iconset/icon_512x512.png
sips -z 1024 1024 icon.png --out assets/icon.iconset/icon_512x512@2x.png

ICON_COUNT=$(find assets/icon.iconset -type f -name "*.png" | wc -l | tr -d ' ')
if [ "$ICON_COUNT" != "10" ]; then
    echo "ERROR: Expected 10 iconset PNG files, got $ICON_COUNT"
    find assets/icon.iconset -type f -maxdepth 1 -print
    exit 1
fi

ls -la assets/icon.iconset
if ! iconutil -c icns assets/icon.iconset -o assets/MacNCheese.icns; then
    if [ -f MacNCheese.icns ]; then
        echo "WARNING: iconutil rejected the generated iconset; using existing MacNCheese.icns"
        cp MacNCheese.icns assets/MacNCheese.icns
    else
        echo "ERROR: iconutil failed and no fallback MacNCheese.icns exists"
        exit 1
    fi
fi
ls -la assets/MacNCheese.icns


echo ""
echo "Building Swift executable (release)..."
cd MacNCheese-SwiftUI
swift build -c release 2>&1
SWIFT_BIN="$(swift build -c release --show-bin-path)/MacNCheese"
echo "Binary: $SWIFT_BIN"
cd ~/macndcheese


echo ""
echo "Creating .app bundle..."
APP_ROOT="build/${APP_NAME}.app"
CONTENTS="$APP_ROOT/Contents"
MACOS="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"
mkdir -p "$MACOS" "$RESOURCES"

cp "$SWIFT_BIN" "$MACOS/MacNCheese"

cp assets/MacNCheese.icns "$RESOURCES/MacNCheese.icns"

# Copy backend files
cp backend_server.py "$RESOURCES/backend_server.py"
cp installer.sh "$RESOURCES/installer.sh"
chmod +x "$RESOURCES/installer.sh"

# Bundle the prebuilt DXMT OpenXR fork DLLs (monofunc/dxmt feature/openxr).
# install_dxmt_openxr picks these up from Resources/dxmt-openxr/ as an instant
# drop-in, so end users never need the from-source toolchain (meson/llvm@15/
# Xcode Metal/wine-build-tree). Built once via installer.sh from-source path.
if [ -f dxmt-openxr/d3d11.dll ] && [ -f dxmt-openxr/winemetal.so ]; then
    mkdir -p "$RESOURCES/dxmt-openxr"
    cp -f dxmt-openxr/d3d11.dll dxmt-openxr/dxgi.dll dxmt-openxr/d3d10core.dll \
          dxmt-openxr/winemetal.dll dxmt-openxr/winemetal.so "$RESOURCES/dxmt-openxr/"
    dxo_sz=$(du -sh "$RESOURCES/dxmt-openxr" 2>/dev/null | cut -f1)
    echo "Bundled prebuilt DXMT OpenXR DLLs (${dxo_sz}) into Resources/dxmt-openxr/"
else
    echo "NOTE: dxmt-openxr/ prebuilt DLLs not found — DXMT+OpenXR will build from source on install."
fi

# Copy image assets
for img in Steam.png Wine.png Setting.png Add.png icon.png; do
    if [ -f "$img" ]; then
        cp "$img" "$RESOURCES/$img"
    fi
done

# Epic Games logo SVG used by EpicLogo.swift (Bundle.main "Epic.svg").
# Optional: EpicLogo falls back to an SF Symbol if it's missing. Look in the
# common spots and copy the first one found so the real logo ships once added.
for svg_src in "Epic.svg" "assets/Epic.svg" "MacNCheese-SwiftUI/Sources/Epic.svg" "MacNCheese-SwiftUI/Epic.svg"; do
    if [ -f "$svg_src" ]; then
        cp "$svg_src" "$RESOURCES/Epic.svg"
        echo "Bundled Epic.svg from $svg_src"
        break
    fi
done


WINE_D3DMETAL_BUNDLE="wine-d3dmetal-bundle.zip"
if [ -f "$WINE_D3DMETAL_BUNDLE" ]; then
    bundle_size=$(stat -f '%z' "$WINE_D3DMETAL_BUNDLE" 2>/dev/null || stat -c '%s' "$WINE_D3DMETAL_BUNDLE")
    bundle_size_mb=$((bundle_size / 1024 / 1024))
    echo "Including Wine D3DMetal bundle (${bundle_size_mb} MiB) in Resources/"
    cp "$WINE_D3DMETAL_BUNDLE" "$RESOURCES/$WINE_D3DMETAL_BUNDLE"
else
    echo "WARNING: $WINE_D3DMETAL_BUNDLE not found in $(pwd) — skipping Wine D3DMetal bundle."
    echo "         To include it, build the zip first (see docs/build-wine-d3dmetal.md)."
fi


# Portable GStreamer runtime — Wine D3DMetal's winegstreamer.so loads GStreamer via
# the /Library/Frameworks/GStreamer.framework rpath. installer.sh's
# ensure_gstreamer_for_d3dmetal() installs this offline (preferred over a network
# pkg download), so ship it in Resources/. Required for RE-Engine video / no black
# screen on D3D12 titles like RE4.
GSTREAMER_BUNDLE="gstreamer-portable.tar.xz"
if [ -f "$GSTREAMER_BUNDLE" ]; then
    gst_size=$(stat -f '%z' "$GSTREAMER_BUNDLE" 2>/dev/null || stat -c '%s' "$GSTREAMER_BUNDLE")
    gst_size_mb=$((gst_size / 1024 / 1024))
    echo "Including portable GStreamer (${gst_size_mb} MiB) in Resources/"
    cp "$GSTREAMER_BUNDLE" "$RESOURCES/$GSTREAMER_BUNDLE"
else
    echo "WARNING: $GSTREAMER_BUNDLE not found in $(pwd) — skipping bundled GStreamer."
    echo "         install_wine_d3dmetal will fall back to downloading the GStreamer pkg."
fi


cat > "$CONTENTS/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>MacNCheese</string>
    <key>CFBundleIdentifier</key>
    <string>com.marcel.macncheese</string>
    <key>CFBundleName</key>
    <string>MacNdCheese Launcher</string>
    <key>CFBundleDisplayName</key>
    <string>MacNdCheese Launcher</string>
    <key>CFBundleVersion</key>
    <string>9.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>9.0.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleIconFile</key>
    <string>MacNCheese</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>14.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSSupportsAutomaticGraphicsSwitching</key>
    <true/>
    <key>LSApplicationCategoryType</key>
    <string>public.app-category.games</string>
    <key>NSMicrophoneUsageDescription</key>
    <string>MacNdCheese runs Windows games that use the microphone for in-game voice chat.</string>
</dict>
</plist>
PLIST

echo "Info.plist written"


echo ""
echo "Signing..."
find "$APP_ROOT" -name "*.cstemp" -delete
xattr -cr "$APP_ROOT"

/usr/bin/codesign --force --deep --sign - --timestamp=none "$APP_ROOT"
/usr/bin/codesign --verify --deep --strict --verbose=2 "$APP_ROOT" 2>&1 || true


echo ""
echo "Creating DMG..."
rm -f MacNCheese.dmg
rm -rf build/dmg_staging
mkdir -p build/dmg_staging
cp -R "$APP_ROOT" build/dmg_staging/
ln -s /Applications build/dmg_staging/Applications

hdiutil create \
    -volname MacNCheese \
    -srcfolder build/dmg_staging \
    -ov \
    -format UDZO \
    MacNCheese.dmg

rm -rf build/dmg_staging


echo ""
echo "=== Done ==="
ls -la "$APP_ROOT/Contents/MacOS/"
ls -la MacNCheese.dmg
echo ""
echo "App:  $APP_ROOT"
echo "DMG:  MacNCheese.dmg"
