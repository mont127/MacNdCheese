#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
import getpass
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Any


from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QThread, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
    QDialog,
    QTabWidget,
)

# SettingsDialog class
class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(680, 520)
        self._build_ui()
        self.load_config_from_parent()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        self._tabs.addTab(self._build_paths_tab(), "Paths")
        self._tabs.addTab(self._build_setup_tab(), "Setup")
        self._tabs.addTab(self._build_dev_tab(), "DEV UI")
        self._tabs.addTab(self._build_logs_tab(), "Logs")

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.save_config_to_parent)
        close_btn.clicked.connect(self.hide)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _build_paths_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self.prefix_combo = QComboBox()
        self.prefix_combo.setEditable(True)
        self.prefix_combo.addItems(self.load_prefixes())
        self.prefix_combo.currentTextChanged.connect(self._save_current_prefixes)

        self.dxvk_src_edit = QLineEdit(DEFAULT_DXVK_SRC)
        self.dxvk_install_edit = QLineEdit(DEFAULT_DXVK_INSTALL)
        self.dxvk_install32_edit = QLineEdit(DEFAULT_DXVK_INSTALL32)
        self.steam_setup_edit = QLineEdit(DEFAULT_STEAM_SETUP)
        self.mesa_dir_edit = QLineEdit(DEFAULT_MESA_DIR)

        form.addRow("Wine prefix", self._build_prefix_row(self.prefix_combo))
        form.addRow("DXVK source", self._browsable(self.dxvk_src_edit, dir=True))
        form.addRow("DXVK install (64-bit)", self._browsable(self.dxvk_install_edit, dir=True))
        form.addRow("DXVK install (32-bit)", self._browsable(self.dxvk_install32_edit, dir=True))
        form.addRow("SteamSetup.exe", self._browsable(self.steam_setup_edit, dir=False))
        form.addRow("Mesa x64 dir", self._browsable(self.mesa_dir_edit, dir=True))

        return widget

    def load_prefixes(self) -> list[str]:
        path = Path.home() / ".macncheese_prefixes.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if isinstance(data, list) and data:
                    return data
            except Exception:
                pass
        return [DEFAULT_PREFIX]

    def _save_current_prefixes(self, *args) -> None:
        current = self.prefix_combo.currentText()
        items = [self.prefix_combo.itemText(i) for i in range(self.prefix_combo.count())]
        if current and current not in items:
            self.prefix_combo.insertItem(0, current)
            self.prefix_combo.setCurrentIndex(0)
            items.insert(0, current)
        
        path = Path.home() / ".macncheese_prefixes.json"
        try:
            path.write_text(json.dumps(items[:10]))
        except Exception:
            pass

    def _build_prefix_row(self, combo: QComboBox) -> QWidget:
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(combo, 1)

        btn_remove = QPushButton("Remove")
        btn_remove.clicked.connect(self._remove_prefix)
        row.addWidget(btn_remove)

        btn_browse = QPushButton("Browse")
        btn_browse.clicked.connect(self._pick_prefix_dir)
        row.addWidget(btn_browse)

        return wrap

    def _remove_prefix(self) -> None:
        idx = self.prefix_combo.currentIndex()
        if idx >= 0:
            self.prefix_combo.removeItem(idx)
        self._save_current_prefixes()

    def _pick_prefix_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Select prefix folder", self.prefix_combo.currentText())
        if chosen:
            self.prefix_combo.setCurrentText(chosen)
            self._save_current_prefixes()

    def _build_setup_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        quick_box = QGroupBox("One-Click")
        quick_layout = QVBoxLayout(quick_box)
        self.quick_setup_btn = QPushButton("One Click Setup")
        self.install_tools_btn = QPushButton("Install Tools")
        self.install_wine_btn = QPushButton("Install Wine")
        self.install_mesa_btn = QPushButton("Install Mesa")
        self.build_dxvk_btn = QPushButton("Build DXVK (64-bit)")
        self.build_dxvk32_btn = QPushButton("Build DXVK (32-bit)")
        self.init_prefix_btn = QPushButton("Init Prefix")
        self.install_steam_btn = QPushButton("Install Steam")
        hint = QLabel("Installs tools, Wine, builds DXVK (64/32), then installs Mesa.")
        hint.setWordWrap(True)
        quick_layout.addWidget(self.quick_setup_btn)
        quick_layout.addWidget(hint)
        layout.addWidget(quick_box)

        steps_box = QGroupBox("Individual Steps")
        grid = QGridLayout(steps_box)
        grid.addWidget(self.install_tools_btn, 0, 0)
        grid.addWidget(self.install_wine_btn, 0, 1)
        grid.addWidget(self.install_mesa_btn, 1, 0)
        grid.addWidget(self.build_dxvk_btn, 1, 1)
        grid.addWidget(self.build_dxvk32_btn, 2, 0)
        grid.addWidget(self.init_prefix_btn, 2, 1)
        grid.addWidget(self.install_steam_btn, 3, 0, 1, 2)
        layout.addWidget(steps_box)
        layout.addStretch()

        return widget

    def _build_dev_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        info = QPlainTextEdit()
        info.setReadOnly(True)
        try:
            dev_text = Path("/tmp/dev_ui_text.txt").read_text()
            info.setPlainText(dev_text)
        except Exception:
            info.setPlainText("Manual installation guide could not be loaded.")
        
        layout.addWidget(info)
        return widget

    def _build_logs_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view)
        return widget

    def _browsable(self, field: QLineEdit, *, dir: bool) -> QWidget:
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(field)
        btn = QPushButton("Browse")
        if dir:
            btn.clicked.connect(lambda: self._pick_dir(field))
        else:
            btn.clicked.connect(lambda: self._pick_file(field))
        row.addWidget(btn)
        return wrap

    def _pick_dir(self, target: QLineEdit) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Select folder", target.text())
        if chosen:
            target.setText(chosen)

    def _pick_file(self, target: QLineEdit) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "Select file", target.text())
        if chosen:
            target.setText(chosen)

    def load_config_from_parent(self) -> None:
        parent = self.parent()
        if parent is None:
            return
        if hasattr(parent, "prefix_combo"):
            self.prefix_combo.setCurrentText(parent.prefix_combo.currentText())
        if hasattr(parent, "dxvk_src_edit"):
            self.dxvk_src_edit.setText(parent.dxvk_src_edit.text())
        if hasattr(parent, "dxvk_install_edit"):
            self.dxvk_install_edit.setText(parent.dxvk_install_edit.text())
        if hasattr(parent, "dxvk_install32_edit"):
            self.dxvk_install32_edit.setText(parent.dxvk_install32_edit.text())
        if hasattr(parent, "steam_setup_edit"):
            self.steam_setup_edit.setText(parent.steam_setup_edit.text())
        if hasattr(parent, "mesa_dir_edit"):
            self.mesa_dir_edit.setText(parent.mesa_dir_edit.text())

    def save_config_to_parent(self) -> None:
        parent = self.parent()
        if parent is None:
            return
        if hasattr(parent, "prefix_combo"):
            current = self.prefix_combo.currentText()
            parent.prefix_combo.setCurrentText(current)
            if current not in [parent.prefix_combo.itemText(i) for i in range(parent.prefix_combo.count())]:
                parent.prefix_combo.insertItem(0, current)
        if hasattr(parent, "dxvk_src_edit"):
            parent.dxvk_src_edit.setText(self.dxvk_src_edit.text())
        if hasattr(parent, "dxvk_install_edit"):
            parent.dxvk_install_edit.setText(self.dxvk_install_edit.text())
        if hasattr(parent, "dxvk_install32_edit"):
            parent.dxvk_install32_edit.setText(self.dxvk_install32_edit.text())
        if hasattr(parent, "steam_setup_edit"):
            parent.steam_setup_edit.setText(self.steam_setup_edit.text())
        if hasattr(parent, "mesa_dir_edit"):
            parent.mesa_dir_edit.setText(self.mesa_dir_edit.text())

    def log(self, message: str) -> None:
        self.log_view.appendPlainText(message)


MODERN_THEME = """
QWidget {
    background-color: #1E1E1E;
    color: #E0E0E0;
    font-family: Arial, Helvetica, sans-serif;
    font-size: 13px;
}

QMainWindow, QDialog {
    background-color: #1E1E1E;
}

QGroupBox {
    background-color: #252526;
    border: 1px solid #333333;
    border-radius: 6px;
    margin-top: 18px; /* Room for title */
    padding-top: 16px;
    padding-bottom: 8px;
    padding-left: 12px;
    padding-right: 12px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top center;
    padding: 0px 8px;
    color: #9CDCFE;
    font-size: 13px;
    font-weight: bold;
    background-color: #252526;
    border-radius: 4px;
}

QPushButton {
    background-color: #333333;
    border: 1px solid #3C3C3C;
    border-radius: 5px;
    padding: 6px 16px;
    color: #FFFFFF;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #3C3C3C;
    border-color: #4A4A4A;
}

QPushButton:pressed {
    background-color: #0E639C;
    border-color: #0E639C;
    color: #FFFFFF;
}

QLineEdit, QComboBox, QPlainTextEdit, QListWidget {
    background-color: #1E1E1E;
    border: 1px solid #3C3C3C;
    border-radius: 4px;
    padding: 8px;
    color: #E0E0E0;
    selection-background-color: #264F78;
    selection-color: #FFFFFF;
}

QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QListWidget:focus {
    border: 1px solid #0E639C;
    background-color: #1E1E1E;
}

QComboBox::drop-down {
    border: none;
    width: 20px;
}

QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 4px solid #9CDCFE;
    margin-right: 8px;
}

QListWidget {
    outline: none;
}

QListWidget::item {
    padding: 8px;
    border-radius: 4px;
    margin-bottom: 2px;
}

QListWidget::item:selected {
    background-color: #0E639C;
    color: #FFFFFF;
}

QListWidget::item:hover:!selected {
    background-color: #2D2D30;
}

QSplitter::handle {
    background-color: transparent;
}

QSplitter::handle:hover {
    background-color: #333333;
}

QScrollBar:vertical {
    border: none;
    background: transparent;
    width: 10px;
    margin: 0px;
}

QScrollBar::handle:vertical {
    background: #424242;
    min-height: 20px;
    border-radius: 5px;
}

QScrollBar::handle:vertical:hover {
    background: #4F4F4F;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical, QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    border: none;
    background: none;
}

QTabWidget::pane {
    border: 1px solid #333333;
    border-radius: 6px;
    background: #252526;
    top: -1px;
}

QTabBar::tab {
    background: #1E1E1E;
    border: 1px solid #333333;
    border-bottom: none;
    padding: 6px 14px;
    margin-right: 2px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    color: #A1A1AA;
}

QTabBar::tab:selected {
    background: #252526;
    color: #9CDCFE;
    font-weight: bold;
    border-bottom: 2px solid #9CDCFE;
}

QTabBar::tab:hover:!selected {
    background: #2D2D30;
    color: #FFFFFF;
}

QLabel {
    color: #CCCCCC;
}

QMessageBox {
    background-color: #1E1E1E;
}
"""

APP_NAME = "MacNCheese"
APP_VERSION = "v2.0.0"
GITHUB_REPO = "mont127/MacNdCheese"
GITHUB_LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
DEFAULT_PREFIX = str(Path.home() / "wined")
DEFAULT_DXVK_SRC = str(Path.home() / "DXVK-macOS")
DEFAULT_DXVK_INSTALL = str(Path.home() / "dxvk-release")
DEFAULT_DXVK_INSTALL32 = str(Path.home() / "dxvk-release-32")
DEFAULT_STEAM_SETUP = str(Path.home() / "Downloads" / "SteamSetup.exe")
DEFAULT_MESA_DIR = str(Path.home() / "mesa" / "x64")
DXVK_DLLS = ("dxgi.dll", "d3d11.dll", "d3d10core.dll")

DEFAULT_MESA_URL = "https://github.com/pal1000/mesa-dist-win/releases/download/23.1.9/mesa3d-23.1.9-release-msvc.7z"


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


# ==== New architecture: LaunchProfile, PrefixModel, GameModel, Component, Backend, Registries ====
@dataclass(frozen=True)
class LaunchProfile:
    launch_type: str = "direct_exe"
    preferred_backend: Optional[str] = None
    required_components: tuple[str, ...] = ()


@dataclass(frozen=True)
class PrefixModel:
    path: Path

    @property
    def steam_dir(self) -> Path:
        return self.path / "drive_c" / "Program Files (x86)" / "Steam"


@dataclass(frozen=True)
class GameModel:
    name: str
    appid: Optional[str]
    install_path: Path
    exe_path: Optional[Path]
    launcher_type: str = "direct_exe"
    preferred_backend: Optional[str] = None
    required_components: tuple[str, ...] = ()


class Component:
    def __init__(self, name: str) -> None:
        self.name = name

    def is_installed(self, prefix: PrefixModel, window: "MainWindow") -> bool:
        raise NotImplementedError

    def install(self, prefix: PrefixModel, window: "MainWindow") -> None:
        raise NotImplementedError

    def repair(self, prefix: PrefixModel, window: "MainWindow") -> None:
        self.install(prefix, window)

    def version(self, prefix: PrefixModel, window: "MainWindow") -> str:
        return "unknown"

    def required_env(self, prefix: PrefixModel, window: "MainWindow") -> dict[str, str]:
        return {}

    def required_dll_overrides(self, prefix: PrefixModel, window: "MainWindow") -> dict[str, str]:
        return {}


class WineComponent(Component):
    def __init__(self) -> None:
        super().__init__("wine")

    def is_installed(self, prefix: PrefixModel, window: "MainWindow") -> bool:
        try:
            return bool(window.wine_binary())
        except Exception:
            return False

    def install(self, prefix: PrefixModel, window: "MainWindow") -> None:
        window.install_wine()

    def version(self, prefix: PrefixModel, window: "MainWindow") -> str:
        try:
            out = subprocess.check_output([window.wine_binary(), "--version"], text=True, stderr=subprocess.STDOUT)
            return out.strip()
        except Exception:
            return "unknown"


class DxvkComponent(Component):
    def __init__(self) -> None:
        super().__init__("dxvk")

    def is_installed(self, prefix: PrefixModel, window: "MainWindow") -> bool:
        return all((window.dxvk_install / "bin" / dll).exists() for dll in DXVK_DLLS)

    def install(self, prefix: PrefixModel, window: "MainWindow") -> None:
        window.build_dxvk()

    def required_dll_overrides(self, prefix: PrefixModel, window: "MainWindow") -> dict[str, str]:
        return {"dxgi": "n,b", "d3d11": "n,b", "d3d10core": "n,b"}


class Vkd3dProtonComponent(Component):
    def __init__(self) -> None:
        super().__init__("vkd3d-proton")

    def is_installed(self, prefix: PrefixModel, window: "MainWindow") -> bool:
        return False

    def install(self, prefix: PrefixModel, window: "MainWindow") -> None:
        raise NotImplementedError("VKD3D-Proton installation is not implemented yet")


class MoltenVkComponent(Component):
    def __init__(self) -> None:
        super().__init__("moltenvk")

    def is_installed(self, prefix: PrefixModel, window: "MainWindow") -> bool:
        return shutil.which("wine") is not None or shutil.which("wine64") is not None

    def install(self, prefix: PrefixModel, window: "MainWindow") -> None:
        raise NotImplementedError("MoltenVK installation is not implemented yet")


class WinetricksComponent(Component):
    def __init__(self) -> None:
        super().__init__("winetricks")

    def is_installed(self, prefix: PrefixModel, window: "MainWindow") -> bool:
        return shutil.which("winetricks") is not None

    def install(self, prefix: PrefixModel, window: "MainWindow") -> None:
        raise NotImplementedError("Winetricks installation is not implemented yet")


class Backend:
    backend_id = "base"
    label = "Base"

    def is_available(self, prefix: PrefixModel, game: GameModel, window: "MainWindow") -> bool:
        return True

    def apply_env(self, env: dict[str, str], game: GameModel, prefix: PrefixModel, window: "MainWindow") -> dict[str, str]:
        return env

    def prepare_game(self, prefix: PrefixModel, game: GameModel, window: "MainWindow") -> dict[str, Any]:
        return {}

    def supports_game(self, game: GameModel) -> bool:
        return True


class WineBuiltinBackend(Backend):
    backend_id = LAUNCH_BACKEND_WINE
    label = "Wine builtin (no DXVK/Mesa)"

    def apply_env(self, env: dict[str, str], game: GameModel, prefix: PrefixModel, window: "MainWindow") -> dict[str, str]:
        env = env.copy()
        env["WINEDLLOVERRIDES"] = "dxgi,d3d11,d3d10core=b"
        env.pop("DXVK_LOG_PATH", None)
        env.pop("DXVK_LOG_LEVEL", None)
        env.pop("GALLIUM_DRIVER", None)
        env.pop("MESA_GLTHREAD", None)
        return env


class DxvkBackend(Backend):
    backend_id = LAUNCH_BACKEND_DXVK
    label = "DXVK (D3D11->Vulkan)"

    def is_available(self, prefix: PrefixModel, game: GameModel, window: "MainWindow") -> bool:
        exe = game.exe_path
        dxvk_bin = window.dxvk_bin_for_exe(exe) if exe is not None else (window.dxvk_install / "bin")
        return all((dxvk_bin / dll).exists() for dll in DXVK_DLLS)

    def prepare_game(self, prefix: PrefixModel, game: GameModel, window: "MainWindow") -> dict[str, Any]:
        current = window.selected_game()
        if current and current.appid == (game.appid or ""):
            window.patch_selected_game()
        return {"kind": "dxvk"}

    def apply_env(self, env: dict[str, str], game: GameModel, prefix: PrefixModel, window: "MainWindow") -> dict[str, str]:
        env = env.copy()
        env["WINEDLLOVERRIDES"] = "dxgi,d3d11,d3d10core=n,b"
        env["DXVK_LOG_PATH"] = str(Path.home() / "dxvk-logs")
        env["DXVK_LOG_LEVEL"] = "info"
        env.pop("GALLIUM_DRIVER", None)
        env.pop("MESA_GLTHREAD", None)
        Path(env["DXVK_LOG_PATH"]).mkdir(parents=True, exist_ok=True)
        return env


class MesaBackend(Backend):
    driver = MESA_DRIVER_LLVMPIPE

    def is_available(self, prefix: PrefixModel, game: GameModel, window: "MainWindow") -> bool:
        return (window.mesa_dir / "opengl32.dll").exists()

    def prepare_game(self, prefix: PrefixModel, game: GameModel, window: "MainWindow") -> dict[str, Any]:
        current = window.selected_game()
        if current and current.appid == (game.appid or "") and game.exe_path is not None:
            applied_driver = window.patch_selected_game_with_mesa(current, game.exe_path, driver=self.driver)
            return {"kind": "mesa", "driver": applied_driver}
        return {"kind": "mesa", "driver": self.driver}

    def apply_env(self, env: dict[str, str], game: GameModel, prefix: PrefixModel, window: "MainWindow") -> dict[str, str]:
        env = env.copy()
        env["GALLIUM_DRIVER"] = self.driver
        env["WINEDLLOVERRIDES"] = "opengl32=n,b"
        env["MESA_GLTHREAD"] = "true"
        env.pop("DXVK_LOG_PATH", None)
        env.pop("DXVK_LOG_LEVEL", None)
        return env


class MesaLlvmpipeBackend(MesaBackend):
    backend_id = LAUNCH_BACKEND_MESA_LLVMPIPE
    label = "Mesa llvmpipe (CPU, safe)"
    driver = MESA_DRIVER_LLVMPIPE


class MesaZinkBackend(MesaBackend):
    backend_id = LAUNCH_BACKEND_MESA_ZINK
    label = "Mesa zink (GPU, Vulkan)"
    driver = MESA_DRIVER_ZINK


class MesaSwrBackend(MesaBackend):
    backend_id = LAUNCH_BACKEND_MESA_SWR
    label = "Mesa swr (CPU rasterizer)"
    driver = MESA_DRIVER_SWR


class Vkd3dProtonBackend(Backend):
    backend_id = "vkd3d-proton"
    label = "VKD3D-Proton (placeholder)"

    def is_available(self, prefix: PrefixModel, game: GameModel, window: "MainWindow") -> bool:
        return False


class DxmtBackend(Backend):
    backend_id = "dxmt"
    label = "DXMT (placeholder)"

    def is_available(self, prefix: PrefixModel, game: GameModel, window: "MainWindow") -> bool:
        return False


class AutoBackend(Backend):
    backend_id = LAUNCH_BACKEND_AUTO
    label = "Auto (recommended)"

    def __init__(self, resolver: "BackendRegistry") -> None:
        self._resolver = resolver

    def supports_game(self, game: GameModel) -> bool:
        return True

    def resolve(self, prefix: PrefixModel, game: GameModel, window: "MainWindow") -> Backend:
        preferred = game.preferred_backend or window.auto_backend_for_game_model(game)
        backend = self._resolver.get(preferred)
        if backend and backend.is_available(prefix, game, window):
            return backend
        fallback = self._resolver.get(LAUNCH_BACKEND_WINE)
        return fallback if fallback is not None else WineBuiltinBackend()

    def prepare_game(self, prefix: PrefixModel, game: GameModel, window: "MainWindow") -> dict[str, Any]:
        backend = self.resolve(prefix, game, window)
        return backend.prepare_game(prefix, game, window)

    def apply_env(self, env: dict[str, str], game: GameModel, prefix: PrefixModel, window: "MainWindow") -> dict[str, str]:
        backend = self.resolve(prefix, game, window)
        return backend.apply_env(env, game, prefix, window)


class ComponentRegistry:
    def __init__(self) -> None:
        self._components: dict[str, Component] = {}

    def register(self, component: Component) -> None:
        self._components[component.name] = component

    def get(self, name: str) -> Optional[Component]:
        return self._components.get(name)

    def values(self) -> Iterable[Component]:
        return self._components.values()


class BackendRegistry:
    def __init__(self) -> None:
        self._backends: dict[str, Backend] = {}

    def register(self, backend: Backend) -> None:
        self._backends[backend.backend_id] = backend

    def get(self, backend_id: str) -> Optional[Backend]:
        return self._backends.get(backend_id)

    def values(self) -> Iterable[Backend]:
        return self._backends.values()


@dataclass
class GameEntry:
    appid: str
    name: str
    install_dir_name: str
    library_root: Path

    @property
    def game_dir(self) -> Path:
        return self.library_root / "steamapps" / "common" / self.install_dir_name

    def detect_exe(self) -> Optional[Path]:
        if not self.game_dir.exists():
            return None

        try:
            shipping = sorted(
                self.game_dir.glob("**/*-Shipping.exe"),
                key=lambda p: p.stat().st_size if p.exists() else 0,
                reverse=True,
            )
            if shipping:
                return shipping[0]
        except Exception:
            pass

        candidates: list[Path] = []
        for name in (
            f"{self.install_dir_name}.exe",
            f"{self.name}.exe",
            f"{self.name.replace(' ', '')}.exe",
            f"{self.install_dir_name.replace(' ', '')}.exe",
        ):
            p = self.game_dir / name
            if p.exists():
                candidates.append(p)

        def _is_probably_not_game(exe: Path) -> bool:
            lowered = exe.name.lower()
            bad_tokens = (
                "unitycrashhandler",
                "crashhandler",
                "unins",
                "uninstall",
                "setup",
                "launcherhelper",
                "steamerrorreporter",
                "vcredist",
                "dxsetup",
            )
            return any(t in lowered for t in bad_tokens)

        root_exes = sorted(self.game_dir.glob("*.exe"), key=lambda p: p.stat().st_size, reverse=True)
        candidates.extend([p for p in root_exes if not _is_probably_not_game(p)])

        sub_exes: list[Path] = []
        patterns = [
            "*/*.exe",
            "*/*/*.exe",
            "*/*/*/*.exe",
            "*/*/*/*/*.exe",
            "*/*/*/*/*/*.exe",
            "*/*/*/*/*/*/*.exe",
            "*/*/*/*/*/*/*/*.exe",
        ]
        for pat in patterns:
            for exe in self.game_dir.glob(pat):
                if exe.is_file() and not _is_probably_not_game(exe):
                    sub_exes.append(exe)

        shipping = [p for p in sub_exes if "shipping.exe" in p.name.lower()]
        shipping.sort(key=lambda p: p.stat().st_size if p.exists() else 0, reverse=True)
        if shipping:
            candidates.extend(shipping)

        sub_exes.sort(key=lambda p: p.stat().st_size, reverse=True)
        candidates.extend(sub_exes)

        for exe in candidates:
            try:
                if exe.exists() and exe.is_file():
                    return exe
            except Exception:
                continue

        return None

    def display(self) -> str:
        return f"{self.name} [{self.appid}]"

    def to_game_model(self, startup_exe: Optional[Path] = None) -> GameModel:
        exe = startup_exe if startup_exe is not None else self.detect_exe()
        launch_type = "steam" if bool(self.appid) else "direct_exe"
        return GameModel(
            name=self.name,
            appid=self.appid,
            install_path=self.game_dir,
            exe_path=exe,
            launcher_type=launch_type,
            preferred_backend=None,
            required_components=("wine", "dxvk") if launch_type == "steam" else ("wine",),
        )

    def detect_exes(self) -> list[Path]:
        if not self.game_dir.exists():
            return []

        def _is_probably_not_game(exe: Path) -> bool:
            lowered = exe.name.lower()
            bad_tokens = (
                "unitycrashhandler",
                "crashhandler",
                "unins",
                "uninstall",
                "setup",
                "launcherhelper",
                "steamerrorreporter",
                "vcredist",
                "dxsetup",
            )
            return any(t in lowered for t in bad_tokens)

        seen: set[str] = set()
        candidates: list[Path] = []

        preferred_names = (
            "Launcher.exe",
            "launcher.exe",
            "WarframeLauncher.exe",
            "Launcher_x64.exe",
        )
        for name in preferred_names:
            for exe in self.game_dir.glob(f"**/{name}"):
                if exe.is_file() and str(exe) not in seen:
                    seen.add(str(exe))
                    candidates.append(exe)

        try:
            shipping = sorted(
                self.game_dir.glob("**/*-Shipping.exe"),
                key=lambda p: p.stat().st_size if p.exists() else 0,
                reverse=True,
            )
            for exe in shipping:
                if str(exe) not in seen:
                    seen.add(str(exe))
                    candidates.append(exe)
        except Exception:
            pass

        for name in (
            f"{self.install_dir_name}.exe",
            f"{self.name}.exe",
            f"{self.name.replace(' ', '')}.exe",
            f"{self.install_dir_name.replace(' ', '')}.exe",
        ):
            p = self.game_dir / name
            if p.exists() and p.is_file() and not _is_probably_not_game(p) and str(p) not in seen:
                seen.add(str(p))
                candidates.append(p)

        try:
            root_exes = sorted(self.game_dir.glob("*.exe"), key=lambda p: p.stat().st_size, reverse=True)
            for p in root_exes:
                if not _is_probably_not_game(p) and str(p) not in seen:
                    seen.add(str(p))
                    candidates.append(p)
        except Exception:
            pass

        patterns = [
            "*/*.exe",
            "*/*/*.exe",
            "*/*/*/*.exe",
            "*/*/*/*/*.exe",
            "*/*/*/*/*/*.exe",
            "*/*/*/*/*/*/*.exe",
            "*/*/*/*/*/*/*/*.exe",
        ]
        sub_exes: list[Path] = []
        for pat in patterns:
            try:
                for exe in self.game_dir.glob(pat):
                    if exe.is_file() and not _is_probably_not_game(exe):
                        sub_exes.append(exe)
            except Exception:
                pass

        try:
            sub_exes.sort(key=lambda p: p.stat().st_size, reverse=True)
        except Exception:
            pass

        for exe in sub_exes:
            if str(exe) not in seen:
                seen.add(str(exe))
                candidates.append(exe)

        return candidates


class CommandWorker(QObject):
    output = pyqtSignal(str)
    error = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, commands: list[list[str]], env: dict[str, str] | None = None, cwd: str | None = None):
        super().__init__()
        self.commands = commands
        self.env = env or os.environ.copy()
        self.cwd = cwd

    def run(self) -> None:
        try:
            for cmd in self.commands:
                self.output.emit(f"$ {' '.join(cmd)}")
                proc = subprocess.Popen(
                    cmd,
                    cwd=self.cwd,
                    env=self.env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    self.output.emit(line.rstrip())
                rc = proc.wait()
                if rc != 0:
                    self.finished.emit(False, f"Command failed with exit code {rc}: {' '.join(cmd)}")
                    return
            self.finished.emit(True, "Done")
        except Exception as exc:
            self.error.emit(str(exc))
            self.finished.emit(False, str(exc))


class SteamScanner:
    APPMANIFEST_RE = re.compile(r'"(?P<key>[^"]+)"\s+"(?P<value>[^"]*)"')

    @staticmethod
    def windows_path_to_unix(prefix: Path, value: str) -> Path:
        normalized = value.replace('\\\\', '\\')
        if re.match(r'^[A-Za-z]:\\', normalized):
            drive = normalized[0].lower()
            remainder = normalized[3:].replace('\\', '/')
            base = prefix / f"drive_{drive}"
            if drive == 'c':
                base = prefix / 'drive_c'
            return base / remainder
        return Path(normalized.replace('\\', '/'))

    @classmethod
    def parse_appmanifest(cls, path: Path) -> Optional[GameEntry]:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return None

        data: dict[str, str] = {}
        for match in cls.APPMANIFEST_RE.finditer(content):
            key = match.group("key")
            value = match.group("value")
            if key in {"appid", "name", "installdir"}:
                data[key] = value

        if not all(k in data for k in ("appid", "name", "installdir")):
            return None

        library_root = path.parent.parent
        return GameEntry(
            appid=data["appid"],
            name=data["name"],
            install_dir_name=data["installdir"],
            library_root=library_root,
        )

    @classmethod
    def library_roots(cls, prefix: Path, steam_dir: Path) -> list[Path]:
        roots: list[Path] = []
        if steam_dir.exists():
            roots.append(steam_dir)

        library_vdf = steam_dir / "steamapps" / "libraryfolders.vdf"
        if not library_vdf.exists():
            return roots

        try:
            content = library_vdf.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return roots

        for key, value in cls.APPMANIFEST_RE.findall(content):
            if key == "path":
                converted = cls.windows_path_to_unix(prefix, value)
                if converted.exists() and converted not in roots:
                    roots.append(converted)
        return roots

    @classmethod
    def scan_games(cls, prefix: Path, steam_dir: Path) -> list[GameEntry]:
        games: list[GameEntry] = []
        for root in cls.library_roots(prefix, steam_dir):
            steamapps = root / "steamapps"
            if not steamapps.exists():
                continue
            for manifest in sorted(steamapps.glob("appmanifest_*.acf")):
                entry = cls.parse_appmanifest(manifest)
                if entry:
                    games.append(entry)
        games.sort(key=lambda g: g.name.lower())
        return games


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 760)

        self.worker_thread: Optional[QThread] = None
        self.worker: Optional[CommandWorker] = None
        self.steam_process: Optional[QProcess] = None
        self.game_process: Optional[QProcess] = None
        self.games: list[GameEntry] = []
        self.last_game_launch_ts: dict[str, float] = {}
        self.last_game_wine_log: dict[str, Path] = {}
        self.selected_startup_exes: dict[str, Path] = {}
        self.settings = SettingsDialog(self)
        self.simple_ui_enabled: bool = False
        self.dev_ui_enabled: bool = False
        self.interactive_install_in_progress: bool = False
        self.interactive_install_action: Optional[str] = None
        self.pending_post_install_action: Optional[str] = None

        self.prefix_combo = self.settings.prefix_combo
        self.dxvk_src_edit = self.settings.dxvk_src_edit
        self.dxvk_install_edit = self.settings.dxvk_install_edit
        self.dxvk_install32_edit = self.settings.dxvk_install32_edit
        self.steam_setup_edit = self.settings.steam_setup_edit
        self.mesa_dir_edit = self.settings.mesa_dir_edit

        self.component_registry = ComponentRegistry()
        self.backend_registry = BackendRegistry()
        self._register_components()
        self._register_backends()

        self._build_ui()
        self._build_menu()
        self.log(f"{APP_NAME} ready")

    def _register_components(self) -> None:
        for component in (
            WineComponent(),
            DxvkComponent(),
            Vkd3dProtonComponent(),
            MoltenVkComponent(),
            WinetricksComponent(),
        ):
            self.component_registry.register(component)

    def _register_backends(self) -> None:
        for backend in (
            WineBuiltinBackend(),
            DxvkBackend(),
            MesaLlvmpipeBackend(),
            MesaZinkBackend(),
            MesaSwrBackend(),
            Vkd3dProtonBackend(),
            DxmtBackend(),
        ):
            self.backend_registry.register(backend)
        self.backend_registry.register(AutoBackend(self.backend_registry))

    def current_prefix_model(self) -> PrefixModel:
        return PrefixModel(path=self.prefix_path)

    def selected_game_model(self, game: Optional[GameEntry] = None) -> Optional[GameModel]:
        entry = game or self.selected_game()
        if entry is None:
            return None
        startup_exe = self.selected_startup_exes.get(entry.appid)
        return entry.to_game_model(startup_exe=startup_exe)

    def auto_backend_for_game_model(self, game: GameModel) -> str:
        token = f"{game.name} {game.install_path.name}".lower()
        if "mewgenics" in token:
            return LAUNCH_BACKEND_MESA_LLVMPIPE
        return LAUNCH_BACKEND_DXVK

    def resolve_backend(self, backend_id: str, game: GameModel, prefix: PrefixModel) -> Backend:
        backend = self.backend_registry.get(backend_id)
        if backend is None:
            backend = self.backend_registry.get(LAUNCH_BACKEND_AUTO)
        if backend is None:
            return WineBuiltinBackend()
        if backend.backend_id == LAUNCH_BACKEND_AUTO and isinstance(backend, AutoBackend):
            return backend.resolve(prefix, game, self)
        if backend.is_available(prefix, game, self):
            return backend
        fallback = self.backend_registry.get(LAUNCH_BACKEND_WINE)
        return fallback if fallback is not None else WineBuiltinBackend()

    def _build_menu(self) -> None:
        check_updates_action = QAction("Check for Updates", self)
        check_updates_action.triggered.connect(self.check_for_updates)
        self.menuBar().addAction(check_updates_action)

        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self.settings.show)
        self.menuBar().addAction(settings_action)

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        self.menuBar().addAction(exit_action)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(24, 24, 24, 24)
        root_layout.setSpacing(16)

        splitter = QSplitter()
        root_layout.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 12, 0)
        left_layout.setSpacing(16)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 0, 0, 0)
        right_layout.setSpacing(16)
        splitter.addWidget(right)
        splitter.setSizes([400, 700])

        steam_box = QGroupBox("Steam")
        steam_layout = QVBoxLayout(steam_box)
        steam_row = QHBoxLayout()
        self.launch_steam_btn = QPushButton("Install & Launch Steam")
        self.launch_steam_btn.clicked.connect(self.unified_steam_action)
        self.scan_games_btn = QPushButton("Scan Games")
        self.scan_games_btn.clicked.connect(self.scan_games)
        steam_row.addWidget(self.launch_steam_btn)
        steam_row.addWidget(self.scan_games_btn)
        steam_layout.addLayout(steam_row)
        left_layout.addWidget(steam_box)

        quick_box = QGroupBox("Quick Setup")
        quick_layout = QVBoxLayout(quick_box)

        self.quick_setup_btn = QPushButton("One Click Setup")
        self.quick_setup_btn.clicked.connect(self.quick_setup)
        quick_layout.addWidget(self.quick_setup_btn)

        self.install_wine_btn = QPushButton("Install Wine")
        self.install_wine_btn.clicked.connect(self.install_wine)
        quick_layout.addWidget(self.install_wine_btn)

        self.check_updates_btn = QPushButton("Check for Updates")
        self.check_updates_btn.clicked.connect(self.check_for_updates)
        quick_layout.addWidget(self.check_updates_btn)

        left_layout.addWidget(quick_box)

        prefix_box = QGroupBox("Fast Prefix Selector")
        prefix_layout = QHBoxLayout(prefix_box)
        
        self.main_prefix_combo = QComboBox()
        self.main_prefix_combo.addItems(self.settings.load_prefixes())
        self.main_prefix_combo.setCurrentText(self.settings.prefix_combo.currentText())
        self.main_prefix_combo.currentTextChanged.connect(self.on_main_prefix_changed)
        prefix_layout.addWidget(self.main_prefix_combo, 1)

        add_prefix_btn = QPushButton("+")
        add_prefix_btn.setFixedWidth(32)
        add_prefix_btn.clicked.connect(self.on_main_add_prefix_clicked)
        prefix_layout.addWidget(add_prefix_btn)
        
        settings_btn = QPushButton("⚙ Settings")
        settings_btn.clicked.connect(self.settings.show)
        prefix_layout.addWidget(settings_btn)
        
        left_layout.addWidget(prefix_box)

        game_box = QGroupBox("Selected Game")
        game_layout = QVBoxLayout(game_box)

        action_row = QHBoxLayout()
        self.patch_dxvk_btn = QPushButton("Patch Selected")
        self.patch_dxvk_btn.clicked.connect(self.patch_selected_game)
        self.launch_game_btn = QPushButton("Launch Selected")
        self.launch_game_btn.clicked.connect(self.launch_selected_game)
        action_row.addWidget(self.patch_dxvk_btn)
        action_row.addWidget(self.launch_game_btn)
        game_layout.addLayout(action_row)

        self.select_startup_exe_btn = QPushButton("Select Startup EXE")
        self.select_startup_exe_btn.clicked.connect(self.select_startup_exe_for_selected_game)
        game_layout.addWidget(self.select_startup_exe_btn)

        backend_row = QHBoxLayout()
        backend_row.addWidget(QLabel("Backend"))
        self.launch_backend_combo = QComboBox()
        for label, value in LAUNCH_BACKENDS:
            self.launch_backend_combo.addItem(label, value)
        self.launch_backend_combo.setCurrentIndex(0)
        backend_row.addWidget(self.launch_backend_combo, 1)
        game_layout.addLayout(backend_row)

        self.game_args_edit = QLineEdit("")
        self.game_args_edit.setPlaceholderText("Extra game args (optional)")
        game_layout.addWidget(self.game_args_edit)

        log_row = QHBoxLayout()
        self.show_dxvk_log_btn = QPushButton("DXVK Log")
        self.show_dxvk_log_btn.clicked.connect(self.show_dxvk_log_for_selected_game)
        self.show_player_log_btn = QPushButton("Unity Log")
        self.show_player_log_btn.clicked.connect(self.show_unity_player_log_for_selected_game)
        log_row.addWidget(self.show_dxvk_log_btn)
        log_row.addWidget(self.show_player_log_btn)
        game_layout.addLayout(log_row)

        left_layout.addWidget(game_box)

        self._quick_setup_box = quick_box

        status_box = QGroupBox("Status")
        status_layout = QVBoxLayout(status_box)
        self.status_label = QLabel("Idle")
        self.status_label.setWordWrap(True)
        status_layout.addWidget(self.status_label)
        left_layout.addWidget(status_box)

        left_layout.addStretch(1)

        games_box = QGroupBox("Installed Games")
        games_layout = QVBoxLayout(games_box)
        self.games_list = QListWidget()
        self.games_list.itemSelectionChanged.connect(self.update_selected_game_status)
        games_layout.addWidget(self.games_list)
        right_layout.addWidget(games_box, 1)

        self._paths_box = None
        self._setup_box = None
        self._runtime_box = None
        self._quick_setup_box = quick_box
        self._status_box = status_box
        self.simple_ui_btn = None
        self.dev_ui_btn = None

    def on_main_prefix_changed(self, text: str) -> None:
        if text:
            self.prefix_combo.setCurrentText(text)
            self.settings.save_config_to_parent()

    def on_main_add_prefix_clicked(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Add new prefix folder", self.main_prefix_combo.currentText())
        if chosen:
            self.main_prefix_combo.setCurrentText(chosen)

    def _with_button(self, field: QLineEdit, button: QPushButton) -> QWidget:
        wrap = QWidget()
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(field)
        layout.addWidget(button)
        return wrap

    def _pick_dir(self, target: QLineEdit) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Select folder", target.text())
        if chosen:
            target.setText(chosen)

    def _pick_file(self, target: QLineEdit) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "Select file", target.text())
        if chosen:
            target.setText(chosen)

    def log(self, message: str) -> None:
        self.settings.log(message)

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)
        self.log(message)

    def toggle_simplified_ui(self) -> None:
        self.simple_ui_enabled = bool(self.simple_ui_btn.isChecked())
        if self.simple_ui_enabled and self.dev_ui_enabled:
            self.dev_ui_enabled = False
            self.dev_ui_btn.setChecked(False)
        self.apply_ui_modes()

    def toggle_dev_ui(self) -> None:
        self.dev_ui_enabled = bool(self.dev_ui_btn.isChecked())
        if self.dev_ui_enabled and self.simple_ui_enabled:
            self.simple_ui_enabled = False
            self.simple_ui_btn.setChecked(False)
        self.apply_ui_modes()

    def apply_ui_modes(self) -> None:
        setup_box = getattr(self, "_setup_box", None)
        quick_setup_box = getattr(self, "_quick_setup_box", None)

        if getattr(self, "simple_ui_enabled", False):
            if setup_box is not None:
                setup_box.setVisible(False)
            if quick_setup_box is not None:
                quick_setup_box.setVisible(True)
            if hasattr(self, "dxvk_src_edit"):
                self.dxvk_src_edit.setVisible(False)
            if hasattr(self, "dxvk_install_edit"):
                self.dxvk_install_edit.setVisible(False)
            if hasattr(self, "dxvk_install32_edit"):
                self.dxvk_install32_edit.setVisible(False)
            if hasattr(self, "mesa_dir_edit"):
                self.mesa_dir_edit.setVisible(False)
            self.set_status("Simplified UI enabled")
            return

        if getattr(self, "dev_ui_enabled", False):
            if setup_box is not None:
                setup_box.setVisible(True)
            if quick_setup_box is not None:
                quick_setup_box.setVisible(False)
            if hasattr(self, "dxvk_src_edit"):
                self.dxvk_src_edit.setVisible(True)
            if hasattr(self, "dxvk_install_edit"):
                self.dxvk_install_edit.setVisible(True)
            if hasattr(self, "dxvk_install32_edit"):
                self.dxvk_install32_edit.setVisible(True)
            if hasattr(self, "mesa_dir_edit"):
                self.mesa_dir_edit.setVisible(True)
            self.set_status("Dev UI enabled")
            return

        if setup_box is not None:
            setup_box.setVisible(True)
        if quick_setup_box is not None:
            quick_setup_box.setVisible(False)
        if hasattr(self, "dxvk_src_edit"):
            self.dxvk_src_edit.setVisible(True)
        if hasattr(self, "dxvk_install_edit"):
            self.dxvk_install_edit.setVisible(True)
        if hasattr(self, "dxvk_install32_edit"):
            self.dxvk_install32_edit.setVisible(True)
        if hasattr(self, "mesa_dir_edit"):
            self.mesa_dir_edit.setVisible(True)
        self.set_status("UI mode reset")

    @property
    def prefix_path(self) -> Path:
        return Path(self.prefix_combo.currentText()).expanduser()

    @property
    def steam_dir(self) -> Path:
        return self.prefix_path / "drive_c" / "Program Files (x86)" / "Steam"

    @property
    def dxvk_src(self) -> Path:
        return Path(self.dxvk_src_edit.text()).expanduser()

    @property
    def dxvk_install(self) -> Path:
        return Path(self.dxvk_install_edit.text()).expanduser()

    @property
    def dxvk_install32(self) -> Path:
        return Path(self.dxvk_install32_edit.text()).expanduser()

    @property
    def steam_setup(self) -> Path:
        return Path(self.steam_setup_edit.text()).expanduser()

    @property
    def mesa_dir(self) -> Path:
        return Path(self.mesa_dir_edit.text()).expanduser()

    def wine_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["WINEPREFIX"] = str(self.prefix_path)
        return env

    def append_log(self, message: str) -> None:
        self.log(message)

    def wine_binary(self) -> str:
        for candidate in (shutil.which("wine"), "/opt/homebrew/bin/wine", "/usr/local/bin/wine"):
            if candidate and Path(candidate).exists():
                return str(candidate)
        raise FileNotFoundError("wine not found. Install Wine first.")

    def wineserver_binary(self) -> str:
        for candidate in (shutil.which("wineserver"), "/opt/homebrew/bin/wineserver", "/usr/local/bin/wineserver"):
            if candidate and Path(candidate).exists():
                return str(candidate)
        return "wineserver"

    def run_commands(
        self,
        commands: list[list[str]],
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        
        if self.worker_thread is not None:
            try:
                if self.worker_thread.isRunning():
                    QMessageBox.warning(self, APP_NAME, "Another setup task is already running.")
                    return
            except RuntimeError:
                self.worker_thread = None
                self.worker = None

        self.set_status("Task running")

       
        self.worker_thread = QThread(self)
        self.worker = CommandWorker(commands, env=env, cwd=cwd)
        self.worker.moveToThread(self.worker_thread)

        self.worker_thread.started.connect(self.worker.run)
        self.worker.output.connect(self.append_log)
        self.worker.error.connect(self.append_log)
        self.worker.finished.connect(self.on_worker_finished)

        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)

        def _cleanup() -> None:
            self.worker_thread = None
            self.worker = None

        self.worker_thread.finished.connect(_cleanup)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)

        self.worker_thread.start()

    def on_worker_finished(self, ok: bool, message: str) -> None:
        self.set_status(message if ok else f"Failed: {message}")

        state = getattr(self, "_unified_state", 0)
        self._unified_state = 0
        if not self.missing_core_tools():
            self.interactive_install_in_progress = False
            self.interactive_install_action = None
            self.pending_post_install_action = None
        if not ok:
            lower = message.lower()
            if "xcode command line tools" in lower or "clt install" in lower:
                QMessageBox.warning(
                    self,
                    APP_NAME,
                    "Xcode Command Line Tools are required before setup can continue. Run 'xcode-select --install', finish the installer, then reopen MacNCheese.",
                )
                self.set_status("Xcode Command Line Tools required")
                return
            if "need sudo access on macos" in lower:
                QMessageBox.warning(
                    self,
                    APP_NAME,
                    "MacNCheese needs an Administrator macOS account for setup. Open Terminal and run 'sudo -v'. If that fails, switch to an admin account and try again.",
                )
                self.set_status("Administrator account required")
                return
            if "password was rejected" in lower:
                QMessageBox.warning(
                    self,
                    APP_NAME,
                    "The macOS password was rejected. Enter the same password you use to sign in to macOS, then try setup again.",
                )
                self.set_status("Incorrect macOS password")
                return
            QMessageBox.warning(self, APP_NAME, message)
            return

        from PyQt6.QtCore import QTimer

        if state == 1:
            self.log("Unified Setup: Prerequisites installed. Checking Steam...")
            QTimer.singleShot(500, self.unified_steam_action)
        elif state == 15:
            self.log("Unified Setup: SteamSetup.exe downloaded. Starting installation...")
            QTimer.singleShot(500, self.unified_steam_action)
        elif state == 2:
            self.log("Unified Setup: Steam installer executed.")
            QTimer.singleShot(500, self.unified_steam_action)

    def has_wine(self) -> bool:
        try:
            return bool(self.wine_binary())
        except Exception:
            return False

    def ensure_wine(self) -> Optional[str]:
        try:
            return self.wine_binary()
        except Exception as exc:
            msg = str(exc)
           
            if "wine not found" in msg.lower() or "no such file" in msg.lower():
                QMessageBox.information(
                    self,
                    APP_NAME,
                    "Wine is not installed or not found in PATH. Starting automatic installation via Homebrew now.",
                )
                self.install_wine()
                return None
            QMessageBox.warning(self, APP_NAME, msg)
            return None

    def request_admin_env(self) -> Optional[dict[str, str]]:
        password, ok = QInputDialog.getText(
            self,
            APP_NAME,
            "Enter your macOS password for installation tasks",
            QLineEdit.EchoMode.Password,
        )
        if not ok:
            return None
        env = os.environ.copy()
        env["MNC_SUDO_PASSWORD"] = password
        return env



    def _version_tuple(self, value: str) -> tuple[int, ...]:
        cleaned = value.strip().lower().lstrip("v")
        parts: list[int] = []
        for part in cleaned.split("."):
            digits = "".join(ch for ch in part if ch.isdigit())
            parts.append(int(digits or 0))
        return tuple(parts)

    def check_for_updates(self) -> None:
        try:
            req = urllib.request.Request(
                GITHUB_LATEST_RELEASE_API,
                headers={"Accept": "application/vnd.github+json", "User-Agent": APP_NAME},
            )
            with urllib.request.urlopen(req, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
            latest_tag = str(payload.get("tag_name") or "").strip()
            release_url = str(payload.get("html_url") or GITHUB_RELEASES_URL)
            if not latest_tag:
                raise ValueError("GitHub did not return a latest release tag")
            if self._version_tuple(latest_tag) > self._version_tuple(APP_VERSION):
                answer = QMessageBox.question(
                    self,
                    APP_NAME,
                    f"A newer version is available.\n\nCurrent: {APP_VERSION}\nLatest: {latest_tag}\n\nOpen the release page?",
                )
                if answer == QMessageBox.StandardButton.Yes:
                    webbrowser.open(release_url)
                return
            QMessageBox.information(
                self,
                APP_NAME,
                f"You are up to date.\n\nCurrent version: {APP_VERSION}",
            )
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"Update check failed: {exc}")

    def install_tools(self) -> None:
        self.run_installer_action_in_terminal("install_tools")

    def install_wine(self) -> None:
        self.run_installer_action_in_terminal("install_wine")

    def install_mesa(self) -> None:
        self.run_installer_action_in_terminal("install_mesa")

    def quick_setup(self) -> None:
        self.run_installer_action_in_terminal("quick_setup")

    def _build_dxvk(self, *, arch: str) -> None:
        action = "build_dxvk64" if arch == "win64" else "build_dxvk32"
        self.run_installer_action_in_terminal(action)

    def build_dxvk(self) -> None:
        self._build_dxvk(arch="win64")

    def build_dxvk32(self) -> None:
        self._build_dxvk(arch="win32")

    def exe_is_32bit(self, exe: Path) -> bool:
        try:
            out = subprocess.check_output(["file", str(exe)], text=True, stderr=subprocess.STDOUT)
        except Exception:
            return False
       
        return "PE32 executable" in out and "PE32+" not in out

    def dxvk_bin_for_exe(self, exe: Path) -> Path:
        if self.exe_is_32bit(exe):
            return self.dxvk_install32 / "bin"
        return self.dxvk_install / "bin"

    def selected_launch_backend(self) -> str:
        try:
            if hasattr(self, "launch_backend_combo"):
                return str(self.launch_backend_combo.currentData())
        except Exception:
            pass
        return LAUNCH_BACKEND_AUTO

    def backend_is_mesa(self, backend: str) -> bool:
        return backend.startswith("mesa:")

    def mesa_driver_from_backend(self, backend: str) -> str:
        
        return backend.split(":", 1)[1] if ":" in backend else MESA_DRIVER_LLVMPIPE

    def auto_backend_for_game(self, game: GameEntry) -> str:
        return self.auto_backend_for_game_model(game.to_game_model(self.selected_startup_exes.get(game.appid)))

    def mesa_runtime_dlls_for_driver(self, driver: str) -> tuple[str, ...]:
        
        base = ("opengl32.dll", "libgallium_wgl.dll", "libglapi.dll")

        
        extras = ("libEGL.dll", "libGLESv2.dll")

        if driver in (MESA_DRIVER_ZINK, MESA_DRIVER_SWR):
            return base + extras
        return base

    def patch_selected_game_with_mesa(self, game: GameEntry, exe: Path, *, driver: str) -> str:
        
        wanted = driver

        dlls = self.mesa_runtime_dlls_for_driver(wanted)
        missing = [dll for dll in dlls if not (self.mesa_dir / dll).exists()]
        if missing:
            
            if wanted in (MESA_DRIVER_ZINK, MESA_DRIVER_SWR):
                self.log(f"Mesa: missing {', '.join(missing)} for '{wanted}', falling back to llvmpipe")
                wanted = MESA_DRIVER_LLVMPIPE
                dlls = self.mesa_runtime_dlls_for_driver(wanted)
                missing = [dll for dll in dlls if not (self.mesa_dir / dll).exists()]

        if missing:
            raise FileNotFoundError(
                f"Missing Mesa DLL(s) in {self.mesa_dir}: {', '.join(missing)}\n\n"
                "Fix: click 'Install Mesa' in the Setup section, or set 'Mesa x64 dir' to the folder that contains those DLLs (usually ~/mesa/x64)."
            )

        
        optional: list[str] = []
        if wanted == MESA_DRIVER_ZINK and (self.mesa_dir / "zink_dri.dll").exists():
            optional.append("zink_dri.dll")

        target_dirs: set[Path] = {game.game_dir, exe.parent}
        for tdir in sorted(target_dirs):
            tdir.mkdir(parents=True, exist_ok=True)

            
            for stale in ("opengl32.dll", "libgallium_wgl.dll", "libglapi.dll", "libEGL.dll", "libGLESv2.dll", "zink_dri.dll"):
                stale_path = tdir / stale
                if stale_path.exists():
                    try:
                        stale_path.unlink()
                    except Exception:
                        pass

            for dll in dlls:
                shutil.copy2(self.mesa_dir / dll, tdir / dll)
            for dll in optional:
                shutil.copy2(self.mesa_dir / dll, tdir / dll)

            copied = list(dlls) + optional
            self.log(f"Copied Mesa ({wanted}) DLLs -> {tdir}: {', '.join(copied)}")

        return wanted

    def init_prefix(self) -> None:
        wine = self.ensure_wine()
        if not wine:
            return
        self.run_installer_action("init_prefix")

    def install_steam(self) -> None:
        wine = self.ensure_wine()
        if not wine:
            return
        env = self.request_admin_env()
        if env is None:
            return
        if not self.steam_setup.exists():
            QMessageBox.warning(self, APP_NAME, f"SteamSetup.exe not found at {self.steam_setup}")
            return
        run_env = env.copy()
        run_env.update(self.wine_env())
        self.run_commands([[wine, str(self.steam_setup)]], env=run_env)

    def launch_steam(self) -> None:
        wine = self.ensure_wine()
        if not wine:
            return
        steam_exe = self.steam_dir / "steam.exe"
        if not steam_exe.exists():
            QMessageBox.warning(self, APP_NAME, "Steam is not installed in this prefix yet.")
            return

        if self.steam_process and self.steam_process.state() != QProcess.ProcessState.NotRunning:
            self.set_status("Steam is already running")
            return

        self.steam_process = QProcess(self)
        env = self.wine_env()
        env.pop("WINEDLLOVERRIDES", None)
        env.pop("DXVK_LOG_PATH", None)
        env.pop("DXVK_LOG_LEVEL", None)
        qenv = QProcessEnvironment.systemEnvironment()
        for key, value in env.items():
            qenv.insert(key, value)
        self.steam_process.setProcessEnvironment(qenv)
        self.steam_process.setWorkingDirectory(str(self.steam_dir))
        self.steam_process.setProgram(wine)
        self.steam_process.setArguments(["steam.exe", "-no-cef-sandbox", "-vgui"])
        self.steam_process.readyReadStandardOutput.connect(lambda: self._drain_process(self.steam_process))
        self.steam_process.readyReadStandardError.connect(lambda: self._drain_process(self.steam_process))
        self.steam_process.started.connect(lambda: self.set_status("Steam started"))
        self.steam_process.errorOccurred.connect(lambda e: self.set_status(f"Steam error: {e}"))
        self.steam_process.start()

    def unified_steam_action(self) -> None:
        if self.steam_process and self.steam_process.state() != QProcess.ProcessState.NotRunning:
            self.set_status("Steam is already running")
            return

        wine_ok = self.has_wine()
        dxvk_ok = (self.dxvk_install / "bin" / "dxgi.dll").exists()
        mesa_ok = (self.mesa_dir / "opengl32.dll").exists()

        steam_installed = (self.steam_dir / "steam.exe").exists()

        if not (wine_ok and dxvk_ok and mesa_ok):
            missing = self.missing_core_tools()

            if self.interactive_install_in_progress:
                self.set_status("Finish the installer in Terminal, then try Launch Steam again")
                QMessageBox.information(
                    self,
                    APP_NAME,
                    "MacNCheese already opened an interactive installer Terminal for the missing tools. Finish setup there, then click Launch Steam again.",
                )
                return

            clt_ok, clt_msg = self.check_clt_installed()
            if not clt_ok:
                QMessageBox.warning(self, APP_NAME, clt_msg)
                self.set_status("Xcode Command Line Tools required")
                return

            self.set_status(f"Missing prerequisites ({', '.join(missing)}). Opening installer Terminal...")
            self._unified_state = 1
            self.run_installer_action_in_terminal("quick_setup", post_action="launch_steam")
            return

        elif not steam_installed:
            self.set_status("Steam not installed in prefix. Launching installer...")

            if not self.steam_setup.exists():
                self.log("SteamSetup.exe missing. Downloading it to Downloads folder...")
                self._unified_state = 15
                self.steam_setup.parent.mkdir(parents=True, exist_ok=True)
                self.run_commands([
                    [
                        "curl",
                        "-L",
                        "-o",
                        str(self.steam_setup),
                        "https://cdn.akamai.steamstatic.com/client/installer/SteamSetup.exe",
                    ]
                ])
            else:
                self._unified_state = 2
                self.install_steam()

            return

        # Everything is ready
        self.launch_steam()

    def _drain_process(self, proc: QProcess | None) -> None:
        if not proc:
            return
        out = bytes(proc.readAllStandardOutput()).decode(errors="ignore")
        err = bytes(proc.readAllStandardError()).decode(errors="ignore")
        for chunk in (out, err):
            if chunk:
                for line in chunk.splitlines():
                    self.log(line)

    def is_unity_game(self, game: GameEntry) -> bool:
        data_dir = game.game_dir / f"{game.install_dir_name}_Data"
        if data_dir.exists():
            return True
        if any(p.is_dir() and p.name.lower().endswith("_data") for p in game.game_dir.iterdir() if game.game_dir.exists()):
            return True
        return False

    def _unity_player_log_candidates(self) -> list[Path]:
        
        base = self.prefix_path / "drive_c" / "users"
        if not base.exists():
            return []
        return list(base.glob("*/AppData/LocalLow/*/*/Player.log")) + list(base.glob("*/AppData/LocalLow/*/Player.log"))

    def latest_unity_player_log_for_game(self, game: GameEntry) -> Optional[Path]:
        candidates = self._unity_player_log_candidates()
        if not candidates:
            return None

        
        needle1 = game.name.lower()
        needle2 = game.install_dir_name.lower()
        preferred = [p for p in candidates if needle1 in str(p).lower() or needle2 in str(p).lower()]
        pool = preferred if preferred else candidates

        
        try:
            pool.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        except Exception:
            return None
        return pool[0] if pool else None

    def show_unity_player_log_for_selected_game(self) -> None:
        game = self.selected_game()
        if not game:
            QMessageBox.warning(self, APP_NAME, "Select a game first.")
            return
        log_path = self.latest_unity_player_log_for_game(game)
        if not log_path or not log_path.exists():
            QMessageBox.warning(self, APP_NAME, "No Unity Player.log found in the prefix yet. Launch the game once, then try again.")
            return
        try:
            text = log_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"Failed to read Player.log: {exc}")
            return
        lines = text.splitlines()
        tail = "\n".join(lines[-200:]) if lines else "(log is empty)"
        self.log(f"--- Unity Player.log: {log_path} (last {min(200, len(lines))} lines) ---")
        for line in tail.splitlines():
            self.log(line)

    def _latest_dxvk_log_for_game(self, game: GameEntry) -> Optional[Path]:
        logs_dir = Path.home() / "dxvk-logs"
        if not logs_dir.exists():
            return None

        patterns = [
            f"{game.install_dir_name}_d3d11.log",
            f"{game.install_dir_name.replace(' ', '')}_d3d11.log",
            f"{game.name}_d3d11.log",
            f"{game.name.replace(' ', '')}_d3d11.log",
            f"{game.install_dir_name}*_d3d11.log",
            f"{game.name.replace(' ', '')}*_d3d11.log",
            f"{game.name}*_d3d11.log",
        ]

        candidates: list[Path] = []
        for pat in patterns:
            candidates.extend(list(logs_dir.glob(pat)))

        if not candidates:
            candidates = list(logs_dir.glob("*_d3d11.log"))

        uniq: dict[str, Path] = {}
        for p in candidates:
            uniq[str(p)] = p
        candidates = list(uniq.values())

        if not candidates:
            return None

        launch_ts = self.last_game_launch_ts.get(game.appid)
        if launch_ts is not None:
            recent = [p for p in candidates if p.exists() and p.stat().st_mtime >= (launch_ts - 5)]
            if recent:
                recent.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                return recent[0]

        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0]

    def show_dxvk_log_for_selected_game(self) -> None:
        game = self.selected_game()
        if not game:
            QMessageBox.warning(self, APP_NAME, "Select a game first.")
            return
        log_path = self._latest_dxvk_log_for_game(game)
        if not log_path or not log_path.exists():
            QMessageBox.warning(self, APP_NAME, "No DXVK d3d11 log found for this game in ~/dxvk-logs yet. Launch the game with DXVK enabled first.")
            return
        try:
            text = log_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"Failed to read log: {exc}")
            return

        lines = text.splitlines()
        tail = "\n".join(lines[-200:]) if lines else "(log is empty)"
        self.log(f"--- DXVK log: {log_path.name} (last {min(200, len(lines))} lines) ---")
        for line in tail.splitlines():
            self.log(line)

    def scan_games(self) -> None:
        games = SteamScanner.scan_games(self.prefix_path, self.steam_dir)
        self.games = games
        self.games_list.clear()
        for game in games:
            item = QListWidgetItem(game.display())
            item.setData(256, game)
            self.games_list.addItem(item)
        self.set_status(f"Found {len(games)} installed game(s)")

    def selected_game(self) -> Optional[GameEntry]:
        item = self.games_list.currentItem()
        if not item:
            return None
        return item.data(256)

    def selected_game_exe(self, game: GameEntry) -> Optional[Path]:
        chosen = self.selected_startup_exes.get(game.appid)
        if chosen and chosen.exists() and chosen.is_file():
            return chosen
        return game.detect_exe()

    def select_startup_exe_for_selected_game(self) -> None:
        game = self.selected_game()
        if not game:
            QMessageBox.warning(self, APP_NAME, "Select a game first.")
            return

        exe_candidates = game.detect_exes()
        labels: list[str] = []
        mapping: dict[str, Path] = {}
        for exe in exe_candidates:
            try:
                rel = str(exe.relative_to(game.game_dir))
            except Exception:
                rel = str(exe)
            label = f"{rel}"
            labels.append(label)
            mapping[label] = exe

        if not labels:
            QMessageBox.warning(self, APP_NAME, f"No EXE files found in {game.game_dir}")
            return

        current = self.selected_startup_exes.get(game.appid)
        current_label = None
        if current:
            for label, path in mapping.items():
                if path == current:
                    current_label = label
                    break

        current_index = labels.index(current_label) if current_label in labels else 0
        choice, ok = QInputDialog.getItem(
            self,
            APP_NAME,
            f"Select startup EXE for {game.name}",
            labels,
            current_index,
            False,
        )
        if not ok or not choice:
            return

        self.selected_startup_exes[game.appid] = mapping[choice]
        self.set_status(f"Startup EXE set for {game.name}: {choice}")

    def update_selected_game_status(self) -> None:
        game = self.selected_game()
        if not game:
            return
        exe = self.selected_game_exe(game)
        self.set_status(
            f"Selected: {game.name} | Folder: {game.game_dir} | EXE: {exe.name if exe else 'not found'}"
        )

    def patch_selected_game(self) -> None:
        game = self.selected_game()
        if not game:
            QMessageBox.warning(self, APP_NAME, "Select a game first.")
            return

        exe = self.selected_game_exe(game)
        dxvk_bin = self.dxvk_bin_for_exe(exe) if exe is not None else (self.dxvk_install / "bin")
        for dll in DXVK_DLLS:
            if not (dxvk_bin / dll).exists():
                QMessageBox.warning(self, APP_NAME, f"Missing {dll} in {dxvk_bin}. Build DXVK first.")
                return

        game.game_dir.mkdir(parents=True, exist_ok=True)

        target_dirs: set[Path] = set()
        target_dirs.add(game.game_dir)

        if exe is not None:
            target_dirs.add(exe.parent)

        windows_no_editor = game.game_dir / "WindowsNoEditor"
        if windows_no_editor.is_dir():
            target_dirs.add(windows_no_editor)

        try:
            for ship in game.game_dir.glob("**/*-Shipping.exe"):
                if ship.is_file():
                    target_dirs.add(ship.parent)
        except Exception:
            pass

        try:
            for p in game.game_dir.glob("**/Binaries/Win64"):
                if p.is_dir():
                    target_dirs.add(p)
        except Exception:
            pass

        try:
            for p in game.game_dir.glob("WindowsNoEditor/**/Binaries/Win64"):
                if p.is_dir():
                    target_dirs.add(p)
        except Exception:
            pass

        for tdir in sorted(target_dirs):
            for dll in DXVK_DLLS:
                shutil.copy2(dxvk_bin / dll, tdir / dll)
            self.log(f"Copied {', '.join(DXVK_DLLS)} -> {tdir}")

        self.set_status(f"Patched {game.name} with local DXVK")

    def launch_selected_game(self) -> None:
        game = self.selected_game()
        if not game:
            QMessageBox.warning(self, APP_NAME, "Select a game first.")
            return
        wine = self.ensure_wine()
        if not wine:
            return
        exe = self.selected_game_exe(game)
        if not exe:
            try:
                root_exes = sorted(game.game_dir.glob('*.exe'))
                sub_exes = sorted(list(game.game_dir.glob('*/*.exe')) + list(game.game_dir.glob('*/*/*.exe')))
                shown = [str(p.relative_to(game.game_dir)) for p in (root_exes + sub_exes)[:20]]
            except Exception:
                shown = []
            hint = "No EXE detected. Some games use a launcher or store the EXE in a subfolder."
            if shown:
                hint += "\n\nEXEs found (first 20):\n" + "\n".join(shown)
            QMessageBox.warning(self, APP_NAME, f"{hint}\n\nFolder: {game.game_dir}")
            return
        self.log(f"Launching EXE: {exe} (cwd={exe.parent})")
        self.log(f"EXE architecture: {'32-bit' if self.exe_is_32bit(exe) else '64-bit'}")
        if not self.steam_process or self.steam_process.state() == QProcess.ProcessState.NotRunning:
            QMessageBox.warning(self, APP_NAME, "Steam must be running first.")
            return

        game_model = self.selected_game_model(game)
        if game_model is None:
            QMessageBox.warning(self, APP_NAME, "Select a game first.")
            return
        prefix_model = self.current_prefix_model()

        backend_id = self.selected_launch_backend()
        resolved_backend = self.resolve_backend(backend_id, game_model, prefix_model)

        try:
            prepare_info = resolved_backend.prepare_game(prefix_model, game_model, self)
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return

        effective_backend = resolved_backend.backend_id
        effective_mesa_driver = ""
        if isinstance(prepare_info, dict):
            effective_backend = str(prepare_info.get("kind", effective_backend)) if prepare_info.get("kind") in {"dxvk", "mesa"} else effective_backend
            effective_mesa_driver = str(prepare_info.get("driver", ""))
            if prepare_info.get("kind") == "dxvk":
                effective_backend = LAUNCH_BACKEND_DXVK
            elif prepare_info.get("kind") == "mesa":
                if effective_mesa_driver == MESA_DRIVER_ZINK:
                    effective_backend = LAUNCH_BACKEND_MESA_ZINK
                elif effective_mesa_driver == MESA_DRIVER_SWR:
                    effective_backend = LAUNCH_BACKEND_MESA_SWR
                else:
                    effective_backend = LAUNCH_BACKEND_MESA_LLVMPIPE

        if self.game_process and self.game_process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.warning(self, APP_NAME, "A game process is already running.")
            return

        self.game_process = QProcess(self)
        env = self.wine_env()
        env = resolved_backend.apply_env(env, game_model, prefix_model, self)
        if self.backend_is_mesa(effective_backend) and not effective_mesa_driver:
            effective_mesa_driver = self.mesa_driver_from_backend(effective_backend)

        qenv = QProcessEnvironment.systemEnvironment()
        for key, value in env.items():
            qenv.insert(key, value)
        self.game_process.setProcessEnvironment(qenv)
    
        exe_dir = exe.parent
        self.game_process.setWorkingDirectory(str(exe_dir))

        args = [exe.name]

        extra = ""
        if hasattr(self, "game_args_edit"):
            extra = self.game_args_edit.text().strip()
        if extra:
            args += extra.split()

        if self.is_unity_game(game):
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", game.install_dir_name or game.name)
            unity_log = str(Path.home() / f"{safe_name}-player.log")
            args += ["-logFile", unity_log]
            self.log(f"Unity log file will be written to: {unity_log}")

        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", game.install_dir_name or game.name)
        host_wine_log = str(Path.home() / f"{safe_name}-wine.log")
        self.log(f"Wine output will be written to: {host_wine_log}")
        self.last_game_launch_ts[game.appid] = time.time()
        self.last_game_wine_log[game.appid] = Path(host_wine_log)

        debug_prefix = "WINEDEBUG=+loaddll"
        if self.backend_is_mesa(effective_backend):
            debug_prefix = "WINEDEBUG=+loaddll,+wgl,+opengl"
        cmd = f"cd {shlex.quote(str(exe_dir))} && {debug_prefix} {shlex.quote(wine)} { ' '.join(shlex.quote(a) for a in args) } > {shlex.quote(host_wine_log)} 2>&1"
        self.game_process.setProgram("bash")
        self.game_process.setArguments(["-lc", cmd])
        self.game_process.readyReadStandardOutput.connect(lambda: self._drain_process(self.game_process))
        self.game_process.readyReadStandardError.connect(lambda: self._drain_process(self.game_process))
        self.game_process.started.connect(
            lambda: self.set_status(
                f"Started {game.name} ({'Mesa ' + effective_mesa_driver if self.backend_is_mesa(effective_backend) else ('DXVK' if effective_backend == LAUNCH_BACKEND_DXVK else 'Wine builtin')})"
            )
        )
        self.game_process.errorOccurred.connect(lambda e: self.set_status(f"Game error: {e}"))

        def _on_game_finished(code, status) -> None:
            self.set_status(f"{game.name} exited with code {code}")

            if effective_backend == LAUNCH_BACKEND_DXVK:
                self.show_dxvk_log_for_selected_game()

            wine_log_path = self.last_game_wine_log.get(game.appid)
            if wine_log_path and wine_log_path.exists():
                try:
                    text = wine_log_path.read_text(encoding="utf-8", errors="ignore")
                    lines = text.splitlines()
                    tail = "\n".join(lines[-200:]) if lines else "(log is empty)"
                    self.log(f"--- Wine log: {wine_log_path.name} (last {min(200, len(lines))} lines) ---")
                    for line in tail.splitlines():
                        self.log(line)
                except Exception as exc:
                    self.log(f"Failed to read wine log {wine_log_path}: {exc}")

            if self.is_unity_game(game):
                self.show_unity_player_log_for_selected_game()

        self.game_process.finished.connect(_on_game_finished)
        self.game_process.start()

    def closeEvent(self, event) -> None:
        for proc in (self.game_process, self.steam_process):
            if proc and proc.state() != QProcess.ProcessState.NotRunning:
                proc.kill()
                proc.waitForFinished(2000)
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(MODERN_THEME)
    win = MainWindow()
    win.show()
    win.apply_ui_modes()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
    def missing_core_tools(self) -> list[str]:
        missing: list[str] = []
        if not self.has_wine():
            missing.append("Wine")
        if not (self.dxvk_install / "bin" / "dxgi.dll").exists():
            missing.append("DXVK")
        if not (self.mesa_dir / "opengl32.dll").exists():
            missing.append("Mesa")
        return missing

    def installer_terminal_command(self, action: str) -> str:
        script = self.installer_script_path()
        args = [
            "bash",
            str(script),
            action,
            str(self.prefix_path),
            str(self.dxvk_src),
            str(self.dxvk_install),
            str(self.dxvk_install32),
            str(self.mesa_dir),
            DEFAULT_MESA_URL,
        ]
        command = " ".join(shlex.quote(part) for part in args)
        return (
            f"cd {shlex.quote(str(script.parent))}; "
            f"echo 'Running MacNCheese installer in interactive Terminal mode'; "
            f"{command}; "
            f"status=$?; "
            f"echo; "
            f"echo 'Installer finished with exit code:' $status; "
            f"echo 'You can run extra commands in this terminal if needed.'; "
            f"exec bash"
        )

    def run_installer_action_in_terminal(self, action: str, *, post_action: Optional[str] = None) -> None:
        script = self.installer_script_path()
        if not script.exists():
            QMessageBox.warning(self, APP_NAME, f"installer.sh not found at {script}")
            return

        if self.interactive_install_in_progress:
            current_missing = self.missing_core_tools()
            if current_missing:
                QMessageBox.information(
                    self,
                    APP_NAME,
                    "The MacNCheese installer terminal is already open. Finish the installation there, then return here and try again.",
                )
                self.set_status("Installer terminal already open")
                return
            self.interactive_install_in_progress = False
            self.interactive_install_action = None
            self.pending_post_install_action = None

        applescript = (
            'tell application "Terminal"\n'
            'activate\n'
            f'do script {json.dumps(self.installer_terminal_command(action))}\n'
            'end tell\n'
        )

        try:
            subprocess.run(["osascript", "-e", applescript], check=True)
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"Failed to open Terminal for installation: {exc}")
            return

        self.interactive_install_in_progress = True
        self.interactive_install_action = action
        self.pending_post_install_action = post_action
        self.log(f"Opened Terminal for installer action: {action}")
        self.set_status(f"Installer opened in Terminal for {action}")

    def _run_shell_check(self, command: str, *, env: dict[str, str] | None = None) -> tuple[int, str]:
        try:
            proc = subprocess.run(
                ["bash", "-lc", command],
                capture_output=True,
                text=True,
                env=env or os.environ.copy(),
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            return proc.returncode, output.strip()
        except Exception as exc:
            return 1, str(exc)

    def check_clt_installed(self) -> tuple[bool, str]:
        rc, out = self._run_shell_check("xcode-select -p")
        if rc == 0 and out:
            return True, out
        return False, "Xcode Command Line Tools are required before setup can continue. Run 'xcode-select --install', finish the macOS installer, then reopen MacNCheese."

    def check_admin_access(self, password: str) -> tuple[bool, str]:
        env = os.environ.copy()
        env["MNC_SUDO_PASSWORD"] = password
        rc, out = self._run_shell_check("printf '%s\\n' \"$MNC_SUDO_PASSWORD\" | sudo -S -k -v", env=env)
        if rc == 0:
            return True, ""
        user_name = getpass.getuser()
        groups_rc, groups_out = self._run_shell_check("id -Gn")
        if groups_rc == 0 and "admin" not in groups_out.split():
            return False, f"The macOS account '{user_name}' is not an Administrator account. Use an admin account, then try again."
        return False, "The macOS password was rejected or sudo is unavailable. Enter the same password you use to sign in to macOS, then try again."

    def prepare_installer_env(self) -> Optional[dict[str, str]]:
        clt_ok, clt_msg = self.check_clt_installed()
        if not clt_ok:
            QMessageBox.warning(self, APP_NAME, clt_msg)
            self.set_status("Xcode Command Line Tools required")
            return None

        env = self.request_admin_env()
        if env is None:
            self.set_status("Setup cancelled")
            return None

        password = env.get("MNC_SUDO_PASSWORD", "")
        admin_ok, admin_msg = self.check_admin_access(password)
        if not admin_ok:
            QMessageBox.warning(self, APP_NAME, admin_msg)
            self.set_status(admin_msg)
            return None

        return env

    def installer_script_path(self) -> Path:
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            candidates = [
                exe_dir / "installer.sh",
                exe_dir.parent / "Frameworks" / "installer.sh",
                exe_dir.parent / "Resources" / "installer.sh",
                Path(getattr(sys, "_MEIPASS", "")) / "installer.sh" if getattr(sys, "_MEIPASS", None) else None,
            ]
            for candidate in candidates:
                if candidate and candidate.exists():
                    return candidate
            return exe_dir / "installer.sh"
        return Path(__file__).resolve().with_name("installer.sh")

    def run_installer_action(self, action: str) -> None:
        env = self.prepare_installer_env()
        if env is None:
            return
        script = self.installer_script_path()
        if not script.exists():
            candidates = []
            if getattr(sys, "frozen", False):
                exe_dir = Path(sys.executable).resolve().parent
                candidates = [
                    exe_dir / "installer.sh",
                    exe_dir.parent / "Frameworks" / "installer.sh",
                    exe_dir.parent / "Resources" / "installer.sh",
                    Path(getattr(sys, "_MEIPASS", "")) / "installer.sh" if getattr(sys, "_MEIPASS", None) else None,
                ]
            checked = "\n".join(str(p) for p in candidates if p is not None)
            QMessageBox.warning(self, APP_NAME, f"installer.sh not found. Checked:\n{checked or script}")
            return
        self.log(f"Using installer script: {script}")
        args = [
            "bash",
            str(script),
            action,
            str(self.prefix_path),
            str(self.dxvk_src),
            str(self.dxvk_install),
            str(self.dxvk_install32),
            str(self.mesa_dir),
            DEFAULT_MESA_URL,
        ]
        self.run_commands([args], env=env, cwd=str(script.parent))
