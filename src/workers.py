from __future__ import annotations

import os
import subprocess

from PyQt6.QtCore import QObject, pyqtSignal


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
