# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only, fail-closed evidence and thermal monitoring for owner-run QA."""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import re
import ssl
import stat
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path
from typing import Any

Row = dict[str, Any]
UNITS = ("sparehash.service", "sparehash-xmrig.service")
CGROUP = Path("/sys/fs/cgroup")
PROPERTIES = ("Id", "LoadState", "ActiveState", "SubState", "MainPID", "InvocationID",
              "ControlGroup", "CPUWeight", "CPUSchedulingPolicy", "Nice", "CPUAccounting", "Delegate")
OLD_COMMIT = "f348ed165358668d8101cd121cf07ee2f001295f"


class SafetyAbort(RuntimeError):  # noqa: N818 - an explicit fail-closed abort signal
    """A condition that forbids starting/continuing benchmark-owned CPU work."""


def optional_read(path: Path, limit: int = 4096) -> str | None:
    try:
        with path.open(encoding="ascii") as stream:
            data = stream.read(limit + 1)
        return data.strip() if len(data) <= limit else None
    except (OSError, UnicodeError):
        return None


def cgroup_evidence(group: str, root: Path = CGROUP) -> Row:
    if not group or group == "/" or not group.startswith("/") or ".." in Path(group).parts:
        return {"path": group, "readable": False}
    path = root / group.lstrip("/")
    if not path.resolve().is_relative_to(root.resolve()):
        raise SafetyAbort("Unexpected cgroup path")
    return {"path": group, **{name: optional_read(path / name) for name in
                              ("cpu.idle", "cpu.weight", "cpu.max", "cpu.stat", "cpuset.cpus.effective")}}


def foreground_evidence() -> Row:
    membership = optional_read(Path("/proc/self/cgroup"))
    if membership is None:
        raise SafetyAbort("Foreground cgroup membership is unreadable")
    group = next((line[3:] for line in membership.splitlines() if line.startswith("0::")), None)
    if group is None:
        raise SafetyAbort("A cgroup v2 foreground membership is required")
    ancestry = []
    path = Path(group)
    while str(path) != "/":
        ancestry.append(cgroup_evidence(str(path)))
        path = path.parent
    return {"membership": membership, "ancestry": ancestry,
            "policy": os.sched_getscheduler(0), "nice": os.getpriority(os.PRIO_PROCESS, 0)}


def services() -> Row:
    # No status command, config, ExecStart, command lines, environment or journal.
    try:
        proc = subprocess.run(["/usr/bin/systemctl", "show", "--no-pager",
                               "--property=" + ",".join(PROPERTIES), "--", *UNITS],
                              capture_output=True, text=True, timeout=1,
                              env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"})
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SafetyAbort("Service-state query failed or timed out") from error
    if proc.returncode:
        raise SafetyAbort("Service-state query was rejected")
    result: Row = {}
    for block in proc.stdout.strip().split("\n\n"):
        fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        name = fields.get("Id")
        if name not in UNITS or name in result or set(fields) - set(PROPERTIES):
            raise SafetyAbort("Ambiguous service-state evidence")
        pid = fields.get("MainPID", "")
        if re.fullmatch(r"[0-9]{1,10}", pid) is None or fields.get("LoadState") != "loaded":
            raise SafetyAbort("Installed service identity/state could not be established")
        fields_row: Row = dict(fields)
        fields_row["pid"] = int(pid)
        if int(pid):
            try:
                fields_row["policy"] = os.sched_getscheduler(int(pid))
                fields_row["nice"] = os.getpriority(os.PRIO_PROCESS, int(pid))
            except OSError as error:
                raise SafetyAbort("Service process disappeared or scheduling is unreadable") from error
        fields_row["cgroup"] = cgroup_evidence(fields.get("ControlGroup", ""))
        result[str(name)] = fields_row
    if set(result) != set(UNITS):
        raise SafetyAbort("Incomplete service-state evidence")
    return result


def validate_services(row: Row, condition: str, pinned: Row | None = None) -> None:
    if condition not in ("mining", "stopped") or set(row) != set(UNITS):
        raise SafetyAbort("Invalid service condition/evidence")
    for name in UNITS:
        service = row[name]
        expected = ("active", "running") if condition == "mining" else ("inactive", "dead")
        if (service["ActiveState"], service["SubState"]) != expected:
            raise SafetyAbort("Service-state mismatch: " + name)
        if (service["pid"] > 0) != (condition == "mining"):
            raise SafetyAbort("Unexpected service PID state: " + name)
        if pinned and condition == "mining":
            for key in ("pid", "InvocationID", "ControlGroup", "policy", "nice", "CPUWeight"):
                if service.get(key) != pinned[name].get(key):
                    raise SafetyAbort("Service restarted or scheduling changed during measurement")
            for key in ("cpu.idle", "cpu.weight", "cpu.max", "cpuset.cpus.effective"):
                if service["cgroup"].get(key) != pinned[name]["cgroup"].get(key):
                    raise SafetyAbort("Effective cgroup scheduling changed during measurement")
    if condition == "mining":
        miner = row[UNITS[1]]
        if miner["policy"] != os.SCHED_IDLE or miner["nice"] != 19:
            raise SafetyAbort("Miner is not using the expected SCHED_IDLE/nice 19; review it manually")


def cpu_information(cpuinfo: Path = Path("/proc/cpuinfo")) -> Row:
    text = optional_read(cpuinfo, 2**20)
    if text is None:
        raise SafetyAbort("CPU identity is unreadable")
    blocks = [dict(line.split(":", 1) for line in block.splitlines() if ":" in line)
              for block in text.split("\n\n") if block.strip()]
    blocks = [{key.strip(): value.strip() for key, value in block.items()} for block in blocks]
    models = sorted({block["model name"] for block in blocks if "model name" in block})
    cores = {(block.get("physical id"), block.get("core id")) for block in blocks if "core id" in block}
    affinity = sorted(os.sched_getaffinity(0))
    if not models or not affinity:
        raise SafetyAbort("CPU model/available affinity could not be established")
    return {"models": models, "logical_cpus": os.cpu_count(), "physical_cores": len(cores) or None,
            "affinity": affinity, "kernel": platform.release(), "architecture": platform.machine(),
            "python": sys.version.split()[0], "openssl": ssl.OPENSSL_VERSION}


class Thermometer:
    def __init__(self, root: Path = Path("/sys/class/hwmon")) -> None:
        self.sensors: list[Row] = []
        for name in sorted(root.glob("hwmon*/name")):
            driver = optional_read(name)
            if driver not in ("k10temp", "zenpower", "coretemp"):
                continue
            for path in sorted(name.parent.glob("temp*_input")):
                self.sensors.append({"path": str(path), "driver": driver,
                                     "label": optional_read(path.with_name(path.name.replace("_input", "_label"))) or path.name})
        if not self.sensors:
            raise SafetyAbort("No readable credible CPU temperature sensor was discovered")

    def read(self) -> list[float]:
        values = []
        for sensor in self.sensors:
            raw = optional_read(Path(sensor["path"]), 32)
            if raw is None or re.fullmatch(r"[0-9]{1,6}", raw) is None or not 0 < int(raw) < 150000:
                raise SafetyAbort("CPU thermal monitor failed: missing, unreadable or malformed sensor")
            values.append(int(raw) / 1000)
        return values


class ThermalMonitor:
    interval = 0.2
    stale_seconds = 1.0

    def __init__(self, thermometer: Thermometer, abort_c: float) -> None:
        self.thermometer, self.abort_c = thermometer, abort_c
        self.records: list[Row] = []
        self.failure: str | None = None
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="benchmark-thermal")

    def sample(self) -> None:
        values = self.thermometer.read()
        with self.lock:
            self.records.append({"at": time.monotonic(), "values_c": values, "max_c": max(values)})
            if max(values) >= self.abort_c:
                self.failure = "CPU reached the thermal abort threshold"
            if len(self.records) >= 20000:
                self.failure = "Thermal trace safety limit exceeded"

    def start(self) -> None:
        self.sample()
        self.check()
        self.thread.start()

    def _loop(self) -> None:
        while not self.stop.wait(self.interval):
            try:
                self.sample()
            except Exception:
                with self.lock:
                    self.failure = "CPU thermal monitor failed"
                return
            if self.failure:
                return

    def check(self) -> None:
        with self.lock:
            if self.failure:
                raise SafetyAbort(self.failure)
            if not self.records or time.monotonic() - self.records[-1]["at"] > self.stale_seconds:
                raise SafetyAbort("CPU temperature evidence became stale")

    def trace(self) -> list[Row]:
        with self.lock:
            return list(self.records)

    def close(self) -> None:
        self.stop.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=1)


def _inventory_digest(files: dict[str, bytes]) -> str:
    hashes = {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}
    return hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def installed_identity(repo: Path, installed: Path = Path("/opt/sparehash/src"), expected_uid: int = 0) -> Row:
    """Hash only known public source files; never read manifests/configs/secrets."""
    allowed = [str(path.relative_to(repo)) for folder, pattern in (("sparehash", "*.py"), ("systemd", "*.in"))
               for path in sorted((repo / folder).glob(pattern))]
    if not allowed:
        return {"available": False, "reason": "Tool checkout lacks the public source inventory"}
    files = {}
    try:
        resolved = installed.resolve(strict=True)
        if resolved != installed:
            # Bazzite's established atomic redirect is the sole accepted alias;
            # never accept arbitrary source-directory symlinks as version proof.
            redirect = Path("/opt").lstat()
            if (installed != Path("/opt/sparehash/src") or resolved != Path("/var/opt/sparehash/src")
                    or not stat.S_ISLNK(redirect.st_mode) or redirect.st_uid != expected_uid
                    or os.readlink("/opt") not in ("var/opt", "/var/opt")):
                raise OSError("untrusted source redirect")
            installed = resolved
        for parent in (installed, *installed.parents):
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != expected_uid or info.st_mode & 0o022:
                raise OSError("untrusted source ancestor")
        for name in allowed:
            path = installed / name
            info = path.parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != expected_uid or info.st_mode & 0o022:
                raise OSError("untrusted source directory")
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != expected_uid or info.st_mode & 0o022 or info.st_size > 256 * 1024:
                    raise OSError("untrusted source metadata")
                value = stream.read(256 * 1024 + 1)
                if len(value) > 256 * 1024:
                    raise OSError("oversized source")
                files[name] = value
    except OSError:
        return {"available": False, "reason": "Installed public source is missing, unreadable or untrusted"}
    fingerprint = _inventory_digest(files)
    matches = []
    for ref in (OLD_COMMIT, "HEAD"):
        try:
            proc = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(repo),
                                   "archive", "--format=tar", ref, "sparehash", "systemd"],
                                  capture_output=True, timeout=5)
            if proc.returncode or len(proc.stdout) > 8 * 2**20:
                continue
            with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as archive:
                expected = {}
                for member in archive.getmembers():
                    if member.isfile() and member.name in allowed and member.size <= 256 * 1024:
                        archived = archive.extractfile(member)
                        if archived is not None:
                            expected[member.name] = archived.read()
            if set(expected) != set(files) or _inventory_digest(expected) != fingerprint:
                continue
            commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", ref],
                                    capture_output=True, text=True, timeout=5)
            if commit.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}\n?", commit.stdout):
                matches.append(commit.stdout.strip())
        except (OSError, subprocess.SubprocessError, tarfile.TarError):
            continue
    return {"available": True, "fingerprint": fingerprint, "file_count": len(files),
            "matching_source_commits": sorted(set(matches)),
            "meaning": "Production source equality only; not proof of the installation commit or deployed miner binary"}
