from __future__ import annotations

import os
import shlex
import stat
import subprocess
import tempfile
from pathlib import Path

from PyQt6.QtWidgets import QMessageBox

from constants import APP_NAME, DEFAULT_MESA_URL, DXVK_PREBUILT_URL


class InstallerOps:
    def _prompt_admin_env(self) -> dict[str, str] | None:
        """Show a native macOS password dialog and return an env dict with SUDO_ASKPASS set.

        Returns None if the user cancels.
        """
        result = subprocess.run(
            [
                "osascript", "-e",
                'display dialog "MacNCheese needs your administrator password to install Wine." '
                'default answer "" with hidden answer '
                'with title "Administrator Password" '
                'buttons {"Cancel", "OK"} default button "OK"',
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or "text returned:" not in result.stdout:
            return None

        password = result.stdout.strip().split("text returned:", 1)[1].strip()

        fd, askpass_path = tempfile.mkstemp(suffix=".sh", prefix="macncheese_")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(password)}\n")
            os.chmod(askpass_path, stat.S_IRWXU)
        except Exception:
            return None

        self._askpass_path: str | None = askpass_path
        env = os.environ.copy()
        env["SUDO_ASKPASS"] = askpass_path
        return env
    def install_tools(self) -> None:
        self.run_commands(
            [["bash", "-lc", "brew install git meson ninja mingw-w64 glslang p7zip winetricks"]]
        )

    def install_wine(self) -> None:
        env = self._prompt_admin_env()
        if env is None:
            return
        self.run_commands(
            [["bash", "-lc", "brew install --cask xquartz || true; brew install --cask wine-stable || brew install wine-stable"]],
            env=env,
        )

    def install_mesa(self) -> None:
        url = DEFAULT_MESA_URL
        mesa_dir = self.mesa_dir
        mesa_base = mesa_dir.parent
        archive = mesa_base.parent / "mesa.7z"
        commands: list[list[str]] = [
            ["bash", "-lc", "brew install p7zip || true"],
            [
                "bash",
                "-lc",
                (
                    "set -euo pipefail; "
                    f"rm -rf {shlex.quote(str(mesa_base))} {shlex.quote(str(archive))}; "
                    f"mkdir -p {shlex.quote(str(mesa_base))}; "
                    f"curl -L -o {shlex.quote(str(archive))} {shlex.quote(url)}; "
                    f"7z x {shlex.quote(str(archive))} -o{shlex.quote(str(mesa_base))} >/dev/null; "
                    f"if [ ! -d {shlex.quote(str(mesa_dir))} ]; then "
                    f"  sub=$(ls -1 {shlex.quote(str(mesa_base))} | grep mesa3d- | head -n1); "
                    f"  if [ -n \"$sub\" ] && [ -d {shlex.quote(str(mesa_base))}/$sub/x64 ]; then "
                    f"    cp -R {shlex.quote(str(mesa_base))}/$sub/x64 {shlex.quote(str(mesa_dir))}; "
                    f"  fi; "
                    f"fi"
                ),
            ],
        ]
        self.run_commands(commands)

    def install_dxvk(self) -> None:
        install64 = self.dxvk_install
        install32 = self.dxvk_install32
        bin64 = install64 / "bin"
        bin32 = install32 / "bin"
        deps_dir = install64.parent.parent / "deps"
        archive = deps_dir / "dxvk.tar.gz"
        extract_dir = deps_dir / "dxvk-prebuilt"

        script = (
            "set -euo pipefail; "
            f"mkdir -p {shlex.quote(str(deps_dir))} {shlex.quote(str(bin64))} {shlex.quote(str(bin32))}; "
            f"curl -L -o {shlex.quote(str(archive))} {shlex.quote(DXVK_PREBUILT_URL)}; "
            f"rm -rf {shlex.quote(str(extract_dir))}; "
            f"mkdir -p {shlex.quote(str(extract_dir))}; "
            f"tar -xzf {shlex.quote(str(archive))} -C {shlex.quote(str(extract_dir))} --strip-components=1; "
            f"cp {shlex.quote(str(extract_dir))}/x64/*.dll {shlex.quote(str(bin64))}/; "
            f"cp {shlex.quote(str(extract_dir))}/x32/*.dll {shlex.quote(str(bin32))}/; "
            f"rm -rf {shlex.quote(str(archive))} {shlex.quote(str(extract_dir))}"
        )
        self.run_commands([["bash", "-lc", script]])

    def quick_setup(self) -> None:
        env = self._prompt_admin_env()
        if env is None:
            return

        install64 = self.dxvk_install
        install32 = self.dxvk_install32
        bin64 = install64 / "bin"
        bin32 = install32 / "bin"
        mesa_dir = self.mesa_dir
        mesa_base = mesa_dir.parent
        mesa_archive = mesa_base.parent / "mesa.7z"
        deps_dir = install64.parent.parent / "deps"
        dxvk_archive = deps_dir / "dxvk.tar.gz"
        dxvk_extract = deps_dir / "dxvk-prebuilt"

        script = (
            "set -euo pipefail; "
            "brew install p7zip winetricks || true; "
            "brew install --cask xquartz || true; "
            "brew install --cask wine-stable || brew install wine-stable || true; "
            f"mkdir -p {shlex.quote(str(deps_dir))} {shlex.quote(str(bin64))} {shlex.quote(str(bin32))}; "
            f"curl -L -o {shlex.quote(str(dxvk_archive))} {shlex.quote(DXVK_PREBUILT_URL)}; "
            f"rm -rf {shlex.quote(str(dxvk_extract))}; "
            f"mkdir -p {shlex.quote(str(dxvk_extract))}; "
            f"tar -xzf {shlex.quote(str(dxvk_archive))} -C {shlex.quote(str(dxvk_extract))} --strip-components=1; "
            f"cp {shlex.quote(str(dxvk_extract))}/x64/*.dll {shlex.quote(str(bin64))}/; "
            f"cp {shlex.quote(str(dxvk_extract))}/x32/*.dll {shlex.quote(str(bin32))}/; "
            f"rm -rf {shlex.quote(str(dxvk_archive))} {shlex.quote(str(dxvk_extract))}; "
            f"rm -rf {shlex.quote(str(mesa_base))} {shlex.quote(str(mesa_archive))}; "
            f"mkdir -p {shlex.quote(str(mesa_base))}; "
            f"curl -L -o {shlex.quote(str(mesa_archive))} {shlex.quote(DEFAULT_MESA_URL)}; "
            f"7z x {shlex.quote(str(mesa_archive))} -o{shlex.quote(str(mesa_base))} >/dev/null; "
            f"if [ ! -d {shlex.quote(str(mesa_dir))} ]; then "
            f"  sub=$(ls -1 {shlex.quote(str(mesa_base))} | grep mesa3d- | head -n1); "
            f"  if [ -n \"$sub\" ] && [ -d {shlex.quote(str(mesa_base))}/$sub/x64 ]; then "
            f"    cp -R {shlex.quote(str(mesa_base))}/$sub/x64 {shlex.quote(str(mesa_dir))}; "
            f"  fi; "
            f"fi"
        )
        self.run_commands([["bash", "-lc", script]], env=env)

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
