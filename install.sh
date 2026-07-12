#!/bin/bash
set -e
APP="/Applications/MacNdCheese Launcher.app"

# Build and emit Swift const-values (Xcode's SWIFT_ENABLE_EMIT_CONST_VALUES).
# appintentsmetadataprocessor reads these to extract App Intents phrase templates;
# without them Siri can't discover the app's shortcuts.
#
# Requires two flags together:
#   -emit-const-values-path   — driver flag: where to write the merged output
#   -const-gather-protocols-file — frontend flag: which protocol conformances to extract
TOOLCHAIN="$(xcode-select -p)/Toolchains/XcodeDefault.xctoolchain"
CONST_FILE="$(pwd)/.build/MacNCheese.swiftconstvalues"

# -const-gather-protocols-file expects a plain JSON array of protocol names.
# (The AppIntents.json in the toolchain uses a different object format that
# the Swift 6 frontend rejects as "malformed".)
PROTOCOLS_FILE="$(mktemp).json"
cat > "$PROTOCOLS_FILE" << 'PROTO_EOF'
["AnyResolverProviding","AppEntity","AppEnum","AppExtension","AppIntent","AppIntentsPackage","AppShortcutProviding","AppShortcutsProvider","AppUnionValue","AppUnionValueCasesProviding","DynamicOptionsProvider","EntityQuery","ExtensionPointDefining","IntentValueQuery","Resolver","TransientEntity","_AssistantIntentsProvider","_GenerativeFunctionExtractable","_IntentValueRepresentable"]
PROTO_EOF

swift build -c release --package-path Sources \
    -Xswiftc -emit-const-values-path -Xswiftc "$CONST_FILE" \
    -Xswiftc -Xfrontend -Xswiftc -const-gather-protocols-file \
    -Xswiftc -Xfrontend -Xswiftc "$PROTOCOLS_FILE"
rm -f "$PROTOCOLS_FILE"
BIN=$(swift build -c release --package-path Sources --show-bin-path 2>/dev/null)

ICONSET=$(mktemp -d)/icon.iconset
mkdir -p "$ICONSET"
sips -z 16 16     icon.png --out "$ICONSET/icon_16x16.png"      2>/dev/null
sips -z 32 32     icon.png --out "$ICONSET/icon_16x16@2x.png"   2>/dev/null
sips -z 32 32     icon.png --out "$ICONSET/icon_32x32.png"      2>/dev/null
sips -z 64 64     icon.png --out "$ICONSET/icon_32x32@2x.png"   2>/dev/null
sips -z 128 128   icon.png --out "$ICONSET/icon_128x128.png"    2>/dev/null
sips -z 256 256   icon.png --out "$ICONSET/icon_128x128@2x.png" 2>/dev/null
sips -z 256 256   icon.png --out "$ICONSET/icon_256x256.png"    2>/dev/null
sips -z 512 512   icon.png --out "$ICONSET/icon_256x256@2x.png" 2>/dev/null
sips -z 512 512   icon.png --out "$ICONSET/icon_512x512.png"    2>/dev/null
sips -z 1024 1024 icon.png --out "$ICONSET/icon_512x512@2x.png" 2>/dev/null
ICNS="/tmp/macncheese_icon_$$.icns"
iconutil -c icns "$ICONSET" -o "$ICNS"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN/MacNCheese" "$APP/Contents/MacOS/MacNCheese"
cp "$ICNS" "$APP/Contents/Resources/MacNCheese.icns"
cp backend_server.py installer.sh Epic.svg "$APP/Contents/Resources/"
chmod +x "$APP/Contents/Resources/installer.sh"
# macndcheese/mnc are plain scripts, dereferenced (not symlinked) into the
# bundle so the codesign step below signs two real files, same as installer.sh.
cp macndcheese "$APP/Contents/Resources/macndcheese"
cp macndcheese "$APP/Contents/Resources/mnc"
chmod +x "$APP/Contents/Resources/macndcheese" "$APP/Contents/Resources/mnc"
# Bundle Apple's gamepolicyctl so the backend can force macOS Game Mode on for
# Wine games without needing Xcode (it keeps its Apple signature; see buildapp.sh).
if [ -f vendor/gamepolicyctl ]; then
    cp vendor/gamepolicyctl "$APP/Contents/Resources/gamepolicyctl"
    chmod +x "$APP/Contents/Resources/gamepolicyctl"
fi
cp Sources/Info.plist "$APP/Contents/Info.plist"

# Extract App Intents metadata so Siri/Apple Intelligence can discover shortcuts.
PROCESSOR=$(xcrun --find appintentsmetadataprocessor 2>/dev/null || echo "")
if [ -n "$PROCESSOR" ]; then
    echo "Extracting App Intents metadata..."
    SDK=$(xcrun --sdk macosx --show-sdk-path 2>/dev/null)
    XCODE_BUILD=$(xcodebuild -version 2>/dev/null | grep "Build version" | awk '{print $3}')
    ARCH=$(uname -m)

    SOURCES_LIST=$(mktemp)
    CONST_VALS_LIST=$(mktemp)

    find Sources -name "*.swift" > "$SOURCES_LIST"

    # Point at the const-values file emitted during swift build above.
    echo "$CONST_FILE" > "$CONST_VALS_LIST"

    "$PROCESSOR" \
        --toolchain-dir "$TOOLCHAIN" \
        --module-name MacNCheese \
        --output "$APP/Contents/Resources" \
        --sdk-root "$SDK" \
        --xcode-version "$XCODE_BUILD" \
        --platform-family macOS \
        --deployment-target 14.0 \
        --target-triple "${ARCH}-apple-macosx14.0" \
        --source-file-list "$SOURCES_LIST" \
        --swift-const-vals-list "$CONST_VALS_LIST" \
        --no-app-shortcuts-localization \
        2>&1 || echo "Warning: App Intents metadata extraction failed — Siri phrases may not work."

    rm -f "$SOURCES_LIST" "$CONST_VALS_LIST"
else
    echo "Warning: appintentsmetadataprocessor not found — install Xcode for Siri support."
fi

xattr -cr "$APP"
codesign --force --deep --sign - "$APP"
# gamepolicyctl needs Apple-private entitlements to reach gamepolicyd; if --deep
# clobbered its signature, restore the pristine copy and reseal without --deep.
GP_RES="$APP/Contents/Resources/gamepolicyctl"
if [ -f "$GP_RES" ] && ! codesign -dvv "$GP_RES" 2>&1 | grep -q "Authority=Apple"; then
    cp vendor/gamepolicyctl "$GP_RES"
    chmod +x "$GP_RES"
    codesign --force --sign - "$APP"
fi
echo "Done: $APP"
