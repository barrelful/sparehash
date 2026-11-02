# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Two-unit deployment contract; manager mutation is installer-only and explicit."""

from __future__ import annotations

import os
import re
import socket
import stat
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, TypedDict
from uuid import uuid4

from .paths import CLI_PATH, UNIT_CONTROLLER, UNIT_MINER
from .sysdetect import FIXED_PATH, Capabilities

MARKER: Final = "# Managed by SpareHash"
TEMPLATES: Final = Path(__file__).resolve().parent.parent / "systemd"
UNIT_NAMES: Final = (UNIT_CONTROLLER, UNIT_MINER)
TIMEOUT: Final = 10.0
CREDENTIAL: Final = "LoadCredential=xmrig.json:/etc/sparehash/xmrig.json"
CREDENTIAL_FALLBACK: Final = "# Wrapper fallback: /etc/sparehash/xmrig.runtime.json (0640 root:xmrig)"
TRUSTED_CONDITION: Final = f"ExecCondition=+{CLI_PATH} __authorize_miner__"
UNIT_DIRECTORIES: Final = ("etc/systemd/system", "usr/lib/systemd/system")
UNIT_SUFFIXES: Final = (".service", ".socket", ".timer", ".target", ".mount", ".path", ".device",
                        ".slice", ".scope", ".automount", ".swap")
UNIT_TEXT_LIMIT: Final = 256 * 1024
Units = TypedDict("Units", {"sparehash.service": str, "sparehash-xmrig.service": str})


class Runner(Protocol):
    """Only the bounded subprocess boundary is injectable."""

    def __call__(self, argv: Sequence[str], *, timeout: float,
                 env: Mapping[str, str]) -> subprocess.CompletedProcess[str]: ...


def _run(argv: Sequence[str], *, timeout: float,
         env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, timeout=timeout, env=env, check=False,
                          capture_output=True, text=True)


class CollisionError(Exception):
    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"Refusing foreign or unsafe systemd unit: {path}")


class UnitError(Exception):
    detail: str

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def render(caps: Capabilities, *, templates: Path | None = None) -> Units:
    """Render from already-detected facts; never perform manager probes here.

    The fixed B2 wrapper selects systemd's CREDENTIALS_DIRECTORY/xmrig.json,
    or /etc/sparehash/xmrig.runtime.json when LoadCredential is absent.
    Runtime ownership, operation removal and benchmark restart suppression are
    controller/wrapper responsibilities, not caller-selectable unit commands.
    """
    root = TEMPLATES if templates is None else templates
    controller = (root / f"{UNIT_CONTROLLER}.in").read_text(encoding="utf-8")
    miner = (root / f"{UNIT_MINER}.in").read_text(encoding="utf-8")
    weights = "CPUWeight=idle\nIOWeight=1" if (
        caps.cgroup_hierarchy == "cgroup2" and caps.cpuweight_idle
    ) else "# Idle cgroup weights unavailable; retaining Nice/SCHED_IDLE/idle I/O"
    credential = CREDENTIAL if (
        caps.load_credential and caps.systemd_version is not None and caps.systemd_version >= 248
    ) else CREDENTIAL_FALLBACK
    rendered_miner = miner.replace("@WEIGHTS@", weights).replace("@CREDENTIAL@", credential)
    conditions = [line for line in rendered_miner.splitlines() if line.startswith("ExecCondition=")]
    if conditions != [TRUSTED_CONDITION]:
        raise UnitError("miner ExecCondition must be the exact root-executed trusted CLI")
    return {"sparehash.service": controller, "sparehash-xmrig.service": rendered_miner}


def check_collisions(directory: Path) -> None:
    """Refuse foreign units, including broken links and nonregular entries."""
    for name in (*UNIT_NAMES, "xmrig.service"):
        path = directory / name
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise CollisionError(path)
        try:
            with path.open(encoding="utf-8") as source:
                first = source.readline(256).rstrip("\n")
        except (OSError, UnicodeError) as error:
            raise CollisionError(path) from error
        if first != MARKER:
            raise CollisionError(path)


def _unit_text(path: Path) -> str:
    """Bounded, no-follow read of a single unit file."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise UnitError(f"Unsafe or unreadable unit: {path.name}") from error
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > UNIT_TEXT_LIMIT:
            raise UnitError(f"Unsafe or oversized unit: {path.name}")
        chunks: list[bytes] = []
        remaining = UNIT_TEXT_LIMIT
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(fd)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeError as error:
        raise UnitError(f"Malformed unit: {path.name}") from error


def _reject_privileged_exec(name: str, lines: Sequence[str]) -> None:
    for line in lines:
        statement = line.strip()
        if statement.startswith("ExecStart=+/") or re.match(r"ExecCondition=\+/usr/local/", statement):
            raise UnitError(f"Foreign unit {name} injects a root-executed helper")


def _validate_owned_unit(name: str, lines: Sequence[str]) -> None:
    statements = [line.strip() for line in lines]
    conditions = [line for line in statements if line.startswith("ExecCondition=")]
    if name == UNIT_MINER:
        if conditions != [TRUSTED_CONDITION]:
            raise UnitError(f"{name} must keep the exact root-executed trusted ExecCondition")
        if not any(line in {CREDENTIAL, CREDENTIAL_FALLBACK} for line in statements):
            raise UnitError(f"{name} must retain its credential binding")
    if any(line.startswith("ExecStart=+/") for line in statements):
        raise UnitError(f"{name} must not root-exec an arbitrary helper")


def validate_units(root: Path) -> None:
    """Reject foreign, masked or privilege-injecting units under a root.

    Walks the effective unit search paths and ``*.d`` drop-ins without invoking a
    manager. Owned units must carry the exact root-executed trusted ExecCondition
    and credential binding; any other present unit is refused when it injects
    ``ExecStart=+/`` or an ``ExecCondition=+/usr/local/...`` helper, and a foreign
    or masked ``xmrig.service``/``sparehash*.service`` is never tolerated.
    """
    for relative in UNIT_DIRECTORIES:
        directory = root / relative
        if not directory.is_dir():
            continue
        with os.scandir(directory) as entries:
            for entry in entries:
                path = directory / entry.name
                if entry.is_dir(follow_symlinks=False) and entry.name.endswith(".d"):
                    if entry.name[:-2] in (*UNIT_NAMES, "xmrig.service"):
                        raise UnitError(f"Refusing drop-in for {entry.name[:-2]}")
                    continue
                if entry.name == "xmrig.service" or (
                    entry.is_symlink() and entry.name in (*UNIT_NAMES, "xmrig.service")
                ):
                    raise UnitError(f"Refusing foreign or masked unit: {entry.name}")
                if not entry.name.endswith(UNIT_SUFFIXES):
                    continue
                lines = _unit_text(path).splitlines()
                marked = bool(lines) and lines[0] == MARKER
                if marked and entry.name not in UNIT_NAMES:
                    raise UnitError(f"Refusing foreign marked unit: {entry.name}")
                if entry.name in UNIT_NAMES and marked:
                    _validate_owned_unit(entry.name, lines)
                    continue
                _reject_privileged_exec(entry.name, lines)


def install_units(units: Units, directory: Path) -> None:
    """Write only to an explicit existing destination; installer runs as root.

    No implicit /etc destination, daemon-reload, enable or start side effects.
    """
    texts = ((UNIT_CONTROLLER, units["sparehash.service"]),
             (UNIT_MINER, units["sparehash-xmrig.service"]))
    if set(units) != set(UNIT_NAMES) or any(
        not text.startswith(MARKER + "\n") for _, text in texts
    ):
        raise UnitError("Expected exactly the two marked SpareHash units")
    check_collisions(directory)
    for name, text in texts:
        fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            os.fchmod(target.fileno(), 0o644)
            _ = target.write(text)


def verify_units(directory: Path, runner: Runner = _run) -> str:
    """Fail installation on analyzer errors; return diagnostics for installer logging."""
    try:
        result = runner(("/usr/bin/systemd-analyze", "verify",
                         *(str(directory / name) for name in UNIT_NAMES)),
                        timeout=TIMEOUT, env={"PATH": FIXED_PATH, "LANG": "C"})
    except (OSError, subprocess.SubprocessError, TimeoutError) as error:
        raise UnitError(f"Unit verification unavailable: {error}") from error
    if result.returncode != 0:
        raise UnitError(f"Unit verification failed: {result.stderr}")
    return result.stderr


def notify_ready(address: str) -> None:
    """Send READY only when the caller has initialized every safety blocker."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
        notifier.settimeout(TIMEOUT)
        notifier.connect("\0" + address[1:] if address.startswith("@") else address)
        _ = notifier.send(b"READY=1")


@dataclass(frozen=True, slots=True)
class ProbeContext:
    """Explicit filesystem/identity fixtures; never derived from mutable environment."""

    euid: int
    cgroup_root: Path
    cgroup_v2: bool

    @classmethod
    def host(cls) -> ProbeContext:
        root = Path("/sys/fs/cgroup")
        return cls(os.geteuid(), root, (root / "cgroup.controllers").is_file())


def probe_cpuweight_idle(env: Mapping[str, str], runner: Runner = _run,
                         context: ProbeContext | None = None) -> bool:
    """Installer-only ManagerProbe for Capabilities.detect(manager_probe=...).

    Never call from render/status. The callback argument matches sysdetect's
    API, but production helper environment is fixed, not inherited from it.
    A bounded live sleeper retains the child cgroup for inspection. An exited
    oneshot can remain active while systemd has already removed its cgroup.
    Type=exec waits for successful exec, not process exit; no --wait is used.
    """
    _ = env
    ctx = context if context is not None else ProbeContext.host()
    # cpu.idle is a non-root control. Only the transient unit's child cgroup
    # below can establish effective kernel + manager support.
    if ctx.euid != 0 or not ctx.cgroup_v2:
        return False
    unit = f"sparehash-idle-probe-{uuid4().hex}.service"
    safe_env = {"PATH": FIXED_PATH, "LANG": "C"}
    accepted = False
    cleaned = True
    try:
        started = runner(("/usr/bin/systemd-run", f"--unit={unit}",
                          "--property=Type=exec", "--property=RuntimeMaxSec=30s",
                          "--property=CPUWeight=idle", "--property=TimeoutStartSec=5s",
                          "--property=CollectMode=inactive-or-failed",
                          "--property=TimeoutStopSec=3s", "/usr/bin/sleep", "30"),
                         timeout=TIMEOUT, env=safe_env)
        if started.returncode == 0:
            shown = runner(("/usr/bin/systemctl", "show", unit,
                            "--property=Result,MainPID,SubState,ControlGroup,CPUWeight,ActiveState"),
                           timeout=TIMEOUT, env=safe_env)
            properties = dict(line.split("=", 1) for line in shown.stdout.splitlines() if "=" in line)
            group = properties.get("ControlGroup", "")
            parts = Path(group).parts
            if (shown.returncode == 0 and properties.get("Result") == "success"
                    and re.fullmatch(r"[1-9][0-9]{0,19}", properties.get("MainPID", "")) is not None
                    and properties.get("SubState") == "running"
                    and properties.get("ActiveState") == "active"
                    and properties.get("CPUWeight") in {"idle", "0"}
                    and group.startswith("/") and len(parts) > 1 and ".." not in parts):
                accepted = (ctx.cgroup_root.joinpath(*parts[1:]) / "cpu.idle").read_text().strip() == "1"
    except (OSError, UnicodeError, subprocess.SubprocessError, TimeoutError):
        accepted = False
    finally:
        try:
            result = runner(("/usr/bin/systemctl", "stop", unit), timeout=TIMEOUT, env=safe_env)
            cleaned = result.returncode == 0
        except (OSError, subprocess.SubprocessError, TimeoutError):
            cleaned = False
    return accepted and cleaned
