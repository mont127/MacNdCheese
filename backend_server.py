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

import atexit
import base64
import datetime
import filecmp
import html as html_lib
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
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple




PORTABLE_DIR = Path.home() / "Library" / "Application Support" / "MacNCheese" / "deps"
VERSION_MARKER = PORTABLE_DIR / ".mnc_versions"
BOTTLES_BASE = Path.home() / "Games" / "MacNCheese"
DEFAULT_PREFIX = str(Path.home() / "wined")

PREFIXES_JSON = Path.home() / ".macncheese_prefixes.json"
BOTTLES_JSON = Path.home() / ".macncheese_bottles.json"

STEAM_SETUP_URL = "https://cdn.fastly.steamstatic.com/client/installer/SteamSetup.exe"

LEGENDARY_DIR = PORTABLE_DIR / "legendary"
LEGENDARY_BIN = LEGENDARY_DIR / "legendary"


def _legendary_config_dir(prefix: str) -> Path:
    """Returns the per-bottle Legendary config directory."""
    return Path(prefix).expanduser().resolve() / ".legendary_config"


def _legendary_cmd(prefix: str) -> List[str]:
    """Base legendary command (config isolation is done via LEGENDARY_CONFIG_PATH env var)."""
    return [str(LEGENDARY_BIN)]


def _legendary_env(prefix: str, base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Returns an environment dict with LEGENDARY_CONFIG_PATH set to the per-bottle dir."""
    env = (base if base is not None else os.environ).copy()
    config_dir = _legendary_config_dir(prefix)
    config_dir.mkdir(parents=True, exist_ok=True)
    env["LEGENDARY_CONFIG_PATH"] = str(config_dir)
    return env
_EPIC_CLIENT_ID = "34a02cf8f4414e29b15921876da36f9a"
_EPIC_REDIRECT = (
    f"https://www.epicgames.com/id/api/redirect"
    f"?clientId={_EPIC_CLIENT_ID}&responseType=code"
)
EPIC_AUTH_URL = (
    "https://www.epicgames.com/id/login"
    f"?redirectUrl={urllib.parse.quote(_EPIC_REDIRECT, safe='')}"
)

APPMANIFEST_RE = re.compile(r'"(\w+)"\s+"([^"]*)"')

_legendary_installing: bool = False
_legendary_installs: Dict[str, Any] = {}  # app_name -> (Popen, file, log_path, prefix)
_legendary_games_cache: Dict[str, Any] = {}  # prefix -> {"games": [], "ts": float, "scanning": bool}
_LEGENDARY_CACHE_TTL = 300  # seconds before a background re-fetch is triggered

# Download queue — one install runs at a time, others wait.
_legendary_download_queue: List[Tuple[str, str]] = []  # [(app_name, prefix)]
_legendary_queue_lock = threading.Lock()
_legendary_queue_worker_running: bool = False


def _terminate_legendary_installs() -> None:
    """Kill all active legendary install processes and clear the queue. Called on backend exit."""
    with _legendary_queue_lock:
        _legendary_download_queue.clear()
    for app_name, entry in list(_legendary_installs.items()):
        proc = entry[0]
        try:
            proc.terminate()
        except Exception:
            pass
    _legendary_installs.clear()


atexit.register(_terminate_legendary_installs)


def _legendary_do_install(app_name: str, prefix: str) -> None:
    """Run one legendary install to completion. Called from the queue worker thread."""
    install_base = str(
        Path(prefix).expanduser().resolve() / "drive_c" / "Program Files" / "Epic Games"
    )
    Path(install_base).mkdir(parents=True, exist_ok=True)
    log_path = str(LEGENDARY_DIR / f"install_{app_name}.log")
    try:
        log_fh = open(log_path, "w")
        proc = subprocess.Popen(
            _legendary_cmd(prefix) + ["install", app_name,
             "--base-path", install_base,
             "-y", "--no-install-prereqs", "--skip-sdl"],
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            env=_legendary_env(prefix),
        )
        _legendary_installs[app_name] = (proc, log_fh, log_path, prefix)
        proc.wait()
    except Exception:
        pass
    finally:
        entry = _legendary_installs.pop(app_name, None)
        if entry:
            try:
                entry[1].close()
            except Exception:
                pass
        _legendary_games_cache.pop(prefix, None)


def _legendary_queue_worker() -> None:
    """Process queued legendary installs one at a time."""
    global _legendary_queue_worker_running
    while True:
        with _legendary_queue_lock:
            if not _legendary_download_queue:
                _legendary_queue_worker_running = False
                return
            app_name, prefix = _legendary_download_queue.pop(0)
        _legendary_do_install(app_name, prefix)


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
# Wine D3DMetal (MNC HACK 22 v3 + 24-26). Bundled patched wine 11 that ships
# inside MacNdCheese Launcher.app and gets unzipped to
# $PORTABLE_DIR/Wine D3DMetal.app by the SwiftUI Setup tab. When picked from
# the Launch sheet, MacNCheese auto-launches Steam in -silent mode inside the
# same prefix under this wine (with WINE_D3DMETAL_NO_STEAM_HACK=1 so HACK 24
# /25 don't perturb Steam's argv), then launches the game in the same prefix
# so cs2 / Source-2 / Steam-IPC games find SteamAPI_Init in the shared
# wineserver. This auto-Steam flow ONLY fires from cmd_launch_game — running
# the wrapper from cli leaves Steam alone.
BACKEND_WINE_D3DMETAL = "wine_d3dmetal"


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

PREFIX_DLL_VERIFY_FILES = (
    "ntdll.dll",
    "kernel32.dll",
    "kernelbase.dll",
    "msvcrt.dll",
    "ucrtbase.dll",
    "advapi32.dll",
    "sechost.dll",
    "ws2_32.dll",
    "rpcrt4.dll",
    "bcrypt.dll",
    "crypt32.dll",
    "combase.dll",
    "ole32.dll",
    "user32.dll",
    "gdi32.dll",
    "shell32.dll",
    "shlwapi.dll",
    "wininet.dll",
    "winhttp.dll",
    "version.dll",
    "start.exe",
    "cmd.exe",
)

PREFIX_LOADER_DLLS = {"ntdll.dll", "kernel32.dll", "kernelbase.dll"}

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
        # 5 min for the same fresh-prefix wineboot reason as _apply_retina_regedit.
        result = subprocess.run(
            [wine, "sc", "start", "rpc-bridge"],
            env=env, timeout=300,
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



def log(msg: str) -> None:
    print(f"[backend] {msg}", file=sys.stderr, flush=True)
    try:
        with APP_LOG_PATH.open("a") as _f:
            import datetime
            _f.write(f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass



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

def _find_wine_d3dmetal() -> Optional[str]:
    """Locate the patched Wine D3DMetal (MNC HACK 22 v3) bundle, if installed.
    The wrapper at Contents/Resources/wine-d3dmetal exec's into the in-process
    Cocoa launcher (Contents/MacOS/wine); we point callers at the wrapper so
    NSApp is set up before wine code runs."""
    wrapper = PORTABLE_DIR / "Wine D3DMetal.app" / "Contents" / "Resources" / "wine-d3dmetal"
    if wrapper.exists():
        return str(wrapper)
    return None

def _wine_d3dmetal_installed() -> bool:
    return (PORTABLE_DIR / "Wine D3DMetal.app").exists()

def _wineopenxr_available() -> bool:
    """True if the wineopenxr bridge (D3D11 OpenXR → native OpenXR) is
    installed into at least one portable Wine tree."""
    for app in ("Wine D3DMetal.app", "Wine Staging.app", "Wine Stable.app"):
        base = PORTABLE_DIR / app / "Contents" / "Resources" / "wine" / "lib" / "wine"
        if (base / "x86_64-windows" / "wineopenxr.dll").exists() and \
           (base / "x86_64-unix" / "wineopenxr.so").exists():
            return True
    return False

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
            try:
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
            except Exception as exc:
                log(f"MoltenVK manifest write failed: {exc}")
                return str(lib)
    return ""


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
        # Timeout is generous (5 min) because the FIRST regedit call against
        # a fresh prefix has to wait for wineboot --init to finish — that's
        # ~2-5 min under our patched wine-d3dmetal because every helper
        # process (services, explorer, plugplay, winedevice, mscoree) goes
        # through the in-process Cocoa launcher init. Subsequent regedit
        # calls in the same prefix return in <1s.
        subprocess.run(
            [wine, "regedit", str(reg_file)],
            env=env, timeout=300,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        log(f"Applied regedit: RetinaMode={retina_val}, Resolution=auto, LogPixels=000000{dpi_hex}")
    except Exception as exc:
        log(f"Warning: regedit failed: {exc}")



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
    """Find the portable Wine's x86_64-windows PE DLL directory (first found)."""
    for wine_app in ["Wine Stable.app", "Wine Staging.app"]:
        candidate = PORTABLE_DIR / wine_app / "Contents" / "Resources" / "wine" / "lib" / "wine" / "x86_64-windows"
        if candidate.is_dir():
            return candidate
    return None

def _find_all_wine_libs() -> List[Tuple[Path, Path]]:
    """Return (win64_lib, unix_lib) pairs for every installed portable Wine bundle."""
    result = []
    for wine_app in ["Wine Stable.app", "Wine Staging.app"]:
        base = PORTABLE_DIR / wine_app / "Contents" / "Resources" / "wine" / "lib" / "wine"
        win64 = base / "x86_64-windows"
        unix = base / "x86_64-unix"
        if win64.is_dir() and unix.is_dir():
            result.append((win64, unix))
    return result

def _find_wine_unix_lib() -> Optional[Path]:
    """Find the portable Wine's x86_64-unix native bridge directory (first found)."""
    for wine_app in ["Wine Stable.app", "Wine Staging.app"]:
        candidate = PORTABLE_DIR / wine_app / "Contents" / "Resources" / "wine" / "lib" / "wine" / "x86_64-unix"
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


def _detect_game_type(exe_path: Optional[str]) -> str:
    if not exe_path:
        return "unknown"
    try:
        p = Path(exe_path)
        name = p.name.lower()
        parent = p.parent

        if "/game/bin/win64/" in str(p).replace("\\", "/").lower():
            return "source2"

        if name.endswith("-win64-shipping.exe") or name.endswith("-shipping.exe"):
            game_root = parent.parent.parent
            for marker_dir in ("Engine/Plugins/Runtime/Nanite",
                               "Content/Paks/Global.utoc"):
                if (game_root / marker_dir).exists():
                    return "ue5"
            return "ue4"

        if p.with_suffix("").name + "_Data" in (
            c.name for c in parent.iterdir() if c.is_dir()
        ) if parent.exists() else False:
            return "unity"

        if parent.exists():
            for sibling in parent.iterdir():
                sn = sibling.name.lower()
                if sn in ("d3d12core.dll", "d3d12sdklayers.dll", "d3d12"):
                    return "dx12"

    except Exception:
        pass
    return "unknown"


def _resolve_auto_backend(exe_path: Optional[str] = None) -> str:
    game_type = _detect_game_type(exe_path)

    if game_type in ("ue5", "ue4", "dx12", "source2"):
        if _wine_d3dmetal_installed():
            return BACKEND_WINE_D3DMETAL
        if _dxmt_available():
            return BACKEND_DXMT
        if _d3dmetal3_available():
            return BACKEND_D3DMETAL3

    if game_type in ("dx11", "unity"):
        if _dxmt_available():
            return BACKEND_DXMT
        if _wine_d3dmetal_installed():
            return BACKEND_WINE_D3DMETAL
        if _d3dmetal3_available():
            return BACKEND_D3DMETAL3
        if _dxvk_available():
            return BACKEND_DXVK

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
       
        backend_ovr = "d3d11,d3d10core,dxgi=b"
        env.pop("DXVK_LOG_PATH", None)
        env.pop("DXVK_LOG_LEVEL", None)
        env.pop("GALLIUM_DRIVER", None)
        env.pop("MESA_GLTHREAD", None)

    elif backend == BACKEND_WINE_D3DMETAL:
        for var in (
            "DXVK_HUD",
            "DXVK_FRAME_RATE",
            "DXVK_LOG_PATH",
            "DXVK_LOG_LEVEL",
            "VKD3D_PROTON_PATH",
            "DXMT_PATH",
            "GALLIUM_DRIVER",
            "MESA_GLTHREAD",
        ):
            env.pop(var, None)

        _d3dmetal_user = _load_d3dmetal_settings()
        for env_key, on in _d3dmetal_user.items():
            env[env_key] = "1" if on else "0"

        backend_ovr = "winemenubuilder.exe=d;mscoree=;mshtml="

    elif backend == BACKEND_D3DMETAL3:

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


    if backend in (BACKEND_GPTK, BACKEND_D3DMETAL3):
        
        env["WINEDLLOVERRIDES"] = backend_ovr
    else:
        mandatory_ovr = "nvapi,nvapi64=;mf,mfplat,mfreadwrite,mfplay=b"
        if backend_ovr:
            env["WINEDLLOVERRIDES"] = f"{mandatory_ovr};{backend_ovr}"
        else:
            env["WINEDLLOVERRIDES"] = mandatory_ovr

    
    dxvk_log_dir = str(LOG_DIR / "dxvk")
    
    env.setdefault("DXVK_LOG_PATH", dxvk_log_dir)
    env.setdefault("DXVK_LOG_LEVEL", "info")
    env["WINEDEBUG"] = "-all"

    return env


def _backend_wine_binary(backend: str, exe: str) -> Optional[str]:
    """Return the wine binary for backends that need a special one, else None."""
    if backend == BACKEND_WINE_D3DMETAL:
       
        wrapper = PORTABLE_DIR / "Wine D3DMetal.app" / "Contents" / "Resources" / "wine-d3dmetal"
        if wrapper.exists():
            log(f"Backend wine_d3dmetal using bundled wrapper: {wrapper}")
            return str(wrapper)
        log("Backend wine_d3dmetal selected but Wine D3DMetal.app not installed in PORTABLE_DIR")
        return None
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
                        prefix: str, exe_full: str, quoted_args: str, log_path: str,
                        extra_env: Optional[Dict[str, str]] = None) -> str:
    """Build the full bash launch command for a given backend."""
    if backend == BACKEND_WINE_D3DMETAL:
        # `wine` here is the Wine D3DMetal.app self-locating wrapper; it
        # already sets DYLD_FALLBACK_LIBRARY_PATH and execs into the
        # in-process Cocoa launcher. WINE_D3DMETAL_NO_STEAM_HACK=1 is
        # exported via _apply_backend_env so HACK 24/25 stay quiet.
        mtl_hud = "MTL_HUD_ENABLED=1 " if extra_env and extra_env.get("MTL_HUD_ENABLED") == "1" else ""
        return (
            f"cd {shlex.quote(exe_dir)} && "
            f"{mtl_hud}arch -x86_64 {shlex.quote(wine)} "
            f"{shlex.quote(exe_name)} {quoted_args} "
            f"> {shlex.quote(log_path)} 2>&1"
        )

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
        # Forward MTL_HUD_ENABLED through the heredoc if set in the parent env.
        metal_hud_line = "export MTL_HUD_ENABLED=1\n" if extra_env and extra_env.get("MTL_HUD_ENABLED") == "1" else ""
        heredoc = f"""\
MNC_WINE={shlex.quote(wine)}
export WINEPREFIX={shlex.quote(prefix)}
export DYLD_FALLBACK_LIBRARY_PATH={shlex.quote(dyld_fallback)}
export ROSETTA_ADVERTISE_AVX=1
export WINEDLLOVERRIDES="{dll_ovr}"
export WINEDEBUG=-all
export SteamAppId=730
export SteamGameId=730
{metal_hud_line}cd {shlex.quote(exe_dir)} || exit 1
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


def _restore_wine_lib_from_dxmt_backup() -> List[str]:
    """Restore wine's stock x86_64-windows PE DLLs that DXMT may have replaced,
    and remove DXMT-only artefacts. Returns the list of restored/removed names.

    Why this matters: DXMT install overwrites wine's lib d3d11/dxgi/d3d10core
    and drops winemetal.dll alongside. If a user then picks D3DMetal3, GPTK,
    DXVK, VKD3D, etc., the game-dir copy of (say) d3d11.dll is correct — but
    wine's loader still resolves *some* dependent DLL out of the wine lib
    path where DXMT's leftover winemetal.dll lives. Result: the game looks
    like it's still running on DXMT. Restore + scrub before any non-DXMT
    launch keeps backends actually isolated."""
    wine_libs = _find_all_wine_libs()
    if not wine_libs:
        return []
    backup_dir = PORTABLE_DIR / ".dxmt-wine-backups"
    touched: List[str] = []
    for win64_lib, _unix_lib in wine_libs:
        if backup_dir.is_dir():
            for dll in ("d3d11.dll", "dxgi.dll", "d3d10core.dll"):
                src = backup_dir / dll
                if src.exists():
                    try:
                        shutil.copy2(str(src), str(win64_lib / dll))
                        touched.append(dll)
                    except Exception as exc:
                        log(f"DXMT restore: failed copying {dll}: {exc}")
        # winemetal.dll is the DXMT bridge — wine itself doesn't ship one, so
        # the safe action is removal. Keeping it leaves a fallback path that
        # the dxgi/d3d11 PE loader can pick up.
        winemetal = win64_lib / "winemetal.dll"
        if winemetal.exists():
            try:
                winemetal.unlink()
                touched.append("winemetal.dll (removed)")
            except Exception as exc:
                log(f"DXMT restore: failed removing winemetal.dll: {exc}")
        if touched:
            log(f"DXMT restore: scrubbed wine lib ({', '.join(touched)}) in {win64_lib}")
    return touched


def _prepare_game_for_backend(backend: str, exe_path: Path, install_dir: str) -> None:
    """
    Copy required DLLs into the game directory before launch.
    This is the critical step the original app does in prepare_game()/patch_selected_game().
    Without it, Wine can't find the native DLLs even with WINEDLLOVERRIDES set.
    """
    game_dir = Path(install_dir) if install_dir else exe_path.parent
    target_dirs = _collect_target_dirs(game_dir, exe_path)

    # Any non-DXMT backend has to undo a prior DXMT install's wine-lib
    # contamination first, otherwise winemetal.dll + DXMT's d3d11/dxgi
    # leak into the wine PE loader's search path even with native DLLs
    # placed correctly in the game dir.
    if backend != BACKEND_DXMT:
        _restore_wine_lib_from_dxmt_backup()

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

    elif backend == BACKEND_WINE_D3DMETAL:
        # Wine D3DMetal ships D3DMetal inside the wine binary (patched
        # ntdll + libd3dshared bridge). No game-dir DLL copies are needed,
        # and any leftover D3D PE DLLs from prior backends (DXVK, GPTK,
        # D3DMetal3, Mesa) MUST be removed so wine's builtin d3d{11,12}
        # are loaded and our D3DMetal path is taken. We don't pollute the
        # game dir, so switching FROM wine_d3dmetal to another backend is
        # a clean handoff — the other backend's prepare puts its own DLLs.
        _unpatch_dxvk(game_dir)
        leftover_dlls = (
            "d3d8.dll", "d3d9.dll",
            "d3d10.dll", "d3d10_1.dll", "d3d10core.dll",
            "d3d11.dll", "d3d11_1.dll",
            "d3d12.dll", "d3d12core.dll",
            "dxgi.dll",
            "atidxx64.dll", "atidxx32.dll",
            "nvapi.dll", "nvapi64.dll",
            "nvngx.dll", "nvngx-on-metalfx.dll",
            "winemetal.dll",  # DXMT bridge — must NOT load alongside D3DMetal
            "vulkan-1.dll",   # avoid Vulkan layer; Steam's vulkandriverquery is overridden via WINEDLLOVERRIDES
            "opengl32.dll", "libgallium_wgl.dll", "libglapi.dll",
            "libEGL.dll", "libGLESv2.dll", "zink_dri.dll",  # Mesa leftovers
        )
        removed_any = False
        for tdir in target_dirs:
            for dll in leftover_dlls:
                p = tdir / dll
                if p.exists():
                    try:
                        p.unlink()
                        removed_any = True
                    except Exception as exc:
                        log(f"wine_d3dmetal cleanup: could not remove {p}: {exc}")
        if removed_any:
            log(f"wine_d3dmetal: scrubbed leftover backend DLLs from {len(target_dirs)} target dir(s) so our builtin D3DMetal-patched DLLs load")

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
        # Sync DXMT DLLs and Unix bridge into every installed Wine bundle so the
        # correct version is loaded regardless of which Wine (Stable/Staging) runs.
        wine_libs = _find_all_wine_libs()
        if wine_libs:
            for win64_lib, unix_lib in wine_libs:
                for dll in ("d3d11.dll", "dxgi.dll", "d3d10core.dll", "winemetal.dll"):
                    src = DEFAULT_DXMT_DIR / dll
                    if src.exists():
                        shutil.copy2(str(src), str(win64_lib / dll))
                for so_src in DEFAULT_DXMT_DIR.glob("*.so"):
                    dst = unix_lib / so_src.name
                    shutil.copy2(str(so_src), str(dst))
                    subprocess.run(
                        ["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(dst)],
                        capture_output=True
                    )
                log(f"DXMT: synced DLLs and .so into {win64_lib.parent.parent}")
        else:
            log("DXMT: could not find any Wine lib dirs — DLLs may be stale")

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

    # Epic Games bottles delegate entirely to legendary
    key = _resolve_key(prefix_str)
    bottle_cfg = _load_bottles().get(key, {})
    if bottle_cfg.get("launcher_type") == "epic":
        return _scan_legendary_games(prefix_str)

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


def cmd_get_steam_description(params: Dict[str, Any]) -> Any:
    appid = str(params.get("appid", "")).strip()
    if not appid:
        raise ValueError("Missing 'appid' parameter")
    description = _fetch_steam_description(appid) or ""
    return {
        "appid": appid,
        "description": description,
    }


def cmd_launch_game(params: Dict[str, Any]) -> Any:
    prefix = params.get("prefix")
    exe = params.get("exe")
    args = params.get("args", "")
    backend = params.get("backend", "auto")
    install_dir = params.get("install_dir", "")
    retina_mode = params.get("retina_mode", False)
    screen_info = params.get("screen_info", "unknown")
    bottle_cfg = _load_bottles().get(_resolve_key(prefix or ""), {})
    metal_hud = params.get("metal_hud")
    if metal_hud is None:
        metal_hud = bottle_cfg.get("metal_hud", False)
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

    if not backend or backend == BACKEND_AUTO:
        backend = _resolve_auto_backend(exe)
        log(f"Auto backend resolved for {Path(exe).name}: {backend} (game_type={_detect_game_type(exe)})")
    else:
        log(f"Resolved graphics backend: {backend}")


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

    # Wine D3DMetal: auto-launch Steam in -silent mode in the SAME prefix
    # under the SAME wine binary so SteamAPI_Init in the game succeeds via
    # shared wineserver. -silent keeps the CEF UI from being rendered
    # (which would hit the libcef+0x59efd15 GPU-init CHECK on current
    # publicbeta CEF). WINE_D3DMETAL_NO_STEAM_HACK=1 keeps wine HACK 24/25
    # quiet so Steam launches with its own default argv. This auto-Steam
    # path ONLY fires here in cmd_launch_game (i.e. only when the user hit
    # Launch in the MacNCheese sheet) — running the wine wrapper from cli
    # never touches Steam.
    if backend == BACKEND_WINE_D3DMETAL:
        # Decide whether this game NEEDS Steam before auto-launching it.
        #
        # Strongest signal: the .exe imports steam_api*.dll / steamworks*.dll
        # in its PE import table — that proves the game links Steamworks SDK.
        # Standalone UE5/Unity builds (e.g. the Lyra demo distributed via
        # Galacticverse) do NOT link Steamworks even though they live under
        # steamapps/common/, so a path-based heuristic would false-positive.
        # We scan the raw .exe bytes for the import-name strings — cheap and
        # robust without needing a PE parser.
        #
        # Secondary signal: steam_api*.dll / steam_appid.txt sitting next to
        # the .exe (or up to 3 dirs up — UE5 .exes are at Binaries/Win64/ but
        # ship the Steam DLL at the game root). Mostly redundant with the PE
        # import scan but covers games that load steam_api*.dll dynamically.
        def _game_needs_steam(exe_p: Path) -> bool:
            # 1. PE imports scan (raw byte search of the .exe).
            try:
                with open(exe_p, "rb") as f:
                    data = f.read(min(exe_p.stat().st_size, 64 * 1024 * 1024))
                for needle in (b"steam_api64.dll", b"steam_api.dll",
                               b"steamworks64.dll", b"steamworks.dll",
                               b"csteamworks.dll"):
                    if needle in data.lower():
                        log(f"wine_d3dmetal: {exe_p.name} imports {needle.decode()} "
                            f"— Steamworks game, will auto-launch Steam")
                        return True
            except Exception as exc:
                log(f"wine_d3dmetal: couldn't scan {exe_p} PE imports: {exc}")

            # 2. File-existence near the .exe (redundant for most games but
            #    covers dynamic-load Steam wrappers).
            check_dirs = [exe_p.parent]
            cur = exe_p.parent
            for _ in range(3):
                if cur.parent == cur: break
                cur = cur.parent
                check_dirs.append(cur)
            steam_dll_names = ("steam_api.dll", "steam_api64.dll",
                               "steamworks64.dll", "csteamworks.dll")
            for d in check_dirs:
                for dll in steam_dll_names:
                    if (d / dll).exists():
                        log(f"wine_d3dmetal: found {dll} in {d} "
                            f"— Steamworks game, will auto-launch Steam")
                        return True
                if (d / "steam_appid.txt").exists():
                    log(f"wine_d3dmetal: found steam_appid.txt in {d} "
                        f"— Steamworks game, will auto-launch Steam")
                    return True
            return False

        steam_exe_winpath = "C:\\Program Files (x86)\\Steam\\steam.exe"
        steam_exe_unix = (
            Path(prefix) / "drive_c" / "Program Files (x86)" / "Steam" / "steam.exe"
        )

        if not _game_needs_steam(exe_path):
            log(f"wine_d3dmetal: {exe_path.name} doesn't link Steamworks SDK "
                f"(no steam_api*.dll / steam_appid.txt found near it) — skipping "
                f"Steam auto-launch. Launching game directly under wine-d3dmetal.")
        elif not steam_exe_unix.exists():
            log(f"wine_d3dmetal: Steam not found in this prefix at {steam_exe_unix} — "
                f"Steam-IPC games (cs2, Source 2, etc.) won't launch. Continuing in case "
                f"the game doesn't actually need Steam.")
        else:
            def _steam_already_alive() -> bool:
                try:
                    ps = subprocess.check_output(["ps", "-axo", "command"], text=True)
                except Exception:
                    return False
                # Match the actual cs2-relevant steam.exe (the wine-process line,
                # not the wrapper or wineboot helper). The wine-process line is
                # literally "C:\\Program Files (x86)\\Steam\\steam.exe".
                return any(
                    "Program Files (x86)\\Steam\\steam.exe" in line
                    for line in ps.splitlines()
                )

            if _steam_already_alive():
                log("wine_d3dmetal: Steam already running in this prefix, proceeding directly to game launch")
            else:
                d3dmetal_wrapper = _backend_wine_binary(BACKEND_WINE_D3DMETAL, "")
                if not d3dmetal_wrapper:
                    log("wine_d3dmetal: Wine D3DMetal.app not installed (Setup → Wine D3DMetal). "
                        "Falling through and letting the game launch under the wrapper anyway.")
                else:
                    steam_env = _wine_env(prefix)
                    steam_env["WINE_D3DMETAL_NO_STEAM_HACK"] = "1"
                    # SHIM=1 keeps steamwebhelper.exe alive (CEF survives the
                    # gs.base / Apple TSD interactions that previously crashed
                    # Chrome_InProcGpuThread). Verified 2026-05-21.
                    steam_env["WINE_D3DMETAL_USE_PTHREAD_SHIM"] = "1"
                    steam_env["WINE_D3DMETAL_USE_PTHREAD_SELF_INTERPOSE"] = "0"
                    steam_env["WINE_D3DMETAL_USE_IOKIT_OBSERVER"] = "0"
                    # KERNEL SAFETY (see _apply_backend_env for full reasoning):
                    # MUST be on or the Mac will kernel-panic after 5-15 min.
                    steam_env["WINE_D3DMETAL_055D_CIRCUIT_BREAKER"] = "1"
                    steam_env["WINE_D3DMETAL_NULL_FP_SKIP"] = "1"
                    # MNC HACK 29: skip wineboot when Steam launches.
                    steam_env["WINE_D3DMETAL_SKIP_WINEBOOT"] = "1"
                    steam_env.setdefault("WINEDEBUG", "-all")
                    steam_env["WINEDLLOVERRIDES"] = "winemenubuilder.exe=d;mscoree=;mshtml="

                    steam_log = str(LOG_DIR / "wine_d3dmetal-steam-silent.log")
                    steam_cmd = (
                        f"cd ~ && arch -x86_64 {shlex.quote(d3dmetal_wrapper)} "
                        f"{shlex.quote(steam_exe_winpath)} -silent "
                        f"> {shlex.quote(steam_log)} 2>&1"
                    )
                    log(f"wine_d3dmetal: auto-launching Steam silent")
                    try:
                        steam_proc = subprocess.Popen(
                            ["bash", "-lc", steam_cmd],
                            env=steam_env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
                        )
                        log(f"wine_d3dmetal: Steam silent launched (pid {steam_proc.pid}), polling for FULL readiness (cap 240s)")
                        # Multi-signal Steam-readiness poll. We can't just check
                        # `steam.exe alive` — that fires too early (Steam still
                        # bootstrapping). cs2's SteamAPI_Init talks to Steam via
                        # the IPC named pipe which only exists AFTER:
                        #   1. steam.exe alive
                        #   2. steamwebhelper.exe alive (CEF init done)
                        #   3. Steam authenticated (connection_log has [Logged On])
                        # We check all three. Without #3, cs2 shows
                        # "FATAL ERROR: Failed to connect with local Steam Client".
                        connection_log_path = (
                            Path(prefix) / "drive_c" / "Program Files (x86)" /
                            "Steam" / "logs" / "connection_log.txt"
                        )
                        steam_launch_ts = time.time()

                        def _steam_fully_ready() -> tuple[bool, str]:
                            """Returns (ready, status_msg). Ready iff Steam is
                            authenticated and IPC pipe is up."""
                            if not _steam_already_alive():
                                return False, "steam.exe not alive yet"
                            try:
                                ps = subprocess.check_output(["ps", "-axo", "command"], text=True)
                            except Exception:
                                return False, "ps failed"
                            swh_alive = any("steamwebhelper.exe" in line for line in ps.splitlines())
                            if not swh_alive:
                                return False, "steamwebhelper.exe not spawned yet"
                            # Check connection_log for [Logged On] AFTER our launch
                            if not connection_log_path.exists():
                                return False, "connection_log.txt absent (Steam still bootstrapping)"
                            try:
                                # Read just the tail (last 50 lines is plenty)
                                with connection_log_path.open("rb") as f:
                                    try:
                                        f.seek(0, 2)
                                        size = f.tell()
                                        f.seek(max(0, size - 16384))
                                    except Exception:
                                        pass
                                    tail = f.read().decode("utf-8", errors="ignore")
                                # Look for [Logged On with a timestamp newer than
                                # our launch (Steam logs use "YYYY-MM-DD HH:MM:SS")
                                # Any "[Logged On," line in the tail means Steam
                                # has authenticated at some point in this session.
                                if "[Logged On," in tail or "[Logged On, " in tail:
                                    return True, "Steam authenticated ([Logged On] in connection_log)"
                                # If still updating/connecting, we see other states
                                if "[Logging On," in tail:
                                    return False, "Steam in [Logging On] (auth in progress)"
                                if "[Connecting," in tail:
                                    return False, "Steam in [Connecting] (still bootstrapping)"
                                if "[Logged Off, 0, 0]" in tail:
                                    return False, "Steam [Logged Off] (cached creds expired? user must sign in)"
                                return False, "connection_log present but no known state"
                            except Exception as exc:
                                return False, f"connection_log read failed: {exc}"

                        ready = False
                        last_status = ""
                        for waited in range(5, 245, 5):
                            time.sleep(5)
                            ok, status = _steam_fully_ready()
                            if status != last_status:
                                log(f"wine_d3dmetal: Steam ready-check t={waited}s: {status}")
                                last_status = status
                            if ok:
                                log(f"wine_d3dmetal: Steam FULLY ready after {waited}s")
                                ready = True
                                # tiny extra wait for steamclient IPC pipe to bind
                                time.sleep(3)
                                break
                            # Special-case: if we see [Logged Off] explicitly,
                            # waiting longer won't help — Steam needs user to
                            # sign in via UI. Bail out early and let cs2 show
                            # its error (or the user manually signs in).
                            if "Logged Off" in status and waited > 60:
                                log(f"wine_d3dmetal: Steam stuck in [Logged Off] at t={waited}s — "
                                    f"cached creds invalid. User must sign into Steam UI manually. "
                                    f"Launching cs2 anyway; it will show 'FATAL ERROR: ...' until "
                                    f"the user signs in.")
                                break
                        if not ready:
                            log("wine_d3dmetal: Steam not fully ready after 240s — launching game anyway "
                                "(SteamAPI_Init will likely fail with FATAL ERROR; sign into Steam UI manually)")
                    except Exception as exc:
                        log(f"wine_d3dmetal: Steam auto-launch failed: {exc} (continuing anyway)")

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

    if backend == BACKEND_WINE_D3DMETAL:
        exe_name_lower = exe_path.name.lower()
        is_ue5 = (
            exe_name_lower.endswith("-win64-shipping.exe")
            or exe_name_lower.endswith("-win64-shippinguncooked.exe")
            or exe_name_lower.endswith("-shipping.exe")
        )
        if is_ue5:
            env["WINE_D3DMETAL_USE_PTHREAD_SHIM"] = "0"
            env["WINE_D3DMETAL_USE_PTHREAD_SELF_INTERPOSE"] = "0"
            env["WINE_D3DMETAL_USE_IOKIT_OBSERVER"] = "0"
            for k in (
                "WINE_D3DMETAL_055D_CIRCUIT_BREAKER",
                "WINE_D3DMETAL_NO_PATCH058",
                "WINE_D3DMETAL_NO_PATCH044V2",
                "WINE_D3DMETAL_NO_PATCH051",
            ):
                env.pop(k, None)
            log(f"wine-d3dmetal: UE5 game detected ({exe_path.name}) — minimal shim-off env (verified Lyra plays)")

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
        backend, wine, exe_dir, exe_name, prefix, exe, quoted_args, log_path,
        extra_env={"MTL_HUD_ENABLED": "1"} if metal_hud else None,
    )

    # Start rpc-bridge before the game using the same wine/env so they share the same wineserver
    if bottle_cfg.get("discord_rpc", True):
        _rpc_bridge_start(wine, env)

    # Heredoc backends (GPTK, D3DMetal3) set env inside zsh — use bash -c.
    # Other backends rely on inherited env — use bash -lc.
    uses_heredoc = backend in (BACKEND_GPTK, BACKEND_D3DMETAL3)
    shell_args = ["bash", "-c", cmd] if uses_heredoc else ["bash", "-lc", cmd]

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

    # Read Metal HUD setting from bottle config so child processes spawned by
    # Steam (e.g. CS2 launched via Steam UI) inherit MTL_HUD_ENABLED.
    metal_hud_line = ""
    if bottle_cfg.get("metal_hud", False):
        metal_hud_line = "export MTL_HUD_ENABLED=1\n"

    # The heredoc sets all env inside zsh so DYLD vars survive SIP.
    heredoc = f"""\
export MNCROOT={shlex.quote(str(mnc_root))}
export MNC_WINE={shlex.quote(wine)}
export WINEPREFIX={shlex.quote(prefix)}
export PATH="$MNCROOT/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export DYLD_FALLBACK_LIBRARY_PATH={shlex.quote(dyld_fallback)}
export ROSETTA_ADVERTISE_AVX=1
{metal_hud_line}unset GTK_PATH GTK_EXE_PREFIX GTK_DATA_PREFIX GDK_PIXBUF_MODULEDIR GDK_PIXBUF_MODULE_FILE GTK_IM_MODULE_FILE XDG_DATA_DIRS
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
        selected_path = Path(custom_path).expanduser()
        if selected_path.name == name:
            bottle_path = selected_path
        else:
            bottle_path = selected_path / name
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

    # For Epic bottles, download legendary CLI in the background if not present
    if launcher_type == "epic":
        threading.Thread(target=_download_legendary_if_needed, daemon=True).start()

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


def cmd_move_bottle(params: Dict[str, Any]) -> Any:
    """Move a prefix directory and update all MacNCheese bottle references."""
    path = params.get("path")
    destination_path = params.get("destination_path")
    destination_parent = params.get("destination_parent")
    if not path:
        raise ValueError("Missing 'path' parameter")
    if not destination_path and not destination_parent:
        raise ValueError("Missing destination path")

    source = Path(path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Prefix not found: {source}")

    if destination_path:
        destination = Path(destination_path).expanduser().resolve()
    else:
        destination_root = Path(destination_parent).expanduser().resolve()
        if destination_root == source:
            return {"path": str(source), "unchanged": True}
        destination = destination_root / source.name

    if destination == source:
        return {"path": str(source), "unchanged": True}
    if str(destination).startswith(str(source) + os.sep):
        raise ValueError("Choose a destination outside the current prefix")
    if destination.exists():
        raise FileExistsError(f"Destination already exists: {destination}")

    old_key = _resolve_key(path)
    new_path = str(destination)
    new_key = _resolve_key(new_path)

    destination.parent.mkdir(parents=True, exist_ok=True)
    log(f"Moving prefix {source} -> {destination}")
    shutil.move(str(source), str(destination))

    try:
        prefixes = _load_prefixes()
        updated_prefixes: List[str] = []
        replaced = False
        for existing in prefixes:
            if _resolve_key(existing) == old_key:
                if new_path not in updated_prefixes:
                    updated_prefixes.append(new_path)
                replaced = True
            elif _resolve_key(existing) != new_key:
                updated_prefixes.append(existing)
        if not replaced and new_path not in updated_prefixes:
            updated_prefixes.append(new_path)
        _save_prefixes(updated_prefixes)

        bottles = _load_bottles()
        config = bottles.pop(old_key, {})
        if config:
            bottles[new_key] = config
        _save_bottles(bottles)
    except Exception:
        log(f"Move config update failed; rolling back {destination} -> {source}")
        try:
            if destination.exists() and not source.exists():
                shutil.move(str(destination), str(source))
        except Exception as rollback_exc:
            log(f"Move rollback failed: {rollback_exc}")
        raise

    return {"path": new_path}


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
    config.setdefault("discord_rpc", True)
    config.setdefault("metal_hud", False)
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


def cmd_open_winecfg(params: Dict[str, Any]) -> Any:
    """Open winecfg for the selected prefix."""
    prefix = params.get("prefix")
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")

    key = _resolve_key(prefix)
    bottle_cfg = _load_bottles().get(key, {})
    wine_pref = str(bottle_cfg.get("wine_binary", "auto") or "auto")
    wine = _find_wine_for_bottle(wine_pref)
    if not wine:
        raise FileNotFoundError("Wine not found")

    env = _wine_env(prefix)
    log(f"open_winecfg: {wine} winecfg for {prefix}")
    proc = subprocess.Popen(
        [wine, "winecfg"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _running_games[proc.pid] = proc
    return {"pid": proc.pid}


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
        {"id": BACKEND_WINE_D3DMETAL, "label": "Wine D3DMetal (CS2/Source 2, auto-launches Steam)", "available": _wine_d3dmetal_installed()},
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

_steam_cache: Dict[str, Any] = {}
_STEAM_CACHE_TTL = 24 * 3600  # 24 hours


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


def _steam_html_to_text(raw: str) -> str:
    """Convert Steam store HTML snippets into readable plain text."""
    if not raw:
        return ""

    text = raw
    text = re.sub(r"(?is)<script.*?>.*?</script>", "", text)
    text = re.sub(r"(?is)<style.*?>.*?</style>", "", text)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*/\s*p\s*>", "\n\n", text)
    text = re.sub(r"(?i)<\s*/\s*div\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*/\s*li\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "• ", text)
    text = re.sub(r"(?i)<\s*/?\s*h[1-6][^>]*>", "\n\n", text)
    text = re.sub(r"(?i)<\s*p[^>]*>", "", text)
    text = re.sub(r"(?i)<\s*div[^>]*>", "", text)
    text = re.sub(r"(?i)<\s*span[^>]*>", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _fetch_steam_description(appid: str) -> Optional[str]:
    """Fetch and cache the Steam store extended description for an app id."""
    appid = str(appid).strip()
    if not appid.isdigit():
        return None

    cache_key = f"steam/{appid}"
    cached = _steam_cache.get(cache_key)
    if cached and (time.time() - cached[0]) < _STEAM_CACHE_TTL:
        return cached[1]

    try:
        url = f"https://store.steampowered.com/api/appdetails?appids={appid}&l=en&cc=us"
        req = urllib.request.Request(url, headers={"User-Agent": "MacNCheese/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read())
        app_data = payload.get(appid, {})
        if not app_data.get("success"):
            _steam_cache[cache_key] = (time.time(), None)
            return None

        data = app_data.get("data", {})
        raw_html = data.get("detailed_description") or data.get("about_the_game") or data.get("short_description") or ""
        description = _steam_html_to_text(raw_html)
        _steam_cache[cache_key] = (time.time(), description or None)
        return description or None
    except Exception as exc:
        log(f"Failed to fetch Steam description for {appid}: {exc}")
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
    has_wine_d3dmetal = _wine_d3dmetal_installed()
    wine_version = _get_wine_version()
    return {
        "has_tools": has_tools,
        "has_wine": has_wine_stable or has_wine_staging or has_wine_d3dmetal,
        "has_wine_stable": has_wine_stable,
        "has_wine_staging": has_wine_staging,
        "has_wine_d3dmetal": has_wine_d3dmetal,
        "has_mesa": _mesa_available(),
        "has_dxvk64": _dxvk_available(),
        "has_dxvk32": has_dxvk32,
        "has_dxmt": _dxmt_available(),
        "has_gptk_dlls": _gptk_dlls_available(),
        "has_d3dmetal3": _d3dmetal3_available(),
        "has_vkd3d": _vkd3d_available(),
        "wine_version": wine_version,
        "has_rpc_bridge": _rpc_bridge_available(),
        "has_wineopenxr": _wineopenxr_available(),
    }


# ---------------------------------------------------------------------------
# Cheese diagnostics
# ---------------------------------------------------------------------------

def _is_apple_silicon() -> bool:
    try:
        uname = os.uname()
        return uname.sysname == "Darwin" and uname.machine == "arm64"
    except Exception:
        return False


def _run_probe(args: List[str], env: Optional[Dict[str, str]] = None, timeout: int = 30) -> tuple[int, str]:
    """Run a short diagnostic probe and return (returncode, combined output)."""
    try:
        result = subprocess.run(
            args,
            env=env,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        if len(output) > 5000:
            output = output[-5000:]
        return result.returncode, output
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or "") + (exc.stderr or "")).strip()
        return 124, f"Timed out after {timeout}s\n{output}".strip()
    except Exception as exc:
        return 127, f"{type(exc).__name__}: {exc}"


def _diag_check(
    check_id: str,
    title: str,
    status: str,
    message: str,
    details: str = "",
    repair_actions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "id": check_id,
        "title": title,
        "status": status,
        "message": message,
        "details": details,
        "repair_actions": repair_actions or [],
    }


def _add_repair(
    repairs: Dict[str, Dict[str, Any]],
    repair_id: str,
    title: str,
    details: str,
    destructive: bool = False,
    recommended: bool = False,
) -> None:
    current = repairs.get(repair_id)
    if current:
        current["recommended"] = bool(current.get("recommended")) or recommended
        current["destructive"] = bool(current.get("destructive")) or destructive
        return
    repairs[repair_id] = {
        "id": repair_id,
        "title": title,
        "details": details,
        "destructive": destructive,
        "recommended": recommended,
    }


def _find_installer_script() -> Optional[Path]:
    candidates = [
        Path(_resources_dir) / "installer.sh",
        Path.home() / "macndcheese" / "installer.sh",
        Path.cwd() / "installer.sh",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _tail_text(path: Path, limit: int = 65536) -> str:
    try:
        with path.open("rb") as f:
            try:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - limit))
            except Exception:
                pass
            return f.read(limit).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _installed_wine_apps() -> List[Dict[str, Any]]:
    apps: List[Dict[str, Any]] = []
    for label, dirname, finder in (
        ("Stable", "Wine Stable.app", _find_wine_stable),
        ("Staging", "Wine Staging.app", _find_wine_staging),
        ("D3DMetal", "Wine D3DMetal.app", _find_wine_d3dmetal),
    ):
        app_dir = PORTABLE_DIR / dirname
        if not app_dir.exists():
            continue
        wine_root = app_dir / "Contents" / "Resources" / "wine"
        bin_dir = wine_root / "bin"
        apps.append({
            "label": label,
            "dirname": dirname,
            "app_dir": app_dir,
            "wine_root": wine_root,
            "wine_bin": finder(),
            "bin_dir": bin_dir,
            "win64_lib": wine_root / "lib" / "wine" / "x86_64-windows",
            "unix_lib": wine_root / "lib" / "wine" / "x86_64-unix",
        })
    return apps


def _file_sizes(path_a: Path, path_b: Path) -> str:
    try:
        size_a = path_a.stat().st_size
    except Exception:
        size_a = -1
    try:
        size_b = path_b.stat().st_size
    except Exception:
        size_b = -1
    return f"wine={size_a} prefix={size_b}"


def _compare_file_content(path_a: Path, path_b: Path) -> bool:
    try:
        if path_a.stat().st_size != path_b.stat().st_size:
            return False
        return filecmp.cmp(str(path_a), str(path_b), shallow=False)
    except Exception:
        return False


def _stable_prefix_dll_sources() -> List[Dict[str, Any]]:
    stable_root = PORTABLE_DIR / "Wine Stable.app" / "Contents" / "Resources" / "wine" / "lib" / "wine"
    return [
        {
            "arch": "x64",
            "wine_dir": stable_root / "x86_64-windows",
            "prefix_dir": "drive_c/windows/system32",
        },
        {
            "arch": "x86",
            "wine_dir": stable_root / "i386-windows",
            "prefix_dir": "drive_c/windows/syswow64",
        },
    ]


def _diagnose_stable_prefix_dlls(prefix: str, repairs: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    prefix_path = Path(prefix).expanduser()
    sections: List[str] = []
    source_missing: List[str] = []
    prefix_missing: List[str] = []
    mismatched: List[str] = []
    checked = 0

    for source in _stable_prefix_dll_sources():
        wine_dir: Path = source["wine_dir"]
        prefix_dir = prefix_path / source["prefix_dir"]
        arch = source["arch"]

        if not wine_dir.is_dir():
            source_missing.append(f"{arch}: {wine_dir}")
            continue
        if not prefix_dir.is_dir():
            prefix_missing.append(f"{arch}: {source['prefix_dir']}/")
            continue

        arch_checked = 0
        arch_missing = 0
        arch_mismatched = 0
        for name in PREFIX_DLL_VERIFY_FILES:
            stable_file = wine_dir / name
            prefix_file = prefix_dir / name
            if not stable_file.exists():
                source_missing.append(f"{arch}: {name}")
                continue
            checked += 1
            arch_checked += 1
            if not prefix_file.exists():
                prefix_missing.append(f"{arch}: {name}")
                arch_missing += 1
                continue
            if not _compare_file_content(stable_file, prefix_file):
                mismatched.append(f"{arch}: {name} ({_file_sizes(stable_file, prefix_file)})")
                arch_mismatched += 1

        sections.append(
            f"{arch}: checked {arch_checked}, missing {arch_missing}, mismatched {arch_mismatched}"
        )

    details: List[str] = []
    details.extend(sections)
    if source_missing:
        details.append("Missing in Wine Stable:")
        details.extend(f"  {item}" for item in source_missing[:16])
        if len(source_missing) > 16:
            details.append(f"  ... {len(source_missing) - 16} more")
    if prefix_missing:
        details.append("Missing in prefix:")
        details.extend(f"  {item}" for item in prefix_missing[:16])
        if len(prefix_missing) > 16:
            details.append(f"  ... {len(prefix_missing) - 16} more")
    if mismatched:
        details.append("Different from Wine Stable:")
        details.extend(f"  {item}" for item in mismatched[:16])
        if len(mismatched) > 16:
            details.append(f"  ... {len(mismatched) - 16} more")

    if not source_missing and checked == 0:
        return _diag_check(
            "prefix_dlls",
            "Prefix DLL verification",
            "info",
            "Wine Stable DLL directories were not found, so the selected prefix could not be compared.",
        )

    if source_missing:
        _add_repair(
            repairs,
            "reinstall_wine_stable",
            "Reinstall Wine Stable",
            "Backs up the current Wine Stable app and installs a fresh copy through installer.sh.",
            destructive=True,
            recommended=True,
        )

    if prefix_missing or mismatched:
        _add_repair(
            repairs,
            "repair_prefix",
            "Repair selected prefix",
            "Runs wineboot -u for the selected bottle/prefix.",
            recommended=True,
        )
        _add_repair(
            repairs,
            "sync_prefix_stable_dlls",
            "Sync prefix DLLs from Wine Stable",
            "Backs up the selected prefix's core runtime DLLs, then copies clean Wine Stable versions into system32/syswow64.",
            destructive=True,
        )

    loader_names = {
        item.split(":", 1)[1].strip().split(" ", 1)[0].lower()
        for item in prefix_missing + mismatched
        if ":" in item
    }
    if loader_names.intersection(PREFIX_LOADER_DLLS):
        _add_repair(
            repairs,
            "backup_recreate_prefix",
            "Back up and recreate prefix",
            "Moves the selected prefix to a timestamped backup and creates a fresh Wine prefix.",
            destructive=True,
        )
        return _diag_check(
            "prefix_dlls",
            "Prefix DLL verification",
            "error",
            "The selected prefix has core loader DLLs that do not match Wine Stable.",
            "\n".join(details),
            ["repair_prefix", "sync_prefix_stable_dlls", "backup_recreate_prefix"],
        )

    if source_missing:
        return _diag_check(
            "prefix_dlls",
            "Prefix DLL verification",
            "error",
            "Wine Stable is missing files needed to verify the selected prefix.",
            "\n".join(details),
            ["reinstall_wine_stable"],
        )

    if prefix_missing or mismatched:
        return _diag_check(
            "prefix_dlls",
            "Prefix DLL verification",
            "warning",
            "Some selected-prefix runtime DLLs do not match Wine Stable.",
            "\n".join(details),
            ["repair_prefix", "sync_prefix_stable_dlls"],
        )

    return _diag_check(
        "prefix_dlls",
        "Prefix DLL verification",
        "ok",
        f"Selected prefix core runtime DLLs match Wine Stable ({checked} files checked).",
        "\n".join(details),
    )


def _diagnose_logs(repairs: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    candidates: List[Path] = []
    if APP_LOG_PATH.exists():
        candidates.append(APP_LOG_PATH)
    try:
        wine_logs = sorted(
            LOG_DIR.glob("*-wine.log"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )
        candidates.extend(wine_logs[:6])
    except Exception:
        pass

    hits: List[str] = []
    patterns = [
        ("could not load kernel32.dll", "Wine could not load kernel32.dll"),
        ("status c0000135", "Wine reported status c0000135"),
        ("_invalid_parameter", "Wine hit _invalid_parameter"),
        ("0xc0000417", "Wine hit exception 0xc0000417"),
        ("couldn't start debugger", "Wine could not start winedbg"),
    ]

    for path in candidates:
        text = _tail_text(path).lower()
        if not text:
            continue
        matched = [label for needle, label in patterns if needle in text]
        if matched:
            hits.append(f"{path.name}: {', '.join(matched)}")

    if not candidates:
        return _diag_check(
            "logs",
            "Recent logs",
            "info",
            "No MacNCheese logs have been created yet.",
        )

    if hits:
        _add_repair(
            repairs,
            "repair_prefix",
            "Repair selected prefix",
            "Runs wineboot -u for the selected bottle/prefix.",
            recommended=True,
        )
        _add_repair(
            repairs,
            "reinstall_wine_stable",
            "Reinstall Wine Stable",
            "Backs up the current Wine Stable app and installs a fresh copy through installer.sh.",
            destructive=True,
        )
        details = "\n".join(hits)
        return _diag_check(
            "logs",
            "Recent logs",
            "warning",
            "Recent logs contain early Wine loader/crash patterns.",
            details,
            ["repair_prefix", "reinstall_wine_stable"],
        )

    return _diag_check(
        "logs",
        "Recent logs",
        "ok",
        f"Checked {len(candidates)} recent log file(s); no known Wine loader patterns found.",
    )


def cmd_diagnose_cheese(params: Dict[str, Any]) -> Any:
    """Scan the MacNCheese runtime for common install, Wine and prefix problems."""
    prefix = str(params.get("prefix") or DEFAULT_PREFIX)
    checks: List[Dict[str, Any]] = []
    repairs: Dict[str, Dict[str, Any]] = {}

    installer = _find_installer_script()
    if installer:
        checks.append(_diag_check(
            "installer",
            "Installer script",
            "ok",
            f"Found installer.sh at {installer}.",
        ))
    else:
        checks.append(_diag_check(
            "installer",
            "Installer script",
            "error",
            "installer.sh was not found, so automated component repairs cannot run.",
        ))

    if _is_apple_silicon():
        rc, output = _run_probe(["/usr/bin/arch", "-x86_64", "/usr/bin/true"], timeout=10)
        if rc == 0:
            checks.append(_diag_check(
                "rosetta",
                "Rosetta 2",
                "ok",
                "Rosetta can run x86_64 commands.",
            ))
        else:
            _add_repair(
                repairs,
                "install_rosetta",
                "Install Rosetta 2",
                "Runs softwareupdate --install-rosetta --agree-to-license.",
                recommended=True,
            )
            checks.append(_diag_check(
                "rosetta",
                "Rosetta 2",
                "error",
                "Rosetta cannot run x86_64 commands.",
                output,
                ["install_rosetta"],
            ))
    else:
        checks.append(_diag_check(
            "rosetta",
            "Rosetta 2",
            "info",
            "This Mac is not reporting Apple Silicon, so Rosetta is not required.",
        ))

    if PORTABLE_DIR.exists():
        checks.append(_diag_check(
            "portable_dir",
            "MacNCheese deps",
            "ok",
            f"Dependency directory exists: {PORTABLE_DIR}.",
        ))
    else:
        _add_repair(
            repairs,
            "quick_setup",
            "Run quick setup",
            "Installs Rosetta, portable tools, Wine Stable, DXMT and Mesa through installer.sh.",
            recommended=True,
        )
        checks.append(_diag_check(
            "portable_dir",
            "MacNCheese deps",
            "warning",
            f"Dependency directory is missing: {PORTABLE_DIR}.",
            repair_actions=["quick_setup"],
        ))

    components = cmd_get_components_status({})
    missing_components: List[str] = []
    if not components.get("has_tools"):
        missing_components.append("tools")
        _add_repair(
            repairs,
            "install_tools",
            "Install portable tools",
            "Installs the portable git/7z/wget tool bundle through installer.sh.",
        )
    if not components.get("has_wine"):
        missing_components.append("Wine")
        _add_repair(
            repairs,
            "install_wine_stable",
            "Install Wine Stable",
            "Installs the MacNCheese Wine Stable bundle through installer.sh.",
            recommended=True,
        )
    if missing_components:
        checks.append(_diag_check(
            "components",
            "Setup components",
            "warning",
            "Missing setup component(s): " + ", ".join(missing_components) + ".",
            repair_actions=["install_tools", "install_wine_stable"],
        ))
    else:
        checks.append(_diag_check(
            "components",
            "Setup components",
            "ok",
            "Required setup components are present.",
            f"Wine version: {components.get('wine_version') or 'unknown'}",
        ))

    wine_apps = _installed_wine_apps()
    if not wine_apps:
        _add_repair(
            repairs,
            "install_wine_stable",
            "Install Wine Stable",
            "Installs the MacNCheese Wine Stable bundle through installer.sh.",
            recommended=True,
        )
        checks.append(_diag_check(
            "wine_selection",
            "Wine selection",
            "error",
            "No portable Wine app is installed.",
            repair_actions=["install_wine_stable"],
        ))
    else:
        labels = [app["label"] for app in wine_apps]
        if len(wine_apps) > 1:
            _add_repair(
                repairs,
                "backup_wine_staging",
                "Keep Stable only",
                "Moves Wine Staging into a diagnostic backup folder so Auto uses only Wine Stable.",
                destructive=True,
                recommended=True,
            )
            checks.append(_diag_check(
                "wine_selection",
                "Wine selection",
                "warning",
                "More than one portable Wine build is installed: " + ", ".join(labels) + ".",
                "The known kernel32.dll reports in the issue thread were often debugged by keeping a single Wine build, preferably Wine Stable.",
                ["backup_wine_staging"],
            ))
        elif labels[0] == "Staging":
            _add_repair(
                repairs,
                "install_wine_stable",
                "Install Wine Stable",
                "Installs the MacNCheese Wine Stable bundle through installer.sh.",
                recommended=True,
            )
            checks.append(_diag_check(
                "wine_selection",
                "Wine selection",
                "warning",
                "Only Wine Staging is installed. Auto can use it, but Wine Stable is the safer default for this app.",
                repair_actions=["install_wine_stable"],
            ))
        else:
            checks.append(_diag_check(
                "wine_selection",
                "Wine selection",
                "ok",
                "Only Wine Stable is installed.",
            ))

    for app in wine_apps:
        missing: List[str] = []
        if not app["wine_bin"] or not Path(str(app["wine_bin"])).exists():
            missing.append("bin/wine or bin/wine64")
        for dll in ("kernel32.dll", "ntdll.dll"):
            if not (app["win64_lib"] / dll).exists():
                missing.append(f"x86_64-windows/{dll}")
        if not app["unix_lib"].exists():
            missing.append("x86_64-unix")

        if missing:
            action = "reinstall_wine_stable" if app["label"] == "Stable" else "backup_wine_staging"
            _add_repair(
                repairs,
                action,
                "Reinstall Wine Stable" if action == "reinstall_wine_stable" else "Keep Stable only",
                "Backs up the broken Wine app and repairs the Wine selection.",
                destructive=True,
                recommended=True,
            )
            checks.append(_diag_check(
                f"wine_integrity_{app['label'].lower()}",
                f"Wine {app['label']} integrity",
                "error",
                f"Wine {app['label']} is missing key runtime file(s).",
                ", ".join(missing),
                [action],
            ))
        else:
            checks.append(_diag_check(
                f"wine_integrity_{app['label'].lower()}",
                f"Wine {app['label']} integrity",
                "ok",
                f"Wine {app['label']} has the expected loader files.",
            ))

    wine = _find_wine()
    if wine:
        version_cmd = [wine, "--version"]
        if _is_apple_silicon():
            version_cmd = ["/usr/bin/arch", "-x86_64", wine, "--version"]
        rc, output = _run_probe(version_cmd, timeout=15)
        if rc == 0 and output:
            status = "ok"
            message = f"Wine responds under x86_64: {output.splitlines()[0]}"
            if "wine-11.0" not in output and "wine-11." not in output:
                status = "info"
                message = f"Wine responds, but it is not a Wine 11.x build: {output.splitlines()[0]}"
            checks.append(_diag_check(
                "wine_version",
                "Wine version probe",
                status,
                message,
            ))
        else:
            _add_repair(
                repairs,
                "reinstall_wine_stable",
                "Reinstall Wine Stable",
                "Backs up the current Wine Stable app and installs a fresh copy through installer.sh.",
                destructive=True,
                recommended=True,
            )
            checks.append(_diag_check(
                "wine_version",
                "Wine version probe",
                "error",
                "Wine did not respond to --version under x86_64.",
                output,
                ["reinstall_wine_stable"],
            ))

    prefix_path = Path(prefix).expanduser()
    if not prefix_path.exists():
        _add_repair(
            repairs,
            "repair_prefix",
            "Repair selected prefix",
            "Creates/updates the selected prefix with wineboot -u.",
            recommended=True,
        )
        checks.append(_diag_check(
            "prefix_files",
            "Selected prefix",
            "warning",
            f"Selected prefix does not exist yet: {prefix_path}.",
            repair_actions=["repair_prefix"],
        ))
    else:
        missing_prefix = []
        for rel in ("drive_c", "system.reg", "user.reg", "drive_c/windows/system32"):
            if not (prefix_path / rel).exists():
                missing_prefix.append(rel)
        if missing_prefix:
            _add_repair(
                repairs,
                "repair_prefix",
                "Repair selected prefix",
                "Runs wineboot -u for the selected bottle/prefix.",
                recommended=True,
            )
            checks.append(_diag_check(
                "prefix_files",
                "Selected prefix",
                "warning",
                "The selected prefix is missing expected Wine files.",
                ", ".join(missing_prefix),
                ["repair_prefix"],
            ))
        else:
            checks.append(_diag_check(
                "prefix_files",
                "Selected prefix",
                "ok",
                "The selected prefix has the expected registry and drive_c structure.",
            ))

        checks.append(_diagnose_stable_prefix_dlls(str(prefix_path), repairs))

        if wine:
            env = _wine_env(str(prefix_path))
            smoke_cmd = [wine, "cmd", "/c", "ver"]
            if _is_apple_silicon():
                smoke_cmd = ["/usr/bin/arch", "-x86_64", wine, "cmd", "/c", "ver"]
            rc, output = _run_probe(smoke_cmd, env=env, timeout=45)
            if rc == 0:
                checks.append(_diag_check(
                    "prefix_smoke",
                    "Prefix smoke test",
                    "ok",
                    "Wine can run a minimal cmd.exe command in the selected prefix.",
                ))
            else:
                smoke_actions = ["repair_prefix", "reinstall_wine_stable"]
                _add_repair(
                    repairs,
                    "repair_prefix",
                    "Repair selected prefix",
                    "Runs wineboot -u for the selected bottle/prefix.",
                    recommended=True,
                )
                _add_repair(
                    repairs,
                    "reinstall_wine_stable",
                    "Reinstall Wine Stable",
                    "Backs up the current Wine Stable app and installs a fresh copy through installer.sh.",
                    destructive=True,
                )
                lowered = output.lower()
                message = "Wine could not run a minimal cmd.exe command in the selected prefix."
                if "kernel32.dll" in lowered or "c0000135" in lowered:
                    message = "Wine hit the kernel32.dll/c0000135 loader failure in this prefix."
                    _add_repair(
                        repairs,
                        "backup_recreate_prefix",
                        "Back up and recreate prefix",
                        "Moves the selected prefix to a timestamped backup and creates a fresh Wine prefix.",
                        destructive=True,
                    )
                    smoke_actions.append("backup_recreate_prefix")
                checks.append(_diag_check(
                    "prefix_smoke",
                    "Prefix smoke test",
                    "error",
                    message,
                    output,
                    smoke_actions,
                ))

    steam_dir = prefix_path / "drive_c" / "Program Files (x86)" / "Steam"
    if steam_dir.exists():
        _add_repair(
            repairs,
            "clear_steam_caches",
            "Clear Steam caches",
            "Deletes Steam html/app/http cache folders inside the selected prefix.",
        )
        checks.append(_diag_check(
            "steam",
            "Steam install",
            "ok",
            "Steam exists in the selected prefix.",
        ))
    else:
        checks.append(_diag_check(
            "steam",
            "Steam install",
            "info",
            "Steam is not installed in the selected prefix yet.",
        ))

    checks.append(_diagnose_logs(repairs))

    errors = sum(1 for check in checks if check["status"] == "error")
    warnings = sum(1 for check in checks if check["status"] == "warning")
    if errors:
        summary = f"Found {errors} error(s) and {warnings} warning(s)."
    elif warnings:
        summary = f"Found {warnings} warning(s), no blocking errors."
    else:
        summary = "No blocking problems found."

    return {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "prefix": str(prefix_path),
        "summary": summary,
        "checks": checks,
        "repairs": list(repairs.values()),
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


def _remove_version_marker(component: str) -> None:
    if not VERSION_MARKER.exists():
        return
    try:
        lines = [
            line for line in VERSION_MARKER.read_text(encoding="utf-8", errors="ignore").splitlines()
            if not line.startswith(f"{component}=")
        ]
        VERSION_MARKER.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    except Exception as exc:
        log(f"Failed to update version marker for {component}: {exc}")


def _diagnostic_backup_path(path: Path) -> Path:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = PORTABLE_DIR / ".diagnose-backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    return backup_root / f"{path.name}.{stamp}"


def _job_append(job: Dict[str, Any], line: str) -> None:
    job["lines"].append(line)


def _run_job_command(
    job: Dict[str, Any],
    args: List[str],
    env: Optional[Dict[str, str]] = None,
    cwd: Optional[str] = None,
) -> int:
    _job_append(job, "$ " + " ".join(shlex.quote(str(arg)) for arg in args))
    proc = subprocess.Popen(
        args,
        env=env,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        _job_append(job, line.rstrip())
    proc.wait()
    _job_append(job, f"exit {proc.returncode}")
    return int(proc.returncode or 0)


def _run_installer_action_for_repair(job: Dict[str, Any], action: str, prefix: str) -> int:
    installer = _find_installer_script()
    if not installer:
        raise FileNotFoundError("installer.sh not found")
    env = {**os.environ, "MNC_SUDOLESS": "1"}
    # Preserve installer.sh positional layout. Most repair actions only need the
    # action and prefix, so the remaining path arguments are intentionally blank.
    args = [
        "/bin/bash",
        str(installer),
        action,
        prefix,
        "", "", "", "", "", "", "", "", "",
    ]
    return _run_job_command(job, args, env=env)


def cmd_run_cheese_repair(params: Dict[str, Any]) -> Any:
    """Run a selected diagnosis repair as an installer-style background job."""
    action = str(params.get("action") or "")
    prefix = str(params.get("prefix") or DEFAULT_PREFIX)
    if not action:
        raise ValueError("Missing 'action' parameter")

    import uuid
    job_id = str(uuid.uuid4())
    job: Dict[str, Any] = {"lines": [], "done": False, "failed": False, "current": ""}
    _install_jobs[job_id] = job

    def _run() -> None:
        job["current"] = action.replace("_", " ").title()
        _job_append(job, f"=== {job['current']} ===")
        try:
            if action == "install_rosetta":
                rc = _run_job_command(
                    job,
                    ["/usr/sbin/softwareupdate", "--install-rosetta", "--agree-to-license"],
                )
                job["failed"] = rc != 0

            elif action == "install_tools":
                job["failed"] = _run_installer_action_for_repair(job, "install_tools", prefix) != 0

            elif action == "install_wine_stable":
                job["failed"] = _run_installer_action_for_repair(job, "install_wine", prefix) != 0

            elif action == "quick_setup":
                job["failed"] = _run_installer_action_for_repair(job, "quick_setup", prefix) != 0

            elif action == "repair_prefix":
                wine = _find_wine()
                if not wine:
                    raise FileNotFoundError("Wine not found")
                Path(prefix).expanduser().mkdir(parents=True, exist_ok=True)
                rc = _run_job_command(job, [wine, "wineboot", "-u"], env=_wine_env(prefix))
                job["failed"] = rc != 0

            elif action == "backup_recreate_prefix":
                wine = _find_wine()
                if not wine:
                    raise FileNotFoundError("Wine not found")
                prefix_path = Path(prefix).expanduser()
                if prefix_path.exists():
                    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                    backup_path = prefix_path.with_name(f"{prefix_path.name}.diagnose-backup-{stamp}")
                    _job_append(job, f"Moving {prefix_path} to {backup_path}")
                    shutil.move(str(prefix_path), str(backup_path))
                prefix_path.mkdir(parents=True, exist_ok=True)
                rc = _run_job_command(job, [wine, "wineboot", "-u"], env=_wine_env(str(prefix_path)))
                job["failed"] = rc != 0

            elif action == "reinstall_wine_stable":
                stable_app = PORTABLE_DIR / "Wine Stable.app"
                if stable_app.exists():
                    backup = _diagnostic_backup_path(stable_app)
                    _job_append(job, f"Moving {stable_app} to {backup}")
                    shutil.move(str(stable_app), str(backup))
                    _remove_version_marker("wine_stable")
                job["failed"] = _run_installer_action_for_repair(job, "install_wine", prefix) != 0

            elif action == "backup_wine_staging":
                staging_app = PORTABLE_DIR / "Wine Staging.app"
                if staging_app.exists():
                    backup = _diagnostic_backup_path(staging_app)
                    _job_append(job, f"Moving {staging_app} to {backup}")
                    shutil.move(str(staging_app), str(backup))
                    _remove_version_marker("wine_staging")
                else:
                    _job_append(job, "Wine Staging.app is already absent.")

            elif action == "sync_prefix_stable_dlls":
                prefix_path = Path(prefix).expanduser()
                if not prefix_path.exists():
                    raise FileNotFoundError(f"Prefix not found: {prefix_path}")
                stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                backup_root = prefix_path / ".macncheese-dll-backups" / stamp
                copied = 0
                backed_up = 0
                missing_sources: List[str] = []

                for source in _stable_prefix_dll_sources():
                    wine_dir: Path = source["wine_dir"]
                    prefix_dir = prefix_path / source["prefix_dir"]
                    if not wine_dir.is_dir():
                        missing_sources.append(str(wine_dir))
                        continue
                    prefix_dir.mkdir(parents=True, exist_ok=True)

                    for name in PREFIX_DLL_VERIFY_FILES:
                        stable_file = wine_dir / name
                        if not stable_file.exists():
                            missing_sources.append(str(stable_file))
                            continue
                        target = prefix_dir / name
                        if target.exists():
                            backup = backup_root / source["prefix_dir"] / name
                            backup.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(str(target), str(backup))
                            backed_up += 1
                        shutil.copy2(str(stable_file), str(target))
                        copied += 1

                if missing_sources:
                    _job_append(job, "Missing Wine Stable source files:")
                    for item in missing_sources[:20]:
                        _job_append(job, f"  {item}")
                    if len(missing_sources) > 20:
                        _job_append(job, f"  ... {len(missing_sources) - 20} more")
                    job["failed"] = True
                _job_append(job, f"Backed up {backed_up} existing prefix file(s) to {backup_root}")
                _job_append(job, f"Copied {copied} Wine Stable runtime file(s) into the selected prefix.")

            elif action == "clear_steam_caches":
                steam_dir = Path(prefix).expanduser() / "drive_c" / "Program Files (x86)" / "Steam"
                targets = [
                    steam_dir / "config" / "htmlcache",
                    steam_dir / "appcache" / "httpcache",
                    steam_dir / "appcache" / "htmlcache",
                ]
                removed = 0
                for target in targets:
                    if target.exists():
                        _job_append(job, f"Removing {target}")
                        shutil.rmtree(str(target), ignore_errors=True)
                        removed += 1
                _job_append(job, f"Removed {removed} Steam cache folder(s).")

            else:
                raise ValueError(f"Unknown repair action: {action}")

        except Exception as exc:
            _job_append(job, f"!!! Repair failed: {exc}")
            job["failed"] = True
        finally:
            job["current"] = ""
            if job.get("failed"):
                _job_append(job, "=== Repair finished with errors ===")
            else:
                _job_append(job, "=== Repair finished successfully ===")
            job["done"] = True

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id}


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
    gptk_dir: str = params.get("gptk_dir", "")

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
                    [installer_path, action, prefix, dxvk_src, dxvk64, dxvk32, mesa, mesa_url, dxmt, "", vkd3d, gptk_dir],
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

_D3DMETAL_DEFAULTS: Dict[str, bool] = {
    "WINE_D3DMETAL_055D_CIRCUIT_BREAKER":     True,
    "WINE_D3DMETAL_USE_PTHREAD_SHIM":         True,
    "WINE_D3DMETAL_USE_PTHREAD_SELF_INTERPOSE": False,
    "WINE_D3DMETAL_USE_IOKIT_OBSERVER":       False,
    "WINE_D3DMETAL_NO_PATCH058":              True,
    "WINE_D3DMETAL_NO_PATCH044V2":            True,
    "WINE_D3DMETAL_NO_PATCH051":              True,
    "WINE_D3DMETAL_FORCE_VISIBLE":            True,
    "WINE_D3DMETAL_NULL_FP_SKIP":             True,
    "DONT_BREAK_ON_ASSERT":                   True,
    "WINE_D3DMETAL_NO_STEAM_HACK":            True,
    "WINE_D3DMETAL_SKIP_WINEBOOT":            True,
}

_D3DMETAL_SETTINGS_PATH = (
    Path.home() / "Library" / "Application Support" / "MacNCheese" / "d3dmetal_settings.json"
)

def _load_d3dmetal_settings() -> Dict[str, bool]:
    out = dict(_D3DMETAL_DEFAULTS)
    try:
        if _D3DMETAL_SETTINGS_PATH.exists():
            data = json.loads(_D3DMETAL_SETTINGS_PATH.read_text())
            for k, v in (data.get("values") or {}).items():
                if k in _D3DMETAL_DEFAULTS:
                    out[k] = bool(v)
    except Exception as exc:
        log(f"Failed to read d3dmetal_settings.json: {exc}")
    return out

def _save_d3dmetal_settings(values: Dict[str, bool]) -> None:
    diff = {k: bool(v) for k, v in values.items()
            if k in _D3DMETAL_DEFAULTS and bool(v) != _D3DMETAL_DEFAULTS[k]}
    try:
        _D3DMETAL_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _D3DMETAL_SETTINGS_PATH.write_text(json.dumps({"values": diff}, indent=2))
    except Exception as exc:
        log(f"Failed to write d3dmetal_settings.json: {exc}")

def cmd_get_d3dmetal_settings(params: Dict[str, Any]) -> Any:
    return _load_d3dmetal_settings()

def cmd_set_d3dmetal_settings(params: Dict[str, Any]) -> Any:
    values = params.get("values") or {}
    if not isinstance(values, dict):
        raise ValueError("values must be a dict")
    _save_d3dmetal_settings(values)
    return {"ok": True}

# ---------------------------------------------------------------------------
# Legendary / Epic Games support
# ---------------------------------------------------------------------------

def _legendary_installed() -> bool:
    return LEGENDARY_BIN.exists()


def _download_legendary_if_needed() -> None:
    global _legendary_installing
    if _legendary_installed() or _legendary_installing:
        return
    _legendary_installing = True
    try:
        log("Downloading Legendary (Epic Games CLI)...")
        # Use GitHub's latest-release redirect — no API call needed, avoids rate limits.
        url = "https://github.com/legendary-gl/legendary/releases/latest/download/legendary_macOS.zip"
        LEGENDARY_DIR.mkdir(parents=True, exist_ok=True)
        tmp_zip = str(LEGENDARY_DIR / "legendary.zip")
        req = urllib.request.Request(url, headers={"User-Agent": "MacNCheese/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            with open(tmp_zip, "wb") as f:
                f.write(resp.read())
        import zipfile
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            # The zip contains a single 'legendary' binary
            names = [n for n in zf.namelist() if not n.startswith("__MACOSX")]
            binary_name = next((n for n in names if "legendary" in n.lower() and not n.endswith("/")), names[0])
            zf.extract(binary_name, str(LEGENDARY_DIR))
            extracted = LEGENDARY_DIR / binary_name
            if extracted != LEGENDARY_BIN:
                extracted.rename(LEGENDARY_BIN)
        Path(tmp_zip).unlink(missing_ok=True)
        os.chmod(str(LEGENDARY_BIN), 0o755)
        subprocess.run(
            ["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(LEGENDARY_BIN)],
            capture_output=True,
        )
        log("Legendary installed successfully")
    except Exception as exc:
        log(f"Error downloading legendary: {exc}")
        try:
            Path(LEGENDARY_DIR / "legendary.tmp").unlink(missing_ok=True)
        except Exception:
            pass
    finally:
        _legendary_installing = False


def _legendary_cover_url(meta: Dict[str, Any]) -> str:
    preferred = ["DieselGameBoxTall", "OfferImageTall", "DieselGameBox", "OfferImageWide"]
    images = meta.get("keyImages", [])
    by_type = {img.get("type", ""): img.get("url", "") for img in images}
    for t in preferred:
        if by_type.get(t):
            return by_type[t]
    for img in images:
        if img.get("url"):
            return img["url"]
    return ""


def _migrate_legendary_installed(prefix: str) -> None:
    """Copy installed entries from the old global config into the per-bottle config.

    Needed so `legendary launch` (which uses LEGENDARY_CONFIG_PATH) can find games
    that were installed before the per-bottle isolation was introduced.
    """
    global_json = Path.home() / ".config" / "legendary" / "installed.json"
    if not global_json.exists():
        return
    prefix_path = str(Path(prefix).expanduser().resolve())
    per_bottle_dir = _legendary_config_dir(prefix)
    per_bottle_json = per_bottle_dir / "installed.json"
    try:
        with open(global_json) as f:
            global_data: Dict[str, Any] = json.load(f)
        if not isinstance(global_data, dict):
            return
        per_bottle_data: Dict[str, Any] = {}
        if per_bottle_json.exists():
            try:
                with open(per_bottle_json) as f:
                    per_bottle_data = json.load(f)
                if not isinstance(per_bottle_data, dict):
                    per_bottle_data = {}
            except Exception:
                per_bottle_data = {}
        added = 0
        for app_name, entry in global_data.items():
            if app_name in per_bottle_data:
                continue
            ip = entry.get("install_path", "")
            if not ip:
                continue
            try:
                ip_resolved = str(Path(ip).expanduser().resolve())
            except Exception:
                ip_resolved = ip
            if ip_resolved.startswith(prefix_path + "/drive_c") or ip_resolved.startswith(prefix_path + "\\drive_c"):
                per_bottle_data[app_name] = entry
                added += 1
        if added:
            per_bottle_dir.mkdir(parents=True, exist_ok=True)
            with open(per_bottle_json, "w") as f:
                json.dump(per_bottle_data, f, indent=2)
            log(f"legendary: migrated {added} pre-existing install(s) into per-bottle config for {prefix}")
    except Exception as exc:
        log(f"legendary: migration failed: {exc}")


_LEGENDARY_LIB_CACHE_FILE = "macncheese_library.json"


def _read_disk_library(prefix: str) -> List[Dict[str, Any]]:
    """Read the owned-games list from the per-bottle disk cache (instant, no network)."""
    path = _legendary_config_dir(prefix) / _LEGENDARY_LIB_CACHE_FILE
    if not path.exists():
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return []


def _write_disk_library(prefix: str, owned: List[Dict[str, Any]]) -> None:
    """Persist the owned-games list to disk so future scans are instant."""
    path = _legendary_config_dir(prefix) / _LEGENDARY_LIB_CACHE_FILE
    try:
        _legendary_config_dir(prefix).mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(owned, f)
    except Exception as exc:
        log(f"legendary: disk library write failed: {exc}")


def _read_installed_here(prefix: str) -> Dict[str, Dict[str, Any]]:
    """Read installed games filtered to this prefix from disk — always instant."""
    prefix_path = str(Path(prefix).expanduser().resolve())
    results: Dict[str, Dict[str, Any]] = {}
    sources = [
        Path.home() / ".config" / "legendary" / "installed.json",
        _legendary_config_dir(prefix) / "installed.json",
    ]
    for path in sources:
        if not path.exists():
            continue
        try:
            with open(path) as f:
                data = json.load(f)
            entries = list(data.values()) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            for entry in entries:
                ip = entry.get("install_path", "")
                if not ip:
                    continue
                try:
                    ip_resolved = str(Path(ip).expanduser().resolve())
                except Exception:
                    ip_resolved = ip
                if ip_resolved.startswith(prefix_path + "/drive_c") or ip_resolved.startswith(prefix_path + "\\drive_c"):
                    results[entry.get("app_name", "")] = entry
        except Exception as exc:
            log(f"legendary: failed to read {path}: {exc}")
    return results


def _build_games_list(prefix: str, owned_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build the game list from owned library + current installed state (all disk reads, no network)."""
    installed_here = _read_installed_here(prefix)
    games: List[Dict[str, Any]] = []
    for g in owned_list:
        app_name = g.get("app_name", "")
        app_title = g.get("app_title", g.get("title", app_name))
        if g.get("is_dlc", False):
            continue
        is_installed = app_name in installed_here
        install_dir = installed_here[app_name].get("install_path", "") if is_installed else ""
        exe = _detect_exe(Path(install_dir), app_name, app_title) if install_dir else None
        cover_url = _legendary_cover_url(g.get("metadata", g))
        games.append({
            "appid": f"epic_{app_name}",
            "name": app_title,
            "exe": exe,
            "install_dir": install_dir,
            "cover_url": cover_url,
            "exe_icon": None,
            "exe_icon_format": "",
            "is_manual": False,
            "is_installed": is_installed,
            "epic_app_name": app_name,
        })
    games.sort(key=lambda g: (0 if g["is_installed"] else 1, g["name"].lower()))
    return games


def _refresh_legendary_cache(prefix: str) -> None:
    """Background thread: serve disk cache instantly, then fetch fresh library from network."""
    try:
        _migrate_legendary_installed(prefix)

        # Phase 1 — instant: build from disk cache and push to memory immediately.
        owned_disk = _read_disk_library(prefix)
        if owned_disk:
            games_fast = _build_games_list(prefix, owned_disk)
            _legendary_games_cache[prefix] = {
                "games": games_fast, "ts": time.time(), "scanning": True,
            }
            log(f"legendary: served {len(games_fast)} games from disk cache for {prefix}")

        # Phase 2 — network: fetch fresh library from Epic (may be slow during downloads).
        lenv = _legendary_env(prefix)
        try:
            r = subprocess.run(
                _legendary_cmd(prefix) + ["list", "--platform", "Windows", "--json"],
                capture_output=True, text=True, timeout=120, env=lenv,
            )
            owned_raw = json.loads(r.stdout) if r.stdout.strip() else []
        except Exception as exc:
            log(f"legendary list failed (network unavailable?): {exc}")
            # Keep the disk-cached result; mark as not scanning.
            entry = _legendary_games_cache.get(prefix, {})
            entry["scanning"] = False
            _legendary_games_cache[prefix] = entry
            return

        if isinstance(owned_raw, dict):
            owned_list = owned_raw.get("games", owned_raw.get("library", []))
        else:
            owned_list = owned_raw

        # Persist fresh library to disk for next cold start.
        _write_disk_library(prefix, owned_list)

        # Build final list with up-to-date installed status.
        games = _build_games_list(prefix, owned_list)
        _legendary_games_cache[prefix] = {"games": games, "ts": time.time(), "scanning": False}
        log(f"legendary: refreshed {len(games)} games from network for {prefix}")

    except Exception as exc:
        log(f"legendary: cache refresh failed: {exc}")
        entry = _legendary_games_cache.get(prefix, {})
        entry["scanning"] = False
        _legendary_games_cache[prefix] = entry


def _scan_legendary_games(prefix: str) -> List[Dict[str, Any]]:
    """Returns games immediately from cache; background-refreshes when stale."""
    if not _legendary_installed():
        return []

    entry = _legendary_games_cache.get(prefix)
    now = time.time()

    if entry:
        if not entry.get("scanning", False):
            age = now - entry.get("ts", 0)
            if age < _LEGENDARY_CACHE_TTL:
                return entry["games"]  # fresh in-memory cache — instant
            # Stale: trigger background refresh but return current data now
            entry["scanning"] = True
            threading.Thread(target=_refresh_legendary_cache, args=(prefix,), daemon=True).start()
        return entry["games"]  # return whatever we have while scanning

    # No in-memory cache — try disk cache for an instant first response.
    owned_disk = _read_disk_library(prefix)
    if owned_disk:
        _migrate_legendary_installed(prefix)
        games_fast = _build_games_list(prefix, owned_disk)
        _legendary_games_cache[prefix] = {"games": games_fast, "ts": 0, "scanning": True}
        threading.Thread(target=_refresh_legendary_cache, args=(prefix,), daemon=True).start()
        return games_fast

    # Truly cold start — nothing cached yet.
    _legendary_games_cache[prefix] = {"games": [], "ts": 0, "scanning": True}
    threading.Thread(target=_refresh_legendary_cache, args=(prefix,), daemon=True).start()
    return []


def cmd_legendary_status(_params: Dict[str, Any]) -> Any:
    return {"installed": _legendary_installed(), "installing": _legendary_installing}


def cmd_legendary_scan_status(params: Dict[str, Any]) -> Any:
    prefix = params.get("prefix", "")
    entry = _legendary_games_cache.get(prefix, {})
    return {"scanning": entry.get("scanning", False), "count": len(entry.get("games", []))}


def cmd_legendary_get_auth_url(_params: Dict[str, Any]) -> Any:
    return {"url": EPIC_AUTH_URL}


def cmd_legendary_check_auth(params: Dict[str, Any]) -> Any:
    prefix = params.get("prefix", "").strip()
    if prefix:
        user_json = _legendary_config_dir(prefix) / "user.json"
    else:
        user_json = Path.home() / ".config" / "legendary" / "user.json"
    if not user_json.exists():
        return {"authenticated": False, "display_name": ""}
    try:
        with open(user_json) as f:
            data = json.load(f)
        name = data.get("displayName") or data.get("display_name") or ""
        if name:
            return {"authenticated": True, "display_name": name}
    except Exception:
        pass
    return {"authenticated": False, "display_name": ""}


def cmd_legendary_auth(params: Dict[str, Any]) -> Any:
    prefix = params.get("prefix", "").strip()
    code = params.get("code", "").strip()
    if not code:
        raise ValueError("Missing 'code' parameter")
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")
    if not _legendary_installed():
        raise RuntimeError("Legendary is not installed")
    try:
        result = subprocess.run(
            _legendary_cmd(prefix) + ["auth", "--code", code],
            capture_output=True, text=True, timeout=120,
            env=_legendary_env(prefix),
        )
        output = result.stdout + result.stderr
        success_markers = ("Successfully logged in", "Logged in as", "login successful")
        if result.returncode == 0 or any(m.lower() in output.lower() for m in success_markers):
            auth = cmd_legendary_check_auth({"prefix": prefix})
            return {"ok": True, "display_name": auth.get("display_name", ""), "error": ""}
        return {"ok": False, "display_name": "", "error": output.strip()[:400]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "display_name": "", "error": "Authentication timed out"}
    except Exception as exc:
        return {"ok": False, "display_name": "", "error": str(exc)}


def cmd_legendary_install_game(params: Dict[str, Any]) -> Any:
    global _legendary_queue_worker_running
    app_name = params.get("app_name", "").strip()
    prefix = params.get("prefix", "").strip()
    if not app_name or not prefix:
        raise ValueError("Missing 'app_name' or 'prefix'")
    if not _legendary_installed():
        raise RuntimeError("Legendary is not installed")
    with _legendary_queue_lock:
        if app_name in _legendary_installs:
            return {"queued": False, "position": 0}
        for i, (qapp, _) in enumerate(_legendary_download_queue):
            if qapp == app_name:
                return {"queued": True, "position": i + 1}
        _legendary_download_queue.append((app_name, prefix))
        position = len(_legendary_download_queue)
        if not _legendary_queue_worker_running:
            _legendary_queue_worker_running = True
            t = threading.Thread(target=_legendary_queue_worker, daemon=True)
            t.start()
    return {"queued": True, "position": position}


def cmd_legendary_install_progress(params: Dict[str, Any]) -> Any:
    app_name = params.get("app_name", "").strip()
    if not app_name:
        raise ValueError("Missing 'app_name'")
    entry = _legendary_installs.get(app_name)
    if not entry:
        return {"progress": 0.0, "done": True, "error": None}
    proc, log_fh, log_path, prefix = entry
    done = proc.poll() is not None
    progress = 0.0
    error = None
    try:
        with open(log_path, "r") as f:
            lines = f.readlines()
        for line in reversed(lines):
            m = re.search(r"Progress:\s*([\d.]+)%", line)
            if m:
                progress = float(m.group(1))
                break
        if done and proc.returncode not in (0, None):
            for line in reversed(lines[-30:]):
                if "error" in line.lower() or "failed" in line.lower():
                    error = line.strip()
                    break
    except Exception:
        pass
    if done:
        try:
            log_fh.close()
        except Exception:
            pass
        _legendary_installs.pop(app_name, None)
        # Invalidate cache so next scan reflects the newly installed game
        _legendary_games_cache.pop(prefix, None)
    return {"progress": progress, "done": done, "error": error}


def cmd_legendary_cancel_install(params: Dict[str, Any]) -> Any:
    app_name = params.get("app_name", "").strip()
    with _legendary_queue_lock:
        for i, (qapp, _) in enumerate(_legendary_download_queue):
            if qapp == app_name:
                _legendary_download_queue.pop(i)
                break
        entry = _legendary_installs.pop(app_name, None)
    if entry:
        proc, log_fh = entry[0], entry[1]
        try:
            proc.terminate()
            log_fh.close()
        except Exception:
            pass
    return {"ok": True}


def cmd_legendary_all_downloads(_params: Dict[str, Any]) -> Any:
    """Return progress of all active and queued legendary downloads."""
    def read_progress(log_path: str) -> float:
        try:
            with open(log_path, "r") as f:
                lines = f.readlines()
            for line in reversed(lines):
                m = re.search(r"Progress:\s*([\d.]+)%", line)
                if m:
                    return float(m.group(1))
        except Exception:
            pass
        return 0.0

    result: Dict[str, Any] = {}
    with _legendary_queue_lock:
        for app_name, entry in _legendary_installs.items():
            _proc, _fh, log_path, prefix = entry
            result[app_name] = {
                "progress": read_progress(log_path),
                "queued": False,
                "queue_position": 0,
                "prefix": prefix,
            }
        for i, (app_name, prefix) in enumerate(_legendary_download_queue):
            result[app_name] = {
                "progress": 0.0,
                "queued": True,
                "queue_position": i + 1,
                "prefix": prefix,
            }
    return result


def cmd_legendary_launch_game(params: Dict[str, Any]) -> Any:
    """Launch an Epic game via legendary, which handles Epic auth token generation."""
    app_name = params.get("app_name", "").strip()
    prefix = params.get("prefix", "").strip()
    backend = params.get("backend", "auto")
    retina_mode = params.get("retina_mode", False)
    metal_hud = params.get("metal_hud", False)
    esync = params.get("esync")
    msync = params.get("msync")
    custom_env_str = params.get("custom_env", "")

    if not app_name or not prefix:
        raise ValueError("Missing 'app_name' or 'prefix'")
    if not _legendary_installed():
        raise RuntimeError("Legendary is not installed")

    prefix_expanded = str(Path(prefix).expanduser().resolve())

    # Find the best Wine binary (backend-aware)
    wine_bin = _backend_wine_binary(backend, "") or _find_wine_for_bottle("auto")
    if not wine_bin:
        raise RuntimeError("No Wine binary found")

    # Build the same environment as a normal Wine launch
    env = _wine_env(prefix_expanded)
    env = _apply_backend_env(env, backend)
    env = _apply_sync_env(env, esync, msync)
    if metal_hud:
        env["MTL_HUD_ENABLED"] = "1"
    for line in (custom_env_str or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()

    # Always apply retina regedit (handles both on and off states)
    threading.Thread(
        target=_apply_retina_regedit, args=(wine_bin, env, retina_mode), daemon=True
    ).start()

    # Inject per-bottle legendary config path into the Wine environment
    env["LEGENDARY_CONFIG_PATH"] = str(_legendary_config_dir(prefix))

    # legendary launch handles Epic auth token generation and passes all required
    # -AUTH_TYPE / -AUTH_PASSWORD / -epicapp / etc. args to Wine automatically.
    cmd = _legendary_cmd(prefix) + [
        "launch", app_name,
        "--wine", wine_bin,
        "--wine-prefix", prefix_expanded,
        "--skip-version-check",
    ]
    log(f"legendary launch: {shlex.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        env=env,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {"pid": proc.pid}


# ---------------------------------------------------------------------------
# Command dispatch table
# ---------------------------------------------------------------------------

COMMANDS: Dict[str, Any] = {
    "list_bottles": cmd_list_bottles,
    "scan_games": cmd_scan_games,
    "get_steam_description": cmd_get_steam_description,
    "launch_game": cmd_launch_game,
    "launch_steam": cmd_launch_steam,
    "create_bottle": cmd_create_bottle,
    "move_bottle": cmd_move_bottle,
    "delete_bottle": cmd_delete_bottle,
    "get_bottle_config": cmd_get_bottle_config,
    "set_bottle_config": cmd_set_bottle_config,
    "kill_wineserver": cmd_kill_wineserver,
    "init_prefix": cmd_init_prefix,
    "clean_prefix": cmd_clean_prefix,
    "open_winecfg": cmd_open_winecfg,
    "run_exe": cmd_run_exe,
    "open_prefix_folder": cmd_open_prefix_folder,
    "get_status": cmd_get_status,
    "add_manual_game": cmd_add_manual_game,
    "detect_exes": cmd_detect_exes,
    "list_backends": cmd_list_backends,
    "get_components_status": cmd_get_components_status,
    "get_update_info": cmd_get_update_info,
    "diagnose_cheese": cmd_diagnose_cheese,
    "run_cheese_repair": cmd_run_cheese_repair,
    "get_running_games": cmd_get_running_games,
    "get_steam_running": cmd_get_steam_running,
    "get_setup_pid": cmd_get_setup_pid,
    "reorder_bottles": cmd_reorder_bottles,
    "launch_launcher": cmd_launch_launcher,
    "get_exe_icon": cmd_get_exe_icon,
    "run_installer": cmd_run_installer,
    "get_install_progress": cmd_get_install_progress,
    "get_d3dmetal_settings": cmd_get_d3dmetal_settings,
    "set_d3dmetal_settings": cmd_set_d3dmetal_settings,
    "legendary_status": cmd_legendary_status,
    "legendary_check_auth": cmd_legendary_check_auth,
    "legendary_auth": cmd_legendary_auth,
    "legendary_install_game": cmd_legendary_install_game,
    "legendary_install_progress": cmd_legendary_install_progress,
    "legendary_cancel_install": cmd_legendary_cancel_install,
    "legendary_all_downloads": cmd_legendary_all_downloads,
    "legendary_get_auth_url": cmd_legendary_get_auth_url,
    "legendary_scan_status": cmd_legendary_scan_status,
    "legendary_launch_game": cmd_legendary_launch_game,
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

    try:
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
    finally:
        _terminate_legendary_installs()


if __name__ == "__main__":
    main()
