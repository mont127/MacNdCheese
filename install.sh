#!/bin/zsh
set -e
APP="/Applications/MacNdCheese Launcher.app"
swift build -c release --package-path Sources
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
cp "$ICNS" "$APP/Contents/Resources/icon.icns"
cp backend_server.py installer.sh "$APP/Contents/Resources/"
chmod +x "$APP/Contents/Resources/installer.sh"
cp Sources/Info.plist "$APP/Contents/Info.plist"
xattr -cr "$APP"
codesign --force --deep --sign - "$APP"
echo "Done: $APP"
