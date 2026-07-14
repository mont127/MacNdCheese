#!/usr/bin/env python3
"""CLI parity check -- see CONTRIBUTING.md's "CLI parity" section.

Sources/BackendClient.swift and ./macndcheese are two independent clients of
backend_server.py's JSON-RPC commands. Nothing enforces they stay in sync, so
they drift: run_installer/get_install_progress were Swift-only for a long
time before `engines install/uninstall/reinstall` gave the CLI a real
equivalent. This script catches the mechanically-detectable half of that --
a backend command Swift calls that the CLI never references at all.

What it can't catch: a NEW UI entry point (button, menu item) that calls an
EXISTING, already-covered-by-neither command -- that's a judgment call, not
a string match, and it's why CONTRIBUTING.md also asks reviewers to check by
hand. This script is the mechanical backstop, not the whole answer.

Algorithm: diff two sets of command names extracted by regex, allowlist the
pre-existing backlog (recorded once, when this check was introduced) so CI
doesn't fail on years of history -- only NEW gaps fail the build. Move a
command's name off the allowlist once the CLI covers it.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SWIFT_CLIENT = ROOT / "Sources" / "BackendClient.swift"
CLI_SCRIPT = ROOT / "macndcheese"
ALLOWLIST = ROOT / ".github" / "cli-parity-allowlist.txt"

SWIFT_CALL_RE = re.compile(r'send\(cmd:\s*"([a-z0-9_]+)"')
CLI_CALL_RE = re.compile(r'backend\.call\(\s*"([a-z0-9_]+)"')


def read_allowlist() -> set[str]:
    if not ALLOWLIST.exists():
        return set()
    names = set()
    for line in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.add(line)
    return names


def main() -> int:
    swift_cmds = set(SWIFT_CALL_RE.findall(SWIFT_CLIENT.read_text(encoding="utf-8")))
    cli_cmds = set(CLI_CALL_RE.findall(CLI_SCRIPT.read_text(encoding="utf-8")))
    allowed = read_allowlist()

    gap = swift_cmds - cli_cmds
    new_violations = sorted(gap - allowed)
    stale_allowlist_entries = sorted(allowed - gap)

    if stale_allowlist_entries:
        print("Note: these allowlist entries are no longer part of the gap (the CLI")
        print("covers them now, or Swift stopped calling them) -- feel free to delete")
        print(f"them from {ALLOWLIST.relative_to(ROOT)}:")
        for cmd in stale_allowlist_entries:
            print(f"  - {cmd}")
        print()

    if not new_violations:
        print("CLI parity check passed.")
        return 0

    print("CLI parity check FAILED.")
    print()
    print("Sources/BackendClient.swift calls backend command(s) that ./macndcheese")
    print("never references, and they aren't in the pre-existing-backlog allowlist:")
    for cmd in new_violations:
        print(f"  - {cmd}")
    print()
    print("Either add a friendly CLI command for it (see how `engines install/")
    print("uninstall/reinstall` wrap run_installer for an example), or if it")
    print("genuinely has no CLI-relevant equivalent (a pure UI concern like an")
    print("icon fetch or a system-settings deep link), add it with a short reason")
    print(f"to {ALLOWLIST.relative_to(ROOT)}.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
