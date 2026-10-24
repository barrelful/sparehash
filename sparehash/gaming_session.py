# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Worker-side conservative fallback with optional Steam evidence.

Every evaluation resnapshots logind; the cache is diagnostic, never clearance
authority. Call on a provider worker, not the controller selector. This module
does not schedule polling or claim the C4 latency/overhead acceptance budgets.
Only standard logind user.slice containment is covered, not arbitrary games.
"""

# SIZE_OK: Task 14 requires the probe/guard lifetime-proof boundary in this one module.

from __future__ import annotations

import math
import os
import re
import stat
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Protocol, final

from .gaming import resolve_helper
from .gaming_state import GamingEvidence, GamingReducer
from .sysdetect import FIXED_PATH

SessionClass = Literal["user", "greeter", "lock-screen", "background", "user-shared",
                       "user-early-session", "user-inexistent", "manager"]
Health = Literal["OK", "STALE", "DISCONNECTED", "MALFORMED", "OVERFLOW"]
REQUIRED: Final = frozenset({"logind.list_sessions", "logind.seat"})
CLASSES: Final[dict[str, SessionClass]] = {
    "user": "user", "greeter": "greeter", "lock-screen": "lock-screen",
    "background": "background", "user-shared": "user-shared",
    "user-early-session": "user-early-session", "user-inexistent": "user-inexistent",
    # Bazzite/Gamescope reports the graphical seat session with Class=manager.
    "manager": "manager",
}
CGROUP_ROOT: Final = Path("/sys/fs/cgroup")
DIR_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
LIMIT: Final = 128
READ_LIMIT: Final = 65536


@dataclass(frozen=True, slots=True)
class SessionInventory:
    uid: int
    session_id: str
    service: str
    klass: SessionClass
    remote: bool
    seat_id: str | None
    idle_hint: bool | None
    cgroup_path: str | None


@dataclass(frozen=True, slots=True)
class InventoryResult:
    sessions: tuple[SessionInventory, ...]
    observed_at: float
    health: Health
    completeness: frozenset[str]
    error: str | None


@dataclass(frozen=True, slots=True)
class CgroupEvidence:
    """Recursive proof; uid=None is the mandatory whole-user.slice coverage."""

    uid: int | None
    cgroup_path: str
    observed_at: float
    procs_empty: bool
    populated: bool
    complete: bool
    error: str | None


class SessionProbe(Protocol):
    def inventory(self) -> InventoryResult: ...


class CgroupProbe(Protocol):
    def snapshot(self, uids: tuple[int, ...]) -> tuple[CgroupEvidence, ...]: ...


class SteamEvidenceSource(Protocol):
    @property
    def uid(self) -> int: ...

    def evaluate(self, epoch: int, *, now: float) -> GamingEvidence: ...


@final
class ProbeError(ValueError):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


@final
class LogindSessionProbe:
    """Fixed protected system-bus helper, with one total snapshot deadline.

    Get probes deployed property availability. A second ListSessions detects
    topology changes during the snapshot. No user-bus code is executed as root.
    """

    def __init__(self) -> None:
        self._deadline = 0.0

    def _call(self, path: str, method: str, args: tuple[str, ...]) -> str:
        helper = resolve_helper("dbus-send")
        remaining = self._deadline - time.monotonic()
        if helper is None or remaining <= 0:
            raise OSError("protected dbus-send unavailable or snapshot deadline exceeded")
        result = subprocess.run(
            (helper, "--system", "--print-reply", "--reply-timeout=750",
             "--dest=org.freedesktop.login1", path, method, *args),
            timeout=remaining, check=False, capture_output=True, text=True,
            env={"PATH": FIXED_PATH, "LC_ALL": "C"},
        )
        if result.returncode:
            raise OSError(result.stderr or f"dbus-send exited {result.returncode}")
        if len(result.stdout) > READ_LIMIT:
            raise OverflowError("logind reply exceeds snapshot limit")
        header, separator, body = result.stdout.partition("\n")
        if not separator or not header.startswith("method return "):
            raise ProbeError("missing D-Bus method return header")
        return body.strip()

    def _sessions(self) -> tuple[tuple[str, int, str, str], ...]:
        text = self._call("/org/freedesktop/login1", "org.freedesktop.login1.Manager.ListSessions", ())
        array = re.fullmatch(r"array\s*\[(.*)\]", text, re.S)
        if array is None:
            raise ProbeError("invalid ListSessions array")
        row = re.compile(r'\s*struct\s*\{\s*string "([a-zA-Z0-9_-]+)"\s+uint32 (\d+)'
                         + r'\s+string "[^"\\\n]*"\s+string "([a-zA-Z0-9_-]*)"'
                         + r'\s+object path "(/org/freedesktop/login1/session/[a-zA-Z0-9_]+)"\s*\}')
        content, offset = array[1], 0
        rows: list[tuple[str, int, str, str]] = []
        while content[offset:].strip():
            match = row.match(content, offset)
            if match is None:
                raise ProbeError("invalid ListSessions row")
            if int(match[2]) > 4294967295:
                raise ProbeError("invalid logind UID")
            rows.append((match[1], int(match[2]), match[3], match[4]))
            offset = match.end()
            if len(rows) > LIMIT:
                raise OverflowError("too many sessions")
        if len({row[0] for row in rows}) != len(rows):
            raise ProbeError("duplicate session identity")
        return tuple(rows)

    def _property(self, path: str, name: str, kind: str) -> str:
        text = self._call(path, "org.freedesktop.DBus.Properties.Get",
                          ("string:org.freedesktop.login1.Session", f"string:{name}"))
        value = re.fullmatch(r"variant\s+" + kind, text, re.S)
        if value is None:
            raise ProbeError(f"invalid session {name}")
        return value[1]

    def inventory(self) -> InventoryResult:
        observed_at = time.monotonic()
        self._deadline = observed_at + 0.75
        try:
            rows = self._sessions()
            sessions: list[SessionInventory] = []
            for session_id, uid, seat, path in rows:
                klass = self._property(path, "Class", r'string "([^"\\\n]+)"')
                if klass not in CLASSES:
                    raise ProbeError(f"unsupported session class: {klass}")
                session_type = self._property(path, "Type", r'string "([^"\\\n]+)"')
                if session_type not in {"unspecified", "tty", "x11", "wayland", "mir", "web"}:
                    raise ProbeError(f"unsupported session type: {session_type}")
                service = self._property(path, "Service", r'string "([^"\\\n]*)"')
                idle = self._property(path, "IdleHint", r"boolean (true|false)") == "true"
                remote = self._property(path, "Remote", r"boolean (true|false)") == "true"
                actual_seat = self._property(path, "Seat", r'struct\s*\{\s*string "([a-zA-Z0-9_-]*)"\s+object path "/[a-zA-Z0-9_/]*"\s*\}')
                if actual_seat != seat:
                    raise ProbeError("session seat changed during inventory")
                sessions.append(SessionInventory(uid, session_id, service, CLASSES[klass], remote,
                                                 seat or None, idle, None))
            if rows != self._sessions() or time.monotonic() > self._deadline:
                return InventoryResult((), observed_at, "STALE", frozenset(), "session topology changed")
            return InventoryResult(tuple(sessions), observed_at, "OK", REQUIRED, None)
        except OverflowError as exc:
            return InventoryResult((), observed_at, "OVERFLOW", frozenset(), str(exc))
        except (ProbeError, UnicodeError) as exc:
            return InventoryResult((), observed_at, "MALFORMED", frozenset(), str(exc))
        except (OSError, subprocess.SubprocessError) as exc:
            return InventoryResult((), observed_at, "DISCONNECTED", frozenset(), str(exc))


@contextmanager
def _directory(path: str, parent: int | None = None) -> Iterator[int]:
    fd = os.open(path, DIR_FLAGS, dir_fd=parent)
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise ProbeError("cgroup directory changed type")
        yield fd
    finally:
        os.close(fd)


def _read(parent: int, name: str) -> str:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ProbeError(f"nonregular cgroup interface: {name}")
        data = stream.read(READ_LIMIT + 1)
    if len(data) > READ_LIMIT:
        raise OverflowError("cgroup interface exceeds read limit")
    return data.decode("ascii")


def _names(fd: int) -> set[str]:
    names: set[str] = set()
    with os.scandir(fd) as entries:
        for entry in entries:
            if len(names) >= 4096:
                raise OverflowError("cgroup directory entry limit exceeded")
            names.add(entry.name)
    return names


@final
class CgroupwalkProbe:
    """No-follow descriptor walk; includes unconfigured/lingering user managers.

    Directory flags include O_DIRECTORY; kernel pseudo-files necessarily use
    O_RDONLY|O_NOFOLLOW instead. An explicitly supplied root is a test seam.
    """

    def __init__(self, root: Path = CGROUP_ROOT, *, clock: Callable[[], float] = time.monotonic) -> None:
        if not root.is_absolute() or ".." in root.parts:
            raise ProbeError("cgroup root must be absolute without traversal")
        self.root, self.clock = root, clock
        self._deadline = 0.0

    def _walk(self, fd: int, path: str, results: dict[str, tuple[bool, bool]]) -> tuple[bool, bool]:
        if len(results) >= 1024 or path.count("/") > 64 or self.clock() > self._deadline:
            raise OverflowError("cgroup walk budget exceeded")
        results[path] = (False, True)
        before = _names(fd)
        procs = _read(fd, "cgroup.procs")
        if any(not line.isdecimal() for line in procs.splitlines()):
            raise ProbeError("malformed cgroup.procs")
        events = _read(fd, "cgroup.events").splitlines()
        populated = [line for line in events if line.startswith("populated ")]
        if populated not in (["populated 0"], ["populated 1"]):
            raise ProbeError("missing or duplicate populated evidence")
        empty, busy = not procs.strip(), populated == ["populated 1"]
        for name in before:
            try:
                with _directory(name, fd) as child:
                    child_empty, child_busy = self._walk(child, f"{path}/{name}", results)
                    with _directory(name, fd) as current:
                        if not os.path.samestat(os.fstat(current), os.fstat(child)):
                            raise ProbeError("cgroup identity changed")
                empty, busy = empty and child_empty, busy or child_busy
            except NotADirectoryError:
                leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
                with os.fdopen(leaf, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise ProbeError("nonregular cgroup subtree entry") from None
        if before != _names(fd) or _read(fd, "cgroup.procs") != procs or _read(fd, "cgroup.events").splitlines() != events:
            raise ProbeError("cgroup changed during walk")
        results[path] = empty, busy
        return empty, busy

    def snapshot(self, uids: tuple[int, ...]) -> tuple[CgroupEvidence, ...]:
        observed_at = self.clock()
        self._deadline = observed_at + 0.75
        paths = ((None, "/user.slice"), *((uid, f"/user.slice/user-{uid}.slice") for uid in uids))
        try:
            results: dict[str, tuple[bool, bool]] = {}
            with ExitStack() as stack:
                fd = stack.enter_context(_directory("/"))
                for component in self.root.parts[1:]:
                    fd = stack.enter_context(_directory(component, fd))
                    info = os.fstat(fd)
                    if self.root == CGROUP_ROOT and (info.st_uid != 0 or info.st_mode & 0o022):
                        raise ProbeError("unprotected cgroup ancestry")
                _ = _read(fd, "cgroup.controllers")
                before = _names(fd)
                if "user.slice" in before:
                    with _directory("user.slice", fd) as user:
                        _ = self._walk(user, "/user.slice", results)
                        with _directory("user.slice", fd) as current:
                            if not os.path.samestat(os.fstat(current), os.fstat(user)):
                                raise ProbeError("user.slice identity changed")
                if before != _names(fd):
                    raise ProbeError("cgroup root changed during walk")
            return tuple(CgroupEvidence(uid, path, observed_at, *results.get(path, (True, False)), True, None)
                         for uid, path in paths)
        except (OSError, ProbeError, OverflowError, UnicodeError) as exc:
            return tuple(CgroupEvidence(uid, path, observed_at, False, True, False, str(exc)) for uid, path in paths)


@final
class GamingSessionGuard:
    """Mutable owner-thread history; removing configuration never erases a lifetime."""

    def __init__(self, session_probe: SessionProbe, cgroup_probe: CgroupProbe, covered_uids: tuple[int, ...],
                 cgroup_max_age_seconds: float = 5.0, tracked_sessions_refresh_seconds: float = 30.0, *,
                 steam_producers: tuple[SteamEvidenceSource, ...] = ()) -> None:
        if any(not math.isfinite(age) or age <= 0 for age in (cgroup_max_age_seconds, tracked_sessions_refresh_seconds)):
            raise ProbeError("freshness budgets must be finite and positive")
        self.session_probe, self.cgroup_probe = session_probe, cgroup_probe
        self.cgroup_max_age_seconds = cgroup_max_age_seconds
        self.tracked_sessions_refresh_seconds = tracked_sessions_refresh_seconds
        self.cached_inventory: InventoryResult | None = None
        self._tracked_uids: tuple[int, ...] = ()
        self._coverage_overflow = False
        self.set_tracked_uids(covered_uids)
        if len(steam_producers) > LIMIT or len({source.uid for source in steam_producers}) != len(steam_producers):
            raise ProbeError("invalid or duplicate Steam coverage")
        self.steam_producers = steam_producers
        self._steam = GamingReducer(cgroup_max_age_seconds)
        self._steam_sessions: tuple[SessionInventory, ...] | None = None
        if steam_producers:
            self.set_tracked_uids(tuple(source.uid for source in steam_producers))

    @property
    def tracked_uids(self) -> tuple[int, ...]:
        return self._tracked_uids

    def set_tracked_uids(self, uids: tuple[int, ...]) -> None:
        if len(uids) > LIMIT or any(type(uid) is not int or uid < 0 for uid in uids):
            raise ProbeError("invalid or excessive UID coverage")
        combined = tuple(sorted(set(self.tracked_uids).union(uids)))
        self._coverage_overflow |= len(combined) > LIMIT
        self._tracked_uids = combined[:LIMIT]
        self.cached_inventory = None

    def evaluate(self, now: float) -> Literal["BLOCKED", "UNKNOWN", "CLEAR"]:
        started = time.monotonic()
        inventory = self.session_probe.inventory()
        self.cached_inventory = None
        if inventory.health != "OK" or inventory.error is not None or not inventory.completeness.issuperset(REQUIRED):
            return "UNKNOWN"
        if len(inventory.sessions) > LIMIT:
            return "UNKNOWN"
        self.set_tracked_uids(tuple(session.uid for session in inventory.sessions))
        if self._coverage_overflow or not self.tracked_uids or not math.isfinite(now):
            return "UNKNOWN"
        groups = self.cgroup_probe.snapshot(self.tracked_uids)
        finished = now + (time.monotonic() - started)
        if not math.isfinite(inventory.observed_at) or inventory.observed_at > finished or now - inventory.observed_at > self.tracked_sessions_refresh_seconds:
            return "UNKNOWN"
        self.cached_inventory = inventory
        expected = {(None, "/user.slice"), *((uid, f"/user.slice/user-{uid}.slice") for uid in self.tracked_uids)}
        actual = {(group.uid, group.cgroup_path) for group in groups}
        if any(not group.complete or group.error is not None or not math.isfinite(group.observed_at)
               or group.observed_at > finished or now - group.observed_at > self.cgroup_max_age_seconds
               for group in groups):
            return "UNKNOWN"
        if actual != expected or len(groups) != len(expected):
            return "UNKNOWN"
        if self.steam_producers:
            self._steam.set_coverage(tuple(f"steam-log:{uid}" for uid in self.tracked_uids))
            if inventory.sessions != self._steam_sessions:
                self._steam.bump_epoch()
                self._steam_sessions = inventory.sessions
            epoch = self._steam.current_epoch
            # This guard is called by Providers' gaming worker, never the selector.
            # Use the caller's clock: producer timestamps are not re-stamped here.
            samples = tuple(source.evaluate(epoch, now=now) for source in self.steam_producers)
            decision = self._steam.reduce(samples, now=now)
            # The reducer intentionally ignores old/replayed samples. A failed
            # *current* query must not borrow its previously cached clearance.
            current = all(
                sample.provider == "steam-log" and sample.uid == source.uid
                and sample.epoch == epoch and sample.state == "INACTIVE"
                and sample.health == "OK" and bool(sample.producer_version)
                and f"steam-log:{source.uid}" in sample.completeness
                and math.isfinite(sample.observed_at)
                and 0 <= now - sample.observed_at <= self._steam.max_age_seconds
                for source, sample in zip(self.steam_producers, samples, strict=True)
            )
            if decision == "CLEAR" and not current:
                decision = "UNKNOWN"
            if decision != "CLEAR":
                return "BLOCKED" if inventory.sessions or decision == "BLOCKED" else "UNKNOWN"
            # The owner chose producer evidence as the idle authority: a normal
            # logged-in desktop always populates user.slice, so requiring an empty
            # slice here would make clearance unreachable. The cgroup snapshot above
            # still gates probe health; it is diagnostic, not permission.
            return "CLEAR"
        elif inventory.sessions:
            return "BLOCKED"
        if any(not group.procs_empty or group.populated for group in groups):
            return "UNKNOWN"
        return "CLEAR"
