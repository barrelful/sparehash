# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Verified hook accounts; hook identity and state belong to IPC/controller."""

from __future__ import annotations

import pwd
import re
import sys
from dataclasses import dataclass
from typing import Final

from . import ipc

HOOK_TIMEOUT: Final = 0.75


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
