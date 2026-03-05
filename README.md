# MacNCheese

MacNCheese manages Wine alongside custom graphics dependencies (ex. MoltenVK, D3D11, Vulkan, etc..) to make installing and running Wine games easier on macOS.

## Requirements

> [!WARNING]  
> This is DESIGNED for Apple Silicon Macs, not Intel. See Intel section.
> This is also designed for Unity and / or DirectX 11 games. Use other apps with caution.

- Something running macOS, preferably Apple Silicon
- [Homebrew](https://brew.sh/)
- Xcode Command Line Tools 
  - If you have homebrew, this is already installed.
 
## So what about Intel?
Theoretically, this works on Intel, but it's designed with Apple Silicon in mind. You can clone the repo and run it locally with UV if you'd like:

```bash
git clone https://github.com/mont127/MacNdCheese/
cd MacNdCheese
uv venv
source .venv/bin/activate
uv pip install -r ./requirements.txt
uv run MacNCheese.py
```

It's just PyQT6 with a GUI layer. If you'd prefer, try the Manual setup below; it's effectively the same thing with less polish and more terminal.


## Setup (manual, only do this if you enjoy suffering)

### Step 1. Install Dependencies
```bash
brew install mingw-w64 meson ninja molten-vk vulkan-sdk glslang wine-stable p7zip
```

### Step 2. Setup DXVK
DXVK is required for Vulkan support. Clone the repo, then build with meson & ninja.
```bash
cd ~
git clone https://github.com/Gcenx/DXVK-macOS.git
cd DXVK-macOS

mkdir -p “$HOME/dxvk-release”

meson setup “$HOME/dxvk-release/build.64” 
–cross-file “$HOME/DXVK-macOS/build-win64.txt” 
–prefix “$HOME/dxvk-release” 
–buildtype release 
-Denable_d3d9=false

ninja -C “$HOME/dxvk-release/build.64”
ninja -C “$HOME/dxvk-release/build.64” install
```
DXVK is now built.

### Step 3. Wine Setup
Setup a prefix and "boot" wine.
```bash
export WINEPREFIX=”$HOME/wined”
wine wineboot
```

### Step 4. Steam Install
Download SteamSetup.exe from the official Steam website and place it in Downloads. Click the windows icon under the download button.

```bash
wine “$HOME/Downloads/SteamSetup.exe”
```

Complete installation inside the Wine window.

When you finish, start Steam with:

```bash
export WINEPREFIX=”$HOME/wined”
cd “$WINEPREFIX/drive_c/Program Files (x86)/Steam”
wine steam.exe -no-cef-sandbox -vgui
```

Login and install your game(s).

### Step 5. DXVK library setup
Before you install your games, you need to setup DXVK inside them.

This example sets up DXVK in People Playground. Adjust the name of the game directory accordingly.
```bash
GAME_DIR=”$WINEPREFIX/drive_c/Program Files (x86)/Steam/steamapps/common/People Playground”

cp “$HOME/dxvk-release/bin/dxgi.dll” “$GAME_DIR/”
cp “$HOME/dxvk-release/bin/d3d11.dll” “$GAME_DIR/”
cp “$HOME/dxvk-release/bin/d3d10core.dll” “$GAME_DIR/”
```

### Step 6. Launching Games
Steam must be running first. Export your environment variables, then launch the game.

```bash
export WINEPREFIX=”$HOME/wined”
export WINEDLLOVERRIDES=“dxgi,d3d11,d3d10core=n,b”
export DXVK_LOG_PATH=”$HOME/dxvk-logs”
export DXVK_LOG_LEVEL=info

cd “$WINEPREFIX/drive_c/Program Files (x86)/Steam/steamapps/common/People Playground” # Replace the name of the game

wine “People Playground.exe” # Replace the name of the game
```

In the future, you can run games simpler with a command similar to this:

```bash
export WINEPREFIX=”$HOME/wined”
cd “$WINEPREFIX/drive_c/Program Files (x86)/Steam”
wine steam.exe -no-cef-sandbox -vgui

# Then start the game
export WINEPREFIX=”$HOME/wined”
export WINEDLLOVERRIDES=“dxgi,d3d11,d3d10core=n,b”
cd “$WINEPREFIX/drive_c/Program Files (x86)/Steam/steamapps/common/People Playground”
wine “People Playground.exe”
```

You now have a working DirectX 11 translation layer running through Metal on macOS. Congrats!

Contact : deepwokenpersona@gmail.com
