# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""GameMode observations; hook identity and state belong to the IPC/controller.

Run observations on provider workers, never the safety selector. Supply the
complete per-UID result to Observation.gaming, including unknown users. Session
absence must come from a trusted session inventory, not a missing bus pathname.
"""

from __future__ import annotations

import logging
import pwd
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Protocol

from . import ipc
from .sysdetect import FIXED_PATH

LOGGER: Final = logging.getLogger(__name__)
HOOK_TIMEOUT: Final = 0.75
QUERY_TIMEOUT: Final = 10.0


class UserError(ValueError):
    def __init__(self, name: str) -> None:
        self.name: str = name
        super().__init__(f"invalid or mismatched gaming account: {name!r}")


@dataclass(frozen=True, slots=True)
class User:
    """Configured account, verified against NSS; never constructed from hooks."""

    uid: ipc.Uid
    name: str

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_.-]*\$?", self.name) is None:
            raise UserError(self.name)
        try:
            account = pwd.getpwnam(self.name)
        except KeyError as exc:
            raise UserError(self.name) from exc
        if type(self.uid) is not int or self.uid < 0 or account.pw_uid != self.uid:
            raise UserError(self.name)

    @classmethod
    def from_name(cls, name: str) -> User:
        """Resolve a protected config account name to its authoritative UID."""
        try:
            uid = pwd.getpwnam(name).pw_uid
        except KeyError as exc:
            raise UserError(name) from exc
        return cls(ipc.Uid(uid), name)


class Runner(Protocol):
    def __call__(self, args: tuple[str, ...], /, *, timeout: float,
                 env: Mapping[str, str], check: bool, capture_output: bool,
                 text: Literal[True]) -> subprocess.CompletedProcess[str]: ...


def _unknown_session(_user: User) -> bool | None:
    return None


def resolve_helper(name: str) -> str | None:
    """Ignore caller PATH; require root-protected resolved helper ancestry."""
    found = shutil.which(name, path=FIXED_PATH)
    if found is None:
        return None
    try:
        path = Path(found).resolve(strict=True)
        for component in (path, *path.parents):
            info = component.stat()
            if info.st_uid != 0 or info.st_mode & 0o022:
                return None
        if not path.is_file():
            return None
    except OSError:
        return None
    return str(path)


def parse_status(text: str) -> bool | None:
    """Accept only the upstream global -s response, not PID-specific output."""
    return {"gamemode is active": True, "gamemode is inactive": False}.get(text.strip())


@dataclass(frozen=True, slots=True)
class Probe:
    """Bounded worker query with explicit filesystem/session inventory seams."""

    runner: Runner = subprocess.run
    bus_root: Path = Path("/run/user")
    session_present: Callable[[User], bool | None] = _unknown_session

    def query(self, user: User) -> bool | None:
        bus = self.bus_root / str(user.uid) / "bus"
        try:
            info = bus.lstat()
        except FileNotFoundError:
            return False if self.session_present(user) is False else None
        except OSError:
            return None
        if not stat.S_ISSOCK(info.st_mode):
            return None
        runuser, env_helper, gamemoded = (resolve_helper(name) for name in ("runuser", "env", "gamemoded"))
        if runuser is None or env_helper is None or gamemoded is None:
            return None
        argv = (runuser, "-u", user.name, "--", env_helper,
                f"DBUS_SESSION_BUS_ADDRESS=unix:path={bus}", gamemoded, "-s")
        try:
            result = self.runner(argv, timeout=QUERY_TIMEOUT, check=False,
                                 capture_output=True, text=True,
                                 env={"PATH": FIXED_PATH, "LC_ALL": "C"})
        except (OSError, subprocess.SubprocessError, TimeoutError, UnicodeError):
            LOGGER.debug("gaming query unavailable", extra={"uid": user.uid}, exc_info=True)
            return None
        value = parse_status(result.stdout) if result.returncode == 0 else None
        if value is None:
            LOGGER.debug("gaming query inconclusive", extra={"uid": user.uid, "returncode": result.returncode})
        return value


DEFAULT_PROBE: Final = Probe()


def observations(users: Sequence[User], probe: Probe = DEFAULT_PROBE) -> tuple[tuple[ipc.Uid, bool | None], ...]:
    """Fresh complete per-user sample; no cache or hook-state mutation."""
    return tuple((user.uid, probe.query(user)) for user in dict.fromkeys(users))


def reconcile(users: Sequence[User], probe: Probe = DEFAULT_PROBE) -> bool | None:
    """Any active wins; otherwise any unknown inhibits, all inactive clears."""
    values = tuple(value for _, value in observations(users, probe))
    if any(value is True for value in values):
        return True
    return None if any(value is None for value in values) else False


def _hook(verb: ipc.Verb) -> int:
    try:
        response = ipc.call({"cmd": verb.value}, timeout=HOOK_TIMEOUT)
    except (ipc.IPCError, OSError) as exc:
        print(f"sparehash: {verb.value}: {exc}; check controller availability and sparehash group/socket access", file=sys.stderr)
        return 1
    if response.get("ok") is not True:
        print(f"sparehash: {verb.value}: controller rejected hook; check controller logs and socket access", file=sys.stderr)
        return 1
    return 0


def gaming_start() -> int:
    """Fixed fast hook; the server authenticates the sender via SO_PEERCRED."""
    return _hook(ipc.Verb.GAMING_START)


def gaming_end() -> int:
    """Fixed fast hook; the controller discards only this peer's contribution."""
    return _hook(ipc.Verb.GAMING_END)
