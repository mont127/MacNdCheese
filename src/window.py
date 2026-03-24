from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QProcess, QThread
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from constants import APP_NAME, LAUNCH_BACKENDS
from models import GameEntry
from workers import CommandWorker
from ops.installer import InstallerOps
from ops.runtime import RuntimeOps
from ui.settings import SettingsDialog


class MainWindow(InstallerOps, RuntimeOps, QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1000, 660)

        self.worker_thread: Optional[QThread] = None
        self.worker: Optional[CommandWorker] = None
        self._askpass_path: Optional[str] = None
        self.steam_process: Optional[QProcess] = None
        self.game_process: Optional[QProcess] = None
        self.games: list[GameEntry] = []
        self.last_game_launch_ts: dict[str, float] = {}
        self.last_game_wine_log: dict[str, Path] = {}

        self.settings = SettingsDialog(self)
        self._wire_settings()
        self._build_ui()
        self._build_menu()
        self.log(f"{APP_NAME} ready")

    def _wire_settings(self) -> None:
        s = self.settings
        s.install_tools_btn.clicked.connect(self.install_tools)
        s.install_wine_btn.clicked.connect(self.install_wine)
        s.install_dxvk_btn.clicked.connect(self.install_dxvk)
        s.install_mesa_btn.clicked.connect(self.install_mesa)
        s.init_prefix_btn.clicked.connect(self.init_prefix)
        s.install_steam_btn.clicked.connect(self.install_steam)
        s.quick_setup_btn.clicked.connect(self.quick_setup)

    def _build_menu(self) -> None:
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

        splitter = QSplitter()
        root_layout.addWidget(splitter)

        # ── Left panel ──────────────────────────────────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)
        splitter.addWidget(left)

        steam_box = QGroupBox("Steam")
        steam_row = QHBoxLayout(steam_box)
        self.launch_steam_btn = QPushButton("Launch Steam")
        self.launch_steam_btn.clicked.connect(self.launch_steam)
        self.scan_games_btn = QPushButton("Scan Games")
        self.scan_games_btn.clicked.connect(self.scan_games)
        steam_row.addWidget(self.launch_steam_btn)
        steam_row.addWidget(self.scan_games_btn)
        left_layout.addWidget(steam_box)

        game_box = QGroupBox("Game")
        game_layout = QVBoxLayout(game_box)

        action_row = QHBoxLayout()
        self.patch_dxvk_btn = QPushButton("Patch Selected")
        self.patch_dxvk_btn.clicked.connect(self.patch_selected_game)
        self.launch_game_btn = QPushButton("Launch Selected")
        self.launch_game_btn.clicked.connect(self.launch_selected_game)
        action_row.addWidget(self.patch_dxvk_btn)
        action_row.addWidget(self.launch_game_btn)
        game_layout.addLayout(action_row)

        log_row = QHBoxLayout()
        self.show_dxvk_log_btn = QPushButton("DXVK Log")
        self.show_dxvk_log_btn.clicked.connect(self.show_dxvk_log_for_selected_game)
        self.show_player_log_btn = QPushButton("Unity Log")
        self.show_player_log_btn.clicked.connect(self.show_unity_player_log_for_selected_game)
        log_row.addWidget(self.show_dxvk_log_btn)
        log_row.addWidget(self.show_player_log_btn)
        game_layout.addLayout(log_row)

        backend_row = QHBoxLayout()
        backend_row.addWidget(QLabel("Backend"))
        self.launch_backend_combo = QComboBox()
        for label, value in LAUNCH_BACKENDS:
            self.launch_backend_combo.addItem(label, value)
        self.launch_backend_combo.setCurrentIndex(0)
        backend_row.addWidget(self.launch_backend_combo, 1)
        game_layout.addLayout(backend_row)

        self.game_args_edit = QLineEdit()
        self.game_args_edit.setPlaceholderText("Extra game args (optional)")
        game_layout.addWidget(self.game_args_edit)

        left_layout.addWidget(game_box)

        self.status_label = QLabel("Idle")
        self.status_label.setWordWrap(True)
        left_layout.addWidget(self.status_label)

        settings_btn = QPushButton("Settings...")
        settings_btn.clicked.connect(self.settings.show)
        left_layout.addWidget(settings_btn)

        left_layout.addStretch()

        # ── Right panel: game list ───────────────────────────────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 8, 8, 8)
        splitter.addWidget(right)
        splitter.setSizes([320, 680])

        games_box = QGroupBox("Installed Games")
        games_layout = QVBoxLayout(games_box)
        self.games_list = QListWidget()
        self.games_list.itemSelectionChanged.connect(self.update_selected_game_status)
        games_layout.addWidget(self.games_list)
        right_layout.addWidget(games_box)

    # ── Core utilities ───────────────────────────────────────────────────────

    def log(self, message: str) -> None:
        self.settings.log(message)

    def append_log(self, message: str) -> None:
        self.log(message)

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)
        self.log(message)

    @property
    def prefix_path(self) -> Path:
        return Path(self.settings.prefix_edit.text()).expanduser()

    @property
    def steam_dir(self) -> Path:
        return self.prefix_path / "drive_c" / "Program Files (x86)" / "Steam"

    @property
    def dxvk_install(self) -> Path:
        return Path(self.settings.dxvk_install_edit.text()).expanduser()

    @property
    def dxvk_install32(self) -> Path:
        return Path(self.settings.dxvk_install32_edit.text()).expanduser()

    @property
    def steam_setup(self) -> Path:
        return Path(self.settings.steam_setup_edit.text()).expanduser()

    @property
    def mesa_dir(self) -> Path:
        return Path(self.settings.mesa_dir_edit.text()).expanduser()

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
        if self._askpass_path:
            try:
                os.unlink(self._askpass_path)
            except Exception:
                pass
            self._askpass_path = None
        self.set_status(message if ok else f"Failed: {message}")
        if not ok:
            QMessageBox.warning(self, APP_NAME, message)

    def closeEvent(self, event) -> None:
        for proc in (self.game_process, self.steam_process):
            if proc and proc.state() != QProcess.ProcessState.NotRunning:
                proc.kill()
                proc.waitForFinished(2000)
        super().closeEvent(event)
