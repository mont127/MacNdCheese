#!/bin/bash
set -eu

TARGET_ARCH="${1:-arm64}"

case "$TARGET_ARCH" in
  arm64)
    APP_NAME="MacNCheese-arm64"
    SWIFT_ARCH_ARGS=""
    ;;
  x86_64|intel)
    TARGET_ARCH="x86_64"
    APP_NAME="MacNCheese-intel"
    SWIFT_ARCH_ARGS="--arch x86_64"
    ;;
  universal)
    APP_NAME="MacNCheese-universal"
    SWIFT_ARCH_ARGS="--arch arm64 --arch x86_64"
    ;;
  *)
    echo "Unsupported arch: $TARGET_ARCH"
    exit 1
    ;;
esac

echo "=== MacNCheese SwiftUI App Builder ($TARGET_ARCH) ==="

rm -rf build dist "${APP_NAME}.dmg"
mkdir -p build assets/icon.iconset

echo "Creating app icon..."
file icon.png
sips -g format icon.png

sips -z 16 16     icon.png --out assets/icon.iconset/icon_16x16.png      2>/dev/null
sips -z 32 32     icon.png --out assets/icon.iconset/icon_16x16@2x.png   2>/dev/null
sips -z 32 32     icon.png --out assets/icon.iconset/icon_32x32.png      2>/dev/null
sips -z 64 64     icon.png --out assets/icon.iconset/icon_32x32@2x.png   2>/dev/null
sips -z 128 128   icon.png --out assets/icon.iconset/icon_128x128.png    2>/dev/null
sips -z 256 256   icon.png --out assets/icon.iconset/icon_128x128@2x.png 2>/dev/null
sips -z 256 256   icon.png --out assets/icon.iconset/icon_256x256.png    2>/dev/null
sips -z 512 512   icon.png --out assets/icon.iconset/icon_256x256@2x.png 2>/dev/null
sips -z 512 512   icon.png --out assets/icon.iconset/icon_512x512.png    2>/dev/null
sips -z 1024 1024 icon.png --out assets/icon.iconset/icon_512x512@2x.png 2>/dev/null

iconutil -c icns assets/icon.iconset -o assets/MacNCheese.icns
ls -la assets/MacNCheese.icns

echo ""
echo "Building Swift executable (release) for $TARGET_ARCH..."

pushd Sources >/dev/null

swift build -c release $SWIFT_ARCH_ARGS 2>&1
SWIFT_BIN="$(swift build -c release $SWIFT_ARCH_ARGS --show-bin-path)/MacNCheese"

echo "Binary: $SWIFT_BIN"

popd >/dev/null


echo ""
echo "Creating .app bundle..."

APP_ROOT="build/MacNCheese.app"
CONTENTS="$APP_ROOT/Contents"
MACOS="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"

mkdir -p "$MACOS" "$RESOURCES"

cp "$SWIFT_BIN" "$MACOS/MacNCheese"

if [ ! -f icon.icns ]; then
  echo "ERROR: icon.icns not found"
  exit 1
fi

cp icon.icns "$RESOURCES/MacNCheese.icns"
cp backend_server.py "$RESOURCES/backend_server.py"
cp installer.sh "$RESOURCES/installer.sh"
chmod +x "$RESOURCES/installer.sh"

for img in Steam.png Wine.png Setting.png Add.png icon.png; do
    if [ -f "$img" ]; then
        cp "$img" "$RESOURCES/$img"
    fi
done

cat > "$CONTENTS/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>MacNCheese</string>
    <key>CFBundleIdentifier</key>
    <string>com.marcel.macncheese</string>
    <key>CFBundleName</key>
    <string>MacNCheese</string>
    <key>CFBundleDisplayName</key>
    <string>MacNCheese</string>
    <key>CFBundleVersion</key>
    <string>7.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>7.0.0</string>
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
rm -f "${APP_NAME}.dmg"
rm -rf build/dmg_staging
mkdir -p build/dmg_staging
cp -R "$APP_ROOT" build/dmg_staging/
ln -s /Applications build/dmg_staging/Applications

hdiutil create \
    -volname MacNCheese \
    -srcfolder build/dmg_staging \
    -ov \
    -format UDZO \
    "${APP_NAME}.dmg"

rm -rf build/dmg_staging

echo ""
echo "=== Done ==="
ls -la "$APP_ROOT/Contents/MacOS/"
ls -la "${APP_NAME}.dmg"
echo ""
echo "App:  $APP_ROOT"
echo "DMG:  ${APP_NAME}.dmg"
