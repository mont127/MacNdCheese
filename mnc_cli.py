#!/usr/bin/env python3
"""macndcheese CLI -- an interactive terminal client for the MacNdCheese backend.

MacNdCheese is a SwiftUI app talkin to backend_server.py over a line-JSON protocol:
one  {"id": N, "cmd": name, ..params}  per line goes IN, one
     {"id": N, "ok": true, "data": ..}  (or {"ok": false, "error": ..})  per line comes OUT.

run it with NO args + you get an interactive shell (a REPL) that keeps ONE backend alive
the hole session -- so state sticks around: launch steam, then `status` sees it, `kill`
stops it, + you `use` a bottle once insted of retypin its long path. run it WITH a
subcommand + it does that one thing + exits (handy for scriptin).

  ./mnc_cli.py                       # interactive shell
  ./mnc_cli.py bottles               # one-shot
  ./mnc_cli.py raw scan_games prefix=/path/to/Bottle
"""
from __future__ import annotations
import argparse
import atexit
import itertools
import json
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(HERE, "backend_server.py")
HISTFILE = os.path.expanduser("~/.mnc_history")

# ---- tiny ansi paint (only when we're a real terminal) --------------------------
_TTY = sys.stdout.isatty()
_BOLD, _DIM, _RED, _GRN, _YEL, _CYN = "1", "2", "31", "32", "33", "36"


def _paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TTY else text


# ---- the backend pipe -----------------------------------------------------------
class BackendError(RuntimeError):
    """raised when the backend anwsers ok=false."""


class Backend:
    """spawns backend_server.py once + does request/reply, allways matchin our OWN id
    back out of the stream (scan_games/scan_apps run on a worker so there replys can
    land out of order -- we cant just take the next line)."""

    def __init__(self, verbose: bool = False) -> None:
        if not os.path.exists(BACKEND):
            sys.exit(f"cant find backend_server.py next to the cli (looked in {HERE})")
        self._nextid = itertools.count(1)
        errto = None if verbose else subprocess.DEVNULL  # backend logs -> stderr, hide em
        self._proc = subprocess.Popen(
            [sys.executable, BACKEND],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errto,
            text=True, bufsize=1, cwd=HERE,
        )

    def call(self, cmd: str, params: "dict | None" = None):
        if self._proc.poll() is not None:
            raise BackendError("the backend has died -- restart the shell")
        rid = next(self._nextid)
        req = {"id": rid, "cmd": cmd}
        if params:
            req.update(params)
        assert self._proc.stdin and self._proc.stdout
        self._proc.stdin.write(json.dumps(req) + "\n")
        self._proc.stdin.flush()
        while True:
            raw = self._proc.stdout.readline()
            if not raw:
                raise BackendError("backend closed the pipe befor replyin (it may of crashd -- try --verbose)")
            raw = raw.strip()
            if not raw:
                continue
            try:
                resp = json.loads(raw)
            except json.JSONDecodeError:
                continue  # not our json -- skip
            if resp.get("id") != rid:
                continue  # a diffrent commands reply -- keep readin
            if resp.get("ok"):
                return resp.get("data")
            raise BackendError(resp.get("error") or "unknown backend error")

    def close(self) -> None:
        try:
            if self._proc.stdin:
                self._proc.stdin.close()   # eof lets the backend main loop end cleanly
            self._proc.wait(timeout=5)
        except Exception:
            self._proc.kill()


# ---- shell state (persists accross commands in the REPL) ------------------------
class State:
    def __init__(self) -> None:
        self.prefix: "str | None" = None   # the currently-selected bottle path
        self.name: "str | None" = None     # ..and its name (for the prompt)
        self.bottles: list = []            # last-fetched bottle list, so `use N` works


# ---- helpers --------------------------------------------------------------------
def _known_cmds() -> "list[str]":
    """scrape the cmd_* names outa backend_server.py so the list cant drift."""
    try:
        with open(BACKEND, encoding="utf-8", errors="ignore") as fh:
            src = fh.read()
    except OSError:
        return []
    return sorted(set(re.findall(r"^def cmd_([a-z0-9_]+)", src, re.M)))


def _emit(data, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, default=str))
    elif data is None:
        print(_paint("ok", _GRN))
    elif isinstance(data, (dict, list)):
        print(json.dumps(data, indent=2, default=str))
    else:
        print(data)


def _parse_kv(pairs: "list[str]") -> dict:
    """key=value tokens -> params. values get json-parsed when they can (true/200/[..]),
    otherwise kept as a plain string."""
    out: dict = {}
    for tok in pairs:
        if "=" not in tok:
            raise BackendError(f"bad param {tok!r} -- expektd key=value")
        key, val = tok.split("=", 1)
        try:
            out[key] = json.loads(val)
        except json.JSONDecodeError:
            out[key] = val
    return out


def _show_bottles(data, state: State) -> None:
    """print bottles as a numberd list + remember it so `use N` can pick one."""
    state.bottles = data if isinstance(data, list) else []
    if not state.bottles:
        print(_paint("no bottles yet", _DIM))
        return
    for i, b in enumerate(state.bottles):
        star = _paint("*", _GRN) if b.get("path") == state.prefix else " "
        name = _paint(b.get("name", "?"), _BOLD)
        be = _paint(b.get("default_backend", "auto"), _CYN)
        print(f" {star}[{i}] {name}  ({be})  {_paint(b.get('path',''), _DIM)}")


def _resolve_prefix(ns, state: State) -> str:
    """the prefix an arg-takin command should use: the --prefix given, else the bottle
    you `use`d, else yell about it."""
    pfx = getattr(ns, "prefix", None) or state.prefix
    if not pfx:
        raise BackendError("no bottle selected -- pass --prefix, or in the shell run `use <n>` first")
    return pfx


def _select(state: State, backend: Backend, token: str) -> None:
    """`use N` or `use <name>` -- point the shell at a bottle."""
    if not state.bottles:
        state.bottles = backend.call("list_bottles") or []
    chosen = None
    if token.isdigit() and int(token) < len(state.bottles):
        chosen = state.bottles[int(token)]
    else:
        for b in state.bottles:
            if b.get("name", "").lower() == token.lower():
                chosen = b
                break
    if not chosen:
        print(_paint(f"no bottle {token!r} (try `bottles` to see the numberd list)", _RED))
        return
    state.prefix, state.name = chosen.get("path"), chosen.get("name")
    print(f"using {_paint(state.name or '?', _YEL)}  {_paint(state.prefix or '', _DIM)}")


# ---- the argparse parser (shared by one-shot + the REPL) ------------------------
def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="mnc", add_help=True,
                                 description="terminal client for the MacNdCheese backend")
    ap.add_argument("--verbose", action="store_true", help="show the backends stderr logs")
    ap.add_argument("--json", action="store_true", dest="as_json", help="print the raw json reply")
    sub = ap.add_subparsers(dest="what")   # NOT required -- no subcommand => interactive shell

    for name, cmd, helptxt in (
        ("bottles",    "list_bottles",          "list the wine bottles / prefixes"),
        ("status",     "get_status",            "backend + wine status"),
        ("backends",   "list_backends",         "the graphics backends (d3dmetal / dxvk / ..)"),
        ("running",    "get_running_games",     "the games runnin right now"),
        ("components", "get_components_status", "wich runtime bits are installd"),
        ("wine",       "detect_wine",           "the wine builds the backend can see"),
    ):
        sp = sub.add_parser(name, help=helptxt)
        sp.set_defaults(_cmd=cmd, _mode="static")

    for name, cmd in (("scan-games", "scan_games"), ("scan-apps", "scan_apps")):
        sp = sub.add_parser(name, help=f"{cmd.replace('_', ' ')} in the selected/gave prefix")
        sp.add_argument("--prefix", help="the bottle/prefix path (defaults to the `use`d one)")
        sp.set_defaults(_cmd=cmd, _mode="prefix")

    sp = sub.add_parser("launch-steam", help="start Steam inside the selected/gave prefix")
    sp.add_argument("--prefix")
    sp.add_argument("--backend", default="auto", help="graphics backend (auto/d3dmetal/dxvk/..)")
    sp.add_argument("--silent", action="store_true", help="start steam silent/minimized")
    sp.add_argument("--wait", action="store_true", dest="wait_ready", help="block til steam is [Logged On]")
    sp.add_argument("--wait-cap", type=int, default=240, dest="ready_cap_s", help="max secs to wait")
    sp.set_defaults(_cmd="launch_steam", _mode="prefix",
                    _extra=("backend", "silent", "wait_ready", "ready_cap_s"))

    sp = sub.add_parser("launch-game", help="launch a game exe inside the selected/gave prefix")
    sp.add_argument("--prefix")
    sp.add_argument("--exe", required=True, help="the game exe (windows or unix path)")
    sp.add_argument("--backend", default="auto")
    sp.add_argument("--args", default="", help="extra args to hand the exe")
    sp.set_defaults(_cmd="launch_game", _mode="prefix", _extra=("exe", "backend", "args"))

    sp = sub.add_parser("kill", help="kill the wineserver (stops everything in the prefix)")
    sp.set_defaults(_cmd="kill_wineserver", _mode="static")

    sp = sub.add_parser("commands", help="print every raw backend cmd you can call")
    sp.set_defaults(_cmd=None, _mode="commands")

    sp = sub.add_parser("raw", help="call any backend cmd directly:  raw CMD [key=value ..]")
    sp.add_argument("cmd")
    sp.add_argument("kv", nargs="*")
    sp.set_defaults(_cmd=None, _mode="raw")

    return ap


def execute(ns, backend: Backend, state: State) -> None:
    """run one parsed command against the backend + print the reply. shared by both modes."""
    mode = getattr(ns, "_mode")
    as_json = ns.as_json

    if mode == "commands":
        cmds = _known_cmds()
        print(f"{len(cmds)} backend commands -- reach any with `raw <name> key=value ..`\n")
        print("  " + "\n  ".join(cmds))
        return

    if mode == "raw":
        _emit(backend.call(ns.cmd, _parse_kv(ns.kv)), as_json)
        return

    if mode == "static":
        data = backend.call(ns._cmd)
        if ns._cmd == "list_bottles" and not as_json:
            _show_bottles(data, state)
        else:
            _emit(data, as_json)
        return

    # mode == "prefix": needs a bottle + maybe extra flags
    params = {"prefix": _resolve_prefix(ns, state)}
    for key in getattr(ns, "_extra", ()):  # backend/silent/wait_ready/exe/args/..
        params[key] = getattr(ns, key)
    _emit(backend.call(ns._cmd, params), as_json)


# ---- the interactive shell ------------------------------------------------------
_REPL_HELP = """interactive commands:
  bottles                 list bottles (numberd)      status      backend/wine status
  use <n|name>            pick a bottle for later      backends    graphics backends
  scan-games [--prefix P] find games in the bottle     running     games runnin now
  launch-steam [--wait]   start steam in the bottle    components  installd runtime bits
  launch-game --exe E     launch a game exe            wine        wine builds seen
  kill                    kill the wineserver          commands    all 59 raw cmds
  raw CMD key=value ..    call any backend cmd
  help                    this               clear   wipe screen        exit / quit   leave
tip: `use 0` once, then most commands just work on that bottle withuot --prefix."""


def _install_readline(state: State, subcmds: "list[str]") -> None:
    try:
        import readline
    except ImportError:
        return
    try:
        readline.read_history_file(HISTFILE)
    except OSError:
        pass
    atexit.register(lambda: _save_history(readline))
    words = subcmds + ["use", "help", "clear", "exit", "quit"]
    raw_cmds = _known_cmds()

    def complete(text, i):
        line = readline.get_line_buffer()
        toks = line.split()
        if not toks or (len(toks) == 1 and not line.endswith(" ")):
            opts = [w for w in words if w.startswith(text)]
        elif toks[0] == "raw":
            opts = [c for c in raw_cmds if c.startswith(text)]
        elif toks[0] == "use":
            opts = [b.get("name", "") for b in state.bottles if b.get("name", "").startswith(text)]
        else:
            opts = []
        return opts[i] if i < len(opts) else None

    readline.set_completer(complete)
    readline.set_completer_delims(" \t")
    # macOS system python ships libedit, not gnu readline -- the bind syntax differs
    if "libedit" in (getattr(readline, "__doc__", "") or ""):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")


def _save_history(readline) -> None:
    try:
        readline.set_history_length(1000)
        readline.write_history_file(HISTFILE)
    except OSError:
        pass


def repl(parser: argparse.ArgumentParser, backend: Backend, state: State) -> None:
    subcmds = list(parser._subparsers._group_actions[0].choices.keys()) if parser._subparsers else []
    _install_readline(state, subcmds)
    print(_paint("macndcheese shell", _BOLD) + _paint("  --  type `help`, or `commands` for the raw list, `exit` to leave", _DIM))
    while True:
        tag = f"[{state.name}]" if state.name else ""
        try:
            line = input(f"mnc{tag}> ").strip()
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print("  (^C -- type `exit` to leave)")
            continue
        if not line:
            continue
        try:
            toks = shlex.split(line)
        except ValueError as exc:
            print(_paint(f"parse error: {exc}", _RED))
            continue
        head = toks[0]
        if head in ("exit", "quit", "q"):
            break
        if head in ("help", "?"):
            print(_REPL_HELP)
            continue
        if head == "clear":
            os.system("clear")
            continue
        if head == "use":
            if len(toks) < 2:
                print(_paint("usage: use <number|name>", _RED))
            else:
                try:
                    _select(state, backend, toks[1])
                except BackendError as exc:
                    print(_paint(f"error: {exc}", _RED))
            continue
        # everything else goes thru the same argparse the one-shot mode uses
        try:
            ns = parser.parse_args(toks)
        except SystemExit:
            continue  # argparse allready printed the usage/error
        if getattr(ns, "what", None) is None:
            continue
        try:
            execute(ns, backend, state)
        except BackendError as exc:
            print(_paint(f"error: {exc}", _RED))


def main() -> None:
    parser = _build_parser()
    ns = parser.parse_args()
    interactive = getattr(ns, "what", None) is None
    backend = Backend(verbose=ns.verbose)
    try:
        if interactive:
            repl(parser, backend, State())
        else:
            execute(ns, backend, State())
    except BackendError as exc:
        sys.exit(f"error: {exc}")
    finally:
        backend.close()


if __name__ == "__main__":
    main()
