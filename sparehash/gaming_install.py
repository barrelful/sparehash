"""Installer-only GameMode consent and account integration; no runtime semantics."""

from __future__ import annotations

import grp
import pwd
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, TypedDict

from .config import Config
from .gaming import Runner, User, UserError, resolve_helper
from .gaming_ini import Home, UnsafePathError, edit
from .sysdetect import FIXED_PATH, Capabilities

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


def merge(user: User) -> bool:
    account = pwd.getpwnam(user.name)
    if account.pw_uid != user.uid:
        raise UserError(user.name)
    return edit(Home(Path(account.pw_dir), account.pw_uid, account.pw_gid), True)


def unmerge(user: User) -> bool:
    account = pwd.getpwnam(user.name)
    if account.pw_uid != user.uid:
        raise UserError(user.name)
    return edit(Home(Path(account.pw_dir), account.pw_uid, account.pw_gid), False)


def configure(cfg: Config, caps: Capabilities, consent: Callable[[str], bool], *,
              runner: Runner = subprocess.run) -> InstallResult:
    """Return immutable enabled/disabled state for Todo18, never persist it here.

    Runner is an independent privileged-command test seam, not a config input.
    All production executables are freshly resolved from the protected fixed PATH.
    """
    if not cfg.gaming.enabled:
        return InstallResult(cfg, False)
    warnings: list[str] = []
    available = detect_gaming(caps)["gamemode"] and resolve_helper("gamemoded") is not None
    if not available:
        notice = "GameMode not found — it is the supported way to pause mining while gaming"
        print(notice)
        warnings.append(notice)
        if not consent(notice + "; install the gamemode package?"):
            warnings.append("gaming-pause will be UNAVAILABLE; mining will only be deprioritized")
        else:
            command = next(((path, *args) for name, args in INSTALL_ARGS
                            if (path := resolve_helper(name)) is not None), None)
            if command is None:
                warnings.append("No available apt-get, dnf or pacman; install GameMode manually and rerun setup")
            else:
                try:
                    _run(command, runner)
                    available = resolve_helper("gamemoded") is not None
                    if not available:
                        warnings.append("Package command completed but gamemoded is unavailable; check GameMode installation")
                except InstallError as exc:
                    warnings.append(str(exc))
    if available:
        for name in cfg.gaming.users:
            try:
                user = User.from_name(name)
                group = grp.getgrnam("sparehash")
                account = pwd.getpwnam(name)
                if account.pw_gid != group.gr_gid and name not in group.gr_mem:
                    helper = resolve_helper("usermod")
                    if helper is None:
                        raise InstallError("group", "usermod unavailable; add account to sparehash group and rerun")
                    _run((helper, "-aG", "sparehash", name), runner)
                _ = merge(user)
            except FileNotFoundError:
                warnings.append(f"Skipping {name}: home or .config missing; gaming integration disabled")
                available = False
            except (UnsafePathError, UserError, InstallError, KeyError) as exc:
                warnings.append(f"Skipping {name}: {exc}; gaming integration disabled")
                available = False
    gaming = replace(cfg.gaming, enabled=available)
    for warning in warnings:
        if not warning.startswith("GameMode not found"):
            print(warning)
    return InstallResult(replace(cfg, gaming=gaming), available, tuple(warnings))
