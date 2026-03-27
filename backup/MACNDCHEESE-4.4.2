#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import ssl
import subprocess
import sys
import time
import urllib.request
import webbrowser
import getpass
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Any
import AVFoundation as AV


from PyQt6.QtGui import QAction, QPixmap, QPainter, QIcon
from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QThread, pyqtSignal, QPoint, QRect, QSize, Qt, QEvent, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
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
    QProgressBar,
    QSplitter,
    QStyle,
    QVBoxLayout,
    QWidget,
    QDialog,
    QTabWidget,
    QFrame,
    QStackedWidget,
    QScrollArea,
    QMenu,
    QWidgetAction,
    QLayout,
    QSizePolicy,
    QButtonGroup,
)

class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=-1, hSpacing=-1, vSpacing=-1):
        super().__init__(parent)
        self._item_list = []
        self._h_space = hSpacing
        self._v_space = vSpacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._item_list.append(item)

    def horizontalSpacing(self):
        if self._h_space >= 0:
            return self._h_space
        return 0

    def verticalSpacing(self):
        if self._v_space >= 0:
            return self._v_space
        return 0

    def count(self):
        return len(self._item_list)

    def itemAt(self, index):
        if 0 <= index < len(self._item_list):
            return self._item_list[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._item_list):
            return self._item_list.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._item_list:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect, test_only):
        x = rect.x()
        y = rect.y()
        line_height = 0

        for item in self._item_list:
            space_x = self.horizontalSpacing()
            space_y = self.verticalSpacing()
            next_x = x + item.sizeHint().width() + space_x
            if next_x - space_x > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x = next_x
            line_height = max(line_height, item.sizeHint().height())

        return y + line_height - rect.y()


                      
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
        self.dxmt_dir_edit = QLineEdit(DEFAULT_DXMT_DIR)
        self.vkd3d_dir_edit = QLineEdit(DEFAULT_VKD3D_DIR)
        self.gptk_dir_edit = QLineEdit(DEFAULT_GPTK_DIR)

        form.addRow("Wine prefix", self._build_prefix_row(self.prefix_combo))
        form.addRow("DXVK source", self._browsable(self.dxvk_src_edit, dir=True))
        form.addRow("DXVK install (64-bit)", self._browsable(self.dxvk_install_edit, dir=True))
        form.addRow("DXVK install (32-bit)", self._browsable(self.dxvk_install32_edit, dir=True))
        form.addRow("SteamSetup.exe", self._browsable(self.steam_setup_edit, dir=False))
        form.addRow("Mesa x64 dir", self._browsable(self.mesa_dir_edit, dir=True))
        form.addRow("DXMT dir", self._browsable(self.dxmt_dir_edit, dir=True))
        form.addRow("VKD3D-Proton dir", self._browsable(self.vkd3d_dir_edit, dir=True))
        form.addRow("GPTK dir", self._browsable(self.gptk_dir_edit, dir=True))


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

        btn_remove = QPushButton("Remove from List")
        btn_remove.clicked.connect(self._remove_prefix)
        row.addWidget(btn_remove)

        btn_delete = QPushButton("Delete Disk")
        btn_delete.setStyleSheet("color: #FF6666;")
        btn_delete.clicked.connect(self._delete_prefix_disk)
        row.addWidget(btn_delete)

        btn_browse = QPushButton("Browse")
        btn_browse.clicked.connect(self._pick_prefix_dir)
        row.addWidget(btn_browse)

        return wrap

    def _remove_prefix(self) -> None:
        path_str = self.prefix_combo.currentText()
        idx = self.prefix_combo.currentIndex()
        if idx >= 0:
            self.prefix_combo.removeItem(idx)
        self._save_current_prefixes()
        
        parent = self.parent()
        if parent and hasattr(parent, "remove_sidebar_button_for_prefix"):
            parent.remove_sidebar_button_for_prefix(path_str)

    def _delete_prefix_disk(self) -> None:
        path_str = self.prefix_combo.currentText()
        if not path_str:
            return
        p = Path(path_str)
        if not p.exists():
            QMessageBox.warning(self, "Delete Prefix", f"Prefix does not exist on disk:\n{p}")
            self._remove_prefix()
            return
            
        reply = QMessageBox.question(
            self,
            "Delete Prefix",
            f"Are you sure you want to PERMANENTLY delete this prefix and all of its contents (games, saves, etc)?\n\n{p}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                import shutil
                shutil.rmtree(p)
                QMessageBox.information(self, "Deleted", "Prefix deleted successfully.")
                self._remove_prefix()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete prefix:\n{e}")

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
        self.build_dxvk_btn = QPushButton("Install DXVK")
        self.build_dxvk32_btn = QPushButton("Install DXVK")  # kept for compatibility
        self.init_prefix_btn = QPushButton("Init Prefix")
        self.install_steam_btn = QPushButton("Install Steam")
        hint = QLabel("Installs Wine, downloads prebuilt DXVK, then installs Mesa.")
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
        grid.addWidget(self.init_prefix_btn, 2, 0)
        grid.addWidget(self.install_steam_btn, 2, 1)
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
        if hasattr(parent, "dxmt_dir_edit"):
            self.dxmt_dir_edit.setText(parent.dxmt_dir_edit.text())
        if hasattr(parent, "vkd3d_dir_edit"):
            self.vkd3d_dir_edit.setText(parent.vkd3d_dir_edit.text())
        if hasattr(parent, "gptk_dir_edit"):
            self.gptk_dir_edit.setText(parent.gptk_dir_edit.text())


            
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
        if hasattr(parent, "dxmt_dir_edit"):
            parent.dxmt_dir_edit.setText(self.dxmt_dir_edit.text())
        if hasattr(parent, "vkd3d_dir_edit"):
            parent.vkd3d_dir_edit.setText(self.vkd3d_dir_edit.text())
        if hasattr(parent, "gptk_dir_edit"):
            parent.gptk_dir_edit.setText(self.gptk_dir_edit.text())

    def log(self, message: str) -> None:
        self.log_view.appendPlainText(message)


class _AdminPasswordDialog(QDialog):
    """Apple HIG-style admin password sheet."""

    def __init__(self, message: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("MacNCheese Setup")
        self.setFixedWidth(380)
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(12)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation).pixmap(40, 40)
        )
        icon_lbl.setFixedSize(40, 40)
        header.addWidget(icon_lbl)
        title_lbl = QLabel("<b>Administrator Password Required</b>")
        title_lbl.setWordWrap(True)
        header.addWidget(title_lbl, 1)
        layout.addLayout(header)

        msg_lbl = QLabel(message)
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet("font-size: 12px;")
        layout.addWidget(msg_lbl)

        self._pwd_field = QLineEdit()
        self._pwd_field.setEchoMode(QLineEdit.EchoMode.Password)
        self._pwd_field.setPlaceholderText("Password")
        self._pwd_field.returnPressed.connect(self.accept)
        layout.addWidget(self._pwd_field)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def password(self) -> str:
        return self._pwd_field.text()


class _InstallProgressDialog(QDialog):
    """Non-terminal install progress dialog with indeterminate progress bar."""
    cancel_requested = pyqtSignal()

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setFixedWidth(420)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)

        self._title_lbl = QLabel(f"<b>{title}</b>")
        self._title_lbl.setStyleSheet("font-size: 14px;")
        layout.addWidget(self._title_lbl)

        self._step_lbl = QLabel("Starting…")
        self._step_lbl.setWordWrap(True)
        self._step_lbl.setStyleSheet("font-size: 12px;")
        layout.addWidget(self._step_lbl)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)  # pulsing indeterminate
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(14)
        self._bar.setStyleSheet(
            "QProgressBar { border-radius: 7px; background: rgba(255,255,255,0.15); }"
            "QProgressBar::chunk { border-radius: 7px; background: qlineargradient("
            "  x1:0, y1:0, x2:1, y2:0, stop:0 #6C8EFF, stop:1 #A855F7); }"
        )
        layout.addWidget(self._bar)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.cancel_requested)
        btn_row.addWidget(self._cancel_btn)
        layout.addLayout(btn_row)

        self._done = False

    def update_step(self, text: str) -> None:
        if not self._done:
            # show only last non-empty line as current step
            last = next((l for l in reversed(text.splitlines()) if l.strip()), text.strip())
            if last:
                self._step_lbl.setText(last)

    def mark_done(self, ok: bool, message: str) -> None:
        self._done = True
        self._bar.setRange(0, 1)
        self._bar.setValue(1)
        self._step_lbl.setText(message)
        self._cancel_btn.setText("Close")
        self._cancel_btn.clicked.disconnect()
        self._cancel_btn.clicked.connect(self.accept)


MODERN_THEME = """
QWidget {
    color: #FFFFFF;
    font-family: "Inter", "Segoe UI", Arial, sans-serif;
    font-size: 13px;
}

QMainWindow, QDialog {
    background-color: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 1,
                                      stop: 0 #1A1F2C, stop: 1 #0D0F16);
}

#Sidebar {
    background-color: rgba(255, 255, 255, 0.05);
    border-right: 1px solid rgba(255, 255, 255, 0.1);
}

QLabel {
    background-color: transparent;
}

#SidebarButton {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 12px;
    padding: 6px 4px 4px 4px;
    margin: 2px 6px;
    color: rgba(255, 255, 255, 0.6);
    font-size: 10px;
}
#SidebarButton:hover {
    background-color: rgba(255, 255, 255, 0.1);
    border: 1px solid rgba(255, 255, 255, 0.2);
    color: #FFFFFF;
}
#SidebarButton:checked {
    background-color: rgba(0, 216, 214, 0.15);
    border: 1px solid rgba(0, 216, 214, 0.5);
    color: #00D8D6;
}

#AddContainerButton {
    background-color: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 22px;
    color: rgba(255, 255, 255, 0.8);
    font-size: 22px;
    font-weight: bold;
    padding: 0px;
    margin: 4px 8px;
}
#AddContainerButton:hover {
    background-color: rgba(0, 216, 214, 0.15);
    border: 1px solid rgba(0, 216, 214, 0.6);
    color: #00D8D6;
}
#AddContainerButton::menu-indicator {
    image: none;
}

#Topbar {
    background-color: rgba(255, 255, 255, 0.03);
    border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

#LogoText {
    color: #00D8D6;
    font-size: 18px;
    font-weight: bold;
    letter-spacing: 1px;
}

#LogoM {
    background-color: transparent;
    color: rgba(255, 255, 255, 0.9);
    border-radius: 10px;
    border: none;
}

QLineEdit#SearchBar {
    background-color: rgba(0, 0, 0, 0.2);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 16px;
    padding: 7px 14px;
    color: rgba(255, 255, 255, 0.8);
    font-size: 13px;
    min-width: 260px;
}
QLineEdit#SearchBar:focus {
    background-color: rgba(0, 0, 0, 0.3);
    border: 1px solid rgba(0, 216, 214, 0.6);
    color: #FFFFFF;
}

#TopBarBtn {
    background-color: transparent;
    border: 1px solid transparent;
    color: rgba(255, 255, 255, 0.6);
    font-size: 18px;
    padding: 4px 6px;
    border-radius: 10px;
}
#TopBarBtn:hover {
    background-color: rgba(255, 255, 255, 0.1);
    border: 1px solid rgba(255, 255, 255, 0.2);
    color: #00D8D6;
}

#GameCard {
    background-color: rgba(255, 255, 255, 0.03);
    border-radius: 14px;
    border: 1px solid rgba(255, 255, 255, 0.08);
}
#GameCard:hover {
    background-color: rgba(255, 255, 255, 0.07);
    border: 1px solid rgba(0, 216, 214, 0.5);
}

#GameCoverLabel {
    background-color: transparent;
    border-radius: 14px;
}

#DialogTitle {
    font-size: 18px;
    font-weight: bold;
    color: #FFFFFF;
}

#PlayBtn, #InstallBtn {
    background-color: rgba(0, 216, 214, 0.1);
    border: 1px solid rgba(0, 216, 214, 0.4);
    border-radius: 20px;
    color: #00D8D6;
    font-size: 14px;
    font-weight: bold;
    padding: 8px 28px;
    min-width: 100px;
}
#PlayBtn:hover, #InstallBtn:hover {
    background-color: rgba(0, 216, 214, 0.2);
    border: 1px solid #00D8D6;
    color: #FFFFFF;
}

QComboBox {
    background-color: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 12px;
    padding: 5px 12px;
    color: #FFFFFF;
    font-size: 13px;
    min-width: 200px;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background-color: #1A1F2C;
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 10px;
    selection-background-color: rgba(0, 216, 214, 0.2);
    color: #FFFFFF;
}

QLineEdit {
    background-color: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 12px;
    padding: 5px 12px;
    color: #FFFFFF;
    font-size: 13px;
}
QLineEdit:focus {
    background-color: rgba(0, 0, 0, 0.2);
    border: 1px solid rgba(0, 216, 214, 0.6);
}

QPushButton {
    background-color: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 12px;
    padding: 8px 16px;
    color: #FFFFFF;
    font-weight: bold;
}
QPushButton:hover {
    background-color: rgba(255, 255, 255, 0.15);
    border: 1px solid rgba(255, 255, 255, 0.3);
}
QPushButton:pressed {
    background-color: rgba(0, 216, 214, 0.2);
    border: 1px solid #00D8D6;
    color: #FFFFFF;
}

QMenu {
    background-color: rgba(26, 31, 44, 0.95);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 12px;
    padding: 6px;
}
QMenu::item {
    background-color: transparent;
    padding: 8px 24px;
    color: #FFFFFF;
    border-radius: 8px;
}
QMenu::item:selected {
    background-color: rgba(255, 255, 255, 0.1);
}

QScrollArea {
    border: none;
    background-color: transparent;
}
QScrollArea > QWidget > QWidget {
    background-color: transparent;
}
QScrollBar:vertical {
    border: none;
    background: rgba(0, 0, 0, 0.1);
    width: 8px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: rgba(255, 255, 255, 0.2);
    min-height: 20px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: rgba(255, 255, 255, 0.3);
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    border: none;
    background: none;
}
QScrollBar:horizontal {
    border: none;
    background: rgba(0, 0, 0, 0.1);
    height: 8px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal {
    background: rgba(255, 255, 255, 0.2);
    min-width: 20px;
    border-radius: 4px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    border: none;
    background: none;
}

#StatusBar {
    background-color: rgba(255, 255, 255, 0.03);
    border-top: 1px solid rgba(255, 255, 255, 0.1);
}
#LogBtn {
    background-color: transparent;
    border: none;
    color: rgba(255, 255, 255, 0.6);
    font-size: 12px;
    font-weight: bold;
    padding: 0px 6px;
    border-radius: 8px;
}
#LogBtn:hover {
    color: #00D8D6;
    background-color: rgba(0, 216, 214, 0.06);
}
#StatusText {
    color: rgba(255, 255, 255, 0.6);
    font-size: 11px;
}
#VersionLabel {
    color: rgba(255, 255, 255, 0.5);
    font-size: 11px;
}

#IconSelectorBtn {
    background-color: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 24px;
    padding: 4px;
}
#IconSelectorBtn:checked {
    border: 1px solid rgba(0, 216, 214, 0.6);
    background-color: rgba(0, 216, 214, 0.1);
}
#IconSelectorBtn:hover {
    border: 1px solid rgba(255, 255, 255, 0.3);
}

#SteamTitle {
    color: #FFFFFF;
    font-size: 48px;
    font-weight: bold;
    letter-spacing: 4px;
}

QTabWidget::pane {
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 12px;
    background-color: rgba(255, 255, 255, 0.03);
}
QTabBar::tab {
    background-color: transparent;
    color: rgba(255, 255, 255, 0.6);
    padding: 8px 16px;
    border: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}
QTabBar::tab:selected {
    background-color: rgba(255, 255, 255, 0.05);
    color: #00D8D6;
    border-bottom: 2px solid #00D8D6;
}
QTabBar::tab:hover {
    color: #FFFFFF;
}

QPlainTextEdit {
    background-color: rgba(0, 0, 0, 0.2);
    color: rgba(255, 255, 255, 0.8);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 10px;
    font-family: monospace;
    font-size: 12px;
}

QGroupBox {
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 12px;
    margin-top: 8px;
    padding-top: 8px;
    color: rgba(255, 255, 255, 0.8);
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 4px;
}
"""

APP_NAME = "MacNCheese"
APP_VERSION = "v4.4.2"
GITHUB_REPO = "mont127/MacNdCheese"
GITHUB_LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
DEFAULT_PREFIX = str(Path.home() / "wined")
DEFAULT_DXVK_SRC = str(Path.home() / "DXVK-macOS")
DEFAULT_DXVK_INSTALL = str(Path.home() / "dxvk-release")
DEFAULT_DXVK_INSTALL32 = str(Path.home() / "dxvk-release-32")
DEFAULT_STEAM_SETUP = str(Path.home() / "Downloads" / "SteamSetup.exe")
DEFAULT_MESA_DIR = str(Path.home() / "mesa" / "x64")
DEFAULT_DXMT_DIR = str(Path.home() / "dxmt")
DEFAULT_VKD3D_DIR = str(Path.home() / "vkd3d-proton")
DEFAULT_GPTK_DIR = str(Path(__file__).resolve().with_name("gptk"))
DXVK_DLLS = ("dxgi.dll", "d3d11.dll", "d3d10core.dll")

DEFAULT_MESA_URL = "https://github.com/pal1000/mesa-dist-win/releases/download/23.1.9/mesa3d-23.1.9-release-msvc.7z"


LAUNCH_BACKEND_AUTO = "auto"
LAUNCH_BACKEND_WINE = "wine"
LAUNCH_BACKEND_DXVK = "dxvk"
LAUNCH_BACKEND_DXMT = "dxmt"
LAUNCH_BACKEND_MESA_LLVMPIPE = "mesa:llvmpipe"
LAUNCH_BACKEND_MESA_ZINK = "mesa:zink"
LAUNCH_BACKEND_MESA_SWR = "mesa:swr"
LAUNCH_BACKEND_VKD3D = "vkd3d-proton"
LAUNCH_BACKEND_GPTK = "gptk"

MESA_DRIVER_LLVMPIPE = "llvmpipe"
MESA_DRIVER_ZINK = "zink"
MESA_DRIVER_SWR = "swr"

LAUNCH_BACKENDS = (
    ("Auto (recommended)", LAUNCH_BACKEND_AUTO),
    ("Wine builtin (no DXVK/Mesa)", LAUNCH_BACKEND_WINE),
    ("DXVK (D3D11->Vulkan)", LAUNCH_BACKEND_DXVK),
    ("DXMT (experimental)", LAUNCH_BACKEND_DXMT),
    ("VKD3D-Proton (D3D12)", LAUNCH_BACKEND_VKD3D),
    ("Mesa llvmpipe (CPU, safe)", LAUNCH_BACKEND_MESA_LLVMPIPE),
    ("Mesa zink (GPU, Vulkan)", LAUNCH_BACKEND_MESA_ZINK),
    ("Mesa swr (CPU rasterizer)", LAUNCH_BACKEND_MESA_SWR),
    ("GPTK (D3DMetal)", LAUNCH_BACKEND_GPTK),
)


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
        if not window.vkd3d_dir.exists() or not window.vkd3d_dir.is_dir():
            return False
        required = ("d3d12.dll", "d3d12core.dll")
        return all((window.vkd3d_dir / name).exists() for name in required)

    def install(self, prefix: PrefixModel, window: "MainWindow") -> None:
        window.install_vkd3d()

    def required_env(self, prefix: PrefixModel, window: "MainWindow") -> dict[str, str]:
        return {
            "VKD3D_PROTON_PATH": str(window.vkd3d_dir),
        }

    def required_dll_overrides(self, prefix: PrefixModel, window: "MainWindow") -> dict[str, str]:
        return {
            "d3d12": "n,b",
            "d3d12core": "n,b",
            "dxgi": "n,b",
        }

class MoltenVkComponent(Component):
    def __init__(self) -> None:
        super().__init__("moltenvk")

    def is_installed(self, prefix: PrefixModel, window: "MainWindow") -> bool:
        return shutil.which("wine") is not None or shutil.which("wine64") is not None

    def install(self, prefix: PrefixModel, window: "MainWindow") -> None:
        raise NotImplementedError("MoltenVK installation is not implemented yet")

class DxmtComponent(Component):
    def __init__(self) -> None:
        super().__init__("dxmt")

    def is_installed(self, prefix: PrefixModel, window: "MainWindow") -> bool:
        if not window.dxmt_dir.exists() or not window.dxmt_dir.is_dir():
            return False
        return all((window.dxmt_dir / name).exists() for name in ("d3d11.dll", "dxgi.dll"))

    def install(self, prefix: PrefixModel, window: "MainWindow") -> None:
        window.install_dxmt()

    def required_env(self, prefix: PrefixModel, window: "MainWindow") -> dict[str, str]:
        return {
            "DXMT_PATH": str(window.dxmt_dir),
        }

    def required_dll_overrides(self, prefix: PrefixModel, window: "MainWindow") -> dict[str, str]:
        return {
            "dxgi": "n,b",
            "d3d11": "n,b",
        }

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
        # Tell Wine where to find the DXVK native DLLs (supplement the per-game copy)
        dxvk_bin = str(window.dxvk_bin_for_exe(game.exe_path) if game.exe_path else window.dxvk_install / "bin")
        existing_dll_path = env.get("WINEDLLPATH", "")
        env["WINEDLLPATH"] = f"{dxvk_bin}:{existing_dll_path}" if existing_dll_path else dxvk_bin
        env["WINEDLLOVERRIDES"] = "dxgi,d3d11,d3d10core=n,b"
        env["DXVK_LOG_PATH"] = str(Path.home() / "dxvk-logs")
        env["DXVK_LOG_LEVEL"] = "info"
        env["DXVK_HDR"] = "0"
        env["DXVK_STATE_CACHE"] = "0"
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
    backend_id = LAUNCH_BACKEND_VKD3D
    label = "VKD3D-Proton (D3D12)"

    def is_available(self, prefix: PrefixModel, game: GameModel, window: "MainWindow") -> bool:
        component = window.component_registry.get("vkd3d-proton")
        return bool(component and component.is_installed(prefix, window))

    def prepare_game(self, prefix: PrefixModel, game: GameModel, window: "MainWindow") -> dict[str, Any]:
        component = window.component_registry.get("vkd3d-proton")
        if not component or not component.is_installed(prefix, window):
            raise RuntimeError("VKD3D-Proton is not installed. Install VKD3D-Proton first, then try again.")
        return {"kind": "vkd3d-proton"}

    def apply_env(self, env: dict[str, str], game: GameModel, prefix: PrefixModel, window: "MainWindow") -> dict[str, str]:
        env = env.copy()
        vkd3d_path = str(window.vkd3d_dir)

        env["VKD3D_PROTON_PATH"] = vkd3d_path
        env["WINEDLLOVERRIDES"] = "d3d12,d3d12core,dxgi=n,b"

        existing_winepath = env.get("WINEPATH", "")
        env["WINEPATH"] = vkd3d_path if not existing_winepath else f"{vkd3d_path};{existing_winepath}"

        env.pop("DXVK_LOG_PATH", None)
        env.pop("DXVK_LOG_LEVEL", None)
        env.pop("GALLIUM_DRIVER", None)
        env.pop("MESA_GLTHREAD", None)
        env.setdefault("VKD3D_CONFIG", "")
        return env


class DxmtBackend(Backend):
    backend_id = LAUNCH_BACKEND_DXMT
    label = "DXMT (experimental)"

    def is_available(self, prefix: PrefixModel, game: GameModel, window: "MainWindow") -> bool:
        component = window.component_registry.get("dxmt")
        return bool(component and component.is_installed(prefix, window))

    def prepare_game(self, prefix: PrefixModel, game: GameModel, window: "MainWindow") -> dict[str, Any]:
        component = window.component_registry.get("dxmt")
        if not component or not component.is_installed(prefix, window):
            raise RuntimeError("DXMT is not installed. Install DXMT first, then try again.")
        return {"kind": "dxmt"}

    def apply_env(self, env: dict[str, str], game: GameModel, prefix: PrefixModel, window: "MainWindow") -> dict[str, str]:
        env = env.copy()
        dxmt_path = str(window.dxmt_dir)

        env["DXMT_PATH"] = dxmt_path
        env["WINEDLLOVERRIDES"] = "dxgi,d3d11=n,b"

        existing_winepath = env.get("WINEPATH", "")
        env["WINEPATH"] = dxmt_path if not existing_winepath else f"{dxmt_path};{existing_winepath}"

        env.pop("DXVK_LOG_PATH", None)
        env.pop("DXVK_LOG_LEVEL", None)
        env.pop("GALLIUM_DRIVER", None)
        env.pop("MESA_GLTHREAD", None)
        return env


class GptkBackend(Backend):
    backend_id = LAUNCH_BACKEND_GPTK
    label = "GPTK (D3DMetal)"

    def is_available(self, prefix: PrefixModel, game: GameModel, window: "MainWindow") -> bool:
        dll_dir = window.gptk_windows_dir
        return dll_dir.exists() and all((dll_dir / name).exists() for name in ("dxgi.dll", "d3d11.dll", "d3d12.dll"))

    def prepare_game(self, prefix: PrefixModel, game: GameModel, window: "MainWindow") -> dict[str, Any]:
        dll_dir = window.gptk_windows_dir
        required = ("dxgi.dll", "d3d11.dll", "d3d12.dll")
        if not dll_dir.exists() or not all((dll_dir / name).exists() for name in required):
            raise RuntimeError("GPTK DLLs not found. Put GPTK Windows DLLs in gptk/lib/wine/x86_64-windows first.")
        return {"kind": "gptk"}

    def apply_env(self, env: dict[str, str], game: GameModel, prefix: PrefixModel, window: "MainWindow") -> dict[str, str]:
        env = env.copy()
        dll_dir = str(window.gptk_windows_dir)
        env["WINEPREFIX"] = str(prefix.path)
        env["WINEPATH"] = dll_dir
        env["WINEDLLOVERRIDES"] = "dxgi,d3d11,d3d12=n,b"
        env.pop("DXVK_LOG_PATH", None)
        env.pop("DXVK_LOG_LEVEL", None)
        env.pop("VKD3D_PROTON_PATH", None)
        env.pop("DXMT_PATH", None)
        env.pop("GALLIUM_DRIVER", None)
        env.pop("MESA_GLTHREAD", None)
        return env


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
            "*.exe",
            "*/*.exe",
            "*/*/*.exe",
            "**/*.exe",
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

        low_name = self.name.lower()
        low_install = self.install_dir_name.lower()
        if "poppy playtime" in low_name or "poppy" in low_install or "project playtime" in low_name or "project playtime" in low_install:
            for exe in candidates:
                lowered = exe.name.lower()
                if "shipping.exe" in lowered and "win64" in str(exe).lower():
                    return exe

        for exe in candidates:
            try:
                if exe.exists() and exe.is_file():
                    return exe
            except Exception:
                continue

        return None
        

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
            "*.exe",
            "*/*.exe",
            "*/*/*.exe",
            "**/*.exe",
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

        low_name = self.name.lower()
        low_install = self.install_dir_name.lower()
        if "poppy playtime" in low_name or "poppy" in low_install or "project playtime" in low_name or "project playtime" in low_install:
            candidates.sort(
                key=lambda p: (
                    0 if ("shipping.exe" in p.name.lower() and "win64" in str(p).lower()) else 1,
                    0 if "shipping.exe" in p.name.lower() else 1,
                    -p.stat().st_size if p.exists() else 0,
                )
            )

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
        self._proc: Optional[subprocess.Popen] = None  # type: ignore[type-arg]
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def run(self) -> None:
        try:
            for cmd in self.commands:
                if self._cancelled:
                    self.finished.emit(False, "Cancelled")
                    return
                self.output.emit(f"$ {' '.join(cmd)}")
                self._proc = subprocess.Popen(
                    cmd,
                    cwd=self.cwd,
                    env=self.env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                assert self._proc.stdout is not None
                for line in self._proc.stdout:
                    self.output.emit(line.rstrip())
                rc = self._proc.wait()
                if self._cancelled:
                    self.finished.emit(False, "Cancelled")
                    return
                if rc != 0:
                    self.finished.emit(False, f"Command failed with exit code {rc}: {' '.join(cmd)}")
                    return
            self.finished.emit(True, "Done")
        except Exception as exc:
            self.error.emit(str(exc))
            self.finished.emit(False, str(exc))


class CoverFetcher(QThread):
                                                                                                  
    cover_bytes_ready = pyqtSignal(str, bytes)                          

    def __init__(self, appid: str, local_path: Optional[Path] = None) -> None:
        super().__init__()
        self.appid = appid
        self.local_path = local_path

    def run(self) -> None:
        if self.local_path and self.local_path.exists():
            try:
                data = self.local_path.read_bytes()
                if data:
                    self.cover_bytes_ready.emit(self.appid, data)
                    return
            except Exception:
                pass

        local_cache_dir = Path.home() / ".cache" / "macncheese" / "covers"
        local_cache_dir.mkdir(parents=True, exist_ok=True)
        cached = local_cache_dir / f"{self.appid}.jpg"

        if cached.exists():
            try:
                data = cached.read_bytes()
                if data:
                    self.cover_bytes_ready.emit(self.appid, data)
                    return
            except Exception:
                pass

        cdn_url = f"https://cdn.akamai.steamstatic.com/steam/apps/{self.appid}/library_600x900.jpg"
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(cdn_url, headers={"User-Agent": "MacNCheese"})
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                data = resp.read()
            if data:
                cached.write_bytes(data)
                self.cover_bytes_ready.emit(self.appid, data)
        except Exception:
            pass


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
                if entry and entry.appid != "228980":
                    games.append(entry)
        games.sort(key=lambda g: g.name.lower())
        return games


class CreateBottleDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create a Bottle")
        self.setObjectName("LaunchDialog")
        self.setFixedSize(480, 520)
        self.setWindowOpacity(0.0)
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity")
        self._fade_anim.setDuration(200)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_anim.start()
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(24)
        
        title = QLabel("Create a Bottle")
        title.setObjectName("DialogTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        
        form_layout = QVBoxLayout()
        form_layout.setSpacing(16)
        
              
        name_group = QVBoxLayout()
        name_group.setSpacing(8)
        name_lbl = QLabel("Bottle Name")
        name_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 12px; font-weight: bold;")
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Cool Library")
        name_group.addWidget(name_lbl)
        name_group.addWidget(self.name_edit)
        form_layout.addLayout(name_group)
        
                     
        path_group = QVBoxLayout()
        path_group.setSpacing(8)
        path_lbl = QLabel("Bottle Prefix (Path)")
        path_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 12px; font-weight: bold;")
        
        path_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setText(str(Path.home() / "Games" / "MacNCheese"))
        btn_browse = QPushButton("...")
        btn_browse.setFixedSize(32, 32)
        btn_browse.clicked.connect(self._browse_path)
        path_row.addWidget(self.path_edit)
        path_row.addWidget(btn_browse)
        
        path_group.addWidget(path_lbl)
        path_group.addLayout(path_row)
        form_layout.addLayout(path_group)
        
                        
        exe_group = QVBoxLayout()
        exe_group.setSpacing(8)
        exe_lbl = QLabel("Installer .exe (Optional)")
        exe_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 12px; font-weight: bold;")
        
        exe_row = QHBoxLayout()
        self.exe_edit = QLineEdit()
        self.exe_edit.setPlaceholderText("Select setup.exe or similar...")
        btn_browse_exe = QPushButton("...")
        btn_browse_exe.setFixedSize(32, 32)
        btn_browse_exe.clicked.connect(self._browse_exe)
        exe_row.addWidget(self.exe_edit)
        exe_row.addWidget(btn_browse_exe)
        
        exe_group.addWidget(exe_lbl)
        exe_group.addLayout(exe_row)
        form_layout.addLayout(exe_group)
        
                         
        win_group = QVBoxLayout()
        win_group.setSpacing(8)
        win_lbl = QLabel("Windows Version")
        win_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 12px; font-weight: bold;")
        self.win_combo = QComboBox()
        for v in ["Windows 10", "Windows 11", "Windows 7", "Windows 8.1"]:
            self.win_combo.addItem(v)
        win_group.addWidget(win_lbl)
        win_group.addWidget(self.win_combo)
        form_layout.addLayout(win_group)
        
               
        icons_group = QVBoxLayout()
        icons_group.setSpacing(8)
        icons_lbl = QLabel("Platform")
        icons_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 12px; font-weight: bold;")
        
        icons_row = QHBoxLayout()
        self.icon_group = QButtonGroup(self)
        self.icon_group.setExclusive(True)
        
        for i, icon_text in enumerate(["+", "", "E", "EA", "U"]):
            btn = QPushButton(icon_text)
            btn.setObjectName("IconSelectorBtn")
            btn.setCheckable(True)
            btn.setFixedSize(48, 48)
            btn.setStyleSheet("font-size: 20px;" if i < 2 else "font-size: 16px; font-weight: bold;")
            if i == 0:
                btn.setChecked(True)
            self.icon_group.addButton(btn)
            icons_row.addWidget(btn)
            
        icons_row.addStretch()
        icons_group.addWidget(icons_lbl)
        icons_group.addLayout(icons_row)
        form_layout.addLayout(icons_group)
        
        layout.addLayout(form_layout)
        layout.addStretch()
        
                       
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        install_btn = QPushButton("↓ Install")
        install_btn.setObjectName("InstallBtn")
        install_btn.clicked.connect(self.accept)
        btn_row.addWidget(install_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _browse_path(self):
        d = QFileDialog.getExistingDirectory(self, "Select Prefix Directory", self.path_edit.text())
        if d:
            self.path_edit.setText(d)

    def _browse_exe(self):
        f, _ = QFileDialog.getOpenFileName(self, "Select Installer Executable", str(Path.home()), "Executables (*.exe *.bat *.msi);;All Files (*)")
        if f:
            self.exe_edit.setText(f)


class GameLaunchDialog(QDialog):
    def __init__(self, game: "GameEntry", parent=None):
        super().__init__(parent)
        self.game = game
        self.parent_window = parent
        self.detected_exes = game.detect_exes()
        self.selected_exe: Optional[Path] = None
        self.setWindowTitle(game.name)
        self.setObjectName("LaunchDialog")
        self.setFixedSize(560, 320)
        self.setWindowOpacity(0.0)
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity")
        self._fade_anim.setDuration(200)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_anim.start()
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(24)
        
                              
        cover_lbl = QLabel()
        cover_lbl.setObjectName("GameCoverLabel")
        cover_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover_lbl.setFixedSize(160, 240)
        cover_lbl.setScaledContents(False)
        cover_lbl.setStyleSheet("background-color: transparent; border-radius: 14px;")
        
        if hasattr(parent, "_cover_cache") and game.appid in parent._cover_cache:
            try:
                pix = QPixmap()
                pix.loadFromData(parent._cover_cache[game.appid])
                if not pix.isNull():
                    scaled = pix.scaled(160, 240, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                        Qt.TransformationMode.SmoothTransformation)
                    x_off = max(0, (scaled.width() - 160) // 2)
                    y_off = max(0, (scaled.height() - 240) // 2)
                    cropped = scaled.copy(x_off, y_off, 160, 240)
                    cover_lbl.setPixmap(cropped)
            except RuntimeError:
                pass
                
        layout.addWidget(cover_lbl)
        
                                         
        right_layout = QVBoxLayout()
        right_layout.setSpacing(16)
        
        title = QLabel(game.name)
        title.setObjectName("DialogTitle")
        title.setWordWrap(True)
        right_layout.addWidget(title)
        
                      
        info_lbl = QLabel("0.0 hours played \nLast played: Never")
        info_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 12px; line-height: 1.5;")
        right_layout.addWidget(info_lbl)
        
        right_layout.addStretch()
        
                     
        form_layout = QVBoxLayout()
        form_layout.setSpacing(12)
        
                          
        back_row = QHBoxLayout()
        back_lbl = QLabel("Backend:")
        back_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 13px; font-weight: bold; width: 60px;")
        self.backend_combo = QComboBox()
        for label, value in LAUNCH_BACKENDS:
            self.backend_combo.addItem(label, value)
        back_row.addWidget(back_lbl)
        back_row.addWidget(self.backend_combo, 1)
        form_layout.addLayout(back_row)

        exe_row = QHBoxLayout()
        exe_lbl = QLabel("EXE:")
        exe_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 13px; font-weight: bold; width: 60px;")
        self.exe_combo = QComboBox()
        self.exe_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.exe_combo.setToolTip("Select which executable to launch")
        self.exe_combo.addItem("Auto-detect", "")

        current_selected = None
        if self.parent_window and hasattr(self.parent_window, "selected_startup_exes"):
            current_selected = self.parent_window.selected_startup_exes.get(game.appid)

        for exe_path in self.detected_exes:
            try:
                rel = exe_path.relative_to(game.game_dir)
                label = str(rel)
            except Exception:
                label = exe_path.name
            self.exe_combo.addItem(label, str(exe_path))

        if current_selected:
            for i in range(self.exe_combo.count()):
                if self.exe_combo.itemData(i) == str(current_selected):
                    self.exe_combo.setCurrentIndex(i)
                    break

        btn_browse_exe = QPushButton("...")
        btn_browse_exe.setFixedSize(32, 32)
        btn_browse_exe.setToolTip("Browse for executable")
        btn_browse_exe.clicked.connect(self._browse_exe)

        exe_row.addWidget(exe_lbl)
        exe_row.addWidget(self.exe_combo, 1)
        exe_row.addWidget(btn_browse_exe)
        form_layout.addLayout(exe_row)
        
                    
        args_row = QHBoxLayout()
        args_lbl = QLabel("Args:")
        args_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 13px; font-weight: bold; width: 60px;")
        self.args_edit = QLineEdit()
        self.args_edit.setPlaceholderText("Optional game arguments...")
        args_row.addWidget(args_lbl)
        args_row.addWidget(self.args_edit, 1)
        form_layout.addLayout(args_row)
        
        right_layout.addLayout(form_layout)
        
        right_layout.addSpacing(16)
        
                     
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.play_btn = QPushButton("▶ PLAY")
        self.play_btn.setObjectName("PlayBtn")
        self.play_btn.clicked.connect(self._on_play)
        btn_row.addWidget(self.play_btn)
        right_layout.addLayout(btn_row)
        
        layout.addLayout(right_layout, 1)

    def _browse_exe(self):
        start_dir = str(self.game.game_dir) if self.game.game_dir.exists() else str(Path.home())
        f, _ = QFileDialog.getOpenFileName(self, "Select game executable", start_dir, "Executables (*.exe);;All Files (*)")
        if not f:
            return
        path = Path(f)
        label = path.name
        try:
            label = str(path.relative_to(self.game.game_dir))
        except Exception:
            pass
        existing_index = -1
        for i in range(self.exe_combo.count()):
            if self.exe_combo.itemData(i) == str(path):
                existing_index = i
                break
        if existing_index >= 0:
            self.exe_combo.setCurrentIndex(existing_index)
        else:
            self.exe_combo.addItem(label, str(path))
            self.exe_combo.setCurrentIndex(self.exe_combo.count() - 1)

    def _on_play(self):
        p = self.parent_window
        selected_exe_data = self.exe_combo.currentData()
        if p and hasattr(p, "selected_startup_exes"):
            if selected_exe_data:
                p.selected_startup_exes[self.game.appid] = Path(selected_exe_data)
            else:
                p.selected_startup_exes.pop(self.game.appid, None)

        if p and hasattr(p, "launch_selected_game"):
            backend_id = self.backend_combo.currentData()
            args = self.args_edit.text()
            p.launch_selected_game(self.game, backend_id=backend_id, extra_args=args)
        self.accept()

class UpdateChecker(QThread):
    update_available = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

    def run(self):
        try:
            ctx = ssl._create_unverified_context()
            
            req = urllib.request.Request(GITHUB_LATEST_RELEASE_API, headers={'User-Agent': f'{APP_NAME}-Updater'})
            with urllib.request.urlopen(req, timeout=5, context=ctx) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    tag = data.get("tag_name", "")
                    if tag:
                        current_clean = APP_VERSION.lstrip('v')
                        latest_clean = tag.lstrip('v')
                        
                        def version_tuple(v):
                            return tuple(int(x) if x.isdigit() else x for x in v.split('.'))

                        if version_tuple(latest_clean) > version_tuple(current_clean):
                            self.update_available.emit(tag)
        except Exception as e:
            print(f"Update check failed: {e}")

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
        self._progress_dlg: Optional[_InstallProgressDialog] = None

        self.prefix_combo = self.settings.prefix_combo
        self.dxvk_src_edit = self.settings.dxvk_src_edit
        self.dxvk_install_edit = self.settings.dxvk_install_edit
        self.dxvk_install32_edit = self.settings.dxvk_install32_edit
        self.steam_setup_edit = self.settings.steam_setup_edit
        self.mesa_dir_edit = self.settings.mesa_dir_edit
        self.dxmt_dir_edit = self.settings.dxmt_dir_edit
        self.vkd3d_dir_edit = self.settings.vkd3d_dir_edit
        self.gptk_dir_edit = self.settings.gptk_dir_edit

        self._cover_cache: dict[str, bytes] = {}      
        self._cover_failed: set[str] = set()            
        self._active_fetchers: list[CoverFetcher] = [] 

        self.component_registry = ComponentRegistry()
        self.backend_registry = BackendRegistry()
        self._register_components()
        self._register_backends()

        self._build_ui()
        self._build_menu()
        self.load_user_settings()
        self.startup_update_check()
        self.log(f"{APP_NAME} ready")

    def load_user_settings(self) -> None:
        self.user_settings_path = Path.home() / ".macncheese_settings.json"
        self.skip_update_check = False
        if self.user_settings_path.exists():
            try:
                data = json.loads(self.user_settings_path.read_text())
                self.skip_update_check = data.get("skip_update_check", False)
            except Exception:
                pass

    def save_user_settings(self) -> None:
        try:
            data = {"skip_update_check": self.skip_update_check}
            self.user_settings_path.write_text(json.dumps(data))
        except Exception:
            pass

    def startup_update_check(self):
        if not self.skip_update_check:
            self.update_checker = UpdateChecker(self)
            self.update_checker.update_available.connect(self.show_update_dialog)
            self.update_checker.start()

    def show_update_dialog(self, latest_version: str):
        msg = QMessageBox(self)
        msg.setWindowTitle("Update Available")
        msg.setText(f"A new version of {APP_NAME} ({latest_version}) is available!\\nWould you like to update now?")
        
        update_btn = msg.addButton("Update", QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = msg.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        
        cb = QCheckBox("Don't ask me again")
        msg.setCheckBox(cb)
        
        msg.exec()
        
        if cb.isChecked():
            self.skip_update_check = True
            self.save_user_settings()
            
        if msg.clickedButton() == update_btn:
            webbrowser.open(GITHUB_RELEASES_URL)

    def _register_components(self) -> None:
        for component in (
            WineComponent(),
            DxvkComponent(),
            DxmtComponent(),
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
            GptkBackend(),
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
        exe_name = game.exe_path.name.lower() if game.exe_path else ""

        if "poppy playtime" in token or "poppy_playtime" in exe_name or "project playtime" in token or "project_playtime" in exe_name:
            return LAUNCH_BACKEND_DXVK
        if "mewgenics" in token:
            return LAUNCH_BACKEND_MESA_LLVMPIPE
        if "enlisted" in token or exe_name == "enlisted.exe" or exe_name == "enlisted-min-cpu.exe":
            return LAUNCH_BACKEND_VKD3D
        if (game.install_path / "D3D12").exists():
            return LAUNCH_BACKEND_VKD3D
        return LAUNCH_BACKEND_DXVK

    def resolve_backend(self, backend_id: str, game: GameModel, prefix: PrefixModel) -> Backend:
        backend = self.backend_registry.get(backend_id)
        if backend is None:
            backend = self.backend_registry.get(LAUNCH_BACKEND_AUTO)
        if backend is None:
            return WineBuiltinBackend()
        if backend.backend_id == LAUNCH_BACKEND_AUTO and isinstance(backend, AutoBackend):
            return backend.resolve(prefix, game, self)
        return backend

    def _asset_path(self, filename: str) -> Optional[Path]:
        candidates = [
            Path(__file__).resolve().with_name(filename),
            Path(__file__).resolve().parent / filename,
            Path.cwd() / filename,
        ]
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            candidates.extend([
                exe_dir / filename,
                exe_dir.parent / "Resources" / filename,
                Path(getattr(sys, "_MEIPASS", "")) / filename if getattr(sys, "_MEIPASS", None) else None,
            ])
        for candidate in candidates:
            if candidate and candidate.exists():
                return candidate
        return None

    def _set_button_icon_from_asset(self, button: QPushButton, filename: str, *, size: int = 20) -> bool:
        asset = self._asset_path(filename)
        if asset is None:
            return False
        icon = QIcon(str(asset))
        if icon.isNull():
            return False
        button.setText("")
        button.setIcon(icon)
        button.setIconSize(QSize(size, size))
        return True

    def _set_label_pixmap_from_asset(self, label: QLabel, filename: str, *, width: int, height: int) -> bool:
        asset = self._asset_path(filename)
        if asset is None:
            return False
        pix = QPixmap(str(asset))
        if pix.isNull():
            return False
        scaled = pix.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        label.setPixmap(scaled)
        return True

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
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

                         
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(64)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 8, 0, 8)
        sidebar_layout.setSpacing(4)

                                                     
        self.sidebar_group = QButtonGroup(self)
        self.sidebar_group.setExclusive(True)

                                                                           
        self._sidebar_containers_layout = QVBoxLayout()
        self._sidebar_containers_layout.setContentsMargins(0, 0, 0, 0)
        self._sidebar_containers_layout.setSpacing(4)
        sidebar_layout.addLayout(self._sidebar_containers_layout)

        sidebar_layout.addStretch()

                                  
        self.btn_add_container = QPushButton("+")
        self.btn_add_container.setObjectName("AddContainerButton")
        self.btn_add_container.setFixedSize(44, 44)
        self.btn_add_container.setToolTip("Create a new Wine container (bottle)")
        self.btn_add_container.clicked.connect(self._open_create_bottle_dialog)
        self._set_button_icon_from_asset(self.btn_add_container, "Add.png", size=22)
        sidebar_layout.addWidget(self.btn_add_container, 0, Qt.AlignmentFlag.AlignHCenter)

        root_layout.addWidget(sidebar, 0)


                           
        main_area = QWidget()
        main_layout = QVBoxLayout(main_area)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

                
        topbar = QFrame()
        topbar.setObjectName("Topbar")
        topbar.setFixedHeight(60)
        topbar_layout = QHBoxLayout(topbar)
        topbar_layout.setContentsMargins(16, 0, 16, 0)

        logo_layout = QHBoxLayout()
        logo_layout.setSpacing(8)
        lbl_m = QLabel()
        lbl_m.setObjectName("LogoM")
        lbl_m.setFixedSize(32, 32)
        if not self._set_label_pixmap_from_asset(lbl_m, "Wine.png", width=28, height=28):
            lbl_m.setText("M")
            lbl_m.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_text = QLabel("MacNCheese Library")
        lbl_text.setObjectName("LogoText")
        logo_layout.addWidget(lbl_m)
        logo_layout.addWidget(lbl_text)
        topbar_layout.addLayout(logo_layout)

        topbar_layout.addSpacing(32)

        self.btn_top_launch_steam = QPushButton(" Steam")
        self.btn_top_launch_steam.setObjectName("TopBarBtn")
        self.btn_top_launch_steam.setToolTip("Launch Steam")
        self._set_button_icon_from_asset(self.btn_top_launch_steam, "Steam.png", size=20)
        self.btn_top_launch_steam.clicked.connect(self.launch_steam)
        topbar_layout.addWidget(self.btn_top_launch_steam)


        self.search_bar = QLineEdit()
        self.search_bar.setObjectName("SearchBar")
        self.search_bar.setPlaceholderText("Search games...")
        self.search_bar.setFixedWidth(280)
        self.search_bar.textChanged.connect(self._filter_games)
        topbar_layout.addWidget(self.search_bar)

        

        main_layout.addWidget(topbar)

                                  
        self.stacked_widget = QStackedWidget()
        main_layout.addWidget(self.stacked_widget, 1)

                            
        self.games_scroll = QScrollArea()
        self.games_scroll.setWidgetResizable(True)
        self.games_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        self.games_container = QWidget()
        self.games_flow_layout = FlowLayout(self.games_container, margin=24, hSpacing=16, vSpacing=16)
        self.games_scroll.setWidget(self.games_container)
        self.stacked_widget.addWidget(self.games_scroll)

                                  
        self.steam_view = QWidget()
        steam_layout = QVBoxLayout(self.steam_view)
        steam_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
                                
        steam_logo_lbl = QLabel("STEAM")
        steam_logo_lbl.setStyleSheet("font-size: 64px; font-weight: bold; color: #FFFFFF;")
        steam_logo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        steam_buttons_layout = QHBoxLayout()
        self.btn_install_steam = QPushButton("Install Steam")
        self.btn_install_steam.setStyleSheet("padding: 12px 24px; font-size: 16px; background-color: transparent; border: 2px solid #FFFFFF; border-radius: 16px;")
        self.btn_install_steam.clicked.connect(self.unified_steam_action)
        
        self.btn_launch_steam = QPushButton("▶")
        self.btn_launch_steam.setStyleSheet("padding: 12px; font-size: 16px; background-color: transparent; border: 2px solid #FFFFFF; border-radius: 16px;")
        self.btn_launch_steam.clicked.connect(self.launch_steam)
        
        steam_buttons_layout.addWidget(self.btn_install_steam)
        steam_buttons_layout.addWidget(self.btn_launch_steam)
        
        steam_layout.addWidget(steam_logo_lbl)
        steam_layout.addSpacing(24)
        steam_layout.addLayout(steam_buttons_layout)
        
        self.stacked_widget.addWidget(self.games_scroll)           

                                                                                               
        self._build_steam_landing_view()            

                                 
        status_bar = QFrame()
        status_bar.setObjectName("StatusBar")
        status_bar.setFixedHeight(26)
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(6, 0, 12, 0)
        status_layout.setSpacing(0)

        
        btn_log = QPushButton("Settings")
        btn_log.setObjectName("LogBtn")
        btn_log.setFixedHeight(26)
        btn_log.clicked.connect(self.settings.show)
        status_layout.addWidget(btn_log)

        status_layout.addSpacing(8)

                       
        self.status_label = QLabel("Logs: Idle")
        self.status_label.setObjectName("StatusText")
        status_layout.addWidget(self.status_label, 1)

        version_label = QLabel(f"version: {APP_VERSION}")
        version_label.setObjectName("VersionLabel")
        status_layout.addWidget(version_label)

        main_layout.addWidget(status_bar)
        root_layout.addWidget(main_area, 1)

                                                                 
        self.games_list = QListWidget()
        self.games_list.hide()
        self.games_list.itemSelectionChanged.connect(self.update_selected_game_status)

        self._quick_setup_box = None
        self._paths_box = None
        self._setup_box = None
        self._runtime_box = None
        self._status_box = None
        self.simple_ui_btn = None
        self.dev_ui_btn = None

                                                        
        self.stacked_widget.setCurrentIndex(0)

        self.scan_timer = QTimer(self)
        self.scan_timer.timeout.connect(self.scan_games)
        self.scan_timer.start(3000)

    def _build_steam_landing_view(self) -> None:
                                                                                                     
        self.steam_view = QWidget()
        self.steam_view.setStyleSheet("background-color: transparent;")
        steam_layout = QVBoxLayout(self.steam_view)
        steam_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        steam_layout.setSpacing(0)

                          
        steam_logo_lbl = QLabel()
        steam_logo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if not self._set_label_pixmap_from_asset(steam_logo_lbl, "Steam.png", width=120, height=120):
            steam_logo_lbl.setText("🎮")
            steam_logo_lbl.setStyleSheet("font-size: 80px;")

                       
        steam_title = QLabel("STEAM")
        steam_title.setObjectName("SteamTitle")
        steam_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        steam_layout.addWidget(steam_title)

        steam_layout.addSpacing(24)

                                                                  
        launch_row = QHBoxLayout()
        launch_row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_install_steam = QPushButton("Launch")
        self.btn_install_steam.setObjectName("PlayBtn")
        self.btn_install_steam.setFixedWidth(160)
        self.btn_install_steam.clicked.connect(self.unified_steam_action)
        launch_row.addWidget(self.btn_install_steam)

                                     
        btn_play_icon = QPushButton("▶")
        btn_play_icon.setObjectName("PlayBtn")
        btn_play_icon.setFixedSize(40, 36)
        btn_play_icon.clicked.connect(self.unified_steam_action)
        launch_row.addWidget(btn_play_icon)

        steam_layout.addLayout(launch_row)

        self.stacked_widget.addWidget(self.steam_view)           

    def _update_steam_button(self) -> None:
        steam_installed = (self.steam_dir / "steam.exe").exists()
        label = "Launch" if steam_installed else "Install Steam"
        if hasattr(self, "btn_install_steam"):
            self.btn_install_steam.setText(label)

    def switch_view(self, view_name: str) -> None:
        if view_name == "steam":
            self._update_steam_button()
            self.stacked_widget.setCurrentIndex(1)
        elif view_name == "games":
            self.stacked_widget.setCurrentIndex(0)
        else:
            self.stacked_widget.setCurrentIndex(0)

    def _open_create_bottle_dialog(self) -> None:
        dlg = CreateBottleDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            path_str = dlg.path_edit.text().strip()
            if not path_str:
                return

            p = Path(path_str)
            p.mkdir(parents=True, exist_ok=True)

            items = [self.prefix_combo.itemText(i) for i in range(self.prefix_combo.count())]
            if str(p) not in items:
                self.prefix_combo.insertItem(0, str(p))
            
            self.prefix_combo.setCurrentText(str(p))
            if hasattr(self.settings, "_save_current_prefixes"):
                self.settings._save_current_prefixes()

            name = dlg.name_edit.text().strip() or "Bottle"
            icon_text = "📦"
            if hasattr(dlg, "icon_group") and dlg.icon_group.checkedButton():
                icon_text = dlg.icon_group.checkedButton().text()

            btn = self._add_sidebar_container(name)
            btn._prefix_path = str(p)
            
                                                                                    
            btn.clicked.connect(lambda _, path=str(p): self._switch_to_bottle(path))
                                                                             
            btn.setChecked(True)

            exe_path = dlg.exe_edit.text().strip()
            wine = self.ensure_wine()
            if wine:
                cmd = [wine, exe_path] if exe_path else [wine, "wineboot"]
                self.run_commands([cmd], env=self.wine_env(), progress_title="Initialising bottle…")
            
            self.set_status(f"Created bottle '{name}' at {p}")
            self.scan_games()

    def remove_sidebar_button_for_prefix(self, path: str) -> None:
        for i in range(self._sidebar_containers_layout.count()):
            item = self._sidebar_containers_layout.itemAt(i)
            if item and item.widget():
                btn = item.widget()
                if getattr(btn, "_prefix_path", None) == path:
                    self.sidebar_group.removeButton(btn)
                    self._sidebar_containers_layout.removeWidget(btn)
                    btn.deleteLater()
                    break

    def _filter_games(self, text: str) -> None:
                                                        
        text = text.strip().lower()
        for i in range(self.games_flow_layout.count()):
            item = self.games_flow_layout.itemAt(i)
            if item and item.widget():
                card = item.widget()
                game_name = getattr(card, "_game_name", "").lower()
                card.setVisible(not text or text in game_name)

    def _add_sidebar_container(self, name: str, icon_path: Optional[Path] = None) -> QPushButton:
                                                                  
        btn = QPushButton()
        btn.setObjectName("SidebarButton")
        btn.setCheckable(True)
        btn.setFixedSize(52, 56)
        btn.setToolTip(name)

                                                     
        btn_layout = QVBoxLayout(btn)
        btn_layout.setContentsMargins(2, 4, 2, 2)
        btn_layout.setSpacing(1)
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_lbl = QLabel()
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setFixedSize(32, 32)
        icon_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        if icon_path and icon_path.exists():
            pix = QPixmap(str(icon_path))
            if not pix.isNull():
                pix = pix.scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
                icon_lbl.setPixmap(pix)
            else:
                icon_lbl.setText("📦")
                icon_lbl.setStyleSheet("font-size: 18px;")
        else:
            icon_lbl.setText("📦")
            icon_lbl.setStyleSheet("font-size: 18px;")

        text_lbl = QLabel(name)
        text_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        text_lbl.setStyleSheet("font-size: 9px; color: inherit; background: transparent;")

        btn_layout.addWidget(icon_lbl)
        btn_layout.addWidget(text_lbl)

        self._sidebar_containers_layout.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
        self.sidebar_group.addButton(btn)
        return btn


    def create_game_card(self, game: "GameEntry") -> "QWidget":
                                                                             
        card = QFrame()
        card.setObjectName("GameCard")
        card.setFixedSize(150, 225)
        card.setStyleSheet("#GameCard { overflow: hidden; }")
        card._game_name = game.name

        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        cover_lbl = QLabel()
        cover_lbl.setObjectName("GameCoverLabel")
        cover_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover_lbl.setFixedSize(150, 225)
        cover_lbl.setScaledContents(False)
        cover_lbl.setStyleSheet("background-color: transparent; border-radius: 14px;")

        def _apply_pixmap(data: bytes, lbl: QLabel = cover_lbl) -> None:
            try:
                pix = QPixmap()
                pix.loadFromData(data)
                if pix.isNull():
                    return
                scaled = pix.scaled(150, 225, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                    Qt.TransformationMode.SmoothTransformation)
                x_off = max(0, (scaled.width() - 150) // 2)
                y_off = max(0, (scaled.height() - 225) // 2)
                cropped = scaled.copy(x_off, y_off, 150, 225)
                lbl.setPixmap(cropped)
                lbl.setStyleSheet("border-radius: 14px;")
            except RuntimeError:
                pass

        if game.appid in self._cover_cache:
            _apply_pixmap(self._cover_cache[game.appid])
        elif game.appid not in self._cover_failed:
            librarycache_dir = self.steam_dir / "appcache" / "librarycache"
            local_candidates = [
                librarycache_dir / f"p{game.appid}_library_600x900.jpg",
                librarycache_dir / f"{game.appid}_library_600x900.jpg",
            ]
            local_path = next((p for p in local_candidates if p.exists()), None)
            cached_path = Path.home() / ".cache" / "macncheese" / "covers" / f"{game.appid}.jpg"
            if local_path is None and cached_path.exists():
                local_path = cached_path

            already_fetching = any(f.appid == game.appid and f.isRunning() for f in self._active_fetchers)
            if not already_fetching:
                fetcher = CoverFetcher(game.appid, local_path)

                def _on_fetched(appid: str, data: bytes, lbl=cover_lbl):
                    self._cover_cache[appid] = data
                    _apply_pixmap(data, lbl)
                    self._active_fetchers[:] = [f for f in self._active_fetchers if f.isRunning()]

                def _on_finished(fetcher_ref=None, appid=game.appid):
                    if appid not in self._cover_cache:
                        self._cover_failed.add(appid)

                fetcher.cover_bytes_ready.connect(_on_fetched)
                fetcher.finished.connect(_on_finished)
                self._active_fetchers.append(fetcher)
                fetcher.start()

        layout.addWidget(cover_lbl)



       
        def _select_game():
            for i in range(self.games_list.count()):
                if self.games_list.item(i).data(256) == game:
                    self.games_list.setCurrentRow(i)
                    break

       
        def _on_click(checked=False, g=game):
            _select_game()
            dlg = GameLaunchDialog(g, self)
            dlg.exec()

        card.mousePressEvent = lambda e: _on_click()
        card.setCursor(Qt.CursorShape.PointingHandCursor)

        
        card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        card.customContextMenuRequested.connect(
            lambda pos: self.show_game_context_menu(card, game, pos)
        )

        return card

    def show_game_context_menu(self, card_widget, game: "GameEntry", pos: "QPoint"):
        for i in range(self.games_list.count()):
            if self.games_list.item(i).data(256) == game:
                self.games_list.setCurrentRow(i)
                break

        menu = QMenu(self)
        action_mesa = menu.addAction("Install Mesa")
        action_dxmt = menu.addAction("Install DXMT")
        action_vkd3d = menu.addAction("Install VKD3D-Proton")
        action_dxvk64 = menu.addAction("Install DXVK")
        action_dxvk32 = menu.addAction("Install DXVK")
        action_wine = menu.addAction("Install Wine")
        action_steam = menu.addAction("Install Steam")

        menu.addSeparator()
        action_setup_btn = QWidgetAction(menu)
        setup_btn = QPushButton("One Click SetUp")
        setup_btn.setStyleSheet("background-color: rgba(255, 102, 0, 0.8); font-weight: bold; border-radius: 12px; padding: 6px;")
        setup_btn.clicked.connect(self.quick_setup)
        setup_btn.clicked.connect(menu.close)
        container = QWidget()
        cl = QHBoxLayout(container)
        cl.setContentsMargins(20, 4, 20, 4)
        cl.addWidget(setup_btn)
        action_setup_btn.setDefaultWidget(container)
        menu.addAction(action_setup_btn)

        action_mesa.triggered.connect(self.install_mesa)
        action_dxmt.triggered.connect(self.install_dxmt)
        action_vkd3d.triggered.connect(self.install_vkd3d)
        action_dxvk64.triggered.connect(self.build_dxvk)
        action_dxvk32.triggered.connect(self.build_dxvk32)
        action_wine.triggered.connect(self.install_wine)
        action_steam.triggered.connect(self.install_steam)

        menu.exec(card_widget.mapToGlobal(pos))


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

    @property
    def dxmt_dir(self) -> Path:
        return Path(self.dxmt_dir_edit.text()).expanduser()

    @property
    def vkd3d_dir(self) -> Path:
        return Path(self.vkd3d_dir_edit.text()).expanduser()

    @property
    def gptk_dir(self) -> Path:
        return Path(self.gptk_dir_edit.text()).expanduser()

    @property
    def gptk_windows_dir(self) -> Path:
        return self.gptk_dir / "lib" / "wine" / "x86_64-windows"


    def wine_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["WINEPREFIX"] = str(self.prefix_path)

        # DXVK needs Vulkan. On macOS, Vulkan comes from MoltenVK bundled inside
        # Wine Stable.app. The Vulkan loader (libvulkan.1.dylib from Homebrew) finds
        # drivers via ICD manifest JSON files pointed to by VK_ICD_FILENAMES.
        # Without a manifest, vkCreateInstance returns VK_ERROR_INCOMPATIBLE_DRIVER
        # and DXVK silently falls back → game shows "DirectX 11 not available".
        if not env.get("VK_ICD_FILENAMES"):
            env["VK_ICD_FILENAMES"] = self._ensure_moltenvk_icd()

        return env

    def _ensure_moltenvk_icd(self) -> str:
        """Return a path to a MoltenVK ICD JSON manifest, creating one if needed."""
        # 1. Pre-existing JSON manifests (SDK / Homebrew installs)
        existing_json = [
            Path("/usr/local/share/vulkan/icd.d/MoltenVK_icd.json"),
            Path("/opt/homebrew/share/vulkan/icd.d/MoltenVK_icd.json"),
            Path(Path.home() / ".local/share/vulkan/icd.d/MoltenVK_icd.json"),
            Path("/Applications/Wine Stable.app/Contents/Resources/vulkan/icd.d/MoltenVK_icd.json"),
            Path("/Applications/Wine Staging.app/Contents/Resources/vulkan/icd.d/MoltenVK_icd.json"),
        ]
        for p in existing_json:
            if p.exists():
                return str(p)

        # 2. No pre-existing manifest — find libMoltenVK.dylib and generate one
        moltenvk_lib_candidates = [
            Path("/Applications/Wine Stable.app/Contents/Resources/wine/lib/libMoltenVK.dylib"),
            Path("/Applications/Wine Staging.app/Contents/Resources/wine/lib/libMoltenVK.dylib"),
            Path("/usr/local/lib/libMoltenVK.dylib"),
            Path("/opt/homebrew/lib/libMoltenVK.dylib"),
        ]
        for lib in moltenvk_lib_candidates:
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

        return ""  # nothing found — leave env var unset

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
        progress_title: str = "Installing…",
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
        self.interactive_install_in_progress = True

        # Show progress dialog
        self._progress_dlg = _InstallProgressDialog(progress_title, self)
        self._progress_dlg.cancel_requested.connect(self._cancel_worker)

        self.worker_thread = QThread(self)
        self.worker = CommandWorker(commands, env=env, cwd=cwd)
        self.worker.moveToThread(self.worker_thread)

        self.worker_thread.started.connect(self.worker.run)
        self.worker.output.connect(self.append_log)
        self.worker.output.connect(self._progress_dlg.update_step)
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
        self._progress_dlg.exec()

    def _cancel_worker(self) -> None:
        if self.worker:
            self.worker.cancel()

    def on_worker_finished(self, ok: bool, message: str) -> None:
        self.set_status(message if ok else f"Failed: {message}")
        self.interactive_install_in_progress = False
        if self._progress_dlg is not None:
            if ok:
                self._progress_dlg.accept()
            else:
                self._progress_dlg.mark_done(False, message)
            self._progress_dlg = None

        state = getattr(self, "_unified_state", 0)
        self._unified_state = 0
        if not self.missing_core_tools():
            self.interactive_install_in_progress = False
            self.interactive_install_action = None
            self.pending_post_install_action = None
        if not ok:
            lower = message.lower()
            if lower == "cancelled":
                return
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


    def missing_core_tools(self) -> list[str]:
        missing: list[str] = []
        if not self.has_wine():
            missing.append("Wine")
        if not (self.dxvk_install / "bin" / "dxgi.dll").exists():
            missing.append("DXVK")
        if not (self.mesa_dir / "opengl32.dll").exists():
            missing.append("Mesa")
        return missing

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

    def request_admin_env(self) -> Optional[dict[str, str]]:
        dlg = _AdminPasswordDialog(
            "MacNCheese needs your administrator password to install software on your Mac.",
            self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        password = dlg.password()
        if not password:
            return None
        env = os.environ.copy()
        env["MNC_SUDO_PASSWORD"] = password
        return env

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

    _ACTION_TITLES: dict[str, str] = {
        "install_tools": "Installing Tools",
        "install_wine": "Installing Wine",
        "install_mesa": "Installing Mesa",
        "install_dxvk": "Installing DXVK",
        "install_dxmt": "Installing DXMT",
        "install_vkd3d": "Installing VKD3D-Proton",
        "quick_setup": "Setting Up MacNCheese",
        "init_prefix": "Initialising Wine Prefix",
        "install_steam": "Installing Steam",
    }

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
        title = self._ACTION_TITLES.get(action, f"Running: {action}")
        self.run_commands([args], env=env, cwd=str(script.parent), progress_title=title)


    def _version_tuple(self, value: str) -> tuple[int, ...]:
        cleaned = value.strip().lower().lstrip("v")
        parts: list[int] = []
        for part in cleaned.split("."):
            digits = "".join(ch for ch in part if ch.isdigit())
            parts.append(int(digits or 0))
        return tuple(parts)

    def check_for_updates(self) -> None:
        try:
            ctx = ssl._create_unverified_context()
            req = urllib.request.Request(
                GITHUB_LATEST_RELEASE_API,
                headers={"Accept": "application/vnd.github+json", "User-Agent": APP_NAME},
            )
            with urllib.request.urlopen(req, timeout=8, context=ctx) as response:
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

    def install_dxmt(self) -> None:
        self.run_installer_action("install_dxmt")

    def install_vkd3d(self) -> None:
        self.run_installer_action("install_vkd3d")

    def quick_setup(self) -> None:
        self.run_installer_action("quick_setup")

    def install_dxvk(self) -> None:
        self.run_installer_action("install_dxvk")

    def build_dxvk(self) -> None:
        self.install_dxvk()

    def build_dxvk32(self) -> None:
        self.install_dxvk()

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
        if not self.steam_setup.exists():
            QMessageBox.warning(self, APP_NAME, f"SteamSetup.exe not found at {self.steam_setup}")
            return

        # Tell the user where to install before the setup window appears
        QMessageBox.information(
            self,
            "Install Steam",
            "Steam Setup will open now.\n\n"
            "When asked where to install, keep the default path (C:\\Program Files\\Steam).\n\n"
            "MacNCheese will detect Steam automatically once setup is complete.",
        )

        self.activateWindow()
        self.raise_()

        env = self.wine_env()
        qenv = QProcessEnvironment.systemEnvironment()
        for key, value in env.items():
            qenv.insert(key, value)

        proc = QProcess(self)
        proc.setProcessEnvironment(qenv)
        proc.setProgram(wine)
        proc.setArguments([str(self.steam_setup)])
        proc.readyReadStandardOutput.connect(lambda: self._drain_process(proc))
        proc.readyReadStandardError.connect(lambda: self._drain_process(proc))

        def _on_steam_setup_done(exit_code: int, _exit_status) -> None:
            self.log(f"SteamSetup.exe exited with code {exit_code}")
            self.activateWindow()
            self.raise_()
            if (self.steam_dir / "steam.exe").exists():
                self.set_status("Steam installed successfully")
                self._update_steam_button()
                QMessageBox.information(self, APP_NAME, "Steam has been installed! Click Launch to start it.")
            else:
                self.set_status("Steam installation may not have completed")

        proc.finished.connect(_on_steam_setup_done)
        proc.start()
        self.set_status("Steam Setup running — complete the installer in the Steam window")


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
                self.set_status("Setup already in progress, please wait…")
                QMessageBox.information(
                    self,
                    APP_NAME,
                    "MacNCheese is already installing the required tools. Please wait for it to finish, then click Launch Steam again.",
                )
                return

            clt_ok, clt_msg = self.check_clt_installed()
            if not clt_ok:
                QMessageBox.warning(self, APP_NAME, clt_msg)
                self.set_status("Xcode Command Line Tools required")
                return

            self.set_status(f"Missing prerequisites ({', '.join(missing)}). Starting setup…")
            self._unified_state = 1
            self.run_installer_action("quick_setup")
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
        return list(base.glob("*/AppData/LocalLowPlayer.log")) + list(base.glob("*/AppData/LocalLow/*/Player.log"))

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
        self._update_steam_button()
        games = SteamScanner.scan_games(self.prefix_path, self.steam_dir)
        self.games = games
        self.games_list.clear()

        while self.games_flow_layout.count():
            item = self.games_flow_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

       
        if not hasattr(self, "_steam_sidebar_btn") or self._steam_sidebar_btn is None:
            steam_icon = Path(__file__).resolve().with_name("Steam.png")
            self._steam_sidebar_btn = self._add_sidebar_container("Steam", steam_icon)
            self._steam_sidebar_btn.clicked.connect(lambda: self._on_steam_container_clicked())
            self._steam_sidebar_btn.setChecked(True)

        for game in games:
            item = QListWidgetItem(game.display())
            item.setData(256, game)
            self.games_list.addItem(item)
            card = self.create_game_card(game)
            self.games_flow_layout.addWidget(card)

        if games:
            self.stacked_widget.setCurrentIndex(0)
        else:
            self.stacked_widget.setCurrentIndex(1)

        self.set_status(f"Found {len(games)} installed game(s)")

    def _on_steam_container_clicked(self) -> None:
        if self.games:
            self.stacked_widget.setCurrentIndex(0)
        else:
            self.stacked_widget.setCurrentIndex(1)
            
    def _switch_to_bottle(self, path: str) -> None:
        self.prefix_combo.setCurrentText(path)
        self.scan_games()

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
                QMessageBox.warning(self, APP_NAME, f"Missing {dll} in {dxvk_bin}. Install DXVK first.")
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

        for tdir in sorted(target_dirs):
            for dll in DXVK_DLLS:
                shutil.copy2(dxvk_bin / dll, tdir / dll)
            self.log(f"Copied {', '.join(DXVK_DLLS)} -> {tdir}")

        self.set_status(f"Patched {game.name} with local DXVK")


    def launch_selected_game(self, game: Optional["GameEntry"] = None, backend_id: Optional[str] = None, extra_args: str = "") -> None:
        game = game or self.selected_game()
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
        is_steam_game = bool(game.appid)
        if is_steam_game:
            if not self.steam_process or self.steam_process.state() == QProcess.ProcessState.NotRunning:
                self.log("Steam is not running but required for this game. Launching Steam first...")
                self.launch_steam()
  

        game_model = self.selected_game_model(game)
        if game_model is None:
            QMessageBox.warning(self, APP_NAME, "Failed to build game model.")
            return
        prefix_model = self.current_prefix_model()

        if sender := self.sender():
            if isinstance(sender, QAction):
                backend_id = sender.data() or backend_id
            elif isinstance(sender, QPushButton) and sender.parent():
                overlay = sender.parent()
                for child in overlay.children():
                    if isinstance(child, QComboBox):
                        backend_id = child.currentData() or backend_id
                        break

        resolved_backend = self.resolve_backend(backend_id, game_model, prefix_model)
        effective_backend = resolved_backend.backend_id
        effective_mesa_driver = ""
        try:
            prepare_info = resolved_backend.prepare_game(prefix_model, game_model, self)
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return

        wine_bin = self.wine_binary()
        self.log(f"Requested backend: {backend_id}")
        self.log(f"Resolved backend: {effective_backend}")
        self.log(f"Runner binary: {wine_bin or 'NONE'}")
        if effective_backend == LAUNCH_BACKEND_GPTK:
            self.log(f"GPTK DLL dir: {self.gptk_windows_dir}")

        effective_backend = resolved_backend.backend_id
        effective_mesa_driver = ""
        if isinstance(prepare_info, dict):
            effective_backend = str(prepare_info.get("kind", effective_backend)) if prepare_info.get("kind") in {"dxvk", "mesa"} else effective_backend
            effective_mesa_driver = str(prepare_info.get("driver", ""))
            if prepare_info.get("kind") == "mesa":
                effective_backend = resolved_backend.backend_id
                effective_mesa_driver = prepare_info.get("driver", "")
            elif prepare_info.get("kind") == "dxvk":
                effective_backend = LAUNCH_BACKEND_DXVK
            elif prepare_info.get("kind") == "vkd3d-proton":
                effective_backend = LAUNCH_BACKEND_VKD3D
            elif prepare_info.get("kind") == "dxmt":
                effective_backend = LAUNCH_BACKEND_DXMT
            elif prepare_info.get("kind") == "gptk":
                effective_backend = LAUNCH_BACKEND_GPTK

        wine_bin = self.wine_binary()

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
                f"Started {game.name} ({'Mesa ' + effective_mesa_driver if self.backend_is_mesa(effective_backend) else ('VKD3D-Proton' if effective_backend == LAUNCH_BACKEND_VKD3D else ('DXMT' if effective_backend == LAUNCH_BACKEND_DXMT else ('DXVK' if effective_backend == LAUNCH_BACKEND_DXVK else 'Wine builtin')))})"
            )
        )
        self.game_process.errorOccurred.connect(lambda e: self.set_status(f"Game error: {e}"))

        def _on_game_finished(code, status) -> None:
            self.set_status(f"{game.name} exited with code {code}")

            if effective_backend == LAUNCH_BACKEND_DXVK:
                log_path = self._latest_dxvk_log_for_game(game)
                if log_path and log_path.exists():
                    try:
                        text = log_path.read_text(encoding="utf-8", errors="ignore")
                        lines = text.splitlines()
                        tail = "\n".join(lines[-200:]) if lines else "(log is empty)"
                        self.log(f"--- DXVK log: {log_path.name} (last {min(200, len(lines))} lines) ---")
                        for line in tail.splitlines():
                            self.log(line)
                    except Exception as exc:
                        self.log(f"Failed to read DXVK log {log_path}: {exc}")

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
                log_path = self.latest_unity_player_log_for_game(game)
                if log_path and log_path.exists():
                    try:
                        text = log_path.read_text(encoding="utf-8", errors="ignore")
                        lines = text.splitlines()
                        tail = "\n".join(lines[-200:]) if lines else "(log is empty)"
                        self.log(f"--- Unity Player.log: {log_path} (last {min(200, len(lines))} lines) ---")
                        for line in tail.splitlines():
                            self.log(line)
                    except Exception as exc:
                        self.log(f"Failed to read Unity log {log_path}: {exc}")

        self.game_process.finished.connect(_on_game_finished)
        self.game_process.start()


    def closeEvent(self, event) -> None:
        # Kill tracked Qt processes first
        for proc in (self.game_process, self.steam_process):
            if proc and proc.state() != QProcess.ProcessState.NotRunning:
                proc.kill()
                proc.waitForFinished(2000)

        # Shut down the entire Wine server for this prefix — terminates all
        # remaining Wine processes (winedevice, services, explorer, etc.) cleanly.
        try:
            import subprocess
            wineserver = self.wineserver_binary()
            env = self.wine_env()
            subprocess.run([wineserver, "-k"], env=env, timeout=5)
        except Exception:
            pass

        super().closeEvent(event)


def request_microphone_permission() -> bool:
    if sys.platform != "darwin":
        return True

    try:
        status = AV.AVCaptureDevice.authorizationStatusForMediaType_(AV.AVMediaTypeAudio)
        if status == 0:
            AV.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                AV.AVMediaTypeAudio, lambda granted: None
            )
            return False
        return status == 3
    except Exception:
        return False


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(MODERN_THEME)
    win = MainWindow()
    request_microphone_permission()
    win.show()
    win.apply_ui_modes()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
