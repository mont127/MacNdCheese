# MacNCheese.spec

import os
from PyInstaller.utils.hooks import collect_submodules

APP_NAME = "MacNCheese"
MAIN_SCRIPT = "MacNdCheeseARM.py"
ICON_PATH = "icon.icns"
BUNDLE_ID = "com.marcel.macncheese"
VERSION = os.environ.get("MACNCHEESE_VERSION", "0.1.0")

block_cipher = None

datas = [
    ("installer.sh", "."),
]

optional_files = [
    "Add.png",
    "Wine.png",
    "Steam.png",
    "Setting.png",
]

for f in optional_files:
    if os.path.exists(f):
        datas.append((f, "."))

if os.path.exists("gptk"):
    datas.append(("gptk", "gptk"))

a = Analysis(
    [MAIN_SCRIPT],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=[
        *collect_submodules("PyQt6"),
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon=ICON_PATH if os.path.exists(ICON_PATH) else None,
    bundle_identifier=BUNDLE_ID,
    info_plist={
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleVersion": VERSION,
        "CFBundleShortVersionString": VERSION,
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": "MacNCheese requests microphone access for audio-related compatibility features.",
        "NSRequiresAquaSystemAppearance": False,
        "LSMinimumSystemVersion": "11.0",
        "LSEnvironment": {
            "OBJC_DISABLE_INITIALIZE_FORK_SAFETY": "YES",
        },
    },
)
