# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Installer-only GameMode consent and account integration; no runtime semantics."""

from __future__ import annotations

import grp
import hashlib
import pwd
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, TypedDict

from . import gaming
from .config import Config
from .gaming import Runner, User, UserError, resolve_helper
from .gaming_ini import EditReceipt, Home, UnsafePathError, edit
from .install_ledger import Ledger, Receipt
from .sysdetect import FIXED_PATH, Capabilities
from .xmrig_ledger import Artifact, Membership

INSTALL_ARGS: Final = (("apt-get", ("install", "-y", "gamemode")),
                      ("dnf", ("install", "-y", "gamemode")),
                      ("pacman", ("-S", "--noconfirm", "gamemode")))


class GamingDetection(TypedDict):
    gamemode: bool
    steam: bool


class InstallError(Exception):
    def __init__(self, stage: str, reason: str) -> None:
        self.stage: str = stage
        self.reason: str = reason
        super().__init__(f"{stage}: {reason}")


@dataclass(frozen=True, slots=True)
class InstallResult:
    config: Config
    available: bool
    warnings: tuple[str, ...] = ()
    receipts: tuple[Receipt, ...] = ()


def detect_gaming(caps: Capabilities) -> GamingDetection:
    """Use the detected fixed-PATH inventory; Steam is informational, not required."""
    helpers = dict(caps.helper_paths)
    return {"gamemode": bool(helpers.get("gamemoded")), "steam": bool(helpers.get("steam"))}


def _run(argv: tuple[str, ...], runner: Runner) -> None:
    try:
        result = runner(argv, timeout=300, check=False, capture_output=True,
                        text=True, env={"PATH": FIXED_PATH, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise InstallError("helper", "command unavailable or timed out") from exc
    if result.returncode != 0:
        raise InstallError("helper", f"{argv[0]} exited {result.returncode}")


def merge(user: User, *, record: Callable[[EditReceipt], None] | None = None) -> bool:
    account = pwd.getpwnam(user.name)
    if account.pw_uid != user.uid:
        raise UserError(user.name)
    return edit(Home(Path(account.pw_dir), account.pw_uid, account.pw_gid), True, record=record)


def unmerge(user: User) -> bool:
    account = pwd.getpwnam(user.name)
    if account.pw_uid != user.uid:
        raise UserError(user.name)
    return edit(Home(Path(account.pw_dir), account.pw_uid, account.pw_gid), False)


def configure(cfg: Config, caps: Capabilities, consent: Callable[[str], bool] | None, *,
              runner: Runner = subprocess.run, ledger: Ledger | None = None) -> InstallResult:
    """Return immutable enabled/disabled state for Todo18, never persist it here.

    Runner is an independent privileged-command test seam, not a config input.
    All production executables are freshly resolved from the protected fixed PATH.
    """
    if not cfg.gaming.enabled:
        return InstallResult(cfg, False)
    warnings: list[str] = []
    receipts: list[Receipt] = []
    available = detect_gaming(caps)["gamemode"] and resolve_helper("gamemoded") is not None
    if not available:
        if consent is None:
            warnings.append("GameMode not installed; gaming protection disabled (noninteractive install)")
        elif not consent("GameMode not found — it is the supported way to pause mining while gaming. "
                         "Install the gamemode package? [Y/n]: "):
            warnings.append("GameMode installation declined; gaming protection disabled")
        else:
            command = next(((path, *args) for name, args in INSTALL_ARGS
                            if (path := resolve_helper(name)) is not None), None)
            if command is None:
                warnings.append("No available apt-get, dnf or pacman; gaming protection disabled; install GameMode manually and rerun setup")
            else:
                try:
                    _run(command, runner)
                    available = resolve_helper("gamemoded") is not None
                    if not available:
                        warnings.append("Package command completed but gamemoded is unavailable; gaming protection disabled; check GameMode installation")
                except InstallError as exc:
                    warnings.append(f"{exc}; gaming protection disabled")
    if available:
        for name in cfg.gaming.users:
            try:
                user = User.from_name(name)
                group = grp.getgrnam("sparehash")
                account = pwd.getpwnam(name)
                added = False
                if account.pw_gid != group.gr_gid and name not in group.gr_mem:
                    helper = resolve_helper("usermod")
                    if helper is None:
                        raise InstallError("group", "usermod unavailable; add account to sparehash group and rerun")
                    sequence = ledger.begin(f"membership:{name}:sparehash", "add-membership") if ledger else 0
                    _run((helper, "-aG", "sparehash", name), runner)
                    added = True
                    warnings.append(f"{name}: added to sparehash group; log out and back in "
                                    "(or run `sg sparehash -c '...'`) for membership to take effect")
                    if ledger:
                        ledger.applied(sequence, (Membership(name, account.pw_uid, "sparehash", group.gr_gid, True),))
                membership = Membership(name, account.pw_uid, "sparehash", group.gr_gid, added)
                receipts.append(membership)
                if ledger is None:
                    _ = merge(user)
                else:
                    ledger.applied(0, (membership,))
                    sequence = ledger.begin("gaming-ini:" + name, "merge-gaming")
                    captured: list[Artifact] = []
                    def record(result: EditReceipt) -> None:
                        for kind, payload, created in (("ini", result.configuration, result.created),
                                                       ("backup", result.backup, True)):
                            if payload is None:
                                continue
                            data, info = payload
                            suffix = ".config/gamemode.ini" + (".sparehash-bak" if kind == "backup" else "")
                            captured.append(Artifact("gaming-" + kind + ":" + name, str(Path(account.pw_dir) / suffix), "file", created,
                                                     info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_dev, info.st_ino, hashlib.sha256(data).hexdigest()))
                    _ = merge(user, record=record)
                    ledger.applied(sequence, tuple(captured))
                    receipts.extend(captured)
                if gaming.Probe(runner=runner).query(user) is None:
                    warnings.append(f"{name}: D-Bus session bus unavailable; GameMode works only after login")
            except FileNotFoundError:
                warnings.append(f"Skipping {name}: home or .config missing; gaming integration disabled")
                available = False
            except (UnsafePathError, UserError, InstallError, KeyError) as exc:
                warnings.append(f"Skipping {name}: {exc}; gaming integration disabled")
                available = False
    configured_gaming = replace(cfg.gaming, enabled=available)
    for warning in warnings:
        print(warning)
    return InstallResult(replace(cfg, gaming=configured_gaming), available, tuple(warnings), tuple(receipts))
