#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
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

        exact = self.game_dir / f"{self.install_dir_name}.exe"
        if exact.exists():
            return exact

        simplified = self.game_dir / f"{self.name}.exe"
        if simplified.exists():
            return simplified

        exes = sorted(self.game_dir.glob("*.exe"), key=lambda p: p.stat().st_size, reverse=True)
        for exe in exes:
            lowered = exe.name.lower()
            if "unitycrashhandler" in lowered:
                continue
            return exe
        return None

    def display(self) -> str:
        return f"{self.name} [{self.appid}]"


class CommandWorker(QObject):
    output = pyqtSignal(str)
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

    def run_commands(self, commands: list[list[str]], *, env: dict[str, str] | None = None, cwd: str | None = None) -> None:
        if self.worker_thread and self.worker_thread.isRunning():
            QMessageBox.warning(self, APP_NAME, "Another setup task is already running.")
            return

        self.worker_thread = QThread()
        self.worker = CommandWorker(commands, env=env, cwd=cwd)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.output.connect(self.log)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.start()
        self.set_status("Task running")

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
        commands = [
            [
                "meson",
                "setup",
                str(build_dir),
                "--cross-file",
                str(src / "build-win64.txt"),
                "--prefix",
                str(install),
                "--buildtype",
                "release",
                "-Denable_d3d9=false",
                "--reconfigure",
            ],
            ["ninja", "-C", str(build_dir)],
            ["ninja", "-C", str(build_dir), "install"],
        ]
        self.run_commands(commands)

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
        for dll in DXVK_DLLS:
            shutil.copy2(dxvk_bin / dll, game.game_dir / dll)
            self.log(f"Copied {dll} -> {game.game_dir}")
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
            QMessageBox.warning(self, APP_NAME, f"Could not detect an executable inside {game.game_dir}")
            return
        if not self.steam_process or self.steam_process.state() == QProcess.ProcessState.NotRunning:
            QMessageBox.warning(self, APP_NAME, "Steam must be running first.")
            return

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
        self.game_process.setWorkingDirectory(str(game.game_dir))
        self.game_process.setProgram(wine)
        self.game_process.setArguments([exe.name])
        self.game_process.readyReadStandardOutput.connect(lambda: self._drain_process(self.game_process))
        self.game_process.readyReadStandardError.connect(lambda: self._drain_process(self.game_process))
        self.game_process.started.connect(lambda: self.set_status(f"Started {game.name}"))
        self.game_process.errorOccurred.connect(lambda e: self.set_status(f"Game error: {e}"))
        self.game_process.finished.connect(
            lambda code, status: self.set_status(f"{game.name} exited with code {code}")
        )
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
