#!/usr/bin/env python3
from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
