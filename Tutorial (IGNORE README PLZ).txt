MacNCheese

Introduction

MacNCheese is a method for running DirectX 11 Windows games on Apple Silicon Macs using Wine together with a custom built DXVK that translates DirectX into Vulkan which is then handled through MoltenVK and Metal.

This setup allows Unity and DirectX 11 based games such as People Playground to run correctly on macOS.

Important

Steam must be running before launching the game.

This guide explains the full installation and launch process.

System requirements

macOS on Apple Silicon
Homebrew installed
Xcode Command Line Tools installed

Step 1 Install dependencies

Run

brew install mingw-w64 meson ninja molten-vk vulkan-sdk glslang wine-stable p7zip

Step 2 Clone DXVK-macOS

Run

git clone https://github.com/Gcenx/DXVK-macOS.git
cd DXVK-macOS

Step 3 Build DXVK

Run

mkdir -p “$HOME/dxvk-release”

meson setup “$HOME/dxvk-release/build.64” 
–cross-file “$HOME/DXVK-macOS/build-win64.txt” 
–prefix “$HOME/dxvk-release” 
–buildtype release 
-Denable_d3d9=false

ninja -C “$HOME/dxvk-release/build.64”
ninja -C “$HOME/dxvk-release/build.64” install

DXVK is now built.

Step 4 Create Wine prefix

Run

export WINEPREFIX=”$HOME/wined”
wine wineboot

Step 5 Install Steam

Download SteamSetup.exe from the official Steam website and place it in Downloads.

Run

export WINEPREFIX=”$HOME/wined”
wine “$HOME/Downloads/SteamSetup.exe”

Complete installation inside the Wine window.

Step 6 Start Steam

Run

export WINEPREFIX=”$HOME/wined”
cd “$WINEPREFIX/drive_c/Program Files (x86)/Steam”
wine steam.exe -no-cef-sandbox -vgui

Login and install your game such as People Playground.

Step 7 Copy DXVK into game

Run

GAME_DIR=”$WINEPREFIX/drive_c/Program Files (x86)/Steam/steamapps/common/People Playground”

cp “$HOME/dxvk-release/bin/dxgi.dll” “$GAME_DIR/”
cp “$HOME/dxvk-release/bin/d3d11.dll” “$GAME_DIR/”
cp “$HOME/dxvk-release/bin/d3d10core.dll” “$GAME_DIR/”

Step 8 Launch game

Steam must be running first.

Then run

export WINEPREFIX=”$HOME/wined”
export WINEDLLOVERRIDES=“dxgi,d3d11,d3d10core=n,b”
export DXVK_LOG_PATH=”$HOME/dxvk-logs”
export DXVK_LOG_LEVEL=info

cd “$WINEPREFIX/drive_c/Program Files (x86)/Steam/steamapps/common/People Playground”

wine “People Playground.exe”

Future launches

Only Steam launch and game launch are required.

Start Steam

export WINEPREFIX=”$HOME/wined”
cd “$WINEPREFIX/drive_c/Program Files (x86)/Steam”
wine steam.exe -no-cef-sandbox -vgui

Then start the game

export WINEPREFIX=”$HOME/wined”
export WINEDLLOVERRIDES=“dxgi,d3d11,d3d10core=n,b”
cd “$WINEPREFIX/drive_c/Program Files (x86)/Steam/steamapps/common/People Playground”
wine “People Playground.exe”

MacNCheese setup is complete.

You now have a working DirectX 11 translation layer running through Metal on macOS.
