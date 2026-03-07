#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

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
)


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
        self.simple_ui_enabled: bool = False
        self.dev_ui_enabled: bool = False

        self._build_ui()
        self._build_menu()
        self.log(f"{APP_NAME} ready")

    def _build_menu(self) -> None:
        check_updates_action = QAction("Check for Updates", self)
        check_updates_action.triggered.connect(self.check_for_updates)
        self.menuBar().addAction(check_updates_action)

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        self.menuBar().addAction(exit_action)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)

        splitter = QSplitter()
        root_layout.addWidget(splitter)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        splitter.addWidget(right)
        splitter.setSizes([480, 620])

        paths_box = QGroupBox("Paths")
        paths_form = QFormLayout(paths_box)

        self.prefix_edit = QLineEdit(DEFAULT_PREFIX)
        self.dxvk_src_edit = QLineEdit(DEFAULT_DXVK_SRC)
        self.dxvk_install_edit = QLineEdit(DEFAULT_DXVK_INSTALL)
        self.dxvk_install32_edit = QLineEdit(DEFAULT_DXVK_INSTALL32)
        self.steam_setup_edit = QLineEdit(DEFAULT_STEAM_SETUP)
        self.mesa_dir_edit = QLineEdit(DEFAULT_MESA_DIR)

        browse_prefix = QPushButton("Browse")
        browse_prefix.clicked.connect(lambda: self._pick_dir(self.prefix_edit))
        browse_dxvk_src = QPushButton("Browse")
        browse_dxvk_src.clicked.connect(lambda: self._pick_dir(self.dxvk_src_edit))
        browse_dxvk_install = QPushButton("Browse")
        browse_dxvk_install.clicked.connect(lambda: self._pick_dir(self.dxvk_install_edit))
        browse_dxvk_install32 = QPushButton("Browse")
        browse_dxvk_install32.clicked.connect(lambda: self._pick_dir(self.dxvk_install32_edit))
        browse_steam_setup = QPushButton("Browse")
        browse_steam_setup.clicked.connect(lambda: self._pick_file(self.steam_setup_edit))
        browse_mesa_dir = QPushButton("Browse")
        browse_mesa_dir.clicked.connect(lambda: self._pick_dir(self.mesa_dir_edit))

        paths_form.addRow("Wine prefix", self._with_button(self.prefix_edit, browse_prefix))
        paths_form.addRow("DXVK source", self._with_button(self.dxvk_src_edit, browse_dxvk_src))
        paths_form.addRow("DXVK install", self._with_button(self.dxvk_install_edit, browse_dxvk_install))
        paths_form.addRow("DXVK install (32-bit)", self._with_button(self.dxvk_install32_edit, browse_dxvk_install32))
        paths_form.addRow("SteamSetup.exe", self._with_button(self.steam_setup_edit, browse_steam_setup))
        paths_form.addRow("Mesa x64 dir", self._with_button(self.mesa_dir_edit, browse_mesa_dir))

        left_layout.addWidget(paths_box)

        ui_mode_box = QGroupBox("UI")
        ui_mode_layout = QGridLayout(ui_mode_box)

        self.simple_ui_btn = QPushButton("Simplified UI")
        self.simple_ui_btn.setCheckable(True)
        self.simple_ui_btn.clicked.connect(self.toggle_simplified_ui)

        self.dev_ui_btn = QPushButton("Dev UI")
        self.dev_ui_btn.setCheckable(True)
        self.dev_ui_btn.clicked.connect(self.toggle_dev_ui)

        ui_mode_layout.addWidget(self.simple_ui_btn, 0, 0)
        ui_mode_layout.addWidget(self.dev_ui_btn, 0, 1)

        left_layout.addWidget(ui_mode_box)

        
        quick_box = QGroupBox("Quick Setup")
        quick_layout = QVBoxLayout(quick_box)

        self.quick_setup_btn = QPushButton("One Click Setup")
        self.quick_setup_btn.clicked.connect(self.quick_setup)
        quick_layout.addWidget(self.quick_setup_btn)

        self.check_updates_btn = QPushButton("Check for Updates")
        self.check_updates_btn.clicked.connect(self.check_for_updates)
        quick_layout.addWidget(self.check_updates_btn)

        quick_hint = QLabel("Installs tools, Wine, builds DXVK (64/32), then installs Mesa")
        quick_hint.setWordWrap(True)
        quick_layout.addWidget(quick_hint)

        left_layout.addWidget(quick_box)
        self._quick_setup_box = quick_box

        self._paths_box = paths_box

        setup_box = QGroupBox("Setup")
        setup_grid = QGridLayout(setup_box)

        self.install_tools_btn = QPushButton("Install Tools")
        self.install_tools_btn.clicked.connect(self.install_tools)

        self.install_wine_btn = QPushButton("Install Wine")
        self.install_wine_btn.clicked.connect(self.install_wine)

        self.install_mesa_btn = QPushButton("Install Mesa")
        self.install_mesa_btn.clicked.connect(self.install_mesa)

        self.build_dxvk_btn = QPushButton("Build DXVK")
        self.build_dxvk_btn.clicked.connect(self.build_dxvk)
        self.build_dxvk32_btn = QPushButton("Build DXVK (32-bit)")
        self.build_dxvk32_btn.clicked.connect(self.build_dxvk32)
        self.init_prefix_btn = QPushButton("Init Prefix")
        self.init_prefix_btn.clicked.connect(self.init_prefix)
        self.install_steam_btn = QPushButton("Install Steam")
        self.install_steam_btn.clicked.connect(self.install_steam)

        setup_grid.addWidget(self.install_tools_btn, 0, 0)
        setup_grid.addWidget(self.install_wine_btn, 0, 1)

        setup_grid.addWidget(self.install_mesa_btn, 1, 0)
        setup_grid.addWidget(self.build_dxvk_btn, 1, 1)

        setup_grid.addWidget(self.build_dxvk32_btn, 2, 0)
        setup_grid.addWidget(self.init_prefix_btn, 2, 1)

        setup_grid.addWidget(self.install_steam_btn, 3, 0, 1, 2)
        left_layout.addWidget(setup_box)
        self._setup_box = setup_box

        runtime_box = QGroupBox("Runtime")
        runtime_grid = QGridLayout(runtime_box)

        self.launch_steam_btn = QPushButton("Launch Steam")
        self.launch_steam_btn.clicked.connect(self.launch_steam)
        self.scan_games_btn = QPushButton("Scan Games")
        self.scan_games_btn.clicked.connect(self.scan_games)
        self.patch_dxvk_btn = QPushButton("Patch Selected Game")
        self.patch_dxvk_btn.clicked.connect(self.patch_selected_game)
        self.launch_game_btn = QPushButton("Launch Selected Game")
        self.launch_game_btn.clicked.connect(self.launch_selected_game)
        self.select_startup_exe_btn = QPushButton("Select Startup EXE")
        self.select_startup_exe_btn.clicked.connect(self.select_startup_exe_for_selected_game)

        runtime_grid.addWidget(self.launch_steam_btn, 0, 0)
        runtime_grid.addWidget(self.scan_games_btn, 0, 1)
        runtime_grid.addWidget(self.patch_dxvk_btn, 1, 0)
        runtime_grid.addWidget(self.launch_game_btn, 1, 1)
        runtime_grid.addWidget(self.select_startup_exe_btn, 2, 0, 1, 2)

        self.show_dxvk_log_btn = QPushButton("Show DXVK Log")
        self.show_dxvk_log_btn.clicked.connect(self.show_dxvk_log_for_selected_game)
        runtime_grid.addWidget(self.show_dxvk_log_btn, 3, 0, 1, 2)

        self.game_args_edit = QLineEdit("")
        self.game_args_edit.setPlaceholderText(
            "Extra game args (optional). Example: -screen-fullscreen 0 -screen-width 1280 -screen-height 720"
        )
        runtime_grid.addWidget(QLabel("Game args"), 4, 0)
        runtime_grid.addWidget(self.game_args_edit, 4, 1)

        self.launch_backend_combo = QComboBox()
        for label, value in LAUNCH_BACKENDS:
            self.launch_backend_combo.addItem(label, value)
        self.launch_backend_combo.setCurrentIndex(0)
        runtime_grid.addWidget(QLabel("Launch backend"), 5, 0)
        runtime_grid.addWidget(self.launch_backend_combo, 5, 1)

        self.show_player_log_btn = QPushButton("Show Unity Player.log")
        self.show_player_log_btn.clicked.connect(self.show_unity_player_log_for_selected_game)
        runtime_grid.addWidget(self.show_player_log_btn, 6, 0, 1, 2)

        left_layout.addWidget(runtime_box)
        self._runtime_box = runtime_box

        status_box = QGroupBox("Status")
        status_layout = QVBoxLayout(status_box)
        self.status_label = QLabel("Idle")
        self.status_label.setWordWrap(True)
        status_layout.addWidget(self.status_label)
        left_layout.addWidget(status_box)
        self._status_box = status_box

        left_layout.addStretch(1)

        games_box = QGroupBox("Installed Games")
        games_layout = QVBoxLayout(games_box)
        self.games_list = QListWidget()
        self.games_list.itemSelectionChanged.connect(self.update_selected_game_status)
        games_layout.addWidget(self.games_list)
        right_layout.addWidget(games_box)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        right_layout.addWidget(self.log_view, 1)

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
        self.log_view.appendPlainText(message)

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
        if getattr(self, "simple_ui_enabled", False):
            self._setup_box.setVisible(False)
            self._quick_setup_box.setVisible(True)
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
            self._setup_box.setVisible(True)
            self._quick_setup_box.setVisible(False)
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

        self._setup_box.setVisible(True)
        self._quick_setup_box.setVisible(False)
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
        return Path(self.prefix_edit.text()).expanduser()

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
        if not ok:
            QMessageBox.warning(self, APP_NAME, message)

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

    def installer_script_path(self) -> Path:
        return Path(__file__).resolve().with_name("installer.sh")

    def run_installer_action(self, action: str) -> None:
        env = self.request_admin_env()
        if env is None:
            return
        script = self.installer_script_path()
        if not script.exists():
            QMessageBox.warning(self, APP_NAME, f"installer.sh not found at {script}")
            return
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
        self.run_installer_action("install_tools")

    def install_wine(self) -> None:
        self.run_installer_action("install_wine")

    def install_mesa(self) -> None:
        self.run_installer_action("install_mesa")

    def quick_setup(self) -> None:
        self.run_installer_action("quick_setup")

    def _build_dxvk(self, *, arch: str) -> None:
        wine = self.ensure_wine()
        if not wine:
            return
        self.run_installer_action("build_dxvk64" if arch == "win64" else "build_dxvk32")

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
      
        token = f"{game.name} {game.install_dir_name}".lower()
        if "mewgenics" in token:
            return LAUNCH_BACKEND_MESA_LLVMPIPE
        return LAUNCH_BACKEND_DXVK

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
        env = self._askpass_env(env)
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

        backend = self.selected_launch_backend()
        if backend == LAUNCH_BACKEND_AUTO:
            backend = self.auto_backend_for_game(game)

        effective_backend = backend
        effective_mesa_driver = ""

        try:
            if self.backend_is_mesa(effective_backend):
                effective_mesa_driver = self.mesa_driver_from_backend(effective_backend)
                effective_mesa_driver = self.patch_selected_game_with_mesa(game, exe, driver=effective_mesa_driver)
            elif effective_backend == LAUNCH_BACKEND_DXVK:
                self.patch_selected_game()
            else:
                pass
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return

        if self.game_process and self.game_process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.warning(self, APP_NAME, "A game process is already running.")
            return

        self.game_process = QProcess(self)
        env = self.wine_env()
        if self.backend_is_mesa(effective_backend):
            env["GALLIUM_DRIVER"] = effective_mesa_driver
            env["WINEDLLOVERRIDES"] = "opengl32=n,b"
            env["MESA_GLTHREAD"] = "true"
            env.pop("DXVK_LOG_PATH", None)
            env.pop("DXVK_LOG_LEVEL", None)
        elif effective_backend == LAUNCH_BACKEND_DXVK:
            env["WINEDLLOVERRIDES"] = "dxgi,d3d11,d3d10core=n,b"
            env["DXVK_LOG_PATH"] = str(Path.home() / "dxvk-logs")
            env["DXVK_LOG_LEVEL"] = "info"
            Path(env["DXVK_LOG_PATH"]).mkdir(parents=True, exist_ok=True)
        else:
            env["WINEDLLOVERRIDES"] = "dxgi,d3d11,d3d10core=b"
            env.pop("DXVK_LOG_PATH", None)
            env.pop("DXVK_LOG_LEVEL", None)

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
    win = MainWindow()
    win.show()
    win.apply_ui_modes()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
