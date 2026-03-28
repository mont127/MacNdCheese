

<p align="center">
  <img src="../icon.png" width="128" alt="MacNCheese icon" />
</p>

# GPTK (D3DMetal) Manual

A quick guide for using the **GPTK (D3DMetal)** backend in **MacNCheese**.

The GPTK backend needs the **Windows GPTK DLL files**. MacNCheese does not only need the `.app` bundle. It needs the actual DLLs so it can place and use them correctly.

---

## Required Files

These files are required:

- `dxgi.dll`
- `d3d11.dll`
- `d3d12.dll`

These are optional, but recommended if they are present:

- `d3d12core.dll`
- `d3d10core.dll`

---

## What MacNCheese Expects

MacNCheese expects the DLLs to end up here:

```text
gptk/lib/wine/x86_64-windows/```

After the DLLs are imported, select this backend in the game launcher:

Backend -> GPTK (D3DMetal)


-----------------------------

###Quick Setup in MacNCheese

##Step 1 — Get the GPTK package

Download the GPTK package you want to use and extract it somewhere on your Mac.

##Step 2 — Find the DLL folder

Inside the extracted files, locate the folder that contains:

dxgi.dll
d3d11.dll
d3d12.dll

In many cases, the folder will look like this:

lib/wine/x86_64-windows/

##Step 3 - Import the DLLs into MacNCheese

Open MacNCheese, then go to:

Settings -> Setup

Click:

Import GPTK DLLs

Then select the extracted folder that contains the DLLs.

MacNCheese will copy them into its configured GPTK directory automatically.

#Step 4 — Launch with GPTK

Open your game in MacNCheese and select:

GPTK (D3DMetal)

Then launch the game.


###Manual Terminal Commands

If you want to prepare or inspect the files manually, these commands help.

##Create the target directory

mkdir -p gptk/lib/wine/x86_64-windows

##Check whether the required DLLs exist in your extracted folder

Replace /path/to/extracted/gptk with your real path:

ls -la /path/to/extracted/gptk
find /path/to/extracted/gptk -iname "dxgi.dll" -o -iname "d3d11.dll" -o -iname "d3d12.dll"

Example: copy the DLLs manually

##[WARNING!]If your extracted DLL folder is already x86_64-windows, run:

cp /path/to/extracted/gptk/x86_64-windows/dxgi.dll gptk/lib/wine/x86_64-windows/
cp /path/to/extracted/gptk/x86_64-windows/d3d11.dll gptk/lib/wine/x86_64-windows/
cp /path/to/extracted/gptk/x86_64-windows/d3d12.dll gptk/lib/wine/x86_64-windows/

##Copy optional DLLs too

cp /path/to/extracted/gptk/x86_64-windows/d3d12core.dll gptk/lib/wine/x86_64-windows/ 2>/dev/null || true
cp /path/to/extracted/gptk/x86_64-windows/d3d10core.dll gptk/lib/wine/x86_64-windows/ 2>/dev/null || true

##Verify the final target folder

ls -la gptk/lib/wine/x86_64-windows/

##Find the DLL folder automatically

If you do not know where the DLLs are after extracting GPTK:

find /path/to/extracted/gptk -type f $begin:math:text$ \-iname \"dxgi\.dll\" \-o \-iname \"d3d11\.dll\" \-o \-iname \"d3d12\.dll\" $end:math:text$

Copy the full DLL directory manually

##If you found the correct x86_64-windows folder:

mkdir -p gptk/lib/wine
cp -R /path/to/extracted/gptk/x86_64-windows gptk/lib/wine/




Example Layout

A correct layout looks like this:

MacNCheese/
├── gptk/
│   └── lib/
│       └── wine/
│           └── x86_64-windows/
│               ├── dxgi.dll
│               ├── d3d11.dll
│               ├── d3d12.dll
│               ├── d3d12core.dll
│               └── d3d10core.dll




##Notes
>GPTK backend needs the Windows DLLs, not only the .app bundle.
>If a game still fails, remove any locally patched DXVK DLLs first.
>MacNCheese uses WINEDLLOVERRIDES for:
>dxgi
>d3d11
>d3d12

A typical override setup looks like this:

export WINEDLLOVERRIDES="dxgi,d3d11,d3d12=n,b"

MacNCheese normally handles that for you automatically.



###Troubleshooting

GPTK backend says DLLs are missing

Check the target folder:

ls -la gptk/lib/wine/x86_64-windows/

Make sure these files are there:

dxgi.dll
d3d11.dll
d3d12.dll

Game still launches with the wrong renderer

Remove old local patches from the game directory first. For example:

find "/path/to/game" -iname "dxgi.dll" -o -iname "d3d11.dll" -o -iname "d3d10core.dll"

Then remove them if needed:

rm -f "/path/to/game/dxgi.dll"
rm -f "/path/to/game/d3d11.dll"
rm -f "/path/to/game/d3d10core.dll"

You want to inspect what MacNCheese is using

Check the configured GPTK folder in the app and confirm the DLLs exist there. The expected target remains:

gptk/lib/wine/x86_64-windows/


