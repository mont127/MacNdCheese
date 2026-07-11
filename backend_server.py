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
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
import urllib.request
from concurrent.futures import ThreadPoolExecutor
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
_legendary_paused: Dict[str, str] = {}    # app_name -> prefix (paused downloads)
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
    _legendary_paused.clear()


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
BACKEND_WINE_DEVEL = "wine_devel"  # Wine Staging 11.8 + OpenGL 3.2 macdrv patch (Mewgenics/SDL3)
BACKEND_DXVK = "dxvk"
BACKEND_DXMT = "dxmt"
# Bradar monofunc/dxmt fork (feature/openxr branch): DXMT's Metal D3D11/10 translation
# plus OpenXR passthrough, so D3D11 OpenXR (VR) apps can reach a native macOS
# Bradar OpenXR runtime via the wineopenxr bridge. Kept separate from BACKEND_DXMT so a
# Bradar stock DXMT install and the VR fork can coexist and be selected independently.
BACKEND_DXMT_OPENXR = "dxmt_openxr"
BACKEND_MESA_LLVMPIPE = "mesa:llvmpipe"
BACKEND_MESA_ZINK = "mesa:zink"
BACKEND_MESA_SWR = "mesa:swr"
BACKEND_VKD3D = "vkd3d-proton"
BACKEND_GPTK = "gptk"
BACKEND_GPTK_FULL = "gptk_full"
BACKEND_D3DMETAL3 = "d3dmetal3"


DEFAULT_DXVK_INSTALL = Path.home() / "dxvk-release"
DEFAULT_MESA_DIR = Path.home() / "mesa" / "x64"
DEFAULT_DXMT_DIR = Path.home() / "dxmt"
# Bradar Staging dir for the monofunc/dxmt OpenXR fork (built from source by
# Bradar installer.sh install_dxmt_openxr). Separate from DEFAULT_DXMT_DIR so the two
# Bradar DXMT variants don't clobber each other.
DEFAULT_DXMT_OPENXR_DIR = Path.home() / "dxmt-openxr"
# Host OpenXR runtime (Monado), built x86_64 by installer.sh
# install_monado_runtime. The wineopenxr bridge forwards D3D11 OpenXR to whatever
# runtime this manifest points at; the runtime is dlopen'd INTO the x86_64
# (Rosetta) Wine process, so its dylib MUST be x86_64. We point XR_RUNTIME_JSON
# at this manifest at launch so our x86_64 runtime is used regardless of any
# stale system registration.
MONADO_RUNTIME_MANIFEST = PORTABLE_DIR / "monado" / "active_runtime.json"
# The OpenXR loader's default system-wide runtime registration — inspected only
# to warn when a stale arm64 runtime is registered and ours isn't installed.
SYSTEM_OPENXR_ACTIVE_RUNTIME = Path("/usr/local/share/openxr/1/active_runtime.json")
# Bradar oxrsys (github.com/demonixis/oxrsys) -- an x86_64 macOS OpenXR runtime that STREAMS
# to a Quest/Pico companion app (WiFi/USB) + gets tracking back, so unlike Monado it can
# actually reach a real HMD on macOS. built from its macos-x64 preset (x86_64 dylib, deps are
# only system frameworks). wineopenxr already forwards D3D11-VR -> XR_KHR_metal_enable/MTLDevice
# which is EXACTLY what oxrsys wants, so we just point XR_RUNTIME_JSON at it for VR launches.
OXRSYS_RUNTIME_DIR = PORTABLE_DIR / "oxrsys"
OXRSYS_RUNTIME_MANIFEST = OXRSYS_RUNTIME_DIR / "oxrsys-runtime.json"
OXRSYS_CONFIG_DIR = Path.home() / "Library" / "Application Support" / "OXRSys"
DEFAULT_VKD3D_DIR = Path.home() / "vkd3d-proton"
DEFAULT_GPTK_DIR = Path.home() / "gptk"
GPTK3_ROOT = Path.home() / "gptk3" / "Game Porting Toolkit.app"
D3DMETAL_NATIVE_DIR = Path.home() / "D3DMetalTesting" / "lib" / "external"

# Bradar Unified engine: one wine renders Steam CEF via DXMT and routes games-from-Steam
# Bradar to a chosen backend (MNC_GAME_BACKEND) via the loader. Steam exes are pinned to
# Bradar DXMT by the loader no matter what MNC_GAME_BACKEND is. WINE_UNIFIED_DIR holds the
# bundled build (build64 layout: loader/wine + dlls + server). DEV path is a fallback.
WINE_UNIFIED_DIR = PORTABLE_DIR / "wine-unified"
WINE_UNIFIED_DEV = Path("/Volumes/ASAFE/D3DMETALWINEDEV/wine-11.0-clean/build64")
UNIFIED_GAME_BACKENDS = ("d3dmetal", "dxmt", "dxvk", "vr")

# Bradar The d3d DLL slots the unified loader routes to. As of 2026-07-04 the design
# Bradar inverted -- canonical d3d11/dxgi/d3d10core are now the D3DMetal STUBS so games
# Bradar default to D3DMetal with no per-game files and the loader routes Steam exes
# Bradar EXPLICITLY to the *_dxmt build. d3dmetal backend -> *_d3dm. dxvk -> *_dxvk.
# Bradar dxmt -> *_dxmt. All must physically exist in a prefix system32 or the loader
# has nothing to route to. We bundle the set and stage it into a prefix on launch.
UNIFIED_D3D_DIR = WINE_UNIFIED_DIR / "mnc-d3d"
UNIFIED_D3D_DEV = Path("/Volumes/ASAFE/steam-clean2/drive_c/windows/system32")
UNIFIED_D3D_DLLS = (
    # Bradar canonical d3d11/dxgi/... = D3DMetal stubs. games fall here by default. also
    # Bradar the loader fallback. winemetal.dll backs the DXMT builds.
    "d3d11.dll", "dxgi.dll", "d3d10core.dll", "d3d10.dll", "d3d10_1.dll",
    "d3d12.dll", "d3d12core.dll", "winemetal.dll",
    # Bradar DXMT builds -- the loader routes Steam exes here always. dxmt game backend too.
    "d3d11_dxmt.dll", "dxgi_dxmt.dll", "d3d10core_dxmt.dll",
    # Bradar D3DMetal stubs. d3dmetal game backend -> libd3dshared.
    "d3d11_d3dm.dll", "dxgi_d3dm.dll", "d3d10core_d3dm.dll", "d3d10_d3dm.dll", "d3d12_d3dm.dll",
    # Bradar DXVK. dxvk game backend.
    "d3d11_dxvk.dll", "d3d10core_dxvk.dll", "dxgi_dxvk.dll",
    # Bradar VR = openxr-DXMT (d3d11 w/ OpenXR passthrough) + the wineopenxr bridge PE.
    # vr game backend -> loader openxr column routes d3d11 -> these _openxr slots
    "d3d11_openxr.dll", "d3d10core_openxr.dll", "dxgi_openxr.dll", "wineopenxr.dll",
)

# Bradar Game-side MediaFoundation video bridge. A homebrew-GStreamer winegstreamer variant
# so games decode H264 intro videos while Steam stays off GStreamer. Its PE exports
# wineg_game so the loader pairs it with dlls/wineg_game (its own unix half on the
# Cellar gst core) not Steam packaged-core slot which would dual-load GStreamer and
# abort. We stage the PE into system32 then re-point these wg_* CLSIDs at it.
UNIFIED_MF_BRIDGE = "winegstreamer_game.dll"
UNIFIED_MF_CLSIDS = (
    "{1F1E273D-12C0-4B3A-8E9B-1933C2498AEA}",  # wg_h264_decoder
    "{1F302877-AAAB-40A3-B9E0-9F48DAF35BC8}",  # wg_mp3_sink_factory
    "{272BFBFB-50D0-4078-B600-1E959C301337}",  # wg_avi_splitter
    "{317DF618-5E5A-468A-9F15-D827A9A08162}",  # Generic Decodebin Byte Stream Handler
    "{3F839EC7-5EA6-49E1-80C2-1EA300F8B0E0}",  # wg_wave_parser
    "{5B4D4E54-0620-4CF9-94AE-7823965C28B6}",  # wg_wma_decoder
    "{5D5407D9-C6CA-4770-A7CC-27C0CB8A7627}",  # wg_mpeg4_sink_factory
    "{5ED2E5F6-BF3E-4180-83A4-4847CC5B4EA3}",  # wg_mpeg_video_decoder
    "{62EE5DDB-4F52-48E2-8928-787B0253A0BC}",  # wg_wmv_decoder
    "{6C34DE69-4670-46CD-8CB4-1F2FA1DFFB65}",  # wg_h264_encoder
    "{84CD8E3E-B221-434A-8882-9D6C8DF490E1}",  # wg_mp3_decoder
    "{92F35E78-15A5-486B-888E-575F99651CE2}",  # wg_resampler
    "{A8EDBF98-2442-42C5-85A1-AB05A580DF53}",  # wg_mpeg1_splitter
    "{C9F285F8-4380-4121-971F-49A95316C27B}",  # wg_mpeg_audio_decoder
    "{D527607F-89CB-4E94-9571-BCFE62175613}",  # wg_video_processor
    "{E7889A8A-2083-4844-8370-5EE349B14503}",  # wg_* transform
    "{F47E2DA5-E370-47B7-903A-078DDD45A5CC}",  # wg_* transform
    "{F9D8D64E-A144-47DC-8EE0-F53498372C29}",  # wg_* transform
)

DXVK_DLLS = ("d3d11.dll", "d3d10core.dll")
GPTK_REQUIRED_DLLS = ("atidxx64.dll", "d3d10.dll", "d3d11.dll", "d3d12.dll", "dxgi.dll", "nvapi64.dll", "nvngx.dll")

SKIP_EXE_TOKENS = (
    "crash", "reporter", "setup", "install", "unins",
    "helper", "bootstrap", "diagnostics", "dxwebsetup",
)

# Program Files subdirectories that ship with Wine itself (not user-installed
# applications). Used to filter the Applications list. Compared lowercased.
WINE_DEFAULT_DIRS = {
    "common files", "internet explorer", "windows media player",
    "windows nt", "windows defender", "windows mail",
    "windows photo viewer", "windows sidebar", "windows security",
    "microsoft.net", "msbuild", "reference assemblies",
    "uninstall information", "application verifier", "windows kits",
    "windowspowershell", "windows multimedia platform",
    "windows portable devices", "modifiablewindowsapps",
    "installshield installation information", "desktop",
}

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


# Bradar Centralised log directory (wine logs, dxvk logs, app log)
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



# Guards every JSON state file this backend reads/writes (bottles.json,
# prefixes.json, per-bottle game-config files, ...). Command handling can run
# concurrently (see _scan_executor below), so a read here can now overlap a
# write from a different thread; without this lock a reader could catch a
# file mid-write (path.write_text() truncates before writing, so a torn read
# could see empty/partial JSON instead of the old or new content).
_json_file_lock = threading.Lock()

def _read_json(path: Path, default: Any = None) -> Any:
    try:
        with _json_file_lock:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"Failed to read {path}: {exc}")
    return default

def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _json_file_lock:
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

def _find_wine_devel() -> Optional[str]:
    """Wine Devel = standalone Wine Staging 11.8 with the OpenGL 3.2+ macdrv
    patch, for SDL3/OpenGL games like Mewgenics (installer.sh install_wine_devel
    → $PORTABLE_DIR/Wine Devel.app). Completely separate from Wine D3DMetal."""
    for name in ("wine64", "wine"):
        p = PORTABLE_DIR / "Wine Devel.app" / "Contents" / "Resources" / "wine" / "bin" / name
        if p.exists():
            return str(p)
    return None

def _wineopenxr_available() -> bool:
    """True if the wineopenxr bridge (D3D11 OpenXR → native OpenXR) is
    installed into at least one portable Wine tree."""
    for app in ("Wine D3DMetal.app", "Wine Staging.app", "Wine Stable.app"):
        base = PORTABLE_DIR / app / "Contents" / "Resources" / "wine" / "lib" / "wine"
        if (base / "x86_64-windows" / "wineopenxr.dll").exists() and \
           (base / "x86_64-unix" / "wineopenxr.so").exists():
            return True
    return False

def _ensure_wineopenxr_registered(prefix: str) -> None:
    """Idempotently register the wineopenxr bridge as the active OpenXR runtime
    in `prefix` (delegates to installer.sh register_wineopenxr_prefix, which
    mirrors register_wineopenxr_in_prefix). Used by the dxmt_openxr backend so
    D3D11 OpenXR apps find the native runtime. No-op when the bridge isn't
    installed or the prefix is already registered (so we don't spawn wine on
    every launch)."""
    try:
        if not _wineopenxr_available():
            log("dxmt_openxr: wineopenxr bridge not installed; skipping OpenXR registration")
            return
        prefix_path = Path(prefix)
        manifest_in_prefix = prefix_path / "drive_c" / "openxr" / "wineopenxr64.json"
        sys32_dll = prefix_path / "drive_c" / "windows" / "system32" / "wineopenxr.dll"
        # Already wired up? Skip the (slow) wine reg spawn.
        if manifest_in_prefix.exists() and sys32_dll.exists():
            return
        installer = _find_installer_script()
        if not installer:
            log("dxmt_openxr: installer.sh not found; cannot register wineopenxr")
            return
        subprocess.run(
            [str(installer), "register_wineopenxr_prefix", prefix],
            env={**os.environ, "MNC_SUDOLESS": "1"},
            timeout=300, capture_output=True, text=True,
        )
        log(f"dxmt_openxr: registered wineopenxr as active OpenXR runtime in {prefix}")
    except Exception as exc:
        log(f"dxmt_openxr: wineopenxr registration failed: {exc}")

def _find_wine_for_bottle(wine_binary_pref: str = "auto") -> Optional[str]:
    """Find wine respecting a per-bottle preference ('stable', 'staging', 'auto')."""
    if wine_binary_pref == "stable":
        return _find_wine_stable() or _find_wine()
    if wine_binary_pref == "staging":
        return _find_wine_staging() or _find_wine()
    if wine_binary_pref == "devel":
        return _find_wine_devel() or _find_wine()
    # auto: prefer stable, fall back to staging, then system
    return _find_wine()

def _find_wine() -> Optional[str]:
    ubt = _unified_build_dir()
    candidates = [
        _find_wine_stable(),
        _find_wine_staging(),
        str(ubt / "wine") if ubt else None,
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

    # fast wineboot gate (no-op unless the unified patched wine is used)
    env["MNC_SKIP_WOW64_INSTALL"] = "1"

    # freetype fallback so direct-Popen launches (cmd_run_exe etc.) find libfreetype.
    # paths that wrap wine in `arch` re-export this in-shell since arch strips DYLD_*
    env["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join([
        "/usr/local/lib", "/usr/local/opt/freetype/lib",
        "/usr/local/opt/fontconfig/lib", "/usr/local/opt/gnutls/lib",
        "/usr/local/opt/glib/lib", "/usr/local/opt/gettext/lib",
        "/usr/local/opt/sdl2/lib",
        # bundled freetype/fontconfig fallback for no-Homebrew boxes (see _unified_env / mnc-fonts)
        str(PORTABLE_DIR / "mnc-fonts"),
        "/usr/lib",
    ])

    return env


def _apply_retina_regedit(wine: str, env: dict, retina_mode: bool) -> None:
    """Apply RetinaMode, Resolution and LogPixels via `wine regedit file.reg`."""
    retina_val = "y" if retina_mode else "n"
    dpi_hex = "c0" if retina_mode else "60"  # 192=0xc0, 96=0x60
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
        # Bradar ~2-5 min under our patched wine-d3dmetal because every helper
        # process (services, explorer, plugplay, winedevice, mscoree) goes
        # through the in-process Cocoa launcher init. Subsequent regedit
        # calls in the same prefix return in <1s.
        subprocess.run(
            [wine, "regedit", str(reg_file)],
            env=env, timeout=300,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        log(f"Applied regedit: RetinaMode={retina_val}, Resolution=auto, LogPixels=000000{dpi_hex} ({int(dpi_hex, 16)} DPI)")
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
    # Bradar Mesa was removed; the unified engine covers DXMT/DXVK/D3DMetal.
    return False

def _vkd3d_available() -> bool:
    # DLLs live in x86/ subfolder (same layout as DXVK)
    vkd3d_bin = DEFAULT_VKD3D_DIR / "x86"
    return vkd3d_bin.exists() and (vkd3d_bin / "d3d12.dll").exists()

def _dxmt_available() -> bool:
    return DEFAULT_DXMT_DIR.exists() and (DEFAULT_DXMT_DIR / "d3d11.dll").exists()

def _dxmt_openxr_available() -> bool:
    """True if the monofunc/dxmt OpenXR fork has been built/staged into
    DEFAULT_DXMT_OPENXR_DIR. This is the DXMT translation layer + OpenXR
    passthrough for VR titles; it relies on the wineopenxr bridge
    (_wineopenxr_available) to reach the native macOS OpenXR runtime."""
    return DEFAULT_DXMT_OPENXR_DIR.exists() and (DEFAULT_DXMT_OPENXR_DIR / "d3d11.dll").exists()

def _dylib_is_x86_64(path: Path) -> Optional[bool]:
    """True if the mach-o at `path` includes an x86_64 slice, False if it has
    slices but none are x86_64 (e.g. arm64-only), None if it can't be read. Used
    to catch the classic arm64-OpenXR-runtime vs x86_64-Wine mismatch."""
    try:
        if not path.exists():
            return None
        out = subprocess.run(["/usr/bin/lipo", "-archs", str(path)],
                             capture_output=True, text=True, timeout=10)
        if out.returncode != 0:
            return None
        return "x86_64" in out.stdout.split()
    except Exception:
        return None

def _read_openxr_runtime_dylib(manifest: Path) -> Optional[str]:
    """Return the runtime library_path from an OpenXR active_runtime.json."""
    try:
        data = json.loads(manifest.read_text(errors="ignore"))
        lib = data.get("runtime", {}).get("library_path")
        return str(lib) if lib else None
    except Exception:
        return None

def _monado_runtime_available() -> bool:
    """True if our x86_64 Monado runtime is installed: the manifest exists and the
    dylib it points at exists. Built/registered by installer.sh
    install_monado_runtime."""
    if not MONADO_RUNTIME_MANIFEST.exists():
        return False
    dylib = _read_openxr_runtime_dylib(MONADO_RUNTIME_MANIFEST)
    return bool(dylib and Path(dylib).exists())

def _oxrsys_runtime_available() -> bool:
    """True if the x86_64 oxrsys STREAMING OpenXR runtime is staged -- the one that can
    actually reach a Quest/Pico headset on macOS (via its companion app), unlike Monado
    which loads fine but has no macOS HMD driver so never reaches a headset."""
    dylib = _read_openxr_runtime_dylib(OXRSYS_RUNTIME_MANIFEST)
    return bool(dylib and Path(dylib).exists() and _dylib_is_x86_64(Path(dylib)) is not False)

def _apply_monado_runtime_env(env: Dict[str, str]) -> Dict[str, str]:
    """For VR (dxmt_openxr) launches, force the OpenXR loader to use our x86_64
    Monado runtime via XR_RUNTIME_JSON, so a stale arm64 system runtime can't be
    picked (it would fail to dlopen into the x86_64 Wine process). If ours isn't
    installed, inspect the system registration and log a clear arch-mismatch
    warning instead of leaving the user with cryptic OpenXR-Loader errors."""
    try:
        # Bradar prefer oxrsys -- the STREAMING runtime that reaches a real Quest/Pico headset
        # on macOS. wineopenxr forwards D3D11-VR -> Metal -> oxrsys -> encode -> stream -> HMD.
        # (Monado loads fine but has no macOS HMD driver, so it never reaches a headset.)
        if _oxrsys_runtime_available():
            env["XR_RUNTIME_JSON"] = str(OXRSYS_RUNTIME_MANIFEST)
            log("vr: using oxrsys streaming OpenXR runtime "
                f"{_read_openxr_runtime_dylib(OXRSYS_RUNTIME_MANIFEST)} -- streams to the "
                "Quest/Pico companion app (open it + connect on the headset over WiFi/USB)")
            return env
        if _monado_runtime_available():
            env["XR_RUNTIME_JSON"] = str(MONADO_RUNTIME_MANIFEST)
            # Self-contained prebuilt runtime: point the Vulkan loader at the
            # bundled MoltenVK ICD so VR works with NO Homebrew Vulkan install.
            icd = MONADO_RUNTIME_MANIFEST.parent / "MoltenVK_icd.json"
            if icd.exists():
                env["VK_DRIVER_FILES"] = str(icd)
                env["VK_ICD_FILENAMES"] = str(icd)  # legacy loader name
            dylib = _read_openxr_runtime_dylib(MONADO_RUNTIME_MANIFEST)
            if dylib and _dylib_is_x86_64(Path(dylib)) is False:
                log("dxmt_openxr: WARNING — installed Monado runtime is not x86_64; "
                    "VR will fail to load. Reinstall it from Settings → VR.")
            else:
                log(f"dxmt_openxr: using Monado OpenXR runtime {dylib}")
            return env
        # Ours isn't installed — warn if the registered system runtime is arm64.
        sys_dylib = _read_openxr_runtime_dylib(SYSTEM_OPENXR_ACTIVE_RUNTIME)
        if sys_dylib and _dylib_is_x86_64(Path(sys_dylib)) is False:
            log("dxmt_openxr: WARNING — the registered OpenXR runtime "
                f"({sys_dylib}) is arm64, but Wine runs x86_64, so it CANNOT load "
                "(you'll see 'incompatible architecture' loader errors). Install "
                "the x86_64 Monado runtime from Settings → VR.")
        elif not sys_dylib:
            log("dxmt_openxr: no OpenXR runtime registered — install the Monado "
                "runtime from Settings → VR for VR titles.")
    except Exception as exc:
        log(f"dxmt_openxr: Monado runtime env setup failed: {exc}")
    return env

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

def _wine_d3dmetal_installed() -> bool:
    """True if the no-shim wine-11-d3dmetal app is installed in PORTABLE_DIR.
    Installed by installer.sh install_wine_d3dmetal (unzips wine-d3dmetal-bundle.zip
    -> PORTABLE_DIR/Wine D3DMetal.app). This is the D3DMetal engine the d3dmetal3
    backend launches via `open -n`."""
    return (PORTABLE_DIR / "Wine D3DMetal.app" / "Contents" / "MacOS" / "wine").exists()


def _d3dmetal3_available() -> bool:
    """Check if D3DMetal is available.
    Requires: GPTK DLLs in x86_64-windows/, and D3DMetal native runtime
    (D3DMetal.framework + libd3dshared.dylib) in the native dir.
    """
    # Bradar The unified wine now provides D3DMetal via the loader (MNC_GAME_BACKEND).
    if _unified_available():
        return True
    # Bradar The no-shim wine-11-d3dmetal app is fully self-contained (bundles
    # Bradar libd3dshared.dylib + D3DMetal.framework), so its presence IS availability.
    if _wine_d3dmetal_installed():
        return True
    # Bradar Legacy fallback: GPTK DLLs + external D3DMetal native runtime.
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
        if _dxmt_available():
            return BACKEND_DXMT
        if _d3dmetal3_available():
            return BACKEND_D3DMETAL3

    if game_type in ("dx11", "unity"):
        if _dxmt_available():
            return BACKEND_DXMT
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


# Advanced-debug WINEDEBUG value (launch-sheet toggle). Lets loader/module/
# exception diagnostics through — exactly what the default "-all" suppresses
# (DLL load failures, unresolved imports, crashes). This is what would have made
# the SDL3.dll + UE4 crash diagnoses instant.
WINE_DEBUG_VERBOSE = "+loaddll,+module,+seh"


def _apply_backend_env(env: Dict[str, str], backend: str, debug: bool = False) -> Dict[str, str]:
    """Apply backend-specific environment variables matching MacNCheese.py Backend classes.

    Flow matches original: backend sets its overrides from clean slate,
    then mandatory overrides are prepended (line 5798 in MacNCheese.py).
    """
    env = dict(env)
    env["WINE_MF_MFT_SKIP_VERIFY"] = "1"

    
    backend_ovr = ""

    if backend in (BACKEND_WINE, BACKEND_WINE_DEVEL):
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

    elif backend == BACKEND_DXMT_OPENXR:
        # Bradar Same Metal D3D11/10/DXGI translation as DXMT (the fork's builtin PE
        # DLLs are synced into the wine lib by _prepare_game_for_backend), but
        # wineopenxr is force-loaded so D3D11 OpenXR apps resolve the bridge that
        # forwards to the native macOS OpenXR runtime (registered per-prefix as
        # the Khronos ActiveRuntime by _ensure_wineopenxr_registered).
        backend_ovr = "d3d11,d3d10core,dxgi=b;wineopenxr=n,b"
        env.pop("DXVK_LOG_PATH", None)
        env.pop("DXVK_LOG_LEVEL", None)
        env.pop("GALLIUM_DRIVER", None)
        env.pop("MESA_GLTHREAD", None)


    elif backend == BACKEND_D3DMETAL3:

        mnc_root = PORTABLE_DIR / "Wine Stable.app" / "Contents" / "Resources" / "wine"
        mnc_bin = mnc_root / "bin"

        env["PATH"] = f"{mnc_bin}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        env["ROSETTA_ADVERTISE_AVX"] = "1"
        # SteamAppId is derived per-game (steam_appid.txt) in the launch-command
        # builder, not hardcoded here.

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
        # SteamAppId is derived per-game (steam_appid.txt) in the launch-command
        # builder, not hardcoded here.

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
    env["WINEDEBUG"] = WINE_DEBUG_VERBOSE if debug else "-all"

    return env


def _backend_wine_binary(backend: str, exe: str) -> Optional[str]:
    """Return the wine binary for backends that need a special one, else None."""
    if backend == BACKEND_D3DMETAL3:
        # Bradar D3DMetal = the no-shim wine-11-d3dmetal app, shipped as Wine D3DMetal.app.
        # Launched via `open -n` (see _backend_launch_cmd); return its Cocoa
        # launcher so callers have a non-None wine path.
        app = PORTABLE_DIR / "Wine D3DMetal.app"
        launcher = app / "Contents" / "MacOS" / "wine"
        if launcher.exists():
            log(f"Backend d3dmetal3 using no-shim wine-11-d3dmetal app: {app}")
            return str(launcher)
        log("Backend d3dmetal3 selected but Wine D3DMetal.app (no-shim) not installed in PORTABLE_DIR")
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
    if backend == BACKEND_WINE_DEVEL:
        wine_bin = _find_wine_devel()
        if wine_bin:
            log(f"Backend wine_devel using Wine Devel.app: {wine_bin} ({_get_wine_version(wine_bin)})")
            return wine_bin
        log("Backend wine_devel selected but Wine Devel.app not installed "
            "(Setup -> Wine Devel). Needed for OpenGL/SDL3 games like Mewgenics.")
        return None
    return None


def _derive_steam_appid(exe_dir: str) -> Optional[str]:
    """Find the Steam appID for a game by reading steam_appid.txt next to the exe
    (or in up to 3 parent dirs). SteamStub-wrapped exes (cs2, RE4, ...) fail with
    'Application load error V:0000065432' if Steam can't match the running appID,
    so we must pass the CORRECT one (e.g. RE4 demo = 2231770, cs2 = 730) rather
    than a hardcoded value. Returns the digits, or None if not found."""
    try:
        d = Path(exe_dir)
        for _ in range(4):
            f = d / "steam_appid.txt"
            if f.exists():
                aid = f.read_text(errors="ignore").strip().split()[0]
                if aid.isdigit():
                    return aid
            if d.parent == d:
                break
            d = d.parent
    except Exception as exc:
        log(f"_derive_steam_appid: {exc}")
    return None


def _backend_launch_cmd(backend: str, wine: str, exe_dir: str, exe_name: str,
                        prefix: str, exe_full: str, quoted_args: str, log_path: str,
                        extra_env: Optional[Dict[str, str]] = None,
                        debug: bool = False) -> str:
    # Advanced-debug toggle: verbose WINEDEBUG instead of the default "-all".
    wine_debug = WINE_DEBUG_VERBOSE if debug else "-all"
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

    if backend == BACKEND_D3DMETAL3:
        # Bradar D3DMetal = no-shim wine-11-d3dmetal app, launched by DIRECT-EXEC of its
        # in-process Cocoa launcher (Contents/MacOS/wine), NOT `open -n`.
        #
        # WHY NOT `open -n`: macOS SIP STRIPS DYLD_* env vars across the `open`/
        # LaunchServices boundary (proven: passing --env DYLD_FALLBACK_LIBRARY_PATH
        # to `open` arrives EMPTY inside the process). With DYLD_FALLBACK stripped,
        # Bradar the MF→winegstreamer→GStreamer video path never initializes
        # (wg_init_gstreamer=0, MFCreateSourceReader=0) and RE-Engine titles (RE4)
        # exit ~1.3GB on a black screen. Direct-exec preserves the env we set here
        # (subprocess.Popen passes env= straight through, no SIP boundary), so
        # GStreamer inits and the intro decodes — A/B verified on the same prefix:
        # direct-exec → wg_init_gstreamer=2, MFCreateSourceReader=3, game runs;
        # `open -n` → 0/0, black. The launcher still does its NSApplication main-
        # thread bootstrap when exec'd directly (it's an in-proc Cocoa launcher).
        app = PORTABLE_DIR / "Wine D3DMetal.app"
        launcher = app / "Contents" / "MacOS" / "wine"
        rx = app / "Contents" / "Resources" / "wine"
        libext = rx / "lib" / "external"
        ovr = "d3d12,d3d11,d3d10,d3d10core,dxgi,d3d9=b;mf,mfplat,mfreadwrite,mferror=b"
        dyld = ":".join([
            str(libext),
            "/usr/local/opt/freetype/lib",
            "/usr/local/opt/fontconfig/lib",
            # Bradar Self-contained font fallback: the Wine D3DMetal bundle ships an
            # x86_64 libfreetype.6.dylib (+ libpng) in its lib/ dir. Listing it
            # here means machines WITHOUT Homebrew freetype (the common case —
            # the installer never installs it) still resolve freetype, so
            # Bradar RE-Engine/D3DMetal titles (RE4) don't fail font init / black-screen.
            # Placed after the Homebrew paths so existing setups are unchanged.
            str(rx / "lib"),
            "/usr/local/lib",
            "/usr/lib",
        ])
        env_lines = [
            f"export WINEPREFIX={shlex.quote(prefix)}",
            "export FONTCONFIG_PATH=/usr/local/opt/fontconfig/etc/fonts",
            f"export DYLD_FALLBACK_LIBRARY_PATH={shlex.quote(dyld)}",
            f"export CX_APPLEGPT_LIBD3DSHARED_PATH={shlex.quote(str(libext / 'libd3dshared.dylib'))}",
            f'export WINEDLLOVERRIDES="{ovr}"',
            f"export WINEDEBUG={wine_debug}",
        ]
        if extra_env and extra_env.get("MTL_HUD_ENABLED") == "1":
            env_lines.append("export MTL_HUD_ENABLED=1")
        # Steam appID: prefer an explicit override, else derive from the game's
        # steam_appid.txt (correct per-game value; a wrong/missing one makes
        # SteamStub exes fail with "Application load error V:0000065432").
        appid = (extra_env or {}).get("SteamAppId") or _derive_steam_appid(exe_dir)
        if appid:
            gid = (extra_env or {}).get("SteamGameId", appid)
            env_lines.append(f"export SteamAppId={shlex.quote(appid)}")
            env_lines.append(f"export SteamGameId={shlex.quote(gid)}")
        env_block = "\n".join(env_lines)
        heredoc = (
            f"{env_block}\n"
            f"cd {shlex.quote(exe_dir)} || exit 1\n"
            f"arch -x86_64 {shlex.quote(str(launcher))} "
            f"{shlex.quote(exe_full)} {quoted_args} > {shlex.quote(log_path)} 2>&1\n"
        )
        return f"/bin/bash <<'MNCEOF'\n{heredoc}MNCEOF"

    if backend == BACKEND_GPTK:
        # GPTK uses the heredoc-to-zsh pattern so that
        # DYLD_FALLBACK_LIBRARY_PATH survives macOS SIP stripping.
        mnc_root = PORTABLE_DIR / "Wine Stable.app" / "Contents" / "Resources" / "wine"
        # Bradar D3DMetal native runtime: .dylib and .framework files, not Windows .dlls
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
        # Per-game Steam appID (read from steam_appid.txt), not a hardcoded value.
        gptk_appid = (extra_env or {}).get("SteamAppId") or _derive_steam_appid(exe_dir)
        steam_id_lines = (
            f"export SteamAppId={shlex.quote(gptk_appid)}\nexport SteamGameId={shlex.quote(gptk_appid)}\n"
            if gptk_appid else ""
        )
        heredoc = f"""\
MNC_WINE={shlex.quote(wine)}
export WINEPREFIX={shlex.quote(prefix)}
export DYLD_FALLBACK_LIBRARY_PATH={shlex.quote(dyld_fallback)}
export ROSETTA_ADVERTISE_AVX=1
export WINEDLLOVERRIDES="{dll_ovr}"
export WINEDEBUG={wine_debug}
{steam_id_lines}{metal_hud_line}cd {shlex.quote(exe_dir)} || exit 1
"$MNC_WINE" {shlex.quote('./' + exe_name)} {quoted_args} 2>&1 | tee {shlex.quote(log_path)}
"""
        return f"cd ~ && /usr/bin/arch -x86_64 /bin/zsh <<'MNCEOF'\n{heredoc}MNCEOF"

    debug_prefix = f"WINEDEBUG={WINE_DEBUG_VERBOSE}" if debug else "WINEDEBUG=+loaddll"
    if backend.startswith("mesa:"):
        debug_prefix = (f"WINEDEBUG={WINE_DEBUG_VERBOSE},+wgl,+opengl" if debug
                        else "WINEDEBUG=+loaddll,+wgl,+opengl")

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
        # Bradar winemetal.dll is the DXMT bridge — wine itself doesn't ship one, so
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


def _patch_copy(src: Path, dst: Path, record: List[Tuple[str, bool]]) -> None:
    """Copy src→dst as a per-launch DLL swap, recording it so it can be reverted
    when the game exits. Any pre-existing dst is preserved as <dst>.mncbak (only
    when no backup exists yet, so a crash-leftover backup keeps the true original).
    The record entry is (dst, existed_before) — existed_before tells the revert
    whether to restore the backup or just delete the DLL we added."""
    try:
        existed = dst.exists()
        if existed:
            bak = dst.with_name(dst.name + ".mncbak")
            if not bak.exists():
                shutil.move(str(dst), str(bak))
        shutil.copy2(str(src), str(dst))
        record.append((str(dst), existed))
    except Exception as e:
        log(f"patch_copy failed for {dst}: {e}")


def _revert_patches(record: List[Tuple[str, bool]]) -> None:
    """Undo the per-launch DLL swap recorded by _patch_copy: restore the backed-up
    original for DLLs that existed before, or remove the ones we added."""
    reverted = 0
    for dst_str, existed in record:
        try:
            dst = Path(dst_str)
            bak = dst.with_name(dst.name + ".mncbak")
            if existed:
                if bak.exists():
                    shutil.move(str(bak), str(dst))  # restore original over our copy
                    reverted += 1
            elif dst.exists():
                dst.unlink()                          # we added it — remove
                reverted += 1
        except Exception as e:
            log(f"revert failed for {dst_str}: {e}")
    if reverted:
        log(f"Reverted {reverted} swapped DLL(s) after game exit")


def _revert_after_game_exit(proc: subprocess.Popen, record: List[Tuple[str, bool]],
                            backend: str = "") -> None:
    """Daemon thread: wait for the launched game to exit, then undo its DLL swap
    so nothing is left replaced. Reverts the per-game-dir copies, and for the
    DXMT family also restores the SHARED Wine-Stable lib (DXMT overwrites
    d3d11/dxgi/d3d10core there) — otherwise Steam, which runs on Wine Stable,
    would load DXMT's Direct3D afterwards and fail to launch."""
    try:
        proc.wait()
    except Exception:
        return
    time.sleep(3.0)  # let file handles close before touching the DLLs
    _revert_patches(record)
    if backend in (BACKEND_DXMT, BACKEND_DXMT_OPENXR):
        try:
            restored = _restore_wine_lib_from_dxmt_backup()
            if restored:
                log(f"Restored stock Wine lib after {backend} game exit: {', '.join(restored)}")
        except Exception as exc:
            log(f"wine-lib restore after game exit failed: {exc}")


def _prepare_game_for_backend(backend: str, exe_path: Path, install_dir: str) -> List[Tuple[str, bool]]:
    """
    Copy required DLLs into the game directory before launch.
    This is the critical step the original app does in prepare_game()/patch_selected_game().
    Without it, Wine can't find the native DLLs even with WINEDLLOVERRIDES set.

    Returns a patch record (game-dir DLLs that were swapped in) so the caller can
    revert it when the game exits — see _revert_after_game_exit. Only the game-dir
    copies are tracked; the DXMT/Wine-lib syncs are shared global state and keep
    their own restore logic.
    """
    record: List[Tuple[str, bool]] = []
    game_dir = Path(install_dir) if install_dir else exe_path.parent
    target_dirs = _collect_target_dirs(game_dir, exe_path)

    # Bradar Any non-DXMT backend has to undo a prior DXMT install's wine-lib
    # Bradar contamination first, otherwise winemetal.dll + DXMT's d3d11/dxgi
    # leak into the wine PE loader's search path even with native DLLs
    # Bradar placed correctly in the game dir. The OpenXR fork is DXMT-family
    # (it installs the same winemetal-based DLLs), so it's excluded too.
    if backend not in (BACKEND_DXMT, BACKEND_DXMT_OPENXR):
        _restore_wine_lib_from_dxmt_backup()

    if backend == BACKEND_DXVK:
        dxvk_bin = DEFAULT_DXVK_INSTALL / "bin"
        if not all((dxvk_bin / dll).exists() for dll in DXVK_DLLS):
            log(f"DXVK DLLs not found at {dxvk_bin}, skipping patch")
            return record
        for tdir in target_dirs:
            tdir.mkdir(parents=True, exist_ok=True)
            for dll in DXVK_DLLS:
                _patch_copy(dxvk_bin / dll, tdir / dll, record)
            for dll in DXVK_OPTIONAL_DLLS:
                if (dxvk_bin / dll).exists():
                    _patch_copy(dxvk_bin / dll, tdir / dll, record)
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
            return record

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
                _patch_copy(DEFAULT_MESA_DIR / dll, tdir / dll, record)
            for dll in optional:
                _patch_copy(DEFAULT_MESA_DIR / dll, tdir / dll, record)
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
                    _patch_copy(vkd3d_bin / dll, tdir / dll, record)
                for dll in vkd3d_optional:
                    if (vkd3d_bin / dll).exists():
                        _patch_copy(vkd3d_bin / dll, tdir / dll, record)
                log(f"Copied VKD3D-Proton DLLs -> {tdir}")

    elif backend == BACKEND_DXMT:
        _unpatch_dxvk(game_dir)
        # Bradar Sync DXMT DLLs and Unix bridge into every installed Wine bundle so the
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

    elif backend == BACKEND_DXMT_OPENXR:
        _unpatch_dxvk(game_dir)
        # Bradar Same sync as DXMT, but sourced from the OpenXR fork's staging dir so it
        # Bradar doesn't depend on / clobber a stock DXMT install.
        src_dir = DEFAULT_DXMT_OPENXR_DIR
        wine_libs = _find_all_wine_libs()
        if wine_libs:
            for win64_lib, unix_lib in wine_libs:
                for dll in ("d3d11.dll", "dxgi.dll", "d3d10core.dll", "winemetal.dll"):
                    src = src_dir / dll
                    if src.exists():
                        shutil.copy2(str(src), str(win64_lib / dll))
                for so_src in src_dir.glob("*.so"):
                    dst = unix_lib / so_src.name
                    shutil.copy2(str(so_src), str(dst))
                    subprocess.run(
                        ["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(dst)],
                        capture_output=True
                    )
                log(f"DXMT-OpenXR: synced fork DLLs and .so into {win64_lib.parent.parent}")
        else:
            log("DXMT-OpenXR: could not find any Wine lib dirs — DLLs may be stale")

    elif backend == BACKEND_WINE:
        _unpatch_dxvk(game_dir)
        # Bradar Restore original Wine PE DLLs if DXMT had replaced them.
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
                        _patch_copy(src, tdir / dll, record)
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
                        _patch_copy(src, tdir / dll, record)
                log(f"Copied D3DMetal3 DLLs -> {tdir}")

    elif backend == BACKEND_GPTK_FULL:
        # This backend needs DXVK/VKD3D DLLs removed (unpatch)
        _unpatch_dxvk(game_dir)

    return record


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
# (prefix, exe) -> last launch PID. Guards against the field-reported leak where
# a hung game makes users click Launch repeatedly, stacking Wine instances.
_launched_games: Dict[Tuple[str, str], int] = {}

# ---------------------------------------------------------------------------
# macOS Game Mode control
#
# A game launched through Wine renders into a Cocoa fullscreen window owned by
# the wine process, not by MacNCheese, and that process's main bundle is not a
# games-category .app — so macOS never auto-activates Game Mode for it, even
# though MacNCheese itself opts in. We instead force the *system* Game Mode
# policy on (Apple's `gamepolicyctl game-mode set on`) for the lifetime of a
# launched game and restore "auto" once the last game exits. The binary is
# bundled in the app's Resources (it only links OS frameworks); we fall back to
# Xcode's copy when running from a source checkout.
# ---------------------------------------------------------------------------

_GAMEPOLICYCTL_XCODE = "/Applications/Xcode.app/Contents/Developer/usr/bin/gamepolicyctl"
_GP_UNRESOLVED = object()
_game_mode_lock = threading.Lock()
_game_mode_refcount = 0
_game_mode_path_cache: Any = _GP_UNRESOLVED


def _gamepolicyctl_path() -> Optional[str]:
    """Locate the gamepolicyctl binary: bundled copy first, Xcode fallback."""
    global _game_mode_path_cache
    if _game_mode_path_cache is not _GP_UNRESOLVED:
        return _game_mode_path_cache
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "gamepolicyctl"),
        _GAMEPOLICYCTL_XCODE,
    ]
    found = next(
        (p for p in candidates if os.path.isfile(p) and os.access(p, os.X_OK)), None
    )
    if found is None:
        log("Game Mode: gamepolicyctl not found; Game Mode will not be forced")
    _game_mode_path_cache = found
    return found


def _gamepolicyctl_set(policy: str) -> None:
    """Run `gamepolicyctl game-mode set <policy>` (auto|on|off). No-op if missing."""
    gp = _gamepolicyctl_path()
    if not gp:
        return
    try:
        subprocess.run(
            [gp, "game-mode", "set", policy],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except Exception as exc:
        log(f"gamepolicyctl set {policy} failed: {exc}")


def _game_mode_acquire() -> None:
    """Force Game Mode on for a launched game (reference-counted)."""
    global _game_mode_refcount
    with _game_mode_lock:
        _game_mode_refcount += 1
        first = _game_mode_refcount == 1
    if first:
        log("Game Mode: forcing ON")
        _gamepolicyctl_set("on")


def _game_mode_release() -> None:
    """Release a game's hold; restore automatic policy when none remain."""
    global _game_mode_refcount
    with _game_mode_lock:
        if _game_mode_refcount > 0:
            _game_mode_refcount -= 1
        last = _game_mode_refcount == 0
    if last:
        log("Game Mode: restoring AUTO")
        _gamepolicyctl_set("auto")


def _game_mode_reset() -> None:
    """Hard-reset the policy to automatic (startup belt + crash safety net)."""
    global _game_mode_refcount
    with _game_mode_lock:
        _game_mode_refcount = 0
    _gamepolicyctl_set("auto")


def _register_running_game(
    proc: subprocess.Popen, enable_game_mode: bool = False
) -> None:
    """Track a launched process and, for real games, hold Game Mode until it exits."""
    _running_games[proc.pid] = proc
    if not enable_game_mode:
        return
    _game_mode_acquire()

    def _watch() -> None:
        try:
            proc.wait()
        except Exception:
            pass
        finally:
            _game_mode_release()

    threading.Thread(target=_watch, daemon=True).start()


atexit.register(_game_mode_reset)

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
        # bottles.json is keyed by the path as the user entered it (which may be
        # a symlink), so look up by the resolved key first, then the raw path.
        bottle = bottles.get(key) or bottles.get(raw_path, {})
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
    for raw_key, bottle in bottles.items():
        if not raw_key or not raw_key.strip():
            continue  # skip ghost entries
        # Normalize through the same resolver as the prefixes loop so a bottle
        # reachable via a symlink isn't emitted twice (once resolved, once raw).
        key = _resolve_key(raw_key)
        if key == bottles_base_str:
            continue
        if key in seen:
            continue
        seen.add(key)
        name = bottle.get("name", Path(raw_key).name)
        if not name:
            name = Path(raw_key).name or raw_key
        result.append({
            "path": raw_key,
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
    steam_dir = _steam_dir(prefix)

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


# ---------------------------------------------------------------------------
# Installed Windows applications (Start Menu shortcuts + Program Files)
# ---------------------------------------------------------------------------

def _parse_lnk(path: Path) -> Optional[Dict[str, str]]:
    """Parse a Windows Shell Link (.lnk) file with the stdlib only.

    Returns {"target": <windows path>, "args": <str>} or None. We read the
    LocalBasePath from the LinkInfo structure for the target, and the
    COMMAND_LINE_ARGUMENTS string from StringData for the arguments.
    """
    try:
        data = path.read_bytes()
    except Exception:
        return None
    if len(data) < 0x4C:
        return None
    if struct.unpack_from("<I", data, 0)[0] != 0x4C:  # HeaderSize
        return None

    link_flags = struct.unpack_from("<I", data, 20)[0]
    HAS_LINK_TARGET_IDLIST = 0x00000001
    HAS_LINK_INFO          = 0x00000002
    HAS_NAME               = 0x00000004
    HAS_RELATIVE_PATH      = 0x00000008
    HAS_WORKING_DIR        = 0x00000010
    HAS_ARGUMENTS          = 0x00000020
    HAS_ICON_LOCATION      = 0x00000040
    IS_UNICODE             = 0x00000080

    offset = 0x4C
    if link_flags & HAS_LINK_TARGET_IDLIST:
        if offset + 2 > len(data):
            return None
        offset += 2 + struct.unpack_from("<H", data, offset)[0]

    target: Optional[str] = None
    if link_flags & HAS_LINK_INFO:
        li_start = offset
        if li_start + 20 > len(data):
            return None
        li_size = struct.unpack_from("<I", data, li_start)[0]
        li_flags = struct.unpack_from("<I", data, li_start + 8)[0]
        local_base_path_offset = struct.unpack_from("<I", data, li_start + 16)[0]
        VOLUMEID_AND_LOCAL_BASE_PATH = 0x00000001
        if (li_flags & VOLUMEID_AND_LOCAL_BASE_PATH) and local_base_path_offset:
            base_off = li_start + local_base_path_offset
            end = data.find(b"\x00", base_off)
            if end != -1:
                target = data[base_off:end].decode("cp1252", errors="replace")
        offset = li_start + li_size  # advance past LinkInfo to StringData

    args = ""

    def _read_string(off: int) -> Tuple[Optional[str], int]:
        if off + 2 > len(data):
            return None, off
        count = struct.unpack_from("<H", data, off)[0]
        off += 2
        if link_flags & IS_UNICODE:
            nbytes = count * 2
            text = data[off:off + nbytes].decode("utf-16-le", errors="replace")
        else:
            nbytes = count
            text = data[off:off + nbytes].decode("cp1252", errors="replace")
        return text, off + nbytes

    for flag in (HAS_NAME, HAS_RELATIVE_PATH, HAS_WORKING_DIR, HAS_ARGUMENTS, HAS_ICON_LOCATION):
        if link_flags & flag:
            text, offset = _read_string(offset)
            if text is None:
                break
            if flag == HAS_ARGUMENTS:
                # Drop NUL padding / non-printable noise from the raw string.
                args = "".join(ch for ch in text if ch.isprintable()).strip()

    if not target:
        return None
    return {"target": target, "args": args}


def _win_path_to_host(prefix: Path, win_path: str) -> Optional[Path]:
    """Map a Windows path (C:\\Foo\\bar.exe) to its host path inside the prefix."""
    if not win_path or len(win_path) < 3 or win_path[1] != ":":
        return None
    if win_path[0].lower() != "c":  # we only manage the C: drive
        return None
    rest = win_path[3:].replace("\\", "/")
    return prefix / "drive_c" / rest


def cmd_scan_apps(params: Dict[str, Any]) -> Any:
    """Return installed Windows applications in a bottle.

    Primary source is Start Menu .lnk shortcuts; if a bottle has none, we fall
    back to scanning each Program Files subfolder for its main executable.
    Steam/Epic games and Windows system tools are excluded (games are already
    shown by scan_games).
    """
    prefix_str = params.get("prefix")
    if not prefix_str:
        raise ValueError("Missing 'prefix' parameter")
    prefix = Path(prefix_str).expanduser().resolve()
    drive_c = prefix / "drive_c"
    if not drive_c.exists():
        return []

    excluded_roots = [
        (drive_c / "windows"),
        (drive_c / "Program Files (x86)" / "Steam"),
        (drive_c / "Program Files" / "Epic Games"),
    ]

    drive_c_resolved = drive_c.resolve()

    def _excluded(exe_path: Path) -> bool:
        try:
            rp = exe_path.resolve()
        except Exception:
            rp = exe_path
        for base in excluded_roots:
            try:
                rp.relative_to(base.resolve())
                return True
            except Exception:
                continue
        # Skip Wine's own bundled Program Files programs.
        try:
            parts = rp.relative_to(drive_c_resolved).parts
        except Exception:
            return False
        if len(parts) >= 2 and parts[0].lower() in ("program files", "program files (x86)"):
            return parts[1].lower() in WINE_DEFAULT_DIRS
        return False

    found: Dict[str, Dict[str, str]] = {}  # keyed by resolved exe path

    # 1. Start Menu .lnk shortcuts (system-wide + per user)
    start_menu_roots = [
        drive_c / "ProgramData" / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    ]
    users_dir = drive_c / "users"
    if users_dir.exists():
        for user in users_dir.iterdir():
            sm = user / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            if sm.exists():
                start_menu_roots.append(sm)

    for sm_root in start_menu_roots:
        if not sm_root.exists():
            continue
        try:
            lnks = list(sm_root.glob("**/*.lnk"))
        except Exception:
            lnks = []
        for lnk in lnks:
            info = _parse_lnk(lnk)
            if not info or not info["target"].lower().endswith(".exe"):
                continue
            host = _win_path_to_host(prefix, info["target"])
            if not host or not host.exists():
                continue
            if _is_probably_not_game(host) or _excluded(host):
                continue
            key = str(host)
            if key not in found:
                found[key] = {"name": lnk.stem, "exe": key, "args": info.get("args", "")}

    # 2. Fallback: one app per Program Files subfolder when no shortcuts exist
    if not found:
        for pf in (drive_c / "Program Files", drive_c / "Program Files (x86)"):
            if not pf.exists():
                continue
            try:
                children = [c for c in pf.iterdir() if c.is_dir()]
            except Exception:
                children = []
            for child in children:
                if child.name.lower() in WINE_DEFAULT_DIRS:
                    continue
                exe = _detect_exe(child, child.name, child.name)
                if not exe:
                    continue
                exe_path = Path(exe)
                if _excluded(exe_path):
                    continue
                key = str(exe_path)
                if key not in found:
                    found[key] = {"name": child.name, "exe": key, "args": ""}

    apps: List[Dict[str, Any]] = []
    for entry in found.values():
        icon_b64 = None
        try:
            ico_bytes = _pe_extract_ico(entry["exe"])
            if ico_bytes:
                icon_b64 = base64.b64encode(ico_bytes).decode()
        except Exception as exc:
            log(f"scan_apps: failed to extract icon for {entry['exe']}: {exc}")
        apps.append({
            "name": entry["name"],
            "exe": entry["exe"],
            "args": entry.get("args", ""),
            "icon": icon_b64,
            "icon_format": "ico" if icon_b64 else "",
        })
    # Bradar merge the manually-added apps (the "Add Application" button -> cmd_add_manual_app)
    # so a user can point at ANY .exe n it sticks in the Applications section, deduped by exe path
    try:
        _mb = _load_bottles().get(_resolve_key(prefix_str), {})
        _seen = {a.get("exe") for a in apps}
        for m in _mb.get("manual_apps", []):
            mexe = m.get("exe")
            if mexe and mexe not in _seen and Path(mexe).exists():
                apps.append({"name": m.get("name") or Path(mexe).stem, "exe": mexe,
                             "args": m.get("args", ""), "icon": "", "icon_format": ""})
    except Exception as _exc:
        log(f"scan_apps: manual_apps merge failed: {_exc}")
    apps.sort(key=lambda a: a["name"].lower())
    return apps


def cmd_get_steam_description(params: Dict[str, Any]) -> Any:
    appid = str(params.get("appid", "")).strip()
    if not appid:
        raise ValueError("Missing 'appid' parameter")
    description = _fetch_steam_description(appid) or ""
    return {
        "appid": appid,
        "description": description,
    }


def cmd_get_steam_media(params: Dict[str, Any]) -> Any:
    """Description + showcase media (screenshots, header) for a Steam app id, from
    one cached appdetails fetch. Powers the game detail page's gallery."""
    appid = str(params.get("appid", "")).strip()
    if not appid:
        raise ValueError("Missing 'appid' parameter")
    data = _fetch_steam_appdetails(appid) or {}
    shots = data.get("screenshots") or []
    screenshots = [s.get("path_full") for s in shots if isinstance(s, dict) and s.get("path_full")]
    thumbnails = [s.get("path_thumbnail") for s in shots if isinstance(s, dict) and s.get("path_thumbnail")]
    raw_html = (data.get("detailed_description")
                or data.get("about_the_game")
                or data.get("short_description") or "")
    return {
        "appid": appid,
        "description": _steam_html_to_text(raw_html) or "",
        "short_description": _steam_html_to_text(data.get("short_description") or "") or "",
        "header_image": data.get("header_image") or "",
        "screenshots": screenshots,
        "thumbnails": thumbnails,
    }



DISCORD_CLIENT_ID = os.environ.get("MACNCHEESE_DISCORD_APP_ID", "1508076871009697902").strip()

_discord_lock = threading.Lock()
_discord_sock = None  

_DISCORD_STEAM_EXES = {
    "steam.exe", "steamwebhelper.exe", "steamerrorreporter.exe",
    "steamerrorreporter64.exe", "steamservice.exe", "gameoverlayui.exe",
    "steamtours.exe",
}


def _discord_ipc_candidates() -> List[str]:
    """Probe the standard Discord IPC socket locations (macOS/Linux)."""
    bases: List[str] = []
    for var in ("XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP"):
        v = os.environ.get(var)
        if v:
            bases.append(v.rstrip("/"))
    bases.append("/tmp")
    seen = set()
    out: List[str] = []
    for b in bases:
        if b and b not in seen:
            seen.add(b)
            for i in range(10):
                out.append(os.path.join(b, f"discord-ipc-{i}"))
    return out


def _discord_send(sock, op: int, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    sock.sendall(struct.pack("<II", op, len(data)) + data)


def _discord_recv(sock):
    header = sock.recv(8)
    if len(header) < 8:
        return None, None
    op, length = struct.unpack("<II", header)
    buf = b""
    while len(buf) < length:
        chunk = sock.recv(length - len(buf))
        if not chunk:
            break
        buf += chunk
    try:
        return op, json.loads(buf.decode("utf-8"))
    except Exception:
        return op, None


def _discord_drop() -> None:
    # Caller must hold _discord_lock.
    global _discord_sock
    if _discord_sock is not None:
        try:
            _discord_sock.close()
        except Exception:
            pass
    _discord_sock = None


def _discord_connect():
   
    global _discord_sock
    if not DISCORD_CLIENT_ID:
        return None
    if _discord_sock is not None:
        return _discord_sock
    for path in _discord_ipc_candidates():
        if not os.path.exists(path):
            continue
        s = None
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect(path)
            _discord_send(s, 0, {"v": 1, "client_id": DISCORD_CLIENT_ID})
            _discord_recv(s)  # READY (best-effort)
            _discord_sock = s
            return s
        except Exception:
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass
            continue
    return None


def discord_set_game(game_name: str) -> None:
    """Set 'Playing MacNCheese' + game presence. Safe no-op on any failure."""
    if not DISCORD_CLIENT_ID or not game_name:
        return
    with _discord_lock:
        sock = _discord_connect()
        if sock is None:
            return
        payload = {
            "cmd": "SET_ACTIVITY",
            "nonce": str(uuid.uuid4()),
            "args": {
                "pid": os.getpid(),
                "activity": {
                    "details": game_name,
                    "state": "via MacNCheese",
                    "timestamps": {"start": int(time.time())},
                    "assets": {
                        "large_image": "macncheese",
                        "large_text": "MacNCheese",
                    },
                },
            },
        }
        try:
            _discord_send(sock, 1, payload)
            _discord_recv(sock)
            log(f"discord: presence set -> {game_name}")
        except Exception:
            _discord_drop()


def discord_clear() -> None:
    """Clear MacNCheese presence. Safe no-op on any failure."""
    if not DISCORD_CLIENT_ID:
        return
    with _discord_lock:
        if _discord_sock is None:
            return
        payload = {
            "cmd": "SET_ACTIVITY",
            "nonce": str(uuid.uuid4()),
            "args": {"pid": os.getpid(), "activity": None},
        }
        try:
            _discord_send(_discord_sock, 1, payload)
            _discord_recv(_discord_sock)
            log("discord: presence cleared")
        except Exception:
            _discord_drop()


def _discord_presence_for_launch(proc, exe, game_name: str) -> None:
    """Report 'Playing MacNCheese' + game for a launched process and clear it
    when the process exits. Skips Steam-family targets."""
    if not DISCORD_CLIENT_ID:
        return
    base = os.path.basename(str(exe or "")).lower()
    if base in _DISCORD_STEAM_EXES:
        return
    name = (game_name or "").strip()
    if (not name or name.lower() == "steam") and base:
        name = os.path.splitext(base)[0]
    if not name or name.lower() == "steam":
        return

    def _watch():
        discord_set_game(name)
        try:
            proc.wait()
        except Exception:
            pass
        discord_clear()

    threading.Thread(target=_watch, daemon=True).start()


def _ensure_steam_sdl_resolvable(prefix: str) -> None:
    """Steam's newer client loads SDL3.dll (older titles: SDL2.dll) by BARE name
    from steamclient during SteamAPI_Init. But the game process only searches its
    exe dir + system32 + cwd — NOT the Steam root (drive_c/Program Files (x86)/
    Steam) where Steam keeps SDL3.dll. So the load returns NULL and Steamworks
    asserts 'tier1\\interface.h ... Failed to load "SDL3.dll"' and the game exits
    (confirmed: the DLL loads fine, it just isn't on the search path). Copy Steam's
    SDL3/SDL2 into the prefix's system32 so the bare-name load resolves for every
    game. Idempotent; a game shipping its own SDL in its exe dir still wins."""
    try:
        steam_root = _steam_dir(prefix)
        sys32 = Path(prefix) / "drive_c" / "windows" / "system32"
        if not sys32.is_dir():
            return
        for dll in ("SDL3.dll", "SDL2.dll"):
            src = steam_root / dll
            if not src.exists():
                continue
            dst = sys32 / dll
            if (not dst.exists()
                    or src.stat().st_size != dst.stat().st_size
                    or src.stat().st_mtime > dst.stat().st_mtime):
                shutil.copy2(str(src), str(dst))
                log(f"steam: synced {dll} -> system32 (bare-name LoadLibrary fix)")
    except Exception as exc:
        log(f"steam SDL sync failed: {exc}")


def _unified_build_dir() -> Optional[Path]:
    """Locate the bundled unified wine build (build64 layout)."""
    for d in (WINE_UNIFIED_DIR, WINE_UNIFIED_DEV):
        if (d / "loader" / "wine").exists():
            return d
    return None


def _unified_available() -> bool:
    return _unified_build_dir() is not None


def _unified_d3d_dir() -> Optional[Path]:
    """Locate the bundled d3d DLL pack the unified loader routes to."""
    for d in (UNIFIED_D3D_DIR, UNIFIED_D3D_DEV):
        if (d / "d3d11.dll").exists():
            return d
    return None


def _d3dmetal_native_dir() -> Path:
    """Where libd3dshared.dylib + D3DMetal.framework live for the d3dmetal backend
    (bundled pack first, then the dev D3DMetalTesting tree)."""
    for d in (UNIFIED_D3D_DIR, D3DMETAL_NATIVE_DIR):
        if (d / "libd3dshared.dylib").exists():
            return d
    return D3DMETAL_NATIVE_DIR


def _stage_unified_dlls(prefix: str) -> None:
    """Copy the unified d3d DLL slots into a prefix system32 so the loader has
    real targets to route to (canonical=DXMT plus *_d3dm and *_dxvk). Idempotent:
    only copies when the dest is missing or a different size."""
    src_dir = _unified_d3d_dir()
    if src_dir is None:
        log("unified: d3d DLL pack not found; backend routing may fail (run install_wine_unified)")
        return
    sys32 = Path(prefix) / "drive_c" / "windows" / "system32"
    if not sys32.is_dir():
        return
    staged = 0
    for dll in UNIFIED_D3D_DLLS:
        src = src_dir / dll
        if not src.exists():
            continue
        dst = sys32 / dll
        try:
            if not dst.exists() or src.stat().st_size != dst.stat().st_size:
                shutil.copy2(str(src), str(dst))
                staged += 1
        except Exception as exc:
            log(f"unified: stage {dll} failed: {exc}")
    if staged:
        log(f"unified: staged {staged} d3d DLL(s) -> system32 from {src_dir}")


def _stage_unified_mf(prefix: str) -> None:
    """Stage the game-side winegstreamer video bridge into a prefix and re-point the
    wg_* MF CLSIDs at it so game intro videos decode. Idempotent: the DLL copy is
    size-checked and the registry import runs once guarded by a sentinel."""
    src_dir = _unified_d3d_dir()
    if src_dir is None:
        return
    src = src_dir / UNIFIED_MF_BRIDGE
    if not src.exists():
        return
    sys32 = Path(prefix) / "drive_c" / "windows" / "system32"
    if not sys32.is_dir():
        return
    dst = sys32 / UNIFIED_MF_BRIDGE
    try:
        if not dst.exists() or src.stat().st_size != dst.stat().st_size:
            shutil.copy2(str(src), str(dst))
            log(f"unified: staged {UNIFIED_MF_BRIDGE} -> system32")
    except Exception as exc:
        log(f"unified: stage {UNIFIED_MF_BRIDGE} failed: {exc}")
        return
    # re-point the wg_* CLSIDs once; the sentinel is the bridge name in system.reg
    sysreg = Path(prefix) / "system.reg"
    try:
        if sysreg.exists() and UNIFIED_MF_BRIDGE in sysreg.read_text(errors="ignore"):
            return
    except Exception:
        pass
    bt = _unified_build_dir()
    if bt is None:
        return
    # REGEDIT4 wants doubled backslashes in the value path
    dll_in_reg = "C:\\windows\\system32\\winegstreamer_game.dll".replace("\\", "\\\\")
    blocks = ["REGEDIT4", ""]
    for guid in UNIFIED_MF_CLSIDS:
        blocks.append(f"[HKEY_LOCAL_MACHINE\\Software\\Classes\\CLSID\\{guid}\\InprocServer32]")
        blocks.append(f'@="{dll_in_reg}"')
        blocks.append('"ThreadingModel"="Both"')
        blocks.append("")
    reg_path = ""
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".reg", delete=False) as fh:
            fh.write("\n".join(blocks))
            reg_path = fh.name
        env = _unified_env(prefix, "d3dmetal", for_steam=True)
        env["WINEPREFIX"] = str(prefix)
        env["WINEDEBUG"] = "-all"
        wine = str(bt / "wine")
        wineserver = str(bt / "server" / "wineserver")
        subprocess.run(["/usr/bin/arch", "-x86_64", wine, "reg", "import", reg_path],
                       env=env, timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # wait for the transient server to flush the hive to disk then exit so the
        # re-point survives the steam path wineserver -k that follows
        subprocess.run(["/usr/bin/arch", "-x86_64", wineserver, "-w"],
                       env=env, timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log(f"unified: re-pointed {len(UNIFIED_MF_CLSIDS)} MF CLSIDs at {UNIFIED_MF_BRIDGE}")
    except Exception as exc:
        log(f"unified: MF CLSID import failed: {exc}")
    finally:
        if reg_path:
            try:
                os.unlink(reg_path)
            except Exception:
                pass


def _apply_retina_unified(bt: Path, wine: str, env: Dict[str, str], retina_mode: bool) -> None:
    """Apply the RetinaMode/LogPixels regedit for the unified flow then flush the hive
    so the setting survives the steam path wineserver -k. Without it unified launches
    render in a tiny HiDPI window."""
    _apply_retina_regedit(wine, env, retina_mode)
    try:
        subprocess.run(["/usr/bin/arch", "-x86_64", str(bt / "server" / "wineserver"), "-w"],
                       env=env, timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _unified_engine_active(bottle_cfg: Dict[str, Any]) -> bool:
    """Unified engine is the default; opt out with engine="classic". Falls back to
    the classic per-game flow when the unified wine isn't installed."""
    return bottle_cfg.get("engine", "unified") != "classic" and _unified_available()


def _unified_game_backend(bottle_cfg: Dict[str, Any], backend: str = "") -> str:
    """Map the app's backend id onto the loader's game backends (d3dmetal/dxmt/dxvk/vr).

    A per-game selection of "" or "auto" isn't an override -- it means "use this
    bottle's global backend", i.e. bottle_cfg["default_backend"] (the toolbar's
    global backend picker). Treating "auto" as a truthy override made every
    "Default" game silently render on d3dmetal no matter what the toolbar picker
    said, contradicting it instead of following it (issue #105).
    """
    override = (backend or "").lower()
    if override == BACKEND_AUTO:
        override = ""
    b = (override or bottle_cfg.get("default_backend") or "d3dmetal").lower()
    # Bradar vr = openxr-DXMT (d3d11 w/ OpenXR passthrough thru wineopenxr) -> loader openxr column
    if b in ("vr", "openxr", "dxmt_openxr"):
        return "vr"
    if b == "dxmt":
        return "dxmt"
    if b in ("dxvk", "vkd3d", "vkd3d-proton"):
        return "dxvk"
    return "d3dmetal"


def _unified_env(prefix: str, game_backend: str, metal_hud: bool = False,
                 for_steam: bool = False, gst_debug: str = "") -> Dict[str, str]:
    """Env for the unified wine. Steam exes always render via DXMT (loader gate);
    non-steam games follow MNC_GAME_BACKEND. GStreamer (MF/H.264 video) is wired for
    GAMES ONLY -- Steam CEF crashes if it touches GStreamer so it gets none."""
    env = dict(os.environ)
    for var in ("GTK_PATH", "GTK_EXE_PREFIX", "GTK_DATA_PREFIX", "GDK_PIXBUF_MODULEDIR",
                "GDK_PIXBUF_MODULE_FILE", "GTK_IM_MODULE_FILE", "XDG_DATA_DIRS"):
        env.pop(var, None)
    nd = _d3dmetal_native_dir()
    libd3d = str(nd / "libd3dshared.dylib")
    # Bradar winegstreamer_game.so links the x86_64 homebrew gstreamer by absolute path so its
    # plugins MUST come from that SAME homebrew instance or the registry rejects them
    gst_lib = "/usr/local/opt/gstreamer/lib"
    gst = gst_lib + "/gstreamer-1.0"
    dyld = ":".join([str(nd), gst_lib, "/usr/local/lib", "/usr/local/opt/freetype/lib",
                     "/usr/local/opt/fontconfig/lib", "/usr/local/opt/gnutls/lib",
                     "/usr/local/opt/sdl2/lib", "/usr/local/opt/glib/lib",
                     "/usr/local/opt/gettext/lib",
                     # bundled x86_64 freetype/fontconfig closure so boxes WITHOUT Homebrew still
                     # resolve libfreetype (else "Wine cannot find the FreeType font library" +
                     # fontless games). DYLD_FALLBACK matches by leaf name when the Homebrew abs
                     # paths above are absent. After Homebrew so existing dev setups are unchanged.
                     str(PORTABLE_DIR / "mnc-fonts"),
                     "/usr/lib"])
    env.update({
        "WINEPREFIX": str(prefix),
        "WINEMSYNC": "1",
        "WINEDEBUG": "-all",
        "WINEDBG": "-all",
        "ROSETTA_ADVERTISE_AVX": "1",
        "CX_APPLEGPT_LIBD3DSHARED_PATH": libd3d,
        "CX_APPLEGPTK_LIBD3DSHARED_PATH": libd3d,
        "FONTCONFIG_PATH": "/usr/local/opt/fontconfig/etc/fonts",
        "DYLD_FALLBACK_LIBRARY_PATH": dyld,
        "WINEDLLOVERRIDES": "winemenubuilder.exe=d;mscoree=;mshtml=;nvapi,nvapi64=",
        "MNC_STEAM_DXMT": "1",
        # Bradar skip the slow i386 Wow64Install during wineboot (10s vs 309s) and keep
        # the PE loader resolving 32-bit builtins from the wine lib dir post-bootstrap
        "MNC_SKIP_WOW64_INSTALL": "1",
        "MNC_GAME_BACKEND": game_backend,
        # Bradar GPU-spoof so Steam CEF accepts ANGLE d3d11 -> DXMT (null-GPU crashes SwiftShader)
        # this is the exact load-bearing set from the proven steam-unified-run.sh
        "MNC_WEBHELPER_FLAGS": ("--no-sandbox --in-process-gpu --use-gl=angle --use-angle=d3d11 "
            "--ignore-gpu-blocklist --disable-gpu-driver-bug-workarounds --disable-software-rasterizer "
            "--disable-gpu-watchdog --disable-gpu-process-crash-limit --gpu-no-context-lost "
            "--disable-gpu-process-for-dx12-info-collection --no-delay-for-dx12-vulkan-info-collection "
            "--gpu-vendor-id=0x1002 --gpu-device-id=0x67df --gpu-driver-version=20.45.0 "
            "--gpu-sub-system-id=0 --gpu-revision=0 "
            # Bradar trim the CEF cost -- 1 renderer proc insted of 9 (each one was runnin the
            # wineserver-round-trip IPC loop that dominate wh.sample), + kill the native-occlusion
            # recalc n the chromecast discovery utility proc = fewer background wakeups.
            # if a steam panel ever go blank/white its this cap -> bump to 2
            "--renderer-process-limit=1 --disable-features=CalculateNativeWinOcclusion,MediaRouter "
            "--disable-smooth-scrolling"),
    })
    for var in ("GTK_PATH", "WINEPATH", "VKD3D_PROTON_PATH", "GALLIUM_DRIVER", "DXVK_LOG_PATH"):
        env.pop(var, None)
    if metal_hud:
        env["MTL_HUD_ENABLED"] = "1"
    # GStreamer is GAMES ONLY. Steam must never touch it (its CEF crashes) so strip
    # any inherited plugin path. For games force software H.264 (avdec_h264) and disable
    # VideoToolbox vtdec which crashes the decode under Rosetta x86_64.
    if for_steam:
        for var in ("GST_PLUGIN_SYSTEM_PATH_1_0", "GST_PLUGIN_PATH", "GST_PLUGIN_SYSTEM_PATH"):
            env.pop(var, None)
    else:
        env["GST_PLUGIN_SYSTEM_PATH_1_0"] = gst
        env["GST_PLUGIN_PATH"] = gst
        env["GST_PLUGIN_FEATURE_RANK"] = "vtdec:NONE,vtdec_hw:NONE,avdec_h264:MAX,openh264dec:SECONDARY"
        if gst_debug:
            env["GST_DEBUG"] = gst_debug
            env["GST_DEBUG_NO_COLOR"] = "1"
            env["GST_DEBUG_FILE"] = str(LOG_DIR / "gstreamer.log")
    return env


def _commonredist_hasrun_reg_cmds(prefix: str, wine: str) -> str:
    """Build shell 'wine reg add' lines that pre-set Steam's CommonRedist 'has-run'
    keys. Steam only runs a redist install-script (.NET / VC++ / DirectX) when its
    per-redist has-run key is MISSING - n those installers HANG forever under wine
    (the bootstrapper Setup.exe never exits), wedgin the launch on "Running install
    script (Microsoft .NET Framework)". pre-settin the keys makes steam SKIP them.
    safe: those runtimes r already present (wine builtins / the .NET reg keys) n the
    installers never actualy work under wine anyway. (proven on World War 3 / .NET 4.6.2)"""
    shared = (Path(prefix) / "drive_c" / "Program Files (x86)" / "Steam" /
              "steamapps" / "common" / "Steamworks Shared")
    if not shared.is_dir():
        return ""
    seen = set()
    lines = []
    # each redist ships _CommonRedist/<Type>/<Ver>/installscript.vdf. inside the
    # "Run Process" list, every sub-block's LABEL is the has-run VALUE NAME n the
    # "HasRunKey" field (case varys: HasRunKey / hasrunkey) is the reg KEY PATH.
    # (the transient runasadmin.vdf steam gens per-run is deleted after it runs, so
    # we parse the PERSISTENT installscript.vdfs instead - they always stick around.)
    # HasRunKey is allways the 1st field in a block so [^{}] stays inside one block
    # even tho some blocks nest a Requirement_OS {..} after it.
    block_re = re.compile(r'"([^"]+)"\s*\{[^{}]*?"HasRunKey"\s+"([^"]+)"',
                          re.IGNORECASE | re.DOTALL)
    for vdf in sorted(shared.rglob("*.vdf")):
        try:
            txt = vdf.read_text(errors="ignore")
        except Exception:
            continue
        if "hasrunkey" not in txt.lower():
            continue
        for label, keypath in block_re.findall(txt):
            # VDF escapes backslashes as '\\' -> collapse to single; normalise the hive
            keypath = keypath.replace("\\\\", "\\").replace("HKEY_LOCAL_MACHINE", "HKLM")
            # steam.exe is a 32-bit proccess so it reads the Wow6432Node view -> set BOTH
            variants = {keypath}
            if "\\Software\\" in keypath and "Wow6432Node" not in keypath:
                variants.add(keypath.replace("\\Software\\", "\\Software\\Wow6432Node\\", 1))
            for kp in variants:
                sig = (kp, label)
                if sig in seen:
                    continue
                seen.add(sig)
                lines.append(
                    f"{shlex.quote(wine)} reg add {shlex.quote(kp)} /v {shlex.quote(label)} "
                    f"/t REG_DWORD /d 1 /f >/dev/null 2>&1"
                )
    if not lines:
        return ""
    log(f"Steam CommonRedist: pre-settin {len(lines)} has-run key(s) so redist "
        f"install-scripts skip (they hang under wine)")
    return ("# Bradar pre-satisfy Steam CommonRedist has-run keys so the .NET/VC++/DirectX\n"
            "# redist install-scripts SKIP (they hang forever under wine n wedge the launch)\n"
            + "\n".join(lines) + "\n")


def _steam_dir(prefix) -> Path:
    """The Steam install dir in a prefix. Prefer the canonical 32-bit 'Program Files (x86)\\Steam',
    but fall back to 'Program Files\\Steam': a 32-bit installer (SteamSetup) on a fast-booted prefix
    -- which lacks the full WoW64 ProgramFiles(x86) redirection -- lands Steam in the 64-bit 'Program
    Files' insted, so the launcher must look in BOTH or it "cant detect Steam" right after a
    successful install. Returns whichever actually has steam.exe; defaults to the (x86) path."""
    dc = Path(prefix).expanduser() / "drive_c"
    x86 = dc / "Program Files (x86)" / "Steam"
    noarch = dc / "Program Files" / "Steam"
    if (x86 / "steam.exe").exists():
        return x86
    if (noarch / "steam.exe").exists():
        return noarch
    return x86


def _launch_steam_unified(prefix: str, bottle_cfg: Dict[str, Any], params: Dict[str, Any]) -> Any:
    """Launch Steam through the unified wine so its CEF renders via DXMT."""
    global _steam_process, _steam_started_silent, _steam_prefix, _steam_started_ts
    bt = _unified_build_dir()
    steam_dir = _steam_dir(prefix)
    steam_exe = steam_dir / "steam.exe"
    if not steam_exe.exists():
        raise FileNotFoundError(f"Steam is not installed in this prefix.\nExpected: {steam_exe}")
    _stage_unified_dlls(str(prefix))
    _stage_unified_mf(str(prefix))
    game_backend = _unified_game_backend(bottle_cfg, params.get("backend", ""))
    env = _unified_env(prefix, game_backend, bottle_cfg.get("metal_hud", False), for_steam=True)
    wine = str(bt / "wine")
    wineserver = str(bt / "server" / "wineserver")
    _apply_retina_unified(bt, wine, env, params.get("retina_mode", False))
    silent = bool(params.get("silent", False))
    steam_args = STEAM_SILENT_ARGS if silent else "-tcp"
    log_path = str(LOG_DIR / "Steam-wine.log")
    # match the proven steam-unified-run.sh: kill the server then wipe the CEF caches
    # Bradar (incl userdata GPUCache) so Steam comes up clean on the spoofed GPU + DXMT
    cmd = (
        # export DYLD inside the shell. the outer arch (SIP-restricted) strips DYLD_* so
        # running wine via `arch wine` loses the fallback path and wine cannot dlopen
        # freetype -> no fonts -> tiny empty window. run wine directly under the arch shell
        f"export DYLD_FALLBACK_LIBRARY_PATH={shlex.quote(env['DYLD_FALLBACK_LIBRARY_PATH'])}\n"
        f"{shlex.quote(wineserver)} -k 2>/dev/null; sleep 1\n"
        f"cd {shlex.quote(str(steam_dir))} || exit 1\n"
        f"rm -f .crash 2>/dev/null\n"
        # Bradar keep config/htmlcache (the CEF compiled-UI cache) so steam dont re-cache +
        # re-JIT the whole panorama UI every boot -- only nuke appcache + the spoofed-GPU state
        f"rm -rf appcache 2>/dev/null\n"
        f"rm -f logs/* dumps/*.dmp 2>/dev/null\n"
        f"find userdata -type d -name GPUCache -prune -exec rm -rf {{}} + 2>/dev/null\n"
        # Bradar steam.cfg freeze the client self-updater -- stop the ~4.5min manifest-download
        # churn every launch + stop it re-copyin/re-enablin the 32-bit service we disable below
        f"[ -f steam.cfg ] || printf 'BootStrapperInhibitAll=Enable\\nBootStrapperForceSelfUpdate=disable\\n' > steam.cfg\n"
        # Bradar make steam SKIP the hang-prone .NET/VC++/DirectX redist install-scripts by
        # pre-settin their has-run keys (else e.g. World War 3 wedges forever on "Running
        # install script (Microsoft .NET Framework)" coz the NDP462 bootstrapper never exits)
        f"{_commonredist_hasrun_reg_cmds(str(prefix), wine)}"
        # Bradar THE big one -- disable the 'Steam Client Service' so wines SCM rejects the start
        # BEFORE it spawns + cold-JITs the 32-bit SteamService.exe every 10s (that respawn loop
        # IS the 100% core burst). re-applied each launch coz steam re-enables it on update.
        # VAC/games dont use this service (Linux steam ships none), so its VAC-safe
        f"{shlex.quote(wine)} reg add \"HKLM\\System\\CurrentControlSet\\Services\\Steam Client Service\" /v Start /t REG_DWORD /d 4 /f >/dev/null 2>&1\n"
        f"{shlex.quote(wine)} steam.exe {steam_args} > {shlex.quote(log_path)} 2>&1"
    )
    log(f"Launching Steam (unified/DXMT, backend={game_backend}, silent={silent})")
    proc = subprocess.Popen(["/usr/bin/arch", "-x86_64", "/bin/bash", "-lc", cmd], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    _steam_process = proc
    _steam_started_silent = silent
    _steam_prefix = str(prefix)
    _steam_started_ts = time.time()
    if silent:
        _ensure_steam_idle_watchdog()
    if params.get("wait_ready"):
        ready, status = _wait_steam_ready(prefix, cap_s=int(params.get("ready_cap_s", 240)))
        return {"pid": proc.pid, "log_path": log_path, "already_running": False,
                "ready": ready, "status": status, "engine": "unified"}
    return {"pid": proc.pid, "log_path": log_path, "already_running": False, "engine": "unified"}


def _steam_is_running() -> bool:
    # Bradar check if steam is ALREADY up so launchin a game dont kill n relaunch it bradar
    # first we trust the steam process we started ourself
    if _steam_process is not None and _steam_process.poll() is None:
        return True
    # Bradar otherwise we scan the real process table - the wine steam client show up as
    # "steam.exe -tcp" so we anchor on line start n we dont match the webhelper or the SteamService bradar
    try:
        out = subprocess.run(["ps", "-Ao", "command"], capture_output=True, text=True, timeout=6).stdout
        return any(line.startswith("steam.exe") for line in out.splitlines())
    except Exception:
        return False


def _stage_syswow64(prefix: str) -> int:
    """Give the prefix a REAL 32-bit system dir (syswow64) by clonin the wine builds i386 PE
    builtins into it. new bottles r booted with MNC_SKIP_WOW64_INSTALL=1 (fast: ~10s vs ~5min)
    which SKIPs the slow i386 Wow64Install, so syswow64 stays EMPTY. the unified wines HACK ntdll
    papers over that (it resolvs 32-bit builtins from the lib dir), but the pre-HACK22 installer
    overlay wine (n Wine Stable) have NO such hack -> a 32-bit installer (SteamSetup, vc_redist,
    Rockstar Launcher, Social-Club ...) dies 'could not load kernel32.dll, status c0000135' befor
    it ever opens a window. clonin the i386 dlls here (APFS clonefile = ~1s, ~0 disk) gives a
    working 32-bit subsystem WITHOUT the slow full wineboot (which crawls / wedges on the i386
    rundll32 under Rosetta - it can sit for 5min+ writin nothing). idempotent: no-op once syswow64
    is populated. returns the count staged (0 if allready set up or no source build).
    See steamsetup-installer-wine-overlay + wineboot-slow-i386-wow64."""
    sw = Path(prefix) / "drive_c" / "windows" / "syswow64"
    # the load-bearing 32-bit bootstrap dlls. a raw count is NOT a safe "done" signal: an interrupted
    # copy (a cross-volume REAL copy on /Volumes, a >timeout stall, or app-quit mid-stage) can leave a
    # partial dir that a count check passes while kernel32/ntdll/user32 r still missing -> c0000135
    # forever (kernel32 lands ~220th, user32 ~455th in glob order). so key off the actual bootstrap
    # dlls + a completion marker written ONLY after a verified-full copy.
    crit = ("kernel32.dll", "ntdll.dll", "kernelbase.dll", "user32.dll")
    marker = sw / ".mnc_syswow64_ok"
    try:
        crit_ok = all((sw / d).is_file() for d in crit)
        # done if a prior stage completed (marker) OR wine full-booted the prefix itself (all
        # bootstrap dlls + a near-full set ~624). adopt a full-booted prefix by writin the marker so
        # we dont needlessly re-clone it.
        if crit_ok and (marker.is_file() or len(list(sw.glob("*.dll"))) >= 580):
            if not marker.is_file():
                try: marker.write_text("adopted")
                except Exception: pass
            return 0
    except Exception:
        pass
    bt = _unified_build_dir()
    if not bt or not (bt / "dlls").is_dir():
        log("_stage_syswow64: no unified build to source i386 builtins from; skippin")
        return 0
    sw.mkdir(parents=True, exist_ok=True)
    # clone the i386 PE builtins into syswow64: dlls/*/i386-windows (kernel32/ntdll/kernelbase/...)
    # AND programs/*/i386-windows (msiexec.exe, rundll32.exe, regsvr32.exe -- needed by 32-bit .msi
    # packages n tool-spawnin installers). cp -c = APFS clonefile (instant, ~0 disk); plain cp
    # fallback covers a prefix on a diffrent volume than deps. count ONLY successful copies so the
    # log/return isnt inflated by failures. (the overlay wine loads its pre-HACK22 UNIX ntdll.so
    # from its own build tree -- the i386 PE ntdll here carries no HACK22, so no fault-storm.)
    q_bt = shlex.quote(str(bt)); q_sw = shlex.quote(str(sw))
    shcmd = (f'shopt -s nullglob; c=0; '
             f'for f in {q_bt}/dlls/*/i386-windows/*.dll {q_bt}/dlls/*/i386-windows/*.exe '
             f'{q_bt}/programs/*/i386-windows/*.exe {q_bt}/programs/*/i386-windows/*.dll; do '
             f'if cp -c "$f" {q_sw}/ 2>/dev/null || cp "$f" {q_sw}/ 2>/dev/null; then c=$((c+1)); fi; '
             f'done; printf %s "$c"')
    try:
        r = subprocess.run(["/bin/bash", "-c", shcmd], capture_output=True, text=True, timeout=300)
        staged = int((r.stdout or "0").strip() or "0")
    except Exception as exc:
        log(f"_stage_syswow64 failed: {exc}")
        return 0
    # mark complete ONLY if the bootstrap dlls actually landed -> a partial/interrupted copy leaves
    # no marker n self-heals by re-stagin on the next call insted of cachin a broken dir forever.
    if all((sw / d).is_file() for d in crit):
        try: marker.write_text(str(staged))
        except Exception: pass
    else:
        log(f"_stage_syswow64: WARNING staged {staged} but bootstrap dlls missing -> will re-stage next call")
    log(f"_stage_syswow64: cloned {staged} i386 builtins into syswow64 (32-bit subsystem for installers)")
    return staged


def _prehack22_wine() -> str:
    """Loader for the PRE-HACK22 wine (stock gs.base swap), used ONLY to run WoW64 redist
    installers (vc_redist / VulkanRT / Rockstar-Games-Launcher + Social-Club Burn bundles /
    .NET) that fault-storm at ~100% CPU forEVER under the unified wines HACK22 gs.base
    rewrite -- HACK22 breaks the WoW64 32<->64 transition so those 32-bit Burn engines jump
    to garbage n spin ("stuck on installer script"). the pre-HACK22 wine runs them clean.
    Steam + the actual games keep the unified HACK22 wine. See winemono-32bit-hack22-rootcause."""
    cands = []
    ov = os.environ.get("MNC_INSTALLER_WINE", "").strip()
    if ov:
        cands.append(Path(ov))
    cands += [
        PORTABLE_DIR / "wine-installer" / "wine",                                 # overlay clone (install_wine_installer)
        PORTABLE_DIR / "wine-installer" / "tools" / "wine" / "wine",              # overlay clone (direct loader)
        PORTABLE_DIR / "wine-installer" / "build64" / "tools" / "wine" / "wine",  # bundled build-tree (older shape)
        PORTABLE_DIR / "wine-installer" / "bt" / "wine",
        Path("/Volumes/ASAFE/D3DMETALWINEDEV/wt-pre-hack22/build64/tools/wine/wine"),  # dev worktree
    ]
    for c in cands:
        try:
            if c.exists():
                return str(c)
        except Exception:
            continue
    return ""


def _run_installer_prehack22(prefix: str, cmd_after_wine: List[str],
                             backend: str = "d3dmetal",
                             log_path: Optional[str] = None) -> subprocess.Popen:
    """Launch a WoW64/32-bit installer (SteamSetup, generic .exe/.msi installers)
    via the PRE-HACK22 wine. Steams NSIS stub -- n other 32-bit NSIS/Burn installer
    stubs -- jump to garbage n fault-storm at 100% CPU under the unified wines HACK22
    gs.base rewrite (it breaks the WoW64 32<->64 transition). from the UI that looks
    like the installer never launchs: the stub faults BEFOR it ever opens a window,
    n with output to /dev/null it writes no logs eithr. the pre-HACK22 wine runs them
    clean, same as the redist pre-install path (_run_installscript_redists). uses
    arch-x86_64 + an in-shell DYLD re-export becuse arch strips DYLD_*. wine output is
    teed to log_path so "Run Installer" isnt a silent black box. Falls back to the
    unified wine only when the pre-HACK22 wine isnt bundled (may then fault-storm)."""
    env = _unified_env(prefix, backend or "d3dmetal", False, for_steam=False)
    env["WINEDEBUG"] = "-all,+err"  # errors only: bounded log but catchs real crashs / missing DLLs
    # the pre-HACK22 overlay wine (n the Wine-Stable fallback) have no MNC_SKIP_WOW64_INSTALL hack,
    # so a fast-booted bottle (empty syswow64) makes 32-bit installers die c0000135. give it a real
    # 32-bit subsystem first (fast clonefile, idempotent). THIS is why "Run Installer" was failing.
    _stage_syswow64(prefix)
    out = open(log_path, "w") if log_path else subprocess.DEVNULL
    iw = _prehack22_wine()
    if iw:
        dyld = env.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        tail = " ".join(shlex.quote(a) for a in cmd_after_wine)
        sh = (f"export DYLD_FALLBACK_LIBRARY_PATH={shlex.quote(dyld)}\n"
              f"exec {shlex.quote(iw)} {tail}")
        log(f"installer (pre-HACK22 wine): {cmd_after_wine}")
        return subprocess.Popen(["/usr/bin/arch", "-x86_64", "/bin/bash", "-lc", sh],
                                env=env, stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True)
    # no pre-HACK22 overlay -> Wine Stable is a normal (no-HACK22) wine that also runs
    # 32-bit NSIS/Burn installers clean; use it before falling back to the storming unified wine.
    stable = _find_wine_stable()
    if stable:
        log(f"installer: no pre-HACK22 overlay -> Wine Stable ({stable})")
        return subprocess.Popen([stable] + list(cmd_after_wine), env=env,
                                stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True)
    wine = _find_wine()
    if not wine:
        raise FileNotFoundError("Wine not found")
    log("installer: no pre-HACK22 overlay + no Wine Stable -> unified wine (32-bit NSIS/Burn MAY fault-storm)")
    return subprocess.Popen([wine] + list(cmd_after_wine), env=env,
                            stdout=out, stderr=subprocess.STDOUT,
                            start_new_session=True)


def _run_installscript_redists(prefix: str, game_dir: str, backend: str) -> None:
    """Actualy INSTALL a Steam games install-script redists (VC++ / Vulkan RT / Rockstar
    Launcher / Social Club / .NET) via the PRE-HACK22 wine, THEN set their per-redist
    has-run keys so Steam skips its OWN run of them. Steam fires these WoW64/Burn installers
    under our HACK22 wine where they spin at 100% CPU forever + wedge the launch on "Running
    install script"; the pre-HACK22 wine finishs them clean. Idempotent -- a redist whos
    has-run value is allready set on disk is skipd. No-op if no installscript.vdf or the
    pre-HACK22 wine isnt present. See winemono-32bit-hack22-rootcause."""
    iw = _prehack22_wine()
    if not iw:
        return
    gd = Path(game_dir)
    # installscript.vdf sits at the game root; steam-shared redists ship per-redist ones too
    vdfs = list(gd.glob("installscript*.vdf"))
    for extra in ("Redistributables", "_CommonRedist"):
        vdfs += list((gd / extra).rglob("installscript*.vdf")) if (gd / extra).is_dir() else []
    if not vdfs:
        return
    try:
        sysreg = (Path(prefix) / "system.reg").read_text(errors="ignore")
    except Exception:
        sysreg = ""
    # a labeld sub-block: "<label>" { "HasRunKey" "<regpath>" ... "process 1" "<exe>" "command 1" "<args>" }
    # HasRunKey is allways the 1st field so [^{}] stays inside the block (matchs the existing
    # _commonredist parser). we then read process/command from the same blocks tail.
    block_re = re.compile(r'"([^"]+)"\s*\{[^{}]*?"HasRunKey"\s+"([^"]+)"',
                          re.IGNORECASE | re.DOTALL)
    def _field(t, name):
        m = re.search(r'"' + name + r'"\s+"([^"]*)"', t, re.IGNORECASE)
        return m.group(1) if m else None
    env = _unified_env(prefix, backend or "d3dmetal", False, for_steam=False)
    env["WINEDEBUG"] = "-all"
    _stage_syswow64(prefix)  # 32-bit subsystem so the pre-HACK22 wine can run these 32-bit redists
    dyld = env.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    handled = 0
    for vdf in sorted(set(vdfs)):
        try:
            txt = vdf.read_text(errors="ignore")
        except Exception:
            continue
        if "hasrunkey" not in txt.lower():
            continue
        for m in block_re.finditer(txt):
            label, key = m.group(1), m.group(2)
            tail = txt[m.end():m.end() + 900]
            proc = _field(tail, "process 1")
            if not proc:
                continue
            cmd_args = _field(tail, "command 1") or ""
            key = key.replace("\\\\", "\\").replace("HKEY_LOCAL_MACHINE", "HKLM")
            # idempotent: already-done redists have the has-run value set on disk
            if f'"{label}"=dword:00000001' in sysreg:
                continue
            unixpath = proc.replace("\\\\", "\\").replace("%INSTALLDIR%", str(gd)).replace("\\", "/")
            if not Path(unixpath).exists():
                continue
            log(f"redist pre-install (pre-HACK22): {Path(unixpath).name} {cmd_args}".rstrip())
            sh = (f"export DYLD_FALLBACK_LIBRARY_PATH={shlex.quote(dyld)}\n"
                  f"{shlex.quote(iw)} {shlex.quote(unixpath)} {cmd_args} >/dev/null 2>&1")
            try:
                subprocess.run(["/usr/bin/arch", "-x86_64", "/bin/bash", "-lc", sh],
                               env=env, timeout=900)
            except Exception as exc:
                log(f"redist {Path(unixpath).name} run failed: {exc}")
            # steam.exe reads the Wow6432Node view -> set BOTH so it skips its storming run
            variants = {key}
            if "\\Software\\" in key and "Wow6432Node" not in key:
                variants.add(key.replace("\\Software\\", "\\Software\\Wow6432Node\\", 1))
            for kp in variants:
                rc = (f"export DYLD_FALLBACK_LIBRARY_PATH={shlex.quote(dyld)}\n"
                      f"{shlex.quote(iw)} reg add {shlex.quote(kp)} /v {shlex.quote(label)} "
                      f"/t REG_DWORD /d 1 /f >/dev/null 2>&1")
                try:
                    subprocess.run(["/usr/bin/arch", "-x86_64", "/bin/bash", "-lc", rc],
                                   env=env, timeout=60)
                except Exception:
                    pass
            handled += 1
    if handled:
        log(f"redist pre-install: finishd {handled} install-script redist(s) via pre-HACK22 "
            f"wine so Steam wont fault-storm on them")


def _launch_game_unified(prefix: str, exe: str, args: str, bottle_cfg: Dict[str, Any],
                         params: Dict[str, Any]) -> Any:
    """Launch a game through the unified wine; the loader routes its d3d to the
    chosen backend while Steam stays on DXMT."""
    bt = _unified_build_dir()
    exe_path = Path(exe)
    # SteamSetup.exe is a 32-bit NSIS stub that fault-storms on the unified HACK22 wine -> a Play
    # would spin forever with NO window (the storm is the HACK22 WINE, not the d3dmetal/dxmt backend,
    # so switchin backend wouldnt help at all). route it to the pre-HACK22 installer wine + /S so
    # Steam installs silently (the GUI wizard doesnt reliably surface under wine); a later Play then
    # finds steam.exe n launchs it via DXMT. this is why "Play on a steam bottle w/o Steam" did
    # nothing + logd backend=d3dmetal.
    if exe_path.name.lower() == "steamsetup.exe":
        tail = [str(exe_path)] + (shlex.split(args) if args else ["/S"])
        logf = str(Path(prefix) / "mnc-installer.log")
        proc = _run_installer_prehack22(str(prefix), tail, "d3dmetal", log_path=logf)
        _running_games[proc.pid] = proc
        log(f"launch: SteamSetup.exe routed to pre-HACK22 installer wine (silent); log {logf}")
        return {"pid": proc.pid}
    _stage_unified_dlls(str(prefix))
    _stage_unified_mf(str(prefix))
    _ensure_steam_sdl_resolvable(str(prefix))
    backend = _unified_game_backend(bottle_cfg, params.get("backend", ""))
    metal_hud = params.get("metal_hud", bottle_cfg.get("metal_hud", False))
    debug = bool(params.get("debug", bottle_cfg.get("debug", False)))
    steam_mode = params.get("steam_mode", "silent")
    is_steam_bottle = bottle_cfg.get("launcher_type", "steam") == "steam"
    # Bradar pre-instal the games install-script redists (VC++/Vulkan RT/Rockstar Launcher/
    # Social Club/.NET) via the pre-HACK22 wine BEFORE steam runs its own install-script.
    # steam fires them under our HACK22 wine where the 32-bit Burn bundles fault-storm at
    # 100% CPU forever ("stuck on installer script"); this finishs them clean + sets the
    # has-run keys so steam skips its storming run. idempotent (skips already-done ones).
    if is_steam_bottle:
        try:
            _run_installscript_redists(str(prefix), str(exe_path.parent), backend)
        except Exception as exc:
            log(f"redist pre-install skipped: {exc}")
    if steam_mode != "none" and is_steam_bottle:
        # Bradar if steam is already up we DONT kill/relaunch it (the old code always ran
        # _launch_steam_unified which does a "wineserver -k" so it was killin n re-bootstrappin
        # the whole steam EVERY launch - slow n stackd processes). BUT we STILL gotta block
        # till it reachs [Logged On]: steam merely "running" aint enough - if its still
        # [Connecting]/[Logging On] the games SteamAPI_Init races ahead n comes back
        # "[API loaded no]" (proven: games launchd 18:33, steam only logd on 18:37 -> fail).
        if _steam_is_running():
            ready, status = _wait_steam_ready(str(prefix), cap_s=180)
            log(f"unified: Steam already running -> waited for auth: ready={ready} ({status})")
        else:
            try:
                _launch_steam_unified(prefix, bottle_cfg,
                                      {"silent": (steam_mode == "silent"), "wait_ready": True,
                                       "backend": params.get("backend", "")})
            except Exception as exc:
                log(f"unified: steam auto-launch failed: {exc} (continuing)")
    env = _unified_env(prefix, backend, metal_hud, gst_debug=("5" if debug else "3"))
    # Bradar VR: register the wineopenxr bridge as the prefixs active OpenXR runtime + force
    # our bundled x86_64 Monado runtime (an arm64 system one wont dlopen into the Rosetta wine)
    if backend == "vr":
        _ensure_wineopenxr_registered(str(prefix))
        env = _apply_monado_runtime_env(env)
    # Bradar DXVK MUST have the MoltenVK vulkan ICD wired or its vkCreateInstance dies with
    # "Failed to create Vulkan 1.1 instance" -> the game pops "Error creating a D3D device".
    # the unified env never set it (only the old per-backend path did) so EVERY dxvk game crashd.
    # _find_moltenvk_icd resolves the x86_64 MoltenVK (the arm64 one wont dlopen in Rosetta wine)
    if backend == "dxvk":
        vk_icd = _find_moltenvk_icd()
        if vk_icd:
            env["VK_ICD_FILENAMES"] = vk_icd   # legacy vulkan-loader name
            env["VK_DRIVER_FILES"] = vk_icd    # modern vulkan-loader name
        env.setdefault("DXVK_STATE_CACHE", "0")
    exe_dir = str(exe_path.parent)
    steam_appid = str(params.get("steam_appid", "")).strip()
    if not steam_appid.isdigit():
        steam_appid = _derive_steam_appid(exe_dir) or ""
    if steam_appid.isdigit():
        try:
            (Path(exe_dir) / "steam_appid.txt").write_text(steam_appid)
        except Exception:
            pass
        env["SteamAppId"] = steam_appid
        env["SteamGameId"] = steam_appid
    # use bt/wine (the build-tree loader symlink -> tools/wine/wine) not bt/loader/wine
    # the latter is the install-style loader and cannot find the build nls -> l_intl.nls fails
    wine = str(bt / "wine")
    _apply_retina_unified(bt, wine, env, params.get("retina_mode", bottle_cfg.get("retina_mode", False)))
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", exe_path.stem)
    log_path = str(LOG_DIR / f"{safe_name}-wine.log")
    quoted_args = (" " + args) if args else ""
    cmd = (
        # export DYLD inside the shell. the outer arch (SIP-restricted) strips DYLD_* so
        # running wine via `arch wine` loses the fallback path and wine cannot dlopen
        # freetype -> no fonts. run wine directly under the arch shell (same as Steam)
        f"export DYLD_FALLBACK_LIBRARY_PATH={shlex.quote(env['DYLD_FALLBACK_LIBRARY_PATH'])}\n"
        f"cd {shlex.quote(exe_dir)} || exit 1\n"
        f"{shlex.quote(wine)} {shlex.quote(str(exe_path))}{quoted_args} "
        f"> {shlex.quote(log_path)} 2>&1"
    )
    log(f"Launching game (unified, backend={backend}): {exe_path.name}")
    proc = subprocess.Popen(["/usr/bin/arch", "-x86_64", "/bin/bash", "-lc", cmd], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    _launched_games[(str(prefix), str(exe))] = proc.pid
    _running_games[proc.pid] = proc
    return {"pid": proc.pid, "log_path": log_path, "backend": backend, "engine": "unified"}


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
    # Advanced debug (launch-sheet toggle): verbose WINEDEBUG + UE -log so the
    # game's log actually contains load/import/crash detail instead of nothing.
    verbose_debug = bool(params.get("debug", bottle_cfg.get("debug", False)))
    # "silent" (background Steam, no window) | "open" (full Steam UI) | "none".
    # Both silent and open launch Steam via the SAME Wine-Stable path
    # Bradar (cmd_launch_steam) — the no-shim D3DMetal wine can't render Steam's CEF UI.
    steam_mode = params.get("steam_mode", "silent")
    # Mirror the frontend's power toggle so the idle-Steam watchdog follows it.
    global _auto_stop_steam
    if "auto_stop_steam" in params:
        _auto_stop_steam = bool(params.get("auto_stop_steam"))

    # ── Duplicate-launch guard (field report: MiKo) ──────────────────────
    # When a game hangs without a window, users click Launch repeatedly and
    # every click used to stack another detached Wine instance. If the SAME exe
    # in the SAME prefix is still alive from a previous launch, refuse to spawn
    # another and tell the UI instead (it shows "already running — use Kill").
    _dup_key = (str(prefix), str(exe))
    _prev_pid = _launched_games.get(_dup_key)
    if _prev_pid:
        _prev_proc = _running_games.get(_prev_pid)
        if _prev_proc is not None:
            _prev_alive = _prev_proc.poll() is None
        else:
            try:
                os.kill(_prev_pid, 0)
                _prev_alive = True
            except OSError:
                _prev_alive = False
        if _prev_alive:
            log(f"Duplicate launch blocked: {exe} already running as PID {_prev_pid}")
            return {"pid": _prev_pid, "already_running": True}
        _launched_games.pop(_dup_key, None)
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")
    if not exe:
        raise ValueError("Missing 'exe' parameter")

    log(f"[display] screens: {screen_info}")
    log(f"[display] retina_mode={retina_mode}")

    exe_path = Path(exe)
    if not exe_path.exists():
        raise FileNotFoundError(f"Executable not found: {exe}")

    if _unified_engine_active(bottle_cfg):
        return _launch_game_unified(prefix, exe, args, bottle_cfg, params)

    if not backend or backend == BACKEND_AUTO:
        backend = _resolve_auto_backend(exe)
        log(f"Auto backend resolved for {Path(exe).name}: {backend} (game_type={_detect_game_type(exe)})")
    else:
        log(f"Resolved graphics backend: {backend}")


    key = _resolve_key(prefix)
    bottle_cfg = _load_bottles().get(key, {})
    wine_pref = bottle_cfg.get("wine_binary", "auto")

    # Steam launcher selection from the launch sheet ("Silent Steam" / "Open
    # Bradar Steam" / "No Steam") — honoured for EVERY backend, not just D3DMetal. We
    # bring Steam up the SAME way the "Open Steam" button does (cmd_launch_steam
    # → Wine Stable), so a Steamworks game always finds an authenticated Steam
    # client. steam_mode picks silent (-silent, background, no window) vs open
    # (full Steam UI); "none" skips Steam entirely (best for standalone games).
    # We BLOCK until Steam reaches [Logged On] before launching the game — a
    # Steamworks game started before the Steam API is authenticated dies with
    # "Steam denied appID". An already-running Steam is assumed ready.
    # Bradar (The no-shim D3DMetal wine in particular can't render Steam's CEF UI, which
    # is the original reason Steam must come up via Wine Stable, not the backend.)
    # Only for Steam bottles — a "None"/custom bottle's launch must not drag up
    # Steam (or the bottle's custom launcher) on every game start.
    is_steam_bottle = bottle_cfg.get("launcher_type", "steam") == "steam"
    if steam_mode != "none" and is_steam_bottle:
        try:
            steam_result = cmd_launch_steam({
                "prefix": prefix,
                "retina_mode": retina_mode,
                "backend": backend,
                "silent": (steam_mode == "silent"),
                "wait_ready": True,
            })
            if steam_result.get("already_running"):
                log("Steam already running, proceeding to game launch")
            else:
                log(f"Steam launched ({steam_mode}, pid {steam_result.get('pid')}) "
                    f"via Wine Stable; ready={steam_result.get('ready')} "
                    f"({steam_result.get('status')})")
        except Exception as exc:
            log(f"Steam auto-launch failed: {exc} (continuing anyway)")

   


    # Honour the bottle's Wine selection (Auto / Stable / Staging / Devel) when
    # Bradar the graphics backend doesn't force a Wine of its own (d3dmetal3/gptk/devel).
    wine = _backend_wine_binary(backend, exe) or _find_wine_for_bottle(wine_pref)
    if not wine:
        raise FileNotFoundError("Wine not found. Install Wine first.")

 
    effective_install_dir = install_dir or str(exe_path.parent)
    # Make Steam's SDL3/SDL2 findable so SteamAPI_Init doesn't assert
    # "Failed to load SDL3.dll" (it lives in the Steam root, off the search path).
    _ensure_steam_sdl_resolvable(prefix)
    patch_record: List[Tuple[str, bool]] = []
    try:
        patch_record = _prepare_game_for_backend(backend, exe_path, effective_install_dir) or []
    except Exception as exc:
        log(f"Warning: DLL patching failed: {exc}")

    # The OpenXR fork needs the wineopenxr bridge registered as the prefix's
    # active OpenXR runtime before a VR app starts (idempotent — skipped if the
    # prefix is already wired up).
    if backend == BACKEND_DXMT_OPENXR:
        _ensure_wineopenxr_registered(prefix)


    env = _wine_env(prefix)
    env = _apply_backend_env(env, backend, verbose_debug)
    env = _apply_sync_env(env, esync, msync)

    # VR: point the OpenXR loader at our x86_64 Monado runtime (XR_RUNTIME_JSON)
    # so a stale arm64 system runtime can't be picked — that would fail to dlopen
    # into the x86_64 Wine process. Also logs a clear warning if it's missing/arm64.
    if backend == BACKEND_DXMT_OPENXR:
        env = _apply_monado_runtime_env(env)


    if metal_hud:
        env["MTL_HUD_ENABLED"] = "1"

  
    _apply_retina_regedit(wine, env, retina_mode)

    exe_dir = str(exe_path.parent)
    exe_name = exe_path.name

    # Steamworks games must know their AppID at SteamAPI_Init, or they can't bind
    # to the running Steam client — SteamAPI_Init returns no user and the game
    # exits with no window (proven: the working run logs "Setting breakpad
    # minidump AppID = <id>" + caches a SteamID; the failing one does neither).
    # We used to ONLY read steam_appid.txt, which fresh installs don't ship — so
    # the game launched blind. Use the AppID the frontend already knows (Steam
    # library scan), fall back to steam_appid.txt, and surface it BOTH as a file
    # next to the exe AND via the SteamAppId/SteamGameId env for every backend.
    steam_appid = str(params.get("steam_appid", "")).strip()
    if not steam_appid.isdigit():
        steam_appid = _derive_steam_appid(exe_dir) or ""
    if steam_appid.isdigit():
        try:
            appid_file = Path(exe_dir) / "steam_appid.txt"
            if (not appid_file.exists()
                    or appid_file.read_text(errors="ignore").strip() != steam_appid):
                appid_file.write_text(steam_appid)
                log(f"steam: wrote steam_appid.txt={steam_appid} next to {exe_name}")
        except Exception as exc:
            log(f"steam: could not write steam_appid.txt: {exc}")
        env["SteamAppId"] = steam_appid
        env["SteamGameId"] = steam_appid
    else:
        steam_appid = ""

    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", exe_path.stem)
    log_path = str(LOG_DIR / f"{safe_name}-wine.log")

    arg_parts = shlex.split(args) if args else []
    # UE4 (4.x) games default to the D3D12 RHI, but UE4's D3D12 path null-derefs
    # Bradar under D3DMetal (EXCEPTION_ACCESS_VIOLATION at RHI init — e.g. Escape the
    # Backrooms "Fatal error!"), while its D3D11 RHI runs fine. Force -d3d11 for
    # Bradar UE4 titles on the D3DMetal/GPTK backends. NOT for UE5 (Nanite/Lumen require
    # D3D12), and never override a user-supplied RHI flag.
    if backend in (BACKEND_D3DMETAL3, BACKEND_GPTK):
        _rhi_flags = ("-d3d11", "-d3d12", "-dx11", "-dx12", "-sm5", "-sm6", "-vulkan", "-opengl", "-d3d10")
        if (_detect_game_type(exe) == "ue4"
                and not any(p.lower() in _rhi_flags for p in arg_parts)):
            arg_parts = ["-d3d11"] + arg_parts
            log("UE4 on D3DMetal: auto-added -d3d11 (UE4 D3D12 RHI crashes on D3DMetal)")
    # Advanced debug: make Unreal Engine titles write their full log to the
    # console (captured in the per-game wine log) so RHI/crash detail is visible.
    if verbose_debug and _detect_game_type(exe) in ("ue4", "ue5") and "-log" not in [p.lower() for p in arg_parts]:
        arg_parts = arg_parts + ["-log"]
    quoted_args = " ".join(shlex.quote(a) for a in arg_parts)

    launch_extra_env: Dict[str, str] = {}
    if metal_hud:
        launch_extra_env["MTL_HUD_ENABLED"] = "1"
    if steam_appid:
        # Bradar The d3dmetal3/gptk heredocs export SteamAppId from extra_env.
        launch_extra_env["SteamAppId"] = steam_appid
        launch_extra_env["SteamGameId"] = steam_appid
    cmd = _backend_launch_cmd(
        backend, wine, exe_dir, exe_name, prefix, exe, quoted_args, log_path,
        extra_env=launch_extra_env or None,
        debug=verbose_debug,
    )

    
    if bottle_cfg.get("discord_rpc", True):
        _rpc_bridge_start(wine, env)

    
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

    _register_running_game(proc, enable_game_mode=params.get("game_mode", True))
    _launched_games[_dup_key] = proc.pid
    log(f"Game launched with PID {proc.pid}, backend={backend}, log at {log_path}")

    # Revert the per-launch DLL swap once the game exits, so nothing is left
    # Bradar replaced: the game-dir copies (D3DMetal/GPTK/DXVK/…) and, for DXMT, the
    # shared Wine-Stable lib (so Steam can launch cleanly afterwards).
    if patch_record or backend in (BACKEND_DXMT, BACKEND_DXMT_OPENXR):
        threading.Thread(
            target=_revert_after_game_exit, args=(proc, patch_record, backend), daemon=True
        ).start()


    if bottle_cfg.get("discord_rpc", True):
        _discord_presence_for_launch(proc, exe, params.get("game_name", ""))

    return {"pid": proc.pid, "log_path": log_path, "backend": backend}



_steam_process: Optional[subprocess.Popen] = None
# ── Background-Steam power management (field report: Hafliss) ──────────────
# A silent-launched Steam kept its full CEF/steamwebhelper stack running
# forever after games quit — Activity Monitor showed "wine" at ~2700 energy
# impact while idle. Silent Steam is only a Steamworks provider (no UI is ever
# shown), so: launch it with -no-browser (skips the CEF stack entirely) and
# auto-stop it a few minutes after the last game exits.
STEAM_SILENT_ARGS = "-silent -tcp -no-browser"
STEAM_IDLE_GRACE_S = 300  # stop silent Steam 5 min after the last game exits
_steam_started_silent = False
_steam_prefix: str = ""
_steam_started_ts: float = 0.0
_last_game_exit_ts: float = 0.0
_auto_stop_steam = True  # frontend mirrors its Settings toggle on every launch
_steam_watchdog_started = False


def cmd_launch_steam(params: Dict[str, Any]) -> Any:
    """Launch Steam inside a Wine prefix.

    Mirrors the logic in MacNCheese.py  MainWindow.launch_steam().
    """
    global _steam_process, _steam_started_silent, _steam_prefix, _steam_started_ts, _auto_stop_steam

    prefix = params.get("prefix")
    retina_mode = params.get("retina_mode", False)
    backend = params.get("backend", "auto")
    silent = bool(params.get("silent", False))
    if "auto_stop_steam" in params:
        _auto_stop_steam = bool(params.get("auto_stop_steam"))
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")

    # Check if Steam is already running
    if _steam_process is not None and _steam_process.poll() is None:
        # Bradar even when its our OWN steam thats already up, honour wait_ready so a
        # game launch dont race ahead of [Logged On] (the "[API loaded no]" bug).
        if params.get("wait_ready"):
            ready, status = _wait_steam_ready(str(prefix))
            return {"already_running": True, "pid": _steam_process.pid,
                    "ready": ready, "status": status}
        return {"already_running": True, "pid": _steam_process.pid}

    _ucfg = _load_bottles().get(_resolve_key(prefix), {})
    if _unified_engine_active(_ucfg):
        return _launch_steam_unified(prefix, _ucfg, params)

    # Bradar Steam runs on Wine Stable. A prior DXMT game replaces Wine Stable's shared
    # lib d3d11/dxgi/d3d10core (and drops winemetal.dll); if left in place, Steam
    # Bradar loads DXMT's Metal-based Direct3D and fails to launch. Restore the stock
    # DLLs first so Steam always starts on clean Direct3D. (In the game-launch
    # flow Steam comes up + reaches [Logged On] BEFORE the per-game DLL prep
    # Bradar re-applies DXMT, so the game still gets DXMT and Steam stays stock.)
    try:
        _restore_wine_lib_from_dxmt_backup()
    except Exception as exc:
        log(f"Steam launch: wine-lib restore failed: {exc}")

    if backend == "auto":
        backend = _resolve_auto_backend()

    wine = _find_wine()
    if not wine:
        raise FileNotFoundError("Wine not found. Install Wine first.")

    
    key = _resolve_key(prefix)
    bottle_cfg = _load_bottles().get(key, {})
    launcher_exe = bottle_cfg.get("launcher_exe", "").strip()

    if launcher_exe and Path(launcher_exe).exists():
      
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
        _steam_started_silent = False  # custom launchers are user-visible; never auto-stop
        _steam_prefix = str(prefix)
        _steam_started_ts = time.time()
        log(f"Custom launcher launched with PID {proc.pid}")
        return {"pid": proc.pid, "log_path": log_path, "already_running": False}
    elif launcher_exe:
        log(f"Custom launcher_exe '{launcher_exe}' not found, falling back to Steam")

    steam_dir = _steam_dir(prefix)
    steam_exe = steam_dir / "steam.exe"

    if not steam_exe.exists():
        raise FileNotFoundError(
            f"Steam is not installed in this prefix.\n"
            f"Expected: {steam_exe}"
        )

   
    mnc_root = PORTABLE_DIR / "Wine Stable.app" / "Contents" / "Resources" / "wine"
    mnc_wine = mnc_root / "bin" / "wine"
    if mnc_wine.exists():
        wine = str(mnc_wine)

   
    dyld_fallback = ":".join([
        str(D3DMETAL_NATIVE_DIR),
        "/usr/local/lib",
        "/usr/local/opt/freetype/lib",
        "/usr/local/opt/gnutls/lib",
        "/usr/lib",
    ])

    
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

   
    regedit_env = dict(env)
    regedit_env["WINEPREFIX"] = prefix
    regedit_env["PATH"] = f"{mnc_root / 'bin'}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    _apply_retina_regedit(wine, regedit_env, retina_mode)

    safe_name = "Steam"
    log_path = str(LOG_DIR / f"{safe_name}-wine.log")

    
    metal_hud_line = ""
    if bottle_cfg.get("metal_hud", False):
        metal_hud_line = "export MTL_HUD_ENABLED=1\n"

    
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
    "$MNC_WINE" steam.exe {STEAM_SILENT_ARGS if silent else "-tcp"} > {shlex.quote(log_path)} 2>&1
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
    _steam_started_silent = silent
    _steam_prefix = str(prefix)
    _steam_started_ts = time.time()
    if silent:
        _ensure_steam_idle_watchdog()
    log(f"Steam launched with PID {proc.pid} (silent={silent}), log at {log_path}")

    # Optionally block until Steam is fully authenticated (API up). Required before
    # launching a Steamworks game (cs2/RE4) — otherwise SteamAPI_Init fails with
    # "Steam denied appID". A bare sleep is NOT enough; Steam can take 30-120s to
    # reach [Logged On] (cold start, content update, 2FA). We poll connection_log.txt.
    if params.get("wait_ready"):
        ready, status = _wait_steam_ready(prefix, cap_s=int(params.get("ready_cap_s", 240)))
        return {"pid": proc.pid, "log_path": log_path, "already_running": False,
                "ready": ready, "status": status}

    return {"pid": proc.pid, "log_path": log_path, "already_running": False}


def _steam_is_alive() -> bool:
    try:
        ps = subprocess.check_output(["ps", "-axo", "command"], text=True)
    except Exception:
        return False
    return any("Program Files (x86)\\Steam\\steam.exe" in line for line in ps.splitlines())


def _wait_steam_ready(prefix: str, cap_s: int = 240) -> tuple:
    """Poll until Steam is authenticated ([Logged On] in connection_log.txt) and
    steamwebhelper is up. Returns (ready: bool, status: str). Lifted from the
    proven pre-no-shim readiness poll."""
    connection_log = (Path(prefix) / "drive_c" / "Program Files (x86)" /
                      "Steam" / "logs" / "connection_log.txt")

    def _check() -> tuple:
        if not _steam_is_alive():
            return False, "steam.exe not alive yet"
        try:
            ps = subprocess.check_output(["ps", "-axo", "command"], text=True)
        except Exception:
            return False, "ps failed"
        if not any("steamwebhelper.exe" in line for line in ps.splitlines()):
            return False, "steamwebhelper.exe not spawned yet"
        if not connection_log.exists():
            return False, "connection_log.txt absent (Steam still bootstrapping)"
        try:
            with connection_log.open("rb") as f:
                try:
                    f.seek(0, 2); size = f.tell(); f.seek(max(0, size - 16384))
                except Exception:
                    pass
                tail = f.read().decode("utf-8", errors="ignore")
            if "[Logged On," in tail or "[Logged On, " in tail:
                return True, "Steam authenticated ([Logged On])"
            if "[Logging On," in tail:
                return False, "Steam in [Logging On] (auth in progress)"
            if "[Connecting," in tail:
                return False, "Steam in [Connecting] (still bootstrapping)"
            if "[Logged Off, 0, 0]" in tail:
                return False, "Steam [Logged Off] (sign in via Open Steam once)"
            return False, "connection_log present but no known state"
        except Exception as exc:
            return False, f"connection_log read failed: {exc}"

    last = ""
    # Bradar fast-path: if steam is ALREADY [Logged On] we return right away (no 5s
    # penalty). this matters coz the game-launch path now waits even when steam was
    # already up, so the common "already signed in" case must not stall the launch.
    ok0, status0 = _check()
    if ok0:
        log("Steam already authenticated ([Logged On]) — no wait needed")
        return True, status0
    for waited in range(5, cap_s + 5, 5):
        time.sleep(5)
        ok, status = _check()
        if status != last:
            log(f"Steam ready-check t={waited}s: {status}")
            last = status
        if ok:
            log(f"Steam FULLY ready after {waited}s")
            time.sleep(3)  # let the IPC pipe settle
            return True, status
        if "Logged Off" in status and waited > 60:
            log("Steam stuck [Logged Off] — cached creds invalid; user must sign in "
                "via Open Steam. Launching game anyway (SteamAPI_Init may fail).")
            return False, status
    log(f"Steam not ready after {cap_s}s — launching anyway (SteamAPI_Init may fail).")
    return False, "timeout"


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


def _download_and_run_steam_setup(prefix: str, wine: str, setup_path: Optional[str] = None) -> None:
    """Run SteamSetup.exe in the given prefix (background thread). Uses a
    user-supplied installer at `setup_path` when given (the onboarding Steam
    guide passes the file the user picked); otherwise downloads the official
    SteamSetup.exe."""
    global _setup_proc
    try:
        if setup_path and Path(setup_path).expanduser().exists():
            exe = Path(setup_path).expanduser()
            log(f"Using provided SteamSetup.exe: {exe}")
        else:
            exe = Path(tempfile.gettempdir()) / "SteamSetup.exe"
            if not exe.exists() or exe.stat().st_size < 1_000_000:
                log("Downloading SteamSetup.exe...")
                # macOS system Python ships no CA bundle -> urlretrieve dies SSL
                # CERTIFICATE_VERIFY_FAILED (the user hit this on create-bottle). curl uses the
                # macOS trust store, so try it first; fall back to an unverified urllib context.
                dl_ok = False
                try:
                    rc = subprocess.run(["/usr/bin/curl", "-fsSL", "-o", str(exe), STEAM_SETUP_URL],
                                        capture_output=True, timeout=300).returncode
                    dl_ok = (rc == 0 and exe.exists() and exe.stat().st_size > 1_000_000)
                except Exception as cexc:
                    log(f"curl download failed: {cexc}")
                if not dl_ok:
                    import ssl as _ssl
                    noverify = _ssl.create_default_context()
                    noverify.check_hostname = False
                    noverify.verify_mode = _ssl.CERT_NONE
                    with urllib.request.urlopen(STEAM_SETUP_URL, context=noverify, timeout=300) as resp:
                        exe.write_bytes(resp.read())
                log("SteamSetup.exe downloaded.")
        logf = str(Path(prefix) / "mnc-installer.log")
        log(f"Launching SteamSetup.exe in {prefix} (pre-HACK22 wine so the NSIS stub wont fault-storm; log {logf})")
        # /S = silent install (the SteamSetup GUI wizard doesnt reliably surface under wine); this
        # lands steam.exe so a later Play launchs Steam via DXMT.
        proc = _run_installer_prehack22(prefix, [str(exe), "/S"], "d3dmetal", log_path=logf)
        _setup_proc = proc
    except Exception as exc:
        log(f"Warning: failed to run SteamSetup: {exc}")


def cmd_get_setup_pid(_params: Dict[str, Any]) -> Any:
    global _setup_proc
    running = _setup_proc is not None and _setup_proc.poll() is None
    return {"running": running}


def cmd_steam_install_status(params: Dict[str, Any]) -> Any:
    """Drives the "Installing Steam…" loading screen. installed = steam.exe present (checks BOTH
    Program Files (x86)\\Steam AND Program Files\\Steam via _steam_dir, since a 32-bit installer on a
    fast-booted prefix lands Steam in the non-x86 dir). running = a SteamSetup install proc is still
    alive. The UI polls this: show the overlay til installed, or drop it if it stops runnin unfinishd."""
    prefix = params.get("prefix")
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")
    installed = (_steam_dir(prefix) / "steam.exe").exists()
    running = False
    try:
        out = subprocess.run(["pgrep", "-f", "SteamSetup"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        running = bool(out)
    except Exception:
        pass
    return {"installed": installed, "running": running}


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

    
    prefixes = _load_prefixes()
    if path_str not in prefixes:
        prefixes.append(path_str)
        _save_prefixes(prefixes)

 
    bottles = _load_bottles()
    existing = bottles.get(key, {})
    existing["name"] = name
    existing["launcher_type"] = launcher_type
    existing["default_backend"] = default_backend
    bottles[key] = existing
    _save_bottles(bottles)

   
    wine = _find_wine()
    if wine:
        env = _wine_env(path_str)
        try:
            log(f"Running wineboot -u for {path_str}")
            subprocess.run(
                [wine, "wineboot", "-u"],
                env=env,
                # backstop: gate makes this ~10s but allow the slow full install to finish
                timeout=600,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            log(f"wineboot failed: {exc}")
        # the fast wineboot skips the i386 Wow64Install -> empty syswow64. stage the 32-bit
        # subsystem now (fast clonefile) so 32-bit installers (SteamSetup + redists) run on this
        # fresh bottle insted of dying c0000135 on the pre-HACK22 installer wine.
        _stage_syswow64(path_str)
    else:
        log("Wine not found, skipping wineboot initialization")

   
    if launcher_type == "steam" and wine:
        # steam_setup_path: a user-supplied SteamSetup.exe (onboarding Steam
        # guide). When absent, _download_and_run_steam_setup fetches the official one.
        threading.Thread(
            target=_download_and_run_steam_setup,
            args=(path_str, wine, params.get("steam_setup_path")),
            daemon=True,
        ).start()

   
    if launcher_type == "epic":
        threading.Thread(target=_download_legendary_if_needed, daemon=True).start()

    return {"path": path_str}


def cmd_reorder_bottles(params: Dict[str, Any]) -> Any:
    """Save a new bottle order. `paths` is the ordered list of prefix paths."""
    paths = params.get("paths")
    if not isinstance(paths, list):
        raise ValueError("Missing 'paths' list parameter")
    
    existing = set(_resolve_key(p) for p in _load_prefixes())
    ordered = [p for p in paths if _resolve_key(p) in existing]
   
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

   
    skip_keys = {"path", "cmd", "id"}
    for k, v in params.items():
        if k not in skip_keys:
            existing[k] = v

    
    if "discord_rpc" in params:
        if params["discord_rpc"]:
            threading.Thread(target=_rpc_bridge_install_prefix, args=(path,), daemon=True).start()
        else:
            threading.Thread(target=_rpc_bridge_uninstall_prefix, args=(path,), daemon=True).start()

    bottles[key] = existing
    _save_bottles(bottles)
    return existing


_libproc = None


def _pid_executable(pid: int) -> str:
    """Real executable path of a pid via libproc's proc_pidpath. Wine's
    Windows-side processes (services.exe, winedevice.exe, the game itself)
    show a PURE Windows argv ("C:\\...") in ps — but their true binary is our
    wine loader under PORTABLE_DIR, which is the precise ownership signal.
    (Verified live: 8/8 Windows-style pids resolved to our deps path.)"""
    global _libproc
    try:
        import ctypes
        if _libproc is None:
            _libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
        buf = ctypes.create_string_buffer(4096)
        n = _libproc.proc_pidpath(pid, buf, 4096)
        return buf.value.decode() if n > 0 else ""
    except Exception:
        return ""


def _macncheese_wine_pids(extra_substrings: Optional[List[str]] = None) -> List[int]:
    """PIDs of host processes belonging to MacNCheese's Wine stack: anything
    whose command line references our portable deps dir (wine, wineserver,
    preloaders, gstreamer helpers — they all run from there) or any of the
    given extra substrings (e.g. a specific prefix path). Matching on OUR
    paths means other third-party Wine installs are never touched.
    The backend itself and the app are excluded."""
    pats = [str(PORTABLE_DIR)] + [s for s in (extra_substrings or []) if s]
    me, parent = os.getpid(), os.getppid()
    pids: List[int] = []
    try:
        out = subprocess.run(["/bin/ps", "-axo", "pid=,command="],
                             capture_output=True, text=True, timeout=10)
        for line in out.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                pid_s, cmdline = line.split(None, 1)
                pid = int(pid_s)
            except ValueError:
                continue
            if pid in (me, parent) or "backend_server.py" in cmdline:
                continue
            if ".app/Contents/MacOS/MacNCheese" in cmdline:
                continue  # the launcher app itself
            if any(p in cmdline for p in pats):
                pids.append(pid)
                continue
            # Windows-argv processes ("C:\..." / "Z:\...") are invisible to the
            # cmdline match — resolve their REAL executable instead. Other Wine
            # third-party Wine installs resolve to THEIR paths, so the
            # never-touch guarantee holds.
            if len(cmdline) > 2 and cmdline[1] == ":" and cmdline[2] == "\\":
                exe = _pid_executable(pid)
                if exe and any(p in exe for p in pats):
                    pids.append(pid)
    except Exception as exc:
        log(f"kill: ps scan failed: {exc}")
    return pids


def _kill_pids(pids: List[int], sig: int) -> int:
    sent = 0
    for pid in pids:
        try:
            os.kill(pid, sig)
            sent += 1
        except OSError:
            pass
    return sent


def cmd_kill_wineserver(params: Dict[str, Any]) -> Any:
    """Stop MacNCheese's Wine — for real. Field report (Hafliss): the old
    single graceful `wineserver -k` left hung games and other Wine builds'
    processes running, forcing users into Activity Monitor. Now:
      1. graceful `wineserver -k` for EVERY portable Wine build present,
      2. short wait,
      3. SIGTERM stragglers (matched by OUR deps/prefix paths only),
      4. SIGKILL whatever still survives.
    Returns how many were force-killed and how many remain."""
    prefix = params.get("prefix")
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")

    env = _wine_env(prefix)

    # 1) graceful shutdown on every portable Wine build that exists (each build
    # Bradar    has its own wineserver; the D3DMetal one was previously never asked).
    servers: List[str] = []
    for app in ("Wine Stable.app", "Wine Staging.app", "Wine Devel.app", "Wine D3DMetal.app"):
        cand = PORTABLE_DIR / app / "Contents" / "Resources" / "wine" / "bin" / "wineserver"
        if cand.exists():
            servers.append(str(cand))
    if not servers:
        ws = _find_wineserver()
        if ws:
            servers.append(ws)
    for ws in servers:
        try:
            subprocess.run([ws, "-k"], env=env, timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            log(f"wineserver -k timed out: {ws}")

    # 2) give graceful shutdown a moment to drain.
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if not _macncheese_wine_pids([str(prefix)]):
            break
        time.sleep(0.3)

    # 3) + 4) escalate on survivors (hung processes ignore wineserver -k).
    force_killed = 0
    survivors = _macncheese_wine_pids([str(prefix)])
    if survivors:
        log(f"kill_wineserver: escalating to SIGTERM for {len(survivors)} survivors: {survivors}")
        _kill_pids(survivors, signal.SIGTERM)
        time.sleep(1.0)
        survivors = _macncheese_wine_pids([str(prefix)])
        if survivors:
            log(f"kill_wineserver: SIGKILL for {len(survivors)} stubborn pids: {survivors}")
            force_killed = _kill_pids(survivors, signal.SIGKILL)
            time.sleep(0.5)

    remaining = _macncheese_wine_pids([str(prefix)])
    _running_games.clear()
    _launched_games.clear()
    log(f"kill_wineserver: done (force_killed={force_killed}, remaining={len(remaining)})")
    return {"force_killed": force_killed, "remaining": len(remaining)}


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


def cmd_add_manual_app(params: Dict[str, Any]) -> Any:
    # Bradar "Add Application" button -- persist a user-picked .exe as a manual app in the bottle
    # so it shows in the Applications section (cmd_scan_apps merges bottle["manual_apps"])
    prefix = params.get("prefix"); exe = params.get("exe"); name = params.get("name")
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")
    if not exe:
        raise ValueError("Missing 'exe' parameter")
    if not name:
        name = Path(exe).stem
    key = _resolve_key(prefix)
    bottles = _load_bottles()
    bottle = bottles.get(key, {})
    manual: List[Dict[str, str]] = list(bottle.get("manual_apps", []))
    if any(m.get("exe") == exe for m in manual):
        return manual
    manual.append({"name": name, "exe": exe, "args": params.get("args", "")})
    bottle["manual_apps"] = manual
    bottles[key] = bottle
    _save_bottles(bottles)
    return manual


def cmd_remove_manual_app(params: Dict[str, Any]) -> Any:
    prefix = params.get("prefix"); exe = params.get("exe")
    if not prefix or not exe:
        raise ValueError("Missing 'prefix'/'exe' parameter")
    key = _resolve_key(prefix)
    bottles = _load_bottles()
    bottle = bottles.get(key, {})
    manual = [m for m in bottle.get("manual_apps", []) if m.get("exe") != exe]
    bottle["manual_apps"] = manual
    bottles[key] = bottle
    _save_bottles(bottles)
    return manual


def cmd_remove_manual_game(params: Dict[str, Any]) -> Any:
    """Remove a manually-added (non-Steam) game from a bottle's list ONLY — the
    game's files on disk are left untouched. Matched by exe path (the same key
    add dedups on). Returns the updated manual_games list."""
    prefix = params.get("prefix")
    exe = params.get("exe")
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")
    if not exe:
        raise ValueError("Missing 'exe' parameter")

    key = _resolve_key(prefix)
    bottles = _load_bottles()
    bottle = bottles.get(key, {})
    manual: List[Dict[str, str]] = list(bottle.get("manual_games", []))

    new_manual = [m for m in manual if m.get("exe") != exe]
    if len(new_manual) == len(manual):
        return manual  # nothing matched; leave the list as-is

    bottle["manual_games"] = new_manual
    bottles[key] = bottle
    _save_bottles(bottles)
    log(f"remove_manual_game: removed {exe} from bottle {key} (files left on disk)")
    return new_manual


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
        [wine, "wineboot", "-u"], env=env, timeout=600,
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
        [wine, "wineboot", "-u"], env=env, timeout=600,
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
    arg_parts = shlex.split(args) if args else []
    if exe_path.suffix.lower() == ".msi":
        # Windows Installer packages are run through msiexec.
        tail = ["msiexec", "/i", str(exe_path)] + arg_parts
    else:
        tail = [str(exe_path)] + arg_parts
    # Installers run on the PRE-HACK22 wine: 32-bit NSIS/Burn stubs (SteamSetup n
    # friends) jump to garbage n fault-storm at 100% CPU under the unified HACK22 wine,
    # so from the UI they look like they never launch + write no logs. tee wine output
    # to a log in the bottle so "Run Installer" isnt a silent black box.
    logf = str(Path(prefix) / "mnc-installer.log")
    proc = _run_installer_prehack22(str(prefix), tail, "d3dmetal", log_path=logf)
    _running_games[proc.pid] = proc
    log(f"run_exe: {tail} -> pid {proc.pid}; log {logf}")
    return {"pid": proc.pid}


def cmd_uninstall_app(params: Dict[str, Any]) -> Any:
    """Uninstall a Windows application from a bottle.

    Prefers the app's own uninstaller (``unins000.exe`` / ``uninstall.exe`` and
    friends) found next to the executable. If none exists, falls back to Wine's
    Add/Remove Programs control panel so the user can pick the entry manually.
    """
    prefix = params.get("prefix")
    exe = params.get("exe")
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")
    if not exe:
        raise ValueError("Missing 'exe' parameter")
    wine = _find_wine()
    if not wine:
        raise FileNotFoundError("Wine not found")

    exe_path = Path(exe)
    app_dir = exe_path.parent

    # Look for a dedicated uninstaller next to the app, then one level up.
    uninstaller: Optional[Path] = None
    search_dirs = [app_dir]
    if app_dir.parent != app_dir:
        search_dirs.append(app_dir.parent)
    for d in search_dirs:
        if not d.exists():
            continue
        try:
            children = sorted(d.iterdir(), key=lambda c: c.name.lower())
        except Exception:
            continue
        for child in children:
            if not child.is_file():
                continue
            low = child.name.lower()
            if low.endswith(".exe") and (low.startswith("unins") or "uninstall" in low):
                uninstaller = child
                break
        if uninstaller:
            break

    if uninstaller:
        tail = [str(uninstaller)]
        method = "uninstaller"
    else:
        # No bundled uninstaller — open Wine's Add/Remove Programs dialog.
        tail = ["uninstaller"]
        method = "control_panel"

    # uninstallers r the same 32-bit NSIS/Burn class as installers, so run them on the pre-HACK22
    # wine (which also stages the 32-bit subsystem) insted of the unified HACK22 wine they'd
    # fault-storm on. output tees to a log so an uninstall isnt a silent black box.
    logf = str(Path(prefix) / "mnc-uninstall.log")
    log(f"uninstall_app ({method}): {tail}")
    proc = _run_installer_prehack22(str(prefix), tail, "d3dmetal", log_path=logf)
    _running_games[proc.pid] = proc
    return {"pid": proc.pid, "method": method}


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
        {"id": BACKEND_WINE, "label": "Wine builtin", "available": True},
        {"id": BACKEND_DXVK, "label": "DXVK (D3D11→Vulkan)", "available": _dxvk_available()},
        {"id": BACKEND_VKD3D, "label": "VKD3D-Proton (D3D12)", "available": _vkd3d_available()},
        {"id": BACKEND_DXMT, "label": "DXMT (experimental)", "available": _dxmt_available()},
        # Bradar VR = openxr-DXMT + wineopenxr + oxrsys streaming runtime. always shown so games
        # can pick it (the openxr d3d DLLs ride w/ the unified wine); install the runtime via Settings -> VR
        {"id": "vr", "label": "VR (OpenXR)", "available": True},
        {"id": BACKEND_D3DMETAL3, "label": "D3DMetal (injection, recommended)", "available": _d3dmetal3_available()},
        {"id": BACKEND_WINE_DEVEL, "label": "Wine Devel (OpenGL/SDL3, e.g. Mewgenics)", "available": _find_wine_devel() is not None},
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



_github_cache: Dict[str, Any] = {}
_GITHUB_CACHE_TTL = 3600  

_steam_cache: Dict[str, Any] = {}
_STEAM_CACHE_TTL = 24 * 3600  


def _fetch_latest_github_release(owner: str, repo: str) -> Optional[Dict[str, Any]]:
    """Fetch latest release info from GitHub API, with 1-hour cache."""
    cache_key = f"{owner}/{repo}"
    cached = _github_cache.get(cache_key)
    if cached and (time.time() - cached[0]) < _GITHUB_CACHE_TTL:
        return cached[1]
    try:
        url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        # System curl, NOT urllib: framework Pythons without CA certs fail with
        # SSL CERTIFICATE_VERIFY_FAILED; curl uses the macOS trust store.
        out = subprocess.run(
            ["/usr/bin/curl", "-fsSL", "--max-time", "15",
             "-H", "User-Agent: MacNCheese/1.0", url],
            capture_output=True, text=True,
        )
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout)
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


def _fetch_steam_appdetails(appid: str) -> Optional[Dict[str, Any]]:
    """Fetch + cache the Steam store appdetails `data` blob for an app id.

    Uses system curl, NOT urllib: framework Pythons without CA certs fail with
    SSL CERTIFICATE_VERIFY_FAILED on store.steampowered.com (that's why the
    description previously came back empty); curl uses the macOS trust store."""
    appid = str(appid).strip()
    if not appid.isdigit():
        return None

    cache_key = f"steam_appdetails/{appid}"
    cached = _steam_cache.get(cache_key)
    if cached and (time.time() - cached[0]) < _STEAM_CACHE_TTL:
        return cached[1]

    try:
        url = f"https://store.steampowered.com/api/appdetails?appids={appid}&l=en&cc=us"
        out = subprocess.run(
            ["/usr/bin/curl", "-fsSL", "--max-time", "15",
             "-H", "User-Agent: MacNCheese/1.0", url],
            capture_output=True, text=True,
        )
        if out.returncode != 0 or not out.stdout.strip():
            _steam_cache[cache_key] = (time.time(), None)
            return None
        payload = json.loads(out.stdout)
        app_data = payload.get(appid, {})
        if not app_data.get("success"):
            _steam_cache[cache_key] = (time.time(), None)
            return None
        data = app_data.get("data", {}) or {}
        _steam_cache[cache_key] = (time.time(), data)
        return data
    except Exception as exc:
        log(f"Failed to fetch Steam appdetails for {appid}: {exc}")
        return None


def _fetch_steam_description(appid: str) -> Optional[str]:
    """Steam store extended description for an app id (HTML stripped to text)."""
    data = _fetch_steam_appdetails(appid)
    if not data:
        return None
    raw_html = (data.get("detailed_description")
                or data.get("about_the_game")
                or data.get("short_description") or "")
    description = _steam_html_to_text(raw_html)
    return description or None


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
    has_wine_devel = _find_wine_devel() is not None
    wine_version = _get_wine_version()
    return {
        "has_tools": has_tools,
        "has_wine": has_wine_stable or has_wine_staging or has_wine_devel or _unified_available(),
        "has_wine_stable": has_wine_stable,
        "has_wine_staging": has_wine_staging,
        "has_wine_devel": has_wine_devel,
        "has_mesa": _mesa_available(),
        "has_dxvk64": _dxvk_available(),
        "has_dxvk32": has_dxvk32,
        "has_dxmt": _dxmt_available(),
        "has_dxmt_openxr": _dxmt_openxr_available(),
        "has_gptk_dlls": _gptk_dlls_available(),
        "has_d3dmetal3": _d3dmetal3_available(),
        "has_wine_d3dmetal": _wine_d3dmetal_installed(),
        "has_wine_unified": _unified_available(),
        "has_vkd3d": _vkd3d_available(),
        "wine_version": wine_version,
        "has_rpc_bridge": _rpc_bridge_available(),
        "has_wineopenxr": _wineopenxr_available(),
        "has_monado_runtime": _monado_runtime_available(),
    }


def cmd_detect_wine(params: Dict[str, Any]) -> Any:
    """Probe the actual installed Wine builds on disk and report each one with
    its real --version string and binary path. Drives the Bottle tab's Wine
    selector so it reflects what's genuinely installed instead of a hardcoded
    list. The selectable preferences are stable / staging / auto (what
    _find_wine_for_bottle honours); devel/d3dmetal are reported as informational
    extras since they're chosen via the graphics backend, not wine_binary."""
    variants: List[Dict[str, Any]] = []
    for vid, label, selectable, finder in (
        ("stable", "Wine Stable", True, _find_wine_stable),
        ("staging", "Wine Staging", True, _find_wine_staging),
        ("devel", "Wine Devel", True, _find_wine_devel),
    ):
        path = finder()
        variants.append({
            "id": vid,
            "label": label,
            "selectable": selectable,
            "installed": path is not None,
            "path": path or "",
            "version": _get_wine_version(path) if path else None,
        })

    # Bradar The unified wine is the default engine (Steam via DXMT + games on the chosen
    # backend). It isn't a wine_binary pref so report it as an informational extra.
    ubt = _unified_build_dir()
    variants.append({
        "id": "unified",
        "label": "Wine Unified",
        "selectable": False,
        "installed": ubt is not None,
        "path": str(ubt / "wine") if ubt else "",
        "version": _get_wine_version(str(ubt / "wine")) if ubt else None,
    })

    # What "Auto" actually resolves to right now, so the UI can say e.g.
    # "Auto → Wine Stable (wine-9.0)".
    auto_path = _find_wine_for_bottle("auto")
    auto_id = None
    if auto_path:
        for v in variants:
            if v["path"] and v["path"] == auto_path:
                auto_id = v["id"]
                break

    return {
        "variants": variants,
        "auto_resolved_id": auto_id,
        "auto_resolved_path": auto_path or "",
        "auto_resolved_version": _get_wine_version(auto_path) if auto_path else None,
    }


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

    steam_dir = _steam_dir(prefix_path)
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




def _pe_rva_to_offset(data: bytes, rva: int) -> int:
    """Convert a PE RVA to a file offset by walking the section table."""
   
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
 
    num_sections = struct.unpack_from("<H", data, pe_off + 6)[0]
    opt_size = struct.unpack_from("<H", data, pe_off + 20)[0]
    
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

        if data[:2] != b"MZ":
            return None
        pe_off = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe_off:pe_off+4] != b"PE\x00\x00":
            return None

        
        opt_magic = struct.unpack_from("<H", data, pe_off + 24)[0]
       
        dd_off = pe_off + 24 + (112 if opt_magic == 0x20B else 96)
        rsrc_rva = struct.unpack_from("<I", data, dd_off + 2 * 8)[0]  # entry [2] = resources
        if rsrc_rva == 0:
            return None

        rsrc_base = _pe_rva_to_offset(data, rsrc_rva)

        
        grp_ptr = _pe_rsrc_find(data, rsrc_base, RT_GROUP_ICON)
        ico_ptr = _pe_rsrc_find(data, rsrc_base, RT_ICON)
        if grp_ptr is None or ico_ptr is None:
            return None

        
        grp_dir = rsrc_base + (grp_ptr & 0x7FFFFFFF)
        ico_dir = rsrc_base + (ico_ptr & 0x7FFFFFFF)

       
        ico_named = struct.unpack_from("<H", data, ico_dir + 12)[0]
        ico_ided  = struct.unpack_from("<H", data, ico_dir + 14)[0]
        icons_by_id: Dict[int, int] = {}
        for i in range(ico_named + ico_ided):
            e = ico_dir + 16 + i * 8
            icon_id  = struct.unpack_from("<I", data, e)[0]
            sub_ptr  = struct.unpack_from("<I", data, e + 4)[0]
            if icon_id & 0x80000000:
                continue  # skip named
            
            lang_dir = rsrc_base + (sub_ptr & 0x7FFFFFFF)
            lang_ptr = struct.unpack_from("<I", data, lang_dir + 16 + 4)[0]
            data_entry_off = rsrc_base + (lang_ptr & 0x7FFFFFFF)
            icons_by_id[icon_id] = data_entry_off

       
        grp_named = struct.unpack_from("<H", data, grp_dir + 12)[0]
        grp_ided  = struct.unpack_from("<H", data, grp_dir + 14)[0]
        if grp_named + grp_ided == 0:
            return None
        first_grp_e = grp_dir + 16  
        grp_sub_ptr = struct.unpack_from("<I", data, first_grp_e + 4)[0]
      
        glang_dir = rsrc_base + (grp_sub_ptr & 0x7FFFFFFF)
        glang_ptr = struct.unpack_from("<I", data, glang_dir + 16 + 4)[0]
        gdata_entry_off = rsrc_base + (glang_ptr & 0x7FFFFFFF)
        grp_rva  = struct.unpack_from("<I", data, gdata_entry_off)[0]
        grp_size = struct.unpack_from("<I", data, gdata_entry_off + 4)[0]
        grp_file_off = _pe_rva_to_offset(data, grp_rva)
        grp_data = data[grp_file_off: grp_file_off + grp_size]

        
        count = struct.unpack_from("<HHH", grp_data, 0)[2]
        GRPICONDIRENTRY_SIZE = 14
        icon_items = []  
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

        
        icon_items.sort(key=lambda x: x[0], reverse=True)
        n = len(icon_items)
        buf = io.BytesIO()
        buf.write(struct.pack("<HHH", 0, 1, n))  # ICONDIR
        data_offset = 6 + n * 16
        for _, _, entry12, raw in icon_items:
           
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
    global _last_game_exit_ts
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
    if dead_pids and not alive:
        # Last game just exited — anchors the background-Steam idle timer.
        _last_game_exit_ts = time.time()

    return alive


def _stop_background_steam(reason: str) -> None:
    """Stop the silent Steam WE started, plus the prefix's lingering Wine
    services. killpg reaches the whole bash→zsh→wine tree because the launch
    used start_new_session=True."""
    global _steam_process
    proc = _steam_process
    if proc is None:
        return
    log(f"power: stopping background Steam (pid {proc.pid}) — {reason}")
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except OSError:
        pass
    time.sleep(3.0)
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    # services.exe & friends idle in the prefix too — drain them as well.
    ws = _find_wineserver()
    if ws and _steam_prefix:
        try:
            subprocess.run([ws, "-k"], env=_wine_env(_steam_prefix), timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    _steam_process = None


def _ensure_steam_idle_watchdog() -> None:
    """Power saver (field report: idle background Steam at ~2700 energy impact):
    a silent-launched Steam has no reason to outlive the games it served — stop
    it STEAM_IDLE_GRACE_S after the last game exits. User-visible Steam ("Open
    Steam" / custom launchers) is never auto-stopped."""
    global _steam_watchdog_started
    if _steam_watchdog_started:
        return
    _steam_watchdog_started = True

    def _loop() -> None:
        while True:
            time.sleep(30)
            try:
                if not _auto_stop_steam or not _steam_started_silent:
                    continue
                proc = _steam_process
                if proc is None or proc.poll() is not None:
                    continue
                if any(p.poll() is None for p in _running_games.values()):
                    continue
                anchor = max(_steam_started_ts, _last_game_exit_ts)
                if time.time() - anchor >= STEAM_IDLE_GRACE_S:
                    _stop_background_steam(
                        f"idle for {STEAM_IDLE_GRACE_S // 60} min with no game running"
                    )
            except Exception as exc:
                log(f"power: steam watchdog error: {exc}")

    threading.Thread(target=_loop, daemon=True, name="steam-idle-watchdog").start()


def cmd_get_steam_running(_params: Dict[str, Any]) -> Any:
    global _steam_process
    running = _steam_process is not None and _steam_process.poll() is None
    return {"running": running}


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

            elif action == "steam_simple_fix":
                # "Steam not launching?" one-click fix: back up the current Wine
                # Stable, download/install the latest MacNCheese Wine, then re-run
                # wineboot -u so the prefix is rebuilt against the fresh Wine.
                stable_app = PORTABLE_DIR / "Wine Stable.app"
                if stable_app.exists():
                    backup = _diagnostic_backup_path(stable_app)
                    _job_append(job, f"Moving {stable_app} to {backup}")
                    shutil.move(str(stable_app), str(backup))
                    _remove_version_marker("wine_stable")
                _job_append(job, "=== Downloading the latest MacNCheese Wine ===")
                if _run_installer_action_for_repair(job, "install_wine", prefix) != 0:
                    job["failed"] = True
                else:
                    wine = _find_wine()
                    if not wine:
                        raise FileNotFoundError("Wine not found after install")
                    Path(prefix).expanduser().mkdir(parents=True, exist_ok=True)
                    _job_append(job, "=== Running wineboot -u on the bottle ===")
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
                steam_dir = _steam_dir(prefix)
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
        # installer.sh lives in Resources; point its bundled-pack lookups there.
        env = {**os.environ, "MNC_SUDOLESS": "1",
               "RESOURCES_DIR": str(Path(installer_path).parent)}
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
            "update_available": False,
            "epic_app_name": app_name,
        })
    games.sort(key=lambda g: (0 if g["is_installed"] else 1, g["name"].lower()))
    return games


def _legendary_updates_from_metadata(prefix: str) -> set:
    """Compare installed versions against legendary's cached metadata (no network).
    Returns app_names that have a newer version available."""
    installed = _read_installed_here(prefix)
    config_dir = _legendary_config_dir(prefix)
    updates: set = set()
    for app_name, info in installed.items():
        installed_version = info.get("version", "")
        if not installed_version:
            continue
        # legendary stores per-game metadata in <config_dir>/metadata/<app_name>.json
        # after `legendary list` runs; fall back to the global config dir.
        for meta_dir in [config_dir / "metadata", Path.home() / ".config" / "legendary" / "metadata"]:
            meta_path = meta_dir / f"{app_name}.json"
            if not meta_path.exists():
                continue
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                available_version = (
                    meta.get("asset_infos", {})
                        .get("Windows", {})
                        .get("build_version", "")
                )
                if available_version and available_version != installed_version:
                    updates.add(app_name)
            except Exception:
                pass
            break  # stop at first found metadata
    return updates


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

        # Phase 3 — detect updates by comparing installed version against metadata on disk.
        # `legendary list` (Phase 2) already refreshed the metadata cache, so this is instant.
        updates_set = _legendary_updates_from_metadata(prefix)
        if updates_set:
            for g in games:
                if g.get("epic_app_name") in updates_set:
                    g["update_available"] = True
            log(f"legendary: {len(updates_set)} update(s) available for {prefix}")

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
                "paused": False,
                "prefix": prefix,
            }
        for i, (app_name, prefix) in enumerate(_legendary_download_queue):
            result[app_name] = {
                "progress": 0.0,
                "queued": True,
                "queue_position": i + 1,
                "paused": False,
                "prefix": prefix,
            }
    for app_name, prefix in _legendary_paused.items():
        log_path = str(LEGENDARY_DIR / f"install_{app_name}.log")
        result[app_name] = {
            "progress": read_progress(log_path),
            "queued": False,
            "queue_position": 0,
            "paused": True,
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
    verbose_debug = bool(params.get("debug", False))

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
    env = _apply_backend_env(env, backend, verbose_debug)
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

    _register_running_game(proc, enable_game_mode=params.get("game_mode", True))

    # MacNCheese-level Discord presence for Epic launches. Prefer the real
    # title passed from the UI; fall back to the Epic app_name (codename).
    try:
        _epic_cfg = _load_bottles().get(_resolve_key(prefix), {})
    except Exception:
        _epic_cfg = {}
    if _epic_cfg.get("discord_rpc", True):
        _discord_presence_for_launch(proc, "", params.get("game_name", "") or app_name)

    return {"pid": proc.pid}


# ---------------------------------------------------------------------------
# Per-game config (esync, msync, backend choice, etc.)
# Stored in <prefix>/.macncheese_games.json keyed by appid.
# ---------------------------------------------------------------------------

def _game_cfg_path(prefix: str) -> Path:
    return Path(prefix).expanduser().resolve() / ".macncheese_games.json"


def cmd_get_game_config(params: Dict[str, Any]) -> Any:
    prefix = params.get("prefix", "").strip()
    appid = params.get("appid", "").strip()
    if not prefix or not appid:
        return {}
    return _read_json(_game_cfg_path(prefix), {}).get(appid, {})


def cmd_set_game_config(params: Dict[str, Any]) -> Any:
    prefix = params.get("prefix", "").strip()
    appid = params.get("appid", "").strip()
    if not prefix or not appid:
        raise ValueError("Missing prefix or appid")
    skip = {"prefix", "appid", "cmd", "id"}
    cfgs = _read_json(_game_cfg_path(prefix), {})
    entry = cfgs.get(appid, {})
    for k, v in params.items():
        if k not in skip:
            entry[k] = v
    cfgs[appid] = entry
    _write_json(_game_cfg_path(prefix), cfgs)
    return entry


# ---------------------------------------------------------------------------
# Game ordering (custom sort order per bottle)
# Stored as "game_order" list in the bottle's entry in bottles.json.
# ---------------------------------------------------------------------------

def cmd_get_game_order(params: Dict[str, Any]) -> Any:
    prefix = params.get("prefix", "").strip()
    if not prefix:
        return []
    key = _resolve_key(prefix)
    return _load_bottles().get(key, {}).get("game_order", [])


def cmd_set_game_order(params: Dict[str, Any]) -> Any:
    prefix = params.get("prefix", "").strip()
    order = params.get("order", [])
    if not prefix:
        raise ValueError("Missing prefix")
    key = _resolve_key(prefix)
    bottles = _load_bottles()
    existing = bottles.get(key, {})
    existing["game_order"] = order
    bottles[key] = existing
    _save_bottles(bottles)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Legendary pause / resume
# ---------------------------------------------------------------------------

def cmd_legendary_pause_install(params: Dict[str, Any]) -> Any:
    app_name = params.get("app_name", "").strip()
    # Kill active process if running
    entry = _legendary_installs.pop(app_name, None)
    if entry:
        proc, log_fh, _log_path, prefix = entry
        try:
            proc.terminate()
            log_fh.close()
        except Exception:
            pass
        _legendary_paused[app_name] = prefix
        return {"ok": True}
    # Remove from queue if waiting
    with _legendary_queue_lock:
        for i, (qapp, qprefix) in enumerate(_legendary_download_queue):
            if qapp == app_name:
                _legendary_download_queue.pop(i)
                _legendary_paused[app_name] = qprefix
                return {"ok": True}
    return {"ok": False, "error": "Not found"}


def cmd_legendary_resume_install(params: Dict[str, Any]) -> Any:
    global _legendary_queue_worker_running
    app_name = params.get("app_name", "").strip()
    prefix = _legendary_paused.pop(app_name, None) or params.get("prefix", "").strip()
    if not prefix:
        raise ValueError("Unknown app_name or missing prefix")
    with _legendary_queue_lock:
        _legendary_download_queue.append((app_name, prefix))
        if not _legendary_queue_worker_running:
            _legendary_queue_worker_running = True
            threading.Thread(target=_legendary_queue_worker, daemon=True).start()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Command dispatch table
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Application self-update — download the newest DMG from mont127/MacNdCheese
# releases, extract the .app, codesign it, and swap it in for the running app.
# ---------------------------------------------------------------------------

APP_UPDATE_REPO = ("mont127", "MacNdCheese")


def _find_dmg_asset(release: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """First .dmg asset in a GitHub release JSON."""
    for a in (release or {}).get("assets", []) or []:
        name = a.get("name", "")
        if name.lower().endswith(".dmg") and a.get("browser_download_url"):
            return {"name": name, "url": a["browser_download_url"], "size": a.get("size", 0)}
    return None


def _version_tuple(v: str) -> Tuple[int, ...]:
    parts = []
    for p in str(v or "").strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def _version_newer(latest: str, current: str) -> bool:
    return _version_tuple(latest) > _version_tuple(current)


def cmd_check_app_update(params: Dict[str, Any]) -> Any:
    """Check mont127/MacNdCheese for a newer release than current_version."""
    current = str(params.get("current_version", "")).strip()
    rel = _fetch_latest_github_release(*APP_UPDATE_REPO)
    if not rel:
        return {"available": False, "error": "Could not reach GitHub releases"}
    tag = rel.get("tag_name", "")
    dmg = _find_dmg_asset(rel)
    available = bool(tag) and bool(dmg) and (not current or _version_newer(tag, current))
    return {
        "available": available,
        "latest": tag,
        "current": current,
        "dmg_url": (dmg or {}).get("url", ""),
        "dmg_name": (dmg or {}).get("name", ""),
        "html_url": rel.get("html_url", ""),
        "notes": (rel.get("body", "") or "")[:4000],
    }


def _app_update_swap_script(app_pid: int, staging_app: str, target_app: str, workdir: str) -> str:
    """Detached swapper: wait for the running app to quit, replace it with the
    freshly-downloaded+signed app, re-sign in place, relaunch, and clean up.
    Runs in its own session so it survives the app (and this backend) exiting."""
    return (
        "#!/bin/bash\n"
        f"PID={int(app_pid)}\n"
        f"STAGING={shlex.quote(staging_app)}\n"
        f"TARGET={shlex.quote(target_app)}\n"
        f"WORK={shlex.quote(workdir)}\n"
        '# Wait (max ~60s) for the running app to exit so we can replace it.\n'
        'for _ in $(seq 1 120); do kill -0 "$PID" 2>/dev/null || break; sleep 0.5; done\n'
        'sleep 1\n'
        '/bin/rm -rf "$TARGET.mncold" 2>/dev/null\n'
        '/bin/mv "$TARGET" "$TARGET.mncold" 2>/dev/null || /bin/rm -rf "$TARGET"\n'
        'if /usr/bin/ditto "$STAGING" "$TARGET"; then\n'
        '  /usr/bin/xattr -cr "$TARGET" 2>/dev/null\n'
        '  /usr/bin/codesign --force --deep --sign - "$TARGET" 2>/dev/null\n'
        '  /bin/rm -rf "$TARGET.mncold" 2>/dev/null\n'
        'else\n'
        '  # rollback on failure\n'
        '  /bin/rm -rf "$TARGET" 2>/dev/null\n'
        '  /bin/mv "$TARGET.mncold" "$TARGET" 2>/dev/null\n'
        'fi\n'
        '/usr/bin/open "$TARGET"\n'
        '/bin/rm -rf "$WORK" 2>/dev/null\n'
    )


def cmd_apply_app_update(params: Dict[str, Any]) -> Any:
    """Download the newest DMG, extract+codesign the .app, and hand off to a
    detached swapper that replaces the running app once it quits. Job-based
    progress (poll via get_install_progress)."""
    app_path = str(params.get("app_path", "")).strip()
    app_pid = int(params.get("app_pid", 0) or 0)
    dmg_url = str(params.get("dmg_url", "")).strip()

    if not app_path or not Path(app_path).exists():
        raise ValueError("app_path missing or does not exist")
    if app_path.startswith("/Volumes/"):
        raise RuntimeError("The app is running from a read-only disk image. Drag "
                           "“MacNdCheese Launcher” to /Applications, then update.")
    if not os.access(str(Path(app_path).parent), os.W_OK):
        raise RuntimeError(f"No write permission to {Path(app_path).parent}. Move the "
                           "app to /Applications (or your user folder) and retry.")

    import uuid
    job_id = str(uuid.uuid4())
    job: Dict[str, Any] = {"lines": [], "done": False, "failed": False, "current": "", "ready": False}
    _install_jobs[job_id] = job

    def emit(msg: str) -> None:
        job["lines"].append(msg)
        log(f"app-update: {msg}")

    def _run() -> None:
        mount = ""
        try:
            url = dmg_url
            if not url:
                job["current"] = "Checking release"
                emit("Fetching latest release from GitHub…")
                rel = _fetch_latest_github_release(*APP_UPDATE_REPO)
                if not rel:
                    raise RuntimeError("Could not reach GitHub releases")
                dmg = _find_dmg_asset(rel)
                if not dmg:
                    raise RuntimeError("Latest release has no .dmg asset")
                url = dmg["url"]
                emit(f"Latest: {rel.get('tag_name','?')} ({dmg['name']})")

            work = Path(tempfile.mkdtemp(prefix="mnc-update-"))
            dmg_path = work / "update.dmg"
            job["current"] = "Downloading"
            emit(f"Downloading {url}")
            # System curl, NOT urllib: framework Pythons without CA certs fail
            # with SSL CERTIFICATE_VERIFY_FAILED (seen in the wild on the v9.0.0
            # update); curl uses the macOS trust store. Progress is emitted by
            # polling the partial file's size.
            proc = subprocess.Popen(
                ["/usr/bin/curl", "-fL", "--retry", "3", "-A", "MacNCheese/1.0",
                 "-o", str(dmg_path), url],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            )
            last = 0
            while proc.poll() is None:
                time.sleep(1)
                got = dmg_path.stat().st_size if dmg_path.exists() else 0
                if got - last >= 25 * 1024 * 1024:
                    last = got
                    emit(f"  {got // (1024 * 1024)} MiB")
            if proc.returncode != 0:
                err = ((proc.stderr.read() if proc.stderr else "") or "").strip()[-300:]
                raise RuntimeError(f"download failed: {err or f'curl exit {proc.returncode}'}")
            emit(f"Downloaded {dmg_path.stat().st_size // (1024 * 1024)} MiB")

            job["current"] = "Mounting"
            emit("Mounting DMG…")
            att = subprocess.run(
                ["hdiutil", "attach", str(dmg_path), "-nobrowse", "-noverify", "-readonly"],
                capture_output=True, text=True,
            )
            if att.returncode != 0:
                raise RuntimeError(f"hdiutil attach failed: {att.stderr.strip()}")
            for line in att.stdout.splitlines():
                idx = line.find("/Volumes/")
                if idx != -1:
                    mount = line[idx:].strip()
            if not mount or not Path(mount).exists():
                raise RuntimeError("Could not determine DMG mount point")

            apps = sorted(Path(mount).glob("*.app"))
            if not apps:
                raise RuntimeError("No .app found inside the DMG")
            src_app = apps[0]
            emit(f"Found {src_app.name}")

            job["current"] = "Extracting"
            staging = work / src_app.name
            emit("Copying app out of the DMG…")
            d = subprocess.run(["ditto", str(src_app), str(staging)], capture_output=True, text=True)
            if d.returncode != 0:
                raise RuntimeError(f"ditto failed: {d.stderr.strip()}")

            subprocess.run(["hdiutil", "detach", mount, "-quiet"], capture_output=True)
            mount = ""

            job["current"] = "Codesigning"
            emit("Codesigning the new app (ad-hoc)…")
            subprocess.run(["xattr", "-cr", str(staging)], capture_output=True)
            cs = subprocess.run(
                ["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(staging)],
                capture_output=True, text=True,
            )
            if cs.returncode != 0:
                emit(f"  codesign warning: {cs.stderr.strip()}")

            swap = work / "swap.sh"
            swap.write_text(_app_update_swap_script(app_pid, str(staging), app_path, str(work)))
            os.chmod(swap, 0o755)
            emit("Ready. Quit to install — the app will relaunch on the new version.")
            subprocess.Popen(
                ["/bin/bash", str(swap)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            job["ready"] = True
            job["current"] = ""
            job["done"] = True
        except Exception as exc:
            if mount:
                subprocess.run(["hdiutil", "detach", mount, "-force", "-quiet"], capture_output=True)
            emit(f"ERROR: {exc}")
            job["failed"] = True
            job["done"] = True

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id}


def _defualt_inpit_info() -> Dict[str, Any]:
    # Bradar this function look at the microfone of the mac and if it is potato quality we warn the user bradar
    infu = {"name": "", "rate": 0, "transport": "", "warn": False, "message": "", "suggest": ""}
    try:
        # Bradar we ask the system profiler what audio device is connected bradar
        aut = subprocess.run(["system_profiler", "SPAudioDataType", "-json"],
                             capture_output=True, text=True, timeout=12).stdout
        dataa = json.loads(aut)
    except Exception as exc:
        log(f"mic-guard: system_profiler failed: {exc}")
        return infu

    # Bradar what is this comment delet this
    def find_itmes(o):
        # Bradar this one dig inside the json to find the device list and it call himself again and again bradar
        if isinstance(o, dict):
            if "_items" in o:
                return o["_items"]
            for v in o.values():
                r = find_itmes(v)
                if r:
                    return r
        elif isinstance(o, list):
            for x in o:
                r = find_itmes(x)
                if r:
                    return r
        return None

    itmes = find_itmes(dataa) or []
    defualt = None
    sugest = ""
    # Bradar now we go one by one on every device bradar
    for it in itmes:
        if not isinstance(it, dict):
            continue
        is_inpit = it.get("coreaudio_device_input") or it.get("coreaudio_input_source")
        rait = int(it.get("coreaudio_device_srate") or 0)
        if it.get("coreaudio_default_audio_input_device") == "spaudio_yes":
            # Bradar this is the one the mac is using right now bradar
            defualt = it
        elif is_inpit and rait >= 48000 and not sugest \
                and "bluetooth" not in str(it.get("coreaudio_device_transport", "")):
            # Bradar this device is 48 kHz very good so we remember it to suggest bradar
            sugest = it.get("_name", "")
    if not defualt:
        return infu

    # Bradar what is this comment delet this
    nem = defualt.get("_name", "")
    rait = int(defualt.get("coreaudio_device_srate") or 0)
    transpurt = str(defualt.get("coreaudio_device_transport", "")).replace("coreaudio_device_type_", "")
    infu.update({"name": nem, "rate": rait, "transport": transpurt, "suggest": sugest})
    # Bradar if the rate is small like under 44100 the mic is potato so we must warn bradar
    if rait and rait < 44100:
        infu["warn"] = True
        mesaj = f'Your mic "{nem}" is running at {rait // 1000} kHz'
        if "bluetooth" in transpurt:
            mesaj += " (Bluetooth HFP). AirPods and BT headsets drop to 24 kHz mono when used as a mic so voice sounds muffled and laggy in games."
        else:
            mesaj += " which is low quality for voice."
        mesaj += (f' Switch to "{sugest}" (48 kHz) in System Settings > Sound > Input.'
                if sugest else " Pick a 48 kHz mic in System Settings > Sound > Input.")
        infu["message"] = mesaj
    return infu


def cmd_chek_audio_inpit(params: Dict[str, Any]) -> Any:
    # Bradar the app is asking how is the microfone so we go and check it bradar
    return _defualt_inpit_info()


def cmd_open_sund_setings(params: Dict[str, Any]) -> Any:
    # Bradar we open the sound setting for the user so he can change the microfone bradar very nice
    try:
        subprocess.run(["open", "x-apple.systempreferences:com.apple.Sound-Settings.extension"], timeout=10)
        return {"ok": True}
    except Exception as exc:
        # Bradar what is this comment delet this
        return {"ok": False, "error": str(exc)}


COMMANDS: Dict[str, Any] = {
    "list_bottles": cmd_list_bottles,
    "scan_games": cmd_scan_games,
    "scan_apps": cmd_scan_apps,
    "get_steam_description": cmd_get_steam_description,
    "get_steam_media": cmd_get_steam_media,
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
    "uninstall_app": cmd_uninstall_app,
    "open_prefix_folder": cmd_open_prefix_folder,
    "get_status": cmd_get_status,
    "add_manual_game": cmd_add_manual_game,
    "add_manual_app": cmd_add_manual_app,
    "remove_manual_app": cmd_remove_manual_app,
    "remove_manual_game": cmd_remove_manual_game,
    "detect_exes": cmd_detect_exes,
    "list_backends": cmd_list_backends,
    "get_components_status": cmd_get_components_status,
    "check_audio_input": cmd_chek_audio_inpit,
    "open_sound_settings": cmd_open_sund_setings,
    "detect_wine": cmd_detect_wine,
    "get_update_info": cmd_get_update_info,
    "check_app_update": cmd_check_app_update,
    "apply_app_update": cmd_apply_app_update,
    "diagnose_cheese": cmd_diagnose_cheese,
    "run_cheese_repair": cmd_run_cheese_repair,
    "get_running_games": cmd_get_running_games,
    "get_steam_running": cmd_get_steam_running,
    "get_setup_pid": cmd_get_setup_pid,
    "steam_install_status": cmd_steam_install_status,
    "reorder_bottles": cmd_reorder_bottles,
    "launch_launcher": cmd_launch_launcher,
    "get_exe_icon": cmd_get_exe_icon,
    "run_installer": cmd_run_installer,
    "get_install_progress": cmd_get_install_progress,
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
    "legendary_pause_install": cmd_legendary_pause_install,
    "legendary_resume_install": cmd_legendary_resume_install,
    "get_game_config": cmd_get_game_config,
    "set_game_config": cmd_set_game_config,
    "get_game_order": cmd_get_game_order,
    "set_game_order": cmd_set_game_order,
}

# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

# Command handling can run concurrently (see _scan_executor below), so two
# responses can now be written around the same time. Without this lock their
# writes could interleave into one corrupted line the Swift client can't
# parse as JSON — each _respond() call must land atomically.
_stdout_lock = threading.Lock()

def _respond(req_id: Any, ok: bool, data: Any = None, error: str = "") -> None:
    resp: Dict[str, Any] = {"id": req_id, "ok": ok}
    if ok:
        resp["data"] = data
    else:
        resp["error"] = error
    line = json.dumps(resp, default=str)
    with _stdout_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

# Polled by the UI on short timers; logging every call drowns the log.
_QUIET_POLL_CMDS = {
    "get_steam_running",
    "get_running_games",
    "get_install_progress",
    "legendary_status",
    "epic_download_progress",
}

# scan_games/scan_apps walk the filesystem (Steam manifests, Start Menu
# shortcuts, exe detection) and can take seconds on a slow or external drive.
# The main loop below otherwise processes one command at a time, so a slow
# scan for one bottle used to block every other command behind it in the
# queue — including a fast, unrelated one like an Epic auth check for a
# bottle the user just switched to. Both handlers are read-only (they never
# write bottles.json/prefixes.json or any other shared state), so running
# several concurrently is safe; _json_file_lock/_stdout_lock cover the only
# state they do touch (a quick bottle-config read, and writing the response).
_SCAN_EXECUTOR_CMDS = {"scan_games", "scan_apps"}
_scan_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="scan")

def _run_and_respond(cmd_name: str, req_id: Any, handler, request: Dict[str, Any]) -> None:
    try:
        # High-frequency UI polls (every 0.5–3s, forever) used to flood
        # the log with tens of thousands of identical lines - skip them.
        if cmd_name not in _QUIET_POLL_CMDS:
            log(f"Handling cmd={cmd_name} id={req_id}")
        result = handler(request)
        _respond(req_id, True, data=result)
    except Exception as exc:
        log(f"Error in {cmd_name}: {exc}")
        _respond(req_id, False, error=str(exc))

def main() -> None:
    log("MacNCheese backend server started")
    log(f"PORTABLE_DIR = {PORTABLE_DIR}")
    log(f"BOTTLES_BASE = {BOTTLES_BASE}")
    log(f"DEFAULT_PREFIX = {DEFAULT_PREFIX}")

    # Restore automatic Game Mode policy in case a previous run crashed while
    # it had Game Mode forced on.
    _game_mode_reset()

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

            if cmd_name in _SCAN_EXECUTOR_CMDS:
                _scan_executor.submit(_run_and_respond, cmd_name, req_id, handler, request)
            else:
                _run_and_respond(cmd_name, req_id, handler, request)
    finally:
        _terminate_legendary_installs()
        _game_mode_reset()


if __name__ == "__main__":
    main()