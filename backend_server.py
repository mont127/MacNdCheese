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

import json
import os
import re
import shlex
import shutil
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

DXVK_DLLS = ("d3d11.dll", "d3d10core.dll")
GPTK_REQUIRED_DLLS = ("atidxx64.dll", "d3d10.dll", "d3d11.dll", "d3d12.dll", "dxgi.dll", "nvapi64.dll", "nvngx.dll")

SKIP_EXE_TOKENS = (
    "crash", "reporter", "setup", "install", "unins",
    "helper", "bootstrap", "diagnostics", "dxwebsetup",
)

# ---------------------------------------------------------------------------
# Logging helper (always to stderr)
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(f"[backend] {msg}", file=sys.stderr, flush=True)

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

def _find_wine() -> Optional[str]:
    candidates = [
        str(PORTABLE_DIR / "Wine Stable.app" / "Contents" / "Resources" / "wine" / "bin" / "wine64"),
        str(PORTABLE_DIR / "Wine Stable.app" / "Contents" / "Resources" / "wine" / "bin" / "wine"),
        str(PORTABLE_DIR / "Wine Staging.app" / "Contents" / "Resources" / "wine" / "bin" / "wine64"),
        str(PORTABLE_DIR / "Wine Staging.app" / "Contents" / "Resources" / "wine" / "bin" / "wine"),
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
    """Apply RetinaMode and LogPixels via `wine regedit file.reg`."""
    retina_val = "y" if retina_mode else "n"
    dpi_hex = "dc" if retina_mode else "60"  # 220=0xdc, 96=0x60
    reg_content = (
        "REGEDIT4\n\n"
        "[HKEY_CURRENT_USER\\Software\\Wine\\Mac Driver]\n"
        f'"RetinaMode"="{retina_val}"\n\n'
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
        log(f"Applied regedit: RetinaMode={retina_val}, LogPixels=000000{dpi_hex}")
    except Exception as exc:
        log(f"Warning: regedit failed: {exc}")


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

def _gptk_full_available() -> bool:
    return Path("/usr/local/bin/gameportingtoolkit").exists() or shutil.which("gameportingtoolkit") is not None


def _resolve_auto_backend() -> str:
    """Pick the best available backend, matching AutoBackend.resolve() logic."""
    # Prefer GPTK > DXVK > wine builtin
    if _gptk_available():
        return BACKEND_GPTK
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
        dxvk_log_dir = str(Path.home() / "dxvk-logs")
        Path(dxvk_log_dir).mkdir(parents=True, exist_ok=True)
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
        dxmt_path = str(DEFAULT_DXMT_DIR)
        env["DXMT_PATH"] = dxmt_path
        backend_ovr = "dxgi,d3d11=n,b"
        existing_winepath = env.get("WINEPATH", "")
        env["WINEPATH"] = dxmt_path if not existing_winepath else f"{dxmt_path};{existing_winepath}"
        env.pop("DXVK_LOG_PATH", None)
        env.pop("DXVK_LOG_LEVEL", None)
        env.pop("GALLIUM_DRIVER", None)
        env.pop("MESA_GLTHREAD", None)

    elif backend == BACKEND_GPTK:
        dll_dir = str(DEFAULT_GPTK_DIR / "lib" / "wine" / "x86_64-windows")
        wine_root = _find_gptk_wine_root()
        if wine_root:
            lib_dir = wine_root / "lib"
            unix_lib_dir = lib_dir / "wine" / "x86_64-unix"
            external_lib_dir = lib_dir / "external"
            env["DYLD_LIBRARY_PATH"] = ":".join([str(unix_lib_dir), str(lib_dir), str(external_lib_dir)])
            env["DYLD_SHARED_REGION"] = "avoid"
            env["WINEESYNC"] = "1"
        wineserver = _find_wineserver()
        if wineserver:
            env["WINESERVER"] = wineserver
        env["WINEPATH"] = dll_dir
        backend_ovr = "atidxx64,d3d10,d3d11,d3d12,dxgi,nvapi64,nvngx=n,b"
        env.pop("DXVK_LOG_PATH", None)
        env.pop("DXVK_LOG_LEVEL", None)
        env.pop("VKD3D_PROTON_PATH", None)
        env.pop("DXMT_PATH", None)
        env.pop("GALLIUM_DRIVER", None)
        env.pop("MESA_GLTHREAD", None)

    elif backend == BACKEND_GPTK_FULL:
        wineserver = _find_wineserver()
        if wineserver:
            env["WINESERVER"] = wineserver

    # Mandatory overrides prepended (matching MacNCheese.py line 5798).
    # In Wine, first match for a DLL wins, so mandatory comes first.
    # Note: nvapi/nvapi64 disabled unless GPTK backend needs them.
    if backend == BACKEND_GPTK:
        mandatory_ovr = "mf,mfplat,mfreadwrite,mfplay=b"
    else:
        mandatory_ovr = "nvapi,nvapi64=;mf,mfplat,mfreadwrite,mfplay=b"
    if backend_ovr:
        env["WINEDLLOVERRIDES"] = f"{mandatory_ovr};{backend_ovr}"
    else:
        env["WINEDLLOVERRIDES"] = mandatory_ovr

    # DXVK log dir always created (for Steam launch etc.)
    dxvk_log_dir = str(Path.home() / "dxvk-logs")
    Path(dxvk_log_dir).mkdir(parents=True, exist_ok=True)
    env.setdefault("DXVK_LOG_PATH", dxvk_log_dir)
    env.setdefault("DXVK_LOG_LEVEL", "info")
    env["WINEDEBUG"] = "-all"

    return env


def _backend_wine_binary(backend: str, exe: str) -> Optional[str]:
    """Return the wine binary for backends that need a special one, else None."""
    if backend == BACKEND_GPTK:
        wine_root = _find_gptk_wine_root()
        if wine_root:
            return str(wine_root / "bin" / "wine64")
    if backend == BACKEND_GPTK_FULL:
        gptk_bin = "/usr/local/bin/gameportingtoolkit"
        if Path(gptk_bin).exists():
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
        dxmt_dlls = ("d3d11.dll", "dxgi.dll")
        if not all((DEFAULT_DXMT_DIR / dll).exists() for dll in dxmt_dlls):
            log(f"DXMT DLLs not found at {DEFAULT_DXMT_DIR}, skipping patch")
        else:
            for tdir in target_dirs:
                tdir.mkdir(parents=True, exist_ok=True)
                for dll in dxmt_dlls:
                    if (DEFAULT_DXMT_DIR / dll).exists():
                        shutil.copy2(str(DEFAULT_DXMT_DIR / dll), str(tdir / dll))
                log(f"Copied DXMT DLLs -> {tdir}")

    elif backend == BACKEND_GPTK:
        # Copy GPTK DLLs into game directory
        gptk_dll_dir = DEFAULT_GPTK_DIR / "lib" / "wine" / "x86_64-windows"
        if not gptk_dll_dir.exists():
            log(f"GPTK DLL dir not found at {gptk_dll_dir}, skipping patch")
        else:
            _unpatch_dxvk(game_dir)
            for tdir in target_dirs:
                tdir.mkdir(parents=True, exist_ok=True)
                for dll in GPTK_REQUIRED_DLLS:
                    src = gptk_dll_dir / dll
                    if src.exists():
                        shutil.copy2(str(src), str(tdir / dll))
                log(f"Copied GPTK DLLs -> {tdir}")

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
                games.append({
                    "appid": appid,
                    "name": name,
                    "exe": exe,
                    "install_dir": str(game_dir),
                    "cover_url": cover_url,
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
        games.append({
            "appid": uid,
            "name": entry_name,
            "exe": exe_str if Path(exe_str).exists() else None,
            "install_dir": str(Path(exe_str).parent) if exe_str else "",
            "cover_url": cover_path or "",
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
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")
    if not exe:
        raise ValueError("Missing 'exe' parameter")

    exe_path = Path(exe)
    if not exe_path.exists():
        raise FileNotFoundError(f"Executable not found: {exe}")

    # Resolve auto backend
    if not backend or backend == BACKEND_AUTO:
        backend = _resolve_auto_backend()
    log(f"Resolved graphics backend: {backend}")

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

    # Apply retina/DPI settings via regedit
    _apply_retina_regedit(wine, env, retina_mode)

    exe_dir = str(exe_path.parent)
    exe_name = exe_path.name

    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", exe_path.stem)
    log_path = str(Path.home() / f"{safe_name}-wine.log")

    arg_parts = shlex.split(args) if args else []
    quoted_args = " ".join(shlex.quote(a) for a in arg_parts)

    cmd = _backend_launch_cmd(
        backend, wine, exe_dir, exe_name, prefix, exe, quoted_args, log_path
    )

    log(f"Launching [{backend}]: bash -lc {cmd!r}")
    proc = subprocess.Popen(
        ["bash", "-lc", cmd],
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
    if not prefix:
        raise ValueError("Missing 'prefix' parameter")

    # Check if Steam is already running
    if _steam_process is not None and _steam_process.poll() is None:
        return {"already_running": True, "pid": _steam_process.pid}

    wine = _find_wine()
    if not wine:
        raise FileNotFoundError("Wine not found. Install Wine first.")

    # Check if this bottle has a custom launcher exe set
    key = _resolve_key(prefix)
    bottle_cfg = _load_bottles().get(key, {})
    launcher_exe = bottle_cfg.get("launcher_exe", "").strip()

    if launcher_exe and Path(launcher_exe).exists():
        # Launch the custom exe instead of Steam
        log(f"Using custom launcher_exe: {launcher_exe}")
        env = _wine_env(prefix)
        resolved = _resolve_auto_backend()
        env = _apply_backend_env(env, resolved)
        _apply_retina_regedit(wine, env, retina_mode)
        exe_path = Path(launcher_exe)
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", exe_path.stem)
        log_path = str(Path.home() / f"{safe_name}-wine.log")
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

    env = _wine_env(prefix)
    # Steam uses the auto-detected backend env
    resolved = _resolve_auto_backend()
    env = _apply_backend_env(env, resolved)

    # Kill existing wineserver before starting Steam (match original behaviour)
    wineserver = _find_wineserver()
    if wineserver:
        try:
            subprocess.run(
                [wineserver, "-k"], env=env, timeout=5,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            time.sleep(1)
        except Exception:
            pass

    # Set retina/DPI via wine regedit with a .reg file
    _apply_retina_regedit(wine, env, retina_mode)

    safe_name = "Steam"
    log_path = str(Path.home() / f"{safe_name}-wine.log")

    cmd = (
        f"cd {shlex.quote(str(steam_dir))} && "
        f"arch -x86_64 {shlex.quote(wine)} "
        f"{shlex.quote(str(steam_exe))} -no-browser -vgui "
        f"> {shlex.quote(log_path)} 2>&1"
    )

    log(f"Launching Steam: bash -lc {cmd!r}")
    proc = subprocess.Popen(
        ["bash", "-lc", cmd],
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
    resolved = _resolve_auto_backend()
    env = _apply_backend_env(env, resolved)
    _apply_retina_regedit(wine, env, retina_mode)

    exe_path = Path(launcher_exe)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", exe_path.stem)
    log_path = str(Path.home() / f"{safe_name}-wine.log")

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
    return bottles.get(key, {})


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
        {"id": BACKEND_GPTK, "label": "GPTK (D3DMetal)", "available": _gptk_available()},
        {"id": BACKEND_GPTK_FULL, "label": "GPTK Full (Apple Toolkit)", "available": _gptk_full_available()},
    ]
    auto_resolved = _resolve_auto_backend()
    return {"backends": all_backends, "auto_resolved": auto_resolved}


def cmd_get_components_status(params: Dict[str, Any]) -> Any:
    """Return installation status for each setup component."""
    has_tools = all(shutil.which(t) is not None for t in ("git", "7z", "winetricks"))
    dxvk32_install = Path.home() / "dxvk-release-32"
    has_dxvk32 = (dxvk32_install / "bin" / "d3d11.dll").exists()
    return {
        "has_tools": has_tools,
        "has_wine": _find_wine() is not None,
        "has_mesa": _mesa_available(),
        "has_dxvk64": _dxvk_available(),
        "has_dxvk32": has_dxvk32,
        "has_gptk_full": _gptk_full_available(),
        "has_d3dmetal3": _gptk_available(),
        "has_gptk": _gptk_available(),
    }


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
    "get_running_games": cmd_get_running_games,
    "get_steam_running": cmd_get_steam_running,
    "get_setup_pid": cmd_get_setup_pid,
    "reorder_bottles": cmd_reorder_bottles,
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
