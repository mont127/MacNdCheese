#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from PyQt6.QtCore import QObject, QProcess, QThread, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
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
DEFAULT_PREFIX = str(Path.home() / "wined")
DEFAULT_DXVK_SRC = str(Path.home() / "DXVK-macOS")
DEFAULT_DXVK_INSTALL = str(Path.home() / "dxvk-release")
DEFAULT_STEAM_SETUP = str(Path.home() / "Downloads" / "SteamSetup.exe")
DXVK_DLLS = ("dxgi.dll", "d3d11.dll", "d3d10core.dll")


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

        # 0) Unreal Engine fast-path: prefer the real Shipping binary if present
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

        # 1) Try obvious names in the root folder
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

        # 2) Scan root folder for EXEs, largest first
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

        # 3) Some games store the main EXE in a subfolder. Search a little deeper.
        # Keep it reasonably shallow to avoid heavy scans.
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

        # Prefer Unreal Engine shipping binaries if present
        # Typical: <Game>/WindowsNoEditor/<Game>/Binaries/Win64/*-Win64-Shipping.exe
        shipping = [p for p in sub_exes if "shipping.exe" in p.name.lower()]
        shipping.sort(key=lambda p: p.stat().st_size if p.exists() else 0, reverse=True)
        if shipping:
            candidates.extend(shipping)

        sub_exes.sort(key=lambda p: p.stat().st_size, reverse=True)
        candidates.extend(sub_exes)

        # 4) If we found anything, pick the largest plausible EXE
        for exe in candidates:
            try:
                if exe.exists() and exe.is_file():
                    return exe
            except Exception:
                continue

        return None

    def display(self) -> str:
        return f"{self.name} [{self.appid}]"


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

        self._build_ui()
        self._build_menu()
        self.log(f"{APP_NAME} ready")

    def _build_menu(self) -> None:
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
        self.steam_setup_edit = QLineEdit(DEFAULT_STEAM_SETUP)

        browse_prefix = QPushButton("Browse")
        browse_prefix.clicked.connect(lambda: self._pick_dir(self.prefix_edit))
        browse_dxvk_src = QPushButton("Browse")
        browse_dxvk_src.clicked.connect(lambda: self._pick_dir(self.dxvk_src_edit))
        browse_dxvk_install = QPushButton("Browse")
        browse_dxvk_install.clicked.connect(lambda: self._pick_dir(self.dxvk_install_edit))
        browse_steam_setup = QPushButton("Browse")
        browse_steam_setup.clicked.connect(lambda: self._pick_file(self.steam_setup_edit))

        paths_form.addRow("Wine prefix", self._with_button(self.prefix_edit, browse_prefix))
        paths_form.addRow("DXVK source", self._with_button(self.dxvk_src_edit, browse_dxvk_src))
        paths_form.addRow("DXVK install", self._with_button(self.dxvk_install_edit, browse_dxvk_install))
        paths_form.addRow("SteamSetup.exe", self._with_button(self.steam_setup_edit, browse_steam_setup))

        left_layout.addWidget(paths_box)

        setup_box = QGroupBox("Setup")
        setup_grid = QGridLayout(setup_box)

        self.install_tools_btn = QPushButton("Install Tools")
        self.install_tools_btn.clicked.connect(self.install_tools)
        self.build_dxvk_btn = QPushButton("Build DXVK")
        self.build_dxvk_btn.clicked.connect(self.build_dxvk)
        self.init_prefix_btn = QPushButton("Init Prefix")
        self.init_prefix_btn.clicked.connect(self.init_prefix)
        self.install_steam_btn = QPushButton("Install Steam")
        self.install_steam_btn.clicked.connect(self.install_steam)

        setup_grid.addWidget(self.install_tools_btn, 0, 0)
        setup_grid.addWidget(self.build_dxvk_btn, 0, 1)
        setup_grid.addWidget(self.init_prefix_btn, 1, 0)
        setup_grid.addWidget(self.install_steam_btn, 1, 1)
        left_layout.addWidget(setup_box)

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

        runtime_grid.addWidget(self.launch_steam_btn, 0, 0)
        runtime_grid.addWidget(self.scan_games_btn, 0, 1)
        runtime_grid.addWidget(self.patch_dxvk_btn, 1, 0)
        runtime_grid.addWidget(self.launch_game_btn, 1, 1)

        self.show_dxvk_log_btn = QPushButton("Show DXVK Log")
        self.show_dxvk_log_btn.clicked.connect(self.show_dxvk_log_for_selected_game)
        runtime_grid.addWidget(self.show_dxvk_log_btn, 2, 0, 1, 2)

        self.game_args_edit = QLineEdit("")
        self.game_args_edit.setPlaceholderText("Extra game args (optional). Example: -screen-fullscreen 0 -screen-width 1280 -screen-height 720")
        runtime_grid.addWidget(QLabel("Game args"), 3, 0)
        runtime_grid.addWidget(self.game_args_edit, 3, 1)

        self.show_player_log_btn = QPushButton("Show Unity Player.log")
        self.show_player_log_btn.clicked.connect(self.show_unity_player_log_for_selected_game)
        runtime_grid.addWidget(self.show_player_log_btn, 4, 0, 1, 2)

        left_layout.addWidget(runtime_box)

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
    def steam_setup(self) -> Path:
        return Path(self.steam_setup_edit.text()).expanduser()

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
        # If an old QThread wrapper exists but its underlying C++ object was deleted, reset it.
        if self.worker_thread is not None:
            try:
                if self.worker_thread.isRunning():
                    QMessageBox.warning(self, APP_NAME, "Another setup task is already running.")
                    return
            except RuntimeError:
                self.worker_thread = None
                self.worker = None

        self.set_status("Task running")

        # Parent the thread to the window to avoid premature deletion/GC.
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
            QMessageBox.warning(self, APP_NAME, str(exc))
            return None

    def install_tools(self) -> None:
        self.run_commands(
            [["bash", "-lc", "brew install git meson ninja mingw-w64 glslang p7zip winetricks"]]
        )

    def build_dxvk(self) -> None:
        wine = self.ensure_wine()
        if not wine:
            return
        src = self.dxvk_src
        install = self.dxvk_install
        if not (src / "build-win64.txt").exists():
            QMessageBox.warning(self, APP_NAME, f"DXVK source not found at {src}")
            return
        install.mkdir(parents=True, exist_ok=True)
        build_dir = install / "build.64"
        coredata = build_dir / 'meson-private' / 'coredata.dat'
        meson_args = [
            "meson",
            "setup",
            str(build_dir),
            str(src),
            "--cross-file",
            str(src / "build-win64.txt"),
            "--prefix",
            str(install),
            "--buildtype",
            "release",
            "-Denable_d3d9=false",
        ]
        # Determine which meson argument to use for reconfigure/wipe
        if build_dir.exists():
            if coredata.exists():
                meson_args.append("--reconfigure")
            else:
                meson_args.append("--wipe")
        # else: neither --reconfigure nor --wipe
        commands = [
            meson_args,
            ["ninja", "-C", str(build_dir)],
            ["ninja", "-C", str(build_dir), "install"],
        ]
        self.log(f"Building DXVK in: {build_dir}")
        self.run_commands(commands, cwd=str(src))

    def init_prefix(self) -> None:
        wine = self.ensure_wine()
        if not wine:
            return
        self.prefix_path.mkdir(parents=True, exist_ok=True)
        self.run_commands([[wine, "wineboot"]], env=self.wine_env())

    def install_steam(self) -> None:
        wine = self.ensure_wine()
        if not wine:
            return
        if not self.steam_setup.exists():
            QMessageBox.warning(self, APP_NAME, f"SteamSetup.exe not found at {self.steam_setup}")
            return
        self.run_commands([[wine, str(self.steam_setup)]], env=self.wine_env())

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
        qenv = self.steam_process.processEnvironment()
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
        # Heuristic: Unity games usually have <GameName>_Data folder
        data_dir = game.game_dir / f"{game.install_dir_name}_Data"
        if data_dir.exists():
            return True
        # Fallback: any *_Data directory next to the exe
        if any(p.is_dir() and p.name.lower().endswith("_data") for p in game.game_dir.iterdir() if game.game_dir.exists()):
            return True
        return False

    def _unity_player_log_candidates(self) -> list[Path]:
        # Unity Player.log location inside prefix is typically under AppData/LocalLow/**/Player.log
        base = self.prefix_path / "drive_c" / "users"
        if not base.exists():
            return []
        return list(base.glob("*/AppData/LocalLow/*/*/Player.log")) + list(base.glob("*/AppData/LocalLow/*/Player.log"))

    def latest_unity_player_log_for_game(self, game: GameEntry) -> Optional[Path]:
        candidates = self._unity_player_log_candidates()
        if not candidates:
            return None

        # Prefer logs whose path contains the game name or install dir
        needle1 = game.name.lower()
        needle2 = game.install_dir_name.lower()
        preferred = [p for p in candidates if needle1 in str(p).lower() or needle2 in str(p).lower()]
        pool = preferred if preferred else candidates

        # Pick newest by mtime
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
        """Return the newest DXVK D3D11 log for this game.

        DXVK log filenames vary; we try strong name matches first, then fall back.
        If we have a recorded launch timestamp for this appid, prefer logs written after launch.
        """
        logs_dir = Path.home() / "dxvk-logs"
        if not logs_dir.exists():
            return None

        # Common DXVK naming patterns
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

        # Final fallback: any d3d11 log
        if not candidates:
            candidates = list(logs_dir.glob("*_d3d11.log"))

        # De-dup
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

    def update_selected_game_status(self) -> None:
        game = self.selected_game()
        if not game:
            return
        exe = game.detect_exe()
        self.set_status(
            f"Selected: {game.name} | Folder: {game.game_dir} | EXE: {exe.name if exe else 'not found'}"
        )

    def patch_selected_game(self) -> None:
        game = self.selected_game()
        if not game:
            QMessageBox.warning(self, APP_NAME, "Select a game first.")
            return

        dxvk_bin = self.dxvk_install / "bin"
        for dll in DXVK_DLLS:
            if not (dxvk_bin / dll).exists():
                QMessageBox.warning(self, APP_NAME, f"Missing {dll} in {dxvk_bin}. Build DXVK first.")
                return

        game.game_dir.mkdir(parents=True, exist_ok=True)

        # Copy DXVK DLLs to the game root folder
        for dll in DXVK_DLLS:
            shutil.copy2(dxvk_bin / dll, game.game_dir / dll)
            self.log(f"Copied {dll} -> {game.game_dir}")

        # Some games place the main EXE in a subfolder (e.g. Binaries/Win64).
        # In that case Wine is typically launched from that folder, so copy DLLs there too.
        exe = game.detect_exe()
        if exe is not None:
            exe_dir = exe.parent
            if exe_dir.exists() and exe_dir != game.game_dir:
                for dll in DXVK_DLLS:
                    shutil.copy2(dxvk_bin / dll, exe_dir / dll)
                    self.log(f"Copied {dll} -> {exe_dir}")

        # Unreal Engine games often launch a Shipping binary under */Binaries/Win64.
        # Even if we launch a wrapper EXE, the real process may run from that folder,
        # so ensure DLLs are present there too.
        try:
            win64_dirs = set()
            for p in game.game_dir.glob("**/Binaries/Win64"):
                if p.is_dir():
                    win64_dirs.add(p)
            for p in game.game_dir.glob("WindowsNoEditor/**/Binaries/Win64"):
                if p.is_dir():
                    win64_dirs.add(p)

            for win64_dir in sorted(win64_dirs):
                for dll in DXVK_DLLS:
                    shutil.copy2(dxvk_bin / dll, win64_dir / dll)
                self.log(f"Copied {', '.join(DXVK_DLLS)} -> {win64_dir}")
        except Exception:
            pass

        self.set_status(f"Patched {game.name} with local DXVK")

    def launch_selected_game(self) -> None:
        game = self.selected_game()
        if not game:
            QMessageBox.warning(self, APP_NAME, "Select a game first.")
            return
        wine = self.ensure_wine()
        if not wine:
            return
        exe = game.detect_exe()
        if not exe:
            # Give the user a helpful hint and list some EXE candidates for debugging.
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
        # If an Unreal Engine Shipping EXE exists, prefer launching it directly.
        # This avoids wrapper launchers that then spawn the real binary from a deeper folder.
        try:
            shipping_exes = sorted(
                game.game_dir.glob("**/*Shipping.exe"),
                key=lambda p: p.stat().st_size if p.exists() else 0,
                reverse=True,
            )
            if shipping_exes:
                exe = shipping_exes[0]
        except Exception:
            pass
        if not self.steam_process or self.steam_process.state() == QProcess.ProcessState.NotRunning:
            QMessageBox.warning(self, APP_NAME, "Steam must be running first.")
            return

        # Patch after we have the final EXE choice so DLLs land in the right folders.
        self.patch_selected_game()

        if self.game_process and self.game_process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.warning(self, APP_NAME, "A game process is already running.")
            return

        self.game_process = QProcess(self)
        env = self.wine_env()
        env["WINEDLLOVERRIDES"] = "dxgi,d3d11,d3d10core=n,b"
        env["DXVK_LOG_PATH"] = str(Path.home() / "dxvk-logs")
        env["DXVK_LOG_LEVEL"] = "info"
        Path(env["DXVK_LOG_PATH"]).mkdir(parents=True, exist_ok=True)

        qenv = self.game_process.processEnvironment()
        for key, value in env.items():
            qenv.insert(key, value)
        self.game_process.setProcessEnvironment(qenv)
        # If the main EXE is in a subfolder, run Wine from that folder.
        exe_dir = exe.parent
        self.game_process.setWorkingDirectory(str(exe_dir))

        # Build arguments
        # Use the basename because we set the working directory to exe.parent above.
        args = [exe.name]

        # Extra args from UI
        extra = ""
        if hasattr(self, "game_args_edit"):
            extra = self.game_args_edit.text().strip()
        if extra:
            args += extra.split()

        # For Unity games, force a host-side log file so users can always fetch it
        if self.is_unity_game(game):
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", game.install_dir_name or game.name)
            unity_log = str(Path.home() / f"{safe_name}-player.log")
            args += ["-logFile", unity_log]
            self.log(f"Unity log file will be written to: {unity_log}")

        # Also tee Wine output to a host-side file via shell redirection
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", game.install_dir_name or game.name)
        host_wine_log = str(Path.home() / f"{safe_name}-wine.log")
        self.log(f"Wine output will be written to: {host_wine_log}")
        self.last_game_launch_ts[game.appid] = time.time()
        self.last_game_wine_log[game.appid] = Path(host_wine_log)

        # Run via bash so we can redirect stdout/stderr to a file
        cmd = f"cd {shlex.quote(str(exe_dir))} && {shlex.quote(wine)} { ' '.join(shlex.quote(a) for a in args) } > {shlex.quote(host_wine_log)} 2>&1"
        self.game_process.setProgram("bash")
        self.game_process.setArguments(["-lc", cmd])
        self.game_process.readyReadStandardOutput.connect(lambda: self._drain_process(self.game_process))
        self.game_process.readyReadStandardError.connect(lambda: self._drain_process(self.game_process))
        self.game_process.started.connect(lambda: self.set_status(f"Started {game.name}"))
        self.game_process.errorOccurred.connect(lambda e: self.set_status(f"Game error: {e}"))

        def _on_game_finished(code, status) -> None:
            self.set_status(f"{game.name} exited with code {code}")

            # Show the newest DXVK log related to this game launch
            self.show_dxvk_log_for_selected_game()

            # Always show last lines of the host-side wine log for this game
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

            # Unity log (if applicable)
            if self.is_unity_game(game):
                self.show_unity_player_log_for_selected_game()

        self.game_process.finished.connect(_on_game_finished)
        self.game_process.start()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        for proc in (self.game_process, self.steam_process):
            if proc and proc.state() != QProcess.ProcessState.NotRunning:
                proc.kill()
                proc.waitForFinished(2000)
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
