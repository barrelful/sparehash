# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Timestamped power evidence; tick/run never wait for UPower property queries."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Final, Protocol, final

FIXED_PATH: Final = "/usr/sbin:/usr/bin:/sbin:/bin"
BUS: Final = "org.freedesktop.UPower"
OBJECT: Final = "/org/freedesktop/UPower"
PROPERTIES: Final = "org.freedesktop.DBus.Properties"
EXTERNAL: Final = frozenset(("Mains", "USB", "USB_C", "USB_PD"))
SYSFS_ROOT: Final = Path("/sys")


@dataclass(frozen=True, slots=True)
class Evidence:
    value: bool | None
    at: float

    def credible(self, now: float) -> bool | None:
        return self.value if 0 <= now - self.at <= 10 else None


@dataclass(frozen=True, slots=True)
class PowerSample:
    """Components map directly to Observation; original source timestamps survive."""

    at: float
    inventory: Evidence
    upower_evidence: Evidence
    sysfs_evidence: Evidence

    @property
    def battery_present(self) -> bool | None:
        return self.inventory.credible(self.at)

    @property
    def upower(self) -> bool | None:
        return self.upower_evidence.credible(self.at)

    @property
    def sysfs(self) -> bool | None:
        return self.sysfs_evidence.credible(self.at)

    @property
    def blocked(self) -> bool:
        """Conservative event value for Controller.safety, not a BlockerState."""
        return self.battery_present is not False and (
            self.battery_present is None or self.upower is True or self.sysfs is True
            or (self.upower is not False and self.sysfs is not False))


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None


def read_sysfs(root: Path, now: float) -> tuple[Evidence, Evidence]:
    """A partial inventory cannot prove absence or absence of online mains."""
    try:
        entries = tuple((root / "class/power_supply").iterdir())
    except OSError:
        return Evidence(None, now), Evidence(None, now)
    complete, present, discharging, online, uncertain = True, False, False, False, False
    external_complete = True
    for entry in entries:
        kind = _read(entry / "type")
        if not kind:
            complete = False
        if kind == "Battery":
            installed = _read(entry / "present")
            if installed not in ("0", "1"):
                complete = False
            if installed != "0":
                present = True
                status = _read(entry / "status")
                discharging |= status == "Discharging"
                uncertain |= installed != "1" or status not in ("Discharging", "Charging", "Full", "Not charging")
        if kind in EXTERNAL:
            value = _read(entry / "online")
            online |= value == "1"
            uncertain |= value not in ("0", "1")
            external_complete &= value in ("0", "1")
    inventory = (True if present else False) if complete else None
    battery_value = (True if discharging or (complete and external_complete and not online)
                     else (False if complete and online and not uncertain else None))
    return Evidence(inventory, now), Evidence(battery_value if present else None, now)


class Backend(Protocol):
    def start(self) -> bool: ...
    def alive(self) -> bool: ...
    def events(self) -> bool: ...
    def query(self) -> Future[bool | None]: ...
    def close(self) -> None: ...


@final
class Helpers:
    """Own fixed helpers and one bounded query worker; no environment path overrides."""

    def __init__(self) -> None:
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="power-query")
        self.monitor: subprocess.Popen[bytes] | None = None
        self.tail = b""

    def _call(self, argv: tuple[str, ...]) -> str:
        executable = shutil.which(argv[0], path=FIXED_PATH)
        if executable is None:
            return ""
        try:
            with tempfile.TemporaryFile() as output:
                result = subprocess.run((executable, *argv[1:]), stdout=output,
                                        stderr=subprocess.DEVNULL, timeout=2, check=False,
                                        env={"PATH": FIXED_PATH, "LC_ALL": "C"})
                _ = output.seek(0)
                data: bytes = output.read(8193)
            return data.decode("ascii").strip() if result.returncode == 0 and len(data) <= 8192 else ""
        except (OSError, UnicodeError, subprocess.SubprocessError):
            return ""

    def _current(self) -> bool | None:
        prefix = ("gdbus", "call", "--system", "--dest", BUS, "--object-path", OBJECT, "--method")
        text = self._call((*prefix, PROPERTIES + ".GetAll", BUS))
        matches = tuple(match.group(1) for match in re.finditer(r"'OnBattery': <(true|false)>", text))
        if len(matches) == 1:
            return matches[0] == "true"
        text = self._call((*prefix, PROPERTIES + ".Get", BUS, "OnBattery"))
        match = re.fullmatch(r"\(<(true|false)>,\)", text)
        if match is None:
            text = self._call(("busctl", "--system", "--timeout=2", "get-property", BUS, OBJECT, BUS, "OnBattery"))
            match = re.fullmatch(r"b (true|false)", text)
        return match.group(1) == "true" if match else None

    def query(self) -> Future[bool | None]:
        return self.worker.submit(self._current)

    def start(self) -> bool:
        self._stop()
        executable = shutil.which("gdbus", path=FIXED_PATH)
        if executable is None:
            return False
        try:
            self.monitor = subprocess.Popen((executable, "monitor", "--system", "--dest", BUS,
                                             "--object-path", OBJECT), stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL,
                                            env={"PATH": FIXED_PATH, "LC_ALL": "C"})
            if self.monitor.stdout is not None:
                os.set_blocking(self.monitor.stdout.fileno(), False)
            self.tail = b""
            return self.alive()
        except OSError:
            return False

    def alive(self) -> bool:
        return self.monitor is not None and self.monitor.poll() is None

    def events(self) -> bool:
        if self.monitor is None or self.monitor.stdout is None:
            return False
        try:
            data = os.read(self.monitor.stdout.fileno(), 8192)
        except BlockingIOError:
            return False
        text = self.tail + data
        self.tail = text[-128:]
        return bool(data) and any(text.find(token) + len(token) > len(text) - len(data)
                                 for token in (b"PropertiesChanged", b"DeviceAdded", b"DeviceRemoved", b"owner")
                                 if token in text)

    def _stop(self) -> None:
        if self.monitor is not None:
            if self.monitor.poll() is None:
                self.monitor.terminate()
                try:
                    _ = self.monitor.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.monitor.kill()
                    _ = self.monitor.wait()
            if self.monitor.stdout is not None:
                self.monitor.stdout.close()
            self.monitor = None

    def close(self) -> None:
        self._stop()
        self.worker.shutdown(wait=True, cancel_futures=True)


@final
class PowerMonitor:
    """Mutable evidence supervisor. Use run off-loop; callbacks must enqueue to owner.

    Manual tick callers must tick at least every 0.1s. Queries run independently;
    the 5s inventory cadence is never delayed by a hung UPower helper. Explicit
    roots/backends are test injection only, never derived from IPC/environment.
    """

    def __init__(self, root: Path = SYSFS_ROOT, backend: Backend | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.root, self.backend, self.clock = root, backend or Helpers(), clock
        self.inventory = self.sysfs = self.upower = Evidence(None, clock())
        self.pending: Future[bool | None] | None = None
        self.query_at = self.next_sysfs = self.next_query = self.retry_at = 0.0
        self.generation = self.query_generation = 0
        self.connected = False

    def snapshot(self) -> PowerSample:
        upower = self.upower if self.backend.alive() else Evidence(None, self.upower.at)
        return PowerSample(self.clock(), self.inventory, upower, self.sysfs)

    def tick(self) -> PowerSample:
        now = self.clock()
        alive = self.backend.alive()
        event = alive and self.backend.events()
        if not alive:
            self.upower = Evidence(None, now)
            if self.connected:
                self.generation += 1
                self.connected = False
            if now >= self.retry_at:
                self.connected = self.backend.start()
                self.retry_at = now + 1
                event = True
        if event:
            self.generation += 1
            self.upower = Evidence(None, now)
        if event or now >= self.next_sysfs:
            self.inventory, self.sysfs = read_sysfs(self.root, now)
            self.next_sysfs = now + 5
        if self.pending is not None and self.pending.done():
            if self.query_generation == self.generation and self.backend.alive():
                self.upower = Evidence(self.pending.result(), self.query_at)
            self.pending = None
        if self.pending is None and (event or now >= self.next_query or self.query_generation != self.generation):
            self.query_at, self.query_generation = now, self.generation
            self.pending = self.backend.query()
            self.next_query = now + 30
        return self.snapshot()

    def run(self, stop: Event, emit: Callable[[PowerSample], None]) -> None:
        """Independent 50ms supervision, emits changes including stale/dead evidence."""
        previous: tuple[bool | None, bool | None, bool | None] | None = None
        try:
            while not stop.is_set():
                sample = self.tick()
                values = (sample.battery_present, sample.upower, sample.sysfs)
                if values != previous:
                    emit(sample)
                    previous = values
                _ = stop.wait(0.05)
        finally:
            self.backend.close()
