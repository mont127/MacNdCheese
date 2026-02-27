# MacNdCheese
Macndcheese is a manual that runs any steam app.
⸻

REPO on macOS with Wine 11 + DXVK (working method)

Goal

Run:
	•	Steam normally inside Wine, without getting stuck in a steamwebhelper.exe loop
	•	REPO with working DirectX 11 via DXVK

What caused the problems

Two separate issues happened:

1) Steam loop

Steam kept respawning steamwebhelper.exe because DXVK was forced globally in the prefix.

That happened because:
	•	dxgi, d3d11, d3d10core were overridden globally in Wine registry
	•	DXVK DLLs were copied into system32

That made Steam’s Chromium UI load DXVK too, which caused the loop.

2) REPO graphics failure

REPO originally said:
	•	Failed to initialize graphics
	•	Make sure you have DirectX 11 installed

That happened because it was using WineD3D/OpenGL instead of DXVK.

Once DXVK was properly built and loaded, REPO created:
	•	a D3D11 device
	•	feature level 11_0
	•	a swapchain

So DX11 started working.

3) REPO still quit after graphics worked

After DXVK worked, REPO still exited because:
	•	Steamworks failed to initialize
	•	SteamApi_Init failed with NoSteamClient
	•	Cannot create IPC pipe to Steam client process

That means REPO needs Steam running properly in the same prefix.

⸻

Final working setup

Core rule

Do not apply DXVK globally.

Instead:
	•	run Steam without DXVK
	•	run REPO with DXVK only for that game

That is the fix.

⸻

Requirements

Wine

Use Wine 11+ (not old GPTK Wine 7.7).

You should check:

which wine
wine --version

You want Wine 11.x.

⸻

Prefix

Use a dedicated Wine prefix:

export WINEPREFIX="$HOME/wined"

This guide assumes your prefix is exactly:

$HOME/wined


⸻

Build DXVK-macOS (D3D11 only)

Install required build tools

brew install meson ninja pkg-config python mingw-w64

Build DXVK

In your ~/DXVK-macOS source folder:

cd ~/DXVK-macOS
rm -rf "$HOME/dxvk-release"

meson setup "$HOME/dxvk-release/build.64" \
  --cross-file "$HOME/DXVK-macOS/build-win64.txt" \
  --prefix "$HOME/dxvk-release" \
  --buildtype release \
  -Denable_d3d9=false

ninja -C "$HOME/dxvk-release/build.64"
ninja -C "$HOME/dxvk-release/build.64" install

Result

The built DLLs end up here:

$HOME/dxvk-release/bin

The important files are:
	•	dxgi.dll
	•	d3d11.dll
	•	d3d10core.dll

⸻

Important: do NOT install DXVK globally into Wine

This is what broke Steam.

Remove global DXVK registry overrides

If you added them before, remove them:

export WINEPREFIX="$HOME/wined"

wine reg delete "HKCU\\Software\\Wine\\DllOverrides" /v dxgi /f
wine reg delete "HKCU\\Software\\Wine\\DllOverrides" /v d3d11 /f
wine reg delete "HKCU\\Software\\Wine\\DllOverrides" /v d3d10core /f

Restore original Wine DLLs in system32

If you previously replaced system32 DLLs, restore the backups:

export WINEPREFIX="$HOME/wined"

cp -v "$WINEPREFIX/dxvk-backup/system32/"{dxgi.dll,d3d11.dll,d3d10core.dll} \
      "$WINEPREFIX/drive_c/windows/system32/" 2>/dev/null || true

After this, Steam should stop loading DXVK globally.

⸻

Start Steam correctly

Run Steam without DXVK variables

Before starting Steam:

export WINEPREFIX="$HOME/wined"
unset WINEDLLOVERRIDES
unset DXVK_LOG_PATH
unset DXVK_LOG_LEVEL

Start Steam

cd "$WINEPREFIX/drive_c/Program Files (x86)/Steam"
wine steam.exe -no-cef-sandbox -vgui

This is the stable Steam launch method.

Why this works

This keeps Steam’s steamwebhelper.exe on normal Wine DLLs instead of DXVK, preventing the endless loop.

⸻

Apply DXVK only to REPO

Define the REPO folder

export WINEPREFIX="$HOME/wined"
REPO_DIR="$WINEPREFIX/drive_c/Program Files (x86)/Steam/steamapps/common/REPO"

Copy DXVK DLLs into the REPO folder only

cp -v "$HOME/dxvk-release/bin/"{dxgi.dll,d3d11.dll,d3d10core.dll} "$REPO_DIR/"

This is the key step.

By placing the DXVK DLLs next to REPO.exe, only REPO uses them.

⸻

Launch REPO with DXVK

Set per-game DXVK environment

export WINEPREFIX="$HOME/wined"
export WINEDLLOVERRIDES="dxgi,d3d11,d3d10core=n,b"
export DXVK_LOG_PATH="$HOME/dxvk-logs"
export DXVK_LOG_LEVEL=info

Run REPO from its game folder

cd "$REPO_DIR"
wine REPO.exe


⸻

How to verify DXVK is working

Check DXVK logs

ls -la "$HOME/dxvk-logs"

You should see:
	•	REPO_d3d11.log
	•	possibly REPO_dxgi.log

Confirm the important lines

Inside REPO_d3d11.log, the important successful lines are:
	•	DXVK: v1.10.3...
	•	D3D11CoreCreateDevice: Using feature level D3D_FEATURE_LEVEL_11_0

That confirms REPO is using DXVK and DirectX 11 is working.

⸻

Steamworks requirement

Even after DXVK works, REPO still needs Steam to be running properly.

Your log showed:
	•	Steamworks failed to initialize
	•	SteamApi_Init failed with NoSteamClient
	•	Cannot create IPC pipe to Steam client process

That means:
	•	Steam must already be running
	•	it must be running in the same prefix
	•	REPO must be launched while Steam is active

Best practice
	1.	Start Steam first:

export WINEPREFIX="$HOME/wined"
unset WINEDLLOVERRIDES DXVK_LOG_PATH DXVK_LOG_LEVEL
cd "$WINEPREFIX/drive_c/Program Files (x86)/Steam"
wine steam.exe -no-cef-sandbox -vgui

	2.	Once Steam is open and logged in, start REPO:

export WINEPREFIX="$HOME/wined"
export WINEDLLOVERRIDES="dxgi,d3d11,d3d10core=n,b"
export DXVK_LOG_PATH="$HOME/dxvk-logs"
export DXVK_LOG_LEVEL=info
cd "$WINEPREFIX/drive_c/Program Files (x86)/Steam/steamapps/common/REPO"
wine REPO.exe


⸻

Daily usage

Start Steam

export WINEPREFIX="$HOME/wined"
unset WINEDLLOVERRIDES
unset DXVK_LOG_PATH
unset DXVK_LOG_LEVEL

cd "$WINEPREFIX/drive_c/Program Files (x86)/Steam"
wine steam.exe -no-cef-sandbox -vgui

Start REPO

export WINEPREFIX="$HOME/wined"
export WINEDLLOVERRIDES="dxgi,d3d11,d3d10core=n,b"
export DXVK_LOG_PATH="$HOME/dxvk-logs"
export DXVK_LOG_LEVEL=info

cd "$WINEPREFIX/drive_c/Program Files (x86)/Steam/steamapps/common/REPO"
wine REPO.exe


⸻

Recovery guide

If Steam starts looping again

That means DXVK was forced globally again.

Fix it

export WINEPREFIX="$HOME/wined"

pkill -f "steamwebhelper.exe|steam.exe|SteamService.exe" 2>/dev/null || true
wineserver -k 2>/dev/null || true

wine reg delete "HKCU\\Software\\Wine\\DllOverrides" /v dxgi /f
wine reg delete "HKCU\\Software\\Wine\\DllOverrides" /v d3d11 /f
wine reg delete "HKCU\\Software\\Wine\\DllOverrides" /v d3d10core /f

cp -v "$WINEPREFIX/dxvk-backup/system32/"{dxgi.dll,d3d11.dll,d3d10core.dll} \
      "$WINEPREFIX/drive_c/windows/system32/" 2>/dev/null || true

Then launch Steam again normally.

⸻

If REPO says DirectX 11 failed again

That means REPO is no longer seeing the local DXVK DLLs.

Fix it

Re-copy the DLLs into the game folder:

export WINEPREFIX="$HOME/wined"
REPO_DIR="$WINEPREFIX/drive_c/Program Files (x86)/Steam/steamapps/common/REPO"

cp -v "$HOME/dxvk-release/bin/"{dxgi.dll,d3d11.dll,d3d10core.dll} "$REPO_DIR/"

Then launch REPO again with the DXVK environment.

⸻

If REPO opens then quits immediately

Check:
	1.	Steam is running in the same prefix
	2.	you launched Steam first
	3.	Player.log in:

$WINEPREFIX/drive_c/users/$USER/AppData/LocalLow/semiwork/Repo/Player.log

If you see:
	•	SteamApi_Init failed with NoSteamClient

then Steam is not being seen by the game.

⸻

What definitely worked in your setup

These facts were confirmed from the logs:
	•	DXVK was successfully built
	•	REPO loaded DXVK
	•	REPO created a D3D11 device
	•	REPO used feature level 11_0
	•	swapchain creation worked
	•	the Steam loop only happened when DXVK was applied globally
	•	Steam became the problem only because steamwebhelper.exe was loading DXVK
	•	local DXVK per game is the correct setup

⸻

Final rule to remember

Never do this for the whole prefix
	•	global DllOverrides for dxgi, d3d11, d3d10core
	•	replacing system32 DXVK DLLs and leaving them there for everything

Always do this instead
	•	Steam on stock Wine DLLs
	•	copy DXVK DLLs into the specific game folder
	•	use WINEDLLOVERRIDES only for that game launch

⸻
