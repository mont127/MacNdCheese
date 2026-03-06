from __future__ import annotations

import json

from constants import (
    CONFIG_PATH,
    DEFAULT_DXVK_INSTALL,
    DEFAULT_DXVK_INSTALL32,
    DEFAULT_DXVK_SRC,
    DEFAULT_MESA_DIR,
    DEFAULT_PREFIX,
    DEFAULT_STEAM_SETUP,
)

_DEFAULTS: dict[str, str] = {
    "prefix": DEFAULT_PREFIX,
    "dxvk_src": DEFAULT_DXVK_SRC,
    "dxvk_install": DEFAULT_DXVK_INSTALL,
    "dxvk_install32": DEFAULT_DXVK_INSTALL32,
    "steam_setup": DEFAULT_STEAM_SETUP,
    "mesa_dir": DEFAULT_MESA_DIR,
}


def load() -> dict[str, str]:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return {**_DEFAULTS, **{k: v for k, v in data.items() if k in _DEFAULTS}}
    except Exception:
        return dict(_DEFAULTS)


def save(data: dict[str, str]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
