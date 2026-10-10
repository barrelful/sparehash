# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Bounded, read-only detection of host capabilities.

All external helpers are resolved from a fixed system PATH and run without a
shell with a ten-second timeout. ``SPAREHASH_SYSFS_ROOT`` may point at a
fixture tree; ``proc_root`` and ``root`` provide equivalent seams for procfs
and other filesystem inventory in tests. Detection is intentionally fresh on
each call and never changes system state.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from . import paths

TIMEOUT_SECONDS = 10
FIXED_PATH = "/usr/sbin:/usr/bin:/sbin:/bin:/usr/games"
Runner = Callable[..., subprocess.CompletedProcess[str]]


class ManagerProbe(Protocol):
    """Installer probe; install orchestration passes its bounded systemd transient probe (Todo18/Todo11).

    This callback is never inferred from the subprocess runner; omission or an
    inconclusive result leaves CPUWeight=idle unavailable.
    """

    def __call__(self, env: Mapping[str, str]) -> bool | None: ...


def _run(argv: Sequence[str], *, runner: Runner, env: Mapping[str, str]) -> str | None:
    """Run a known helper, returning None for absent, failed, or malformed output."""
    executable = shutil.which(argv[0], path=FIXED_PATH)
    if executable is None:
        return None
    try:
        result = runner(
            [executable, *argv[1:]], check=False, capture_output=True, text=True,
            timeout=TIMEOUT_SECONDS, env=dict(env),
        )
    except (OSError, subprocess.SubprocessError, TimeoutError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None


def _version(text: str | None) -> int | None:
    if not text:
        return None
    match = re.search(r"systemd\s+(\d+)(?:\D|$)", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _parse_busctl_boolean(text: str | None) -> bool | None:
    if text is None:
        return None
    match = re.fullmatch(r"b\s+(true|false|0|1)", text.strip(), re.IGNORECASE)
    if match is None:
        return None
    return match.group(1).casefold() in {"true", "1"}


def _cpuweight_probe(*, manager_probe: ManagerProbe | None, env: Mapping[str, str], euid: int,
                     cgroup_cpu_idle: bool) -> bool:
    """Require root, kernel support, and an explicit conclusive manager probe."""
    if euid != 0 or not cgroup_cpu_idle:
        return False
    return manager_probe(env) is True if manager_probe is not None else False


@dataclass(frozen=True, slots=True)
class Capabilities:
    architecture: str
    python_supported: bool
    systemd_version: int | None
    systemd_pid1: bool | None
    cgroup_hierarchy: str | None
    load_credential: bool
    upower_available: bool | None
    battery_present: bool | None
    numa_nodes: tuple[int, ...] | None
    helper_paths: tuple[tuple[str, str | None], ...]
    nproc: int | None
    memory_bytes: int | None
    xmrig_path: str | None
    xmrig_package: str | None
    cpuweight_idle: bool

    @classmethod
    def detect(
        cls,
        *,
        runner: Runner = subprocess.run,
        environ: Mapping[str, str] | None = None,
        root: str | Path = "/",
        proc_root: str | Path = "/proc",
        architecture: str | None = None,
        python_version: tuple[int, int] | None = None,
        euid: int | None = None,
        manager_probe: ManagerProbe | None = None,
    ) -> "Capabilities":
        env = dict(os.environ if environ is None else environ)
        base = Path(root)
        sysfs = Path(env.get(paths.SPAREHASH_SYSFS_ROOT, str(base / "sys")))
        proc = Path(proc_root)
        arch = architecture or platform.machine()
        pyver: tuple[int, int] = python_version or (sys.version_info.major, sys.version_info.minor)
        uid = os.geteuid() if euid is None else euid

        systemctl = _run(("systemctl", "--version"), runner=runner, env=env)
        version = _version(systemctl)
        pid1_comm = _read(proc / "1" / "comm")
        pid1 = None if pid1_comm is None else pid1_comm == "systemd"

        mounts = _read(proc / "1" / "mountinfo")
        cgroup = _read(proc / "1" / "cgroup")
        hierarchy: str | None = None
        if mounts is not None:
            hierarchy = "cgroup2" if " - cgroup2 " in mounts else ("cgroup1" if " - cgroup " in mounts else None)
        if cgroup is None and hierarchy is None:
            hierarchy = None
        cpu_idle_path = sysfs / "fs/cgroup/cpu.idle"
        cpu_idle = hierarchy == "cgroup2" and _read(cpu_idle_path) is not None

        helpers = ("gdbus", "busctl", "gamemoded", "gamemoderun", "steam", "gpg", "ss", "runuser",
                   "stress-ng", "systemctl", "systemd-run", "nproc", "upower", "dpkg-query", "rpm")
        helper_paths = tuple((name, shutil.which(name, path=FIXED_PATH)) for name in helpers)
        unit_properties = _run(("systemctl", "show", "--property=LoadCredential"), runner=runner, env=env)
        load_credential = bool(version is not None and version >= 248 and unit_properties is not None)

        upower_query = _run(("busctl", "--system", "get-property", "org.freedesktop.UPower",
                             "/org/freedesktop/UPower", "org.freedesktop.UPower", "OnBattery"),
                            runner=runner, env=env)
        upower_on_battery = _parse_busctl_boolean(upower_query)
        upower_available = None if upower_on_battery is None else True
        supplies = sysfs / "class/power_supply"
        battery: bool | None = None
        try:
            entries = tuple(supplies.iterdir())
        except OSError:
            entries = ()
            battery = None
        else:
            uncertain = False
            for entry in entries:
                kind = _read(entry / "type")
                if kind is not None:
                    if kind.casefold() == "battery":
                        battery = True
                        break
                else:
                    uncertain = True
            if battery is not True:
                battery = None if uncertain else False

        nodes_dir = sysfs / "devices/system/node"
        node_ids: list[int] = []
        node_unknown = False
        try:
            node_entries = tuple(nodes_dir.glob("node[0-9]*"))
        except OSError:
            node_entries = ()
            node_unknown = True
        if not nodes_dir.is_dir():
            node_unknown = True
        if not node_unknown:
            for node in node_entries:
                match = re.fullmatch(r"node(\d+)", node.name)
                meminfo_text = _read(node / "meminfo")
                total = re.search(r"^Node\s+\d+\s+MemTotal:\s+(\d+)\s+kB\s*$", meminfo_text or "", re.MULTILINE)
                online = _read(node / "online")
                if match and online in {None, "1"} and total and int(total.group(1)) > 0:
                    node_ids.append(int(match.group(1)))
        numa_nodes = None if node_unknown or not node_ids else tuple(sorted(node_ids))

        nproc_raw = _run(("nproc",), runner=runner, env=env)
        try:
            nproc_value = int(nproc_raw) if nproc_raw else None
            if nproc_value is not None and nproc_value <= 0:
                nproc_value = None
        except ValueError:
            nproc_value = None
        meminfo = _read(proc / "meminfo")
        memory = None
        if meminfo:
            found = re.search(r"^MemTotal:\s+(\d+)\s+kB\s*$", meminfo, re.MULTILINE)
            if found:
                memory = int(found.group(1)) * 1024

        discovered_xmrig = shutil.which("xmrig", path=FIXED_PATH)
        xmrig_path = next((str(p) for p in (
            base / "opt/xmrig/current/xmrig", base / "usr/bin/xmrig", base / "usr/local/bin/xmrig",
        ) if p.is_file()), discovered_xmrig)
        package_query = _run(("dpkg-query", "-W", "-f=${Status}", "xmrig"), runner=runner, env=env)
        if package_query is None:
            package_query = _run(("rpm", "-q", "xmrig"), runner=runner, env=env)
        xmrig_package = "xmrig" if package_query and ("install ok installed" in package_query or "xmrig-" in package_query) else None

        return cls(
            architecture=arch,
            python_supported=arch in {"x86_64", "amd64"} and pyver >= (3, 11),
            systemd_version=version,
            systemd_pid1=pid1,
            cgroup_hierarchy=hierarchy,
            load_credential=load_credential,
            upower_available=upower_available,
            battery_present=battery,
            numa_nodes=numa_nodes,
            helper_paths=helper_paths,
            nproc=nproc_value,
            memory_bytes=memory,
            xmrig_path=xmrig_path,
            xmrig_package=xmrig_package,
            cpuweight_idle=_cpuweight_probe(manager_probe=manager_probe, env=env, euid=uid, cgroup_cpu_idle=cpu_idle),
        )

    def summary(self) -> dict[str, object]:
        """Return a stable, serialization-friendly summary without new probes."""
        return {
            "architecture": self.architecture,
            "python_supported": self.python_supported,
            "systemd_version": self.systemd_version,
            "systemd_pid1": self.systemd_pid1,
            "cgroup_hierarchy": self.cgroup_hierarchy,
            "load_credential": self.load_credential,
            "upower_available": self.upower_available,
            "battery_present": self.battery_present,
            "numa_nodes": self.numa_nodes,
            "helpers": dict(self.helper_paths),
            "nproc": self.nproc,
            "memory_bytes": self.memory_bytes,
            "xmrig_path": self.xmrig_path,
            "xmrig_package": self.xmrig_package,
            "cpuweight_idle": self.cpuweight_idle,
        }
