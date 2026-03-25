from __future__ import annotations

from pathlib import Path

APP_NAME = "MacNCheese"
MNC_DIR = Path.home() / ".macncheese"
CONFIG_PATH = MNC_DIR / "config" / "config.json"
LOGS_DIR = MNC_DIR / "logs"

DEFAULT_PREFIX = str(MNC_DIR / "wine")
DEFAULT_DXVK_INSTALL = str(MNC_DIR / "libs" / "dxvk64")
DEFAULT_DXVK_INSTALL32 = str(MNC_DIR / "libs" / "dxvk32")
DEFAULT_STEAM_SETUP = str(Path.home() / "Downloads" / "SteamSetup.exe")
DEFAULT_MESA_DIR = str(MNC_DIR / "deps" / "mesa" / "x64")
DXVK_DLLS = ("d3d11.dll", "d3d10core.dll")

DEFAULT_MESA_URL = "https://github.com/pal1000/mesa-dist-win/releases/download/23.1.9/mesa3d-23.1.9-release-msvc.7z"
DXVK_PREBUILT_URL = "https://github.com/Gcenx/DXVK-macOS/releases/download/v1.10.3-20230507-repack/dxvk-macOS-async-v1.10.3-20230507-repack.tar.gz"


LAUNCH_BACKEND_AUTO = "auto"
LAUNCH_BACKEND_WINE = "wine"
LAUNCH_BACKEND_DXVK = "dxvk"
LAUNCH_BACKEND_MESA_LLVMPIPE = "mesa:llvmpipe"
LAUNCH_BACKEND_MESA_ZINK = "mesa:zink"
LAUNCH_BACKEND_MESA_SWR = "mesa:swr"

MESA_DRIVER_LLVMPIPE = "llvmpipe"
MESA_DRIVER_ZINK = "zink"
MESA_DRIVER_SWR = "swr"

LAUNCH_BACKENDS = (
    ("Auto (recommended)", LAUNCH_BACKEND_AUTO),
    ("Wine builtin (no DXVK/Mesa)", LAUNCH_BACKEND_WINE),
    ("DXVK (D3D11->Vulkan)", LAUNCH_BACKEND_DXVK),
    ("Mesa llvmpipe (CPU, safe)", LAUNCH_BACKEND_MESA_LLVMPIPE),
    ("Mesa zink (GPU, Vulkan)", LAUNCH_BACKEND_MESA_ZINK),
    ("Mesa swr (CPU rasterizer)", LAUNCH_BACKEND_MESA_SWR),
)
