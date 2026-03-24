from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import config
from constants import (
    DEFAULT_DXVK_INSTALL,
    DEFAULT_DXVK_INSTALL32,
    DEFAULT_MESA_DIR,
    DEFAULT_PREFIX,
    DEFAULT_STEAM_SETUP,
)


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(680, 520)
        self._build_ui()
        self.load_config()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        self._tabs.addTab(self._build_paths_tab(), "Paths")
        self._tabs.addTab(self._build_setup_tab(), "Setup")
        self._tabs.addTab(self._build_logs_tab(), "Logs")

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.save_config)
        close_btn.clicked.connect(self.hide)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _build_paths_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self.prefix_edit = QLineEdit(DEFAULT_PREFIX)
        self.dxvk_install_edit = QLineEdit(DEFAULT_DXVK_INSTALL)
        self.dxvk_install32_edit = QLineEdit(DEFAULT_DXVK_INSTALL32)
        self.steam_setup_edit = QLineEdit(DEFAULT_STEAM_SETUP)
        self.mesa_dir_edit = QLineEdit(DEFAULT_MESA_DIR)

        form.addRow("Wine prefix", self._browsable(self.prefix_edit, dir=True))
        form.addRow("DXVK install (64-bit)", self._browsable(self.dxvk_install_edit, dir=True))
        form.addRow("DXVK install (32-bit)", self._browsable(self.dxvk_install32_edit, dir=True))
        form.addRow("SteamSetup.exe", self._browsable(self.steam_setup_edit, dir=False))
        form.addRow("Mesa x64 dir", self._browsable(self.mesa_dir_edit, dir=True))

        return widget

    def _build_setup_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        quick_box = QGroupBox("One-Click")
        quick_layout = QVBoxLayout(quick_box)
        self.quick_setup_btn = QPushButton("One Click Setup")
        hint = QLabel("Installs Wine, downloads prebuilt DXVK, then installs Mesa.")
        hint.setWordWrap(True)
        quick_layout.addWidget(self.quick_setup_btn)
        quick_layout.addWidget(hint)
        layout.addWidget(quick_box)

        steps_box = QGroupBox("Individual Steps")
        grid = QGridLayout(steps_box)

        self.install_tools_btn = QPushButton("Install Tools")
        self.install_wine_btn = QPushButton("Install Wine")
        self.install_dxvk_btn = QPushButton("Install DXVK")
        self.install_mesa_btn = QPushButton("Install Mesa")
        self.init_prefix_btn = QPushButton("Init Prefix")
        self.install_steam_btn = QPushButton("Install Steam")

        grid.addWidget(self.install_tools_btn, 0, 0)
        grid.addWidget(self.install_wine_btn, 0, 1)
        grid.addWidget(self.install_dxvk_btn, 1, 0)
        grid.addWidget(self.install_mesa_btn, 1, 1)
        grid.addWidget(self.init_prefix_btn, 2, 0)
        grid.addWidget(self.install_steam_btn, 2, 1)

        layout.addWidget(steps_box)
        layout.addStretch()

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

    def load_config(self) -> None:
        cfg = config.load()
        self.prefix_edit.setText(cfg["prefix"])
        self.dxvk_install_edit.setText(cfg["dxvk_install"])
        self.dxvk_install32_edit.setText(cfg["dxvk_install32"])
        self.steam_setup_edit.setText(cfg["steam_setup"])
        self.mesa_dir_edit.setText(cfg["mesa_dir"])

    def save_config(self) -> None:
        config.save({
            "prefix": self.prefix_edit.text(),
            "dxvk_install": self.dxvk_install_edit.text(),
            "dxvk_install32": self.dxvk_install32_edit.text(),
            "steam_setup": self.steam_setup_edit.text(),
            "mesa_dir": self.mesa_dir_edit.text(),
        })

    def log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
