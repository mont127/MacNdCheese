#!/usr/bin/env python3
"""
MacNCheese backend server -- JSON-RPC over stdin/stdout.

Protocol
--------
Read one JSON object per line from stdin.
Write one JSON object per line to stdout.
Stderr is reserved for debug logging.

Request:  {"id": 1, "cmd": "command_name", ...params}
Response: {"id": 1, "ok": true, "data": ...}
    or    {"id": 1, "ok": false, "error": "message"}
"""

from __future__ import annotations

import sys as _sys
import os as _os
# Vendored packages bundled inside MacNCheese.app/Contents/Resources/
_resources_dir = _os.path.dirname(_os.path.abspath(__file__))
if _resources_dir not in _sys.path:
    _sys.path.insert(0, _resources_dir)

import base64
import io
import json
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PORTABLE_DIR = Path.home() / "Library" / "Application Support" / "MacNCheese" / "deps"
VERSION_MARKER = PORTABLE_DIR / ".mnc_versions"
BOTTLES_BASE = Path.home() / "Games" / "MacNCheese"
DEFAULT_PREFIX = str(Path.home() / "wined")

PREFIXES_JSON = Path.home() / ".macncheese_prefixes.json"
BOTTLES_JSON = Path.home() / ".macncheese_bottles.json"

STEAM_SETUP_URL = "https://cdn.fastly.steamstatic.com/client/installer/SteamSetup.exe"

APPMANIFEST_RE = re.compile(r'"(\w+)"\s+"([^"]*)"')

# Graphics backend IDs (must match MacNCheese.py)
BACKEND_AUTO = "auto"
BACKEND_WINE = "wine"
BACKEND_DXVK = "dxvk"
BACKEND_DXMT = "dxmt"
BACKEND_MESA_LLVMPIPE = "mesa:llvmpipe"
BACKEND_MESA_ZINK = "mesa:zink"
BACKEND_MESA_SWR = "mesa:swr"
BACKEND_VKD3D = "vkd3d-proton"
BACKEND_GPTK = "gptk"
BACKEND_GPTK_FULL = "gptk_full"
BACKEND_D3DMETAL3 = "d3dmetal3"

# Default paths for graphics components
DEFAULT_DXVK_INSTALL = Path.home() / "dxvk-release"
DEFAULT_MESA_DIR = Path.home() / "mesa" / "x64"
DEFAULT_DXMT_DIR = Path.home() / "dxmt"
DEFAULT_VKD3D_DIR = Path.home() / "vkd3d-proton"
DEFAULT_GPTK_DIR = Path.home() / "gptk"
GPTK3_ROOT = Path.home() / "gptk3" / "Game Porting Toolkit.app"
D3DMETAL_NATIVE_DIR = Path.home() / "D3DMetalTesting" / "lib" / "external"

DXVK_DLLS = ("d3d11.dll", "d3d10core.dll")
GPTK_REQUIRED_DLLS = ("atidxx64.dll", "d3d10.dll", "d3d11.dll", "d3d12.dll", "dxgi.dll", "nvapi64.dll", "nvngx.dll")

SKIP_EXE_TOKENS = (
    "crash", "reporter", "setup", "install", "unins",
    "helper", "bootstrap", "diagnostics", "dxwebsetup",
)

# ---------------------------------------------------------------------------
# Discord Rich Presence — rpc-bridge (https://github.com/EnderIce2/rpc-bridge)
# ---------------------------------------------------------------------------
# rpc-bridge runs bridge.exe inside Wine as a Windows service.
# It intercepts the game's own Discord RPC calls and forwards them to the
# native Discord client via the macOS LaunchAgent installed by launchd.sh.

RPC_BRIDGE_DIR = PORTABLE_DIR / "rpc-bridge"
RPC_BRIDGE_EXE = RPC_BRIDGE_DIR / "bridge.exe"
RPC_BRIDGE_LAUNCHD = RPC_BRIDGE_DIR / "launchd.sh"


def _rpc_bridge_available() -> bool:
    return RPC_BRIDGE_EXE.exists()


def _rpc_bridge_start(wine: str, env: dict) -> None:
    """Install (or re-register) and start rpc-bridge using the exact same Wine/env as the game."""
    if not _rpc_bridge_available():
        return
    try:
        result = subprocess.run(
            [wine, "sc", "start", "rpc-bridge"],
            env=env, timeout=15,
            capture_output=True, text=True,
        )
        log(f"rpc-bridge: sc start rc={result.returncode} stdout={result.stdout.strip()!r}")
        time.sleep(2)
    except Exception as exc:
        log(f"rpc-bridge: start failed: {exc}")


def _rpc_bridge_install_prefix(prefix: str) -> None:
    """Install bridge.exe as a Windows service inside the given Wine prefix."""
    if not _rpc_bridge_available():
        log("rpc-bridge: bridge.exe not found, skipping install")
        return
    wine = _find_wine_for_bottle("auto")
    env = _wine_env(prefix)
    try:
        result = subprocess.run(
            [wine, str(RPC_BRIDGE_EXE), "--install"],
            env=env, timeout=30,
            capture_output=True, text=True,
        )
        log(f"rpc-bridge: install stdout={result.stdout.strip()!r} stderr={result.stderr.strip()!r} rc={result.returncode}")
        log(f"rpc-bridge: installed service in prefix {prefix}")
    except Exception as exc:
        log(f"rpc-bridge: install failed: {exc}")


def _rpc_bridge_uninstall_prefix(prefix: str) -> None:
    """Remove bridge.exe Windows service from the given Wine prefix."""
    if not _rpc_bridge_available():
        return
    wine = _find_wine()
    env = _wine_env(prefix)
    try:
        subprocess.run(
            [wine, str(RPC_BRIDGE_EXE), "--uninstall"],
            env=env, timeout=30,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        log(f"rpc-bridge: uninstalled service from prefix {prefix}")
    except Exception as exc:
        log(f"rpc-bridge: uninstall failed: {exc}")


# Centralised log directory (wine logs, dxvk logs, app log)
LOG_DIR = Path.home() / "Library" / "Logs" / "MacNCheese"
LOG_DIR.mkdir(parents=True, exist_ok=True)
(LOG_DIR / "dxvk").mkdir(exist_ok=True)
APP_LOG_PATH = LOG_DIR / "macncheese.log"

# ---------------------------------------------------------------------------
# Logging helper (stderr + persistent app log file)
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(f"[backend] {msg}", file=sys.stderr, flush=True)
    try:
        with APP_LOG_PATH.open("a") as _f:
            import datetime
            _f.write(f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# JSON helpers for config files
# ---------------------------------------------------------------------------

def _read_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"Failed to read {path}: {exc}")
    return default

def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

def _load_prefixes() -> List[str]:
    data = _read_json(PREFIXES_JSON, [])
    if isinstance(data, list):
        return data
    return []

def _save_prefixes(prefixes: List[str]) -> None:
    _write_json(PREFIXES_JSON, prefixes)

def _load_bottles() -> Dict[str, Any]:
    data = _read_json(BOTTLES_JSON, {})
    if isinstance(data, dict):
        return data
    return {}

def _save_bottles(bottles: Dict[str, Any]) -> None:
    _write_json(BOTTLES_JSON, bottles)

def _resolve_key(path: str) -> str:
    try:
        return str(Path(path).expanduser().resolve())
    except Exception:
        return path

# ---------------------------------------------------------------------------
# Wine / wineserver discovery
# ---------------------------------------------------------------------------

def _find_wine_stable() -> Optional[str]:
    for name in ("wine64", "wine"):
        p = PORTABLE_DIR / "Wine Stable.app" / "Contents" / "Resources" / "wine" / "bin" / name
        if p.exists():
            return str(p)
    return None

def _find_wine_staging() -> Optional[str]:
    for name in ("wine64", "wine"):
        p = PORTABLE_DIR / "Wine Staging.app" / "Contents" / "Resources" / "wine" / "bin" / name
        if p.exists():
            return str(p)
    return None

def _find_wine_for_bottle(wine_binary_pref: str = "auto") -> Optional[str]:
    """Find wine respecting a per-bottle preference ('stable', 'staging', 'auto')."""
    if wine_binary_pref == "stable":
        return _find_wine_stable() or _find_wine()
    if wine_binary_pref == "staging":
        return _find_wine_staging() or _find_wine()
    # auto: prefer stable, fall back to staging, then system
    return _find_wine()

def _find_wine() -> Optional[str]:
    candidates = [
        _find_wine_stable(),
        _find_wine_staging(),
        str(PORTABLE_DIR / "bin" / "wine64"),
        str(PORTABLE_DIR / "bin" / "wine"),
        shutil.which("wine64"),
        shutil.which("wine"),
        "/usr/local/bin/wine64",
        "/opt/homebrew/bin/wine64",
        "/usr/local/bin/wine",
        "/opt/homebrew/bin/wine",
    ]
    for c in candidates:
        if c and Path(c).exists():
            version = _get_wine_version(c)
            log(f"Found Wine: {c} ({version})")
            return c
    return None

def _find_wineserver() -> Optional[str]:
    candidates = [
        str(PORTABLE_DIR / "Wine Stable.app" / "Contents" / "Resources" / "wine" / "bin" / "wineserver"),
        str(PORTABLE_DIR / "Wine Staging.app" / "Contents" / "Resources" / "wine" / "bin" / "wineserver"),
        str(PORTABLE_DIR / "bin" / "wineserver"),
        shutil.which("wineserver"),
        "/usr/local/bin/wineserver",
        "/opt/homebrew/bin/wineserver",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None

def _find_moltenvk_icd() -> str:
    json_candidates = [
        Path("/usr/local/share/vulkan/icd.d/MoltenVK_icd.json"),
        Path("/opt/homebrew/share/vulkan/icd.d/MoltenVK_icd.json"),
        Path.home() / ".local" / "share" / "vulkan" / "icd.d" / "MoltenVK_icd.json",
        Path("/Applications/Wine Stable.app/Contents/Resources/vulkan/icd.d/MoltenVK_icd.json"),
        Path("/Applications/Wine Staging.app/Contents/Resources/vulkan/icd.d/MoltenVK_icd.json"),
    ]
    for p in json_candidates:
        if p.exists():
            return str(p)

    lib_candidates = [
        Path("/Applications/Wine Stable.app/Contents/Resources/wine/lib/libMoltenVK.dylib"),
        Path("/Applications/Wine Staging.app/Contents/Resources/wine/lib/libMoltenVK.dylib"),
        Path("/usr/local/lib/libMoltenVK.dylib"),
        Path("/opt/homebrew/lib/libMoltenVK.dylib"),
    ]
    for lib in lib_candidates:
        if lib.exists():
            manifest_dir = Path.home() / ".config" / "macncheese" / "vulkan" / "icd.d"
            manifest_dir.mkdir(parents=True, exist_ok=True)
            manifest = manifest_dir / "MoltenVK_icd.json"
            manifest.write_text(json.dumps({
                "file_format_version": "1.0.0",
                "ICD": {
                    "library_path": str(lib),
                    "api_version": "1.2.0",
                },
            }, indent=2))
            return str(manifest)
    return ""

# ---------------------------------------------------------------------------
# Wine environment builder
# ---------------------------------------------------------------------------

def _wine_env(prefix: str) -> Dict[str, str]:
    """Base Wine environment — matches original MainWindow.wine_env().
    Does NOT set WINEDLLOVERRIDES; that is handled by _apply_backend_env()."""
    env = dict(os.environ)
    env["WINEPREFIX"] = prefix
    env["WINEDEBUG"] = "-all"

    portable_bin = str(PORTABLE_DIR / "bin")
    path = env.get("PATH", "")
    if portable_bin not in path:
        env["PATH"] = f"{portable_bin}:{path}"

    vk_icd = _find_moltenvk_icd()
    if vk_icd:
        env["VK_ICD_FILENAMES"] = vk_icd

    return env


def _apply_retina_regedit(wine: str, env: dict, retina_mode: bool) -> None:
    """Apply RetinaMode, Resolution and LogPixels via `wine regedit file.reg`."""
    retina_val = "y" if retina_mode else "n"
    dpi_hex = "dc" if retina_mode else "60"  # 220=0xdc, 96=0x60
    # "Resolution"="auto" forces Wine to recalculate screen size on next launch,
    # preventing the top-left-corner artifact when switching retina mode.
    reg_content = (
        "REGEDIT4\n\n"
        "[HKEY_CURRENT_USER\\Software\\Wine\\Mac Driver]\n"
        f'"RetinaMode"="{retina_val}"\n'
        '"Resolution"="auto"\n\n'
        "[HKEY_CURRENT_USER\\Control Panel\\Desktop]\n"
        f'"LogPixels"=dword:000000{dpi_hex}\n'
    )
    try:
        reg_file = Path(tempfile.gettempdir()) / "wine_retina.reg"
        reg_file.write_text(reg_content, encoding="utf-8")
        subprocess.run(
            [wine, "regedit", str(reg_file)],
            env=env, timeout=15,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        log(f"Applied regedit: RetinaMode={retina_val}, Resolution=auto, LogPixels=000000{dpi_hex}")
    except Exception as exc:
        log(f"Warning: regedit failed: {exc}")


# ---------------------------------------------------------------------------
# Optional esync/msync env helper
# ---------------------------------------------------------------------------
def _apply_sync_env(env: Dict[str, str], esync: Optional[bool], msync: Optional[bool]) -> Dict[str, str]:
    """Apply optional per-launch esync/msync flags.

    If a value is None, leave the current environment setting unchanged.
    If a value is True/False, force the corresponding env var to 1/0.
    """
    env = dict(env)
    if esync is not None:
        env["WINEESYNC"] = "1" if esync else "0"
    if msync is not None:
        env["WINEMSYNC"] = "1" if msync else "0"
    return env


# ---------------------------------------------------------------------------
# Graphics backend detection & env setup
# ---------------------------------------------------------------------------

def _dxvk_available() -> bool:
    return all((DEFAULT_DXVK_INSTALL / "bin" / dll).exists() for dll in DXVK_DLLS)

def _mesa_available() -> bool:
    return (DEFAULT_MESA_DIR / "opengl32.dll").exists()

def _vkd3d_available() -> bool:
    # DLLs live in x86/ subfolder (same layout as DXVK)
    vkd3d_bin = DEFAULT_VKD3D_DIR / "x86"
    return vkd3d_bin.exists() and (vkd3d_bin / "d3d12.dll").exists()

def _dxmt_available() -> bool:
    return DEFAULT_DXMT_DIR.exists() and (DEFAULT_DXMT_DIR / "d3d11.dll").exists()

def _find_wine_win64_lib() -> Optional[Path]:
    """Find the portable Wine's x86_64-windows PE DLL directory."""
    for wine_app in ["Wine Staging.app", "Wine Stable.app"]:
        candidate = PORTABLE_DIR / wine_app / "Contents" / "Resources" / "wine" / "lib" / "wine" / "x86_64-windows"
        if candidate.is_dir():
            return candidate
    return None

def _find_gptk_wine_root() -> Optional[Path]:
    """Find the GPTK toolkit wine root (contains bin/wine64, lib/, etc.)."""
    candidates = [
        GPTK3_ROOT / "Contents" / "Resources" / "wine",
        DEFAULT_GPTK_DIR / "lib" / "wine" / "Game Porting Toolkit.app" / "Contents" / "Resources" / "wine",
    ]
    for c in candidates:
        if (c / "bin" / "wine64").exists():
            return c
    return None

def _gptk_available() -> bool:
    dll_dir = DEFAULT_GPTK_DIR / "lib" / "wine" / "x86_64-windows"
    has_dlls = dll_dir.exists() and all((dll_dir / name).exists() for name in GPTK_REQUIRED_DLLS)
    has_wine = _find_gptk_wine_root() is not None
    return has_dlls and has_wine

def _d3dmetal3_available() -> bool:
    """Check if D3DMetal is available.
    Requires: GPTK DLLs in x86_64-windows/, and D3DMetal native runtime
    (D3DMetal.framework + libd3dshared.dylib) in the native dir.
    """
    dll_dir = DEFAULT_GPTK_DIR / "lib" / "wine" / "x86_64-windows"
    has_dlls = (
        dll_dir.exists()
        and (dll_dir / "d3d11.dll").exists()
        and (dll_dir / "dxgi.dll").exists()
        and (dll_dir / "d3d12.dll").exists()
    )
    has_native = (
        D3DMETAL_NATIVE_DIR.exists()
        and (D3DMETAL_NATIVE_DIR / "D3DMetal.framework").exists()
        and (D3DMETAL_NATIVE_DIR / "libd3dshared.dylib").exists()
    )
    return has_dlls and has_native

def _gptk_full_available() -> bool:
    return Path("/usr/local/bin/gameportingtoolkit").exists() or shutil.which("gameportingtoolkit") is not None


def _resolve_auto_backend() -> str:
    """Pick the best available backend: DXMT > D3DMetal > DXVK > Wine builtin."""
    if _dxmt_available():
        return BACKEND_DXMT
    if _d3dmetal3_available():
        return BACKEND_D3DMETAL3
    if _dxvk_available():
        return BACKEND_DXVK
    return BACKEND_WINE


def _apply_backend_env(env: Dict[str, str], backend: str) -> Dict[str, str]:
    """Apply backend-specific environment variables matching MacNCheese.py Backend classes.

    Flow matches original: backend sets its overrides from clean slate,
    then mandatory overrides are prepended (line 5798 in MacNCheese.py).
    """
    env = dict(env)
    env["WINE_MF_MFT_SKIP_VERIFY"] = "1"

    # Each backend sets WINEDLLOVERRIDES from scratch (no leftover base overrides)
    backend_ovr = ""

    if backend == BACKEND_WINE:
        backend_ovr = "dxgi,d3d11,d3d10core=b"
        env.pop("DXVK_LOG_PATH", None)
        env.pop("DXVK_LOG_LEVEL", None)
        env.pop("GALLIUM_DRIVER", None)
        env.pop("MESA_GLTHREAD", None)

    elif backend == BACKEND_DXVK:
        backend_ovr = "dxgi,d3d11,d3d10core=n,b"
        dxvk_log_dir = str(LOG_DIR / "dxvk")
        
        env["DXVK_LOG_PATH"] = dxvk_log_dir
        env["DXVK_LOG_LEVEL"] = "info"
        env["DXVK_HDR"] = "0"
        env["DXVK_STATE_CACHE"] = "0"
        env["DXVK_ASYNC"] = "1"
        env["DXVK_ENABLE_NVAPI"] = "0"
        env.pop("GALLIUM_DRIVER", None)
        env.pop("MESA_GLTHREAD", None)

    elif backend.startswith("mesa:"):
        driver = backend.split(":", 1)[1]
        env["GALLIUM_DRIVER"] = driver
        backend_ovr = "opengl32=n,b"
        env["MESA_GLTHREAD"] = "true"
        env.pop("DXVK_LOG_PATH", None)
        env.pop("DXVK_LOG_LEVEL", None)

    elif backend == BACKEND_VKD3D:
        vkd3d_bin = str(DEFAULT_VKD3D_DIR / "x86")
        env["VKD3D_PROTON_PATH"] = vkd3d_bin
        backend_ovr = "d3d12,d3d12core,dxgi=n,b"
        existing_winepath = env.get("WINEPATH", "")
        env["WINEPATH"] = vkd3d_bin if not existing_winepath else f"{vkd3d_bin};{existing_winepath}"
        env.pop("DXVK_LOG_PATH", None)
        env.pop("DXVK_LOG_LEVEL", None)
        env.pop("GALLIUM_DRIVER", None)
        env.pop("MESA_GLTHREAD", None)
        env.setdefault("VKD3D_CONFIG", "")

    elif backend == BACKEND_DXMT:
        # builtin-dll=true build: DLLs are in Wine's own lib dir.
        # Explicitly force builtin so stale native DLLs in the game dir don't win.
        backend_ovr = "d3d11,d3d10core,dxgi=b"
        env.pop("DXVK_LOG_PATH", None)
        env.pop("DXVK_LOG_LEVEL", None)
        env.pop("GALLIUM_DRIVER", None)
        env.pop("MESA_GLTHREAD", None)

    elif backend == BACKEND_D3DMETAL3:
        # D3DMetal3: uses MacNCheese Wine Stable + GPTK DLLs.
        # DYLD_FALLBACK_LIBRARY_PATH must point to the native macOS D3DMetal
        # runtime (.dylib / .framework), NOT the Windows .dll dir.
        mnc_root = PORTABLE_DIR / "Wine Stable.app" / "Contents" / "Resources" / "wine"
        mnc_bin = mnc_root / "bin"

        env["PATH"] = f"{mnc_bin}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        env["ROSETTA_ADVERTISE_AVX"] = "1"
        env["SteamAppId"] = "730"
        env["SteamGameId"] = "730"

        for var in (
            "DYLD_LIBRARY_PATH",
            "DYLD_SHARED_REGION",
            "WINEDLLPATH",
            "WINEPATH",
            "WINESERVER",
            "DXVK_LOG_PATH",
            "DXVK_LOG_LEVEL",
            "VKD3D_PROTON_PATH",
            "DXMT_PATH",
            "GALLIUM_DRIVER",
            "MESA_GLTHREAD",
        ):
            env.pop(var, None)

        backend_ovr = "winemenubuilder.exe=d;mscoree=;mshtml=;mf,mfplat,mfreadwrite,mfplay=b;atidxx64,d3d10,d3d11,d3d12,dxgi,nvapi64,nvngx-on-metalfx=n"

    elif backend == BACKEND_GPTK:
        mnc_root = PORTABLE_DIR / "Wine Stable.app" / "Contents" / "Resources" / "wine"
        mnc_bin = mnc_root / "bin"

        env["PATH"] = f"{mnc_bin}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        env["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join([
            str(D3DMETAL_NATIVE_DIR),
            "/usr/local/lib",
            "/usr/local/opt/freetype/lib",
            "/usr/local/opt/gnutls/lib",
            "/usr/lib",
        ])
        env["ROSETTA_ADVERTISE_AVX"] = "1"
        env["SteamAppId"] = "730"
        env["SteamGameId"] = "730"

        for var in (
            "DYLD_LIBRARY_PATH",
            "DYLD_SHARED_REGION",
            "WINEDLLPATH",
            "WINEPATH",
            "WINESERVER",
            "DXVK_LOG_PATH",
            "DXVK_LOG_LEVEL",
            "VKD3D_PROTON_PATH",
            "DXMT_PATH",
            "GALLIUM_DRIVER",
            "MESA_GLTHREAD",
        ):
            env.pop(var, None)

        backend_ovr = "winemenubuilder.exe=d;mscoree=;mshtml=;mf,mfplat,mfreadwrite,mfplay=b;atidxx64,d3d10,d3d11,d3d12,dxgi,nvapi64,nvngx-on-metalfx=n"

    elif backend == BACKEND_GPTK_FULL:
        wineserver = _find_wineserver()
        if wineserver:
            env["WINESERVER"] = wineserver

    # Mandatory overrides prepended (matching MacNCheese.py line 5798).
    # In Wine, first match for a DLL wins, so mandatory comes first.
    # Note: nvapi/nvapi64 disabled unless GPTK backend needs them.
    if backend in (BACKEND_GPTK, BACKEND_D3DMETAL3):
        # These backends provide a complete override string — no prepending.
        env["WINEDLLOVERRIDES"] = backend_ovr
    else:
        mandatory_ovr = "nvapi,nvapi64=;mf,mfplat,mfreadwrite,mfplay=b"
        if backend_ovr:
            env["WINEDLLOVERRIDES"] = f"{mandatory_ovr};{backend_ovr}"
        else:
            env["WINEDLLOVERRIDES"] = mandatory_ovr

    # DXVK log dir always created (for Steam launch etc.)
    dxvk_log_dir = str(LOG_DIR / "dxvk")
    
    env.setdefault("DXVK_LOG_PATH", dxvk_log_dir)
    env.setdefault("DXVK_LOG_LEVEL", "info")
    env["WINEDEBUG"] = "-all"

    return env


def _backend_wine_binary(backend: str, exe: str) -> Optional[str]:
    """Return the wine binary for backends that need a special one, else None."""
    if backend == BACKEND_D3DMETAL3:
        mnc_wine = PORTABLE_DIR / "Wine Stable.app" / "Contents" / "Resources" / "wine" / "bin" / "wine"
        if mnc_wine.exists():
            wine_bin = str(mnc_wine)
            version = _get_wine_version(wine_bin)
            log(f"Backend d3dmetal3 using MacNCheese Wine Stable: {wine_bin} ({version})")
            return wine_bin
        wine_root = _find_gptk_wine_root()
        if wine_root:
            wine_bin = str(wine_root / "bin" / "wine64")
            log(f"Backend d3dmetal3 fallback using GPTK wine64: {wine_bin} ({_get_wine_version(wine_bin)})")
            return wine_bin
        return None
    if backend == BACKEND_GPTK:
        mnc_wine = PORTABLE_DIR / "Wine Stable.app" / "Contents" / "Resources" / "wine" / "bin" / "wine"
        if mnc_wine.exists():
            wine_bin = str(mnc_wine)
            version = _get_wine_version(wine_bin)
            log(f"Backend gptk using MacNCheese Wine Stable: {wine_bin} ({version})")
            return wine_bin
        wine_root = _find_gptk_wine_root()
        if wine_root:
            wine_bin = str(wine_root / "bin" / "wine64")
            version = _get_wine_version(wine_bin)
            log(f"Backend gptk fallback using GPTK wine: {wine_bin} ({version})")
            return wine_bin
    if backend == BACKEND_GPTK_FULL:
        gptk_bin = "/usr/local/bin/gameportingtoolkit"
        if Path(gptk_bin).exists():
            version = _get_wine_version(gptk_bin)
            log(f"Backend gptk_full using GPTK Full: {gptk_bin} ({version})")
            return gptk_bin
    return None


def _backend_launch_cmd(backend: str, wine: str, exe_dir: str, exe_name: str,
                        prefix: str, exe_full: str, quoted_args: str, log_path: str) -> str:
    """Build the full bash launch command for a given backend."""
    if backend == BACKEND_GPTK_FULL:
        gptk_bin = "/usr/local/bin/gameportingtoolkit"
        if not Path(gptk_bin).exists():
            raise FileNotFoundError("gameportingtoolkit not found in /usr/local/bin")
        return (
            f"arch -x86_64 {shlex.quote(gptk_bin)} {shlex.quote(prefix)} "
            f"{shlex.quote(exe_full)} {quoted_args} "
            f"> {shlex.quote(log_path)} 2>&1"
        )

    if backend in (BACKEND_GPTK, BACKEND_D3DMETAL3):
        # Both GPTK and D3DMetal3 use the heredoc-to-zsh pattern so that
        # DYLD_FALLBACK_LIBRARY_PATH survives macOS SIP stripping.
        mnc_root = PORTABLE_DIR / "Wine Stable.app" / "Contents" / "Resources" / "wine"
        # D3DMetal native runtime: .dylib and .framework files, not Windows .dlls
        dyld_fallback = ":".join([
            str(D3DMETAL_NATIVE_DIR),
            "/usr/local/lib",
            "/usr/local/opt/freetype/lib",
            "/usr/local/opt/gnutls/lib",
            "/usr/lib",
        ])
        dll_ovr = "winemenubuilder.exe=d;mscoree=;mshtml=;mf,mfplat,mfreadwrite,mfplay=b;atidxx64,d3d10,d3d11,d3d12,dxgi,nvapi64,nvngx-on-metalfx=n"
        heredoc = f"""\
MNC_WINE={shlex.quote(wine)}
export WINEPREFIX={shlex.quote(prefix)}
export DYLD_FALLBACK_LIBRARY_PATH={shlex.quote(dyld_fallback)}
export ROSETTA_ADVERTISE_AVX=1
export WINEDLLOVERRIDES="{dll_ovr}"
export WINEDEBUG=-all
export SteamAppId=730
export SteamGameId=730
cd {shlex.quote(exe_dir)} || exit 1
"$MNC_WINE" {shlex.quote('./' + exe_name)} {quoted_args} 2>&1 | tee {shlex.quote(log_path)}
"""
        return f"cd ~ && /usr/bin/arch -x86_64 /bin/zsh <<'MNCEOF'\n{heredoc}MNCEOF"

    debug_prefix = "WINEDEBUG=+loaddll"
    if backend.startswith("mesa:"):
        debug_prefix = "WINEDEBUG=+loaddll,+wgl,+opengl"

    return (
        f"cd {shlex.quote(exe_dir)} && "
        f"{debug_prefix} arch -x86_64 {shlex.quote(wine)} "
        f"{shlex.quote(exe_name)} {quoted_args} "
        f"> {shlex.quote(log_path)} 2>&1"
    )


def _collect_target_dirs(game_dir: Path, exe_path: Path) -> List[Path]:
    """Collect all directories that need DLL patching (matches original logic)."""
    target_dirs: set = set()
    target_dirs.add(game_dir)
    target_dirs.add(exe_path.parent)

    windows_no_editor = game_dir / "WindowsNoEditor"
    if windows_no_editor.is_dir():
        target_dirs.add(windows_no_editor)

    try:
        for ship in game_dir.glob("**/*-Shipping.exe"):
            if ship.is_file():
                target_dirs.add(ship.parent)
    except Exception:
        pass

    try:
        for p in game_dir.glob("**/Binaries/Win64"):
            if p.is_dir():
                target_dirs.add(p)
    except Exception:
        pass

    return sorted(target_dirs)


DXVK_OPTIONAL_DLLS = ("dxgi.dll",)

MESA_RUNTIME_DLLS_BASE = ("opengl32.dll", "libgallium_wgl.dll", "libglapi.dll")
MESA_RUNTIME_DLLS_EXTRA = ("libEGL.dll", "libGLESv2.dll")


def _prepare_game_for_backend(backend: str, exe_path: Path, install_dir: str) -> None:
    """
    Copy required DLLs into the game directory before launch.
    This is the critical step the original app does in prepare_game()/patch_selected_game().
    Without it, Wine can't find the native DLLs even with WINEDLLOVERRIDES set.
    """
    game_dir = Path(install_dir) if install_dir else exe_path.parent
    target_dirs = _collect_target_dirs(game_dir, exe_path)

    if backend == BACKEND_DXVK:
        dxvk_bin = DEFAULT_DXVK_INSTALL / "bin"
        if not all((dxvk_bin / dll).exists() for dll in DXVK_DLLS):
            log(f"DXVK DLLs not found at {dxvk_bin}, skipping patch")
            return
        for tdir in target_dirs:
            tdir.mkdir(parents=True, exist_ok=True)
            for dll in DXVK_DLLS:
                shutil.copy2(str(dxvk_bin / dll), str(tdir / dll))
            for dll in DXVK_OPTIONAL_DLLS:
                if (dxvk_bin / dll).exists():
                    shutil.copy2(str(dxvk_bin / dll), str(tdir / dll))
            log(f"Copied DXVK DLLs -> {tdir}")

    elif backend.startswith("mesa:"):
        driver = backend.split(":", 1)[1]
        # Determine which DLLs are needed for this driver
        dlls = list(MESA_RUNTIME_DLLS_BASE)
        if driver in ("zink", "swr"):
            dlls.extend(MESA_RUNTIME_DLLS_EXTRA)

        # Check if DLLs exist, fall back to llvmpipe if needed
        missing = [dll for dll in dlls if not (DEFAULT_MESA_DIR / dll).exists()]
        if missing and driver in ("zink", "swr"):
            log(f"Mesa: missing {', '.join(missing)} for '{driver}', falling back to llvmpipe")
            dlls = list(MESA_RUNTIME_DLLS_BASE)
            missing = [dll for dll in dlls if not (DEFAULT_MESA_DIR / dll).exists()]

        if missing:
            log(f"Mesa DLLs not found at {DEFAULT_MESA_DIR}: {', '.join(missing)}, skipping patch")
            return

        optional = []
        if driver == "zink" and (DEFAULT_MESA_DIR / "zink_dri.dll").exists():
            optional.append("zink_dri.dll")

        for tdir in target_dirs:
            tdir.mkdir(parents=True, exist_ok=True)
            # Clean stale Mesa DLLs first
            for stale in ("opengl32.dll", "libgallium_wgl.dll", "libglapi.dll",
                          "libEGL.dll", "libGLESv2.dll", "zink_dri.dll"):
                stale_path = tdir / stale
                if stale_path.exists():
                    try:
                        stale_path.unlink()
                    except Exception:
                        pass
            for dll in dlls:
                shutil.copy2(str(DEFAULT_MESA_DIR / dll), str(tdir / dll))
            for dll in optional:
                shutil.copy2(str(DEFAULT_MESA_DIR / dll), str(tdir / dll))
            log(f"Copied Mesa ({driver}) DLLs -> {tdir}")

    elif backend == BACKEND_VKD3D:
        vkd3d_bin = DEFAULT_VKD3D_DIR / "x86"
        vkd3d_dlls = ("d3d12.dll", "d3d12core.dll")
        vkd3d_optional = ("dxgi.dll",)
        if not all((vkd3d_bin / dll).exists() for dll in vkd3d_dlls):
            log(f"VKD3D DLLs not found at {vkd3d_bin}, skipping patch")
        else:
            for tdir in target_dirs:
                tdir.mkdir(parents=True, exist_ok=True)
                for dll in vkd3d_dlls:
                    shutil.copy2(str(vkd3d_bin / dll), str(tdir / dll))
                for dll in vkd3d_optional:
                    if (vkd3d_bin / dll).exists():
                        shutil.copy2(str(vkd3d_bin / dll), str(tdir / dll))
                log(f"Copied VKD3D-Proton DLLs -> {tdir}")

    elif backend == BACKEND_DXMT:
        _unpatch_dxvk(game_dir)
        # Verify DXMT DLLs are current in Wine's lib — copy from ~/dxmt/ if needed.
        wine_lib = _find_wine_win64_lib()
        if wine_lib:
            for dll in ("d3d11.dll", "dxgi.dll", "d3d10core.dll", "winemetal.dll"):
                src = DEFAULT_DXMT_DIR / dll
                if src.exists():
                    shutil.copy2(str(src), str(wine_lib / dll))
            log(f"DXMT: verified/copied DLLs into {wine_lib}")
        else:
            log("DXMT: could not find Wine lib dir — DLLs may be stale")

    elif backend == BACKEND_WINE:
        _unpatch_dxvk(game_dir)
        # Restore original Wine PE DLLs if DXMT had replaced them.
        wine_lib = _find_wine_win64_lib()
        backup_dir = PORTABLE_DIR / ".dxmt-wine-backups"
        if wine_lib and backup_dir.is_dir():
            restored = []
            for dll in ("d3d11.dll", "dxgi.dll", "d3d10core.dll"):
                backup = backup_dir / dll
                if backup.exists():
                    shutil.copy2(str(backup), str(wine_lib / dll))
                    restored.append(dll)
            if restored:
                log(f"Wine builtin: restored original DLLs: {', '.join(restored)}")

    elif backend == BACKEND_GPTK:
        gptk_dll_dir = DEFAULT_GPTK_DIR / "lib" / "wine" / "x86_64-windows"
        gptk_launch_dlls = (
            "atidxx64.dll",
            "d3d10.dll",
            "d3d11.dll",
            "d3d12.dll",
            "dxgi.dll",
            "nvapi64.dll",
            "nvngx-on-metalfx.dll",
        )
        if not gptk_dll_dir.exists():
            log(f"GPTK DLL dir not found at {gptk_dll_dir}, skipping patch")
        else:
            _unpatch_dxvk(game_dir)
            for tdir in target_dirs:
                tdir.mkdir(parents=True, exist_ok=True)
                for dll in gptk_launch_dlls:
                    src = gptk_dll_dir / dll
                    if src.exists():
                        shutil.copy2(str(src), str(tdir / dll))
                log(f"Copied GPTK launch DLLs -> {tdir}")

    elif backend == BACKEND_D3DMETAL3:
        gptk_dll_dir = DEFAULT_GPTK_DIR / "lib" / "wine" / "x86_64-windows"
        d3dmetal_dlls = (
            "atidxx64.dll",
            "d3d10.dll",
            "d3d11.dll",
            "d3d12.dll",
            "dxgi.dll",
            "nvapi64.dll",
            "nvngx-on-metalfx.dll",
        )
        if not gptk_dll_dir.exists():
            log(f"D3DMetal3: GPTK DLL dir not found at {gptk_dll_dir}, skipping patch")
        else:
            _unpatch_dxvk(game_dir)
            for tdir in target_dirs:
                tdir.mkdir(parents=True, exist_ok=True)
                for dll in d3dmetal_dlls:
                    src = gptk_dll_dir / dll
                    if src.exists():
                        shutil.copy2(str(src), str(tdir / dll))
                log(f"Copied D3DMetal3 DLLs -> {tdir}")

    elif backend == BACKEND_GPTK_FULL:
        # This backend needs DXVK/VKD3D DLLs removed (unpatch)
        _unpatch_dxvk(game_dir)


VKD3D_DLLS = ("d3d12.dll", "d3d12core.dll")

def _unpatch_dxvk(game_dir: Path) -> None:
    """Remove DXVK/VKD3D/Mesa DLLs from game directory (matches unpatch_selected_game)."""
    removed = 0
    all_dlls = set(d.lower() for d in DXVK_DLLS + DXVK_OPTIONAL_DLLS + VKD3D_DLLS)
    try:
        for p in game_dir.glob("**/*.dll"):
            if p.name.lower() in all_dlls:
                p.unlink()
                removed += 1
        if removed:
            log(f"Removed {removed} DXVK DLLs from {game_dir}")
    except Exception as e:
        log(f"Failed to unpatch game: {e}")


# ---------------------------------------------------------------------------
# Steam library / game scanning helpers
# ---------------------------------------------------------------------------

def _windows_path_to_unix(prefix: Path, value: str) -> Path:
    normalized = value.replace("\\\\", "\\")
    if re.match(r"^[A-Za-z]:\\", normalized):
        drive = normalized[0].lower()
        remainder = normalized[3:].replace("\\", "/")
        base = prefix / f"drive_{drive}"
        if drive == "c":
            base = prefix / "drive_c"
        return base / remainder
    return Path(normalized.replace("\\", "/"))

def _library_roots(prefix: Path, steam_dir: Path) -> List[Path]:
    roots: List[Path] = []
    if steam_dir.exists():
        roots.append(steam_dir)

    library_vdf = steam_dir / "steamapps" / "libraryfolders.vdf"
    if not library_vdf.exists():
        return roots

    try:
        content = library_vdf.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return roots

    for match in APPMANIFEST_RE.finditer(content):
        key, value = match.group(1), match.group(2)
        if key == "path":
            converted = _windows_path_to_unix(prefix, value)
            if converted.exists() and converted not in roots:
                roots.append(converted)
    return roots

def _parse_appmanifest(path: Path) -> Optional[Dict[str, str]]:
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None

    data: Dict[str, str] = {}
    for match in APPMANIFEST_RE.finditer(content):
        key, value = match.group(1), match.group(2)
        if key in ("appid", "name", "installdir"):
            data[key] = value

    if not all(k in data for k in ("appid", "name", "installdir")):
        return None
    return data

def _is_probably_not_game(exe: Path) -> bool:
    lowered = exe.name.lower()
    return any(t in lowered for t in SKIP_EXE_TOKENS)

def _detect_exe(game_dir: Path, install_dir_name: str, game_name: str) -> Optional[str]:
    if not game_dir.exists():
        return None

    # 1. *-Shipping.exe (largest first)
    try:
        shipping = sorted(
            game_dir.glob("**/*-Shipping.exe"),
            key=lambda p: p.stat().st_size if p.exists() else 0,
            reverse=True,
        )
        if shipping:
            return str(shipping[0])
    except Exception:
        pass

    # 2. Named candidates
    named_candidates: List[Path] = []
    for name in (
        f"{install_dir_name}.exe",
        f"{game_name}.exe",
        f"{game_name.replace(' ', '')}.exe",
        f"{install_dir_name.replace(' ', '')}.exe",
    ):
        p = game_dir / name
        if p.exists():
            named_candidates.append(p)
    if named_candidates:
        return str(named_candidates[0])

    # 3. Root *.exe sorted by size descending, skipping bad names
    try:
        root_exes = sorted(
            (p for p in game_dir.glob("*.exe") if p.is_file() and not _is_probably_not_game(p)),
            key=lambda p: p.stat().st_size,
            reverse=True,
        )
        if root_exes:
            return str(root_exes[0])
    except Exception:
        pass

    # 4. Recursive fallback
    try:
        sub_exes = sorted(
            (p for p in game_dir.glob("**/*.exe") if p.is_file() and not _is_probably_not_game(p)),
            key=lambda p: p.stat().st_size,
            reverse=True,
        )
        if sub_exes:
            return str(sub_exes[0])
    except Exception:
        pass

    return None


def _detect_all_exes(game_dir: Path) -> List[str]:
    """Return all plausible game executables in a game directory."""
    if not game_dir.exists():
        return []
    results: List[Path] = []
    try:
        for exe in game_dir.glob("**/*.exe"):
            if exe.is_file() and not _is_probably_not_game(exe):
                results.append(exe)
    except Exception:
        pass
    # Sort by size descending (largest = most likely the real game)
    results.sort(key=lambda p: p.stat().st_size if p.exists() else 0, reverse=True)
    return [str(p) for p in results]


# ---------------------------------------------------------------------------
# Launched-game process tracker
# ---------------------------------------------------------------------------

_running_games: Dict[int, subprocess.Popen] = {}

# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def cmd_list_bottles(params: Dict[str, Any]) -> Any:
    prefixes = _load_prefixes()
    bottles = _load_bottles()
    result: List[Dict[str, Any]] = []
    seen: set[str] = set()
    bottles_base_str = str(BOTTLES_BASE.resolve())

    for raw_path in prefixes:
        if not raw_path or not raw_path.strip():
            continue  # skip empty paths (ghost bottles)
        key = _resolve_key(raw_path)
        # Skip the bottles base directory itself – it's the container, not a bottle
        if key == bottles_base_str:
            continue
        if key in seen:
            continue
        seen.add(key)
        bottle = bottles.get(key, {})
        name = bottle.get("name", Path(raw_path).name)
        if not name:
            name = Path(raw_path).name or raw_path
        result.append({
            "path": raw_path,
            "name": name,
            "icon_path": bottle.get("icon_path", ""),
            "launcher_exe": bottle.get("launcher_exe", ""),
            "launcher_type": bottle.get("launcher_type", "steam"),
            "default_backend": bottle.get("default_backend", "auto"),
            "wine_binary": bottle.get("wine_binary", "auto"),
            "game_esync": bottle.get("game_esync", True),
            "game_msync": bottle.get("game_msync", True),
            "discord_rpc": bottle.get("discord_rpc", True),
        })

    # Include bottles that may not be in the prefixes list
    for key, bottle in bottles.items():
        if not key or not key.strip():
            continue  # skip ghost entries
        if key == bottles_base_str:
            continue
        if key in seen:
            continue
        seen.add(key)
        name = bottle.get("name", Path(key).name)
        if not name:
            name = Path(key).name or key
        result.append({
            "path": key,
            "name": name,
            "icon_path": bottle.get("icon_path", ""),
            "launcher_exe": bottle.get("launcher_exe", ""),
            "launcher_type": bottle.get("launcher_type", "steam"),
            "default_backend": bottle.get("default_backend", "auto"),
            "wine_binary": bottle.get("wine_binary", "auto"),
            "game_esync": bottle.get("game_esync", True),
            "game_msync": bottle.get("game_msync", True),
            "discord_rpc": bottle.get("discord_rpc", True),
        })

    return result


def cmd_scan_games(params: Dict[str, Any]) -> Any:
    prefix_str = params.get("prefix")
    if not prefix_str:
        raise ValueError("Missing 'prefix' parameter")

    prefix = Path(prefix_str).expanduser().resolve()
    steam_dir = prefix / "drive_c" / "Program Files (x86)" / "Steam"

    games: List[Dict[str, Any]] = []

    # --- Steam games ---
    if steam_dir.exists():
        roots = _library_roots(prefix, steam_dir)
        for root in roots:
            steamapps = root / "steamapps"
            if not steamapps.exists():
                continue
            for manifest in sorted(steamapps.glob("appmanifest_*.acf")):
                data = _parse_appmanifest(manifest)
                if not data:
                    continue
                appid = data["appid"]
                if appid == "228980":
                    continue
                name = data["name"]
                installdir = data["installdir"]
                library_root = manifest.parent.parent
                game_dir = steamapps / "common" / installdir
                exe = _detect_exe(game_dir, installdir, name)
                cover_url = f"https://steamcdn-a.akamaihd.net/steam/apps/{appid}/library_600x900_2x.jpg"
                exe_icon_b64 = None
                if exe:
                    try:
                        ico_bytes = _pe_extract_ico(exe)
                        if ico_bytes:
                            exe_icon_b64 = base64.b64encode(ico_bytes).decode()
                    except Exception as exc:
                        log(f"scan_games: failed to extract icon for {exe}: {exc}")
                games.append({
                    "appid": appid,
                    "name": name,
                    "exe": exe,
                    "install_dir": str(game_dir),
                    "cover_url": cover_url,
                    "exe_icon": exe_icon_b64,
                    "exe_icon_format": "ico" if exe_icon_b64 else "",
                    "is_manual": False,
                })

    # --- Manual games from bottles config ---
    key = _resolve_key(prefix_str)
    bottles = _load_bottles()
    bottle = bottles.get(key, {})
    for entry in bottle.get("manual_games", []):
        entry_name = entry.get("name", "")
        exe_str = entry.get("exe", "")
        if not entry_name or not exe_str:
            continue
        uid = f"custom_{abs(hash(exe_str)) % 10_000_000}"
        cover_path = entry.get("cover_path", "")
        resolved_exe = exe_str if Path(exe_str).exists() else None
        exe_icon_b64 = None
        if resolved_exe:
            try:
                ico_bytes = _pe_extract_ico(resolved_exe)
                if ico_bytes:
                    exe_icon_b64 = base64.b64encode(ico_bytes).decode()
            except Exception as exc:
                log(f"scan_games: failed to extract manual icon for {resolved_exe}: {exc}")
        games.append({
            "appid": uid,
            "name": entry_name,
            "exe": resolved_exe,
            "install_dir": str(Path(exe_str).parent) if exe_str else "",
            "cover_url": cover_path or "",
            "exe_icon": exe_icon_b64,
            "exe_icon_format": "ico" if exe_icon_b64 else "",
            "is_manual": True,
        })

    # Deduplicate by appid (a game may appear in multiple library roots)
    seen_ids: set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for g in games:
        if g["appid"] not in seen_ids:
            seen_ids.add(g["appid"])
            deduped.append(g)
    deduped.sort(key=lambda g: g["name"].lower())
    return deduped


def cmd_launch_game(params: Dict[str, Any]) -> Any:
    prefix = params.get("prefix")
    exe = params.get("exe")
    args = params.get("args", "")
    backend = params.get("backend", "auto")
    install_dir = params.get("install_dir", "")
    retina_mode = params.get("retina_mode", False)
<<<<<<< discord-rpc
    metal_hud = params.get("metal_hud", False)
    screen_info = params.get("screen_info", "unknown")
    bottle_cfg = _load_bottles().get(_resolve_key(prefix), {})
=======
    bottle_cfg = _load_bottles().get(_resolve_key(prefix or ""), {})
    metal_hud = params.get("metal_hud")
    if metal_hud is None:
        metal_hud = bottle_cfg.get("metal_hud", False)
>>>>>>> main
    esync = params.get("esync")
    if esync is None:
        esync = bottle_cfg.get("game_esync")
    msync = params.get("msync")
    if msync is None:
        msync = bottle_cfg.get("game_msync")
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")
    if not exe:
        raise ValueError("Missing 'exe' parameter")

    log(f"[display] screens: {screen_info}")
    log(f"[display] retina_mode={retina_mode}")

    exe_path = Path(exe)
    if not exe_path.exists():
        raise FileNotFoundError(f"Executable not found: {exe}")

    # Resolve auto backend
    if not backend or backend == BACKEND_AUTO:
        backend = _resolve_auto_backend()
    log(f"Resolved graphics backend: {backend}")

    # Find wine binary (may be overridden by backend or per-bottle preference)
    key = _resolve_key(prefix)
    bottle_cfg = _load_bottles().get(key, {})
    wine_pref = bottle_cfg.get("wine_binary", "auto")
    wine = _backend_wine_binary(backend, exe) or _find_wine_for_bottle(wine_pref)
    # D3DMetal3: launch Steam in Mini Games List mode first, then the game
    # under GPTK wine64. Steam must be running for CS2 / Source 2 games to
    # authenticate, and minigameslist mode skips the crashy steamwebhelper.
    if backend == BACKEND_D3DMETAL3:
        try:
            steam_result = cmd_launch_steam({
                "prefix": prefix,
                "retina_mode": retina_mode,
                "backend": BACKEND_D3DMETAL3,
            })
            if steam_result.get("already_running"):
                log("D3DMetal3: Steam already running, proceeding to game launch")
            else:
                log(f"D3DMetal3: Steam launched (pid {steam_result.get('pid')}), "
                    f"waiting for it to come up before launching game")
                time.sleep(8)
        except Exception as exc:
            log(f"D3DMetal3: Steam auto-launch failed: {exc} (continuing anyway)")

    # Find wine binary (may be overridden by backend)
    wine = _backend_wine_binary(backend, exe) or _find_wine()
    if not wine:
        raise FileNotFoundError("Wine not found. Install Wine first.")

    # Patch game directory with required DLLs (critical step!)
    effective_install_dir = install_dir or str(exe_path.parent)
    try:
        _prepare_game_for_backend(backend, exe_path, effective_install_dir)
    except Exception as exc:
        log(f"Warning: DLL patching failed: {exc}")

    # Build env with backend-specific setup
    env = _wine_env(prefix)
    env = _apply_backend_env(env, backend)
    env = _apply_sync_env(env, esync, msync)

    if metal_hud:
        env["MTL_HUD_ENABLED"] = "1"

    # Apply retina/DPI settings via regedit
    _apply_retina_regedit(wine, env, retina_mode)

    exe_dir = str(exe_path.parent)
    exe_name = exe_path.name

    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", exe_path.stem)
    log_path = str(LOG_DIR / f"{safe_name}-wine.log")

    arg_parts = shlex.split(args) if args else []
    quoted_args = " ".join(shlex.quote(a) for a in arg_parts)

    cmd = _backend_launch_cmd(
        backend, wine, exe_dir, exe_name, prefix, exe, quoted_args, log_path
    )

<<<<<<< discord-rpc
    # Start rpc-bridge before the game using the same wine/env so they share the same wineserver
    if bottle_cfg.get("discord_rpc", True):
        _rpc_bridge_start(wine, env)
=======
    # Heredoc backends (GPTK, D3DMetal3) set env inside zsh — use bash -c.
    # Other backends rely on inherited env — use bash -lc.
    uses_heredoc = backend in (BACKEND_GPTK, BACKEND_D3DMETAL3)
    shell_args = ["bash", "-c", cmd] if uses_heredoc else ["bash", "-lc", cmd]
>>>>>>> main

    log(
        f"Launching [{backend}] esync={env.get('WINEESYNC', '')} "
        f"msync={env.get('WINEMSYNC', '')}: {' '.join(shell_args[:2])} {cmd!r}"
    )
    proc = subprocess.Popen(
        shell_args,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    _running_games[proc.pid] = proc
    log(f"Game launched with PID {proc.pid}, backend={backend}, log at {log_path}")

    return {"pid": proc.pid, "log_path": log_path, "backend": backend}


# Track the Steam process separately so we can detect "already running"
_steam_process: Optional[subprocess.Popen] = None


def cmd_launch_steam(params: Dict[str, Any]) -> Any:
    """Launch Steam inside a Wine prefix.

    Mirrors the logic in MacNCheese.py  MainWindow.launch_steam().
    """
    global _steam_process

    prefix = params.get("prefix")
    retina_mode = params.get("retina_mode", False)
    backend = params.get("backend", "auto")
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")

    # Check if Steam is already running
    if _steam_process is not None and _steam_process.poll() is None:
        return {"already_running": True, "pid": _steam_process.pid}

    if backend == "auto":
        backend = _resolve_auto_backend()

    wine = _find_wine()
    if not wine:
        raise FileNotFoundError("Wine not found. Install Wine first.")

    # Check if this bottle has a custom launcher exe set
    key = _resolve_key(prefix)
    bottle_cfg = _load_bottles().get(key, {})
    launcher_exe = bottle_cfg.get("launcher_exe", "").strip()

    if launcher_exe and Path(launcher_exe).exists():
        # Launch the custom exe instead of Steam — use clean Wine (no graphics backend)
        log(f"Using custom launcher_exe: {launcher_exe}")
        env = _wine_env(prefix)
        env = _apply_backend_env(env, BACKEND_WINE)
        _apply_retina_regedit(wine, env, retina_mode)
        exe_path = Path(launcher_exe)
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", exe_path.stem)
        log_path = str(LOG_DIR / f"{safe_name}-wine.log")
        cmd = (
            f"cd {shlex.quote(str(exe_path.parent))} && "
            f"arch -x86_64 {shlex.quote(wine)} "
            f"{shlex.quote(str(exe_path))} "
            f"> {shlex.quote(log_path)} 2>&1"
        )
        proc = subprocess.Popen(
            ["bash", "-lc", cmd], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _steam_process = proc
        log(f"Custom launcher launched with PID {proc.pid}")
        return {"pid": proc.pid, "log_path": log_path, "already_running": False}
    elif launcher_exe:
        log(f"Custom launcher_exe '{launcher_exe}' not found, falling back to Steam")

    steam_dir = Path(prefix) / "drive_c" / "Program Files (x86)" / "Steam"
    steam_exe = steam_dir / "steam.exe"

    if not steam_exe.exists():
        raise FileNotFoundError(
            f"Steam is not installed in this prefix.\n"
            f"Expected: {steam_exe}"
        )

    # Steam launch: replicate the exact working Terminal heredoc pattern.
    # All env is set inside the zsh heredoc so DYLD_FALLBACK_LIBRARY_PATH
    # is visible to the process (macOS strips DYLD_* from inherited env).
    mnc_root = PORTABLE_DIR / "Wine Stable.app" / "Contents" / "Resources" / "wine"
    mnc_wine = mnc_root / "bin" / "wine"
    if mnc_wine.exists():
        wine = str(mnc_wine)

    # GPTK DLL dir (from installer's install_gptk_dlls)
    # D3DMetal native runtime (.dylib / .framework), not Windows .dlls
    dyld_fallback = ":".join([
        str(D3DMETAL_NATIVE_DIR),
        "/usr/local/lib",
        "/usr/local/opt/freetype/lib",
        "/usr/local/opt/gnutls/lib",
        "/usr/lib",
    ])

    # Build a minimal clean env for the outer process
    env = dict(os.environ)
    for var in (
        "GTK_PATH",
        "GTK_EXE_PREFIX",
        "GTK_DATA_PREFIX",
        "GDK_PIXBUF_MODULEDIR",
        "GDK_PIXBUF_MODULE_FILE",
        "GTK_IM_MODULE_FILE",
        "XDG_DATA_DIRS",
    ):
        env.pop(var, None)

    # Apply retina before launch (writes a .reg file via wine regedit)
    regedit_env = dict(env)
    regedit_env["WINEPREFIX"] = prefix
    regedit_env["PATH"] = f"{mnc_root / 'bin'}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    _apply_retina_regedit(wine, regedit_env, retina_mode)

    safe_name = "Steam"
    log_path = str(LOG_DIR / f"{safe_name}-wine.log")

    # The heredoc sets all env inside zsh so DYLD vars survive SIP.
    heredoc = f"""\
export MNCROOT={shlex.quote(str(mnc_root))}
export MNC_WINE={shlex.quote(wine)}
export WINEPREFIX={shlex.quote(prefix)}
export PATH="$MNCROOT/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export DYLD_FALLBACK_LIBRARY_PATH={shlex.quote(dyld_fallback)}
export ROSETTA_ADVERTISE_AVX=1
unset GTK_PATH GTK_EXE_PREFIX GTK_DATA_PREFIX GDK_PIXBUF_MODULEDIR GDK_PIXBUF_MODULE_FILE GTK_IM_MODULE_FILE XDG_DATA_DIRS
export WINEDLLOVERRIDES="winemenubuilder.exe=d;mscoree=;mshtml="
export WINEDEBUG=-all
export WINEDBG=-all
cd {shlex.quote(str(steam_dir))} || exit 1
rm -rf config/htmlcache appcache/httpcache appcache/htmlcache
"$MNC_WINE" steam.exe -tcp > {shlex.quote(log_path)} 2>&1
"""

    cmd = f"cd ~ && /usr/bin/arch -x86_64 /bin/zsh <<'MNCEOF'\n{heredoc}MNCEOF"

    log(f"Launching Steam: {cmd!r}")
    proc = subprocess.Popen(
        ["bash", "-c", cmd],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    _steam_process = proc
    log(f"Steam launched with PID {proc.pid}, log at {log_path}")

    return {"pid": proc.pid, "log_path": log_path, "already_running": False}


def cmd_launch_launcher(params: Dict[str, Any]) -> Any:
    """Launch the custom launcher_exe for a non-steam bottle.
    Falls back to a plain wine explorer if none is set."""
    global _steam_process

    prefix = params.get("prefix")
    retina_mode = params.get("retina_mode", False)
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")

    if _steam_process is not None and _steam_process.poll() is None:
        return {"already_running": True, "pid": _steam_process.pid}

    wine = _find_wine()
    if not wine:
        raise FileNotFoundError("Wine not found. Install Wine first.")

    key = _resolve_key(prefix)
    bottle_cfg = _load_bottles().get(key, {})
    launcher_exe = bottle_cfg.get("launcher_exe", "").strip()

    if not launcher_exe or not Path(launcher_exe).exists():
        raise FileNotFoundError(
            "No launcher exe configured for this bottle, or the file doesn't exist.\n"
            "Set one in Settings → Bottle → Launcher exe."
        )

    env = _wine_env(prefix)
    # Launchers run clean — no graphics backend, just plain Wine
    env = _apply_backend_env(env, BACKEND_WINE)
    _apply_retina_regedit(wine, env, retina_mode)

    exe_path = Path(launcher_exe)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", exe_path.stem)
    log_path = str(LOG_DIR / f"{safe_name}-wine.log")

    cmd = (
        f"cd {shlex.quote(str(exe_path.parent))} && "
        f"arch -x86_64 {shlex.quote(wine)} "
        f"{shlex.quote(str(exe_path))} "
        f"> {shlex.quote(log_path)} 2>&1"
    )

    log(f"Launching custom launcher: bash -lc {cmd!r}")
    proc = subprocess.Popen(
        ["bash", "-lc", cmd], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _steam_process = proc
    log(f"Custom launcher PID {proc.pid}, log at {log_path}")
    return {"pid": proc.pid, "log_path": log_path, "already_running": False}


_setup_proc: Optional[subprocess.Popen] = None


def _download_and_run_steam_setup(prefix: str, wine: str) -> None:
    """Download SteamSetup.exe and run it in the given prefix (background thread)."""
    global _setup_proc
    try:
        setup_path = Path(tempfile.gettempdir()) / "SteamSetup.exe"
        if not setup_path.exists():
            log("Downloading SteamSetup.exe...")
            urllib.request.urlretrieve(STEAM_SETUP_URL, str(setup_path))
            log("SteamSetup.exe downloaded.")
        env = _wine_env(prefix)
        log(f"Launching SteamSetup.exe in {prefix}")
        proc = subprocess.Popen(
            [wine, str(setup_path)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _setup_proc = proc
    except Exception as exc:
        log(f"Warning: failed to run SteamSetup: {exc}")


def cmd_get_setup_pid(_params: Dict[str, Any]) -> Any:
    global _setup_proc
    running = _setup_proc is not None and _setup_proc.poll() is None
    return {"running": running}


def cmd_create_bottle(params: Dict[str, Any]) -> Any:
    name = params.get("name")
    if not name:
        raise ValueError("Missing 'name' parameter")

    launcher_type = params.get("launcher_type", "steam")
    default_backend = params.get("default_backend", "auto")

    custom_path = params.get("path")
    if custom_path:
        bottle_path = Path(custom_path)
    else:
        bottle_path = BOTTLES_BASE / name
    bottle_path.mkdir(parents=True, exist_ok=True)

    path_str = str(bottle_path)
    key = _resolve_key(path_str)

    # Add to prefixes list
    prefixes = _load_prefixes()
    if path_str not in prefixes:
        prefixes.append(path_str)
        _save_prefixes(prefixes)

    # Set bottle config
    bottles = _load_bottles()
    existing = bottles.get(key, {})
    existing["name"] = name
    existing["launcher_type"] = launcher_type
    existing["default_backend"] = default_backend
    bottles[key] = existing
    _save_bottles(bottles)

    # Run wineboot to initialize the prefix
    wine = _find_wine()
    if wine:
        env = _wine_env(path_str)
        try:
            log(f"Running wineboot -u for {path_str}")
            subprocess.run(
                [wine, "wineboot", "-u"],
                env=env,
                timeout=120,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            log(f"wineboot failed: {exc}")
    else:
        log("Wine not found, skipping wineboot initialization")

    # For Steam bottles, download and run SteamSetup.exe in the background
    if launcher_type == "steam" and wine:
        threading.Thread(
            target=_download_and_run_steam_setup,
            args=(path_str, wine),
            daemon=True,
        ).start()

    return {"path": path_str}


def cmd_reorder_bottles(params: Dict[str, Any]) -> Any:
    """Save a new bottle order. `paths` is the ordered list of prefix paths."""
    paths = params.get("paths")
    if not isinstance(paths, list):
        raise ValueError("Missing 'paths' list parameter")
    # Keep only paths that are already known, discard unknowns
    existing = set(_resolve_key(p) for p in _load_prefixes())
    ordered = [p for p in paths if _resolve_key(p) in existing]
    # Append any that were in existing but not in the new order (safety)
    ordered_keys = set(_resolve_key(p) for p in ordered)
    for p in _load_prefixes():
        if _resolve_key(p) not in ordered_keys:
            ordered.append(p)
    _save_prefixes(ordered)
    return {"ok": True}


def cmd_delete_bottle(params: Dict[str, Any]) -> Any:
    path = params.get("path")
    if not path:
        raise ValueError("Missing 'path' parameter")

    key = _resolve_key(path)

    # Remove from prefixes
    prefixes = _load_prefixes()
    prefixes = [p for p in prefixes if _resolve_key(p) != key]
    _save_prefixes(prefixes)

    # Remove from bottles config
    bottles = _load_bottles()
    bottles.pop(key, None)
    _save_bottles(bottles)

    # Delete directory
    resolved = Path(path).expanduser().resolve()
    if resolved.exists():
        log(f"Deleting directory: {resolved}")
        shutil.rmtree(str(resolved), ignore_errors=True)

    return None


def cmd_get_bottle_config(params: Dict[str, Any]) -> Any:
    path = params.get("path")
    if not path:
        raise ValueError("Missing 'path' parameter")

    key = _resolve_key(path)
    bottles = _load_bottles()
    config = dict(bottles.get(key, {}))
    config.setdefault("game_esync", True)
    config.setdefault("game_msync", True)
<<<<<<< discord-rpc
    config.setdefault("discord_rpc", True)
=======
    config.setdefault("metal_hud", False)
>>>>>>> main
    return config


def cmd_set_bottle_config(params: Dict[str, Any]) -> Any:
    path = params.get("path")
    if not path:
        raise ValueError("Missing 'path' parameter")

    key = _resolve_key(path)
    bottles = _load_bottles()
    existing = bottles.get(key, {})

    # Update with all provided keys except "path" and "cmd"/"id"
    skip_keys = {"path", "cmd", "id"}
    for k, v in params.items():
        if k not in skip_keys:
            existing[k] = v

    # If discord_rpc changed, install or uninstall the bridge service in the prefix
    if "discord_rpc" in params:
        if params["discord_rpc"]:
            threading.Thread(target=_rpc_bridge_install_prefix, args=(path,), daemon=True).start()
        else:
            threading.Thread(target=_rpc_bridge_uninstall_prefix, args=(path,), daemon=True).start()

    bottles[key] = existing
    _save_bottles(bottles)
    return existing


def cmd_kill_wineserver(params: Dict[str, Any]) -> Any:
    prefix = params.get("prefix")
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")

    wineserver = _find_wineserver()
    if not wineserver:
        raise FileNotFoundError("wineserver not found")

    env = _wine_env(prefix)
    try:
        subprocess.run(
            [wineserver, "-k"],
            env=env,
            timeout=10,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        log("wineserver -k timed out")
    return None


def cmd_get_status(params: Dict[str, Any]) -> Any:
    wine = _find_wine()
    return {
        "wine_found": wine is not None,
        "wine_path": wine or "",
        "has_dxvk": _dxvk_available(),
        "has_mesa": _mesa_available(),
    }


def cmd_add_manual_game(params: Dict[str, Any]) -> Any:
    prefix = params.get("prefix")
    name = params.get("name")
    exe = params.get("exe")
    cover_path = params.get("cover_path")

    if not prefix:
        raise ValueError("Missing 'prefix' parameter")
    if not name:
        raise ValueError("Missing 'name' parameter")
    if not exe:
        raise ValueError("Missing 'exe' parameter")

    key = _resolve_key(prefix)
    bottles = _load_bottles()
    bottle = bottles.get(key, {})
    manual: List[Dict[str, str]] = list(bottle.get("manual_games", []))

    # Deduplicate by exe path
    if any(m.get("exe") == exe for m in manual):
        return bottle.get("manual_games", [])

    entry: Dict[str, str] = {"name": name, "exe": exe}
    if cover_path:
        entry["cover_path"] = cover_path
    manual.append(entry)

    bottle["manual_games"] = manual
    bottles[key] = bottle
    _save_bottles(bottles)

    return manual


def cmd_init_prefix(params: Dict[str, Any]) -> Any:
    """Run wineboot -u to create/repair a Wine prefix."""
    prefix = params.get("prefix")
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")
    wine = _find_wine()
    if not wine:
        raise FileNotFoundError("Wine not found")
    env = _wine_env(prefix)
    log(f"init_prefix: wineboot -u for {prefix}")
    subprocess.run(
        [wine, "wineboot", "-u"], env=env, timeout=120,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return None


def cmd_clean_prefix(params: Dict[str, Any]) -> Any:
    """Run wineboot -u to clean/update a prefix."""
    prefix = params.get("prefix")
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")
    wine = _find_wine()
    if not wine:
        raise FileNotFoundError("Wine not found")
    env = _wine_env(prefix)
    log(f"clean_prefix: wineboot -u for {prefix}")
    subprocess.run(
        [wine, "wineboot", "-u"], env=env, timeout=120,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return None


def cmd_run_exe(params: Dict[str, Any]) -> Any:
    """Run an arbitrary .exe inside a prefix (for installers, SteamSetup, etc.)."""
    prefix = params.get("prefix")
    exe = params.get("exe")
    args = params.get("args", "")
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")
    if not exe:
        raise ValueError("Missing 'exe' parameter")
    exe_path = Path(exe)
    if not exe_path.exists():
        raise FileNotFoundError(f"File not found: {exe}")
    wine = _find_wine()
    if not wine:
        raise FileNotFoundError("Wine not found")
    env = _wine_env(prefix)
    arg_parts = shlex.split(args) if args else []
    cmd_list = [wine, str(exe_path)] + arg_parts
    log(f"run_exe: {cmd_list}")
    proc = subprocess.Popen(
        cmd_list, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _running_games[proc.pid] = proc
    return {"pid": proc.pid}


def cmd_open_prefix_folder(params: Dict[str, Any]) -> Any:
    """Open a prefix folder in Finder."""
    prefix = params.get("prefix")
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")
    p = Path(prefix)
    if not p.exists():
        raise FileNotFoundError(f"Path not found: {prefix}")
    subprocess.Popen(["open", str(p)])
    return None


def cmd_detect_exes(params: Dict[str, Any]) -> Any:
    """List all plausible game executables in a game's install directory."""
    install_dir = params.get("install_dir")
    if not install_dir:
        raise ValueError("Missing 'install_dir' parameter")
    return _detect_all_exes(Path(install_dir))


def cmd_list_backends(params: Dict[str, Any]) -> Any:
    """Return available graphics backends and which is auto-selected."""
    all_backends = [
        {"id": BACKEND_AUTO, "label": "Auto (recommended)", "available": True},
        {"id": BACKEND_WINE, "label": "Wine builtin (no DXVK/Mesa)", "available": True},
        {"id": BACKEND_DXVK, "label": "DXVK (D3D11→Vulkan)", "available": _dxvk_available()},
        {"id": BACKEND_MESA_LLVMPIPE, "label": "Mesa llvmpipe (CPU, safe)", "available": _mesa_available()},
        {"id": BACKEND_MESA_ZINK, "label": "Mesa zink (GPU, Vulkan)", "available": _mesa_available()},
        {"id": BACKEND_MESA_SWR, "label": "Mesa swr (CPU rasterizer)", "available": _mesa_available()},
        {"id": BACKEND_VKD3D, "label": "VKD3D-Proton (D3D12)", "available": _vkd3d_available()},
        {"id": BACKEND_DXMT, "label": "DXMT (experimental)", "available": _dxmt_available()},
        {"id": BACKEND_D3DMETAL3, "label": "D3DMetal (injection, recommended)", "available": _d3dmetal3_available()},
        {"id": BACKEND_GPTK, "label": "GPTK (D3DMetal, copy DLLs)", "available": _gptk_available()},
        {"id": BACKEND_GPTK_FULL, "label": "GPTK Full (Apple Toolkit)", "available": _gptk_full_available()},
    ]
    auto_resolved = _resolve_auto_backend()
    return {"backends": all_backends, "auto_resolved": auto_resolved}


def _tool_available(name: str) -> bool:
    """Check if a CLI tool is available, also searching Homebrew paths."""
    if shutil.which(name) is not None:
        return True
    for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
        if Path(prefix, name).exists():
            return True
    return False


def _read_version_marker(component: str) -> Optional[str]:
    """Read an installed version tag from the marker file."""
    if not VERSION_MARKER.exists():
        return None
    for line in VERSION_MARKER.read_text().splitlines():
        if line.startswith(f"{component}="):
            return line.split("=", 1)[1].strip()
    return None


def _get_wine_version(wine: Optional[str] = None) -> Optional[str]:
    """Run wine --version and return the raw version string."""
    if wine is None:
        wine = _find_wine()
    if not wine:
        return None
    try:
        result = subprocess.run(
            [wine, "--version"],
            capture_output=True, text=True, timeout=8
        )
        return result.stdout.strip() or None
    except Exception:
        return None


# Simple in-process cache: {cache_key: (timestamp, value)}
_github_cache: Dict[str, Any] = {}
_GITHUB_CACHE_TTL = 3600  # 1 hour


def _fetch_latest_github_release(owner: str, repo: str) -> Optional[Dict[str, Any]]:
    """Fetch latest release info from GitHub API, with 1-hour cache."""
    cache_key = f"{owner}/{repo}"
    cached = _github_cache.get(cache_key)
    if cached and (time.time() - cached[0]) < _GITHUB_CACHE_TTL:
        return cached[1]
    try:
        url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        req = urllib.request.Request(url, headers={"User-Agent": "MacNCheese/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            _github_cache[cache_key] = (time.time(), data)
            return data
    except Exception:
        return None


def cmd_get_update_info(params: Dict[str, Any]) -> Any:
    """Check GitHub for latest release versions and compare with installed markers."""
    cheese_release = _fetch_latest_github_release("mont127", "CheeseInstallation")
    gcenx_release = _fetch_latest_github_release("Gcenx", "macOS_Wine_builds")
    dxmt_release = _fetch_latest_github_release("3Shain", "dxmt")

    cheese_tag = cheese_release.get("tag_name") if cheese_release else None
    gcenx_tag = gcenx_release.get("tag_name") if gcenx_release else None
    gcenx_name = (gcenx_release.get("name") or gcenx_tag) if gcenx_release else None
    dxmt_tag = dxmt_release.get("tag_name") if dxmt_release else None
    dxmt_name = (dxmt_release.get("name") or dxmt_tag) if dxmt_release else None

    installed_tools = _read_version_marker("tools")
    installed_wine_stable = _read_version_marker("wine_stable")
    installed_wine_staging = _read_version_marker("wine_staging")
    installed_dxmt = _read_version_marker("dxmt")

    tools_update = bool(cheese_tag and installed_tools and cheese_tag != installed_tools)
    wine_stable_update = bool(cheese_tag and installed_wine_stable and cheese_tag != installed_wine_stable)
    wine_staging_update = bool(gcenx_tag and installed_wine_staging and gcenx_tag != installed_wine_staging)
    dxmt_update = bool(dxmt_tag and installed_dxmt and dxmt_tag != installed_dxmt)

    return {
        "cheese_latest_tag": cheese_tag,
        "gcenx_latest_tag": gcenx_tag,
        "gcenx_latest_name": gcenx_name,
        "dxmt_latest_tag": dxmt_tag,
        "dxmt_latest_name": dxmt_name,
        "tools_update_available": tools_update,
        "wine_update_available": wine_stable_update or wine_staging_update,
        "wine_stable_update_available": wine_stable_update,
        "wine_staging_update_available": wine_staging_update,
        "dxmt_update_available": dxmt_update,
    }


def _portable_tools_available() -> bool:
    """Check if portable toolchain is present enough for app use."""
    bin_dir = PORTABLE_DIR / "bin"
    # Need at least 7zz/7z and git
    has_7z = (bin_dir / "7zz").exists() or (bin_dir / "7z").exists()
    has_git = (bin_dir / "git").exists()
    return has_7z and has_git

def _gptk_dlls_available() -> bool:
    """Check if GPTK DLL package is installed (just the DLLs, not the full toolkit)."""
    dll_dir = DEFAULT_GPTK_DIR / "lib" / "wine" / "x86_64-windows"
    if not dll_dir.exists():
        return False
    required = ("d3d11.dll", "d3d12.dll", "dxgi.dll")
    return all((dll_dir / name).exists() for name in required)

def cmd_get_components_status(params: Dict[str, Any]) -> Any:
    """Return installation status for each setup component."""
    has_tools = _portable_tools_available() or all(_tool_available(t) for t in ("git", "7z"))
    dxvk32_install = Path.home() / "dxvk-release-32"
    has_dxvk32 = (dxvk32_install / "bin" / "d3d11.dll").exists()
    has_wine_stable = _find_wine_stable() is not None
    has_wine_staging = _find_wine_staging() is not None
    wine_version = _get_wine_version()
    return {
        "has_tools": has_tools,
        "has_wine": has_wine_stable or has_wine_staging,
        "has_wine_stable": has_wine_stable,
        "has_wine_staging": has_wine_staging,
        "has_mesa": _mesa_available(),
        "has_dxvk64": _dxvk_available(),
        "has_dxvk32": has_dxvk32,
        "has_dxmt": _dxmt_available(),
        "has_gptk_dlls": _gptk_dlls_available(),
        "has_d3dmetal3": _d3dmetal3_available(),
        "has_vkd3d": _vkd3d_available(),
        "wine_version": wine_version,
        "has_rpc_bridge": _rpc_bridge_available(),
    }


# ---------------------------------------------------------------------------
# Pure-Python PE icon extractor (zero external dependencies)
# ---------------------------------------------------------------------------

def _pe_rva_to_offset(data: bytes, rva: int) -> int:
    """Convert a PE RVA to a file offset by walking the section table."""
    # PE sig offset is at 0x3C
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    # COFF header: sig(4) + machine(2) + num_sections(2) + ...
    num_sections = struct.unpack_from("<H", data, pe_off + 6)[0]
    opt_size = struct.unpack_from("<H", data, pe_off + 20)[0]
    # Section table starts right after the optional header
    sect_off = pe_off + 24 + opt_size
    for i in range(num_sections):
        s = sect_off + i * 40
        virt_addr = struct.unpack_from("<I", data, s + 12)[0]
        virt_size = struct.unpack_from("<I", data, s + 16)[0]
        raw_off   = struct.unpack_from("<I", data, s + 20)[0]
        if virt_addr <= rva < virt_addr + max(virt_size, 1):
            return raw_off + (rva - virt_addr)
    raise ValueError(f"RVA 0x{rva:x} not found in any section")


def _pe_rsrc_find(data: bytes, rsrc_off: int, target_id: int) -> Optional[int]:
    """
    Walk one level of an IMAGE_RESOURCE_DIRECTORY to find an entry by integer ID.
    Returns the raw OffsetToData value (high bit indicates sub-directory).
    """
    named = struct.unpack_from("<H", data, rsrc_off + 12)[0]
    ided  = struct.unpack_from("<H", data, rsrc_off + 14)[0]
    for i in range(named + ided):
        entry_off = rsrc_off + 16 + i * 8
        name_id = struct.unpack_from("<I", data, entry_off)[0]
        offset  = struct.unpack_from("<I", data, entry_off + 4)[0]
        # Skip named entries (high bit set on name_id) — we only match integer IDs
        if name_id & 0x80000000:
            continue
        if name_id == target_id:
            return offset
    return None


def _pe_extract_ico(exe_path: str) -> Optional[bytes]:
    """
    Parse a Windows PE file and extract its primary group icon as ICO bytes.
    Uses only stdlib (struct, io). Returns None if no icon is found.
    """
    RT_ICON       = 3
    RT_GROUP_ICON = 14

    try:
        with open(exe_path, "rb") as f:
            data = f.read()

        # Validate MZ + PE signatures
        if data[:2] != b"MZ":
            return None
        pe_off = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe_off:pe_off+4] != b"PE\x00\x00":
            return None

        # Optional header magic: 0x10B = PE32, 0x20B = PE32+
        opt_magic = struct.unpack_from("<H", data, pe_off + 24)[0]
        # DataDirectory starts at byte 96 in PE32 optional header, 112 in PE32+
        dd_off = pe_off + 24 + (112 if opt_magic == 0x20B else 96)
        rsrc_rva = struct.unpack_from("<I", data, dd_off + 2 * 8)[0]  # entry [2] = resources
        if rsrc_rva == 0:
            return None

        rsrc_base = _pe_rva_to_offset(data, rsrc_rva)

        # Level 1: find RT_GROUP_ICON and RT_ICON type directories
        grp_ptr = _pe_rsrc_find(data, rsrc_base, RT_GROUP_ICON)
        ico_ptr = _pe_rsrc_find(data, rsrc_base, RT_ICON)
        if grp_ptr is None or ico_ptr is None:
            return None

        # Both should be sub-directories (high bit set)
        grp_dir = rsrc_base + (grp_ptr & 0x7FFFFFFF)
        ico_dir = rsrc_base + (ico_ptr & 0x7FFFFFFF)

        # Level 2 for RT_ICON: build map of icon_id → data entry offset
        ico_named = struct.unpack_from("<H", data, ico_dir + 12)[0]
        ico_ided  = struct.unpack_from("<H", data, ico_dir + 14)[0]
        icons_by_id: Dict[int, int] = {}
        for i in range(ico_named + ico_ided):
            e = ico_dir + 16 + i * 8
            icon_id  = struct.unpack_from("<I", data, e)[0]
            sub_ptr  = struct.unpack_from("<I", data, e + 4)[0]
            if icon_id & 0x80000000:
                continue  # skip named
            # Level 3: language sub-directory → first entry → data entry
            lang_dir = rsrc_base + (sub_ptr & 0x7FFFFFFF)
            lang_ptr = struct.unpack_from("<I", data, lang_dir + 16 + 4)[0]
            data_entry_off = rsrc_base + (lang_ptr & 0x7FFFFFFF)
            icons_by_id[icon_id] = data_entry_off

        # Level 2 for RT_GROUP_ICON: first group entry
        grp_named = struct.unpack_from("<H", data, grp_dir + 12)[0]
        grp_ided  = struct.unpack_from("<H", data, grp_dir + 14)[0]
        if grp_named + grp_ided == 0:
            return None
        first_grp_e = grp_dir + 16  # first entry (we take index 0)
        grp_sub_ptr = struct.unpack_from("<I", data, first_grp_e + 4)[0]
        # Level 3: language sub-directory → data entry
        glang_dir = rsrc_base + (grp_sub_ptr & 0x7FFFFFFF)
        glang_ptr = struct.unpack_from("<I", data, glang_dir + 16 + 4)[0]
        gdata_entry_off = rsrc_base + (glang_ptr & 0x7FFFFFFF)
        grp_rva  = struct.unpack_from("<I", data, gdata_entry_off)[0]
        grp_size = struct.unpack_from("<I", data, gdata_entry_off + 4)[0]
        grp_file_off = _pe_rva_to_offset(data, grp_rva)
        grp_data = data[grp_file_off: grp_file_off + grp_size]

        # Parse GRPICONDIR + GRPICONDIRENTRY structs
        count = struct.unpack_from("<HHH", grp_data, 0)[2]
        GRPICONDIRENTRY_SIZE = 14
        icon_items = []  # (width, height, entry_bytes_12, icon_raw_data)
        for i in range(count):
            off = 6 + i * GRPICONDIRENTRY_SIZE
            entry = grp_data[off: off + GRPICONDIRENTRY_SIZE]
            width  = entry[0] or 256
            height = entry[1] or 256
            icon_id = struct.unpack_from("<H", entry, 12)[0]
            if icon_id not in icons_by_id:
                continue
            de = icons_by_id[icon_id]
            ico_rva  = struct.unpack_from("<I", data, de)[0]
            ico_size = struct.unpack_from("<I", data, de + 4)[0]
            ico_file_off = _pe_rva_to_offset(data, ico_rva)
            icon_raw = data[ico_file_off: ico_file_off + ico_size]
            icon_items.append((width, height, bytes(entry[:12]), icon_raw))

        if not icon_items:
            return None

        # Sort largest first, then build the .ico file
        icon_items.sort(key=lambda x: x[0], reverse=True)
        n = len(icon_items)
        buf = io.BytesIO()
        buf.write(struct.pack("<HHH", 0, 1, n))  # ICONDIR
        data_offset = 6 + n * 16
        for _, _, entry12, raw in icon_items:
            # ICONDIRENTRY = 12 bytes (width..BytesInRes from GRPICONDIRENTRY) + 4-byte ImageOffset
            buf.write(entry12)
            buf.write(struct.pack("<I", data_offset))
            data_offset += len(raw)
        for _, _, _, raw in icon_items:
            buf.write(raw)
        return buf.getvalue()

    except Exception as exc:
        log(f"_pe_extract_ico error ({type(exc).__name__}): {exc}")
        return None


def cmd_get_exe_icon(params: Dict[str, Any]) -> Any:
    """Extract the primary icon from a Windows PE executable and return it as base64 ICO."""
    exe_path = params.get("exe", "")
    log(f"get_exe_icon: exe={exe_path!r}")
    if not exe_path or not Path(exe_path).exists():
        log("get_exe_icon: file not found")
        return {"icon": None, "format": "", "ok": False}

    ico_bytes = _pe_extract_ico(exe_path)
    if ico_bytes:
        log(f"get_exe_icon: returning {len(ico_bytes)} bytes")
        return {"icon": base64.b64encode(ico_bytes).decode(), "format": "ico", "ok": True}

    log("get_exe_icon: no icon found")
    return {"icon": None, "format": "", "ok": False}


def cmd_get_running_games(params: Dict[str, Any]) -> Any:
    alive: List[Dict[str, Any]] = []
    dead_pids: List[int] = []

    for pid, proc in _running_games.items():
        retcode = proc.poll()
        if retcode is None:
            alive.append({"pid": pid})
        else:
            dead_pids.append(pid)

    # Clean up finished processes
    for pid in dead_pids:
        _running_games.pop(pid, None)

    return alive


def cmd_get_steam_running(_params: Dict[str, Any]) -> Any:
    global _steam_process
    running = _steam_process is not None and _steam_process.poll() is None
    return {"running": running}

# ---------------------------------------------------------------------------
# Installer job management
# ---------------------------------------------------------------------------

_install_jobs: Dict[str, Dict] = {}

def cmd_run_installer(params: Dict[str, Any]) -> Any:
    actions: List[str] = params.get("actions", [])
    installer_path: str = params.get("installer_path", "")
    prefix: str = params.get("prefix", "")
    dxvk_src: str = params.get("dxvk_src", "")
    dxvk64: str = params.get("dxvk64", "")
    dxvk32: str = params.get("dxvk32", "")
    mesa: str = params.get("mesa", "")
    mesa_url: str = params.get("mesa_url", "")
    dxmt: str = params.get("dxmt", "")
    vkd3d: str = params.get("vkd3d", "")

    if not actions:
        raise ValueError("No actions specified")
    if not installer_path or not Path(installer_path).exists():
        raise FileNotFoundError(f"installer.sh not found at: {installer_path}")

    import uuid
    job_id = str(uuid.uuid4())
    job: Dict[str, Any] = {"lines": [], "done": False, "failed": False, "current": ""}
    _install_jobs[job_id] = job

    def _friendly_action(action: str) -> str:
        verb = "Uninstalling" if action.startswith("uninstall_") else "Installing"
        name = action.replace("install_", "").replace("uninstall_", "").replace("_", " ").title()
        return f"{verb} {name}"

    def _run() -> None:
        env = {**os.environ, "MNC_SUDOLESS": "1"}
        for action in actions:
            friendly = _friendly_action(action)
            job["current"] = friendly
            job["lines"].append(f"=== {friendly} ===")
            try:
                proc = subprocess.Popen(
                    [installer_path, action, prefix, dxvk_src, dxvk64, dxvk32, mesa, mesa_url, dxmt, "", vkd3d],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    job["lines"].append(line.rstrip())
                proc.wait()
                if proc.returncode != 0:
                    job["lines"].append(f"!!! {friendly} failed (exit {proc.returncode})")
                    job["failed"] = True
                else:
                    job["lines"].append(f"--- {friendly} completed successfully ---")
            except Exception as exc:
                job["lines"].append(f"!!! {friendly} error: {exc}")
                job["failed"] = True
        job["current"] = ""
        job["lines"].append("=== All tasks finished ===")
        job["done"] = True

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"job_id": job_id}


def cmd_get_install_progress(params: Dict[str, Any]) -> Any:
    job_id: str = params.get("job_id", "")
    offset: int = params.get("offset", 0)
    job = _install_jobs.get(job_id)
    if job is None:
        return {"lines": [], "total_lines": 0, "done": True, "failed": False, "current": ""}
    lines = job["lines"]
    new_lines = lines[offset:]
    return {
        "lines": new_lines,
        "total_lines": len(lines),
        "done": job["done"],
        "failed": job.get("failed", False),
        "current": job.get("current", ""),
    }

# ---------------------------------------------------------------------------
# Command dispatch table
# ---------------------------------------------------------------------------

COMMANDS: Dict[str, Any] = {
    "list_bottles": cmd_list_bottles,
    "scan_games": cmd_scan_games,
    "launch_game": cmd_launch_game,
    "launch_steam": cmd_launch_steam,
    "create_bottle": cmd_create_bottle,
    "delete_bottle": cmd_delete_bottle,
    "get_bottle_config": cmd_get_bottle_config,
    "set_bottle_config": cmd_set_bottle_config,
    "kill_wineserver": cmd_kill_wineserver,
    "init_prefix": cmd_init_prefix,
    "clean_prefix": cmd_clean_prefix,
    "run_exe": cmd_run_exe,
    "open_prefix_folder": cmd_open_prefix_folder,
    "get_status": cmd_get_status,
    "add_manual_game": cmd_add_manual_game,
    "detect_exes": cmd_detect_exes,
    "list_backends": cmd_list_backends,
    "get_components_status": cmd_get_components_status,
    "get_update_info": cmd_get_update_info,
    "get_running_games": cmd_get_running_games,
    "get_steam_running": cmd_get_steam_running,
    "get_setup_pid": cmd_get_setup_pid,
    "reorder_bottles": cmd_reorder_bottles,
    "launch_launcher": cmd_launch_launcher,
    "get_exe_icon": cmd_get_exe_icon,
    "run_installer": cmd_run_installer,
    "get_install_progress": cmd_get_install_progress,
}

# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def _respond(req_id: Any, ok: bool, data: Any = None, error: str = "") -> None:
    resp: Dict[str, Any] = {"id": req_id, "ok": ok}
    if ok:
        resp["data"] = data
    else:
        resp["error"] = error
    line = json.dumps(resp, default=str)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    log("MacNCheese backend server started")
    log(f"PORTABLE_DIR = {PORTABLE_DIR}")
    log(f"BOTTLES_BASE = {BOTTLES_BASE}")
    log(f"DEFAULT_PREFIX = {DEFAULT_PREFIX}")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        req_id = None
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _respond(None, False, error=f"Invalid JSON: {exc}")
            continue

        req_id = request.get("id")
        cmd_name = request.get("cmd")

        if not cmd_name:
            _respond(req_id, False, error="Missing 'cmd' field")
            continue

        handler = COMMANDS.get(cmd_name)
        if not handler:
            _respond(req_id, False, error=f"Unknown command: {cmd_name}")
            continue

        try:
            log(f"Handling cmd={cmd_name} id={req_id}")
            result = handler(request)
            _respond(req_id, True, data=result)
        except Exception as exc:
            log(f"Error in {cmd_name}: {exc}")
            _respond(req_id, False, error=str(exc))


if __name__ == "__main__":
    main()
