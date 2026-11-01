# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Sparehash command-line entrypoint and internal dispatch scaffold."""

import argparse
import os
import pwd
import sys
from collections.abc import Callable, Sequence
from typing import Final

# install.sh executes this file directly under ``python3 -I -B``. Isolated mode
# does not put the script directory on sys.path, so re-anchor the launcher-selected
# source root here; environment imports stay scrubbed by the shell.
if __package__ in (None, ""):
    _SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _SOURCE_ROOT not in sys.path:
        sys.path.insert(0, _SOURCE_ROOT)

PUBLIC_COMMANDS: Final = (
    "install",
    "status",
    "start",
    "stop",
    "pause",
    "resume",
    "config",
    "logs",
    "benchmark",
    "update",
    "uninstall",
    "complete-upgrade",
)
GAMING_COMMANDS: Final = ("gaming-start", "gaming-end")
INTERNAL_COMMANDS: Final = (
    "__controller__",
    "__cleanup__",
    "__authorize_miner__",
    "__miner_exec__",
    "authorize-start",
)
PRIVILEGED_COMMANDS: Final = (
    "__controller__",
    "__cleanup__",
    "__authorize_miner__",
    "authorize-start",
)


class CliArguments(argparse.Namespace):
    json: bool = False
    follow: bool = False
    command: str = ""
    config_command: str = ""
    debug_command: str | None = None
    name: str = ""
    purge: bool = False
    operation_id: str = ""


def _parser() -> argparse.ArgumentParser:
    """Build the public argument parser."""
    parser = argparse.ArgumentParser(prog="sparehash")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in PUBLIC_COMMANDS:
        help_text = None
        if command == "status":
            help_text = "requires root; show controller/miner/blocker/power state"
        elif command in ("update", "benchmark", "complete-upgrade"):
            help_text = "deferred in experimental v0.2; refuses before effects"
        command_parser = subparsers.add_parser(command, help=help_text)
        if command == "install":
            _ = command_parser.add_argument("--answers", metavar="FILE")
            _ = command_parser.add_argument("--use-existing", action="store_true")
            _ = command_parser.add_argument("--clear-lifecycle-latch", action="store_true")
            _ = command_parser.add_argument("--preflight", action="store_true")
            _ = command_parser.add_argument("--json", action="store_true")
            _ = command_parser.add_argument("--yes", action="store_true")
            _ = command_parser.add_argument("--no-start", action="store_true")
            _ = command_parser.add_argument("--no-enable", action="store_true")
        elif command == "complete-upgrade":
            _ = command_parser.add_argument("operation_id")
        elif command == "uninstall":
            _ = command_parser.add_argument("--purge", action="store_true")
        elif command == "status":
            _ = command_parser.add_argument("--json", action="store_true")
        elif command == "logs":
            _ = command_parser.add_argument("-f", "--follow", action="store_true")
        elif command == "config":
            config_commands = command_parser.add_subparsers(dest="config_command", required=True)
            for action in ("show", "edit"):
                _ = config_commands.add_parser(action, help="deferred in experimental v0.2" if action == "edit" else "show public configuration")
    for command in GAMING_COMMANDS:
        _ = subparsers.add_parser(command)
    debug = subparsers.add_parser("debug")
    debug_commands = debug.add_subparsers(dest="debug_command", required=True)
    for command in ("set-blocker", "clear-blocker"):
        _ = debug_commands.add_parser(command).add_argument("name")
    return parser


def _incomplete(command: str) -> int:
    print(f"sparehash: {command} is unavailable until its implementation is installed", file=sys.stderr)
    return 2


def _unsupported(command: str) -> int:
    print(f"sparehash: {command} is unsupported in experimental v0.2.0; "
          "upgrades/rollback and benchmark are deferred. Preserve any existing "
          "migration journals and safety holds; do not delete them to bypass refusal.", file=sys.stderr)
    return 2


def _username(uid: int) -> str:
    return pwd.getpwuid(uid).pw_name


def main(
    argv: Sequence[str] | None = None,
    *,
    geteuid: Callable[[], int] = os.geteuid,
    getusername: Callable[[int], str] = _username,
) -> int:
    """Dispatch approved public and internal command names without fake success."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in INTERNAL_COMMANDS:
        command = arguments[0]
        if command in PRIVILEGED_COMMANDS and geteuid() != 0:
            print("sparehash: privileged command requires root", file=sys.stderr)
            return 2
        if command == "__miner_exec__" and getusername(geteuid()) != "xmrig":
            print("sparehash: miner command requires the xmrig identity", file=sys.stderr)
            return 2
        if command in ("__controller__", "__cleanup__", "__miner_exec__"):
            if len(arguments) != 1 or (command == "__miner_exec__" and geteuid() == 0):
                return 1
            from sparehash.runtime import cleanup, run
            from sparehash.runtime_spec import miner

            return {"__controller__": run, "__cleanup__": cleanup, "__miner_exec__": miner}[command]()
        if command in ("__authorize_miner__", "authorize-start"):
            if len(arguments) != 1:
                return 1
            from sparehash.controller_io import authorize_miner

            return authorize_miner()
    if arguments and arguments[0] == "install":
        from sparehash.install import main as install

        return install(arguments[1:], geteuid=geteuid)
    parsed = _parser().parse_args(arguments, namespace=CliArguments())
    command = parsed.command
    if command == "config" and parsed.config_command == "edit":
        return _unsupported("config edit")
    if command in ("benchmark", "update", "complete-upgrade"):
        return _unsupported(command)
    if command == "config":
        from sparehash.cli_config import show

        return show()
    if command in ("start", "stop", "pause", "resume", "logs"):
        from sparehash.cli_ops import logs, manual, stack

        return {
            "start": lambda: stack("start", geteuid=geteuid),
            "stop": lambda: stack("stop", geteuid=geteuid),
            "pause": lambda: manual("pause"),
            "resume": lambda: manual("resume"),
            "logs": lambda: logs(parsed.follow),
        }[command]()
    if command == "status":
        if geteuid() != 0:
            print("sparehash: status requires root; run: sudo sparehash status", file=sys.stderr)
            return 1
        from sparehash.status import run as status_run

        return status_run(parsed.json)
    if command in GAMING_COMMANDS:
        from sparehash.gaming import gaming_end, gaming_start

        return {"gaming-start": gaming_start, "gaming-end": gaming_end}[command]()
    if command == "uninstall":
        from sparehash.cli_uninstall import uninstall

        return uninstall(purge=parsed.purge, geteuid=geteuid)
    if command == "debug":
        if geteuid() != 0:
            print("sparehash: debug commands require root", file=sys.stderr)
            return 2
        from sparehash.ipc import IPCError, call

        verb = "debug-set" if parsed.debug_command == "set-blocker" else "debug-clear"
        try:
            return 0 if call({"cmd": verb, "blocker": parsed.name}, timeout=1).get("ok") is True else 1
        except IPCError:
            return 1
    return _incomplete(command)


if __name__ == "__main__":
    raise SystemExit(main())
