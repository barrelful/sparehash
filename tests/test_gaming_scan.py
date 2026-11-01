"""Lutris-derived matching and real Providers/controller path with proc fixtures."""

from __future__ import annotations

from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest

from sparehash import config, gaming_scan
from sparehash.controller import Controller
from sparehash.controller_io import Action, Completion, Generation, Observation, Ports
from sparehash.gaming_scan import ScanResult, scan_steam_games
from sparehash.ipc import Command, Verb
from sparehash.runtime import Providers
from sparehash.state import BlockerState
from tests.controller_fixtures import ROOT, FakePorts


def proc(root: Path, pid: int, cmdline: bytes) -> Path:
    directory = root / str(pid)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "cmdline").write_bytes(cmdline)
    fields = ["S", "1", *(["0"] * 17), "123"]
    (directory / "stat").write_text(f"{pid} (fixture) " + " ".join(fields))
    return directory


@pytest.fixture
def procfs(tmp_path: Path) -> Path:
    root = tmp_path / "proc"
    (root / "self").mkdir(parents=True)
    (root / "self/stat").write_bytes(b"procfs fixture")
    return root


@pytest.mark.parametrize("app", [b"0", b"1", b"440", b"4400", b"9876543210123456789"])
def test_any_appid_without_steam_client(procfs: Path, app: bytes) -> None:
    proc(procfs, 10, b"/steam/ubuntu12_32/reaper\0SteamLaunch\0AppId=" + app + b"\0--\0game\0")
    result = scan_steam_games(procfs)
    assert (result.decision, result.matches, result.reason) == ("BLOCKED", 1, "ok")


@pytest.mark.parametrize("cmdline", [
    b"", b"steam\0", b"steamwebhelper\0--type=renderer\0",
    b"editor\0reaper SteamLaunch AppId=440\0",
    b"reaper-not-a-game\0SteamLaunch\0AppId=440\0",
    b"reaper\0--\0editor\0SteamLaunch\0AppId=440\0",
    b"reaper\0notSteamLaunch\0AppId=440\0",
])
def test_desktop_and_incidental_arguments_do_not_match(procfs: Path, cmdline: bytes) -> None:
    proc(procfs, 10, cmdline)
    assert scan_steam_games(procfs).decision == "CLEAR"


@pytest.mark.parametrize("cmdline", [
    b"reaper\0SteamLaunch\0AppId=440x\0", b"reaper\0SteamLaunch\0",
    b"reaper\0SteamLaunch\0AppId=\0", b"reaper\0SteamLaunch\0AppId=42",
])
def test_relevant_malformed_input_inhibits(procfs: Path, cmdline: bytes) -> None:
    proc(procfs, 10, cmdline)
    assert scan_steam_games(procfs).decision == "UNKNOWN"


def test_multiple_matches_and_ordinary_churn(procfs: Path) -> None:
    for pid in (10, 11):
        proc(procfs, pid, b"reaper\0SteamLaunch\0AppId=42\0--\0fixture\0")
    (procfs / "12").mkdir()  # Exited between enumeration and cmdline open.
    result = scan_steam_games(procfs)
    assert (result.decision, result.matches) == ("BLOCKED", 2)


@pytest.mark.parametrize("error", [PermissionError, OSError])
def test_genuine_read_failure_is_not_empty_success(procfs: Path, error: type[OSError]) -> None:
    directory = proc(procfs, 10, b"desktop\0")
    original = gaming_scan._read

    def failed(path: Path, limit: int) -> bytes:
        if path == directory / "cmdline":
            raise error("private bytes must not appear in result")
        return original(path, limit)

    with patch.object(gaming_scan, "_read", side_effect=failed):
        result = scan_steam_games(procfs)
    assert result.decision == "UNKNOWN"
    assert result.reason == "process_unreadable"
    assert "private" not in repr(result)


def test_unavailable_procfs(procfs: Path, tmp_path: Path) -> None:
    assert scan_steam_games(tmp_path / "missing").decision == "UNKNOWN"
    (procfs / "self/stat").unlink()
    assert scan_steam_games(procfs).decision == "UNKNOWN"


@pytest.mark.parametrize("bound", ["bytes", "processes", "time", "identity"])
def test_work_and_identity_bounds_inhibit(procfs: Path, bound: str) -> None:
    directory = proc(procfs, 10, b"reaper\0SteamLaunch\0AppId=42\0--\0fixture\0")
    if bound == "bytes":
        (directory / "cmdline").write_bytes(b"x" * (gaming_scan.MAX_CMDLINE + 1))
        assert scan_steam_games(procfs).reason == "cmdline_limit"
    elif bound == "processes":
        with patch.object(gaming_scan, "MAX_PROCESSES", 0):
            assert scan_steam_games(procfs).reason == "process_limit"
    elif bound == "time":
        times = iter((0.0, 0.6, 0.6))
        assert scan_steam_games(procfs, clock=lambda: next(times)).decision == "UNKNOWN"
    else:
        with patch.object(gaming_scan, "_identity", side_effect=[(1000, 1), (1000, 2)]):
            assert scan_steam_games(procfs).reason == "identity_changed"


def safe(owner: Controller, io: FakePorts) -> None:
    owner.refresh()
    owner.observe(Observation(owner.sample, io.now, False, False, False, (), False))


def finish(owner: Controller) -> None:
    assert owner.pending is not None
    owner.complete(Completion(owner.pending, True, True))


def test_runtime_desktop_game_exit_and_restart_dwell(procfs: Path, tmp_path: Path) -> None:
    io = FakePorts()
    owner = Controller(BlockerState(tmp_path / "state", io.clock, 5),
                       Ports(io, io, io, io.clock), gaming_enabled=True)
    proc(procfs, 11, b"ordinary-desktop\0")  # No session emptiness prerequisite.
    with patch("sparehash.runtime.power.PowerMonitor"), patch("sparehash.runtime.thermal.find_sensor", return_value=None):
        provider = Providers(config.Config(), lambda action: action(),
                             gaming_scan=lambda: scan_steam_games(procfs, clock=io.clock),
                             gaming_sink=lambda result, epoch: owner.gaming_sample(result, Generation(epoch)))
        try:
            owner.start()
            assert not owner.authorize()
            provider.collect_gaming(int(owner.generation))
            safe(owner, io)
            finish(owner)
            assert owner.pending is not None and owner.pending.action is Action.START
            finish(owner)
            assert owner.running
            assert owner.gaming_status()["state"] == "not_detected"

            io.now = 1.0
            game = proc(procfs, 12, b"reaper\0SteamLaunch\0AppId=789\0--\0synthetic-proton\0")
            provider.collect_gaming(int(owner.generation))
            assert owner.pending is not None and owner.pending.action is Action.STOP
            assert not owner.authorize()
            finish(owner)

            (game / "cmdline").unlink()  # A normal exit during the next scan.
            provider.collect_gaming(int(owner.generation))
            finish(owner)
            safe(owner, io)
            assert owner.pending is None  # Dwell still applies despite CLEAR.
            io.now = 6.1
            provider.collect_gaming(int(owner.generation))
            safe(owner, io)
            assert owner.pending is not None and owner.pending.action is Action.START
        finally:
            provider.close()


@pytest.mark.parametrize("blocker", ["battery", "thermal", "manual", "lifecycle"])
def test_clear_scan_never_releases_other_protections(tmp_path: Path, blocker: str) -> None:
    io = FakePorts()
    owner = Controller(BlockerState(tmp_path / "state", io.clock, 0),
                       Ports(io, io, io, io.clock), gaming_enabled=True)
    owner.start()
    owner.gaming_sample(ScanResult("CLEAR", io.now), owner.generation)
    safe(owner, io)
    finish(owner)
    finish(owner)
    assert owner.running
    if blocker == "manual":
        owner.handle(Command(Verb.PAUSE), ROOT)
    elif blocker == "lifecycle":
        owner.acquire_update()
    else:
        owner.safety(blocker, True)  # type: ignore[arg-type]
    owner.gaming_sample(ScanResult("CLEAR", io.now), owner.generation)
    assert owner.excluded if blocker == "lifecycle" else blocker in owner.state.active()
    assert not owner.authorize()


def test_owner_tick_stops_on_expiry_without_worker_or_aggregate(tmp_path: Path) -> None:
    io = FakePorts()
    owner = Controller(BlockerState(tmp_path / "state", io.clock, 0),
                       Ports(io, io, io, io.clock), gaming_enabled=True)
    owner.start()
    owner.gaming_sample(ScanResult("CLEAR", io.now), owner.generation)
    safe(owner, io)
    finish(owner)
    finish(owner)
    assert owner.running
    io.now = 2.01
    owner.tick()
    assert owner.pending is not None and owner.pending.action is Action.STOP
    assert "gaming" in owner.state.active()
    assert not owner.authorize()


def test_scan_and_authenticated_hook_holds_remain_independent(tmp_path: Path) -> None:
    io = FakePorts()
    owner = Controller(BlockerState(tmp_path / "state", io.clock, 0),
                       Ports(io, io, io, io.clock), gaming_enabled=True)
    owner.start()
    owner.gaming_sample(ScanResult("CLEAR", io.now), owner.generation)
    safe(owner, io)
    finish(owner)
    finish(owner)
    assert owner.running
    owner.handle(Command(Verb.GAMING_START), ROOT)
    owner.gaming_sample(ScanResult("CLEAR", io.now), owner.generation)
    assert "gaming" in owner.state.active() and not owner.authorize()
    owner.gaming_sample(ScanResult("BLOCKED", io.now, matches=1), owner.generation)
    owner.handle(Command(Verb.GAMING_END), ROOT)
    assert not owner.hooks
    assert "gaming" in owner.state.active() and not owner.authorize()


def test_stale_and_out_of_generation_results_cannot_authorize(tmp_path: Path) -> None:
    io = FakePorts()
    owner = Controller(BlockerState(tmp_path / "state", io.clock, 0),
                       Ports(io, io, io, io.clock), gaming_enabled=True)
    owner.start()
    owner.gaming_sample(ScanResult("CLEAR", io.now), owner.generation)
    safe(owner, io)
    finish(owner)
    finish(owner)
    old_generation = owner.generation
    io.now = 2.01
    # Expiry runs at authorization even if the worker never responds and tick
    # hasn't run; a slow aggregate cannot freshen the previous scan.
    safe(owner, io)
    assert not owner.authorize()
    assert "gaming" in owner.state.active()
    assert owner.gaming_status()["state"] == "unknown"
    owner.gaming_sample(ScanResult("CLEAR", io.now), old_generation)
    assert "gaming" in owner.state.active()
    owner.gaming_sample(ScanResult("UNKNOWN", io.now, reason="proc_unavailable"), owner.generation)
    assert not owner.authorize()


def test_hung_worker_does_not_queue_scans_or_freshen_clearance(tmp_path: Path) -> None:
    io = FakePorts()
    owner = Controller(BlockerState(tmp_path / "state", io.clock, 0),
                       Ports(io, io, io, io.clock), gaming_enabled=True)
    owner.start()
    owner.gaming_sample(ScanResult("CLEAR", io.now), owner.generation)
    safe(owner, io)
    finish(owner)
    finish(owner)
    entered, release, delivered = Event(), Event(), Event()
    results: list[tuple[ScanResult, int]] = []
    calls: list[int] = []

    def stuck() -> ScanResult:
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return ScanResult("CLEAR", 0.0)

    def sink(result: ScanResult, epoch: int) -> None:
        results.append((result, epoch))
        delivered.set()

    with patch("sparehash.runtime.power.PowerMonitor"), patch("sparehash.runtime.thermal.find_sensor", return_value=None):
        provider = Providers(config.Config(), lambda action: action(), gaming_scan=stuck, gaming_sink=sink)
        try:
            provider.request_gaming(int(owner.generation))
            assert entered.wait(5)
            for _ in range(20):
                provider.request_gaming(int(owner.generation))
            assert len(calls) == 1
            io.now = 2.01
            owner.tick()
            assert owner.pending is not None and owner.pending.action is Action.STOP
            release.set()
            assert delivered.wait(5)
            result, epoch = results[0]
            owner.gaming_sample(result, Generation(epoch))
            assert "gaming" in owner.state.active()
            assert not owner.authorize()
        finally:
            release.set()
            provider.close()


def test_public_status_reports_best_effort_not_mining_permission() -> None:
    from sparehash import status
    from sparehash.power import Evidence
    from tests.test_status import PowerHelper

    detection = {"detector": "steam-reaper", "best_effort": True, "enabled": True,
                 "state": "not_detected", "reason": "ok"}
    raw = {"ok": True, "ready": True, "running": False,
           "reasons": {"manual": "manual pause"}, "gaming_detection": detection}
    with patch("sparehash.status.read_sysfs", return_value=(Evidence(False, 0), Evidence(False, 0), None, None)), patch(
        "sparehash.status.Helpers", PowerHelper,
    ), patch("sparehash.status.find_sensor", return_value=None), patch("sparehash.status.load", return_value=config.Config()):
        snapshot = status.LiveProvider(controller_reader=lambda: raw, resolver=lambda _: True).snapshot()
    assert snapshot.gaming_detection == detection
    assert snapshot.coverage_tier == "BEST_EFFORT"
    assert snapshot.blockers == (("manual", "manual pause"),)
    assert '"best_effort": true' in status.format_status(snapshot, True)
