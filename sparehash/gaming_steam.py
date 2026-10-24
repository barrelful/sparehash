# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Rootless, bounded Steam process-log evidence for the gaming worker.

The caller supplies the log and client PID; neither is discovered by scanning.
Byte-zero replay with paired records establishes *log* continuity, not Steam
build authentication or coverage of games outside this log. See the integration
contract for the clean-start assumption and the unchanged cgroup clearance gate.
"""

from __future__ import annotations

import ctypes
import hashlib
import math
import os
import re
import select
import stat
import struct
import time
import uuid
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final

from .gaming_state import GamingEvidence, GamingHealth, GamingState

MAX_HISTORY_BYTES: Final = 1048576
MAX_LINE_LENGTH: Final = 4096
MAX_TRACKED_PROCESSES: Final = 64
MAX_EVENTS: Final = 4096
FRESHNESS_SECONDS: Final = 5.0
# Only these leave the retained history provably intact, so a replacement reader
# may legitimately retry. Anything else could have hidden an event and latches.
RECOVERABLE_REASONS: Final[frozenset[str]] = frozenset({
    "source_changed_during_read", "consumer_freshness", "evaluation_expired", "read_error",
})
FORMAT_VERSION: Final = "steam-process-log-v1"
_HEADER: Final = struct.Struct("iIII")
_WATCH: Final = 0x2 | 0x4 | 0x400 | 0x800 | 0x2000
_LOSS: Final = 0x400 | 0x800 | 0x2000 | 0x8000
_PREFIX: Final = rb"\[([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2})\] AppID "
_ADD: Final = re.compile(
    _PREFIX + rb"([0-9]{1,20}) adding PID ([0-9]{1,10}) as a tracked process(?: [^\r\n]*)?\r?"
)
_REMOVE: Final = re.compile(
    _PREFIX + rb"([0-9]+) no longer tracking PID ([0-9]+), exit code -?[0-9]+"
)


def canonical_source(path: Path) -> Path:
    """Return the caller-supplied path with every symlinked ancestor resolved.

    Atomic distributions expose ``/home`` as a symlink to ``/var/home``, and the
    reader deliberately refuses symlinked path components while it opens. The
    caller owns path selection, so it resolves the location once here; the final
    component must still be a real file, which the reader re-checks on every
    snapshot.
    """
    return Path(os.path.realpath(path))


class SteamSourceError(ValueError):
    """Fixed-vocabulary failure; never include private source bytes."""

    def __init__(self, health: GamingHealth, reason: str) -> None:
        self.health: GamingHealth = health
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class SteamRecord:
    """AppID is a storefront association, not an authenticated game identity."""

    app_id: str
    pid: int
    adding: bool
    timestamp: str


def parse_record(line: bytes) -> SteamRecord | None:
    """Parse a complete line without its LF; discard the private addition suffix."""
    addition = _ADD.fullmatch(line)
    match = addition if addition is not None else _REMOVE.fullmatch(line)
    if match is not None:
        try:
            return SteamRecord(match[2].decode("ascii"), int(match[3]),
                               addition is not None, match[1].decode("ascii"))
        except ValueError:
            raise SteamSourceError("MALFORMED", "numeric_limit") from None
    if any(marker in line for marker in (
        b"adding PID", b"no longer tracking", b"tracked process", b"tracking PID",
    )):
        raise SteamSourceError("MALFORMED", "tracking_grammar")
    return None


def _start_ticks(pid: int, uid: int) -> int:
    """Read bounded stat metadata for exactly one explicit PID, never its args."""
    if not 0 < pid < 2**31:
        raise SteamSourceError("DISCONNECTED", "invalid_pid")
    directory = Path(f"/proc/{pid}")
    if directory.stat().st_uid != uid:
        raise SteamSourceError("DISCONNECTED", "foreign_process")
    with (directory / "stat").open("rb") as stream:
        data = stream.read(4097)
    if len(data) > 4096:
        raise SteamSourceError("OVERFLOW", "process_stat_limit")
    try:
        value = int(data.rpartition(b") ")[2].split()[19])
    except (ValueError, IndexError):
        raise SteamSourceError("MALFORMED", "process_stat") from None
    if value <= 0:
        raise SteamSourceError("MALFORMED", "process_start")
    return value


def _pidfd_open(pid: int) -> int:
    # Some supported Python builds omit os.pidfd_open; use Linux libc directly.
    libc = ctypes.CDLL(None, use_errno=True)
    libc.pidfd_open.argtypes = [ctypes.c_int, ctypes.c_uint]
    libc.pidfd_open.restype = ctypes.c_int
    fd = int(libc.pidfd_open(pid, 0))
    if fd < 0:
        raise OSError(ctypes.get_errno(), "pidfd_open")
    return fd


def _pin(pid: int, uid: int, holder: ExitStack) -> tuple[int, int]:
    before = _start_ticks(pid, uid)
    fd = _pidfd_open(pid)
    holder.callback(os.close, fd)
    if before != _start_ticks(pid, uid) or select.select([fd], [], [], 0)[0]:
        raise SteamSourceError("DISCONNECTED", "process_identity_lost")
    return fd, before


class _SteamLogReader:
    """One latched reader incarnation with mutable reconciliation state.

    Use as a context manager. Losses latch until a new instance is created;
    changing the reducer epoch cannot repair a lost source. The owner must stop
    requesting evidence before closing. No background thread or source writes.
    """

    def __init__(
        self, path: Path, client_pid: int, *, uid: int | None = None,
        max_history_bytes: int = MAX_HISTORY_BYTES, max_line_length: int = MAX_LINE_LENGTH,
        max_tracked_processes: int = MAX_TRACKED_PROCESSES, max_events: int = MAX_EVENTS,
        freshness_seconds: float = FRESHNESS_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if any(type(limit) is not int or limit <= 0 for limit in (
            max_history_bytes, max_line_length, max_tracked_processes, max_events,
        )) or not math.isfinite(freshness_seconds) or freshness_seconds <= 0:
            raise SteamSourceError("OVERFLOW", "invalid_budget")
        self.path, self.client_pid = path, client_pid
        self.uid = os.getuid() if uid is None else uid
        self.max_history_bytes, self.max_line_length = max_history_bytes, max_line_length
        self.max_tracked_processes, self.max_events = max_tracked_processes, max_events
        self.freshness_seconds, self.clock = freshness_seconds, clock
        self._resources = ExitStack()
        self._fd = self._notifications = self._client_fd = -1
        self._client_start = 0
        self._health: GamingHealth = "DISCONNECTED"
        self._continuity_lost = False
        self._entered = False
        self._sequence = 0
        self._remaining = max_events
        self._last: float | None = None
        self._size = 0
        self._digest = hashlib.sha256(b"").digest()
        self._metadata: tuple[int, int] | None = None
        self._pins: dict[int, tuple[int, int, ExitStack]] = {}
        # A fresh consumer is a new producer incarnation, even for the same inode.
        self._incarnation = uuid.uuid4().hex
        self.producer_version = self._incarnation

    def _open_file(self, holder: ExitStack) -> int:
        if not self.path.is_absolute() or ".." in self.path.parts or len(self.path.parts) > 64:
            raise SteamSourceError("DISCONNECTED", "source_path")
        with ExitStack() as parents:
            parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            parents.callback(os.close, parent)
            for component in self.path.parts[1:-1]:
                parent = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                                 | os.O_CLOEXEC, dir_fd=parent)
                parents.callback(os.close, parent)
            fd = os.open(self.path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                         | os.O_CLOEXEC, dir_fd=parent)
            holder.callback(os.close, fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != self.uid or info.st_nlink != 1:
            raise SteamSourceError("DISCONNECTED", "source_not_owned_regular")
        return fd

    def __enter__(self) -> _SteamLogReader:
        if self._entered:
            raise SteamSourceError("DISCONNECTED", "consumer_already_entered")
        self._entered = True
        try:
            if os.getuid() == 0 or self.uid != os.getuid() or os.geteuid() != self.uid:
                raise SteamSourceError("DISCONNECTED", "requires_same_unprivileged_uid")
            self._client_fd, self._client_start = _pin(self.client_pid, self.uid, self._resources)
            self._fd = self._open_file(self._resources)
            libc = ctypes.CDLL(None, use_errno=True)
            libc.inotify_init1.argtypes = [ctypes.c_int]
            libc.inotify_init1.restype = ctypes.c_int
            libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            libc.inotify_add_watch.restype = ctypes.c_int
            self._notifications = int(libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC))
            if self._notifications < 0:
                raise OSError(ctypes.get_errno(), "inotify_init1")
            self._resources.callback(os.close, self._notifications)
            if libc.inotify_add_watch(self._notifications, f"/proc/self/fd/{self._fd}".encode(), _WATCH) < 0:
                raise OSError(ctypes.get_errno(), "inotify_add_watch")
            info = os.fstat(self._fd)
            version = (f"{FORMAT_VERSION}:{self.uid}:{self.client_pid}:{self._client_start}:"
                       f"{info.st_dev}:{info.st_ino}:{self._incarnation}")
            self.producer_version = hashlib.sha256(version.encode("ascii")).hexdigest()
            self._health = "OK"
        except SteamSourceError as exc:
            self._health = exc.health
            self._resources.close()
        except (OSError, AttributeError):
            self._resources.close()
        return self

    def close(self) -> None:
        self._health = "DISCONNECTED"
        for _, _, holder in self._pins.values():
            holder.close()
        self._pins.clear()
        self._resources.close()

    def __exit__(self, kind: type[BaseException] | None, value: BaseException | None,
                 traceback: TracebackType | None) -> None:
        self.close()

    def fileno(self) -> int:
        """Readable on source changes/loss; registration must precede mutation."""
        return self._notifications

    def _drain(self) -> bool:
        try:
            data = os.read(self._notifications, _HEADER.size * (self.max_events + 1))
        except BlockingIOError:
            return False
        if not data:
            raise SteamSourceError("DISCONNECTED", "notification_eof")
        offset, count = 0, 0
        while offset < len(data):
            count += 1
            self._remaining -= 1
            if count > self.max_events or self._remaining < 0:
                raise SteamSourceError("OVERFLOW", "notification_limit")
            if len(data) - offset < _HEADER.size:
                raise SteamSourceError("MALFORMED", "notification_header")
            _, mask, _, size = _HEADER.unpack_from(data, offset)
            offset += _HEADER.size + size
            if offset > len(data):
                raise SteamSourceError("MALFORMED", "notification_size")
            if mask & 0x4000:
                raise SteamSourceError("OVERFLOW", "notification_overflow")
            if mask & _LOSS:
                raise SteamSourceError("DISCONNECTED", "notification_loss")
        return True

    def _snapshot(self) -> bytes:
        changed = self._drain()
        before = os.fstat(self._fd)
        with ExitStack() as check:
            named = os.fstat(self._open_file(check))
        if not os.path.samestat(before, named):
            raise SteamSourceError("DISCONNECTED", "source_replaced")
        if before.st_size > self.max_history_bytes:
            raise SteamSourceError("OVERFLOW", "history_limit")
        if before.st_size < self._size:
            raise SteamSourceError("DISCONNECTED", "source_truncated")
        metadata = before.st_mtime_ns, before.st_ctime_ns
        if (self._metadata is not None and before.st_size == self._size
                and (changed or metadata != self._metadata)):
            raise SteamSourceError("DISCONNECTED", "source_rewritten")
        data_holder: list[bytes] = []
        try:
            data_holder.append(os.pread(self._fd, before.st_size, 0))
        except OSError:
            # A failed read advances no cursor state, so continuity is intact.
            raise SteamSourceError("STALE", "read_error") from None
        data = data_holder[0]
        after = os.fstat(self._fd)
        if len(data) < before.st_size or after.st_size < before.st_size:
            # A shrink, even one that races the read, lost bytes we never saw.
            raise SteamSourceError("DISCONNECTED", "source_truncated_during_read")
        # Verify the retained prefix from the bytes actually read before deciding
        # whether the interruption was a benign append. A corrupted prefix must
        # never be excused as a race.
        retained = min(self._size, len(data))
        if hashlib.sha256(data[:retained]).digest() != self._digest:
            raise SteamSourceError("DISCONNECTED", "history_rewritten")
        final = after.st_mtime_ns, after.st_ctime_ns
        if self._metadata is not None and (
                (after.st_size == self._size and final != self._metadata)
                or (after.st_size == before.st_size == self._size and changed)):
            # Same length but written again: content may have been replaced and
            # restored, so no append-only argument survives.
            raise SteamSourceError("DISCONNECTED", "source_rewritten_same_size")
        if (len(data) != before.st_size or final != metadata
                or before.st_size != after.st_size or self._drain()):
            # The interruption might still be a pure append. Re-verify the retained
            # prefix from the current bytes before calling this recoverable.
            current = os.pread(self._fd, min(self._size, after.st_size), 0)
            if hashlib.sha256(current).digest() != self._digest:
                raise SteamSourceError("DISCONNECTED", "history_rewritten")
            raise SteamSourceError("STALE", "source_changed_during_read")
        if hashlib.sha256(data[:self._size]).digest() != self._digest:
            raise SteamSourceError("DISCONNECTED", "history_rewritten")
        self._size, self._metadata = len(data), metadata
        self._digest = hashlib.sha256(data).digest()
        return data

    def _replay(self, data: bytes) -> tuple[dict[int, SteamRecord], bool]:
        lines = data.split(b"\n")
        pending = lines.pop()
        if len(pending) > self.max_line_length or any(len(line) > self.max_line_length for line in lines):
            raise SteamSourceError("OVERFLOW", "line_limit")
        self._remaining -= len(lines)
        if self._remaining < 0:
            raise SteamSourceError("OVERFLOW", "event_limit")
        tracked: dict[int, SteamRecord] = {}
        any_add = False
        for raw_line in lines:
            # The storefront writes CRLF on Linux; strip exactly one trailing CR.
            line = raw_line[:-1] if raw_line.endswith(b"\r") else raw_line
            record = parse_record(line)
            if record is None:
                continue
            if record.adding:
                # A second add while this PID is still tracked is contradictory;
                # a later add after a matching removal is ordinary PID reuse.
                if record.pid in tracked:
                    raise SteamSourceError("MALFORMED", "duplicate_or_reused_pid")
                tracked[record.pid] = record
                any_add = True
                if len(tracked) > self.max_tracked_processes:
                    raise SteamSourceError("OVERFLOW", "tracked_limit")
            else:
                prior = tracked.pop(record.pid, None)
                if prior is None or prior.app_id != record.app_id:
                    raise SteamSourceError("MALFORMED", "unmatched_removal")
        return tracked, any_add and not pending

    def _reconcile(self, tracked: dict[int, SteamRecord]) -> None:
        for pid in tuple(self._pins):
            fd, start, holder = self._pins[pid]
            exited = bool(select.select([fd], [], [], 0)[0])
            if pid not in tracked:
                if not exited:
                    raise SteamSourceError("MALFORMED", "removal_while_alive")
                holder.close()
                del self._pins[pid]
            elif exited or _start_ticks(pid, self.uid) != start:
                raise SteamSourceError("DISCONNECTED", "tracked_identity_lost")
        for pid, record in tracked.items():
            if pid in self._pins:
                continue
            with ExitStack() as holder:
                fd, start = _pin(pid, self.uid, holder)
                try:
                    logged_at = time.mktime(time.strptime(record.timestamp, "%Y-%m-%d %H:%M:%S"))
                except (ValueError, OverflowError):
                    raise SteamSourceError("MALFORMED", "record_timestamp") from None
                # Seconds-resolution log timestamps: a later incarnation cannot
                # authenticate an old addition. Clock shifts fail closed.
                boot_age = time.clock_gettime(time.CLOCK_BOOTTIME)
                process_start = time.time() - boot_age + start / os.sysconf("SC_CLK_TCK")
                if not process_start < logged_at + 1 or logged_at > time.time() + 1:
                    raise SteamSourceError("MALFORMED", "reused_pid_or_clock_change")
                self._pins[pid] = fd, start, holder.pop_all()

    def evaluate(self, epoch: int, *, now: float | None = None) -> GamingEvidence:
        """Resnapshot on the worker; stale consumer gaps never freshen clearance."""
        # Use the worker request's start time as a conservative observation bound.
        # Never transfer a cached result to this timestamp: all source checks run.
        at = self.clock() if now is None else now
        self._sequence += 1
        self._remaining = self.max_events
        state: GamingState = "UNKNOWN"
        health: GamingHealth = self._health
        complete = False
        if health == "OK":
            try:
                if (not math.isfinite(at) or not 0 <= self.clock() - at <= self.freshness_seconds
                        or (self._last is not None
                            and not 0 <= at - self._last <= self.freshness_seconds)):
                    raise SteamSourceError("STALE", "consumer_freshness")
                if (select.select([self._client_fd], [], [], 0)[0]
                        or _start_ticks(self.client_pid, self.uid) != self._client_start):
                    raise SteamSourceError("DISCONNECTED", "client_lost")
                data = self._snapshot()
                tracked, complete = self._replay(data)
                self._reconcile(tracked)
                # Moving an ancestor does not notify the watch on the leaf.
                # Reopen the no-follow name after reconciliation before publish.
                with ExitStack() as check:
                    named = os.fstat(self._open_file(check))
                if (not os.path.samestat(os.fstat(self._fd), named)
                        or (named.st_mtime_ns, named.st_ctime_ns) != self._metadata
                        or named.st_size != self._size):
                    raise SteamSourceError("DISCONNECTED", "source_changed_before_publish")
                if (self.clock() - at > self.freshness_seconds
                        or select.select([self._client_fd], [], [], 0)[0]
                        or self._drain()):
                    raise SteamSourceError("STALE", "evaluation_expired")
                state = "ACTIVE" if tracked else "INACTIVE"
                if not complete:
                    state, health = "UNKNOWN", "STALE"
            except SteamSourceError as exc:
                health = self._health = exc.health
                # Continuity is either established for the whole retained history or
                # it is not. A truncation/rewrite/replacement, a dropped notification
                # or any other reason that could have hidden an event is terminal for
                # this reader: regrowth that happens to restore the old prefix cannot
                # prove the gap was empty.
                if exc.reason not in RECOVERABLE_REASONS:
                    self._continuity_lost = True
                complete = False
            except OSError:
                health = self._health = "DISCONNECTED"
                self._continuity_lost = True
                complete = False
            except OverflowError:
                health = self._health = "OVERFLOW"
                self._continuity_lost = True
                complete = False
        self._last = at
        return GamingEvidence(
            "steam-log", self.uid, epoch, self._sequence, at, state, health,
            frozenset({f"steam-log:{self.uid}"}) if complete else frozenset(),
            self.producer_version,
        )


class SteamLogProducer(_SteamLogReader):
    """Worker-side supervisor owning at most one replacement reader.

    STALE/transport loss is recoverable only with the same pinned client and
    source identity and retained prefix. Malformed, overflowed, replaced or
    rewritten history never gains permission by reconnecting. Recovery emits a
    new version fingerprint so GamingReducer revokes its old epoch first.
    """

    _successor: _SteamLogReader | None = None
    _closed = False

    def __enter__(self) -> SteamLogProducer:
        super().__enter__()
        return self

    def _recover(self, prior: _SteamLogReader) -> _SteamLogReader:
        candidate = _SteamLogReader(
            self.path, self.client_pid, uid=self.uid,
            max_history_bytes=self.max_history_bytes, max_line_length=self.max_line_length,
            max_tracked_processes=self.max_tracked_processes, max_events=self.max_events,
            freshness_seconds=self.freshness_seconds, clock=self.clock,
        ).__enter__()
        try:
            if (candidate._health != "OK" or prior._fd < 0
                    or select.select([prior._client_fd], [], [], 0)[0]
                    or candidate._client_start != prior._client_start
                    or not os.path.samestat(os.fstat(candidate._fd), os.fstat(prior._fd))):
                candidate.close()
                # An identity mismatch, or a source we could not re-open at all,
                # is an observed gap: the retained reader must never recover.
                prior._continuity_lost = True
                return prior
            # Carry the previous prefix and metadata into the fresh resnapshot.
            # It must prove append-only continuity before emitting healthy state.
            candidate._size, candidate._digest = prior._size, prior._digest
            candidate._metadata = prior._metadata
            # Preserve pidfd lifetimes: reconnect cannot erase an unpaired game.
            candidate._pins, prior._pins = prior._pins, {}
            _SteamLogReader.close(prior)
            self._successor = candidate
            self.producer_version = candidate.producer_version
            return candidate
        except OSError:
            candidate.close()
            prior._continuity_lost = True
            return prior

    def evaluate(self, epoch: int, *, now: float | None = None) -> GamingEvidence:
        reader = self._successor if self._successor is not None else self
        if (not self._closed and reader._entered and not reader._continuity_lost
                and reader._health in {"STALE", "DISCONNECTED"}):
            reader = self._recover(reader)
        return _SteamLogReader.evaluate(reader, epoch, now=now)

    def fileno(self) -> int:
        reader = self._successor if self._successor is not None else self
        return reader._notifications

    def close(self) -> None:
        self._closed = True
        if self._successor is not None:
            self._successor.close()
        super().close()
