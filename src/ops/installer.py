from __future__ import annotations

import os
import shlex
import stat
import subprocess
import tempfile
from pathlib import Path

from PyQt6.QtWidgets import QMessageBox

from constants import APP_NAME, DEFAULT_MESA_URL, DXVK_MACOS_REPO


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

    def clone_dxvk(self) -> None:
        src = self.dxvk_src
        src.parent.mkdir(parents=True, exist_ok=True)
        script = (
            f"if [ -d {shlex.quote(str(src / '.git'))} ]; then "
            f"  git -C {shlex.quote(str(src))} pull; "
            f"else "
            f"  git clone {shlex.quote(DXVK_MACOS_REPO)} {shlex.quote(str(src))}; "
            f"fi"
        )
        self.run_commands([["bash", "-lc", script]])

    def quick_setup(self) -> None:
        src = self.dxvk_src
        cross64 = src / "build-win64.txt"
        cross32 = src / "build-win32.txt"
        install64 = self.dxvk_install
        install32 = self.dxvk_install32
        mesa_dir = self.mesa_dir
        mesa_base = mesa_dir.parent
        mesa_archive = mesa_base.parent / "mesa.7z"

        src.parent.mkdir(parents=True, exist_ok=True)

        script = (
            "set -euo pipefail; "
            "brew install git meson ninja mingw-w64 glslang p7zip winetricks || true; "
            "brew install --cask xquartz || true; "
            "brew install --cask wine-stable || brew install wine-stable || true; "
            f"if [ -d {shlex.quote(str(src / '.git'))} ]; then "
            f"  git -C {shlex.quote(str(src))} pull; "
            f"else "
            f"  git clone {shlex.quote(DXVK_MACOS_REPO)} {shlex.quote(str(src))}; "
            f"fi; "
            f"mkdir -p {shlex.quote(str(install64))} {shlex.quote(str(install32))}; "
            f"rm -rf {shlex.quote(str(install64 / 'build.64'))} {shlex.quote(str(install32 / 'build.32'))}; "
            f"meson setup {shlex.quote(str(install64 / 'build.64'))} {shlex.quote(str(src))} --cross-file {shlex.quote(str(cross64))} --prefix {shlex.quote(str(install64))} --buildtype release -Denable_d3d9=false; "
            f"ninja -C {shlex.quote(str(install64 / 'build.64'))}; "
            f"ninja -C {shlex.quote(str(install64 / 'build.64'))} install; "
            f"meson setup {shlex.quote(str(install32 / 'build.32'))} {shlex.quote(str(src))} --cross-file {shlex.quote(str(cross32))} --prefix {shlex.quote(str(install32))} --buildtype release -Denable_d3d9=false; "
            f"ninja -C {shlex.quote(str(install32 / 'build.32'))}; "
            f"ninja -C {shlex.quote(str(install32 / 'build.32'))} install; "
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

        self.run_commands([["bash", "-lc", script]], env=None, cwd=str(src.parent))

    def _build_dxvk(self, *, arch: str) -> None:
        wine = self.ensure_wine()
        if not wine:
            return

        src = self.dxvk_src
        if arch == "win64":
            install = self.dxvk_install
            cross_file = src / "build-win64.txt"
            build_dir = install / "build.64"
        else:
            install = self.dxvk_install32
            cross_file = src / "build-win32.txt"
            build_dir = install / "build.32"

        install.mkdir(parents=True, exist_ok=True)
        src.parent.mkdir(parents=True, exist_ok=True)
        coredata = build_dir / "meson-private" / "coredata.dat"

        clone_script = (
            f"if [ -d {shlex.quote(str(src / '.git'))} ]; then "
            f"  git -C {shlex.quote(str(src))} pull; "
            f"else "
            f"  git clone {shlex.quote(DXVK_MACOS_REPO)} {shlex.quote(str(src))}; "
            f"fi"
        )

        meson = self.meson_binary()
        ninja = self.ninja_binary()

        meson_args = [
            meson, "setup",
            str(build_dir), str(src),
            "--cross-file", str(cross_file),
            "--prefix", str(install),
            "--buildtype", "release",
            "-Denable_d3d9=false",
        ]

        if build_dir.exists():
            if coredata.exists():
                meson_args.append("--reconfigure")
            else:
                meson_args.append("--wipe")

        commands = [
            ["bash", "-lc", clone_script],
            meson_args,
            [ninja, "-C", str(build_dir)],
            [ninja, "-C", str(build_dir), "install"],
        ]

        env = os.environ.copy()
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "")

        self.log(f"Building DXVK ({arch}) in: {build_dir}")
        self.run_commands(commands, env=env, cwd=str(src.parent))

    def build_dxvk(self) -> None:
        self._build_dxvk(arch="win64")

    def build_dxvk32(self) -> None:
        self._build_dxvk(arch="win32")

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
