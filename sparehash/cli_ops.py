"""Fixed stack operations; manual state changes belong solely to controller IPC."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, Literal, Protocol, assert_never

from . import ipc
from .state import BLOCKERS
from .sysdetect import FIXED_PATH

HELPER_TIMEOUT: Final = 30.0
ENV: Final = {"PATH": FIXED_PATH, "LC_ALL": "C", "SYSTEMD_PAGER": "cat", "SYSTEMD_COLORS": "0"}


class Runner(Protocol):
    # Mirrors the stdlib subprocess adapter; keywords enforce process safety.
    def __call__(self, argv: tuple[str, ...], /, *, timeout: float | None,
                 check: bool, capture_output: bool, text: Literal[True],
                 env: Mapping[str, str], shell: bool) -> subprocess.CompletedProcess[str]: ...


class Client(Protocol):
    def __call__(self, command: ipc.Document, /, *, timeout: float) -> ipc.Document: ...


class OperationError(Exception):
    def __init__(self, operation: str, detail: str) -> None:
        self.operation: str = operation
        self.detail: str = detail
        super().__init__(f"{operation}: {detail}")


def _safe_text(text: str) -> str:
    """Keep controller feedback single-line and redact the credential format."""
    return re.sub(r"[0-9a-fA-F]{64}", "[redacted]", " ".join(text.split()))[:512]


@dataclass(frozen=True, slots=True)
class Helpers:
    runner: Runner = subprocess.run
    resolve: Callable[[str], str | None] = lambda name: shutil.which(name, path=FIXED_PATH)

    def execute(self, argv: tuple[str, ...], follow: bool = False) -> None:
        """No shell or inherited environment; journal output inherits the terminal."""
        executable = self.resolve(argv[0])
        if executable is None:
            raise OperationError(argv[0], "required helper not found in system PATH")
        try:
            result = self.runner((executable, *argv[1:]), timeout=None if follow else HELPER_TIMEOUT,
                                 check=False, capture_output=argv[0] != "journalctl", text=True,
                                 env=ENV, shell=False)
        except subprocess.TimeoutExpired as exc:
            raise OperationError(argv[0], "helper timed out") from exc
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            raise OperationError(argv[0], "helper could not run") from exc
        if result.returncode != 0:
            detail = _safe_text(result.stderr or "")
            raise OperationError(argv[0], f"helper exited {result.returncode}" + (f": {detail}" if detail else ""))


def stack(command: Literal["start", "stop"], helpers: Helpers | None = None,
          geteuid: Callable[[], int] = os.geteuid) -> int:
    """Start controller only; explicit stack stop retains boot enablement."""
    if geteuid() != 0:
        print(f"sparehash: {command} requires root", file=sys.stderr)
        return 2
    match command:
        case "start":
            argv = ("systemctl", "enable", "--now", "sparehash.service")
            message = "SpareHash controller enabled and started; mining remains blocker-gated."
        case "stop":
            argv = ("systemctl", "stop", "sparehash-xmrig.service", "sparehash.service")
            message = "SpareHash stack stopped (not disabled; reboot can resume). Use pause for a persistent manual pause."
        case _:
            assert_never(command)
    try:
        (helpers or Helpers()).execute(argv)
    except OperationError as exc:
        print(f"sparehash: {exc}", file=sys.stderr)
        return 1
    print(message)
    return 0


def _request(command: str, client: Client) -> ipc.Document:
    response = client({"cmd": command}, timeout=1)
    if response.get("ok") is not True:
        error = response.get("error")
        raise OperationError(command, _safe_text(error) if isinstance(error, str) else "controller rejected request")
    return response


def manual(command: Literal["pause", "resume"], client: Client = ipc.call) -> int:
    """Socket permissions gate access; resume never claims a successful launch."""
    try:
        _ = _request(command, client)
        match command:
            case "pause":
                message = "Mining paused manually."
            case "resume":
                response = _request("status", client)
                blockers, reasons = response.get("blockers"), response.get("reasons")
                if not isinstance(blockers, list) or not isinstance(reasons, dict):
                    raise OperationError(command, "manual resume accepted; blocker status unavailable")
                active: list[tuple[str, str]] = []
                for name in blockers:
                    if not isinstance(name, str) or name not in BLOCKERS or not isinstance(reasons.get(name), str):
                        raise OperationError(command, "manual resume accepted; invalid blocker status")
                    reason = reasons[name]
                    if isinstance(reason, str):
                        active.append((name, _safe_text(reason)))
                if set(reasons) != {name for name, _ in active}:
                    raise OperationError(command, "manual resume accepted; inconsistent blocker status")
                message = ("Manual pause cleared: still blocked by " +
                           "; ".join(f"{name} ({reason})" for name, reason in active) + "." if active else
                           "Manual pause cleared: no blockers active (not a mining-start confirmation).")
            case _:
                assert_never(command)
    except (ipc.IPCError, OSError):
        print(f"sparehash: {command}: controller unavailable or socket access denied; check service and sparehash group access", file=sys.stderr)
        return 1
    except OperationError as exc:
        print(f"sparehash: {exc}", file=sys.stderr)
        return 1
    print(message)
    return 0


def logs(follow: bool = False, helpers: Helpers | None = None) -> int:
    """Stream only the two fixed units, without a pager or a follow time limit."""
    argv = ("journalctl", "-u", "sparehash.service", "-u", "sparehash-xmrig.service", "-n", "100")
    try:
        (helpers or Helpers()).execute(argv + (("--follow",) if follow else ()), follow)
    except OperationError as exc:
        print(f"sparehash: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
