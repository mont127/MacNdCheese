# MacNCheese.spec
# PyInstaller spec file for MacNCheese
#
# If you have an icon, drop MacNCheese.icns in the project root and
# uncomment the icon= line in the BUNDLE section below.
#
# Build with:
#   pyinstaller MacNCheese.spec
# Or for a specific arch:
#   arch -x86_64 python3.12 -m PyInstaller MacNCheese.spec

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

a = Analysis(
    ['MacNdCheeseARM.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        # if we ever ship binaries manually add them here
    ],
    hiddenimports=[
        # PyQt6 sometimes needs these nudged explicitly
        *collect_submodules('PyQt6'),
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # runner doesn't have these
        'tkinter',
        'unittest',
        'email',
        'html',
        'http',
        'xml',
        'pydoc',
        'doctest',
        'difflib',
        'ftplib',
        'imaplib',
        'smtplib',
        'telnetlib',
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
    name='MacNCheese',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,       
    console=False,   # no terminal window
    disable_windowed_traceback=False,
    target_arch=None,  # controlled at CLI via --target-arch
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
    name='MacNCheese',
)

app = BUNDLE(
    coll,
    name='MacNCheese.app',
    icon='icon.icns',
    bundle_identifier='com.macncheese.app',
    info_plist={
        'CFBundleName': 'MacNCheese',
        'CFBundleDisplayName': 'MacNCheese',
        'CFBundleVersion': '0.1.0',
        'CFBundleShortVersionString': '0.1.0',
        'NSHighResolutionCapable': True,
        'NSRequiresAquaSystemAppearance': False,  # allows dark mode
        'LSMinimumSystemVersion': '11.0',
        # Suppress the "damaged app" prompt on first launch for
        # unsigned builds distributed outside the App Store
        'LSEnvironment': {
            'OBJC_DISABLE_INITIALIZE_FORK_SAFETY': 'YES',
        },
    },
)
