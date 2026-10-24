"""Real file, inotify and pidfd tests; no workstation Steam or service access."""

from __future__ import annotations

import importlib
import os
import select
import struct
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

from sparehash.gaming_session import GamingSessionGuard
from sparehash.gaming_state import GamingReducer
from sparehash.gaming_steam import SteamLogProducer, SteamRecord
from tests.test_gaming_session import EMPTY, FRESH, SESSION, USER_EMPTY, Groups, Inventory

STAMP = b"[2026-10-23 12:00:00]"
ADD = STAMP + b" AppID 42 adding PID 123 as a tracked process"
REMOVE = STAMP + b" AppID 42 no longer tracking PID 123, exit code -9"
HISTORY = ADD + b"\n" + REMOVE + b"\n"


@pytest.fixture
def steam() -> ModuleType:
    # Import per test so the pre-implementation run records every missing behavior.
    return importlib.import_module("sparehash.gaming_steam")


@pytest.fixture
def child() -> Iterator[subprocess.Popen[bytes]]:
    with subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE,
    ) as process:
        try:
            yield process
        finally:
            if process.stdin is not None:
                process.stdin.close()
            process.wait(timeout=5)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "gameprocess_log.txt"
    path.write_bytes(HISTORY)
    return path


def live_add(pid: int) -> bytes:
    stamp = time.strftime("[%Y-%m-%d %H:%M:%S]").encode("ascii")
    return stamp + f" AppID 42 adding PID {pid} as a tracked process\n".encode()


def remove(pid: int) -> bytes:
    return STAMP + f" AppID 42 no longer tracking PID {pid}, exit code 0\n".encode()


def append(path: Path, data: bytes) -> None:
    with path.open("ab") as stream:
        stream.write(data)


@pytest.mark.parametrize("suffix", [b"", b"\r", b" private /home/secret", b" \xffsecret\r"])
def test_exact_addition_discards_private_suffix(steam: ModuleType, suffix: bytes) -> None:
    # Given / When
    record = steam.parse_record(ADD + suffix)
    # Then
    assert record is not None
    assert (record.app_id, record.pid, record.adding) == ("42", 123, True)
    assert "secret" not in repr(record)


@pytest.mark.parametrize("code", [b"0", b"-9", b"12345678901234567890"])
def test_exact_removal_grammar(steam: ModuleType, code: bytes) -> None:
    # Given / When
    record = steam.parse_record(REMOVE.rsplit(b" ", 1)[0] + b" " + code)
    # Then
    assert record is not None
    assert (record.app_id, record.pid, record.adding) == ("42", 123, False)


@pytest.mark.parametrize("line", [
    ADD + b"x", ADD + b"\tprivate", ADD + b"\r\r", ADD + b" private\rbroken",
    ADD.replace(b"42 ", b"1" * 21 + b" "), ADD.replace(b"123 ", b"1" * 11 + b" "),
    ADD.replace(b"2026-", b"202-"), ADD.replace(b"AppID", b"GameID"),
    ADD.replace(b" as a tracked", b" as tracked"), b"prefix " + ADD,
    REMOVE + b" private", REMOVE + b"\r", REMOVE.replace(b"-9", b"+9"),
    REMOVE.replace(b"123,", b"123"), b"adding PID", b"no longer tracking PID",
    b"as a tracked process", ADD.replace(b"adding PID", b"adding  PID"),
])
def test_malformed_tracking_never_disappears(steam: ModuleType, line: bytes) -> None:
    # Given / When / Then
    with pytest.raises(steam.SteamSourceError):
        steam.parse_record(line)


def test_unrelated_lines_are_not_game_identity(steam: ModuleType) -> None:
    # Given / When / Then
    assert steam.parse_record(b"Steam client ordinary diagnostic") is None
    assert steam.parse_record(ADD.replace(b"42 ", b"0" * 20 + b" ")).app_id == "0" * 20


def test_balanced_full_history_emits_complete_inactive(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given
    reducer = GamingReducer()
    key = f"steam-log:{os.getuid()}"
    reducer.set_coverage((key,))
    with steam.SteamLogProducer(source, child.pid) as producer:
        # When
        evidence = producer.evaluate(reducer.current_epoch)
        # Then
        assert evidence.state == "INACTIVE"
        assert evidence.health == "OK"
        assert evidence.provider == "steam-log"
        assert evidence.completeness == frozenset({key})
        assert evidence.producer_version
        assert reducer.reduce([evidence]) == "CLEAR"


@pytest.mark.parametrize("data,health", [
    (b"", "STALE"), (b"unrelated\n", "STALE"), (REMOVE + b"\n", "MALFORMED"),
    (HISTORY + REMOVE + b"\n", "MALFORMED"),
    (ADD + b"\n" + ADD + b"\n", "MALFORMED"),
    (ADD + b"\n" + REMOVE.replace(b"42 ", b"43 ") + b"\n", "MALFORMED"),
    (HISTORY + b"adding PID broken\n", "MALFORMED"),
    (HISTORY + b"partial", "STALE"),
    (ADD + b"\n", "DISCONNECTED"),
])
def test_incomplete_or_contradictory_history_never_clears(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes], data: bytes, health: str,
) -> None:
    # Given
    source.write_bytes(data)
    with steam.SteamLogProducer(source, child.pid) as producer:
        # When
        evidence = producer.evaluate(1)
        # Then
        assert (evidence.state, evidence.health) == ("UNKNOWN", health)
        assert not evidence.completeness


def test_accepts_admitted_idle_game_lifecycles(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given a source subscribed before a real game's creation
    with steam.SteamLogProducer(source, child.pid) as producer:
        assert producer.evaluate(1).state == "INACTIVE"
        with subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
                              stdin=subprocess.PIPE) as game:
            try:
                # When
                append(source, live_add(game.pid))
                assert select.select([producer.fileno()], [], [], 5)[0]
                active = producer.evaluate(1)
                # Then
                assert active.state == "ACTIVE"
                with os.fdopen(steam._pidfd_open(game.pid), "rb") as death:
                    assert game.stdin is not None
                    game.stdin.close()
                    assert select.select([death], [], [], 5)[0]
                    game.wait(timeout=5)
                append(source, remove(game.pid))
                assert select.select([producer.fileno()], [], [], 5)[0]
                inactive = producer.evaluate(1)
                assert inactive.state == "INACTIVE"
                assert inactive.sequence > active.sequence
            finally:
                if game.stdin is not None:
                    game.stdin.close()
                game.wait(timeout=5)


@pytest.mark.parametrize("change", ["rotation", "truncation", "rewrite", "same-bytes", "symlink"])
def test_file_discontinuity_latches_unknown(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes], change: str,
) -> None:
    # Given
    with steam.SteamLogProducer(source, child.pid) as producer:
        assert producer.evaluate(1).state == "INACTIVE"
        # When
        if change == "rotation":
            source.rename(source.with_suffix(".old"))
            source.write_bytes(HISTORY)
        elif change == "truncation":
            source.write_bytes(b"")
        elif change == "rewrite":
            source.write_bytes(HISTORY.replace(b"42", b"43") + b"ordinary\n")
        elif change == "same-bytes":
            source.write_bytes(HISTORY)
        else:
            source.rename(source.with_suffix(".old"))
            source.symlink_to(source.with_suffix(".old"))
        assert select.select([producer.fileno()], [], [], 5)[0]
        # Then
        assert producer.evaluate(1).state == "UNKNOWN"
        assert producer.evaluate(2).state == "UNKNOWN"


@pytest.mark.parametrize("kind", ["missing", "directory", "fifo", "symlink", "parent-symlink", "foreign-uid"])
def test_refuses_unadmitted_or_lost_coverage(
    steam: ModuleType, tmp_path: Path, child: subprocess.Popen[bytes], kind: str,
) -> None:
    # Given
    path = tmp_path / "source"
    uid = os.getuid()
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "symlink":
        target = tmp_path / "target"
        target.write_bytes(HISTORY)
        path.symlink_to(target)
    elif kind == "parent-symlink":
        target = tmp_path / "directory"
        target.mkdir()
        (target / "log").write_bytes(HISTORY)
        path.symlink_to(target, target_is_directory=True)
        path = path / "log"
    elif kind == "foreign-uid":
        path.write_bytes(HISTORY)
        uid += 1
    with steam.SteamLogProducer(path, child.pid, uid=uid) as producer:
        # When
        evidence = producer.evaluate(1)
        # Then
        assert (evidence.state, evidence.health) == ("UNKNOWN", "DISCONNECTED")


@pytest.mark.parametrize("limit", ["max_history_bytes", "max_line_length", "max_tracked_processes", "max_events"])
def test_all_work_bounds_inhibit(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes], limit: str,
) -> None:
    # Given
    if limit == "max_tracked_processes":
        source.write_bytes(ADD + b"\n" + ADD.replace(b"123 ", b"124 ") + b"\n")
    with steam.SteamLogProducer(source, child.pid, **{limit: 1}) as producer:
        # When
        evidence = producer.evaluate(1)
        # Then
        assert (evidence.state, evidence.health) == ("UNKNOWN", "OVERFLOW")


def test_exact_bounds_are_inclusive(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given
    with steam.SteamLogProducer(source, child.pid, max_history_bytes=len(HISTORY),
                               max_line_length=max(len(ADD), len(REMOVE)),
                               max_tracked_processes=1, max_events=2) as producer:
        # When / Then
        assert producer.evaluate(1).state == "INACTIVE"


def test_freshness_gap_cannot_be_retimestamped(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given
    now = [100.0]
    with steam.SteamLogProducer(source, child.pid, clock=lambda: now[0],
                               freshness_seconds=2.0) as producer:
        initial = producer.evaluate(1)
        assert initial.state == "INACTIVE"
        # When
        now[0] = 102.01
        stale = producer.evaluate(1)
        # Then
        assert (stale.state, stale.health) == ("UNKNOWN", "STALE")
        assert not stale.completeness


def test_partial_append_requires_completion_before_clear(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given
    with steam.SteamLogProducer(source, child.pid) as producer:
        assert producer.evaluate(1).state == "INACTIVE"
        # When
        append(source, b"ordinary")
        assert producer.evaluate(1).state == "UNKNOWN"
        append(source, b"\n")
        # Then
        assert producer.evaluate(1).state == "INACTIVE"


def test_anchor_exit_and_consumer_loss_revoke_clearance(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given
    with steam.SteamLogProducer(source, child.pid) as producer:
        assert producer.evaluate(1).state == "INACTIVE"
        with os.fdopen(steam._pidfd_open(child.pid), "rb") as death:
            # When
            assert child.stdin is not None
            child.stdin.close()
            assert select.select([death], [], [], 5)[0]
            child.wait(timeout=5)
        # Then
        assert producer.evaluate(1).health == "DISCONNECTED"
    assert producer.evaluate(2).state == "UNKNOWN"


def test_consumer_close_is_not_an_inactive_sample(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given
    with steam.SteamLogProducer(source, child.pid) as producer:
        assert producer.evaluate(1).state == "INACTIVE"
    # When / Then
    assert producer.evaluate(1).health == "DISCONNECTED"


def test_pid_reuse_cannot_adopt_an_old_addition(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given: an alive PID but an addition predating this process by decades
    source.write_bytes(ADD.replace(b"2026", b"2000").replace(b"123 ", f"{child.pid} ".encode()) + b"\n")
    with steam.SteamLogProducer(source, child.pid) as producer:
        # When / Then
        evidence = producer.evaluate(1)
        assert (evidence.state, evidence.health) == ("UNKNOWN", "MALFORMED")


@pytest.mark.parametrize("packet,health", [
    (struct.pack("iIII", -1, 0x4000, 0, 0), "OVERFLOW"),
    (struct.pack("iIII", 1, 0x8000, 0, 0), "DISCONNECTED"),
    (b"short", "MALFORMED"),
])
def test_notification_loss_maps_to_health(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes], packet: bytes, health: str,
) -> None:
    # Given: kernel overflow cannot be forced portably; inject only its wire record
    with steam.SteamLogProducer(source, child.pid) as producer:
        assert producer.evaluate(1).state == "INACTIVE"
        real_read = os.read

        def read(fd: int, size: int) -> bytes:
            return packet if fd == producer.fileno() else real_read(fd, size)

        # When
        with patch("sparehash.gaming_steam.os.read", side_effect=read):
            evidence = producer.evaluate(1)
        # Then
        assert (evidence.state, evidence.health) == ("UNKNOWN", health)


def test_restart_fingerprint_revokes_reducer_epoch(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given
    reducer = GamingReducer()
    reducer.set_coverage((f"steam-log:{os.getuid()}",))
    with steam.SteamLogProducer(source, child.pid) as first:
        prior = first.evaluate(reducer.current_epoch)
        assert reducer.reduce([prior]) == "CLEAR"
    with steam.SteamLogProducer(source, child.pid) as second:
        # When
        current = second.evaluate(reducer.current_epoch)
        # Then
        assert current.producer_version != prior.producer_version
        assert reducer.reduce([current]) == "UNKNOWN"
        assert reducer.reduce([replace(prior, sequence=prior.sequence + 1)]) == "UNKNOWN"
        assert reducer.reduce([second.evaluate(reducer.current_epoch)]) == "CLEAR"


@pytest.mark.parametrize("budget", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_freshness_budget_is_refused(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes], budget: float,
) -> None:
    # Given / When / Then
    with pytest.raises(ValueError):
        steam.SteamLogProducer(source, child.pid, freshness_seconds=budget)


def test_real_producer_can_reach_guard_clearance(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given: a populated user.slice, which is what a real logged-in desktop has.
    from sparehash.gaming_session import GamingSessionGuard

    now = time.monotonic()
    uid = os.getuid()
    inventory = replace(FRESH, observed_at=now, sessions=(replace(SESSION, uid=uid),))
    groups = (replace(EMPTY, observed_at=now, procs_empty=False),
              replace(USER_EMPTY, uid=uid, cgroup_path=f"/user.slice/user-{uid}.slice",
                      observed_at=now, procs_empty=False))
    with steam.SteamLogProducer(source, child.pid) as producer:
        guard = GamingSessionGuard(Inventory(inventory), Groups(groups), (uid,),
                                   steam_producers=(producer,))
        # When / Then
        assert guard.evaluate(now) == "CLEAR"


def test_populated_slice_still_inhibits_without_producer(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given: the same populated desktop, but no producer configured.
    from sparehash.gaming_session import GamingSessionGuard

    now = time.monotonic()
    uid = os.getuid()
    inventory = replace(FRESH, observed_at=now, sessions=(replace(SESSION, uid=uid),))
    groups = (replace(EMPTY, observed_at=now, procs_empty=False),
              replace(USER_EMPTY, uid=uid, cgroup_path=f"/user.slice/user-{uid}.slice",
                      observed_at=now, procs_empty=False))
    guard = GamingSessionGuard(Inventory(inventory), Groups(groups), (uid,))
    # When / Then: legacy conservative behaviour is untouched.
    assert guard.evaluate(now) == "BLOCKED"


def test_producer_active_still_blocks_with_populated_slice(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given: a live game recorded in the log, and a populated desktop.
    from sparehash.gaming_session import GamingSessionGuard

    source.write_bytes(HISTORY + live_add(child.pid))
    now = time.monotonic()
    uid = os.getuid()
    inventory = replace(FRESH, observed_at=now, sessions=(replace(SESSION, uid=uid),))
    groups = (replace(EMPTY, observed_at=now, procs_empty=False),
              replace(USER_EMPTY, uid=uid, cgroup_path=f"/user.slice/user-{uid}.slice",
                      observed_at=now, procs_empty=False))
    with steam.SteamLogProducer(source, child.pid) as producer:
        guard = GamingSessionGuard(Inventory(inventory), Groups(groups), (uid,),
                                   steam_producers=(producer,))
        # When / Then
        assert guard.evaluate(now) == "BLOCKED"


def test_parent_rotation_during_reconciliation_is_unknown(
    steam: ModuleType, tmp_path: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given
    directory = tmp_path / "logs"
    directory.mkdir()
    path = directory / "gameprocess_log.txt"
    path.write_bytes(HISTORY)
    with steam.SteamLogProducer(path, child.pid) as producer:
        original = producer._reconcile

        def rotate(tracked: dict[int, SteamRecord]) -> None:
            original(tracked)
            directory.rename(tmp_path / "old")
            directory.mkdir()
            path.write_bytes(live_add(child.pid))

        # When: directory replacement does not notify a watch on the old file.
        with patch.object(producer, "_reconcile", side_effect=rotate):
            evidence = producer.evaluate(1)
        # Then
        assert evidence.state == "UNKNOWN"


@pytest.mark.parametrize("fault", ["append-race", "read-loss"])
def test_supervisor_recovers_transient_loss_within_five_seconds(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes], fault: str,
) -> None:
    # Given: real source/anchor, controlled monotonic time, real guard/reducer.
    from sparehash.gaming_session import GamingSessionGuard

    now = [100.0]
    uid = os.getuid()
    inventory = replace(FRESH, sessions=(replace(SESSION, uid=uid, idle_hint=None),))
    groups = (EMPTY, replace(USER_EMPTY, uid=uid, cgroup_path=f"/user.slice/user-{uid}.slice"))
    with steam.SteamLogProducer(source, child.pid, clock=lambda: now[0]) as producer:
        guard = GamingSessionGuard(Inventory(inventory), Groups(groups), (uid,),
                                   steam_producers=(producer,))
        assert guard.evaluate(now[0]) == "CLEAR"
        old_epoch = guard._steam.current_epoch
        old_sample = producer.evaluate(old_epoch, now=now[0])
        original = os.pread
        triggered = False

        def fault_once(fd: int, size: int, offset: int) -> bytes:
            nonlocal triggered
            if not triggered:
                triggered = True
                if fault == "read-loss":
                    raise OSError("synthetic transient read loss")
                data = original(fd, size, offset)
                append(source, b"ordinary diagnostic\n")
                return data
            return original(fd, size, offset)

        # When: an exact I/O-boundary fault, without sleeps or timing luck.
        now[0] = 101.0
        with patch("sparehash.gaming_steam.os.pread", side_effect=fault_once):
            assert guard.evaluate(now[0]) == "BLOCKED"
        assert triggered
        now[0] = 102.0
        assert guard.evaluate(now[0]) == "BLOCKED"  # New incarnation revokes epoch.
        assert guard._steam.current_epoch > old_epoch
        assert guard._steam.reduce([old_sample], now=now[0]) == "UNKNOWN"
        now[0] = 103.0
        # Then: only fresh evidence in the new epoch can restore clearance.
        assert guard.evaluate(now[0]) == "CLEAR"
        current = producer.evaluate(guard._steam.current_epoch, now=now[0])
        assert current.producer_version != old_sample.producer_version
        assert now[0] - 101.0 <= 5.0


def _guard(steam: ModuleType, source: Path, child: subprocess.Popen[bytes], now: list[float],
           producer: SteamLogProducer) -> GamingSessionGuard:
    uid = os.getuid()
    inventory = replace(FRESH, observed_at=now[0], sessions=(replace(SESSION, uid=uid),))
    groups = (replace(EMPTY, observed_at=now[0]),
              replace(USER_EMPTY, uid=uid, cgroup_path=f"/user.slice/user-{uid}.slice",
                      observed_at=now[0]))
    return GamingSessionGuard(Inventory(inventory), Groups(groups), (uid,),
                              steam_producers=(producer,))


@pytest.mark.parametrize("damage", ["truncate", "replace"])
def test_continuity_loss_is_terminal_when_history_regrows(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes], damage: str,
) -> None:
    # Given: a proven idle desktop with complete paired history.
    now = [200.0]
    with steam.SteamLogProducer(source, child.pid, clock=lambda: now[0]) as producer:
        guard = _guard(steam, source, child, now, producer)
        assert guard.evaluate(now[0]) == "CLEAR"
        # When: continuity is destroyed, then a longer file restores the old prefix.
        if damage == "truncate":
            source.write_bytes(b"")
        else:
            replacement = source.parent / "replacement"
            replacement.write_bytes(b"")
            replacement.replace(source)
        now[0] = 201.0
        assert guard.evaluate(now[0]) != "CLEAR"
        source.write_bytes(HISTORY + live_add(child.pid) + remove(child.pid))
        # Then: an observed gap can never be repaired by later bytes or a returning inode.
        for step in (201.5, 202.0, 202.5, 203.0):
            now[0] = step
            assert guard.evaluate(now[0]) != "CLEAR"
        evidence = producer.evaluate(guard._steam.current_epoch, now=now[0])
        assert evidence.state == "UNKNOWN"
        assert evidence.health != "OK"
        assert evidence.completeness == frozenset()


def test_transient_read_loss_still_recovers_without_hiding_a_gap(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given: the same reader that must refuse regrowth also reports read races.
    now = [300.0]
    with steam.SteamLogProducer(source, child.pid, clock=lambda: now[0]) as producer:
        guard = _guard(steam, source, child, now, producer)
        assert guard.evaluate(now[0]) == "CLEAR"
        original = os.pread
        triggered = False

        def race_once(fd: int, size: int, offset: int) -> bytes:
            nonlocal triggered
            if triggered:
                return original(fd, size, offset)
            triggered = True
            data = original(fd, size, offset)
            append(source, b"ordinary diagnostic\n")
            return data

        # When: a read races an append, which loses no record and keeps continuity.
        now[0] = 301.0
        with patch("sparehash.gaming_steam.os.pread", side_effect=race_once):
            assert guard.evaluate(now[0]) == "BLOCKED"
        assert triggered
        now[0] = 302.0
        # Then: recovery revokes the epoch, then a fresh sample clears again.
        assert guard.evaluate(now[0]) == "BLOCKED"
        now[0] = 303.0
        assert guard.evaluate(now[0]) == "CLEAR"


def test_truncation_during_read_is_terminal(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given: a proven idle desktop with complete paired history.
    now = [400.0]
    with steam.SteamLogProducer(source, child.pid, clock=lambda: now[0]) as producer:
        guard = _guard(steam, source, child, now, producer)
        assert guard.evaluate(now[0]) == "CLEAR"
        original = os.pread

        def truncating_read(fd: int, size: int, offset: int) -> bytes:
            data = original(fd, size, offset)
            source.write_bytes(b"")
            return data

        # When: the file shrinks between the pre-read size check and the read.
        now[0] = 401.0
        with patch("sparehash.gaming_steam.os.pread", side_effect=truncating_read):
            assert guard.evaluate(now[0]) != "CLEAR"
        # Then: observed loss of bytes is terminal even if the prefix returns.
        source.write_bytes(HISTORY + live_add(child.pid) + remove(child.pid))
        for step in (401.5, 402.0, 402.5, 403.0):
            now[0] = step
            assert guard.evaluate(now[0]) != "CLEAR"


def test_replacement_observed_during_recovery_is_terminal(
    steam: ModuleType, source: Path, tmp_path: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given: a transient read failure that is itself recoverable.
    now = [500.0]
    with steam.SteamLogProducer(source, child.pid, clock=lambda: now[0]) as producer:
        guard = _guard(steam, source, child, now, producer)
        assert guard.evaluate(now[0]) == "CLEAR"
        original = os.pread
        triggered = False

        def failing_read(fd: int, size: int, offset: int) -> bytes:
            nonlocal triggered
            if triggered:
                return original(fd, size, offset)
            triggered = True
            raise OSError("synthetic transient read loss")

        now[0] = 501.0
        with patch("sparehash.gaming_steam.os.pread", side_effect=failing_read):
            assert guard.evaluate(now[0]) != "CLEAR"
        assert triggered
        # When: recovery observes a different inode at the path.
        parked = tmp_path / "parked"
        source.replace(parked)
        source.write_bytes(HISTORY)
        now[0] = 502.0
        assert guard.evaluate(now[0]) != "CLEAR"
        # Then: returning the original inode with a longer history cannot repair it.
        source.unlink()
        parked.replace(source)
        append(source, live_add(child.pid) + remove(child.pid))
        for step in (502.5, 503.0, 503.5, 504.0):
            now[0] = step
            assert guard.evaluate(now[0]) != "CLEAR"


@pytest.mark.parametrize("replacement", ["identical", "different"])
def test_same_size_rewrite_during_read_is_terminal(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes], replacement: str,
) -> None:
    # Given: a proven idle desktop with complete paired history.
    now = [600.0]
    with steam.SteamLogProducer(source, child.pid, clock=lambda: now[0]) as producer:
        guard = _guard(steam, source, child, now, producer)
        assert guard.evaluate(now[0]) == "CLEAR"
        original = os.pread
        history = source.read_bytes()
        altered = history if replacement == "identical" else history.replace(b"AppID 42", b"AppID 43")

        def rewriting_read(fd: int, size: int, offset: int) -> bytes:
            data = original(fd, size, offset)
            source.write_bytes(altered)
            return data

        # When: the file is rewritten at the same length while copying the bytes.
        now[0] = 601.0
        with patch("sparehash.gaming_steam.os.pread", side_effect=rewriting_read):
            assert guard.evaluate(now[0]) != "CLEAR"
        # Then: restoring the original prefix in a longer file cannot repair it.
        source.write_bytes(history + live_add(child.pid) + remove(child.pid))
        for step in (601.5, 602.0, 602.5, 603.0):
            now[0] = step
            assert guard.evaluate(now[0]) != "CLEAR"


def test_pid_reuse_after_removal_is_ordinary_history(
    steam: ModuleType, tmp_path: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given: the storefront reuses a PID in a later session (add, remove, add, remove).
    path = tmp_path / "gameprocess_log.txt"
    path.write_bytes(HISTORY + HISTORY)
    # When
    with steam.SteamLogProducer(path, child.pid) as producer:
        evidence = producer.evaluate(1)
    # Then
    assert (evidence.state, evidence.health) == ("INACTIVE", "OK")
    assert evidence.completeness


def test_second_addition_while_still_tracked_is_malformed(
    steam: ModuleType, tmp_path: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given
    path = tmp_path / "gameprocess_log.txt"
    path.write_bytes(ADD + b"\n" + ADD + b"\n")
    # When
    with steam.SteamLogProducer(path, child.pid) as producer:
        evidence = producer.evaluate(1)
    # Then
    assert (evidence.state, evidence.health) == ("UNKNOWN", "MALFORMED")
    assert not evidence.completeness


def test_crlf_history_is_accepted_like_lf(
    steam: ModuleType, tmp_path: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given: the real Linux storefront writes CRLF records.
    path = tmp_path / "gameprocess_log.txt"
    path.write_bytes(HISTORY.replace(b"\n", b"\r\n"))
    # When
    with steam.SteamLogProducer(path, child.pid) as producer:
        evidence = producer.evaluate(1)
    # Then
    assert (evidence.state, evidence.health) == ("INACTIVE", "OK")
    assert evidence.completeness


def test_crlf_tracking_removal_is_still_exact(
    steam: ModuleType, tmp_path: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given: arbitrary text after a CR is still a malformed record.
    path = tmp_path / "gameprocess_log.txt"
    path.write_bytes(HISTORY.replace(b"\n", b"\r\n") + REMOVE.replace(b"\n", b"") + b"\rPRIVATE\r\n")
    # When
    with steam.SteamLogProducer(path, child.pid) as producer:
        evidence = producer.evaluate(1)
    # Then
    assert evidence.state == "UNKNOWN"
    assert evidence.health == "MALFORMED"
    assert not evidence.completeness


def test_admits_canonical_path_when_an_ancestor_is_a_symlink(
    steam: ModuleType, tmp_path: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given: Atomic distributions expose /home as a symlink to /var/home.
    real = tmp_path / "real"
    (real / "logs").mkdir(parents=True)
    (real / "logs" / "gameprocess_log.txt").write_bytes(HISTORY)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    aliased = alias / "logs" / "gameprocess_log.txt"
    # When / Then: the raw aliased path is refused by design, and the canonical
    # location the caller resolves it to is admitted.
    with steam.SteamLogProducer(aliased, child.pid) as refused:
        unknown = refused.evaluate(1)
    assert (unknown.state, unknown.health) == ("UNKNOWN", "DISCONNECTED")
    canonical = steam.canonical_source(aliased)
    assert canonical == real / "logs" / "gameprocess_log.txt"
    with steam.SteamLogProducer(canonical, child.pid) as producer:
        evidence = producer.evaluate(1)
    assert (evidence.state, evidence.health) == ("INACTIVE", "OK")
    assert evidence.completeness


def test_refuses_log_that_is_itself_a_symlink(
    steam: ModuleType, tmp_path: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given
    real = tmp_path / "real.log"
    real.write_bytes(HISTORY)
    link = tmp_path / "gameprocess_log.txt"
    link.symlink_to(real)
    # When
    with steam.SteamLogProducer(link, child.pid) as producer:
        evidence = producer.evaluate(1)
    # Then
    assert (evidence.state, evidence.health) == ("UNKNOWN", "DISCONNECTED")
    assert not evidence.completeness


def test_corrupted_prefix_during_append_race_is_terminal(
    steam: ModuleType, source: Path, child: subprocess.Popen[bytes],
) -> None:
    # Given
    now = [700.0]
    with steam.SteamLogProducer(source, child.pid, clock=lambda: now[0]) as producer:
        guard = _guard(steam, source, child, now, producer)
        assert guard.evaluate(now[0]) == "CLEAR"
        original = os.pread
        history = source.read_bytes()
        triggered = False

        def racing_read(fd: int, size: int, offset: int) -> bytes:
            nonlocal triggered
            if triggered:
                return original(fd, size, offset)
            triggered = True
            data = original(fd, size, offset)
            # Append, but also corrupt the retained prefix in the same window.
            source.write_bytes(history.replace(b"AppID 42", b"AppID 43") + b"ordinary\n")
            return data

        # When
        now[0] = 701.0
        with patch("sparehash.gaming_steam.os.pread", side_effect=racing_read):
            assert guard.evaluate(now[0]) != "CLEAR"
        assert triggered
        # Then: the race does not excuse the corrupted prefix.
        source.write_bytes(history + live_add(child.pid) + remove(child.pid))
        for step in (701.5, 702.0, 702.5, 703.0):
            now[0] = step
            assert guard.evaluate(now[0]) != "CLEAR"

