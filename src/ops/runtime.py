from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QProcess, QProcessEnvironment
from PyQt6.QtWidgets import QListWidgetItem, QMessageBox

from constants import (
    APP_NAME,
    DXVK_DLLS,
    LAUNCH_BACKEND_AUTO,
    LAUNCH_BACKEND_DXVK,
    LAUNCH_BACKEND_MESA_LLVMPIPE,
    LOGS_DIR,
    MESA_DRIVER_LLVMPIPE,
    MESA_DRIVER_ZINK,
    MESA_DRIVER_SWR,
)
from models import GameEntry, SteamScanner


class RuntimeOps:
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
                "Fix: click 'Install Mesa' in Settings > Setup, or set 'Mesa x64 dir' to the folder that contains those DLLs (usually ~/mesa/x64)."
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

        exe = game.detect_exe()
        dxvk_bin = self.dxvk_bin_for_exe(exe) if exe is not None else (self.dxvk_install / "bin")
        for dll in DXVK_DLLS:
            if not (dxvk_bin / dll).exists():
                QMessageBox.warning(self, APP_NAME, f"Missing {dll} in {dxvk_bin}. Build DXVK first.")
                return

        game.game_dir.mkdir(parents=True, exist_ok=True)

        target_dirs: set[Path] = {game.game_dir}
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
        logs_dir = LOGS_DIR
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
            try:
                root_exes = sorted(game.game_dir.glob("*.exe"))
                sub_exes = sorted(
                    list(game.game_dir.glob("*/*.exe")) + list(game.game_dir.glob("*/*/*.exe"))
                )
                shown = [str(p.relative_to(game.game_dir)) for p in (root_exes + sub_exes)[:20]]
            except Exception:
                shown = []
            hint = "No EXE detected. Some games use a launcher or store the EXE in a subfolder."
            if shown:
                hint += "\n\nEXEs found (first 20):\n" + "\n".join(shown)
            QMessageBox.warning(self, APP_NAME, f"{hint}\n\nFolder: {game.game_dir}")
            return

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
            env["DXVK_LOG_PATH"] = str(LOGS_DIR)
            env["DXVK_LOG_LEVEL"] = "info"
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
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
        extra = self.game_args_edit.text().strip()
        if extra:
            args += extra.split()

        if self.is_unity_game(game):
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", game.install_dir_name or game.name)
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            unity_log = str(LOGS_DIR / f"{safe_name}-player.log")
            args += ["-logFile", unity_log]
            self.log(f"Unity log file will be written to: {unity_log}")

        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", game.install_dir_name or game.name)
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        host_wine_log = str(LOGS_DIR / f"{safe_name}-wine.log")
        self.log(f"Wine output will be written to: {host_wine_log}")
        self.last_game_launch_ts[game.appid] = time.time()
        self.last_game_wine_log[game.appid] = Path(host_wine_log)

        debug_prefix = "WINEDEBUG=+loaddll"
        if self.backend_is_mesa(effective_backend):
            debug_prefix = "WINEDEBUG=+loaddll,+wgl,+opengl"
        cmd = (
            f"cd {shlex.quote(str(exe_dir))} && {debug_prefix} {shlex.quote(wine)} "
            f"{' '.join(shlex.quote(a) for a in args)} > {shlex.quote(host_wine_log)} 2>&1"
        )
        self.game_process.setProgram("bash")
        self.game_process.setArguments(["-lc", cmd])
        self.game_process.readyReadStandardOutput.connect(lambda: self._drain_process(self.game_process))
        self.game_process.readyReadStandardError.connect(lambda: self._drain_process(self.game_process))
        self.game_process.started.connect(
            lambda: self.set_status(
                f"Started {game.name} "
                f"({'Mesa ' + effective_mesa_driver if self.backend_is_mesa(effective_backend) else ('DXVK' if effective_backend == LAUNCH_BACKEND_DXVK else 'Wine builtin')})"
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
