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

import array
import atexit
import base64
import datetime
import filecmp
import html as html_lib
import io
import json
import os
import platform
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
# x86_64 TLS closure. PRIMARY source is the bundled deps/mnc-tls pack (see stage_mnc_tls),
# same idea as mnc-fonts for freetype. The Wine-Stable path below is only a FALLBACK: it is
# NOT installed by default, so relying on it alone left boxes with no gnutls at all ->
# crypt32/bcrypt cant verify a cert signature -> Steam login sits on WaitingForServerResponse.
# (libgnutls.30 + nettle/hogweed/gmp/tasn1/p11-kit/intl...) for the
# wine-unified build. it was only ever found via /usr/local/opt/gnutls/lib = INTEL homebrew,
# which does NOT exist on a normal Apple-Silicon mac (only /opt/homebrew, useless to an x86_64
# wine). without it wine loads with NO encryption (gnutls_process_attach fails -> no schannel ->
# Steam's CM logon never completes -> login/QR spins forever). Wine Stable.app ships the exact
# x86_64 closure in its own wine/lib, so list it as a DYLD_FALLBACK so wine-unified always has
# TLS regardless of Homebrew. (Diagnosed by a user Codex report, 2026-07-19.)
_WINE_STABLE_LIB = str(PORTABLE_DIR / "Wine Stable.app" / "Contents" / "Resources" / "wine" / "lib")
VERSION_MARKER = PORTABLE_DIR / ".mnc_versions"
BOTTLES_BASE = Path.home() / "Games" / "MacNCheese"
DEFAULT_PREFIX = str(Path.home() / "wined")

PREFIXES_JSON = Path.home() / ".macncheese_prefixes.json"
BOTTLES_JSON = Path.home() / ".macncheese_bottles.json"

STEAM_SETUP_URL = "https://cdn.fastly.steamstatic.com/client/installer/SteamSetup.exe"
EA_APP_SETUP_URL = "https://origin-a.akamaihd.net/EA-Desktop-Client-Download/installer-releases/EAappInstaller.exe"

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

NILE_DIR = PORTABLE_DIR / "nile"
NILE_BIN = NILE_DIR / "nile"


def _nile_config_dir(prefix: str) -> Path:
    """Returns the per-bottle Nile (Amazon Games) config directory."""
    return Path(prefix).expanduser().resolve() / ".nile_config"


def _nile_cmd(prefix: str) -> List[str]:
    """Base nile command (config isolation is done via NILE_CONFIG_PATH env var)."""
    return [str(NILE_BIN)]


def _nile_env(prefix: str, base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Returns an environment dict with NILE_CONFIG_PATH set to the per-bottle dir."""
    env = (base if base is not None else os.environ).copy()
    config_dir = _nile_config_dir(prefix)
    config_dir.mkdir(parents=True, exist_ok=True)
    env["NILE_CONFIG_PATH"] = str(config_dir)
    return env


APPMANIFEST_RE = re.compile(r'"(\w+)"\s+"([^"]*)"')

_legendary_installing: bool = False
_legendary_installs: Dict[str, Any] = {}  # app_name -> (Popen, file, log_path, prefix)
_legendary_paused: Dict[str, str] = {}    # app_name -> prefix (paused downloads)
_legendary_failed: Dict[str, Dict[str, Any]] = {}  # app_name -> {"error": str, "prefix": str}
_legendary_games_cache: Dict[str, Any] = {}  # prefix -> {"games": [], "ts": float, "scanning": bool}
_LEGENDARY_CACHE_TTL = 300  # seconds before a background re-fetch is triggered


def _scan_legendary_log_for_error(log_path: str, tail: int = 30) -> Optional[str]:
    """Scans the tail of a legendary log for an error line, regardless of
    the process's return code -- some failures (e.g. a third-party-managed
    title) exit 0 despite printing a `[cli] ERROR: ...` line."""
    try:
        with open(log_path, "r") as f:
            lines = f.readlines()
        for line in reversed(lines[-tail:]):
            if "error" in line.lower() or "failed" in line.lower():
                return line.strip()
    except Exception:
        pass
    return None

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
    _legendary_failed.pop(app_name, None)  # clear any stale failure from a prior attempt
    install_base = str(
        Path(prefix).expanduser().resolve() / "drive_c" / "Program Files" / "Epic Games"
    )
    Path(install_base).mkdir(parents=True, exist_ok=True)
    log_path = str(LEGENDARY_DIR / f"install_{app_name}.log")
    proc = None
    try:
        log_fh = open(log_path, "w")
        proc = subprocess.Popen(
            _legendary_cmd(prefix) + ["install", app_name,
             "--base-path", install_base,
             "-y", "--no-install-prereqs", "--skip-sdl"],
            stdin=subprocess.DEVNULL,
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
        # legendary can exit 0 while having done nothing (e.g. a title that
        # has to be installed via a third-party store) -- scan the log
        # regardless of return code rather than trusting the exit status.
        err = _scan_legendary_log_for_error(log_path)
        if err or (proc is not None and proc.returncode not in (0, None)):
            _legendary_failed[app_name] = {
                "error": err or f"legendary exited with code {proc.returncode}",
                "prefix": prefix,
            }
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


_nile_installing: bool = False
_nile_installs: Dict[str, Any] = {}  # amazon_id -> (Popen, file, log_path, prefix)
_nile_paused: Dict[str, str] = {}    # amazon_id -> prefix (paused downloads)
_nile_games_cache: Dict[str, Any] = {}  # prefix -> {"games": [], "ts": float, "scanning": bool}
_NILE_CACHE_TTL = 300  # seconds before a background re-fetch is triggered

# Download queue — one install runs at a time, others wait.
_nile_download_queue: List[Tuple[str, str]] = []  # [(amazon_id, prefix)]
_nile_queue_lock = threading.Lock()
_nile_queue_worker_running: bool = False


def _terminate_nile_installs() -> None:
    """Kill all active nile install processes and clear the queue. Called on backend exit."""
    with _nile_queue_lock:
        _nile_download_queue.clear()
    for amazon_id, entry in list(_nile_installs.items()):
        proc = entry[0]
        try:
            proc.terminate()
        except Exception:
            pass
    _nile_installs.clear()
    _nile_paused.clear()


atexit.register(_terminate_nile_installs)


def _nile_do_install(amazon_id: str, prefix: str) -> None:
    """Run one nile install to completion. Called from the queue worker thread."""
    install_base = str(
        Path(prefix).expanduser().resolve() / "drive_c" / "Program Files" / "Amazon Games"
    )
    Path(install_base).mkdir(parents=True, exist_ok=True)
    log_path = str(NILE_DIR / f"install_{amazon_id}.log")
    try:
        log_fh = open(log_path, "w")
        proc = subprocess.Popen(
            _nile_cmd(prefix) + ["install", amazon_id, "--base-path", install_base],
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            env=_nile_env(prefix),
        )
        _nile_installs[amazon_id] = (proc, log_fh, log_path, prefix)
        proc.wait()
    except Exception:
        pass
    finally:
        entry = _nile_installs.pop(amazon_id, None)
        if entry:
            try:
                entry[1].close()
            except Exception:
                pass
        _nile_games_cache.pop(prefix, None)


def _nile_queue_worker() -> None:
    """Process queued nile installs one at a time."""
    global _nile_queue_worker_running
    while True:
        with _nile_queue_lock:
            if not _nile_download_queue:
                _nile_queue_worker_running = False
                return
            amazon_id, prefix = _nile_download_queue.pop(0)
        _nile_do_install(amazon_id, prefix)


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
# The engine ships inside the app. backend_server.py itself lives at
# <App>.app/Contents/Resources/, so the bundled tree sits next to this file. deps/ still
# wins when it is there, because WineVersionGate.reconcileEngines() only leaves a deps
# copy in place while it is NEWER than the one we ship -- otherwise it deletes it. Running
# from the repo this resolves to a path that does not exist, so dev falls through to deps.
WINE_UNIFIED_BUNDLED = Path(__file__).resolve().parent / "wine-unified"
UNIFIED_GAME_BACKENDS = ("d3dmetal", "dxmt", "dxvk", "vr", "opengl")

# Bradar redist runtimes we PRE-PROVISION into a prefix insted of runnin the 32-bit
# installers (which fault-storm under HACK22): the real MS d3dcompiler_47 (wines builtin
# is weak for HLSL shader compile) + a wine-mono MSI for .NET. the VC++ CRT n DirectX
# Jun2010 r allready wine builtins in every prefix so they need NO install -- only these
# two r genuine gaps. See the CommonRedist run-path + winemono-32bit-hack22-rootcause.
REDIST_DIR = PORTABLE_DIR / "redist"                              # deployed (installer stages here)
REDIST_DEV = Path("/Volumes/ASAFE/D3DMETALWINEDEV/mnc-redist")   # dev source (bundler copies from here)

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
    # Bradar OpenGL = the wine-staging 11.8 wined3d->OpenGL build folded into the unified wine.
    # opengl game backend -> loader opengl column routes d3d11/dxgi/d3d10core here + the d3d11's
    # wined3d import -> wined3d_opengl (the matching 11.8 wined3d). runs on OUR opengl32 + the
    # macdrv GL 3.2 clamp (WINE_MAC_GL_CONTEXT_CLAMP) so SDL3/OpenGL 3.2 games (Mewgenics) render.
    "d3d11_opengl.dll", "dxgi_opengl.dll", "d3d10core_opengl.dll", "wined3d_opengl.dll",
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
        # the unified engine is the one we build, patch and test against -- prefer it over
        # any stock Wine Stable / Staging install that happens to be lying around.
        str(ubt / "wine") if ubt else None,
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
        # Bundled first: the Homebrew/system paths below only exist if the user happens to
        # have an x86_64 Vulkan installed. Without this, winevulkan cant even dlopen
        # libvulkan.1.dylib ("Failed to load libvulkan.1.dylib") and DXVK/VR are dead --
        # the same missing-x86_64-library trap as freetype n gnutls. Note /opt/homebrew is
        # ARM MoltenVK, which an x86_64 wine cant use anyway.
        PORTABLE_DIR / "mnc-vulkan" / "MoltenVK_icd.json",
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
        "/usr/local/opt/fontconfig/lib", "/usr/local/opt/gnutls/lib", _WINE_STABLE_LIB,
        "/usr/local/opt/glib/lib", "/usr/local/opt/gettext/lib",
        "/usr/local/opt/sdl2/lib",
        # bundled freetype/fontconfig fallback for no-Homebrew boxes (see _unified_env / mnc-fonts)
        str(PORTABLE_DIR / "mnc-fonts"), str(PORTABLE_DIR / "mnc-tls"), str(PORTABLE_DIR / "mnc-vulkan"), str(PORTABLE_DIR / "mnc-sdl"),
        "/usr/lib",
    ])
    # arch(1) purges every DYLD_* var on the way through, so a launch that wraps wine in arch
    # hands it an empty search path and wine cant dlopen libfreetype -> "Wine cannot find the
    # FreeType font library". Carry the same value under a name arch leaves alone so those
    # paths can put it back on the far side of the boundary.
    env["MNC_DYLD"] = env["DYLD_FALLBACK_LIBRARY_PATH"]

    return env


def _apply_dpi_aware_regedit(wine: str, env: dict, exes: set) -> None:
    r"""Mark executables DPI-aware using wine's OWN app-compat mechanism.

    HKCU\...\AppCompatFlags\Layers keyed by the lowercased exe BASENAME is the same
    knob Windows exposes for forcing a legacy app HiDPI-aware, and win32u applies it at
    startup (sysparams.c, "HIGHDPIAWARE") -- crucially BEFORE any window exists, so the
    windows are created aware and every geometry API agrees from the outset.

    This is why nothing has to be patched into wine: with Retina Mode on, wine reports the
    display-mode list in physical pixels but monitor rects and mouse input in logical ones,
    and a fullscreen game caught between the two mis-places the cursor and can end up with
    a black window. Marking it aware makes both answers physical.

    An embedded manifest that simply omits dpiAware (Battlefield 4 ships exactly that)
    cannot be overridden with an external manifest, since wine prefers the embedded one --
    the registry layer is the supported way in."""
    if not exes:
        return
    lines = ["REGEDIT4", "",
             "[HKEY_CURRENT_USER\\Software\\Microsoft\\Windows NT\\CurrentVersion"
             "\\AppCompatFlags\\Layers]"]
    for exe in sorted(exes):
        lines.append(f'"{exe.lower()}"="HIGHDPIAWARE"')
    try:
        reg_file = Path(tempfile.gettempdir()) / "wine_dpi_aware.reg"
        reg_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        subprocess.run([wine, "regedit", str(reg_file)], env=env, timeout=300,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log(f"Applied regedit: HIGHDPIAWARE for {sorted(exes)}")
    except Exception as exc:
        log(f"dpi-aware regedit failed: {exc}")


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


def _apply_gecko_regedit(wine: str, env: dict) -> None:
    """Point mshtml.dll at Wine Gecko so embedded-HTML/COM rendering (EULA text, WiX Burn's own
    bootstrapper chrome, any embedded browser control) actually works. mshtml is enabled on
    every launch now (see _unified_env's mscoree/mshtml note); this supplies the Gecko package
    it needs to actually render, and is still called only from the needs_dotnet paths. Our
    unified engine ships no Gecko package of its own (unlike Wine Stable/D3DMetal), so builtin
    mshtml.dll loads but can't render anything without this. Sourced from the SAME self-contained
    redist pack that already provisions wine-mono/d3dcompiler_47 for the unified engine (deps/
    redist/wine-gecko/) -- deliberately NOT Wine Stable.app: unified wine is meant to eventually
    replace Wine Stable outright, so it can't depend on it still being installed. Sets BOTH the
    plain and Wow6432Node views since a 32-bit process's HKLM\\Software\\Wine view is WOW64-
    redirected to Wow6432Node. Confirmed live against EA App's installer."""
    src = _redist_dir()
    gecko_dir = (src / "wine-gecko") if src else None
    x64 = gecko_dir / "wine-gecko-2.47.4-x86_64" if gecko_dir else None
    x86 = gecko_dir / "wine-gecko-2.47.4-x86" if gecko_dir else None
    if not x64 or not x64.is_dir() or not x86.is_dir():
        log("gecko: redist wine-gecko pack not bundled (deps/redist/wine-gecko) -- embedded HTML/CEF UIs may not render")
        return

    def _win_path(p: Path) -> str:
        # macOS abs path -> wine's Z: drive mapping; each '/' becomes a doubled '\\' in one
        # step since REGEDIT4 string values need every backslash escaped.
        return "Z:" + str(p).replace("/", "\\\\")

    reg_content = (
        "REGEDIT4\n\n"
        "[HKEY_LOCAL_MACHINE\\Software\\Wine\\MSHTML\\2.47.4]\n"
        f'"GeckoPath"="{_win_path(x64)}"\n\n'
        "[HKEY_LOCAL_MACHINE\\Software\\Wow6432Node\\Wine\\MSHTML\\2.47.4]\n"
        f'"GeckoPath"="{_win_path(x86)}"\n'
    )
    try:
        reg_file = Path(tempfile.gettempdir()) / "wine_gecko.reg"
        reg_file.write_text(reg_content, encoding="utf-8")
        subprocess.run([wine, "regedit", str(reg_file)], env=env, timeout=300,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log("Applied regedit: mshtml GeckoPath -> redist wine-gecko 2.47.4")
    except Exception as exc:
        log(f"Warning: gecko regedit failed: {exc}")



# --- msync must be uniform per wineserver -------------------------------- WIP
#
# do_msync() is read once per process from WINEMSYNC, on BOTH sides: the client in
# ntdll and the wineserver itself. The server's answer is fixed the moment it
# starts, and every later process joining that prefix inherits a server that
# already decided.
#
# Mixing the two is not a degraded mode, it is silent corruption.
# server/inproc_sync.c only fills reply->shm_idx when the SERVER has msync on:
#
#     if (do_msync()) reply->shm_idx = (unsigned int)fd;
#
# while a client with msync on reads that field regardless:
#
#     if (do_msync()) sync->fd = reply->shm_idx;
#
# so the client ends up using whatever happened to be in an unfilled reply as its
# sync-object index, for every wait and signal it makes.
#
# Live case, 2026-09-01: opening Steam starts the wineserver with msync off
# (_unified_env hardcodes 0 for that path and Steam never reaches
# _apply_sync_env), then launching Schedule I into the same prefix with the
# per-game toggle on made every wait in the game operate on a garbage index.
# Unity's asset loader took a premature wake and died reading level0 with
# "Position out of bounds" -- on a file that was byte-perfect on disk.
#
# WIP: this makes the toggle honest rather than making it work everywhere. The
# first launch into a cold prefix picks the mode and records it; anything joining
# a live server is forced to match and told so. Turning msync on for a prefix
# still means closing everything in it first. The real fix is for the Steam path
# to honour the same per-bottle toggle so the question stops arising.


def _wineserver_msync_mode(prefix: str) -> Optional[bool]:
    """msync mode of the wineserver already serving this prefix, or None if none is.

    Keyed the way wine keys it: the socket directory is named from the prefix
    directory's device and inode, so this asks the same question the loader does
    rather than guessing from a process list."""
    try:
        st = os.stat(prefix)
    except OSError:
        return None
    sock = Path(f"/tmp/.wine-{os.getuid()}") / f"server-{st.st_dev:x}-{st.st_ino:x}" / "socket"
    if not sock.exists():
        return None
    try:
        return (Path(prefix) / ".mnc_msync").read_text().strip() == "1"
    except OSError:
        # A live server with no marker predates this code. It was started by a path
        # that hardcoded msync off, so that is the safe assumption.
        return False


def _reconcile_msync(env: Dict[str, str], prefix: Optional[str]) -> Dict[str, str]:
    """Force WINEMSYNC to agree with the wineserver already serving this prefix."""
    if not prefix:
        return env
    want = env.get("WINEMSYNC", "0") == "1"
    live = _wineserver_msync_mode(prefix)
    if live is None:
        try:
            (Path(prefix) / ".mnc_msync").write_text("1" if want else "0")
        except OSError:
            pass
        return env
    if live != want:
        log(f"msync: this prefix already has a wineserver running with msync "
            f"{'on' if live else 'off'}; forcing this launch to match instead of "
            f"{'enabling' if want else 'disabling'} it "
            f"(mixing the two corrupts every sync object -- close everything in the "
            f"prefix first to change it)")
        env = dict(env)
        env["WINEMSYNC"] = "1" if live else "0"
    return env


def _apply_sync_env(env: Dict[str, str], esync: Optional[bool], msync: Optional[bool],
                    prefix: Optional[str] = None) -> Dict[str, str]:
    """Apply optional per-launch esync/msync flags.

    If a value is None, leave the current environment setting unchanged.
    If a value is True/False, force the corresponding env var to 1/0.
    """
    env = dict(env)
    if esync is not None:
        env["WINEESYNC"] = "1" if esync else "0"
    if msync is not None:
        env["WINEMSYNC"] = "1" if msync else "0"
    return _reconcile_msync(env, prefix)




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
# Set MNC_WINEDEBUG to override this for a session (e.g. "+seh,+coreaudio,+mmdevapi" when
# chasing an audio abort) without editing code.
WINE_DEBUG_VERBOSE = os.environ.get("MNC_WINEDEBUG", "+loaddll,+module,+seh")


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

        backend_ovr = "winemenubuilder.exe=d;mf,mfplat,mfreadwrite,mfplay=b;atidxx64,d3d10,d3d11,d3d12,dxgi,nvapi64,nvngx-on-metalfx=n"

    elif backend == BACKEND_GPTK:
        mnc_root = PORTABLE_DIR / "Wine Stable.app" / "Contents" / "Resources" / "wine"
        mnc_bin = mnc_root / "bin"

        env["PATH"] = f"{mnc_bin}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        env["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join([
            str(D3DMETAL_NATIVE_DIR),
            "/usr/local/lib",
            "/usr/local/opt/freetype/lib",
            "/usr/local/opt/gnutls/lib", _WINE_STABLE_LIB,
            "/usr/lib",
        ])
        env["MNC_DYLD"] = env["DYLD_FALLBACK_LIBRARY_PATH"]
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

        backend_ovr = "winemenubuilder.exe=d;mf,mfplat,mfreadwrite,mfplay=b;atidxx64,d3d10,d3d11,d3d12,dxgi,nvapi64,nvngx-on-metalfx=n"

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
        # The OpenGL path is folded into the unified wine now (the _opengl DLL set +
        # the macdrv GL 3.2 clamp, routed by MNC_GAME_BACKEND=opengl), so a missing
        # standalone Wine Devel.app is normal -- fall through to unified insted of
        # failing the launch outright like it used to.
        unified = _find_wine()
        if unified and _opengl_available():
            log("Backend wine_devel -> unified wine (OpenGL folded in; no standalone app needed)")
            return unified
        log("Backend wine_devel selected but no OpenGL-capable wine found "
            "(the unified wine's _opengl DLLs are missing -- re-run Setup).")
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
            "/usr/local/opt/gnutls/lib", _WINE_STABLE_LIB,
            "/usr/lib",
        ])
        dll_ovr = "winemenubuilder.exe=d;mf,mfplat,mfreadwrite,mfplay=b;atidxx64,d3d10,d3d11,d3d12,dxgi,nvapi64,nvngx-on-metalfx=n"
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

    # Run a SHELL under arch and re-export DYLD inside it, rather than handing wine straight
    # to arch -- arch strips DYLD_* crossing the boundary, which left wine with no way to find
    # libfreetype and put users on "Wine cannot find the FreeType font library". The heredoc
    # paths above already do this; this one was missed.
    inner = (
        'export DYLD_FALLBACK_LIBRARY_PATH="$MNC_DYLD"; '
        f'export {debug_prefix}; '
        f"exec {shlex.quote(wine)} {shlex.quote(exe_name)} {quoted_args}"
    )
    return (
        f"cd {shlex.quote(exe_dir)} && "
        f"/usr/bin/arch -x86_64 /bin/bash -c {shlex.quote(inner)} "
        f"> {shlex.quote(log_path)} 2>&1"
    )


def _write_d3dmetal_legendary_wrapper(prefix: str, metal_hud: bool, debug: bool) -> str:
    """Build a shell wrapper that stands in for a `--wine` binary for
    legendary/nile launches, mirroring _backend_launch_cmd's D3DMETAL3 heredoc
    (the known-working Steam/manual path) instead of the generic
    _apply_backend_env native-DLL-override route, which crashes UnityPlayer.dll
    with an access violation (D3DMetal.app's no-shim wine ships its OWN builtin
    D3D-to-Metal translation -- forcing WINEDLLOVERRIDES to native (=n) and
    relying on copied GPTK DLLs, as the generic path does, doesn't match how
    this wine build actually expects to be run).

    legendary/nile invoke their `--wine` argument as `<wine_bin> <exe> <args...>`
    (proven via `legendary launch --dry-run`), so a chmod+x script here that
    forwards "$@" to a direct-exec of the real D3DMetal launcher works as a
    drop-in -- legendary still owns generating the Epic auth args, we just
    control the environment the launcher actually sees them run in.

    TEMPORARY: this duplicates _backend_launch_cmd's D3DMETAL3 branch rather
    than sharing it, since that function is built around a Popen call MacNCheese
    makes directly (bash -c <heredoc>), not a `--wine` binary path legendary
    execs itself. Worth unifying if a GPTK/GPTK_FULL equivalent for Epic/Amazon
    is ever needed too.
    """
    app = PORTABLE_DIR / "Wine D3DMetal.app"
    launcher = app / "Contents" / "MacOS" / "wine"
    rx = app / "Contents" / "Resources" / "wine"
    libext = rx / "lib" / "external"
    ovr = "d3d12,d3d11,d3d10,d3d10core,dxgi,d3d9=b;mf,mfplat,mfreadwrite,mferror=b"
    dyld = ":".join([
        str(libext),
        "/usr/local/opt/freetype/lib",
        "/usr/local/opt/fontconfig/lib",
        str(rx / "lib"),
        "/usr/local/lib",
        "/usr/lib",
    ])
    wine_debug = WINE_DEBUG_VERBOSE if debug else "-all"
    lines = [
        "#!/bin/bash",
        f"export WINEPREFIX={shlex.quote(prefix)}",
        "export FONTCONFIG_PATH=/usr/local/opt/fontconfig/etc/fonts",
        f"export DYLD_FALLBACK_LIBRARY_PATH={shlex.quote(dyld)}",
        f"export CX_APPLEGPT_LIBD3DSHARED_PATH={shlex.quote(str(libext / 'libd3dshared.dylib'))}",
        f'export WINEDLLOVERRIDES="{ovr}"',
        f"export WINEDEBUG={wine_debug}",
    ]
    if metal_hud:
        lines.append("export MTL_HUD_ENABLED=1")
    lines.append(f'exec /usr/bin/arch -x86_64 {shlex.quote(str(launcher))} "$@"')
    script_path = Path(prefix) / ".mnc-d3dmetal-legendary-wrapper.sh"
    script_path.write_text("\n".join(lines) + "\n")
    script_path.chmod(0o755)
    return str(script_path)


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

# --- wine reparse points (Windows symlinks / directory junctions) ------------
#
# Wine does NOT store a Windows symlink/junction as a unix symlink. It stores it as an
# EMPTY DIRECTORY carrying a `user.WINEREPARSE` xattr (the raw REPARSE_DATA_BUFFER) and
# appends ONE '?' to the UNIX name -- '?' is illegal in a Windows filename so it can never
# collide with a real file, and ntdll strips exactly one trailing '?' off every directory
# entry on the way back to Windows (dlls/ntdll/unix/file.c, append_entry()). So a path
# that opens perfectly INSIDE the bottle can look missing from Python: EA App's own
# installer creates C:\...\EA Desktop\EA Desktop -> 13.754.0.6267\EA Desktop, which lands
# on disk as a directory literally named "EA Desktop?" -- every Start Menu shortcut
# through it failed Path.exists(), so EA App was silently dropped from the Apps section.
# Resolve THROUGH the reparse point (read its target) rather than just tolerating the '?'
# in the name: the path we hand back is then real on BOTH sides, so wine gets something it
# can open and exe_dir / DLL staging / icon lookup keep working unchanged. Generic -- any
# bottle, any app that creates a junction, no per-app knowledge.
WINE_REPARSE_XATTR = "user.WINEREPARSE"
_IO_REPARSE_TAG_SYMLINK = 0xA000000C
_IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003


def _wine_reparse_target(link: Path, prefix: Path) -> Optional[Path]:
    """Follow ONE wine reparse point to the host path it points at (None if it isn't one)."""
    try:
        out = subprocess.run(["xattr", "-px", WINE_REPARSE_XATTR, str(link)],
                             capture_output=True, text=True, timeout=5)
        if out.returncode != 0 or not out.stdout.strip():
            return None
        buf = bytes.fromhex("".join(out.stdout.split()))
        tag = struct.unpack_from("<I", buf, 0)[0]
        if tag == _IO_REPARSE_TAG_SYMLINK:
            off, length, _po, _pl, flags = struct.unpack_from("<HHHHI", buf, 8)
            base, relative = 20, bool(flags & 1)          # SYMLINK_FLAG_RELATIVE
        elif tag == _IO_REPARSE_TAG_MOUNT_POINT:
            off, length = struct.unpack_from("<HH", buf, 8)
            base, relative = 16, False
        else:
            return None
        target = buf[base + off:base + off + length].decode("utf-16-le", "ignore").rstrip("\x00")
    except Exception:
        return None
    if not target:
        return None
    if relative:
        return link.parent / target.replace("\\", "/")
    if target.startswith("\\??\\"):                        # NT form: \??\C:\...
        target = target[4:]
    if len(target) > 2 and target[1] == ":":
        return prefix / f"drive_{target[0].lower()}" / target[3:].replace("\\", "/")
    return None


def _resolve_wine_path(prefix: Path, path: Path, _depth: int = 0) -> Path:
    """Rewrite `path` so any wine reparse point along it points at its real target.

    Cheap by construction: a path that already exists is returned untouched after a single
    stat, so the xattr/component walk only ever runs for a path Python couldn't find."""
    if _depth > 8 or path.exists():
        return path
    try:
        rel = path.relative_to(prefix)
    except ValueError:
        return path
    cur = prefix
    parts = rel.parts
    for i, part in enumerate(parts):
        nxt = cur / part
        if nxt.exists():
            cur = nxt
            continue
        link = cur / (part + "?")
        if not link.exists():
            return path            # genuinely missing -- let the caller's exists() fail
        target = _wine_reparse_target(link, prefix)
        if target is None:
            return path
        rest = parts[i + 1:]
        return _resolve_wine_path(prefix, target.joinpath(*rest) if rest else target,
                                  _depth + 1)
    return cur


def _windows_path_to_unix(prefix: Path, value: str) -> Path:
    normalized = value.replace("\\\\", "\\")
    if re.match(r"^[A-Za-z]:\\", normalized):
        drive = normalized[0].lower()
        remainder = normalized[3:].replace("\\", "/")
        base = prefix / f"drive_{drive}"
        if drive == "c":
            base = prefix / "drive_c"
        return _resolve_wine_path(prefix, base / remainder)
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


# ---------------------------------------------------------------------------
# Steam's own answer to "which exe is the game"
# ---------------------------------------------------------------------------

_APPINFO_MAGIC_V29 = 0x07564429


def _steam_launch_exes(steam_dir: Path, appid: str) -> List[Tuple[str, str]]:
    """[(executable, description)] from Steam's appcache/appinfo.vdf, in Steam's
    own order, or [] when we cannot read it.

    This is what Steam itself would run, so it beats any guess we could make from
    the filesystem. Train Simulator Classic is the motivating case: its real exes
    are ~0.4MB (the engine lives in DLLs) while a mesh converter beside them is
    1MB, so "largest file wins" picks a tool. Steam lists RailWorks64.exe.

    appinfo.vdf is an undocumented binary VDF whose layout changes between client
    versions; only v29 (string-table) is parsed and anything unexpected returns
    [] so callers fall back to the heuristic rather than crash.
    """
    path = steam_dir / "appcache" / "appinfo.vdf"
    try:
        if not path.is_file() or not str(appid).isdigit():
            return []
        want = int(appid)
        data = path.read_bytes()
        magic, _universe = struct.unpack_from("<II", data, 0)
        if magic != _APPINFO_MAGIC_V29:
            return []
        (tbl_off,) = struct.unpack_from("<q", data, 8)
        (count,) = struct.unpack_from("<I", data, tbl_off)
        off = tbl_off + 4
        strings: List[str] = []
        for _ in range(count):
            end = data.index(b"\x00", off)
            strings.append(data[off:end].decode("utf-8", "replace"))
            off = end + 1

        def parse_kv(pos: int, limit: int):
            out: Dict[str, Any] = {}
            while pos < limit:
                t = data[pos]; pos += 1
                if t == 0x08:
                    return out, pos
                (ki,) = struct.unpack_from("<I", data, pos); pos += 4
                key = strings[ki] if ki < len(strings) else str(ki)
                if t == 0x00:
                    out[key], pos = parse_kv(pos, limit)
                elif t == 0x01:
                    end = data.index(b"\x00", pos)
                    out[key] = data[pos:end].decode("utf-8", "replace"); pos = end + 1
                elif t == 0x02:
                    (out[key],) = struct.unpack_from("<i", data, pos); pos += 4
                elif t == 0x07:
                    (out[key],) = struct.unpack_from("<Q", data, pos); pos += 8
                else:
                    raise ValueError("unknown vdf type")
            return out, pos

        pos = 16
        while pos < tbl_off:
            (this_id,) = struct.unpack_from("<I", data, pos)
            if this_id == 0:
                break
            (size,) = struct.unpack_from("<I", data, pos + 4)
            body = pos + 8
            if this_id == want:
                # infoState, lastUpdated, picsToken, sha1(text), changeNumber, sha1(vdf)
                kv, _ = parse_kv(body + 4 + 4 + 8 + 20 + 4 + 20, body + size)
                launch = (kv.get("appinfo", {}).get("config", {}).get("launch")
                          or kv.get("config", {}).get("launch") or {})
                out: List[Tuple[str, str]] = []
                for _k, v in sorted(launch.items(), key=lambda kv2: str(kv2[0])):
                    if not isinstance(v, dict):
                        continue
                    exe = str(v.get("executable", "")).strip().replace("\\", "/")
                    if not exe:
                        continue
                    oslist = str(v.get("config", {}).get("oslist", "")).lower()
                    if oslist and "windows" not in oslist:
                        continue          # a macOS/Linux entry is not what wine runs
                    out.append((exe, str(v.get("description", ""))))
                return out
            pos = body + size
    except Exception as exc:
        log(f"_steam_launch_exes({appid}): {exc}")
    return []


# Names that are shipped beside a game but are not the game: asset converters,
# editors, crash handlers. Matched on the stem, case-insensitively.
_EXE_TOOL_HINTS = ("convert", "editor", "blueprint", "logmate", "utilit", "serz",
                   "luac", "extractor", "optimiser", "optimizer", "namemy",
                   "crash", "report", "unins", "setup", "install", "redist",
                   "dxsetup", "vcredist", "benchmark", "config", "settings")


def _detect_all_exes(game_dir: Path, steam_dir: Optional[Path] = None,
                     appid: str = "") -> List[str]:
    """Plausible game executables, best first.

    Steam's own launch list wins when we can read it -- it is what Steam would
    run, and it distinguishes the 64-bit build from the 32-bit one by name.
    Otherwise fall back to a ranking, because the old "largest file wins" rule
    breaks on any engine that keeps its code in DLLs: Train Simulator Classic's
    real exes are ~0.4MB while the mesh converter next to them is 1MB.
    """
    if not game_dir.exists():
        return []
    results: List[Path] = []
    try:
        for exe in game_dir.glob("**/*.exe"):
            if exe.is_file() and not _is_probably_not_game(exe):
                results.append(exe)
    except Exception:
        pass

    # Steam's launch entries, resolved against what is actually on disk.
    preferred: List[Path] = []
    if steam_dir is not None and appid:
        by_name = {p.name.lower(): p for p in results}
        for exe_rel, _desc in _steam_launch_exes(steam_dir, appid):
            hit = by_name.get(Path(exe_rel).name.lower())
            if hit is not None and hit not in preferred:
                preferred.append(hit)
        # Steam lists the 32-bit entry first for some titles; 64-bit avoids the
        # 2GB cap and the x87 path entirely, so prefer it among Steam's own.
        preferred.sort(key=lambda p: 0 if _exe_is_64bit(p) else 1)

    dir_stem = game_dir.name.lower()

    def rank(p: Path) -> Tuple[int, int]:
        stem = p.stem.lower()
        score = 0
        if stem.startswith(dir_stem) or dir_stem.startswith(stem):
            score -= 40                      # named after the game directory
        if any(h in stem for h in _EXE_TOOL_HINTS):
            score += 60                      # a tool shipped beside the game
        if _exe_is_64bit(p):
            score -= 20                      # prefer the 64-bit build
        try:
            size = p.stat().st_size
        except Exception:
            size = 0
        return (score, -size)                # tie-break on size, descending

    rest = sorted((p for p in results if p not in preferred), key=rank)
    return [str(p) for p in preferred + rest]


def _exe_is_64bit(exe: Path) -> bool:
    info = _pe_header_info(str(exe))
    return bool(info and info[0] != _PE_MACHINE_I386)


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


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _find_process_by_exe(exe_path: Path) -> Optional[int]:
    """Find the OS pid of a running process whose command line contains exe_path."""
    try:
        out = subprocess.run(["ps", "-eo", "pid=,command="], capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    needle = str(exe_path)
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_str, _, cmdline = line.partition(" ")
        if needle in cmdline:
            try:
                return int(pid_str)
            except ValueError:
                continue
    return None


class _HandoffProcess:
    """Popen-like shim for legendary/nile launches.

    `legendary launch`/`nile launch` hand off to Wine and exit within seconds of
    starting it -- their own subprocess.wait() fires almost immediately, which was
    releasing Game Mode and clearing Discord presence while the actual game (a
    separate, unrelated pid) kept running for the whole session. .wait()/.poll()
    here track the real Wine-hosted exe (found by matching its resolved path in
    the process list) instead, so _register_running_game/_discord_presence_for_launch
    hold their state for as long as the game is actually alive.
    """

    def __init__(self, cli_proc: subprocess.Popen, exe_path: Optional[Path]):
        self._cli_proc = cli_proc
        self._exe_path = exe_path
        self.pid = cli_proc.pid
        self._game_pid: Optional[int] = None
        self._searching = bool(exe_path)
        self._settled = False

    def wait(self) -> int:
        try:
            self._cli_proc.wait()
        except Exception:
            pass
        if self._exe_path:
            for _ in range(40):  # ~20s grace for wine to actually exec the game
                self._game_pid = _find_process_by_exe(self._exe_path)
                if self._game_pid:
                    break
                time.sleep(0.5)
            self._searching = False
            while self._game_pid and _pid_alive(self._game_pid):
                time.sleep(1)
        self._settled = True
        return 0

    def poll(self) -> Optional[int]:
        if self._game_pid:
            return None if _pid_alive(self._game_pid) else 0
        # Still inside the post-handoff discovery window -- the CLI wrapper has
        # already exited by design, so its own exit code says nothing about
        # whether the real game is up yet. Report "still alive" until the
        # search in wait() either finds it or gives up.
        if self._searching:
            return None
        if self._settled:
            return 0
        return self._cli_proc.poll()


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
            "game_msync": bottle.get("game_msync", False),
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
            "game_msync": bottle.get("game_msync", False),
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
    if bottle_cfg.get("launcher_type") == "amazon":
        return _scan_nile_games(prefix_str)

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
    # a shortcut target can legitimately run through a wine reparse point (EA App's
    # Start Menu entry does) -- resolve it, or the path looks missing to Python
    return _resolve_wine_path(prefix, prefix / "drive_c" / rest)


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
        (drive_c / "Program Files" / "Steam"),   # fresh fast-boot prefixes land Steam here
        (drive_c / "Program Files" / "Epic Games"),
    ]
    # ...and every game the EA app has installed. Applications is for APPS; a game belongs on
    # the Games tab (it shows up there via its store entry). Battlefield 4 was landing here as
    # "EA Games" -- the Program Files fallback scan below found EA Games/Battlefield 4/bf4.exe
    # and named the entry after the folder. Read the install dirs from EA's own registry record
    # rather than hardcoding a folder name, so a custom install location is covered too. The
    # EA app ITSELF stays listed: it lives under Electronic Arts\, not in a game's install dir.
    excluded_roots += [ea["dir"] for ea in _ea_installed_games(prefix)]

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

    # 2. Fallback: one app per Program Files subfolder for apps with no shortcut of
    # their own. Bradar this used to be gated on "if not found" (the WHOLE bottle
    # has zero shortcuts) instead of per-app -- the moment ANY app in the bottle got
    # a real Start Menu shortcut (e.g. a proper installer like VS Code's own), this
    # entire fallback stopped running and silently dropped every app that was only
    # ever discoverable through it (tools with no Start Menu entry of their own).
    # The existing "key not in found" dedup below already prevents double-counting
    # anything a shortcut already found, so this can just always run.
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
    """Locate the unified wine build (build64 layout).

    deps/ first, then the copy bundled in the .app, then the dev tree. Keyed on the
    loader rather than the directory so a half-copied tree does not win."""
    for d in (WINE_UNIFIED_DIR, WINE_UNIFIED_BUNDLED, WINE_UNIFIED_DEV):
        if (d / "loader" / "wine").exists():
            return d
    return None


def _d3d_pack_candidates() -> Tuple[Path, ...]:
    """Pack locations, best first. The pack ships INSIDE the engine tree, so whichever
    engine won above owns the pack that goes with it -- pairing a deps engine with a
    bundled pack (or the reverse) is how you get a DXMT build talking to the wrong
    winemetal."""
    build = _unified_build_dir()
    seen: List[Path] = []
    for d in ([build / "mnc-d3d"] if build else []) + [UNIFIED_D3D_DIR, UNIFIED_D3D_DEV]:
        if d not in seen:
            seen.append(d)
    return tuple(seen)


def _unified_available() -> bool:
    return _unified_build_dir() is not None


def _mnc_fonts_staged() -> bool:
    """True once the bundled freetype/fontconfig closure is in deps/mnc-fonts.

    Asked separately from the engine because the two stopped moving together. Onboarding
    used to gate stage_mnc_fonts on has_wine_unified, which was sound while the engine
    only ever arrived by being installed into deps/: no engine meant a fresh box meant
    stage the fonts. With the engine shipping inside the .app, has_wine_unified is true
    on a box that has never run anything, so that gate would skip the fonts forever and
    no-Homebrew machines would hit "Wine cannot find the FreeType font library"."""
    return any((PORTABLE_DIR / "mnc-fonts").glob("*.dylib"))


# --- mnc-d3d pack layout ----------------------------------------------------
#
# Layout 2 gives each backend its own folder and keeps the canonical Windows DLL
# name inside it, so refreshing a backend is a plain copy with no renaming:
#
#   mnc-d3d/dxmt/d3d11.dll          was  mnc-d3d/d3d11_dxmt.dll
#   mnc-d3d/opengl/wined3d.dll      was  mnc-d3d/wined3d_opengl.dll
#   mnc-d3d/base/d3d11.dll          was  mnc-d3d/d3d11.dll
#   mnc-d3d/d3dm/external/...       was  mnc-d3d/{libd3dshared.dylib,D3DMetal.framework}
#
# Layout 1 (the flat naming) is still read, so an older pack keeps working. What
# the wrapper WRITES is unchanged either way: a prefix still gets
# system32/d3d11_dxmt.dll, because that is the name the engine's redirect table
# resolves. The engine needs no change for this -- mnc-d3d is a staging source,
# not a loader search path.
#
# Slots whose flat name is not "<base>_<backend>.dll":
_D3D_PACK_SPECIAL = {
    # d3d9's second copy is an ARCH variant, not a backend one
    "d3d9_dxmt.dll": ("dxmt", "d3d9.dll"),
    "d3d9_dxmt32.dll": ("dxmt", "d3d9-32.dll"),
    # the OpenXR bridge PE is unsuffixed but belongs to the openxr backend
    "wineopenxr.dll": ("openxr", "wineopenxr.dll"),
}
_D3D_PACK_BACKENDS = ("d3dm", "dxmt", "dxvk", "opengl", "openxr")

# Canonical (unsuffixed) slots that ARE the GPTK stubs -- verified byte-identical
# to their _d3dm twins. They must come from the SAME toolkit as the
# libd3dshared.dylib/D3DMetal.framework pair they link against, or a 26.4+ Mac
# would pair GPTK 3.0 stubs with GPTK 4.0b2's runtime. The rest of the canonical
# set (d3d10, d3d10_1, d3d10core, d3d12core) is wine's own builtins and lives in
# base/.
_D3D_PACK_GPTK_CANONICAL = ("d3d11.dll", "d3d12.dll", "dxgi.dll")

# GPTK 4.0b2's D3DMetal.framework is built against the macOS 26.4 SDK
# (DTPlatformVersion 26.4); 3.0 against 15.4. Older systems stay on 3.0.
_GPTK4_MIN_MACOS = (26, 4)


def _macos_version() -> Tuple[int, ...]:
    try:
        return tuple(int(x) for x in platform.mac_ver()[0].split(".")[:2])
    except Exception:
        return (0, 0)


def _d3d_pack_layout(d: Path) -> int:
    """2 = folder layout, 1 = flat layout, 0 = not a d3d pack.

    Probe several slots rather than one. base/d3d11.dll does not exist in a
    layout-2 pack at all (the canonical d3d11 is a GPTK stub and is resolved out
    of the selected d3dm folder), and keying on any single Apple-supplied slot is
    what made the old d3d11.dll check misreport a pack full of our own DLLs as
    absent."""
    for probe in ("dxmt/d3d11.dll", "base/winemetal.dll", "d3dm/d3d11.dll", "base/d3d11.dll"):
        if (d / probe).exists():
            return 2
    if (d / "winemetal.dll").exists() or (d / "d3d11.dll").exists():
        return 1
    return 0


def _d3dm_dir_name(d: Path) -> str:
    """Which GPTK folder the d3dmetal backend should use in a layout-2 pack."""
    if _macos_version() >= _GPTK4_MIN_MACOS and (d / "d3dm-gptk4" / "d3d11.dll").exists():
        return "d3dm-gptk4"
    return "d3dm"


def _d3d_pack_file(d: Path, flat: str) -> Optional[Path]:
    """Resolve one pack slot by its flat name, whichever layout the pack uses.

    Callers keep speaking the flat vocabulary ("d3d11_dxmt.dll") because that is
    what the loader expects to find in system32; this only decides where the
    bytes are read FROM. Returns None when the pack does not carry that slot."""
    if _d3d_pack_layout(d) == 2:
        found = _D3D_PACK_SPECIAL.get(flat)
        canonical = False
        if found is None:
            stem = flat[:-4] if flat.endswith(".dll") else flat
            for backend in _D3D_PACK_BACKENDS:
                if stem.endswith("_" + backend):
                    found = (backend, stem[: -(len(backend) + 1)] + ".dll")
                    break
            else:
                canonical = True
                found = ("d3dm" if flat in _D3D_PACK_GPTK_CANONICAL else "base", flat)
        sub, name = found
        if sub == "d3dm":
            sub = _d3dm_dir_name(d)
        p = d / sub / name
        if p.exists():
            return p
        # Only a CANONICAL slot may fall back to base/. A backend-suffixed one must
        # not: base/ holds wine's own builtins, so letting d3d10core_openxr.dll fall
        # through to base/d3d10core.dll would stage wine's d3d10core into the VR slot
        # and the loader would route MNC_GAME_BACKEND=vr straight at it. An absent
        # backend slot has to stay absent so the caller skips it.
        if canonical:
            p = d / "base" / name
            if p.exists():
                return p
    p = d / flat
    return p if p.exists() else None


def _d3d_external_dir(d: Path) -> Path:
    """Where libd3dshared.dylib and D3DMetal.framework live in this pack."""
    if _d3d_pack_layout(d) == 2:
        return d / _d3dm_dir_name(d) / "external"
    return d


def _unified_d3d_dir() -> Optional[Path]:
    """Locate the bundled d3d DLL pack the unified loader routes to.

    Probed by _d3d_pack_layout(), which keys on slots that are OURS. It used to key
    on d3d11.dll alone, but that slot holds Apples D3DMetal stub -- so a pack full of
    our own DLLs could look entirely absent. That reasoning carries into the folder
    layout, where the canonical d3d11.dll does not sit at the pack root at all.
    """
    for d in _d3d_pack_candidates():
        if _d3d_pack_layout(d):
            return d
    return None


def _opengl_available() -> bool:
    """True when the OpenGL game backend can actualy run.

    The OpenGL path used to be a SEPARATE "Wine Devel.app" download, so every
    check keyed on _find_wine_devel(). It got folded INTO the unified wine (the
    wined3d->OpenGL _opengl DLL set + the macdrv GL 3.2 clamp) and that app
    stopped being installed -- but the checks were never repointed, so OpenGL
    reported "not installed" on evry machine even with the DLLs sittin right
    there. Key on the real capability (the _opengl slots in the unified d3d
    pack) n keep the legacy app as a fallbak for older installs that have it."""
    d3ddir = _unified_d3d_dir()
    if (d3ddir and _d3d_pack_file(d3ddir, "d3d11_opengl.dll")
            and _d3d_pack_file(d3ddir, "wined3d_opengl.dll")):
        return True
    return _find_wine_devel() is not None


def _unified_pe_builtin(name: str, arch_dir: str) -> Optional[Path]:
    """Resolve one PE builtin inside the unified wine (build64 layout).

    Unlike an installed wine (flat lib/wine/<arch>/) the unified tree keeps each
    builtin under its own module dir: dlls/ntdll/x86_64-windows/ntdll.dll. Used to
    diff a prefix against the wine that ACTUALY bootstrapped it."""
    root = _unified_build_dir()
    if not root:
        return None
    stem = name.rsplit(".", 1)[0]
    for top in ("dlls", "programs"):
        p = root / top / stem / arch_dir / name
        if p.exists():
            return p
    return None


def _d3dmetal_native_dir() -> Path:
    """Where libd3dshared.dylib + D3DMetal.framework live for the d3dmetal backend
    (bundled pack first, then the dev D3DMetalTesting tree).

    Require BOTH files, not just libd3dshared.dylib. libd3dshared dlopens D3DMetal via
    @rpath=@loader_path, i.e. it looks for D3DMetal.framework RIGHT NEXT TO ITSELF -- so a
    dir with the dylib but no framework is not a usable d3dmetal pack, it's a trap: routing
    d3d there loads fine, logs "[D3DMETAL_FB] dlopen ok" + resolves d3d11/dxgi, and only
    then dies on `Assertion failed: (GFXTHandle && "Failed to dlopen D3DMetal") ... shared.mm`
    with no hint that a FILE is missing. Hit live 2026-07-25: wine-unified/mnc-d3d shipped
    the dylib but not the framework (the wine-installer overlay had both), so every
    d3dmetal launch under the unified engine asserted. Checking both here makes it fall
    through to a pack that is actually complete instead."""
    for d in _d3d_pack_candidates() + (D3DMETAL_NATIVE_DIR,):
        ext = _d3d_external_dir(d)
        if (ext / "libd3dshared.dylib").exists() and (ext / "D3DMetal.framework").exists():
            return ext
    # nothing complete -- warn loudly rather than silently returning a broken pack
    for d in _d3d_pack_candidates() + (D3DMETAL_NATIVE_DIR,):
        ext = _d3d_external_dir(d)
        if (ext / "libd3dshared.dylib").exists():
            log(f"d3dmetal: {ext} has libd3dshared.dylib but NO D3DMetal.framework next to it "
                f"-> d3dmetal launches will assert in shared.mm; copy the framework there")
            return ext
    return D3DMETAL_NATIVE_DIR


def _disable_shadowing_builtins() -> int:
    """Move aside any wine builtin that shadows a backend-specific d3d DLL.

    The loader rewrites a d3d module NAME (d3d10core.dll -> d3d10core_dxmt.dll) and then
    resolves it -- but find_builtin_dll looks the builtin up by its ORIGINAL name, so if
    wine still ships its own builtin for that module the builtin wins and the redirect is
    silently defeated. The engine already had dxgi and d3d11 renamed aside by hand;
    d3d10core was missed, so DXMT games got WINE's d3d10core, which imports
    dxgi.DXGID3D10CreateDevice -- a symbol DXMT's dxgi (correctly) does not export. Wine
    then stubs the import and terminates the process on the first D3D10 call:
    "Call from ... to unimplemented function dxgi.dll.DXGID3D10CreateDevice, aborting".
    Live-confirmed with a minimal D3D10CreateDevice probe: aborted before, returns S_OK
    after, and the mapped image switches from wine's 159 KB builtin to DXMT's real 11 MB one.

    The set is deliberately explicit rather than derived from the pack's filenames. It is
    NOT "every module the pack ships a file for": the pack also ships canonical d3d10.dll,
    d3d10_1.dll and d3d12.dll, and wine's d3d10 builtin in particular MUST stay -- it is the
    public D3D10 API layer that calls D3D10CoreCreateDevice into the redirected d3d10core,
    and there is no DXMT/DXVK d3d10 build to replace it with. Likewise wined3d and
    winegstreamer only have per-backend variants (wined3d_opengl, winegstreamer_game) and
    the loader falls back to wine's builtin for every other backend, so disabling those
    breaks the fallback. Only these three are both redirected to a *_<backend>.dll target
    AND have a canonical pack build to stand in for the builtin.

    Idempotent, and x86_64 only -- the replacements are 64-bit builds, so a 32-bit process
    must keep wine's builtins."""
    d3d_dir = _unified_d3d_dir()
    bt = _unified_build_dir()
    if d3d_dir is None or bt is None:
        return 0
    moved = 0
    for mod in ("dxgi", "d3d11", "d3d10core"):
        if _d3d_pack_file(d3d_dir, f"{mod}.dll") is None:
            continue    # no canonical replacement staged -> leave wine's builtin alone
        builtin = bt / "dlls" / mod / "x86_64-windows" / f"{mod}.dll"
        if not builtin.exists():
            continue
        try:
            builtin.rename(builtin.with_suffix(".dll.builtin-disabled"))
            log(f"unified: disabled wine builtin {mod}.dll so the backend redirect can win")
            moved += 1
        except Exception as exc:
            log(f"unified: could not disable builtin {mod}.dll: {exc}")
    return moved


def _stage_unified_dlls(prefix: str) -> None:
    """Copy the unified d3d DLL slots into a prefix system32 so the loader has
    real targets to route to (canonical=DXMT plus *_d3dm and *_dxvk).

    Idempotent, but it MUST NOT key idempotency on size alone. Successive DXMT
    builds land on byte-identical sizes (section alignment pads them out), so a
    size-only check silently pins whatever build a prefix was first staged with
    and no pack update ever reaches it. Live example 2026-08-05: the Steam prefix
    ran DXMT v0.80-108 while the pack had shipped v0.80-132 for 24 commits --
    all three *_dxmt DLLs matched on size and differed in content.

    Compare mtime as well: shutil.copy2 preserves the source mtime, so a copy
    made from an older pack carries that pack's timestamp. The 2s tolerance is
    for filesystems with coarse mtime granularity (exFAT), which would otherwise
    re-copy several MB on every launch."""
    _disable_shadowing_builtins()
    src_dir = _unified_d3d_dir()
    if src_dir is None:
        log("unified: d3d DLL pack not found; backend routing may fail (run install_wine_unified)")
        return
    sys32 = Path(prefix) / "drive_c" / "windows" / "system32"
    if not sys32.is_dir():
        return
    staged = 0
    for dll in UNIFIED_D3D_DLLS:
        src = _d3d_pack_file(src_dir, dll)
        if src is None:
            continue
        dst = sys32 / dll
        try:
            if not dst.exists():
                stale = True
            else:
                ss, ds = src.stat(), dst.stat()
                stale = (ss.st_size != ds.st_size
                         or abs(ss.st_mtime - ds.st_mtime) > 2)
            if stale:
                shutil.copy2(str(src), str(dst))
                staged += 1
        except Exception as exc:
            log(f"unified: stage {dll} failed: {exc}")
    if staged:
        log(f"unified: staged {staged} d3d DLL(s) -> system32 from {src_dir}")
    _stage_unified_d3d9(prefix, src_dir)


def _stage_unified_d3d9(prefix: str, src_dir: Path) -> None:
    """Drop DXMT's d3d9 into the prefix, but only on an Apple GPU.

    Unlike the d3d11/dxgi family this is NOT a builtin rename: the DLL ships
    unmarked (no `winebuild --builtin`) and is loaded natively via the "d3d9=n"
    override that _unified_env() adds on the same condition. That keeps the
    choice in one place -- drop the file AND set the override, or neither:

      Apple Silicon -> native DXMT d3d9 (Metal)
      Intel         -> wines builtin d3d9 (wined3d), untouched

    Without "=n" wine loads its own builtin out of the wine tree even when a
    d3d9.dll sits in system32, so a stale file left behind by a machine swap
    cannot silently take over.

    64-bit goes to system32, 32-bit to syswow64; the unix bridge behind both is
    the single 64-bit winemetal9.so, reached from a 32-bit PE via WoW64."""
    if not _is_apple_silicon():
        return
    win_dir = Path(prefix) / "drive_c" / "windows"
    for src_name, sys_dir in (("d3d9_dxmt.dll", "system32"),
                              ("d3d9_dxmt32.dll", "syswow64")):
        src = _d3d_pack_file(src_dir, src_name)
        dst_dir = win_dir / sys_dir
        if src is None or not dst_dir.is_dir():
            continue
        dst = dst_dir / "d3d9.dll"
        try:
            if dst.exists():
                ss, ds = src.stat(), dst.stat()
                if ss.st_size == ds.st_size and abs(ss.st_mtime - ds.st_mtime) <= 2:
                    continue
            shutil.copy2(str(src), str(dst))
            log(f"unified: staged {src_name} -> {sys_dir}\\d3d9.dll (DXMT, Apple GPU)")
        except Exception as exc:
            log(f"unified: stage {src_name} failed: {exc}")


# --- PE header poking: the 32-bit gate, and the 4GB patch -------------------
#
# Offsets we need, all from the DOS stub forward:
#   0x00  e_magic 'MZ'
#   0x3c  e_lfanew -> start of the NT headers
#   +0    "PE\0\0"
#   +4    IMAGE_FILE_HEADER: Machine at +0, Characteristics at +18
#   +24   IMAGE_OPTIONAL_HEADER: Magic at +0, CheckSum at +64 (same in PE32/PE32+)
_PE_MACHINE_I386 = 0x014C
_PE_MACHINE_AMD64 = 0x8664
_IMAGE_FILE_LARGE_ADDRESS_AWARE = 0x0020


def _pe_header_info(exe: str) -> Optional[Tuple[int, int, int, int]]:
    """(machine, characteristics, characteristics_offset, checksum_offset) for a PE.

    None when the file isn't a PE at all -- a shell script, a .NET single-file
    bundle stub we can't parse, a truncated download. Every caller treats that as
    "leave it alone", which is the only safe reading of "I don't understand this
    file"."""
    try:
        with open(exe, "rb") as fh:
            if fh.read(2) != b"MZ":
                return None
            fh.seek(0x3C)
            (e_lfanew,) = struct.unpack("<I", fh.read(4))
            if e_lfanew <= 0 or e_lfanew > (1 << 24):
                return None
            fh.seek(e_lfanew)
            if fh.read(4) != b"PE\0\0":
                return None
            machine, _nsec, _ts, _psym, _nsym, _optsz, chars = struct.unpack("<HHIIIHH", fh.read(20))
            return machine, chars, e_lfanew + 4 + 18, e_lfanew + 24 + 64
    except Exception:
        return None


def _pe_is_32bit(exe: str) -> bool:
    info = _pe_header_info(exe)
    return bool(info) and info[0] == _PE_MACHINE_I386


def _pe_checksum(buf: bytearray, checksum_off: int) -> int:
    """The PE checksum: 16-bit ones-complement sum of the image with the checksum
    field taken as zero, plus the file size."""
    n = len(buf)
    saved = bytes(buf[checksum_off:checksum_off + 4])
    buf[checksum_off:checksum_off + 4] = b"\0\0\0\0"
    try:
        words = array.array("H")
        words.frombytes(bytes(buf[:n - (n & 1)]))
        if sys.byteorder != "little":
            words.byteswap()
        total = sum(words)
        if n & 1:
            total += buf[-1]
        while total >> 16:
            total = (total & 0xFFFF) + (total >> 16)
    finally:
        buf[checksum_off:checksum_off + 4] = saved
    return (total + n) & 0xFFFFFFFF


def _apply_4gb_patch(exe: str) -> Optional[bool]:
    """Set IMAGE_FILE_LARGE_ADDRESS_AWARE on a 32-bit exe that lacks it.

    Same edit as ntcore's "4GB patch": one bit in the COFF Characteristics word.
    Without it a 32-bit process is capped at a 2GB user address space even though
    wine hands out the full 4GB; OMSI 2 is the classic case, it simply runs out
    and dies. The bit is a promise that the program's pointer arithmetic is
    unsigned-clean, which is why it can't just be set on everything -- but a game
    that ships it unset and then OOMs is the exact case it exists for.

    Returns True if we patched, False if nothing to do, None if we couldn't.
    The original is kept alongside so a bad patch is one copy away from undone."""
    info = _pe_header_info(exe)
    if not info:
        return None
    machine, chars, chars_off, cksum_off = info
    if machine != _PE_MACHINE_I386:
        return False                      # 64-bit is large-address-aware by definition
    if chars & _IMAGE_FILE_LARGE_ADDRESS_AWARE:
        return False                      # already patched, by us or by the vendor
    backup = Path(exe).with_suffix(Path(exe).suffix + ".mnc-orig")
    try:
        size = os.path.getsize(exe)
        if not backup.exists():
            shutil.copy2(exe, str(backup))
        with open(exe, "r+b") as fh:
            fh.seek(chars_off)
            fh.write(struct.pack("<H", chars | _IMAGE_FILE_LARGE_ADDRESS_AWARE))
            # Recompute the checksum only when the file carried one and is small
            # enough to slurp. A zero checksum is legal and unverified for
            # anything that isn't a driver, so leaving it alone is safe; wine
            # never checks it either way.
            fh.seek(cksum_off)
            (old_cksum,) = struct.unpack("<I", fh.read(4))
            if old_cksum and size <= (256 << 20):
                fh.seek(0)
                buf = bytearray(fh.read())
                fh.seek(cksum_off)
                fh.write(struct.pack("<I", _pe_checksum(buf, cksum_off)))
        log(f"4GB patch: set LARGE_ADDRESS_AWARE on {Path(exe).name} "
            f"(original kept as {backup.name})")
        return True
    except Exception as exc:
        log(f"4GB patch: {Path(exe).name} left alone ({exc})")
        return None


def _redist_dir() -> Optional[Path]:
    """Locate the bundled redist pack (real MS d3dcompiler_47 + a wine-mono MSI)."""
    for d in (REDIST_DIR, REDIST_DEV):
        if (d / "d3dcompiler_47" / "d3dcompiler_47.dll").exists():
            return d
    return None


def _provision_redist_dlls(prefix: str) -> None:
    """Drop the REAL Microsoft d3dcompiler_47 into a prefix (x64 -> system32, i386 ->
    syswow64). Wines builtin d3dcompiler_47 is a thin vkd3d-based reimpl that compiles HLSL
    poorly; the real MS DLL is what shader-heavy games actualy want. Paired with the
    d3dcompiler_47=native,builtin override in _unified_env (native FIRST, builtin fallbak --
    NEVER native alone, see winetricks #2344, else an incomplete native can break worse than
    builtin). Idempotent size-checkd copy. This is a FILE-DROP, not an installer run, so it
    carrys zero HACK22 fault-storm risk -- the whole point vs runnin the DXSETUP installer."""
    src = _redist_dir()
    if src is None:
        return
    pairs = (
        (src / "d3dcompiler_47" / "d3dcompiler_47.dll",
         Path(prefix) / "drive_c" / "windows" / "system32" / "d3dcompiler_47.dll"),
        (src / "d3dcompiler_47" / "d3dcompiler_47_32.dll",
         Path(prefix) / "drive_c" / "windows" / "syswow64" / "d3dcompiler_47.dll"),
    )
    staged = 0
    for s, d in pairs:
        try:
            if s.exists() and d.parent.is_dir() and (not d.exists() or s.stat().st_size != d.stat().st_size):
                shutil.copy2(str(s), str(d))
                staged += 1
        except Exception as exc:
            log(f"redist: provision {d.name} failed: {exc}")
    if staged:
        log(f"redist: provisiond real MS d3dcompiler_47 ({staged} arch) -> prefix")


def _install_wine_mono(prefix: str, backend: str = "d3dmetal") -> bool:
    """Install wine-mono (the .NET Framework substitute) into a prefix from the bundled MSI,
    via the unified wine -- a plain 'msiexec /i', NOT the real MS .NET NDP*.exe
    bootstrapper (that one fault-storms forever under HACK22 n never exits, which is the whole
    reason .NET "installers" appeard broken). Once-per-prefix, guarded by the drive_c/windows/
    mono sentinel. Returns True if mono is present (allready-there or freshly installd).
    wine-mono covers most .NET games; the few it cant (WPF / strong-named / anticheat-gated)
    stay a manual escalation. NEVER auto-runs real .NET. See winemono-32bit-hack22-rootcause."""
    mono_marker = Path(prefix) / "drive_c" / "windows" / "mono"
    if mono_marker.is_dir():
        return True
    src = _redist_dir()
    if src is None:
        return False
    msis = sorted((src / "wine-mono").glob("wine-mono-*.msi")) if (src / "wine-mono").is_dir() else []
    if not msis:
        return False
    iw = _installer_wine()
    if not iw:
        log("redist: wine-mono install skipd (no wine found)")
        return False
    msi = str(msis[-1])   # newest cached
    env = _unified_env(prefix, backend or "d3dmetal", False, for_steam=False)
    env["WINEDEBUG"] = "-all"
    # mscoree MUST be enabled for msiexec to register mono. _unified_env no longer disables it
    # (nor mshtml), so this only needs to keep winemenubuilder out of the way.
    env["WINEDLLOVERRIDES"] = "winemenubuilder.exe=d"
    _stage_syswow64(prefix)   # 32-bit subsystem so the wine can run the 32-bit MSI
    dyld = env.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    sh = (f"export DYLD_FALLBACK_LIBRARY_PATH={shlex.quote(dyld)}\n"
          f"{shlex.quote(iw)} msiexec /i {shlex.quote(msi)} /qn >/dev/null 2>&1")
    log(f"redist: installing {Path(msi).name} (wine-mono / .NET) via the unified wine...")
    try:
        subprocess.run(["/usr/bin/arch", "-x86_64", "/bin/bash", "-lc", sh], env=env, timeout=600)
    except Exception as exc:
        log(f"redist: wine-mono install failed: {exc}")
        return False
    ok = mono_marker.is_dir()
    log(f"redist: wine-mono {'installd' if ok else 'install did NOT land (game .NET may not work)'}")
    return ok


def _install_corefonts(prefix: str) -> None:
    """File-drop the MS core fonts (arial/times/verdana/...) into a prefix so CEF/HTML UIs like
    the EA app render text insted of tofu boxes. TTFs come from the bundled deps/redist/corefonts
    pack; wine auto-registers any TTF dropped in the Fonts dir on next start. Idempotent (sentinel
    arial.ttf). Pure FILE-DROP -- NOT the winetricks corefonts installer -> zero fault-storm risk n
    no network. No-op (with a log) if the pack isnt bundled."""
    fonts = Path(prefix) / "drive_c" / "windows" / "Fonts"
    try:
        fonts.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    if (fonts / "arial.ttf").exists():
        return
    src = _redist_dir()
    cf = (src / "corefonts") if src else None
    if cf is None or not cf.is_dir():
        log("corefonts: pack not bundled (deps/redist/corefonts) -> EA/HTML UI text may be boxes")
        return
    n = 0
    for ttf in list(cf.glob("*.ttf")) + list(cf.glob("*.TTF")):
        dst = fonts / ttf.name
        try:
            if not dst.exists():
                shutil.copy2(str(ttf), str(dst))
                n += 1
        except Exception as exc:
            log(f"corefonts: copy {ttf.name} failed: {exc}")
    if n:
        log(f"corefonts: droppd {n} TTF(s) -> prefix Fonts")


def _game_needs_dotnet(prefix: str, game_dir: str,
                       bottle_cfg: Dict[str, Any], params: Dict[str, Any]) -> bool:
    """Whether to PROVISION wine-mono for THIS game launch. Explicit per-game opt-in
    (params/bottle_cfg 'needs_dotnet') wins; else auto-detect -- a game that SHIPS a .NET
    redist in its CommonRedist/Redistributables almost certainly wants .NET.

    Bradar this no longer gates whether mscoree/mshtml may LOAD -- _unified_env leaves both at
    wines default now, so a .NET app never needs this flag just to start (see the mscoree note
    there). It only decides whether we go install the Mono/Gecko packages up front, which is
    what .NET FRAMEWORK titles need and .NET Core ones do not. Default OFF keeps that install
    off the hot path for the majority of games that dont touch .NET at all."""
    flag = params.get("needs_dotnet", bottle_cfg.get("needs_dotnet", None))
    if flag is not None:
        return bool(flag)
    try:
        roots = [Path(game_dir)]
        shared = _steam_dir(prefix) / "steamapps" / "common" / "Steamworks Shared"
        if shared.is_dir():
            roots.append(shared)
        for root in roots:
            for cr in ("_CommonRedist", "Redistributables"):
                p = root / cr
                if not p.is_dir():
                    continue
                for sub in p.iterdir():
                    nm = sub.name.lower()
                    if sub.is_dir() and ("dotnet" in nm or ".net" in nm or "netfx" in nm):
                        return True
    except Exception:
        pass
    return False


# Bradar Titles marked HIGHDPIAWARE (see _apply_dpi_aware_regedit).
# Keyed BOTH by exe and by title because the two launch paths know different things: a
# manual/Steam launch hands us a real exe path, but an Epic THIRD-PARTY title (BF4 is
# fulfilled through the EA App) has no legendary install record at all -- no install_path,
# no executable -- so there the title from the disk library is the only handle we get.
# A list, not a heuristic: the condition is "this game reads the display-mode list in
# physical pixels but hit-tests in logical ones", which cannot be detected before launch.
_DPI_AWARE_EXES = {"bf4.exe"}
_DPI_AWARE_TITLE_HINTS = ("battlefield 4",)


_hidpi_screen_cached: Optional[bool] = None


def _screen_is_hidpi() -> bool:
    """True when the main display has a backing scale > 1.

    Same test the Swift side uses for the Retina Mode default
    (`NSScreen.main.backingScaleFactor > 1.0`), done backend-side so it also covers launch
    paths the UI doesn't originate. Deliberately NOT keyed off the retina_mode setting:
    that is user-toggleable, and the DPI mismatch this gates is a property of the PANEL --
    measured with Retina Mode OFF on a retina Mac, the monitor still reports 735x478 while
    the display mode reports 1470x956, the same 2x disagreement."""
    global _hidpi_screen_cached
    if _hidpi_screen_cached is not None:
        return _hidpi_screen_cached
    _hidpi_screen_cached = _probe_screen_is_hidpi()
    return _hidpi_screen_cached


def _probe_screen_is_hidpi() -> bool:
    try:
        import ctypes, ctypes.util
        lib = ctypes.util.find_library("CoreGraphics")
        if not lib:
            return False
        cg = ctypes.CDLL(lib)
        cg.CGMainDisplayID.restype = ctypes.c_uint32
        cg.CGDisplayCopyDisplayMode.restype = ctypes.c_void_p
        cg.CGDisplayCopyDisplayMode.argtypes = [ctypes.c_uint32]
        cg.CGDisplayModeGetPixelWidth.restype = ctypes.c_size_t
        cg.CGDisplayModeGetPixelWidth.argtypes = [ctypes.c_void_p]
        cg.CGDisplayModeGetWidth.restype = ctypes.c_size_t
        cg.CGDisplayModeGetWidth.argtypes = [ctypes.c_void_p]
        cg.CGDisplayModeRelease.argtypes = [ctypes.c_void_p]

        mode = cg.CGDisplayCopyDisplayMode(cg.CGMainDisplayID())
        if not mode:
            return False
        try:
            return cg.CGDisplayModeGetPixelWidth(mode) > cg.CGDisplayModeGetWidth(mode)
        finally:
            cg.CGDisplayModeRelease(mode)
    except Exception:
        return False


def _game_needs_dpi_aware(prefix: str, game_dir: str, exe_name: str,
                          app_name: str, bottle_cfg: Dict[str, Any],
                          params: Dict[str, Any]) -> bool:
    """Whether to force per-monitor DPI awareness for THIS game launch.

    Explicit per-game opt-in (params/bottle_cfg 'dpi_aware') always wins; otherwise fall
    back to the known-title list. Default OFF: forcing awareness stops wine virtualizing
    screen coordinates, which is exactly what an older 96-DPI game WANTS, so it must not
    become a blanket behaviour change."""
    # A present-but-null value means "auto" (the UI sends that for its Auto setting), so it
    # must fall through to the bottle setting rather than count as an explicit choice.
    flag = params.get("dpi_aware")
    if flag is None:
        flag = bottle_cfg.get("dpi_aware")
    if flag is not None:
        return bool(flag)

    # Only a HiDPI panel produces the mismatch this works around: on a 1x display wine
    # scales nothing, the mode list and the monitor rect already agree, and there is
    # nothing to correct. Checked after the explicit flag so a user can still force it.
    if not _screen_is_hidpi():
        return False

    if exe_name and exe_name.lower() in _DPI_AWARE_EXES:
        return True

    if game_dir:
        try:
            for exe in _DPI_AWARE_EXES:
                if (Path(game_dir) / exe).exists():
                    return True
        except Exception:
            pass

    # Epic third-party titles: no exe and no dir, so match the catalogue title.
    if app_name:
        try:
            for g in _read_disk_library(prefix):
                if g.get("app_name") != app_name:
                    continue
                title = (g.get("app_title")
                         or g.get("metadata", {}).get("title", "") or "").lower()
                if any(hint in title for hint in _DPI_AWARE_TITLE_HINTS):
                    return True
                break
        except Exception:
            pass

    return False


def _stage_unified_mf(prefix: str) -> None:
    """Stage the game-side winegstreamer video bridge into a prefix and re-point the
    wg_* MF CLSIDs at it so game intro videos decode. Idempotent: the DLL copy is
    size-checked and the registry import runs once guarded by a sentinel."""
    src_dir = _unified_d3d_dir()
    if src_dir is None:
        return
    src = _d3d_pack_file(src_dir, UNIFIED_MF_BRIDGE)
    if src is None:
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


def _classic_default_backend(bottle_cfg: Dict[str, Any]) -> Optional[str]:
    """Map the bottle's global default_backend (the toolbar picker's id) onto a
    concrete classic-engine backend id, or None if it isn't a concrete choice
    (unset, or itself "auto" -- nothing above the bottle level to defer to)."""
    raw = str(bottle_cfg.get("default_backend") or "").lower()
    if not raw or raw == BACKEND_AUTO:
        return None
    if raw in ("vr", "openxr"):
        return BACKEND_DXMT_OPENXR
    if raw in (BACKEND_DXMT, BACKEND_DXVK, BACKEND_D3DMETAL3, BACKEND_DXMT_OPENXR):
        return raw
    return None


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
    # Bradar opengl = the wine-staging 11.8 wined3d->OpenGL build + the macdrv GL 3.2 clamp,
    # now folded into the unified wine (no more separate wine_devel). wine_devel maps here too.
    if b in ("opengl", "wine_devel", "gl"):
        return "opengl"
    return "d3dmetal"


def _rosetta_x87_loader() -> Optional[str]:
    """RosettaHack x87+JIT loader shipped with the engine, or None if unusable.

    Rosetta translates x87 through a slow generic path, and 32-bit titles do
    their float maths on the x87 stack rather than in SSE -- OMSI 2 is the
    textbook case. runtime_loader patches those handlers in the target process
    before it runs.

    We only hand wine the PATH here; wine decides whether to USE it, per process,
    from the target PE's machine word (dlls/ntdll/unix/loader.c,
    use_rosetta_x87_loader). That is deliberate: the 32-bit-only rule is then
    structural rather than something every caller has to remember, and a 64-bit
    child of a 32-bit process still gets a plain loader.

    Apple Silicon only -- there is no Rosetta to patch on an Intel Mac.
    """
    if not _is_apple_silicon():
        return None
    d = _unified_build_dir() / "mnc-rosetta"
    loader = d / "runtime_loader"
    # the loader mmaps libRuntimeRosettax87 into the target and looks for it
    # NEXT TO ITSELF, so a half-staged dir is worse than none: it would attach,
    # fail, and take the launch with it.
    if not (loader.is_file() and os.access(loader, os.X_OK)
            and (d / "libRuntimeRosettax87").is_file()):
        return None
    return str(loader)


def _native_d3d9_staged(prefix: str) -> bool:
    """True only when a NATIVE d3d9 is realy sat in this prefix's system32.

    "d3d9=n" means native ONLY. It does not mean "prefer native" -- if no native d3d9 is
    there, wine does not quietly fall back to its builtin, the load fails outright with
    c0000135 and every game that links d3d9 dies before it draws anything. CS2 links it from
    rendersystemdx11.dll, so it died on "FATAL ERROR: Failed to load rendersystemdx11.dll,
    which is the default rendersystem and should not fail to load", which points at the game
    files and not at us. Games started FROM Steam inherit Steam's environment, so the override
    reached them too, whether or not they were launched through the launcher.

    We were setting it on every Apple Silicon machine while d3d9_dxmt.dll shipped in no pack
    at all, so the promised native d3d9 could never be staged. Key the override on the file
    being present insted of on the intent to stage one.

    Resolved through _d3d_pack_file() rather than by joining the flat name: in a
    layout-2 pack that file is dxmt/d3d9.dll, so a direct join would find nothing,
    this would answer False on every machine, and DXMT d3d9 would go quietly
    unused. (The pack gap this gate was written for is itself fixed there --
    stage_unified_d3d_pack copies the tree wholesale instead of walking a list
    that never named d3d9.)
    """
    src_dir = _unified_d3d_dir()
    src = _d3d_pack_file(src_dir, "d3d9_dxmt.dll") if src_dir else None
    if src is None:
        return False
    dst = Path(prefix) / "drive_c" / "windows" / "system32" / "d3d9.dll"
    try:
        return dst.is_file() and dst.stat().st_size == src.stat().st_size
    except Exception:
        return False


def _unified_env(prefix: str, game_backend: str, metal_hud: bool = False,
                 for_steam: bool = False, gst_debug: str = "",
                 cef_safe_mode: bool = False,
                 debug: bool = False, x87_jit: bool = True,
                 x87_opts: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Env for the unified wine. Steam exes always render via DXMT (loader gate);
    non-steam games follow MNC_GAME_BACKEND. GStreamer (MF/H.264 video) is wired for
    GAMES ONLY -- Steam CEF crashes if it touches GStreamer so it gets none.
    cef_safe_mode generalizes the Steam/EA CEF workaround (winegstreamer-block +
    GPU-spoof flag injection) to any launch that opted in via force_dxmt_cef --
    e.g. arbitrary "Applications" whose own CEF/Electron helper subprocesses
    have no fixed name to hardcode, unlike Steam's/EA's."""
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
                     "/usr/local/opt/fontconfig/lib", "/usr/local/opt/gnutls/lib", _WINE_STABLE_LIB,
                     "/usr/local/opt/sdl2/lib", "/usr/local/opt/glib/lib",
                     "/usr/local/opt/gettext/lib",
                     # bundled x86_64 freetype/fontconfig closure so boxes WITHOUT Homebrew still
                     # resolve libfreetype (else "Wine cannot find the FreeType font library" +
                     # fontless games). DYLD_FALLBACK matches by leaf name when the Homebrew abs
                     # paths above are absent. After Homebrew so existing dev setups are unchanged.
                     str(PORTABLE_DIR / "mnc-fonts"), str(PORTABLE_DIR / "mnc-tls"), str(PORTABLE_DIR / "mnc-vulkan"), str(PORTABLE_DIR / "mnc-sdl"),
                     "/usr/lib"])
    # Bradar d3dcompiler_47=n,b -> the real MS DLL we provision (native FIRST) with wines weak
    # builtin as fallbak (NEVER native alone, winetricks #2344).
    #
    # Bradar mscoree + mshtml are deliberately NOT disabled here any more (2026-08-05). Both
    # were inherited whole from the Apple GPTK / CrossOver D3DMetal boilerplate string and then
    # carried into the unified engine, where they applied to EVERY launch rather than just the
    # D3DMetal targets they were copied for. Disabling mscoree is not a no-op for non-.NET
    # apps -- wines PE loader routes ANY IL-only image through fixup_imports_ilonly(), which
    # hard-requires mscoree.dll, so a disabled mscoree makes LoadLibraryEx() of an IL-only
    # assembly fail with STATUS_DLL_NOT_FOUND. .NET Core hits that on its FIRST assembly:
    # System.Runtime.dll is a pure IL-only facade, so CoreCLR throws EEFileLoadException
    # (HRESULT 0x8007007E) out of coreclr_execute_assembly and the process dies before any
    # window appears. That is what killed LuaTools (.NET 8 WPF) on every launch, and it is the
    # same override behind EA App's installer 1603/JunoInitializeSession failure documented in
    # _download_and_run_eaapp_setup. Epic/Amazon launches never passed needs_dotnet at all, so
    # nothing there could ever opt out of it either.
    #
    # Bradar leaving them at wines default costs nothing measurable: both are LAZY -- the loader
    # only maps them when something actually loads an IL-only image / an HTML control, so a game
    # that never touches .NET never loads mscoree (measured: exactly one mscoree map across a
    # full LuaTools startup trace, and zero Mono errors with wine-mono not even installed --
    # fixup_imports_ilonly only needs mscoree to export _CorDllMain, not a live Mono runtime).
    # The prefix-creation hang that originally motivated suppressing Mono is fixed properly at
    # the engine level instead, in dlls/appwiz.cpl/addons.c (install_addon() returns early), and
    # that patch's own comment notes it makes prefix creation fast with NO env overrides.
    #
    # Bradar needs_dotnet still matters at the CALL SITES -- it gates _install_wine_mono() and
    # _apply_gecko_regedit(), which provision the actual Mono/Gecko packages that a real CLR or
    # a rendering mshtml needs. It just no longer decides whether the DLLs may load at all.
    # Bradar msvcp140_2 + vcruntime140_1 native: UE bootstrappers (BootstrapPackagedGame)
    # dont trust the VC\Runtimes reg keys -- they read the VERSION RESOURCE of those two
    # DLLs in system32 n compare it to the redist they ship. wines builtins r stamped
    # 14.42.34433, so a game shippin 14.42.34438 (Satisfactory) allways fails the check,
    # pops "Microsoft Visual C++ 2015-2022 Redistributable (x64) is required" n then its
    # vc_redist.x64.exe Burn bundle fault-storms at 100% CPU forever under the HACK22 wine
    # = launch wedged w/ no window. The real MS DLLs r allready in system32 (the redist
    # installs them), so prefer em; ",b" keeps wines builtin as fallback on prefixes that
    # dont have em, which behaves exactly as before.
    dll_ovr = ("winemenubuilder.exe=d;d3dcompiler_47=n,b;"
               "msvcp140_2,vcruntime140_1=n,b;nvapi,nvapi64=")
    # Bradar d3d9 -> DXMT, but only on Apple GPUs. DXMT calls non-Apple GPU support
    # "experimental" (dxmt_device.cpp: it drops to Metal 3.1 and loses the features it
    # wants), and wines wined3d is the mature path there, so an Intel Mac must keep the
    # builtin. The switch is the override alone: _stage_unified_d3d9() only drops the
    # native DXMT d3d9 into the prefix on Apple Silicon, and without "=n" wine loads its
    # own builtin from the wine tree regardless of what sits in system32.
    if _is_apple_silicon() and _native_d3d9_staged(prefix):
        dll_ovr += ";d3d9=n"
    env.pop("ROSETTA_X87_PATH", None)   # never inherit a stale one from the shell
    for _stale in ("ROSETTA_X87_EXTENDED_FPR_SCRATCH", "ROSETTA_X87_FAST_ROUND",
                   "ROSETTA_X87_F32_ARITH", "ROSETTA_X87_F32_NARROW",
                   "ROSETTA_X87_FAST_RECIP_DIV"):
        env.pop(_stale, None)
    if x87_jit:
        _x87 = _rosetta_x87_loader()
        if _x87:
            env["ROSETTA_X87_PATH"] = _x87
            # Opt-in tuning flags, surfaced in the UI only while x87 is enabled.
            # Every one is off unless the bottle asks for it: the first two are
            # semantics-preserving, the last three are explicitly not bit-exact
            # (f32_arith changes
            # intermediate precision, fast_recip_div is up to 1 ULP off -- and a
            # 1 ULP error inside a convergence loop is a hang, not a wrong pixel).
            for _key, _var in (("x87_extended_fpr",   "ROSETTA_X87_EXTENDED_FPR_SCRATCH"),
                               ("x87_fast_round",     "ROSETTA_X87_FAST_ROUND"),
                               ("x87_f32_arith",      "ROSETTA_X87_F32_ARITH"),
                               ("x87_fast_recip_div", "ROSETTA_X87_FAST_RECIP_DIV")):
                if (x87_opts or {}).get(_key):
                    env[_var] = "1"
            # F32_ARITH's chain pass is gated on F32_NARROW upstream
            # (X87IROptimize: f32_chain_on = f32_narrow_on && f32_arith), and
            # narrowing is opt-in since a345363d. Setting ARITH alone is a
            # silent no-op, so the one UI switch turns on both.
            if (x87_opts or {}).get("x87_f32_arith"):
                env["ROSETTA_X87_F32_NARROW"] = "1"
    env.update({
        "WINEPREFIX": str(prefix),
        # msync OFF by default. The bundled unified wine is msync-capable (server
        # protocol 931), but turnin msync ON crash-loops the Steam CEF webhelper on a
        # COLD boot (the cold ncalrpc service-startup storm makes an rpcrt4 error path
        # blow up) -- warm boots r fine but we cant tell cold from warm at launch, so we
        # keep it dormant. Steam goes thru _unified_env n never calls _apply_sync_env, so
        # this value governs the Steam path outright. Flip back to "1" (n re-default
        # game_msync True) once the cold-boot msync fix lands. See steam-msync-port.
        "WINEMSYNC": "0",
        # Bradar the Debug toggle was a no-op for wine logging on the whole unified engine:
        # this was hardcoded "-all", and the Epic path's verbose flag only fed gst_debug.
        # So turning Debug on produced GStreamer chatter and not one extra wine line, on
        # Steam, manual and Epic launches alike. WINE_DEBUG_VERBOSE was only ever wired
        # into the classic path (_apply_backend_env).
        "WINEDEBUG": WINE_DEBUG_VERBOSE if debug else "-all",
        "WINEDBG": "-all",
        "ROSETTA_ADVERTISE_AVX": "1",
        "CX_APPLEGPT_LIBD3DSHARED_PATH": libd3d,
        "CX_APPLEGPTK_LIBD3DSHARED_PATH": libd3d,
        "FONTCONFIG_PATH": "/usr/local/opt/fontconfig/etc/fonts",
        "DYLD_FALLBACK_LIBRARY_PATH": dyld,
        "MNC_DYLD": dyld,
        "WINEDLLOVERRIDES": dll_ovr,
        "MNC_STEAM_DXMT": "1",
        # Bradar skip the slow i386 Wow64Install during wineboot (10s vs 309s) and keep
        # the PE loader resolving 32-bit builtins from the wine lib dir post-bootstrap
        "MNC_SKIP_WOW64_INSTALL": "1",
        "MNC_GAME_BACKEND": game_backend,
        # Bradar generalizes is_steam_client_process()'s hardcoded name-list gate (loader.c) and
        # kernelbase's MNC_WEBHELPER_FLAGS/MNC_EA_WEBHELPER_EXTRA_FLAGS name checks (process.c) to
        # any launch, not just Steam/EA -- inherited by the whole process tree so it reaches CEF
        # helper subprocesses regardless of what they're named.
        "MNC_CEF_SAFE_MODE": "1" if cef_safe_mode else "0",
        # Bradar MNC_EA_WEBHELPER_EXTRA_FLAGS is deliberately NOT set (the engine still honours
        # it as a manual escape hatch for a CEF app that needs extra switches -- it just has no
        # default any more). It used to carry "--single-process" on the theory that EA App's
        # multi-process CEF would make its GPU subprocess build a Metal swapchain for a HWND
        # owned by another process, which DXMT rejects. 2026-07-25: measured, and BOTH halves of
        # that were wrong. --in-process-gpu (in MNC_WEBHELPER_FLAGS below) already puts the GPU
        # in the browser process -- the one that owns the HWND -- so with a normal multi-process
        # CEF there are ZERO GPU-process crashes, zero cross-process swapchain rejections and
        # zero "Failed to get metal layer" (the earlier blue-window run that prompted this had
        # NO flags at all on its command line, so --in-process-gpu had never actually been tried
        # on its own). And --single-process actively BROKE the app: it collapses the renderer
        # into the browser process, so the renderer-side OnContextCreated that injects
        # qt.webChannelTransport never runs -> EA's qwebchannelwrapper.js sees `qt` undefined,
        # falls back to `new WebSocket("ws://127.0.0.1:4695")` which nothing ever listens on,
        # times out after its own 30s CHANNEL_CONNECTION_TIMEOUT and leaves onLoginSuccess
        # undefined -- sign-in completed but the UI never advanced past the login page. CEF
        # documents single-process as debug-only; don't reach for it.
        # Bradar GPU-spoof so Steam CEF accepts ANGLE d3d11 -> DXMT (null-GPU crashes SwiftShader)
        # this is the exact load-bearing set from the proven steam-unified-run.sh
        # Bradar THE BLACK-WINDOW FIX (2026-08-13, user-confirmed "oh it renders").
        # Steam does NOT let CEF draw to the window: it renders offscreen and composites the
        # result itself (CBrowserComposerSystem). With gpu compositing ON, CEF hands that
        # result over as a d3d11 SHARED TEXTURE -- and that handoff produces nothing through
        # DXMT, so steam composited an empty surface. Every log stayed clean the whole time
        # (window shown 1337x782 titled 'Steam', FriendsUI ReadyToRender, zero CEF errors,
        # metal presenting) which is exactly why this hid for so long: nothing FAILED, the
        # pixels just never arrived. --disable-gpu-compositing makes CEF hand over plain CPU
        # bitmaps insted, which steam blits fine.
        #
        # --disable-software-rasterizer had to GO at the same time: it forbids the software
        # path that cpu compositing needs. The two only work as a pair.
        "MNC_WEBHELPER_FLAGS": ("--no-sandbox --in-process-gpu --use-gl=angle --use-angle=d3d11 "
            "--disable-gpu-compositing "
            "--ignore-gpu-blocklist --disable-gpu-driver-bug-workarounds "
            "--disable-gpu-watchdog --disable-gpu-process-crash-limit --gpu-no-context-lost "
            "--disable-gpu-process-for-dx12-info-collection --no-delay-for-dx12-vulkan-info-collection "
            "--gpu-vendor-id=0x1002 --gpu-device-id=0x67df --gpu-driver-version=20.45.0 "
            "--gpu-sub-system-id=0 --gpu-revision=0 "
            # Bradar trim the CEF cost -- 1 renderer proc insted of 9 (each one was runnin the
            # wineserver-round-trip IPC loop that dominate wh.sample), + kill the native-occlusion
            # recalc n the chromecast discovery utility proc = fewer background wakeups.
            # if a steam panel ever go blank/white its this cap -> bump to 2
            "--renderer-process-limit=1 --disable-features=CalculateNativeWinOcclusion,MediaRouter,"
            "Translate,OptimizationHints,AutofillServerCommunication "
            "--disable-smooth-scrolling "
            # Bradar kill the CEF background chatter -- none of it works usefully under wine + it
            # just burns wakeups/net during the boot window. breakpad here is CEFs OWN crash
            # reporter (steamwebhelper), NOT steam.exe crashhandler, so the self-heal dumps we key
            # on r untouched. component/domain-reliability/first-run r pure startup dead weight.
            "--disable-background-networking --disable-component-update "
            "--disable-domain-reliability --disable-breakpad --no-first-run"),
    })
    for var in ("GTK_PATH", "WINEPATH", "VKD3D_PROTON_PATH", "GALLIUM_DRIVER", "DXVK_LOG_PATH"):
        env.pop(var, None)
    if metal_hud:
        env["MTL_HUD_ENABLED"] = "1"
        # MTL_DEBUG_BUILD too, matching the classic d3dmetal heredoc path, which has
        # always exported both. The unified path only ever set MTL_HUD_ENABLED and the
        # HUD did not appear on a DXMT game.
        env["MTL_DEBUG_BUILD"] = "1"
    # GStreamer is GAMES ONLY. Steam must never touch it (its CEF crashes) so strip
    # any inherited plugin path. For games force software H.264 (avdec_h264) and disable
    # VideoToolbox vtdec which crashes the decode under Rosetta x86_64.
    # The macdrv OpenGL 3.2 clamp, so SDL3 / OpenGL 3.2 titles (Mewgenics) get a workin
    # forward-compat core context insted of ERROR_INVALID_VERSION. env-gated inside
    # winemac.drv, so it is a no-op for anything that never asks for a GL context.
    #
    # Set it for STEAM as well, not just games. A game started from inside Steam inherits
    # Steam's environment, so while this lived on the game-only branch Mewgenics never saw
    # it -- launching from the Steam library gave "Could not create gl context" no matter
    # which backend was picked. Steam's own UI is unaffected: its CEF is driven through
    # ANGLE on d3d11, so it never takes the GL path this touches.
    env["WINE_MAC_GL_CONTEXT_CLAMP"] = "1"
    if for_steam:
        # GStreamer stays OFF for steam itself (a second gst core crashes the client); games
        # get it below for their intro videos.
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
    # Steam comes through here and never reaches _apply_sync_env, so this is the
    # only place its msync mode is decided -- and it is the launch that usually
    # starts the wineserver every later game inherits.
    env = _reconcile_msync(env, str(prefix))
    return env


def _commonredist_hasrun_reg_cmds(prefix: str, wine: str) -> str:
    """Build shell 'wine reg add' lines that pre-set Steam's CommonRedist 'has-run'
    keys. Steam only runs a redist install-script (.NET / VC++ / DirectX) when its
    per-redist has-run key is MISSING - n those installers HANG forever under wine
    (the bootstrapper Setup.exe never exits), wedgin the launch on "Running install
    script (Microsoft .NET Framework)". pre-settin the keys makes steam SKIP them.
    safe: those runtimes r already present (wine builtins / the .NET reg keys) n the
    installers never actualy work under wine anyway. (proven on World War 3 / .NET 4.6.2)"""
    shared = _steam_dir(prefix) / "steamapps" / "common" / "Steamworks Shared"
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


def _unreg_str(value: str) -> str:
    """Decode a wine system.reg string value: \\\\ -> \\, \\" -> ", \\xNNNN -> the char."""
    out, i = [], 0
    while i < len(value):
        c = value[i]
        if c == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            if nxt == "x" and i + 5 < len(value):
                try:
                    out.append(chr(int(value[i + 2:i + 6], 16))); i += 6; continue
                except ValueError:
                    pass
            out.append(nxt); i += 2; continue
        out.append(c); i += 1
    return "".join(out)


def _ea_installed_games(prefix) -> List[Dict[str, Any]]:
    """Every EA-App-installed game in a prefix, from EA's OWN record.

    The EA app writes HKLM\\Software\\EA Games\\<Title> (plus the Wow6432Node mirror) with
    DisplayName + "Install Dir" for each title it installs -- live-confirmed on Battlefield 4.
    That is authoritative, covers a custom install location, and needs no per-title knowledge,
    so prefer it over globbing a hardcoded "EA Games" folder. Returns [{"name", "dir"}] for
    the entries whose install dir actually exists on disk."""
    try:
        reg = (Path(prefix).expanduser() / "system.reg").read_text(errors="ignore")
    except Exception:
        return []
    out: Dict[str, Dict[str, Any]] = {}
    # [Software\\EA Games\\<Title>]  or  [Software\\Wow6432Node\\EA Games\\<Title>]
    # note the trailing ".*": wine writes the section's mtime after the closing bracket
    for m in re.finditer(r'(?m)^\[Software\\\\(?:Wow6432Node\\\\)?EA Games\\\\([^\]\\\\]+)\].*$', reg):
        body = reg[m.end():]
        end = body.find("\n[")
        body = body[:end] if end != -1 else body
        dm = re.search(r'(?m)^"DisplayName"="([^"]*)"', body)
        # value is backslash-escaped: a run of non-quote chars, or an escape pair (\\ , \x2122)
        im = re.search(r'(?m)^"Install Dir"="((?:[^"\\]|\\.)*)"', body)
        if not im:
            continue
        win_dir = _unreg_str(im.group(1))
        host = _win_path_to_host(Path(prefix).expanduser(), win_dir.rstrip("\\/") )
        if not host or not host.is_dir():
            continue
        name = _unreg_str(dm.group(1)) if dm else _unreg_str(m.group(1))
        out[str(host)] = {"name": name, "dir": host}   # dedup the Wow6432Node mirror
    return list(out.values())


def _title_tokens(title: str) -> List[str]:
    """Split a game title into comparable tokens, dropping trademark marks, punctuation and
    case: "Battlefield 4(tm) Premium Edition" -> ["battlefield", "4", "premium", "edition"]."""
    return re.findall(r"[a-z0-9]+", (title or "").lower())


def _titles_match(a: List[str], b: List[str]) -> bool:
    """Whether two tokenized titles name the SAME game across two stores.

    One store's title routinely carries an edition/bundle suffix the other's does not
    ("Battlefield 4(tm) Premium Edition" on Epic vs "Battlefield 4(tm)" in EA's registry), so
    accept a token-prefix match. But a purely NUMERIC tail means a different entry in a series,
    not an edition -- without that guard "skate." would match an installed "Skate 3", since
    "skate" is a prefix of it either way."""
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if not short or len("".join(short)) < 4:
        return False
    if long_[:len(short)] != short:
        return False
    extra = long_[len(short):]
    return not (extra and all(t.isdigit() for t in extra))


def _ea_install_for_title(title: str, prefix) -> Optional[Dict[str, Any]]:
    """Match a store title to an EA-App install of the same game, or None."""
    want = _title_tokens(title)
    best, best_len = None, -1
    for ea in _ea_installed_games(prefix):
        have = _title_tokens(ea["name"])
        if _titles_match(want, have) and len(have) > best_len:
            best, best_len = ea, len(have)   # most specific match wins
    return best


def _ea_app_dir(prefix) -> Path:
    """The EA App (EA Desktop) install dir in a prefix -- the innermost folder actually
    containing EADesktop.exe. Same both-arch-paths caveat as _steam_dir: EA App installs
    as 64-bit, but check Program Files (x86) too in case a fast-booted prefix's WoW64
    redirection lands it somewhere unexpected.

    EA App installs (and self-updates) into a VERSIONED subfolder --
    "EA Desktop/<version>/EA Desktop/EADesktop.exe" -- not directly under "EA Desktop/"
    (confirmed live: checking the un-versioned path never matched, so cmd_install_ea_app
    re-ran the installer on every single click even though EA App was already there).
    An in-progress self-update stages into a second "<version>-<unix timestamp>" folder
    before promoting it -- skip those (no reliable "-" in a real EA version string) and
    prefer the highest real version if more than one is present."""
    dc = Path(prefix).expanduser() / "drive_c"
    for base_name in ("Program Files", "Program Files (x86)"):
        base = dc / base_name / "Electronic Arts" / "EA Desktop"
        candidates = sorted(
            (v for v in base.glob("*/EA Desktop/EADesktop.exe") if "-" not in v.parent.parent.name),
            key=lambda p: p.parent.parent.name,
        )
        if candidates:
            return candidates[-1].parent
    return dc / "Program Files" / "Electronic Arts" / "EA Desktop"


_STEAM_SEED_EXCLUDES = ["steamapps/", "userdata/", "config/", "logs/", "dumps/",
                        "appcache/", ".crash", "ssfn*", "*.log"]
# a COMPLETE Steam client has all of these -- used to reject a half-built template + validate a source
# Valves client-update CDN, in the order the bootstrapper itself prefers them. Any one of these
# serves both the manifest and the packages, so we just walk the list until one answers.
_STEAM_CLIENT_CDN_HOSTS = (
    "https://client-update.akamai.steamstatic.com",
    "https://client-update.fastly.steamstatic.com",
    "https://client-update.steamstatic.com",
)
_STEAM_CLIENT_CRIT = ("steamclient.dll", "steamclient64.dll", "steam.exe")
_STEAM_CLIENT_CRIT_DIRS = ("bin", "steamui", "clientui")
_steam_tmpl_lock = threading.Lock()


def _steam_client_complete(d: Path) -> bool:
    """True if d holds a COMPLETE Steam client (not a half-finished/interrupted rsync). Guards the
    template cache so an interrupted build never poisons every future seeded bottle."""
    try:
        return (all((d / f).is_file() for f in _STEAM_CLIENT_CRIT)
                and all((d / s).is_dir() for s in _STEAM_CLIENT_CRIT_DIRS))
    except Exception:
        return False


def _steam_source_crashing(d: Path) -> bool:
    """True if the Steam dir d shows a crash storm -- dont build the template from a broken client."""
    try:
        if (d / ".crash").exists():
            return True
        dmp = d / "dumps"
        if dmp.is_dir() and sum(f.stat().st_size for f in dmp.glob("*.dmp") if f.is_file()) > 5_000_000:
            return True
    except Exception:
        pass
    return False


def _steam_client_version(d: Path) -> int:
    """The installed Steam client version from package/steam_client_win64.manifest, or -1. A NEWER
    steamclient.dll mtime does NOT mean a newer CLIENT (a partial/old-build copy can have a fresh
    mtime) -- the manifest version is the authoritative + bootable-currency signal, which is why the
    template source is ranked on THIS, not dll mtime. Higher = more current = less likely to eat the
    Valve mandatory-update crash path on a fresh seed."""
    try:
        m = (d / "package" / "steam_client_win64.manifest").read_text(errors="ignore")
        mt = re.search(r'"version"\s*"(\d+)"', m)
        return int(mt.group(1)) if mt else -1
    except Exception:
        return -1


def _refresh_seed_if_bottle_newer(prefix: str) -> bool:
    """Opportunistic re-cache: if a bottle has self-updated to a client NEWER than the cached template
    (e.g. after Valves next mandatory update finally downloads + applies), refresh deps/steam-client
    from it so the seed never goes stale + never re-seeds fresh bottles onto a crash-looping old
    client. Cheap: only fires when the bottles manifest version is STRICTLY higher than the templates,
    and the bottle is complete + not crash-storming. No-op otherwise. Returns True if it refreshed."""
    try:
        cache = PORTABLE_DIR / "steam-client"
        src = _steam_dir(prefix)
        if not (src.is_dir() and _steam_client_complete(src)) or _steam_source_crashing(src):
            return False
        bv = _steam_client_version(src)
        tv = _steam_client_version(cache) if cache.is_dir() else -1
        if bv <= tv or bv < 0:
            return False
        with _steam_tmpl_lock:
            log(f"_refresh_seed_if_bottle_newer: bottle client v{bv} > template v{tv} -> refreshing seed from {src}")
            cache.mkdir(parents=True, exist_ok=True)
            cmd = ["rsync", "-a", "--delete"]
            for ex in _STEAM_SEED_EXCLUDES:
                cmd += ["--exclude", ex]
            cmd += [str(src) + "/", str(cache) + "/"]
            r = subprocess.run(cmd, timeout=1800)
            if r.returncode in (0, 24) and _steam_client_complete(cache):
                try: (cache / ".mnc_steam_client_ok").write_text(f"refreshed v{bv}")
                except Exception: pass
                return True
    except Exception as exc:
        log(f"_refresh_seed_if_bottle_newer failed (non-fatal): {exc}")
    return False


_STEAM_CDN_VER_TTL = 6 * 3600
_steam_cdn_ver_cache: Dict[str, Any] = {}


def _cdn_steam_client_version() -> Optional[int]:
    """The CURRENT Steam client version per Valves manifest, or None if unreachable.

    Cached for a few hours so this costs one 7KB fetch a day, not one per launch, and so
    being offline never slows a launch down.
    """
    hit = _steam_cdn_ver_cache.get("v")
    if hit and (time.time() - hit[0]) < _STEAM_CDN_VER_TTL:
        return hit[1]
    for host in _STEAM_CLIENT_CDN_HOSTS:
        try:
            out = subprocess.run(["/usr/bin/curl", "-fsSL", "--max-time", "20",
                                  f"{host}/steam_client_win64"],
                                 capture_output=True, text=True, timeout=25).stdout
            m = re.search(r'"version"\s*"(\d+)"', out or "")
            if m:
                ver = int(m.group(1))
                _steam_cdn_ver_cache["v"] = (time.time(), ver)
                return ver
        except Exception:
            continue
    return None


def _steam_template_outdated(cache: Path) -> bool:
    """True when the cached template is behind the client Valve is currently shipping.

    A seeded bottle inherits the templates version, and once Valve makes a client update
    MANDATORY an outdated client cannot just run -- it has to update itself first, and that
    is the path that ends on "Steam needs to be online to update". Fetching the client fresh
    is the whole point of the CDN seed, but that only ever fired when there was no template
    at all, so anyone who had built one earlier stayed pinned to it forever.

    Fail OPEN: if we cannot reach the CDN we keep whatever we have rather than refuse to
    seed, since an old client still beats no client.
    """
    try:
        local = _steam_client_version(cache)
    except Exception:
        return False
    if not local:
        return False
    remote = _cdn_steam_client_version()
    return bool(remote and remote > local)


def _steam_client_template() -> Optional[Path]:
    """Cached CLEAN Steam client (no games / userdata / login) used to SEED fresh bottles, because the
    Steam bootstrapper's first-run download is BROKEN under our wine (32-bit HACK22 storm on the
    unified wine; 'failed to create updater window' on the pre-HACK22 wine). Built ONCE via rsync (w/
    excludes) from a working prefixs full client. Cached in deps/steam-client so seeding a new bottle
    is a ~instant same-volume clone. Marker-guarded (never caches a HALF-built client -> steamclient.dll
    copies before the big steamui/bin subtrees, so a presence check alone would cache a partial build),
    lock-serialized (concurrent create+launch cant clobber each other mid-rsync), + picks the NEWEST
    healthy source. Returns the template dir, or None when theres no source. See steamsetup notes."""
    cache = PORTABLE_DIR / "steam-client"
    marker = cache / ".mnc_steam_client_ok"
    if marker.is_file() and _steam_client_complete(cache):
        # Refresh a template Valve has moved on from, else every bottle seeded off it starts
        # life needing a mandatory update it may not survive.
        if _steam_template_outdated(cache):
            log("_steam_client_template: cached client is behind the current one -> refreshing from the CDN")
            with _steam_tmpl_lock:
                if _build_steam_client_from_cdn(cache):
                    try: marker.write_text("cdn")
                    except Exception: pass
                else:
                    log("_steam_client_template: refresh failed, keeping the client we have")
        return cache
    if _steam_client_complete(cache):            # complete but unmarked (older build) -> adopt
        try: marker.write_text("adopted")
        except Exception: pass
        return cache
    with _steam_tmpl_lock:
        if _steam_client_complete(cache):        # another thread just built it
            if not marker.is_file():
                try: marker.write_text("adopted")
                except Exception: pass
            return cache
        # pick the HEALTHIEST source: a COMPLETE, non-crashing client with the HIGHEST manifest
        # VERSION (a fresh dll mtime is NOT currency -- an old-build copy can carry a new mtime; the
        # manifest version is what decides whether a seeded bottle boots or eats the mandatory-update
        # crash). tie-break on dll mtime.
        best = None
        best_key = (-1, -1.0)  # (client version, dll mtime)
        try:
            cands = list(_load_prefixes())
        except Exception:
            cands = []
        for pfx in cands:
            for sub in ("Program Files (x86)", "Program Files"):
                d = Path(pfx) / "drive_c" / sub / "Steam"
                sc = d / "steamclient.dll"
                if not (sc.is_file() and _steam_client_complete(d)) or _steam_source_crashing(d):
                    continue
                try:
                    mt = sc.stat().st_mtime
                except Exception:
                    mt = 0.0
                key = (_steam_client_version(d), mt)
                if key > best_key:
                    best_key = key
                    best = d
        if not best:
            # Nothing local to clone from -- a first-ever install. Rather than hand the bottle to
            # the broken bootstrapper first-run, fetch the client from Valves CDN into the same
            # template slot, so this costs one download ever and every later bottle is an instant
            # clone exactly as if the user had allready had Steam.
            if _build_steam_client_from_cdn(cache):
                try: marker.write_text("cdn")
                except Exception: pass
                return cache
            return None
        cache.mkdir(parents=True, exist_ok=True)
        log(f"_steam_client_template: building cached clean Steam client from {best} (one-time, ~1.4G)")
        cmd = ["rsync", "-a", "--delete"]
        for ex in _STEAM_SEED_EXCLUDES:
            cmd += ["--exclude", ex]
        cmd += [str(best) + "/", str(cache) + "/"]
        try:
            r = subprocess.run(cmd, timeout=1800)
        except Exception as exc:
            log(f"_steam_client_template: build failed: {exc}")
            return None
        # only cache a VERIFIED-complete build (rc 24 = source file vanished mid-copy, tolerable)
        if r.returncode not in (0, 24) or not _steam_client_complete(cache):
            log(f"_steam_client_template: incomplete build (rc={r.returncode}) -> not caching")
            return None
        try: marker.write_text("built")
        except Exception: pass
        return cache


def _build_steam_client_from_cdn(dest: Path) -> bool:
    """Build a COMPLETE, CURRENT Steam client in `dest` straight from Valves client-update CDN.

    This exists because of a chicken-and-egg gap that made MacNdCheese unusable for brand new
    users. The Steam bootstrappers first-run download does not work under our wine, so we seed a
    bottle by cloning a cached template insted -- but that template can only be built from a
    working client the user ALLREADY has. Someone installing for the first time has none, so
    _steam_client_template() returned None, seeding no-oped, and the bottle fell back to the very
    bootstrapper path thats broken. Steam then sat there insisting it "needs to be online to
    update" on a perfectly good connection, and no amount of reinstalling wine could help, since
    wine was never the problem. In other words the workaround for the broken path was only
    reachable by the people who did not need it.

    So do what the bootstrapper would have done, ourselves. The manifest lists every package with
    a plain .zip as well as the LZMA .zip.vz, and curl plus zipfile handle those fine -- no VZ
    decoder, and no 32-bit NSIS installer either, since steam.exe ships in the packages too.

    Packages are cached under deps/steam-pkgcache so a re-run (or a second bottle) re-uses them.
    Returns True only when the result passes _steam_client_complete."""
    import hashlib, zipfile
    cachedir = PORTABLE_DIR / "steam-pkgcache"
    cachedir.mkdir(parents=True, exist_ok=True)

    def _get(path: str, dst: Path) -> bool:
        for host in _STEAM_CLIENT_CDN_HOSTS:
            try:
                rc = subprocess.run(["/usr/bin/curl", "-fsSL", "--max-time", "900",
                                     "-o", str(dst), f"{host}/{path}"],
                                    capture_output=True, timeout=960).returncode
                if rc == 0 and dst.is_file() and dst.stat().st_size > 0:
                    return True
            except Exception as exc:
                log(f"_build_steam_client_from_cdn: {host} failed for {path}: {exc}")
        return False

    man = cachedir / "steam_client_win64.vdf"
    if not _get("steam_client_win64", man):
        log("_build_steam_client_from_cdn: could not fetch the client manifest")
        return False
    text = man.read_text(errors="replace")
    mv = re.search(r'"version"\s*"(\d+)"', text)
    version = mv.group(1) if mv else "unknown"

    pkgs = []
    for m in re.finditer(r'^\t"(\w+)"\s*\n\t\{(.*?)^\t\}', text, re.S | re.M):
        body = m.group(2)
        f = re.search(r'"file"\s*"([^"]+)"', body)
        sha = re.search(r'"sha2"\s*"([0-9a-f]+)"', body)
        if f:
            pkgs.append((m.group(1), f.group(1), sha.group(1) if sha else None))
    if not pkgs:
        log("_build_steam_client_from_cdn: manifest parsed to zero packages")
        return False

    log(f"_build_steam_client_from_cdn: fetching Steam client {version} "
        f"({len(pkgs)} packages) -- first run only, later bottles clone the cached template")
    # Extract to a staging dir and only swap it in once its verified complete, so an interrupted
    # download can never leave a half-client that the presence checks would happily accept.
    staging = dest.parent / (dest.name + ".mnc-partial")
    subprocess.run(["rm", "-rf", str(staging)], capture_output=True)
    staging.mkdir(parents=True, exist_ok=True)
    for i, (name, fn, sha) in enumerate(pkgs, 1):
        blob = cachedir / fn
        if sha and blob.is_file():
            try:
                if hashlib.sha256(blob.read_bytes()).hexdigest() != sha:
                    blob.unlink()
            except Exception:
                pass
        if not blob.is_file():
            log(f"_build_steam_client_from_cdn: [{i}/{len(pkgs)}] {name}")
            if not _get(fn, blob):
                log(f"_build_steam_client_from_cdn: failed to fetch {name}")
                return False
        if sha:
            try:
                if hashlib.sha256(blob.read_bytes()).hexdigest() != sha:
                    log(f"_build_steam_client_from_cdn: checksum mismatch on {name}")
                    blob.unlink()
                    return False
            except Exception as exc:
                log(f"_build_steam_client_from_cdn: could not checksum {name}: {exc}")
                return False
        try:
            with zipfile.ZipFile(blob) as z:
                for info in z.infolist():
                    # entry names carry WINDOWS separators, so a plain extractall would write
                    # single files literaly called "steam\cached\foo" insted of a tree
                    rel = info.filename.replace("\\", "/")
                    if not rel or rel.endswith("/"):
                        continue
                    tgt = staging / rel
                    tgt.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as src, open(tgt, "wb") as out:
                        shutil.copyfileobj(src, out)
        except Exception as exc:
            log(f"_build_steam_client_from_cdn: could not unpack {name}: {exc}")
            return False

    # Steam reads this back to decide whether it is current; without it the client thinks it has
    # no version and goes straight back to the update path we are avoiding.
    try:
        (staging / "package").mkdir(parents=True, exist_ok=True)
        (staging / "package" / "steam_client_win64.manifest").write_text(text)
    except Exception:
        pass

    if not _steam_client_complete(staging):
        log("_build_steam_client_from_cdn: assembled client is incomplete -- discarding")
        subprocess.run(["rm", "-rf", str(staging)], capture_output=True)
        return False
    subprocess.run(["rm", "-rf", str(dest)], capture_output=True)
    staging.rename(dest)
    log(f"_build_steam_client_from_cdn: built a complete Steam client {version}")
    return True


def _seed_steam_client(prefix: str) -> bool:
    """Give a fresh Steam bottle a WORKING client by cloning the cached template into it (the
    bootstrapper first-run is broken under wine). No-op if the bottle already has a full client
    (steamclient.dll) or no template source exists. Returns True if it seeded a client."""
    dst = Path(prefix) / "drive_c" / "Program Files (x86)" / "Steam"
    try:
        if (dst / "steamclient.dll").exists() or (_steam_dir(prefix) / "steamclient.dll").exists():
            return False
    except Exception:
        pass
    tmpl = _steam_client_template()
    if not tmpl:
        log("_seed_steam_client: no seed source (no existing working Steam client to copy from)")
        return False
    dst.mkdir(parents=True, exist_ok=True)
    # cp -c = APFS clonefile (~0 disk, instant) on the same volume; rsync fallback covers cross-volume.
    subprocess.run(f'cp -c -R {shlex.quote(str(tmpl))}/. {shlex.quote(str(dst))}/ 2>/dev/null',
                   shell=True)
    if not (dst / "steamclient.dll").exists():
        cmd = ["rsync", "-a"]
        for ex in _STEAM_SEED_EXCLUDES:
            cmd += ["--exclude", ex]
        cmd += [str(tmpl) + "/", str(dst) + "/"]
        try:
            subprocess.run(cmd, timeout=1800)
        except Exception as exc:
            log(f"_seed_steam_client: rsync fallback failed: {exc}")
    ok = (dst / "steamclient.dll").exists()
    if ok:
        log("_seed_steam_client: seeded a working Steam client into the bottle "
            "(the bootstrapper first-run is broken under wine)")
    return ok


def _reseed_steam_client(prefix: str) -> bool:
    """REPAIR a present-but-corrupt Steam client (crash-looping) by rsyncing the clean template OVER
    it with --checksum (refreshes even same-size-but-corrupt files, which a plain size+mtime rsync
    would SKIP -- the steamtest client had a same-size steamclient.dll), NO --delete so
    steamapps/userdata/config/login survive. This is the manual steamtest fix, automated; it
    deliberately SKIPS the presence gate that _seed_steam_client uses. Returns True if it refreshed."""
    dst = _steam_dir(prefix)
    tmpl = _steam_client_template()
    if not tmpl or not dst.is_dir():
        return False
    cmd = ["rsync", "-a", "--checksum"]
    for ex in _STEAM_SEED_EXCLUDES:
        cmd += ["--exclude", ex]
    cmd += [str(tmpl) + "/", str(dst) + "/"]
    try:
        subprocess.run(cmd, timeout=1800)
    except Exception as exc:
        log(f"_reseed_steam_client: rsync-over failed: {exc}")
        return False
    log("_reseed_steam_client: refreshed the Steam client from the clean template (crash self-heal)")
    return True


def _bottle_client_outdated(steam_dir: Path) -> bool:
    """True when a bottle's own Steam client is behind the one Valve ships now.

    Distinct from _steam_client_complete: this client is not broken, it is just OLD, so
    neither the seed (needs a missing steamclient.dll) nor the completeness repair fires --
    and yet once an update is mandatory an old client still cannot start, it sits on "Steam
    needs to be online to update" while it tries the first-run download path that does not
    work under our wine. Observed exactly that: a FRESH bottle worked while the same users
    existing bottle kept failing. Fail open when the CDN is unreachable.
    """
    try:
        local = _steam_client_version(steam_dir)
    except Exception:
        return False
    if not local:
        return False
    remote = _cdn_steam_client_version()
    return bool(remote and remote > local)


def _launch_steam_unified(prefix: str, bottle_cfg: Dict[str, Any], params: Dict[str, Any]) -> Any:
    """Launch Steam through the unified wine so its CEF renders via DXMT."""
    global _steam_process, _steam_started_silent, _steam_prefix, _steam_started_ts
    bt = _unified_build_dir()
    # fresh bottle w/ only the broken bootstrapper -> seed a working client (bootstrapper first-run
    # fails under wine). no-op if a full client is already present or theres no seed source.
    _seed_steam_client(str(prefix))
    steam_dir = _steam_dir(prefix)
    # HEAL a client thats PRESENT but INCOMPLETE. Neither guard around this one catches that
    # state: the seed above only fires when steamclient.dll is missing outright, and the crash
    # self-heal below needs real dumps to trigger. A bottle left half-populated by the broken
    # bootstrapper first-run does neither -- it does not crash and it does not look empty, Steam
    # just sits on "needs to be online to update" forever. Repair it from the template (which can
    # now be built from Valves CDN when theres nothing local to clone), so an allready-broken
    # install fixes ITSELF on the next launch insted of the user having to recreate the bottle.
    # rsync-over keeps steamapps/userdata/config/login, so this never costs anyone their games.
    if steam_dir.is_dir() and not _steam_client_complete(steam_dir):
        log("_launch_steam_unified: Steam client is incomplete -> repairing it from the template")
        if not _reseed_steam_client(str(prefix)):
            log("_launch_steam_unified: could not repair the Steam client")
    elif steam_dir.is_dir() and _bottle_client_outdated(steam_dir):
        # Complete but OLD. Left alone it has to run the mandatory self-update, which is the
        # broken path, so bring it up to date from the template ourselves instead. This is why
        # making a new bottle "fixed" it for people while their existing one stayed broken.
        log("_launch_steam_unified: Steam client is out of date -> updating it from the template")
        if not _reseed_steam_client(str(prefix)):
            log("_launch_steam_unified: could not update the Steam client")
    # SELF-HEAL: client present but the PREVIOUS launch crash-STORMED -> re-seed clean (the launch cmd
    # below wipes dumps each run, so dumps here are from the last run). _seed_steam_client is
    # presence-idempotent so it never repairs a present-but-broken client (the steamtest gap).
    # Trigger on the dump TOTAL, NOT .crash: a normal quit (the app SIGKILLs Steam via kill_wineserver)
    # leaves a .crash but ~0 dumps, so keying on .crash would needlessly run the slow --checksum
    # re-seed after every quit. A healthy bottle emits only small transient GPU dumps; a real
    # crash-loop leaves tens of MB (steamtest had 38MB), so 15MB cleanly separates them.
    # Bradar the spoofed-GPU cache (userdata/**/GPUCache) is now KEPT across launches so CEF dont
    # re-warm its shader/program cache on the stable 0x1002 adapter every boot -- we only nuke it as
    # a FALLBACK when the last run crash-stormed (a stale GPU blob CAN reintroduce a startup GPU
    # crash). same crash-storm signal as the client re-seed below.
    _wipe_gpucache = False
    try:
        if (steam_dir / "steamclient.dll").exists():
            _dmp = steam_dir / "dumps"
            _dumps = (sum(f.stat().st_size for f in _dmp.glob("*.dmp") if f.is_file())
                      if _dmp.is_dir() else 0)
            # a crash_steam.exe / assert_steam.exe minidump = steam.exe ITSELF crashed last run.
            # Steam only writes these on a real exception; a clean quit / our SIGKILL produces NONE
            # (verified: a kill leaves .crash but 0 dumps), so keying on the FILE has no kill-false-
            # positive. the old-client-hits-Valves-mandatory-update BRICK is exactly ONE ~300KB
            # crash_steam dump, way under the 15MB storm total -- so trigger on the file too, not just
            # the byte total, else a crash-loopin bottle never self-heals (--disable-breakpad only kills
            # CEFs webhelper reporter, NOT steam.exe's crash handler, so these dumps stay written).
            _steam_crashed = _dmp.is_dir() and (any(_dmp.glob("crash_steam.exe*.dmp"))
                                                or any(_dmp.glob("assert_steam.exe*.dmp")))
            if _dumps > 15_000_000 or _steam_crashed:
                _wipe_gpucache = True
                log(f"Steam crashed last run (steam.exe dump={bool(_steam_crashed)}, {_dumps // 1_000_000}MB total) -> re-seeding a clean client + wiping GPUCache")
                _reseed_steam_client(str(prefix))
    except Exception as _exc:
        log(f"steam self-heal check failed (non-fatal): {_exc}")
    # opportunistic seed re-cache: if THIS bottle self-updated to a newer client on a prior run,
    # refresh the template from it so fresh bottles never get seeded onto a stale (crash-looping) one.
    _refresh_seed_if_bottle_newer(str(prefix))
    steam_exe = steam_dir / "steam.exe"
    if not steam_exe.exists():
        raise FileNotFoundError(f"Steam is not installed in this prefix.\nExpected: {steam_exe}")
    _stage_unified_dlls(str(prefix))
    _stage_unified_mf(str(prefix))
    game_backend = _unified_game_backend(bottle_cfg, params.get("backend", ""))
    _provision_redist_dlls(str(prefix))   # real MS d3dcompiler_47 file-drop for games launchd in-Steam
    # Bradar install the uncoverd SHARED CommonRedist (mfc/physx/...) so games startd from Steams OWN
    # UI (not via MNC's game launch) still get em; skip-listd + marker-gated so its ~instant after 1st.
    try:
        _run_shared_commonredist(str(prefix), game_backend)
    except Exception as exc:
        log(f"shared redist (steam launch) skipped: {exc}")
    env = _unified_env(prefix, game_backend, bottle_cfg.get("metal_hud", False), for_steam=True)
    # Bradar wire the MoltenVK vulkan ICD into steam.exe's env so DXVK games launchd from Steams OWN
    # UI (they inherit steam.exe's env, NOT our per-game _launch_game_unified env) can create a Vulkan
    # instance. without it a Steam-launchd dxvk game crashs in d3d11_dxvk (vkCreateInstance fails ->
    # "Crash!!!"). harmless to steam.exe itself (CEF renders via DXMT->Metal, never touchs the Vulkan
    # loader). mirrors the per-game dxvk block in _launch_game_unified.
    _vk_icd = _find_moltenvk_icd()
    if _vk_icd:
        env["VK_ICD_FILENAMES"] = _vk_icd
        env["VK_DRIVER_FILES"] = _vk_icd
        env.setdefault("DXVK_STATE_CACHE", "0")
    wine = str(bt / "wine")
    wineserver = str(bt / "server" / "wineserver")
    # Bradar collapse the launch-path wineserver churn: the retina regedit + the Steam-Client-Service
    # disable used to run as TWO separate pre-launch `wine` spawns (retina even flushed the hive with
    # `wineserver -w`), and then the launch shells first line `wineserver -k` immediately KILLED that
    # server -- wasting a Rosetta wineserver cold-start + a regedit PE-JIT every launch. now BOTH reg
    # writes r folded into one batch.reg imported by a single `wine regedit` INSIDE the shell AFTER
    # the -k, so steam.exe reuses that same server (2 server lifecycles + 2 wine spawns -> 1).
    _retina = bool(params.get("retina_mode", False))
    _retina_val = "y" if _retina else "n"
    _dpi_hex = "c0" if _retina else "60"  # 192=0xc0, 96=0x60
    batch_reg = (
        "REGEDIT4\n\n"
        "[HKEY_CURRENT_USER\\Software\\Wine\\Mac Driver]\n"
        f'"RetinaMode"="{_retina_val}"\n'
        '"Resolution"="auto"\n\n'
        "[HKEY_CURRENT_USER\\Control Panel\\Desktop]\n"
        f'"LogPixels"=dword:000000{_dpi_hex}\n\n'
        # THE big steady-state win: disable the 32-bit 'Steam Client Service' so wines SCM rejects
        # the start BEFORE it cold-JITs SteamService.exe every 10s (that respawn loop IS the 100%
        # burst). re-applied each launch coz steam re-enables it on update. VAC-safe (Linux ships none).
        "[HKEY_LOCAL_MACHINE\\System\\CurrentControlSet\\Services\\Steam Client Service]\n"
        '"Start"=dword:00000004\n'
    )
    _batch_reg_path = Path(tempfile.gettempdir()) / "mnc_steam_batch.reg"
    try:
        _batch_reg_path.write_text(batch_reg, encoding="utf-8")
    except Exception as _exc:
        log(f"steam batch.reg write failed (non-fatal): {_exc}")
    # Bradar: the `wine regedit` above only sets Start=4 in the RUNNING hive -- an ALREADY-MADE
    # prefix keeps "Start"=dword:00000003 ON DISK in system.reg, so the SCM can still cold-JIT the
    # SteamService.exe respawn loop (a full P-core, THE big slowness) befor the regedit lands. So we
    # also rewrite it DIRECTLY in system.reg while the wineserver is dead (right after the -k below),
    # so the value is already 4 the moment wine loads the hive. Line-based (no regedit) so it fixes
    # existing bottles on startup. Confirmed: user reported Steam "much faster" after this flip.
    _svcfix_py = (
        "import sys, io\n"
        "p = sys.argv[1]\n"
        "try:\n"
        "    L = io.open(p, encoding='utf-8', errors='replace').read().splitlines(keepends=True)\n"
        "except Exception:\n"
        "    sys.exit(0)\n"
        "insec = False; changd = False\n"
        "for i, ln in enumerate(L):\n"
        "    s = ln.lstrip()\n"
        "    if s.startswith('['):\n"
        "        insec = 'Steam Client Service]' in ln\n"
        "    elif insec and s.startswith('\"Start\"=dword:'):\n"
        "        if ln.strip() != '\"Start\"=dword:00000004':\n"
        "            L[i] = '\"Start\"=dword:00000004' + chr(10); changd = True\n"
        "if changd:\n"
        "    io.open(p, 'w', encoding='utf-8').write(''.join(L))\n"
    )
    _svcfix_path = Path(tempfile.gettempdir()) / "mnc_svcfix.py"
    try:
        _svcfix_path.write_text(_svcfix_py, encoding="utf-8")
    except Exception as _exc:
        log(f"steam svcfix.py write failed (non-fatal): {_exc}")
    silent = bool(params.get("silent", False))
    steam_args = STEAM_SILENT_ARGS if silent else "-tcp"
    log_path = str(LOG_DIR / "Steam-wine.log")
    # match the proven steam-unified-run.sh: kill the server then wipe the CEF caches
    # Bradar (incl userdata GPUCache) so Steam comes up clean on the spoofed GPU + DXMT
    _gpucache_wipe_line = (f"find userdata -type d -name GPUCache -prune -exec rm -rf {{}} + 2>/dev/null\n"
                           if _wipe_gpucache else "")
    cmd = (
        # export DYLD inside the shell. the outer arch (SIP-restricted) strips DYLD_* so
        # running wine via `arch wine` loses the fallback path and wine cannot dlopen
        # freetype -> no fonts -> tiny empty window. run wine directly under the arch shell
        f"export DYLD_FALLBACK_LIBRARY_PATH={shlex.quote(env['DYLD_FALLBACK_LIBRARY_PATH'])}\n"
        f"{shlex.quote(wineserver)} -k 2>/dev/null; sleep 1\n"
        # Bradar: with the server now dead, disable the Steam Client Service DIRECTLY in system.reg
        # (flips Start 3->4 on disk) so even already-made bottles get it the moment wine loads the hive.
        f"python3 {shlex.quote(str(_svcfix_path))} {shlex.quote(str(Path(prefix) / 'system.reg'))} 2>/dev/null\n"
        f"cd {shlex.quote(str(steam_dir))} || exit 1\n"
        f"rm -f .crash 2>/dev/null\n"
        # Bradar keep config/htmlcache (the CEF compiled-UI cache) so steam dont re-cache +
        # re-JIT the whole panorama UI every boot. also KEEP appcache/httpcache (etag-validated
        # library metadata + images) so the library paints faster on later boots -- only nuke the
        # REST of appcache. (the GPUCache wipe is gated below: kept unless last run crash-stormed.)
        f"find appcache -mindepth 1 -maxdepth 1 ! -name httpcache -exec rm -rf {{}} + 2>/dev/null\n"
        f"rm -f logs/* dumps/*.dmp 2>/dev/null\n"
        f"{_gpucache_wipe_line}"
        # steam.cfg used to freeze the client self-updater (BootStrapperInhibitAll) to skip the
        # manifest-download churn every launch. That freeze bricks EVERY install the moment Valve
        # makes a client update mandatory (July 2026): the bootstrap logs "Suppressing Steam
        # update" and dead-ends -- a silent ~100%-CPU fault storm under the unified wine, a quiet
        # exit under stock wine. Never write the freeze again, and delete OUR old freeze-file so
        # affected installs self-heal on next launch (a user-authored steam.cfg without our
        # marker is left alone). The service re-enable steam.cfg also guarded against is already
        # re-disabled on every launch by the reg add below.
        f"grep -q 'BootStrapperInhibitAll' steam.cfg 2>/dev/null && rm -f steam.cfg\n"
        # Bradar make steam SKIP the hang-prone .NET/VC++/DirectX redist install-scripts by
        # pre-settin their has-run keys (else e.g. World War 3 wedges forever on "Running
        # install script (Microsoft .NET Framework)" coz the NDP462 bootstrapper never exits)
        f"{_commonredist_hasrun_reg_cmds(str(prefix), wine)}"
        # Bradar ONE regedit import (retina + LogPixels + the Steam-Client-Service disable) reusing
        # the server the -k just cleared, insted of a pre-launch regedit + a separate reg add. the
        # service disable is THE big one (kills the SteamService.exe respawn loop); steam.exe below
        # reuses this same wineserver so we spend 1 server lifecycle, not 3.
        f"{shlex.quote(wine)} regedit {shlex.quote(str(_batch_reg_path))} >/dev/null 2>&1\n"
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


def _ensure_progfiles_x86(prefix: str) -> None:
    """Set the WoW64 ProgramFilesDir (x86) registry keys so 32-bit installers (SteamSetup, redists)
    land in 'Program Files (x86)' like on real Windows, insted of the 64-bit 'Program Files'. the
    fast wineboot (MNC_SKIP_WOW64_INSTALL) skips the wine.inf step that writes these, so on a fresh
    prefix a 32-bit installer's $PROGRAMFILES falls back to the 64-bit dir (thats why Steam landed in
    'Program Files' not '(x86)'). idempotent: no-op once the key is present (proper-booted / already
    -fixed prefixes have it). See steamsetup-installer-wine-overlay."""
    try:
        if '"ProgramFilesDir (x86)"' in (Path(prefix) / "system.reg").read_text(errors="ignore"):
            return
    except Exception:
        pass
    wine = _find_wine()
    if not wine:
        return
    try:
        (Path(prefix) / "drive_c" / "Program Files (x86)").mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    env = _wine_env(prefix)
    dyld = env.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    cv = r"HKLM\Software\Microsoft\Windows\CurrentVersion"
    wow = r"HKLM\Software\Wow6432Node\Microsoft\Windows\CurrentVersion"
    keys = [
        (cv,  "ProgramFilesDir (x86)", r"C:\Program Files (x86)"),
        (cv,  "CommonFilesDir (x86)",  r"C:\Program Files (x86)\Common Files"),
        (wow, "ProgramFilesDir",       r"C:\Program Files (x86)"),
        (wow, "ProgramFilesDir (x86)", r"C:\Program Files (x86)"),
        (wow, "CommonFilesDir",        r"C:\Program Files (x86)\Common Files"),
    ]
    lines = "\n".join(
        f'{shlex.quote(wine)} reg add "{k}" /v "{v}" /t REG_SZ /d {shlex.quote(d)} /f >/dev/null 2>&1'
        for k, v, d in keys)
    sh = f"export DYLD_FALLBACK_LIBRARY_PATH={shlex.quote(dyld)}\n" + lines
    try:
        subprocess.run(["/usr/bin/arch", "-x86_64", "/bin/bash", "-lc", sh], env=env, timeout=120)
        log("_ensure_progfiles_x86: set ProgramFilesDir (x86) so 32-bit installers use Program Files (x86)")
    except Exception as exc:
        log(f"_ensure_progfiles_x86 failed: {exc}")


def _installer_wine() -> str:
    """The wine to run installers with: the unified engine, always.

    This replaces the old pre-HACK22 overlay (deps/wine-installer). That overlay existed
    because 32-bit NSIS/Burn stubs fault-stormed at ~100% CPU under HACK 22's gs.base
    rewrite. Root-caused 2026-07-25: the far `ljmp` in dlls/wow64cpu/cpu.c's syscall_32to64
    did not switch the CPU to 64-bit mode under Rosetta 2, so the 64-bit body decoded as
    32-bit and faulted (CW HACK 20760). With that fixed the unified engine runs 32-bit Burn
    bundles clean -- EA App's own installer reaches "Apply complete, result: 0x0" -- so the
    overlay is retired and installers use the same engine as everything else.

    MNC_INSTALLER_WINE still overrides, for bisecting an installer against another build."""
    ov = os.environ.get("MNC_INSTALLER_WINE", "").strip()
    if ov and Path(ov).exists():
        return ov
    bt = _unified_build_dir()
    if bt:
        return str(bt / "wine")
    return _find_wine() or ""


def _run_installer_unified(prefix: str, cmd_after_wine: List[str],
                           backend: str = "dxmt",
                           log_path: Optional[str] = None,
                           env: Optional[Dict[str, str]] = None) -> subprocess.Popen:
    """Launch an installer on the UNIFIED wine. This is the only installer path.

    A pre-HACK22 overlay used to exist because 32-bit NSIS/Burn stubs fault-stormed under
    the unified engine. Root-caused 2026-07-25: the far `ljmp` in dlls/wow64cpu/cpu.c's
    syscall_32to64 did not switch the CPU to 64-bit mode under Rosetta 2, so the 64-bit body
    decoded as 32-bit and faulted (CW HACK 20760, now ported). With that fixed a 32-bit Burn
    bundle runs clean here -- EA App's own installer reaches "Apply complete, result:
    0x0" -- and the overlay has been removed.

    Stage a real 32-bit subsystem first (a fast-booted
    bottle with an empty syswow64 kills 32-bit installers with c0000135), run under
    arch -x86_64 with an in-shell DYLD re-export (arch strips DYLD_*), and tee wine's output
    to log_path so an install is never a silent black box."""
    wine = _installer_wine()
    if not wine:
        raise FileNotFoundError("Wine not found")
    if env is None:
        env = _unified_env(prefix, backend or "dxmt", False, for_steam=False)
        env["WINEDEBUG"] = "-all,+err"
    _stage_syswow64(prefix)
    _ensure_progfiles_x86(prefix)
    _stage_unified_dlls(prefix)
    out = open(log_path, "w") if log_path else subprocess.DEVNULL
    dyld = env.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    tail = " ".join(shlex.quote(a) for a in cmd_after_wine)
    sh = (f"export DYLD_FALLBACK_LIBRARY_PATH={shlex.quote(dyld)}\n"
          f"exec {shlex.quote(wine)} {tail}")
    log(f"installer (unified wine): {cmd_after_wine}")
    return subprocess.Popen(["/usr/bin/arch", "-x86_64", "/bin/bash", "-lc", sh],
                            env=env, stdout=out, stderr=subprocess.STDOUT,
                            start_new_session=True)


def _run_installscript_redists(prefix: str, game_dir: str, backend: str) -> None:
    """Actualy INSTALL a Steam games install-script redists (VC++ / Vulkan RT / Rockstar
    Launcher / Social Club / .NET) via the unified wine, THEN set their per-redist
    has-run keys so Steam skips its OWN run of them. Steam fires these WoW64/Burn installers
    under our HACK22 wine where they spin at 100% CPU forever + wedge the launch on "Running
    install script"; the unified wine (with the wow64cpu ljmp fix) finishs them clean. Idempotent -- a redist whos
    has-run value is allready set on disk is skipd. No-op if no installscript.vdf or the
    installscript.vdf is present. See winemono-32bit-hack22-rootcause."""
    iw = _installer_wine()
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
    _stage_syswow64(prefix)  # 32-bit subsystem so the unified wine can run these 32-bit redists
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
            log(f"redist pre-install: {Path(unixpath).name} {cmd_args}".rstrip())
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
        log(f"redist pre-install: finishd {handled} install-script redist(s) via the unified "
            f"wine so Steam wont fault-storm on them")


# redist kinds allready satisfied by wine builtins (VC++ CRT / DirectX Jun2010) or by wine-mono
# (.NET) -> we DONT run their 32-bit installers (redundant, n they .cmd/bootstrap fault-storm);
# the has-run SKIP path + the builtins/wine-mono/d3dcompiler_47-drop cover em. everything ELSE
# (mfc / physx / openal / xna / exotic) has NO builtin -> the run-path actualy installs it.
_REDIST_BUILTIN_COVERED = ("vcredist", "vc_redist", "visual c++", "directx", "dxsetup",
                           "dotnet", "netfx", "ndp", ".net framework")


def _run_shared_commonredist(prefix: str, backend: str) -> None:
    """INSTALL the SHARED 'Steamworks Shared/_CommonRedist' redists that have NO wine builtin
    (mfc / physx / openal / xna / ...) via the unified wine, so games that need em
    actualy get em -- Steams own run of these 32-bit Burn/NSIS installers fault-storms under
    HACK22 so it never installs em, it just marks em has-run. The builtin-coverd ones (VC++ n
    DirectX = wine builtins, .NET = wine-mono) r deliberately SKIPD (redundant). 3 fixes over
    the game-local path: (1) %INSTALLDIR% resolves PER-VDF to the dir just ABOVE _CommonRedist,
    not the game dir; (2) .cmd/.bat run via 'wine cmd /c' (wine cant exec a .cmd as a PE, the
    game-local path silently continue-skips them); (3) idempotency keys a prefix-local marker
    file, NOT the reg key -- the has-run SKIP path sets that same reg key independently, so a
    reg-key gate would skip forever. On any run failure we STILL leave the has-run key set so a
    launch is never wedged. See canonical-patch + winemono-32bit-hack22-rootcause."""
    iw = _installer_wine()
    if not iw:
        return
    shared = _steam_dir(prefix) / "steamapps" / "common" / "Steamworks Shared"
    if not shared.is_dir():
        return
    vdfs = sorted(shared.rglob("installscript*.vdf"))
    if not vdfs:
        return
    marker_path = Path(prefix) / ".mnc_redists_done.json"   # {"<vdf_dir>::<label>": 1}
    try:
        done = json.loads(marker_path.read_text()) if marker_path.exists() else {}
    except Exception:
        done = {}
    block_re = re.compile(r'"([^"]+)"\s*\{[^{}]*?"HasRunKey"\s+"([^"]+)"', re.IGNORECASE | re.DOTALL)
    def _field(t, name):
        m = re.search(r'"' + name + r'"\s+"([^"]*)"', t, re.IGNORECASE)
        return m.group(1) if m else None
    env = _unified_env(prefix, backend or "d3dmetal", False, for_steam=False)
    env["WINEDEBUG"] = "-all"
    _stage_syswow64(prefix)
    dyld = env.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    handled = 0
    for vdf in vdfs:
        try:
            txt = vdf.read_text(errors="ignore")
        except Exception:
            continue
        if "hasrunkey" not in txt.lower():
            continue
        # Blocker 1: %INSTALLDIR% = the dir just ABOVE _CommonRedist for THIS vdf (fallbak: shared)
        installdir = shared
        for parent in vdf.parents:
            if parent.name == "_CommonRedist":
                installdir = parent.parent
                break
        hay_dir = str(vdf).lower()
        for m in block_re.finditer(txt):
            label, key = m.group(1), m.group(2)
            # skip builtin/wine-mono-coverd redists (dont re-run redundant/hanging installers)
            if any(k in hay_dir or k in label.lower() for k in _REDIST_BUILTIN_COVERED):
                continue
            mk = f"{vdf.parent}::{label}"
            if done.get(mk):
                continue
            tail = txt[m.end():m.end() + 900]
            proc = _field(tail, "process 1")
            if not proc:
                continue
            cmd_args = _field(tail, "command 1") or ""
            key = key.replace("\\\\", "\\").replace("HKEY_LOCAL_MACHINE", "HKLM")
            unixpath = (proc.replace("\\\\", "\\").replace("%INSTALLDIR%", str(installdir))
                        .replace("\\", "/"))
            if not Path(unixpath).exists():
                continue
            low = unixpath.lower()
            # Blocker 2: wine cant exec a .cmd/.bat as a PE -> run it thru cmd.exe. But `cmd /c
            # <path>` chokes on a spaced/paren'd path -- "Program Files (x86)" trips cmds /c
            # quote-strip rule so the path splits n nothing runs (proven live). Robust fix: cd into
            # the .cmds OWN dir + run it by BASENAME (no spaces). an .exe takes a unix path fine.
            if low.endswith(".cmd") or low.endswith(".bat"):
                cmd_dir = shlex.quote(str(Path(unixpath).parent))
                base = shlex.quote(Path(unixpath).name)
                runcmd = f"cd {cmd_dir} && {shlex.quote(iw)} cmd /c {base} {cmd_args}".rstrip()
            else:
                runcmd = f"{shlex.quote(iw)} {shlex.quote(unixpath)} {cmd_args}".rstrip()
            log(f"shared redist install: {Path(unixpath).name} {cmd_args}".rstrip())
            sh = f"export DYLD_FALLBACK_LIBRARY_PATH={shlex.quote(dyld)}\n{runcmd} >/dev/null 2>&1"
            try:
                subprocess.run(["/usr/bin/arch", "-x86_64", "/bin/bash", "-lc", sh], env=env, timeout=900)
            except Exception as exc:
                log(f"shared redist {Path(unixpath).name} run failed: {exc}")
            # set has-run (+ Wow6432Node mirror) so Steam skips its OWN storming run
            variants = {key}
            if "\\Software\\" in key and "Wow6432Node" not in key:
                variants.add(key.replace("\\Software\\", "\\Software\\Wow6432Node\\", 1))
            for kp in variants:
                rc = (f"export DYLD_FALLBACK_LIBRARY_PATH={shlex.quote(dyld)}\n"
                      f"{shlex.quote(iw)} reg add {shlex.quote(kp)} /v {shlex.quote(label)} "
                      f"/t REG_DWORD /d 1 /f >/dev/null 2>&1")
                try:
                    subprocess.run(["/usr/bin/arch", "-x86_64", "/bin/bash", "-lc", rc], env=env, timeout=60)
                except Exception:
                    pass
            # Blocker 3: mark done in the prefix-local marker (regardless of run outcome so a
            # broken installer never re-fires every launch; has-run above keeps Steam un-wedged)
            done[mk] = 1
            handled += 1
    if handled:
        try:
            marker_path.write_text(json.dumps(done))
        except Exception:
            pass
        log(f"shared redist: processd {handled} uncoverd CommonRedist redist(s) via the unified wine")


def _ue_project_token(exe_path: Path, args: str) -> str:
    """The project name a packaged Unreal game needs as its FIRST argument, or "".

    UE derives the project from the exes OWN name: <Project>-Win64-Shipping.exe looks for
    ../../../<Project>/<Project>.uproject. When the shipped exe is named after the store
    build insted of the project -- Satisfactory ships FactoryGameSteam-Win64-Shipping.exe
    inside a FactoryGame project -- that lookup misses n the game dies in a modal "Failed
    to open descriptor file ../../../FactoryGameSteam/FactoryGameSteam.uproject" before it
    ever opens a window, so Play just looks broken. The games own bootstrap exe passes the
    real name ("FactoryGame -NO_EOS_OVERLAY"), so do the same when we launch the shipping
    exe directly. Only fires when the exes own name does NOT resolve, so games that allready
    work r untouched, n bails out whenever the layout is unfamiliar rather than guessin."""
    m = re.match(r"(.+?)-Win64-(?:Shipping|Test|Development|DebugGame)$", exe_path.stem, re.I)
    if not m:
        return ""
    exe_project = m.group(1)
    # demand the canonical <root>/Engine/Binaries/Win64/<exe> layout
    if (len(exe_path.parents) < 4
            or exe_path.parents[1].name.lower() != "binaries"
            or exe_path.parents[2].name.lower() != "engine"):
        return ""
    root = exe_path.parents[3]
    own = root / exe_project
    if (own / f"{exe_project}.uproject").exists() or (own / "Content").is_dir():
        return ""   # the exes own name resolves -> UE finds the project by itself
    # never override a project token the frontend/user allready put first
    try:
        first = next(iter(shlex.split(args)), "")
    except ValueError:
        return ""   # unbalanced quotes -- leave the argv exactly as given
    if first and not first.startswith(("-", "/")):
        return ""
    try:
        cands = sorted(d.name for d in root.iterdir()
                       if d.is_dir() and d.name.lower() != "engine" and (d / "Content").is_dir())
    except OSError:
        return ""
    return cands[0] if len(cands) == 1 else ""   # ambiguous -> dont guess


def _launch_game_unified(prefix: str, exe: str, args: str, bottle_cfg: Dict[str, Any],
                         params: Dict[str, Any]) -> Any:
    """Launch a game through the unified wine; the loader routes its d3d to the
    chosen backend while Steam stays on DXMT."""
    bt = _unified_build_dir()
    exe_path = Path(exe)
    # SteamSetup.exe is a 32-bit NSIS stub that fault-storms on the unified HACK22 wine -> a Play
    # would spin forever with NO window (the storm is the HACK22 WINE, not the d3dmetal/dxmt backend,
    # so switchin backend wouldnt help at all). route it to the unified installer wine + /S so
    # Steam installs silently (the GUI wizard doesnt reliably surface under wine); a later Play then
    # finds steam.exe n launchs it via DXMT. this is why "Play on a steam bottle w/o Steam" did
    # nothing + logd backend=d3dmetal.
    if exe_path.name.lower() == "steamsetup.exe":
        tail = [str(exe_path)] + (shlex.split(args) if args else ["/S"])
        logf = str(Path(prefix) / "mnc-installer.log")
        proc = _run_installer_unified(str(prefix), tail, "d3dmetal", log_path=logf)
        _running_games[proc.pid] = proc
        log(f"launch: SteamSetup.exe routed to the unified installer wine (silent); log {logf}")
        return {"pid": proc.pid}
    # DEPRECATED 2026-07-25: the EA app (EADesktop.exe) used to be routed to a gated pre-HACK22
    # overlay path (_launch_ea_app, since removed) becuse HACK22 appeard to break its CEF/mscoree
    # startup. The
    # real cause was never HACK22: it was the missing Rosetta-2 WoW64 thunk workaround in
    # dlls/wow64cpu/cpu.c (far ljmp not switching the CPU to 64-bit under Rosetta, so
    # syscall_32to64's body decoded as 32-bit n faulted). That is fixed in the engine now
    # (MNC ROSETTA-THUNK / CW HACK 20760), so EA App no longer needs a special launch path --
    # it goes through the SAME generic "Application" route as any other CEF/Chromium app
    # (force_dxmt_cef -> DXMT + the CEF flag injection), on the unified wine. Keeping one code
    # path here is the whole point: no per-app exe-name gating, no overlay wine to maintain.
    _stage_unified_dlls(str(prefix))
    _stage_unified_mf(str(prefix))
    _provision_redist_dlls(str(prefix))   # real MS d3dcompiler_47 file-drop (no installer, safe)
    _ensure_steam_sdl_resolvable(str(prefix))
    # Bradar arbitrary "Applications" (cmd_launch_app -> launchGame's force_dxmt_cef) are far
    # more likely than a chosen game to embed a CEF/Chromium UI (Electron, Qt WebEngine, other
    # launchers users point MacNdCheese at). Plain D3DMetal crashes those the same way it crashed
    # EA App's Link2EA.exe this session ("Failed to dlopen D3DMetal" assertion in shared.mm) even
    # when the process never really renders 3D -- just loading d3d11.dll/dxgi.dll as a dependency
    # is enough. DXMT doesn't have that failure mode, so force it for this whole launch class
    # regardless of the bottle's configured default_backend, same as EA App's own origin-launch.
    _cef_backend = _cef_launcher_backend(params.get("exe", ""))
    force_cef = bool(params.get("force_dxmt_cef")) or _cef_backend is not None
    if _cef_backend is not None:
        backend = _cef_backend       # per-launcher, see _CEF_LAUNCHER_BACKENDS
    elif params.get("force_dxmt_cef"):
        backend = "dxmt"             # generic Applications, per the EA App finding
    else:
        backend = _unified_game_backend(bottle_cfg, params.get("backend", ""))
    metal_hud = params.get("metal_hud", bottle_cfg.get("metal_hud", False))
    debug = bool(params.get("debug", bottle_cfg.get("debug", False)))
    steam_mode = params.get("steam_mode", "silent")
    is_steam_bottle = bottle_cfg.get("launcher_type", "steam") == "steam"
    # Bradar decide .NET up-front: a game that ships a .NET redist (or is flagged) gets wine-mono
    # installd + mscoree ENABLED for its launch. default off so mscoree stays globally disabled,
    # which suppresses the .NET CrashReport red-herring on the games that never touch .NET.
    needs_dotnet = _game_needs_dotnet(str(prefix), str(exe_path.parent), bottle_cfg, params)
    if needs_dotnet:
        try:
            _install_wine_mono(str(prefix), backend)
        except Exception as exc:
            log(f".NET (wine-mono) install skipped: {exc}")
    # Bradar pre-instal the games install-script redists (VC++/Vulkan RT/Rockstar Launcher/
    # Social Club/.NET) via the unified wine BEFORE steam runs its own install-script.
    # steam fires them under our HACK22 wine where the 32-bit Burn bundles fault-storm at
    # 100% CPU forever ("stuck on installer script"); this finishs them clean + sets the
    # has-run keys so steam skips its storming run. idempotent (skips already-done ones).
    if is_steam_bottle:
        try:
            _run_installscript_redists(str(prefix), str(exe_path.parent), backend)
        except Exception as exc:
            log(f"redist pre-install skipped: {exc}")
        # Bradar + the SHARED Steamworks-Shared/_CommonRedist redists that have NO wine builtin
        # (mfc/physx/openal/...) -- the game-local scan above misses em (theyr a sibling of the
        # game dir); this installs the uncoverd ones + skips the builtin-coverd VC++/DirectX/.NET.
        try:
            _run_shared_commonredist(str(prefix), backend)
        except Exception as exc:
            log(f"shared redist pre-install skipped: {exc}")
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
    # 4GB patch before we launch, not after: the flag is read by the loader when the
    # image is mapped, so patching a running process would do nothing. No-op on a
    # 64-bit exe and on one that already ships the bit.
    if bool(params.get("large_address_aware", bottle_cfg.get("large_address_aware", True))):
        _apply_4gb_patch(str(exe_path))
    # x87+JIT is on by default; the per-bottle/per-launch flag is an escape hatch for
    # a title the patched handlers upset, not a thing users should have to find.
    env = _unified_env(prefix, backend, metal_hud, gst_debug=("5" if debug else "3"),
                       cef_safe_mode=force_cef, debug=debug,
                       x87_jit=bool(params.get("x87_jit", bottle_cfg.get("x87_jit", True))),
                       x87_opts={k: bool(params.get(k, bottle_cfg.get(k, False)))
                                 for k in ("x87_extended_fpr", "x87_fast_round",
                                           "x87_f32_arith",
                                           "x87_fast_recip_div")})
    # Rockstar: make d3d12 cleanly ABSENT. The Social Club CEF resolves D3D12CreateDevice
    # dynamicaly and calls it through an UNGUARDED proc-table slot -- with our half-alive
    # d3d12 stub loaded the slot ends up NULL and every helper dies calling address 0
    # (proven from the crashpad minidumps: call site libcef+0x4c984d3, rax=0, args =
    # (adapter, FL_11_0, IID_ID3D12Device, &out)). With the dll disabled the LoadLibrary
    # fails and CEF takes its guarded no-d3d12 path insted -> browser lives, UI paints.
    # Also quiets RGL's own caught "[dx12] Exception thrown during DX12 GPU query".
    # Scoped to Rockstar launches only: D3DMetal d3d12 games must keep ther d3d12.
    if _is_rockstar_launcher(params.get("exe", "")):
        env["WINEDLLOVERRIDES"] = env.get("WINEDLLOVERRIDES", "") + ";d3d12,d3d12core=d"
        log("rockstar launcher: d3d12 disabled (CEF NULL D3D12CreateDevice crash)")
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
    # The Rockstar launcher is a LAUNCHER, not a steamworks title: handing it an appid (via
    # steam_appid.txt or SteamAppId) makes it take its steam-launched code path, call
    # SteamAPI_Init and die with "Steam failed to initialize" when no client is up. It only
    # needs those when STEAM itself started it -- and then steam supplys them, not us. A tile
    # configured for RDR2 was leaking RDR2's 1174180 into the Launcher dir.
    if steam_appid.isdigit() and _is_rockstar_launcher(params.get("exe", "")):
        log("rockstar launcher: skipping steam appid (would force SteamAPI_Init)")
        try:
            (Path(exe_dir) / "steam_appid.txt").unlink()
        except Exception:
            pass
        steam_appid = ""
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
    if _game_needs_dpi_aware(str(prefix), str(exe_path.parent), exe_path.name, "",
                             bottle_cfg, params):
        _apply_dpi_aware_regedit(wine, env, {exe_path.name})
    if needs_dotnet:
        _apply_gecko_regedit(wine, env)   # mshtml is enabled above (needs_dotnet); give it a Gecko to render with
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", exe_path.stem)
    log_path = str(LOG_DIR / f"{safe_name}-wine.log")
    # CEF-safe-mode Applications: deliver the GPU-spoof + single-process switches on the
    # launched exe's OWN argv. The engine (kernelbase/process.c) only injects these when it
    # intercepts CreateProcess -- i.e. for CHILD processes -- but the exe we start here IS the
    # CEF *browser* process, launched directly by us, so that hook never fires for it and it
    # comes up with no window at all (confirmed live with EA App: CEF children spawned n died,
    # EADesktop sat at 0% CPU logging "Ui initialization completed" with nothing on screen).
    # The old EA-only path hardcoded these onto EADesktop.exe's argv; doing it here instead
    # means ANY Application/launcher gets it with no exe-name gating. Chromium forwards the
    # switches to its own children, so one delivery covers the whole tree. Never clobber a
    # user-supplied switch.
    if force_cef:
        _cef_argv = (env.get("MNC_WEBHELPER_FLAGS", "") + " "
                     + env.get("MNC_EA_WEBHELPER_EXTRA_FLAGS", "")).split()
        _have = {p.split("=", 1)[0] for p in (shlex.split(args) if args else []) if p.startswith("--")}
        _add = [f for f in _cef_argv if f.split("=", 1)[0] not in _have]
        if _add:
            args = ((args + " ") if args else "") + " ".join(_add)
            log(f"CEF-safe-mode Application: appended {len(_add)} CEF/GPU switch(es) to argv")
    _ue_project = _ue_project_token(exe_path, args)
    if _ue_project:
        args = f"{_ue_project} {args}".strip()
        log(f"UE: prepended project '{_ue_project}' -- {exe_path.name} doesnt resolve to a "
            f"project dir, so UE would die on 'Failed to open descriptor file'")
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


# Launchers with a CEF/Chromium UI all need Steam's *flag* treatment -- the GPU-spoof
# switches the kernelbase hook injects -- or their GPU process crash-loops into a black
# window. That part is universal. The BACKEND is not, and picking one for everybody breaks
# somebody:
#
#   EA App   -> DXMT. Plain D3DMetal aborts the instant d3d11/dxgi loads as a dependency,
#               even with nothing rendering ("Failed to dlopen D3DMetal" in Link2EA.exe).
#   Rockstar -> DXVK. RGL is D3D10, and dxgi_dxmt is the one variant with NO
#               DXGID3D10CreateDevice export (d3dm has 5, dxvk 7, opengl 2, dxmt 0), so on
#               DXMT it dies "unimplemented function dxgi.dll.DXGID3D10CreateDevice".
#               Auto-resolve hands unknown launchers DXMT, which is exactly how RGL went
#               from booting to aborting. Measured: DXVK takes that abort count to 0 and
#               RGL reaches its own init. (The "failed to initialize" past this point is
#               the separate wine-SCM frontier, not a graphics problem.)
_CEF_LAUNCHER_BACKENDS = {
    # Rockstar is back on DXMT: its D2D UI needed IDXGISurface1/GetDC + SwapDeviceContextState,
    # both now in the bundled DXMT (d3d11_dxmt 21511438+). Pair with the d3d12 block below.
    "socialclubhelper.exe":        "dxmt",      # Rockstar CEF helper
    "launcher.exe":                "dxmt",      # Rockstar Games Launcher (dir-checked below)
    "launcherpatcher.exe":         "dxmt",
    "rockstar-games-launcher.exe": "dxmt",      # the installer
    "playrdr2.exe":                "dxmt",
    "eadesktop.exe":               "dxmt",      # EA App
    "link2ea.exe":                 "dxmt",
}


def _cef_launcher_backend(exe: str) -> Optional[str]:
    """Backend a known CEF launcher must use, or None when this isn't one.

    Split on BOTH separators by hand: these are windows paths but we run on macOS, where
    Path() treats a backslash as an ordinary character -- Path(r"C:\\x\\Launcher.exe").name
    hands back the whole string and every match silently fails."""
    raw = str(exe or "").replace("\\", "/")
    name = raw.rsplit("/", 1)[-1].lower()
    backend = _CEF_LAUNCHER_BACKENDS.get(name)
    if backend is None:
        return None
    # "Launcher.exe" is far too generic to claim outright -- only Rockstar's, which always
    # lives under a Rockstar Games dir. An indie game shipping the same name is untouched.
    if name in ("launcher.exe", "launcherpatcher.exe") and "rockstar" not in raw.lower():
        return None
    return backend


def _is_cef_launcher(exe: str) -> bool:
    return _cef_launcher_backend(exe) is not None


def _is_rockstar_launcher(exe: str) -> bool:
    raw = str(exe or "").replace("\\", "/").lower()
    name = raw.rsplit("/", 1)[-1]
    return "rockstar" in raw or name in ("socialclubhelper.exe", "playrdr2.exe")


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
        # Bradar same contract as the unified path (issue #105): a game left on
        # "Default" defers to the bottle's global backend (the toolbar picker)
        # before falling back to game-type heuristics. Previously this branch
        # ignored default_backend entirely, so the toolbar picker had zero
        # effect on any game whenever Unified Steam engine was turned off.
        global_backend = _classic_default_backend(bottle_cfg)
        if global_backend:
            backend = global_backend
            log(f"Auto backend deferred to bottle's global backend for {Path(exe).name}: {backend}")
        else:
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
    env = _apply_sync_env(env, esync, msync, prefix=str(prefix))

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
        # same arch/DYLD trap as _backend_launch_cmd -- re-export inside the arch'd shell
        _inner = (
            'export DYLD_FALLBACK_LIBRARY_PATH="$MNC_DYLD"; '
            f"exec {shlex.quote(wine)} {shlex.quote(str(exe_path))}"
        )
        cmd = (
            f"cd {shlex.quote(str(exe_path.parent))} && "
            f"/usr/bin/arch -x86_64 /bin/bash -c {shlex.quote(_inner)} "
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
        "/usr/local/opt/gnutls/lib", _WINE_STABLE_LIB,
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
        metal_hud_line = "export MTL_HUD_ENABLED=1\nexport MTL_DEBUG_BUILD=1\n"

    
    heredoc = f"""\
    export MNCROOT={shlex.quote(str(mnc_root))}
    export MNC_WINE={shlex.quote(wine)}
    export WINEPREFIX={shlex.quote(prefix)}
    export PATH="$MNCROOT/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    export DYLD_FALLBACK_LIBRARY_PATH={shlex.quote(dyld_fallback)}
    export ROSETTA_ADVERTISE_AVX=1
    {metal_hud_line}unset GTK_PATH GTK_EXE_PREFIX GTK_DATA_PREFIX GDK_PIXBUF_MODULEDIR GDK_PIXBUF_MODULE_FILE GTK_IM_MODULE_FILE XDG_DATA_DIRS
    export WINEDLLOVERRIDES="winemenubuilder.exe=d"
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
    return any("\\Steam\\steam.exe" in line for line in ps.splitlines())  # x86 OR non-x86 Steam


def _wait_steam_ready(prefix: str, cap_s: int = 240) -> tuple:
    """Poll until Steam is authenticated ([Logged On] in connection_log.txt) and
    steamwebhelper is up. Returns (ready: bool, status: str). Lifted from the
    proven pre-no-shim readiness poll."""
    connection_log = _steam_dir(prefix) / "logs" / "connection_log.txt"

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
        log(f"Launching SteamSetup.exe in {prefix} (unified wine; the wow64cpu ljmp fix keeps the NSIS stub from fault-storming; log {logf})")
        # /S = silent install (the SteamSetup GUI wizard doesnt reliably surface under wine); this
        # lands steam.exe so a later Play launchs Steam via DXMT.
        proc = _run_installer_unified(prefix, [str(exe), "/S"], "d3dmetal", log_path=logf)
        _setup_proc = proc
    except Exception as exc:
        log(f"Warning: failed to run SteamSetup: {exc}")


def cmd_get_setup_pid(_params: Dict[str, Any]) -> Any:
    global _setup_proc
    running = _setup_proc is not None and _setup_proc.poll() is None
    return {"running": running}


_ea_app_setup_proc: Optional[subprocess.Popen] = None


def _download_and_run_eaapp_setup(prefix: str, wine: str, setup_path: Optional[str] = None) -> None:
    """Run EAappInstaller.exe in the given prefix (background thread). Same
    download/silent-install pattern as _download_and_run_steam_setup --
    runs on the unified wine (_run_installer_unified).

    CONFIRMED live (2026-07-21): the Lutris prerequisite note below was right.
    A fresh bottle with none of the prerequisites provisioned below (real MS
    d3dcompiler_47, wine-mono, corefonts, mscoree enabled) fails EA App's own
    installer with "err:msi:ITERATE_Actions Execution halted, action
    L"JunoInitializeSession" returned 1603" plus repeated
    "err:mscoree:LoadLibraryShim error reading registry key for installroot" --
    an install-time custom action needs a working CLR, which the plain
    for_steam=False env explicitly disabled at the time (mscoree=;). That blanket
    disable is gone as of 2026-08-05 (see _unified_env), so the CLR half of this
    is no longer self-inflicted; the wine-mono/corefonts/d3dcompiler_47
    provisioning below is still genuinely needed. Matches a public
    Linux Mint forum report hitting the identical JunoInitializeSession/1603
    failure. A bottle that happened to already have these from unrelated
    earlier winetricks/game activity (real d3dcompiler_47 via `winetricks
    d3dcompiler_47`, in particular) installs fine, which is why this went
    unnoticed until tested against a genuinely fresh bottle.

    NOTE: the SAME 1603 failure still reproduces on the pre-HACK22 overlay even
    WITH all these prerequisites in place, while the identical installer/prefix
    setup installs clean under plain Wine Stable -- so there's a genuine,
    not-yet-root-caused difference in our own wine build (pre-HACK22 ntdll +
    whatever else differs from stock) breaking this one MSI custom action.
    Actively being investigated at the engine level rather than worked around."""
    global _ea_app_setup_proc
    try:
        if setup_path and Path(setup_path).expanduser().exists():
            exe = Path(setup_path).expanduser()
            log(f"Using provided EAappInstaller.exe: {exe}")
        else:
            exe = Path(tempfile.gettempdir()) / "EAappInstaller.exe"
            if not exe.exists() or exe.stat().st_size < 1_000_000:
                log("Downloading EAappInstaller.exe...")
                dl_ok = False
                try:
                    rc = subprocess.run(["/usr/bin/curl", "-fsSL", "-o", str(exe), EA_APP_SETUP_URL],
                                        capture_output=True, timeout=300).returncode
                    dl_ok = (rc == 0 and exe.exists() and exe.stat().st_size > 1_000_000)
                except Exception as cexc:
                    log(f"curl download failed: {cexc}")
                if not dl_ok:
                    import ssl as _ssl
                    noverify = _ssl.create_default_context()
                    noverify.check_hostname = False
                    noverify.verify_mode = _ssl.CERT_NONE
                    with urllib.request.urlopen(EA_APP_SETUP_URL, context=noverify, timeout=300) as resp:
                        exe.write_bytes(resp.read())
                log("EAappInstaller.exe downloaded.")
        # EA App's installer needs all of these for its own JunoInitializeSession custom
        # action (confirmed live, see docstring above), not just the launched app.
        _provision_redist_dlls(prefix)                      # real MS d3dcompiler_47
        try:
            _install_wine_mono(prefix, "d3dmetal")           # working CLR for the installer's custom actions
        except Exception as exc:
            log(f"EA App install: wine-mono install skipped: {exc}")
        try:
            _install_corefonts(prefix)                       # MS core fonts for the installer's own CEF UI
        except Exception as exc:
            log(f"EA App install: corefonts install skipped: {exc}")

        logf = str(Path(prefix) / "mnc-eaapp-installer.log")
        log(f"Launching EAappInstaller.exe in {prefix} (pre-HACK22 wine so the installer stub wont fault-storm; log {logf})")
        # Confirmed live: neither /S nor /silent produced an actual silent install -- the
        # installer (itself CEF-based, per EA's own docs) launched real GUI/GPU-init
        # processes (MoltenVK/Vulkan init in the log, 2 Dock icons, no window content, no
        # files ever landing) and then exited with nothing installed. This matches a
        # well-documented CEF-under-Wine failure mode (blank/non-rendering window unless
        # GPU compositing is disabled) rather than a silent-flag problem. Append the
        # standard CEF/Chromium flags known to fix this class of bug.
        #
        # DEPRECATED 2026-07-25: this used to run on the pre-HACK22 overlay wine with
        # Chromium's SOFTWARE compositor (--disable-gpu --disable-gpu-compositing). Both are
        # gone. The 1603/JunoInitializeSession failure the docstring above describes was never
        # a missing prerequisite or an overlay-vs-unified difference -- it was the missing
        # Rosetta-2 WoW64 thunk workaround in dlls/wow64cpu/cpu.c (a far ljmp not switching the
        # CPU to 64-bit, so syscall_32to64's body decoded as 32-bit and faulted). With that
        # fixed in the engine, EA's own Burn installer runs to "Apply complete, result: 0x0"
        # on the unified wine (live-confirmed 2026-07-25), so it takes the same generic CEF
        # path as everything else: DXMT + cef_safe_mode, and real GPU rendering rather than
        # the software fallback. The prerequisites above stay -- they are genuinely needed.
        install_env = _unified_env(prefix, "dxmt", False, for_steam=False,
                                   cef_safe_mode=True)
        install_env["WINEDEBUG"] = "-all,+err"
        # unified engine is optional; fall back to whatever wine _run_installer_unified will
        # itself fall back to, so the gecko regedit never becomes the thing that fails here
        _ubt = _unified_build_dir()
        gecko_wine = str(_ubt / "wine") if _ubt else _installer_wine()
        if gecko_wine:
            _apply_gecko_regedit(gecko_wine, install_env)   # mshtml is enabled above (needs_dotnet)
        # the installer stub IS a CEF browser process, and we start it directly, so hand it
        # the switches on argv (the engine's CreateProcess hook only sees its children)
        cef_argv = install_env.get("MNC_WEBHELPER_FLAGS", "").split()
        proc = _run_installer_unified(
            prefix, [str(exe), "/silent"] + cef_argv, log_path=logf, env=install_env,
        )
        _ea_app_setup_proc = proc
    except Exception as exc:
        log(f"Warning: failed to run EAappInstaller: {exc}")


def cmd_install_ea_app(params: Dict[str, Any]) -> Any:
    """Kick off the EA App bootstrap for a bottle, lazily -- called the first
    time the user interacts with an EA-managed Epic title, not at bottle
    creation time like Steam (most Epic bottles never touch one)."""
    prefix = params.get("prefix")
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")
    if (_ea_app_dir(prefix) / "EADesktop.exe").exists():
        return {"already_installed": True}
    wine = _find_wine()
    if not wine:
        raise RuntimeError("Wine not found")
    threading.Thread(
        target=_download_and_run_eaapp_setup,
        args=(prefix, wine, params.get("ea_app_setup_path")),
        daemon=True,
    ).start()
    return {"already_installed": False}


def cmd_ea_app_install_status(params: Dict[str, Any]) -> Any:
    """Drives the "Installing EA App…" loading screen, mirroring cmd_steam_install_status.
    installed = EADesktop.exe present. running = an EAappInstaller process is still alive."""
    prefix = params.get("prefix")
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")
    installed = (_ea_app_dir(prefix) / "EADesktop.exe").exists()
    running = False
    try:
        out = subprocess.run(["pgrep", "-f", "EAappInstaller"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        running = bool(out)
    except Exception:
        pass
    return {"installed": installed, "running": running}


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
        _ensure_progfiles_x86(path_str)
    else:
        log("Wine not found, skipping wineboot initialization")

   
    if launcher_type == "steam" and wine:
        # the Steam bootstrapper's first-run download is BROKEN under our wine (32-bit HACK22 storm
        # on the unified wine; "failed to create updater window" on the pre-HACK22 wine), so SEED a
        # working client from the cached template insted. only fall back to the bootstrapper if
        # theres no seed source yet (no prior working Steam install to build the template from).
        def _provision_steam():
            if not _seed_steam_client(path_str):
                _download_and_run_steam_setup(path_str, wine, params.get("steam_setup_path"))
        threading.Thread(target=_provision_steam, daemon=True).start()

   
    if launcher_type == "epic":
        threading.Thread(target=_download_legendary_if_needed, daemon=True).start()
    if launcher_type == "amazon":
        threading.Thread(target=_download_nile_if_needed, daemon=True).start()

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
    config.setdefault("game_msync", False)  # msync dormant by default (see WINEMSYNC note); cold-boot Steam crash otherwise
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
    proc = _run_installer_unified(str(prefix), tail, "d3dmetal", log_path=logf)
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
    proc = _run_installer_unified(str(prefix), tail, "d3dmetal", log_path=logf)
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


def cmd_exe_arch(params: Dict[str, Any]) -> Any:
    """Is this exe 32-bit (i386)?  {"is32": true|false|null}

    null means "we could not tell" -- not a PE, unreadable, or a stub we cannot
    parse -- and callers should treat that the same way _apply_4gb_patch does:
    leave the decision alone rather than guess.

    The UI uses this to hide settings that are meaningless on a 64-bit title:
    the 4GB patch (64-bit is large-address-aware by definition) and the x87 JIT
    (wine only engages the loader for a 32-bit PE, see use_rosetta_x87_loader).
    """
    exe = params.get("exe") or ""
    if not exe or not os.path.isfile(exe):
        return {"is32": None}
    info = _pe_header_info(exe)
    if not info:
        return {"is32": None}
    return {"is32": info[0] == _PE_MACHINE_I386}


def cmd_detect_exes_labeled(params: Dict[str, Any]) -> Any:
    """Like detect_exes, but each entry carries a human label.

    For a Steam title the label is Steam's own launch description ("Train
    Simulator 64-bit Edition"), which says far more than a filename does --
    especially for games that ship a 32-bit and a 64-bit build side by side.
    Falls back to the file name when Steam has nothing to say."""
    install_dir = params.get("install_dir")
    if not install_dir:
        raise ValueError("Missing 'install_dir' parameter")
    steam_dir = None
    prefix = params.get("prefix")
    if prefix:
        try:
            steam_dir = _steam_dir(str(prefix))
        except Exception:
            steam_dir = None
    appid = str(params.get("steam_appid", "") or "")
    paths = _detect_all_exes(Path(install_dir), steam_dir, appid)
    labels: Dict[str, str] = {}
    if steam_dir is not None and appid:
        for exe_rel, desc in _steam_launch_exes(steam_dir, appid):
            if desc:
                labels[Path(exe_rel).name.lower()] = desc
    out = []
    for p in paths:
        name = Path(p).name
        out.append({"path": p, "label": labels.get(name.lower(), ""),
                    "is32": (lambda i: None if not i else i[0] == _PE_MACHINE_I386)(
                        _pe_header_info(p))})
    return out


def cmd_detect_exes(params: Dict[str, Any]) -> Any:
    """List all plausible game executables in a game's install directory.

    Pass prefix + steam_appid when known: Steam's own launch list is far more
    reliable than guessing from the filesystem (see _detect_all_exes)."""
    install_dir = params.get("install_dir")
    if not install_dir:
        raise ValueError("Missing 'install_dir' parameter")
    steam_dir = None
    prefix = params.get("prefix")
    if prefix:
        try:
            steam_dir = _steam_dir(str(prefix))
        except Exception:
            steam_dir = None
    return _detect_all_exes(Path(install_dir), steam_dir,
                            str(params.get("steam_appid", "") or ""))


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
        {"id": BACKEND_WINE_DEVEL, "label": "OpenGL (SDL3 / GL 3.2, e.g. Mewgenics)", "available": _unified_available()},
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
    # OpenGL lives in the unified wine now, so report the capability, not the retired
    # standalone app -- keying on the app made OpenGL read as missing everywhere.
    has_wine_devel = _opengl_available()
    wine_version = _get_wine_version()
    return {
        "has_tools": has_tools,
        "has_wine": has_wine_stable or has_wine_staging or has_wine_devel or _unified_available(),
        "has_wine_stable": has_wine_stable,
        "has_wine_staging": has_wine_staging,
        "has_wine_devel": has_wine_devel,
        "has_opengl": has_wine_devel,
        "has_mesa": _mesa_available(),
        "has_dxvk64": _dxvk_available(),
        "has_dxvk32": has_dxvk32,
        "has_dxmt": _dxmt_available(),
        "has_dxmt_openxr": _dxmt_openxr_available(),
        "has_gptk_dlls": _gptk_dlls_available(),
        "has_d3dmetal3": _d3dmetal3_available(),
        "has_wine_d3dmetal": _wine_d3dmetal_installed(),
        "has_wine_unified": _unified_available(),
        "has_mnc_fonts": _mnc_fonts_staged(),
        "has_vkd3d": _vkd3d_available(),
        "wine_version": wine_version,
        "has_rpc_bridge": _rpc_bridge_available(),
        "has_wineopenxr": _wineopenxr_available(),
        "has_monado_runtime": _monado_runtime_available(),
        "has_winetricks": _winetricks_bin() is not None,
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
            "unified_arch": "x86_64-windows",
            "prefix_dir": "drive_c/windows/system32",
        },
        {
            "arch": "x86",
            "wine_dir": stable_root / "i386-windows",
            "unified_arch": "i386-windows",
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

    # Compare each prefix against the wine that ACTUALY bootstrapped it. Prefixes are
    # built by the UNIFIED wine now, whose builtins are legitimately different from Wine
    # Stable's (unified ntdll is 4.6MB, stable's is 0.7MB) -- diffing them against Stable
    # flagged evry single file as "mismatched" on a perfectly healthy prefix, and on a box
    # with no Wine Stable installed it insted reported everything as missing. Prefer the
    # unified builtin, fall back to Stable for prefixes that realy came from it.
    unified_root = _unified_build_dir()
    for source in _stable_prefix_dll_sources():
        wine_dir: Path = source["wine_dir"]
        prefix_dir = prefix_path / source["prefix_dir"]
        arch = source["arch"]
        unified_arch = source["unified_arch"]

        if not wine_dir.is_dir() and not unified_root:
            source_missing.append(f"{arch}: {wine_dir}")
            continue
        if not prefix_dir.is_dir():
            prefix_missing.append(f"{arch}: {source['prefix_dir']}/")
            continue

        arch_checked = 0
        arch_missing = 0
        arch_mismatched = 0
        for name in PREFIX_DLL_VERIFY_FILES:
            stable_file = _unified_pe_builtin(name, unified_arch) or (wine_dir / name)
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
        details.append("Missing in the reference wine:")
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
    if _steam_process is not None and not running:
        _steam_process = None
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
# Winetricks App Store
# ---------------------------------------------------------------------------
# winetricks + cabextract ship in the same portable-deps zip as git/7zz/wget/zstd
# (install_portable_tools() in installer.sh) and land in PORTABLE_DIR/bin, which
# _wine_env() already prepends onto PATH -- no separate download/vendoring needed.

def _winetricks_bin() -> Optional[str]:
    p = PORTABLE_DIR / "bin" / "winetricks"
    return str(p) if p.exists() else None


def _winetricks_wine_and_server(prefix: str) -> Tuple[str, str]:
    """Resolve the wine winetricks should use: the unified engine, same as every other
    installer path (see _installer_wine()). Deliberately ignores the bottle's own
    wine_binary preference (stable/staging/auto) -- that preference is for GAME
    rendering, not installer compatibility."""
    wine = _installer_wine()
    if not wine:
        raise FileNotFoundError("Wine not found")
    # Pass WINESERVER explicitly rather than relying on winetricks' own
    # dirname(WINE)-relative search.
    return wine, (_find_wineserver() or "")


def _winetricks_env(prefix: str, wine: str, wineserver: str) -> Dict[str, str]:
    """Base env for a winetricks subprocess. Deliberately built on _wine_env
    (WINEPREFIX, WINEDEBUG, PATH incl. PORTABLE_DIR/bin so winetricks finds
    cabextract/7zz/wget, DYLD fallback for wine's own font rendering) rather
    than _unified_env, which carries DXMT/Steam-CEF-specific env winetricks
    has no use for."""
    env = _wine_env(prefix)
    env["WINE"] = wine
    if wineserver:
        env["WINESERVER"] = wineserver
    env["WINETRICKS_LATEST_VERSION_CHECK"] = "disabled"
    return env


def _winetricks_popen(prefix: str, verb: str, force: bool = False) -> subprocess.Popen:
    wtk = _winetricks_bin()
    if not wtk:
        raise FileNotFoundError("Winetricks isn't installed yet — run Setup first.")
    wine, wineserver = _winetricks_wine_and_server(prefix)
    # Same prerequisites _run_installer_unified requires: a fast-booted
    # bottle can have an empty syswow64, which makes 32-bit installers die
    # with c0000135 before winetricks even gets a chance to run them.
    _stage_syswow64(prefix)
    _ensure_progfiles_x86(prefix)
    env = _winetricks_env(prefix, wine, wineserver)
    env["WINEDEBUG"] = "-all,+err"
    # arch strips DYLD_*, so re-export it inside the subshell -- same pattern
    # _run_installer_unified uses.
    dyld = env.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    flags = "-q" + (" -f" if force else "")
    sh = (f"export DYLD_FALLBACK_LIBRARY_PATH={shlex.quote(dyld)}\n"
          f"exec {shlex.quote(wtk)} {flags} {shlex.quote(verb)}")
    log(f"winetricks: running {verb} (wine={wine})")
    return subprocess.Popen(
        ["/usr/bin/arch", "-x86_64", "/bin/bash", "-lc", sh],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        start_new_session=True,
    )


def cmd_winetricks_run(params: Dict[str, Any]) -> Any:
    """Run one or more winetricks verbs as a background job. Reuses the SAME
    _install_jobs registry / cmd_get_install_progress polling contract as
    cmd_run_installer -- the Swift side needs no new progress-polling code."""
    prefix: str = params.get("prefix", "")
    verbs: List[str] = params.get("verbs", [])
    force: bool = bool(params.get("force", False))
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")
    if not verbs:
        raise ValueError("No verbs specified")
    if not _winetricks_bin():
        raise FileNotFoundError("Winetricks isn't installed yet — run Setup first.")

    job_id = str(uuid.uuid4())
    job: Dict[str, Any] = {"lines": [], "done": False, "failed": False, "current": "", "proc": None}
    _install_jobs[job_id] = job

    def _run() -> None:
        any_failed = False
        for verb in verbs:
            job["current"] = verb
            job["lines"].append(f"=== Installing {verb} ===")
            try:
                proc = _winetricks_popen(prefix, verb, force=force)
                job["proc"] = proc
                assert proc.stdout is not None
                for line in proc.stdout:
                    job["lines"].append(line.rstrip())
                proc.wait()
                job["proc"] = None
                if proc.returncode != 0:
                    job["lines"].append(f"!!! {verb} failed (exit {proc.returncode})")
                    any_failed = True
                else:
                    job["lines"].append(f"--- {verb} installed ---")
            except Exception as exc:
                job["lines"].append(f"!!! {verb} error: {exc}")
                any_failed = True
                job["proc"] = None
        job["current"] = ""
        job["failed"] = any_failed
        job["lines"].append("=== All done ===" if not any_failed else "=== Finished with errors ===")
        job["done"] = True

    threading.Thread(target=_run, daemon=True, name="winetricks-run").start()
    return {"job_id": job_id}


def cmd_winetricks_cancel(params: Dict[str, Any]) -> Any:
    """Terminate a running winetricks job, mirroring cmd_legendary_cancel_install."""
    job_id = params.get("job_id", "")
    job = _install_jobs.get(job_id)
    if job and job.get("proc") is not None:
        try:
            job["proc"].terminate()
        except Exception:
            pass
        job["lines"].append("=== Cancelled ===")
        job["failed"] = True
        job["done"] = True
        job["proc"] = None
    return {"ok": True}


def cmd_winetricks_list_installed(params: Dict[str, Any]) -> Any:
    """Ground truth is $WINEPREFIX/winetricks.log (one verb id per completed
    line, appended by winetricks itself) rather than duplicating state in
    ~/.macncheese_bottles.json -- stays correct even if winetricks was ever
    run outside the app."""
    prefix = params.get("prefix")
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")
    log_path = Path(prefix).expanduser() / "winetricks.log"
    if not log_path.exists():
        return {"installed": []}
    try:
        lines = log_path.read_text(errors="ignore").splitlines()
    except Exception:
        return {"installed": []}
    installed = sorted({ln.strip() for ln in lines if ln.strip()})
    return {"installed": installed}


_winetricks_catalog_cache: Optional[List[Dict[str, str]]] = None

# w_metadata <id> <category> \
#     title="..." \
#     ...
_WTK_METADATA_RE = re.compile(
    r'^w_metadata\s+(\S+)\s+(\S+)\s*\\\s*\n'
    r'((?:^\s+\w+="(?:[^"\\]|\\.)*"\s*\\?\s*\n)*)',
    re.MULTILINE,
)
_WTK_FIELD_RE = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def _parse_winetricks_catalog() -> List[Dict[str, str]]:
    """Extract every verb winetricks actually supports, straight from its own
    w_metadata declarations in the bundled script -- id, category, title (+
    publisher/year when present). This is the same source `winetricks
    list-all` itself is built from, so the catalog can never drift from what
    the bundled binary can actually install, and needs no hand maintenance
    as winetricks adds/removes verbs in future bundle updates."""
    global _winetricks_catalog_cache
    if _winetricks_catalog_cache is not None:
        return _winetricks_catalog_cache
    wtk = _winetricks_bin()
    if not wtk:
        return []
    try:
        text = Path(wtk).read_text(errors="ignore")
    except Exception:
        return []
    verbs: List[Dict[str, str]] = []
    for m in _WTK_METADATA_RE.finditer(text):
        verb_id, category, fields_block = m.group(1), m.group(2), m.group(3)
        fields = dict(_WTK_FIELD_RE.findall(fields_block))
        verbs.append({
            "id": verb_id,
            "category": category,
            "title": fields.get("title", verb_id),
            "publisher": fields.get("publisher", ""),
            "year": fields.get("year", ""),
        })
    verbs.sort(key=lambda v: (v["category"], v["title"].lower()))
    _winetricks_catalog_cache = verbs
    return verbs


def cmd_winetricks_catalog(_params: Dict[str, Any]) -> Any:
    return {"verbs": _parse_winetricks_catalog()}

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


def _epic_third_party_store(g: Dict[str, Any]) -> Optional[str]:
    """Returns e.g. "The EA App" for Epic-catalog titles Epic's own catalog
    flags as requiring install/activation through another launcher (surfaced
    by `legendary info --json` as `external_activation`), else None. Read
    straight from the same raw metadata blob `legendary list --json` already
    returns and that's already cached on disk -- no extra network/subprocess
    call needed."""
    try:
        ca = g.get("metadata", g).get("customAttributes", {})
        return ca.get("ThirdPartyManagedApp", {}).get("value") or None
    except (AttributeError, TypeError):
        return None


def _epic_third_party_store_for(app_name: str, prefix: str) -> Optional[str]:
    """Looks up _epic_third_party_store() for a single app_name from the
    already-cached disk library -- no network call. Used at launch time to
    decide whether this title needs the link2ea:// handoff instead of a
    normal legendary launch."""
    for g in _read_disk_library(prefix):
        if g.get("app_name") == app_name:
            return _epic_third_party_store(g)
    return None


def _epic_origin_launch_uri(app_name: str, prefix: str) -> Optional[str]:
    """Builds the same link2ea://launchgame/... URI legendary's own `launch --origin`
    would build, and returns it for us to hand to `wine start` directly.

    Bundled legendary is 0.20.34 (Dec 2023) -- 8 months before upstream commit
    56a2314 ("Support both origin and EA App names", #632, Aug 2024) taught
    `Game.is_origin_game` to recognize "The EA App", not just the older "Origin"
    string. Epic's catalog now universally uses "The EA App" for these entries, so
    `legendary launch --origin` unconditionally fails with "not an Origin title"
    on this build (live-confirmed) -- it never even reaches the URI construction.
    No newer official release exists to upgrade to (0.20.34 is still "Latest").

    Rather than patch/replace the shared legendary binary (used for every Epic
    install, not just this), replicate just the URI-building step using pieces
    legendary already exposes/maintains on disk: `get-token` (stable, documented
    CLI command) for the exchange code, and its own persisted user.json for the
    account identity. Mirrors legendary/core.py's get_origin_uri() exactly."""
    try:
        lenv = _legendary_env(prefix)
        # Bradar 120s, not 30s. This is a live round-trip to Epic's auth service: ~13-14s on
        # an idle machine, but 32-40s measured on a heavily loaded one -- and a game launch
        # is exactly when the machine is busy. At 30s that tips over and the launch dies with
        # "Could not build the EA App launch link", making a third-party title unlaunchable
        # for a reason that has nothing to do with the title. The ceiling only costs us
        # patience in the failure case, so keep it well clear of the load-induced range.
        r = subprocess.run(
            _legendary_cmd(prefix) + ["get-token", "--json"],
            capture_output=True, text=True, timeout=120, env=lenv,
        )
        token = json.loads(r.stdout) if r.stdout.strip() else {}
        code = token.get("code")
        if not code:
            log(f"EA origin launch: get-token failed for {app_name}: {r.stderr.strip()[:300]}")
            return None

        user_path = _legendary_config_dir(prefix) / "user.json"
        user = json.loads(user_path.read_text())
        username = user.get("displayName", "")
        account_id = user.get("account_id", "")
        if not account_id:
            log(f"EA origin launch: no account_id in user.json for {prefix}")
            return None

        params = [
            ("AUTH_PASSWORD", code),
            ("AUTH_TYPE", "exchangecode"),
            ("epicusername", username),
            ("epicuserid", account_id),
            ("epiclocale", "en"),
        ]
        for g in _read_disk_library(prefix):
            if g.get("app_name") == app_name:
                extra = (g.get("metadata", g).get("customAttributes", {})
                         .get("AdditionalCommandline", {}).get("value"))
                if extra:
                    params.extend(urllib.parse.parse_qsl(extra))
                break

        return f"link2ea://launchgame/{app_name}?{urllib.parse.urlencode(params)}"
    except Exception as exc:
        log(f"EA origin launch: failed to build link2ea:// URI for {app_name}: {exc}")
        return None


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
        third_party = _epic_third_party_store(g)
        # A third-party-managed title (EA app etc.) is installed by that launcher, not by
        # legendary, so legendary's installed record NEVER lists it -- the card stayed on
        # "Download" forever even with the game sitting on disk. Ask the managing launcher's
        # own record instead. Only consulted when legendary has nothing, so a normal Epic
        # install is completely unaffected.
        if not is_installed and third_party:
            ea = _ea_install_for_title(app_title, prefix)
            if ea:
                is_installed = True
                install_dir = str(ea["dir"])
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
            "third_party_store": third_party,
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


# NOTE: Nile's exact `library list --json` / `installed.json` field names below
# are best-effort — inferred from the CLI's `--id` based subcommands rather than
# confirmed against a real Amazon account (not verifiable in this environment).
# Each lookup tries a few plausible key names and degrades gracefully (empty
# string / not-installed) rather than raising if the real shape differs.

def _nile_cover_url(entry: Dict[str, Any]) -> str:
    product = entry.get("product", entry)
    for key in ("iconUrl", "coverUrl", "boxArtUrl", "imageUrl", "image"):
        url = product.get(key) if isinstance(product, dict) else None
        if url:
            return url
    images = entry.get("images") or entry.get("keyImages") or []
    if isinstance(images, list):
        for img in images:
            if isinstance(img, dict) and img.get("url"):
                return img["url"]
    return ""


_NILE_LIB_CACHE_FILE = "macncheese_amazon_library.json"


def _nile_read_disk_library(prefix: str) -> List[Dict[str, Any]]:
    """Read the owned-games list from the per-bottle disk cache (instant, no network)."""
    path = _nile_config_dir(prefix) / _NILE_LIB_CACHE_FILE
    if not path.exists():
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return []


def _nile_write_disk_library(prefix: str, owned: List[Dict[str, Any]]) -> None:
    """Persist the owned-games list to disk so future scans are instant."""
    path = _nile_config_dir(prefix) / _NILE_LIB_CACHE_FILE
    try:
        _nile_config_dir(prefix).mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(owned, f)
    except Exception as exc:
        log(f"nile: disk library write failed: {exc}")


def _nile_read_installed_here(prefix: str) -> Dict[str, Dict[str, Any]]:
    """Read installed games from this bottle's isolated Nile config — always instant."""
    path = _nile_config_dir(prefix) / "installed.json"
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        entries = list(data.values()) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        results: Dict[str, Dict[str, Any]] = {}
        for entry in entries:
            amazon_id = entry.get("id") or entry.get("app_name") or entry.get("asin")
            if amazon_id:
                results[amazon_id] = entry
        return results
    except Exception as exc:
        log(f"nile: failed to read {path}: {exc}")
        return {}


def _nile_build_games_list(prefix: str, owned_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build the game list from owned library + current installed state (all disk reads, no network)."""
    installed_here = _nile_read_installed_here(prefix)
    games: List[Dict[str, Any]] = []
    for g in owned_list:
        amazon_id = g.get("id") or g.get("app_name") or g.get("asin") or ""
        if not amazon_id:
            continue
        product = g.get("product", g)
        title = (product.get("title") if isinstance(product, dict) else None) or g.get("title", amazon_id)
        is_installed = amazon_id in installed_here
        install_dir = installed_here[amazon_id].get("path", "") if is_installed else ""
        exe = _detect_exe(Path(install_dir), amazon_id, title) if install_dir else None
        games.append({
            "appid": f"amazon_{amazon_id}",
            "name": title,
            "exe": exe,
            "install_dir": install_dir,
            "cover_url": _nile_cover_url(g),
            "exe_icon": None,
            "exe_icon_format": "",
            "is_manual": False,
            "is_installed": is_installed,
            "update_available": False,
            "amazon_id": amazon_id,
        })
    games.sort(key=lambda g: (0 if g["is_installed"] else 1, g["name"].lower()))
    return games


def _nile_updates_from_cli(prefix: str) -> set:
    """Ask nile directly which owned ids have an update available (no metadata guessing)."""
    try:
        r = subprocess.run(
            _nile_cmd(prefix) + ["list-updates", "--json"],
            capture_output=True, text=True, timeout=60, env=_nile_env(prefix),
        )
        data = json.loads(r.stdout) if r.stdout.strip() else []
        ids = data if isinstance(data, list) else data.get("updates", [])
        return {i.get("id") if isinstance(i, dict) else i for i in ids}
    except Exception:
        return set()


def _refresh_nile_cache(prefix: str) -> None:
    """Background thread: serve disk cache instantly, then fetch fresh library from network."""
    try:
        # Phase 1 — instant: build from disk cache and push to memory immediately.
        owned_disk = _nile_read_disk_library(prefix)
        if owned_disk:
            games_fast = _nile_build_games_list(prefix, owned_disk)
            _nile_games_cache[prefix] = {
                "games": games_fast, "ts": time.time(), "scanning": True,
            }
            log(f"nile: served {len(games_fast)} games from disk cache for {prefix}")

        # Phase 2 — network: fetch fresh library from Amazon (may be slow during downloads).
        nenv = _nile_env(prefix)
        try:
            r = subprocess.run(
                _nile_cmd(prefix) + ["library", "list", "--json"],
                capture_output=True, text=True, timeout=120, env=nenv,
            )
            owned_raw = json.loads(r.stdout) if r.stdout.strip() else []
        except Exception as exc:
            log(f"nile list failed (network unavailable?): {exc}")
            entry = _nile_games_cache.get(prefix, {})
            entry["scanning"] = False
            _nile_games_cache[prefix] = entry
            return

        owned_list = owned_raw.get("games", owned_raw.get("library", [])) if isinstance(owned_raw, dict) else owned_raw

        # Persist fresh library to disk for next cold start.
        _nile_write_disk_library(prefix, owned_list)

        # Build final list with up-to-date installed status.
        games = _nile_build_games_list(prefix, owned_list)

        # Phase 3 — detect updates by asking nile directly.
        updates_set = _nile_updates_from_cli(prefix)
        if updates_set:
            for g in games:
                if g.get("amazon_id") in updates_set:
                    g["update_available"] = True
            log(f"nile: {len(updates_set)} update(s) available for {prefix}")

        _nile_games_cache[prefix] = {"games": games, "ts": time.time(), "scanning": False}
        log(f"nile: refreshed {len(games)} games from network for {prefix}")

    except Exception as exc:
        log(f"nile: cache refresh failed: {exc}")
        entry = _nile_games_cache.get(prefix, {})
        entry["scanning"] = False
        _nile_games_cache[prefix] = entry


def _scan_nile_games(prefix: str) -> List[Dict[str, Any]]:
    """Returns games immediately from cache; background-refreshes when stale."""
    if not _nile_installed():
        return []

    entry = _nile_games_cache.get(prefix)
    now = time.time()

    if entry:
        if not entry.get("scanning", False):
            age = now - entry.get("ts", 0)
            if age < _NILE_CACHE_TTL:
                return entry["games"]  # fresh in-memory cache — instant
            entry["scanning"] = True
            threading.Thread(target=_refresh_nile_cache, args=(prefix,), daemon=True).start()
        return entry["games"]  # return whatever we have while scanning

    # No in-memory cache — try disk cache for an instant first response.
    owned_disk = _nile_read_disk_library(prefix)
    if owned_disk:
        games_fast = _nile_build_games_list(prefix, owned_disk)
        _nile_games_cache[prefix] = {"games": games_fast, "ts": 0, "scanning": True}
        threading.Thread(target=_refresh_nile_cache, args=(prefix,), daemon=True).start()
        return games_fast

    # Truly cold start — nothing cached yet.
    _nile_games_cache[prefix] = {"games": [], "ts": 0, "scanning": True}
    threading.Thread(target=_refresh_nile_cache, args=(prefix,), daemon=True).start()
    return []
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
            _legendary_games_cache[prefix] = {"games": [], "ts": 0, "scanning": True}
            threading.Thread(target=_refresh_legendary_cache, args=(prefix,), daemon=True).start()
            auth = cmd_legendary_check_auth({"prefix": prefix})
            return {"ok": True, "display_name": auth.get("display_name", ""), "error": ""}
        return {"ok": False, "display_name": "", "error": output.strip()[:400]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "display_name": "", "error": "Authentication timed out"}
    except Exception as exc:
        return {"ok": False, "display_name": "", "error": str(exc)}


# ---------------------------------------------------------------------------
# Nile / Amazon Games support
# ---------------------------------------------------------------------------

def _nile_installed() -> bool:
    return NILE_BIN.exists()


def _download_nile_if_needed() -> None:
    global _nile_installing
    if _nile_installed() or _nile_installing:
        return
    _nile_installing = True
    try:
        log("Downloading Nile (Amazon Games CLI)...")
        # Use GitHub's latest-release redirect — no API call needed, avoids rate limits.
        # Nile publishes the raw arm64 binary directly as a release asset (no zip).
        url = "https://github.com/imLinguin/nile/releases/latest/download/nile_macOS_arm64"
        NILE_DIR.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "MacNCheese/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            with open(NILE_BIN, "wb") as f:
                f.write(resp.read())
        os.chmod(str(NILE_BIN), 0o755)
        subprocess.run(
            ["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(NILE_BIN)],
            capture_output=True,
        )
        log("Nile installed successfully")
    except Exception as exc:
        log(f"Error downloading nile: {exc}")
        try:
            NILE_BIN.unlink(missing_ok=True)
        except Exception:
            pass
    finally:
        _nile_installing = False


def cmd_nile_status(_params: Dict[str, Any]) -> Any:
    return {"installed": _nile_installed(), "installing": _nile_installing}


def cmd_nile_get_auth_params(_params: Dict[str, Any]) -> Any:
    """Starts a Nile device-auth attempt: runs `nile auth --login --non-interactive`,
    which prints a JSON blob with a fresh Amazon sign-in URL plus the PKCE
    client_id / code_verifier / device serial the caller must echo back
    verbatim to cmd_nile_auth once the sign-in redirect is captured."""
    if not _nile_installed():
        raise RuntimeError("Nile is not installed")
    result = subprocess.run(
        [str(NILE_BIN), "auth", "--login", "--non-interactive"],
        capture_output=True, text=True, timeout=30,
    )
    data = json.loads(result.stdout.strip())
    return {
        "url": data["url"],
        "client_id": data["client_id"],
        "code_verifier": data["code_verifier"],
        "serial": data["serial"],
    }


def cmd_nile_check_auth(params: Dict[str, Any]) -> Any:
    prefix = params.get("prefix", "").strip()
    if not prefix or not _nile_installed():
        return {"authenticated": False, "display_name": ""}
    try:
        result = subprocess.run(
            _nile_cmd(prefix) + ["auth", "--status"],
            capture_output=True, text=True, timeout=30,
            env=_nile_env(prefix),
        )
        data = json.loads(result.stdout.strip())
        logged_in = bool(data.get("LoggedIn", False))
        name = data.get("Username", "") if logged_in else ""
        return {"authenticated": logged_in, "display_name": name}
    except Exception:
        return {"authenticated": False, "display_name": ""}


def cmd_nile_auth(params: Dict[str, Any]) -> Any:
    prefix = params.get("prefix", "").strip()
    code = params.get("code", "").strip()
    client_id = params.get("client_id", "").strip()
    code_verifier = params.get("code_verifier", "").strip()
    serial = params.get("serial", "").strip()
    if not all([prefix, code, client_id, code_verifier, serial]):
        raise ValueError("Missing required auth parameters")
    if not _nile_installed():
        raise RuntimeError("Nile is not installed")
    try:
        result = subprocess.run(
            _nile_cmd(prefix) + [
                "register", "--code", code, "--client-id", client_id,
                "--code-verifier", code_verifier, "--serial", serial,
            ],
            capture_output=True, text=True, timeout=60,
            env=_nile_env(prefix),
        )
        auth = cmd_nile_check_auth({"prefix": prefix})
        if auth.get("authenticated"):
            _nile_games_cache[prefix] = {"games": [], "ts": 0, "scanning": True}
            threading.Thread(target=_refresh_nile_cache, args=(prefix,), daemon=True).start()
            return {"ok": True, "display_name": auth.get("display_name", ""), "error": ""}
        output = (result.stdout + result.stderr).strip()[:400]
        return {"ok": False, "display_name": "", "error": output or "Registration failed"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "display_name": "", "error": "Authentication timed out"}
    except Exception as exc:
        return {"ok": False, "display_name": "", "error": str(exc)}


def cmd_nile_scan_status(params: Dict[str, Any]) -> Any:
    prefix = params.get("prefix", "")
    entry = _nile_games_cache.get(prefix, {})
    return {"scanning": entry.get("scanning", False), "count": len(entry.get("games", []))}


def cmd_nile_install_game(params: Dict[str, Any]) -> Any:
    global _nile_queue_worker_running
    amazon_id = params.get("amazon_id", "").strip()
    prefix = params.get("prefix", "").strip()
    if not amazon_id or not prefix:
        raise ValueError("Missing 'amazon_id' or 'prefix'")
    if not _nile_installed():
        raise RuntimeError("Nile is not installed")
    with _nile_queue_lock:
        if amazon_id in _nile_installs:
            return {"queued": False, "position": 0}
        for i, (qid, _) in enumerate(_nile_download_queue):
            if qid == amazon_id:
                return {"queued": True, "position": i + 1}
        _nile_download_queue.append((amazon_id, prefix))
        position = len(_nile_download_queue)
        if not _nile_queue_worker_running:
            _nile_queue_worker_running = True
            t = threading.Thread(target=_nile_queue_worker, daemon=True)
            t.start()
    return {"queued": True, "position": position}


def cmd_nile_install_progress(params: Dict[str, Any]) -> Any:
    amazon_id = params.get("amazon_id", "").strip()
    if not amazon_id:
        raise ValueError("Missing 'amazon_id'")
    entry = _nile_installs.get(amazon_id)
    if not entry:
        return {"progress": 0.0, "done": True, "error": None}
    proc, log_fh, log_path, prefix = entry
    done = proc.poll() is not None
    progress = 0.0
    error = None
    try:
        with open(log_path, "r") as f:
            lines = f.readlines()
        # Best-effort: Nile's install progress log format is unverified against
        # a real Amazon account. Falls back to leaving progress at 0 (indeterminate)
        # rather than raising if the format differs.
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
        _nile_installs.pop(amazon_id, None)
        _nile_games_cache.pop(prefix, None)
    return {"progress": progress, "done": done, "error": error}


def cmd_nile_cancel_install(params: Dict[str, Any]) -> Any:
    amazon_id = params.get("amazon_id", "").strip()
    with _nile_queue_lock:
        for i, (qid, _) in enumerate(_nile_download_queue):
            if qid == amazon_id:
                _nile_download_queue.pop(i)
                break
        entry = _nile_installs.pop(amazon_id, None)
    if entry:
        proc, log_fh = entry[0], entry[1]
        try:
            proc.terminate()
            log_fh.close()
        except Exception:
            pass
    return {"ok": True}


def cmd_nile_all_downloads(_params: Dict[str, Any]) -> Any:
    """Return progress of all active and queued nile downloads."""
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
    with _nile_queue_lock:
        for amazon_id, entry in _nile_installs.items():
            _proc, _fh, log_path, prefix = entry
            result[amazon_id] = {
                "progress": read_progress(log_path),
                "queued": False,
                "queue_position": 0,
                "paused": False,
                "prefix": prefix,
            }
        for i, (amazon_id, prefix) in enumerate(_nile_download_queue):
            result[amazon_id] = {
                "progress": 0.0,
                "queued": True,
                "queue_position": i + 1,
                "paused": False,
                "prefix": prefix,
            }
    for amazon_id, prefix in _nile_paused.items():
        log_path = str(NILE_DIR / f"install_{amazon_id}.log")
        result[amazon_id] = {
            "progress": read_progress(log_path),
            "queued": False,
            "queue_position": 0,
            "paused": True,
            "prefix": prefix,
        }
    return result


def cmd_nile_pause_install(params: Dict[str, Any]) -> Any:
    amazon_id = params.get("amazon_id", "").strip()
    entry = _nile_installs.pop(amazon_id, None)
    if entry:
        proc, log_fh, _log_path, prefix = entry
        try:
            proc.terminate()
            log_fh.close()
        except Exception:
            pass
        _nile_paused[amazon_id] = prefix
        return {"ok": True}
    with _nile_queue_lock:
        for i, (qid, qprefix) in enumerate(_nile_download_queue):
            if qid == amazon_id:
                _nile_download_queue.pop(i)
                _nile_paused[amazon_id] = qprefix
                return {"ok": True}
    return {"ok": False, "error": "Not found"}


def cmd_nile_resume_install(params: Dict[str, Any]) -> Any:
    global _nile_queue_worker_running
    amazon_id = params.get("amazon_id", "").strip()
    prefix = _nile_paused.pop(amazon_id, None) or params.get("prefix", "").strip()
    if not prefix:
        raise ValueError("Unknown amazon_id or missing prefix")
    with _nile_queue_lock:
        _nile_download_queue.append((amazon_id, prefix))
        if not _nile_queue_worker_running:
            _nile_queue_worker_running = True
            threading.Thread(target=_nile_queue_worker, daemon=True).start()
    return {"ok": True}


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
                "error": None,
            }
        for i, (app_name, prefix) in enumerate(_legendary_download_queue):
            result[app_name] = {
                "progress": 0.0,
                "queued": True,
                "queue_position": i + 1,
                "paused": False,
                "prefix": prefix,
                "error": None,
            }
    for app_name, prefix in _legendary_paused.items():
        log_path = str(LEGENDARY_DIR / f"install_{app_name}.log")
        result[app_name] = {
            "progress": read_progress(log_path),
            "queued": False,
            "queue_position": 0,
            "paused": True,
            "prefix": prefix,
            "error": None,
        }
    for app_name, info in _legendary_failed.items():
        if app_name not in result:
            result[app_name] = {
                "progress": 0.0,
                "queued": False,
                "queue_position": 0,
                "paused": False,
                "prefix": info["prefix"],
                "error": info["error"],
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
    bottle_cfg = _load_bottles().get(_resolve_key(prefix), {})
    unified = _unified_engine_active(bottle_cfg)
    third_party_store = _epic_third_party_store_for(app_name, prefix)

    # Epic never hands MacNCheese a raw exe path (legendary owns exe invocation), so
    # look it up from legendary's own installed-games record. Used below to DLL-patch
    # the right game dir on the classic path, and -- regardless of engine -- to find
    # the real game process for Game Mode/Discord tracking (see _HandoffProcess).
    installed_entry = _read_installed_here(prefix_expanded).get(app_name, {})
    install_dir = installed_entry.get("install_path", "")
    exe_name = installed_entry.get("executable", "")
    exe_path = Path(install_dir) / exe_name if install_dir and exe_name else None
    if not (exe_path and exe_path.exists()):
        exe_path = None
        log(f"legendary: couldn't resolve install dir/exe for {app_name}")

    if unified:
        # Same engine Steam/manual launches use (issue #122). Epic titles were stuck on
        # the pre-unified classic path: no D3D DLL staging, no MNC_GAME_BACKEND, no
        # WINE_MAC_GL_CONTEXT_CLAMP -> Unity/OpenGL games (Among Us) crashed on load.
        bt = _unified_build_dir()
        _stage_unified_dlls(prefix_expanded)
        _stage_unified_mf(prefix_expanded)
        # DEPRECATED 2026-07-28: this used to force dxmt for the whole third-party launch, so
        # EA App's CEF processes wouldn't crash on D3DMetal. It also bound the GAME -- picking
        # D3DMetal on Battlefield 4's card silently ran BF4 on DXMT, whose dxgi is the only one
        # of our five builds missing the private DXGID3D10CreateDevice export wine's own d3d10
        # calls, so BF4 aborted before its menu. The engine now scopes the DXMT redirect to CEF
        # host processes itself (is_cef_host_process(), libcef.dll beside the exe), so the
        # launcher gets DXMT and the game gets whatever the user actually chose.
        game_backend = _unified_game_backend(bottle_cfg, backend)
        # Bradar a third-party-managed title (BF4 etc.) launches by handing a link2ea:// URI to
        # the EA App, so this launch IS a CEF launch even though the exe we invoke is `wine
        # start`. It needs the same CEF treatment the Applications section gets or EA App comes
        # up as an empty blue window: MNC_CEF_SAFE_MODE is what lets the engine recognise
        # EADesktop.exe as a CEF browser process (libcef.dll beside it) and put the GPU-spoof
        # switches on the command line Link2EA/EALaunchHelper builds for it -- we never invoke
        # EADesktop ourselves here, so argv delivery cannot reach it. Also blocks winegstreamer
        # for the tree, same as any other CEF app.
        env = _unified_env(prefix_expanded, game_backend, metal_hud,
                            gst_debug=("5" if verbose_debug else "3"),
                            cef_safe_mode=bool(third_party_store),
                            debug=verbose_debug)
        # Bradar backend-specific env, same as _launch_game_unified does for Steam/manual
        # launches. This path never had it, so an Epic game set to DXVK came up with
        # "Required Vulkan extension VK_KHR_surface not supported" (no MoltenVK ICD wired) and
        # a VR title got no OpenXR runtime at all -- both selectable from the game card, both
        # broken only here. Kept in sync deliberately; see _launch_game_unified.
        if game_backend == "vr":
            _ensure_wineopenxr_registered(prefix_expanded)
            env = _apply_monado_runtime_env(env)
        if game_backend == "dxvk":
            vk_icd = _find_moltenvk_icd()
            if vk_icd:
                env["VK_ICD_FILENAMES"] = vk_icd   # legacy vulkan-loader name
                env["VK_DRIVER_FILES"] = vk_icd    # modern vulkan-loader name
            env.setdefault("DXVK_STATE_CACHE", "0")
        # bt/wine, not bt/loader/wine -- the loader-style path can't find the build nls
        wine_bin = str(bt / "wine")
        if _game_needs_dpi_aware(prefix_expanded, install_dir, exe_name, app_name,
                                 bottle_cfg, params):
            # A third-party title hands us no exe, so fall back to the known-title list --
            # the registry key is matched on basename, which is all wine needs.
            _apply_dpi_aware_regedit(wine_bin, env, {exe_name} if exe_name else _DPI_AWARE_EXES)
    else:
        # Classic fallback (unified wine not installed, or bottle engine="classic").
        # Resolve "auto"/"" the same way cmd_launch_game does (issue #105) instead of
        # leaving it unresolved for _apply_backend_env's if/elif chain to silently ignore.
        if not backend or backend == BACKEND_AUTO:
            backend = _classic_default_backend(bottle_cfg) or _resolve_auto_backend(None)
        wine_bin = _backend_wine_binary(backend, "") or _find_wine_for_bottle("auto")
        if not wine_bin:
            raise RuntimeError("No Wine binary found")
        env = _wine_env(prefix_expanded)
        env = _apply_backend_env(env, backend, verbose_debug)
        if metal_hud:
            env["MTL_HUD_ENABLED"] = "1"

        # cmd_launch_game's classic branch DLL-patches the game before every launch
        # (_prepare_game_for_backend) -- for DXMT that's what syncs d3d11/dxgi/d3d10core
        # + winemetal.dll/.so into the wine lib dirs; without it "backend=dxmt" is a no-op
        # and the game silently gets whatever DLLs happen to already be in the wine build.
        if exe_path:
            try:
                _prepare_game_for_backend(backend, exe_path, install_dir)
            except Exception as exc:
                log(f"Warning: DLL patching failed: {exc}")

        # TEMPORARY: the generic _apply_backend_env route above crashes D3DMetal3
        # (UnityPlayer.dll EXCEPTION_ACCESS_VIOLATION on init -- confirmed live).
        # Steam/manual launches never hit this because cmd_launch_game routes
        # D3DMETAL3 through _backend_launch_cmd's heredoc instead: builtin
        # WINEDLLOVERRIDES (D3DMetal.app's own wine ships its own D3D-to-Metal
        # translation, not native-DLL injection) + a direct-exec'd launcher so
        # DYLD_FALLBACK_LIBRARY_PATH survives (macOS strips DYLD_* across `open`).
        # legendary owns exe invocation, so we can't reuse that heredoc as-is (it's
        # built around us calling Popen directly) -- swap in a wrapper script that
        # sets up the identical env and forwards argv, so legendary still generates
        # the Epic auth args and just execs through it.
        if backend == BACKEND_D3DMETAL3:
            wine_bin = _write_d3dmetal_legendary_wrapper(prefix_expanded, metal_hud, verbose_debug)

    env = _apply_sync_env(env, esync, msync, prefix=str(prefix))
    for line in (custom_env_str or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()

    # Always apply retina regedit (handles both on and off states)
    if unified:
        threading.Thread(
            target=_apply_retina_unified, args=(bt, wine_bin, env, retina_mode), daemon=True
        ).start()
    else:
        threading.Thread(
            target=_apply_retina_regedit, args=(wine_bin, env, retina_mode), daemon=True
        ).start()

    # Inject per-bottle legendary config path into the Wine environment
    env["LEGENDARY_CONFIG_PATH"] = str(_legendary_config_dir(prefix))

    # Titles Epic's catalog flags as third-party-managed (e.g. Battlefield 4, fulfilled
    # via "The EA App") have no real manifest to launch directly -- hand off to the
    # managing launcher instead. Derived here (not passed from Swift) so it applies to
    # any such title launched through this one function, not just ones the UI knows about.
    # Build the link2ea:// handoff ourselves (see _epic_origin_launch_uri) rather than
    # `legendary launch --origin` -- the bundled legendary predates EA App-name support
    # and unconditionally rejects these titles as "not an Origin title" (live-confirmed).
    if third_party_store:
        uri = _epic_origin_launch_uri(app_name, prefix)
        if not uri:
            raise RuntimeError(f"Could not build the EA App launch link for {app_name}")
        cmd = [wine_bin, "start", uri]
    else:
        # legendary launch handles Epic auth token generation and passes all required
        # -AUTH_TYPE / -AUTH_PASSWORD / -epicapp / etc. args to Wine automatically.
        cmd = _legendary_cmd(prefix) + [
            "launch", app_name,
            "--wine", wine_bin,
            "--wine-prefix", prefix_expanded,
            "--skip-version-check",
        ]
    log(f"legendary launch: {shlex.join(cmd)}")
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", app_name)
    log_path = str(LOG_DIR / f"{safe_name}-legendary.log")
    log_fh = open(log_path, "wb")
    proc = subprocess.Popen(
        cmd,
        env=env,
        start_new_session=True,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    log_fh.close()

    # legendary hands off to wine and exits within seconds -- proc itself is NOT the
    # game. Track the real wine-hosted exe instead so Game Mode/Discord presence hold
    # for the whole session instead of releasing ~10s after launch (issue: Game Mode
    # visibly flips back off shortly after Among Us starts, while it's still running).
    handoff = _HandoffProcess(proc, exe_path)
    _register_running_game(handoff, enable_game_mode=params.get("game_mode", True))

    # MacNCheese-level Discord presence for Epic launches. Prefer the real
    # title passed from the UI; fall back to the Epic app_name (codename).
    if bottle_cfg.get("discord_rpc", True):
        _discord_presence_for_launch(handoff, "", params.get("game_name", "") or app_name)

    return {"pid": proc.pid, "log_path": log_path}


def cmd_nile_launch_game(params: Dict[str, Any]) -> Any:
    """Launch an Amazon game via nile, which handles Amazon auth token generation."""
    amazon_id = params.get("amazon_id", "").strip()
    prefix = params.get("prefix", "").strip()
    backend = params.get("backend", "auto")
    retina_mode = params.get("retina_mode", False)
    metal_hud = params.get("metal_hud", False)
    esync = params.get("esync")
    msync = params.get("msync")
    custom_env_str = params.get("custom_env", "")
    verbose_debug = bool(params.get("debug", False))

    if not amazon_id or not prefix:
        raise ValueError("Missing 'amazon_id' or 'prefix'")
    if not _nile_installed():
        raise RuntimeError("Nile is not installed")

    prefix_expanded = str(Path(prefix).expanduser().resolve())
    bottle_cfg = _load_bottles().get(_resolve_key(prefix), {})
    unified = _unified_engine_active(bottle_cfg)

    # Amazon never hands MacNCheese a raw exe path (nile owns exe invocation), so look
    # it up the same way _nile_build_games_list does for the library listing (no
    # "executable" field like Epic's installed.json, so fall back to _detect_exe
    # against the install dir). Used below to DLL-patch the right game dir on the
    # classic path, and -- regardless of engine -- to find the real game process for
    # Game Mode/Discord tracking (see _HandoffProcess).
    install_dir = _nile_read_installed_here(prefix_expanded).get(amazon_id, {}).get("path", "")
    exe_str = _detect_exe(Path(install_dir), amazon_id, params.get("game_name", "") or amazon_id) if install_dir else None
    exe_path = Path(exe_str) if exe_str else None
    if not (exe_path and exe_path.exists()):
        exe_path = None
        log(f"nile: couldn't resolve install dir/exe for {amazon_id}")

    if unified:
        # Same engine Steam/manual launches use (issue #122). Amazon titles were stuck on
        # the pre-unified classic path: no D3D DLL staging, no MNC_GAME_BACKEND, no
        # WINE_MAC_GL_CONTEXT_CLAMP -> Unity/OpenGL games crashed on load.
        bt = _unified_build_dir()
        _stage_unified_dlls(prefix_expanded)
        _stage_unified_mf(prefix_expanded)
        game_backend = _unified_game_backend(bottle_cfg, backend)
        env = _unified_env(prefix_expanded, game_backend, metal_hud,
                            gst_debug=("5" if verbose_debug else "3"))
        # bt/wine, not bt/loader/wine -- the loader-style path can't find the build nls
        wine_bin = str(bt / "wine")
        _amazon_exe = exe_path.name if exe_path else ""
        if _amazon_exe and _game_needs_dpi_aware(prefix_expanded, install_dir,
                                                 _amazon_exe, "", bottle_cfg, params):
            _apply_dpi_aware_regedit(wine_bin, env, {_amazon_exe})
    else:
        # Classic fallback (unified wine not installed, or bottle engine="classic").
        # Resolve "auto"/"" the same way cmd_launch_game does (issue #105) instead of
        # leaving it unresolved for _apply_backend_env's if/elif chain to silently ignore.
        if not backend or backend == BACKEND_AUTO:
            backend = _classic_default_backend(bottle_cfg) or _resolve_auto_backend(None)
        wine_bin = _backend_wine_binary(backend, "") or _find_wine_for_bottle("auto")
        if not wine_bin:
            raise RuntimeError("No Wine binary found")
        env = _wine_env(prefix_expanded)
        env = _apply_backend_env(env, backend, verbose_debug)
        if metal_hud:
            env["MTL_HUD_ENABLED"] = "1"

        # cmd_launch_game's classic branch DLL-patches the game before every launch
        # (_prepare_game_for_backend) -- for DXMT that's what syncs d3d11/dxgi/d3d10core
        # + winemetal.dll/.so into the wine lib dirs; without it "backend=dxmt" is a no-op
        # and the game silently gets whatever DLLs happen to already be in the wine build.
        if exe_path:
            try:
                _prepare_game_for_backend(backend, exe_path, install_dir)
            except Exception as exc:
                log(f"Warning: DLL patching failed: {exc}")

        # TEMPORARY: see cmd_legendary_launch_game -- the generic _apply_backend_env
        # route above crashes D3DMetal3 (UnityPlayer.dll EXCEPTION_ACCESS_VIOLATION,
        # confirmed live via the Epic path; same generic code, so it applies here
        # too). Swap in the same wrapper-script approach cmd_launch_game's
        # _backend_launch_cmd heredoc uses for Steam/manual D3DMetal3 launches.
        if backend == BACKEND_D3DMETAL3:
            wine_bin = _write_d3dmetal_legendary_wrapper(prefix_expanded, metal_hud, verbose_debug)

    env = _apply_sync_env(env, esync, msync, prefix=str(prefix))
    for line in (custom_env_str or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()

    # Always apply retina regedit (handles both on and off states)
    if unified:
        threading.Thread(
            target=_apply_retina_unified, args=(bt, wine_bin, env, retina_mode), daemon=True
        ).start()
    else:
        threading.Thread(
            target=_apply_retina_regedit, args=(wine_bin, env, retina_mode), daemon=True
        ).start()

    # Inject per-bottle nile config path into the Wine environment
    env["NILE_CONFIG_PATH"] = str(_nile_config_dir(prefix))

    # nile launch handles Amazon auth token generation and Wine invocation itself.
    cmd = _nile_cmd(prefix) + [
        "launch", amazon_id,
        "--wine", wine_bin,
        "--wine-prefix", prefix_expanded,
    ]
    log(f"nile launch: {shlex.join(cmd)}")
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", amazon_id)
    log_path = str(LOG_DIR / f"{safe_name}-nile.log")
    log_fh = open(log_path, "wb")
    proc = subprocess.Popen(
        cmd,
        env=env,
        start_new_session=True,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    log_fh.close()

    # nile hands off to wine and exits within seconds -- proc itself is NOT the game.
    # Track the real wine-hosted exe instead so Game Mode/Discord presence hold for
    # the whole session instead of releasing ~10s after launch.
    handoff = _HandoffProcess(proc, exe_path)
    _register_running_game(handoff, enable_game_mode=params.get("game_mode", True))

    # MacNCheese-level Discord presence for Amazon launches. Prefer the real
    # title passed from the UI; fall back to the Amazon id.
    if bottle_cfg.get("discord_rpc", True):
        _discord_presence_for_launch(handoff, "", params.get("game_name", "") or amazon_id)

    return {"pid": proc.pid, "log_path": log_path}


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
    "detect_exes_labeled": cmd_detect_exes_labeled,
    "exe_arch": cmd_exe_arch,
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
    "install_ea_app": cmd_install_ea_app,
    "ea_app_install_status": cmd_ea_app_install_status,
    "reorder_bottles": cmd_reorder_bottles,
    "launch_launcher": cmd_launch_launcher,
    "get_exe_icon": cmd_get_exe_icon,
    "run_installer": cmd_run_installer,
    "get_install_progress": cmd_get_install_progress,
    "winetricks_run": cmd_winetricks_run,
    "winetricks_cancel": cmd_winetricks_cancel,
    "winetricks_list_installed": cmd_winetricks_list_installed,
    "winetricks_catalog": cmd_winetricks_catalog,
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
    "nile_status": cmd_nile_status,
    "nile_get_auth_params": cmd_nile_get_auth_params,
    "nile_check_auth": cmd_nile_check_auth,
    "nile_auth": cmd_nile_auth,
    "nile_install_game": cmd_nile_install_game,
    "nile_install_progress": cmd_nile_install_progress,
    "nile_cancel_install": cmd_nile_cancel_install,
    "nile_all_downloads": cmd_nile_all_downloads,
    "nile_scan_status": cmd_nile_scan_status,
    "nile_pause_install": cmd_nile_pause_install,
    "nile_resume_install": cmd_nile_resume_install,
    "nile_launch_game": cmd_nile_launch_game,
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
    "nile_status",
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

def _ensure_cli_on_path() -> None:
    """Best-effort: symlink macndcheese/mnc into /usr/local/bin the moment the app
    runs, so the CLI is on PATH without the user ever running a setup step. Never
    prompts for a password and never blocks startup -- if /usr/local/bin doesn't
    exist or isn't writable, this silently does nothing (the `macndcheese setup`
    subcommand is the loud fallback for that case)."""
    try:
        cli_path = Path(_resources_dir) / "macndcheese"
        if not cli_path.exists():
            return
        target = str(cli_path.resolve())
        bin_dir = Path("/usr/local/bin")
        if not bin_dir.is_dir() or not os.access(str(bin_dir), os.W_OK):
            return
        for link_name in ("macndcheese", "mnc"):
            link_path = bin_dir / link_name
            if link_path.is_symlink() and os.readlink(str(link_path)) == target:
                continue  # already correct
            if link_path.exists() or link_path.is_symlink():
                link_path.unlink()
            link_path.symlink_to(target)
    except Exception as exc:
        log(f"Could not link the CLI onto PATH: {exc}")


def _app_version() -> str:
    """Our own CFBundleShortVersionString for the startup banner.

    backend_server.py ships at <App>.app/Contents/Resources/, so the plist is one dir up.
    Worth the few lines: bug reports used to arrive as a pile of logs with NO way to tell
    which build produced them, so triage started by guessing the version -- and a report
    from a months-old build looks exactly like a fresh regression."""
    plist = Path(__file__).resolve().parent.parent / "Info.plist"
    try:
        out = subprocess.run(["/usr/libexec/PlistBuddy", "-c",
                              "Print :CFBundleShortVersionString", str(plist)],
                             capture_output=True, text=True, timeout=5)
        if (v := (out.stdout or "").strip()):
            return v
    except Exception:
        pass
    return "unknown (not running from an app bundle?)"


def main() -> None:
    # Version + engine layout FIRST. The rest of this log is near-useless for triage without
    # knowing which build wrote it and whether the unified wine is even installed: a
    # pre-unified install has none of the Steam/DXMT/TLS wiring and so fails in ways that
    # look like brand-new bugs.
    _uni = _unified_build_dir()
    log(f"MacNCheese backend server started -- app {_app_version()}")
    log(f"unified wine = {_uni if _uni else 'NOT INSTALLED (old pre-unified layout; run Setup)'}")
    log(f"mnc-tls      = {'present' if (PORTABLE_DIR / 'mnc-tls' / 'libgnutls.30.dylib').exists() else 'MISSING (Steam login can fail on a mac with no Homebrew)'}")
    log(f"mnc-vulkan   = {'present' if (PORTABLE_DIR / 'mnc-vulkan' / 'libvulkan.1.dylib').exists() else 'MISSING (DXVK/VR unavailable on a mac with no Homebrew)'}")
    log(f"mnc-sdl      = {'present' if (PORTABLE_DIR / 'mnc-sdl' / 'libSDL2-2.0.0.dylib').exists() else 'MISSING (game controllers wont work without Homebrew)'}")
    log(f"PORTABLE_DIR = {PORTABLE_DIR}")
    log(f"BOTTLES_BASE = {BOTTLES_BASE}")
    log(f"DEFAULT_PREFIX = {DEFAULT_PREFIX}")

    _ensure_cli_on_path()

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
        _terminate_nile_installs()
        _game_mode_reset()


if __name__ == "__main__":
    main()