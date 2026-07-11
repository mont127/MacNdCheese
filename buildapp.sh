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
# Built fresh from icon.png every run — do NOT fall back to any committed .icns
# file. A previous version of this script shipped the stale root-level
# icon.icns (an old logo) instead of the one it had just built here.

echo ""
echo "Building Swift executable (release) for $TARGET_ARCH..."

# Build and emit Swift const-values (Xcode's SWIFT_ENABLE_EMIT_CONST_VALUES) so the
# appintentsmetadataprocessor step below can extract Siri/Shortcuts phrase templates.
# See install.sh, which this was ported from — that script only ever builds native,
# so it can get away with using `uname -m` for the target triple. Here we must use
# TARGET_ARCH instead, since this script cross-builds x86_64/universal on arm64 CI
# runners; `uname -m` would silently produce the wrong triple in that case.
CONST_FILE="$(pwd)/.build/MacNCheese.swiftconstvalues"
mkdir -p "$(dirname "$CONST_FILE")"

PROTOCOLS_FILE="$(mktemp).json"
cat > "$PROTOCOLS_FILE" << 'PROTO_EOF'
["AnyResolverProviding","AppEntity","AppEnum","AppExtension","AppIntent","AppIntentsPackage","AppShortcutProviding","AppShortcutsProvider","AppUnionValue","AppUnionValueCasesProviding","DynamicOptionsProvider","EntityQuery","ExtensionPointDefining","IntentValueQuery","Resolver","TransientEntity","_AssistantIntentsProvider","_GenerativeFunctionExtractable","_IntentValueRepresentable"]
PROTO_EOF

pushd Sources >/dev/null

swift build -c release $SWIFT_ARCH_ARGS \
    -Xswiftc -emit-const-values-path -Xswiftc "$CONST_FILE" \
    -Xswiftc -Xfrontend -Xswiftc -const-gather-protocols-file \
    -Xswiftc -Xfrontend -Xswiftc "$PROTOCOLS_FILE" 2>&1
SWIFT_BIN="$(swift build -c release $SWIFT_ARCH_ARGS --show-bin-path)/MacNCheese"

echo "Binary: $SWIFT_BIN"

popd >/dev/null
rm -f "$PROTOCOLS_FILE"

echo ""
echo "Creating .app bundle..."

APP_ROOT="build/MacNdCheese Launcher.app"
CONTENTS="$APP_ROOT/Contents"
MACOS="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"

mkdir -p "$MACOS" "$RESOURCES"

cp "$SWIFT_BIN" "$MACOS/MacNCheese"

if [ ! -f assets/MacNCheese.icns ]; then
  echo "ERROR: assets/MacNCheese.icns not found (icon build step above should have created it)"
  exit 1
fi

cp assets/MacNCheese.icns "$RESOURCES/MacNCheese.icns"
cp backend_server.py "$RESOURCES/backend_server.py"
cp installer.sh "$RESOURCES/installer.sh"
chmod +x "$RESOURCES/installer.sh"
cp Epic.svg "$RESOURCES/Epic.svg"
# macndcheese/mnc are plain scripts, dereferenced (not symlinked) into the
# bundle so the codesign step below signs two real files, same as installer.sh.
cp macndcheese "$RESOURCES/macndcheese"
cp macndcheese "$RESOURCES/mnc"
chmod +x "$RESOURCES/macndcheese" "$RESOURCES/mnc"

# Bundle Apple's gamepolicyctl so the backend can force macOS Game Mode on for
# launched Wine games without requiring Xcode. It only links OS frameworks, so
# the ad-hoc re-sign below (codesign --deep) is sufficient.
if [ -f vendor/gamepolicyctl ]; then
    cp vendor/gamepolicyctl "$RESOURCES/gamepolicyctl"
    chmod +x "$RESOURCES/gamepolicyctl"
else
    echo "WARNING: vendor/gamepolicyctl missing — Game Mode forcing will be disabled in this build" >&2
fi

for img in Steam.png Wine.png Setting.png Add.png icon.png; do
    if [ -f "$img" ]; then
        cp "$img" "$RESOURCES/$img"
    fi
done

# Extract App Intents metadata so Siri/Apple Intelligence can discover shortcuts.
# App Intents definitions don't vary by CPU arch, so for a universal build one
# representative triple (arm64) is enough — this only affects Siri phrase
# discovery, not the binary itself.
case "$TARGET_ARCH" in
  universal) INTENTS_ARCH="arm64" ;;
  *) INTENTS_ARCH="$TARGET_ARCH" ;;
esac

PROCESSOR=$(xcrun --find appintentsmetadataprocessor 2>/dev/null || echo "")
if [ -n "$PROCESSOR" ]; then
    echo "Extracting App Intents metadata..."
    TOOLCHAIN="$(xcode-select -p)/Toolchains/XcodeDefault.xctoolchain"
    SDK=$(xcrun --sdk macosx --show-sdk-path 2>/dev/null)
    XCODE_BUILD=$(xcodebuild -version 2>/dev/null | grep "Build version" | awk '{print $3}')

    SOURCES_LIST=$(mktemp)
    CONST_VALS_LIST=$(mktemp)

    find Sources -name "*.swift" > "$SOURCES_LIST"
    echo "$CONST_FILE" > "$CONST_VALS_LIST"

    "$PROCESSOR" \
        --toolchain-dir "$TOOLCHAIN" \
        --module-name MacNCheese \
        --output "$RESOURCES" \
        --sdk-root "$SDK" \
        --xcode-version "$XCODE_BUILD" \
        --platform-family macOS \
        --deployment-target 14.0 \
        --target-triple "${INTENTS_ARCH}-apple-macosx14.0" \
        --source-file-list "$SOURCES_LIST" \
        --swift-const-vals-list "$CONST_VALS_LIST" \
        --no-app-shortcuts-localization \
        2>&1 || echo "Warning: App Intents metadata extraction failed — Siri phrases may not work."

    rm -f "$SOURCES_LIST" "$CONST_VALS_LIST"
else
    echo "Warning: appintentsmetadataprocessor not found — install Xcode for Siri support."
fi

# Use the real Info.plist (same one install.sh uses) instead of a separately
# hand-maintained copy — this script used to hardcode its own, which drifted
# out of sync with the actual app name/category/URL scheme/Spotlight config in
# Sources/Info.plist (this is what was shipping the stale "MacNCheese" name
# instead of the current "MacNdCheese Launcher").
cp Sources/Info.plist "$CONTENTS/Info.plist"

echo "Info.plist copied from Sources/Info.plist"

echo ""
echo "Signing..."
find "$APP_ROOT" -name "*.cstemp" -delete
xattr -cr "$APP_ROOT"

/usr/bin/codesign --force --deep --sign - --timestamp=none "$APP_ROOT"

# gamepolicyctl must keep Apple's ORIGINAL signature: it carries Apple-private
# entitlements (com.apple.gamepolicyd.tool.* + mach-lookup) needed to reach the
# gamepolicyd daemon, which an ad-hoc re-sign would strip (breaking Game Mode
# control). --deep leaves a Mach-O in Resources/ untouched, but guard anyway:
# if its signature was clobbered, restore the pristine copy and reseal the
# bundle WITHOUT --deep.
GP_RES="$RESOURCES/gamepolicyctl"
if [ -f "$GP_RES" ] && ! /usr/bin/codesign -dvv "$GP_RES" 2>&1 | grep -q "Authority=Apple"; then
    echo "Restoring Apple-signed gamepolicyctl after deep sign"
    cp vendor/gamepolicyctl "$GP_RES"
    chmod +x "$GP_RES"
    /usr/bin/codesign --force --sign - --timestamp=none "$APP_ROOT"
fi

/usr/bin/codesign --verify --deep --strict --verbose=2 "$APP_ROOT" 2>&1 || true

echo ""
echo "Creating DMG..."
rm -f "${APP_NAME}.dmg"
rm -rf build/dmg_staging
mkdir -p build/dmg_staging
cp -R "$APP_ROOT" build/dmg_staging/
ln -s /Applications build/dmg_staging/Applications

hdiutil create \
    -volname "MacNdCheese Launcher" \
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
