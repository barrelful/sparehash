"""Owner-loop gaming inhibition and stop-proof timing; no live host mutation."""

from __future__ import annotations

from pathlib import Path
from threading import Event, get_ident
from unittest.mock import patch

import pytest

from sparehash import config
from sparehash.controller import Controller
from sparehash.controller_io import (
    Action,
    Completion,
    Generation,
    Job,
    Mode,
    Observation,
    Operation,
    Ports,
    SampleId,
)
from sparehash.gaming_scan import ScanResult
from sparehash.hugepages import LeaseManager
from sparehash.runtime import Providers
from sparehash.runtime_jobs import Jobs
from sparehash.state import BlockerState
from tests.controller_fixtures import FakePorts


def _build(tmp_path: Path, dwell: float = 0.0) -> tuple[FakePorts, BlockerState, Controller]:
    io = FakePorts()
    state = BlockerState(tmp_path / "state.json", io.clock, dwell)
    controller = Controller(state, Ports(io, io, io, io.clock))
    return io, state, controller


def _safe(controller: Controller, io: FakePorts) -> Observation:
    return Observation(controller.sample, io.now, True, False, False, (), False)


def _observe(controller: Controller, io: FakePorts) -> None:
    controller.refresh()
    controller.observe(_safe(controller, io))


def _finish(controller: Controller, *, success: bool = True, empty: bool = True) -> None:
    job = controller.pending
    assert job is not None
    controller.complete(Completion(job, success, empty))


def _running(controller: Controller, io: FakePorts) -> None:
    controller.start()
    _observe(controller, io)
    _finish(controller)
    _finish(controller)


def test_accepts_prioritized_stop_and_recovery(tmp_path: Path) -> None:
    # Given a running miner with an empty-cgroup STOP proof still pending cleanup
    io, state, controller = _build(tmp_path)
    _running(controller, io)
    revoked = controller.generation

    # When the single gaming reducer reports an ACTIVE session
    io.now = 2.0
    controller.safety("gaming", True, source="gaming", epoch=revoked)

    # Then a fresh STOP is queued in the same owner-loop turn, before any START
    assert io.jobs[-1].action is Action.STOP
    assert controller.generation != revoked
    assert controller.launch_permitted is False
    assert controller.trigger_received_at == 2.0
    assert controller.generation_revoked_at == 2.0
    assert controller.safety_callback_at is not None
    assert controller.stop_submitted_at == 2.0
    assert not controller.authorize()
    controller.tick()
    assert io.jobs[-1].action is Action.STOP
    assert "gaming" in state.active()

    # When the reducer loses its monitor and reports UNKNOWN
    io.now = 3.0
    controller.safety("gaming", True, source="gaming", epoch=controller.generation)

    # Then inhibition holds; a fast watcher cannot hide a late START authorization
    assert "gaming" in state.active()
    assert io.jobs[-1].action is Action.STOP
    assert not controller.authorize()

    # When the recursive cgroup STOP proof lands
    _finish(controller, success=True, empty=True)

    # Then proof and cleanup are dated and precede any recovery
    proof, cleanup = controller.empty_proof_received_at, controller.cleanup_finished_at
    assert proof is not None and cleanup is not None
    assert proof == 3.0
    assert cleanup >= proof
    assert not controller.authorize()

    # When a current-epoch CLEAR arrives, a fresh STOP proof is still required
    io.now = 4.0
    controller.safety("gaming", False, source="gaming", epoch=controller.generation)
    _finish(controller)
    _observe(controller, io)

    # Then mining is re-authorized only through the normal dwell/observation path
    assert io.jobs[-1].action is Action.START


@pytest.mark.parametrize("kind", [
    "old-inactive",
    "delayed-start",
    "stuck-helper",
    "cpu-ipc-flood",
    "stop-timeout",
    "slow-cleanup",
    "stale-epoch-clear",
])
def test_rejects_delayed_clear_and_shutdown_overrun(tmp_path: Path, kind: str) -> None:
    # Given a running miner that just received an authoritative ACTIVE event
    io, state, controller = _build(tmp_path)
    _running(controller, io)
    controller.safety("gaming", True, source="gaming", epoch=controller.generation)
    assert io.jobs[-1].action is Action.STOP

    # When an adversarial schedule tries to relax or overrun the inhibition
    if kind == "old-inactive":
        stale = Observation(SampleId(int(controller.sample) - 1), io.now, True, False, False, (), False)
        controller.observe(stale)
    elif kind == "delayed-start":
        superseded = Job(Action.START, controller.operation, io.now + 5, io.now - 1)
        controller.complete(Completion(superseded, True, False))
    elif kind == "stuck-helper":
        controller.safety("gaming", True, source="gaming", epoch=controller.generation)
    elif kind == "cpu-ipc-flood":
        for _ in range(200):
            controller.safety("gaming", True, source="gaming", epoch=controller.generation)
            controller.tick()
    elif kind == "stop-timeout":
        job = controller.pending
        assert job is not None
        io.now = job.deadline
        controller.tick()
    elif kind == "slow-cleanup":
        _finish(controller, success=True, empty=False)
    else:  # stale-epoch-clear
        controller.safety("gaming", False, source="gaming", epoch=Generation(int(controller.generation) - 1))

    # Then no launch can happen and the unsafe state stays explicit
    assert controller.running is False
    assert controller.empty is False
    assert controller.launch_permitted is False
    assert "gaming" in state.active()
    assert controller.generation_revoked_at is not None
    assert not controller.authorize()


def test_jobs_rejects_start_after_stop_requested() -> None:
    # Given a START submitted before a STOP was requested
    enqueued: list[Completion] = []
    jobs = Jobs(config.Config(), lambda thunk: thunk(), clock=lambda: 0.0)
    jobs.finish = enqueued.append
    try:
        start = Job(Action.START, Operation(Generation(1), Mode.MINE), 5.0, 0.0)
        jobs.stop_requested_at = 1.0
        # When the queued START is dispatched
        with patch("sparehash.runtime_jobs.publish") as publish_mock, patch("sparehash.runtime_jobs.checked") as checked_mock:
            jobs.perform(start, jobs.epoch)
        # Then it is refused before publication, reservation or systemctl start
        publish_mock.assert_not_called()
        checked_mock.assert_not_called()
        assert enqueued[-1].success is False
    finally:
        jobs.close()


def test_jobs_dates_proof_separately_from_cleanup() -> None:
    # Given three distinct clock readings for request, proof and cleanup
    moments = iter([10.0, 11.0, 12.0])
    enqueued: list[Completion] = []
    jobs = Jobs(config.Config(), lambda thunk: thunk(), clock=lambda: next(moments))
    jobs.finish = enqueued.append
    try:
        with patch("sparehash.runtime_jobs.checked", return_value=""), patch(
            "sparehash.runtime_jobs.empty", return_value=True
        ), patch.object(LeaseManager, "cleanup") as cleanup_mock:
            # When a STOP proves the recursive cgroup empty
            jobs.perform(Job(Action.STOP, Operation(Generation(1), Mode.MINE), 5.0, 0.0), jobs.epoch)
        # Then cleanup runs strictly after the empty proof and both are dated
        assert cleanup_mock.called
        assert jobs.stop_requested_at == 10.0
        assert jobs.empty_proof_received_at == 11.0
        assert jobs.cleanup_finished_at == 12.0
        completion = enqueued[-1]
        assert completion.success and completion.cgroup_empty
        assert completion.empty_proof_at == 11.0
        assert completion.cleanup_finished_at == 12.0
    finally:
        jobs.close()


def test_providers_gaming_path_fails_closed_on_scan_failure() -> None:
    # Given a scanner that fails, not a successful scan with no matches.
    events: list[tuple[str, int]] = []
    with patch("sparehash.runtime.power.PowerMonitor"), patch(
        "sparehash.runtime.thermal.find_sensor", return_value=None
    ):
        def failed_scan() -> ScanResult:
            raise OSError("synthetic fault")

        provider = Providers(config.Config(gaming=config.Gaming(enabled=True, users=())),
                             lambda action: action(), gaming_scan=failed_scan,
                             gaming_sink=lambda result, epoch: events.append((result.decision, epoch)))
        try:
            # When the off-loop priority query runs
            provider.collect_gaming(7)
        finally:
            provider.close()
    # Then the controller is told UNKNOWN and the cached blocker fails closed
    assert events == [("UNKNOWN", 7)]
    assert provider.gaming_blocked is True


def test_providers_disabled_gaming_is_inert() -> None:
    # Given gaming protection is disabled
    events: list[tuple[str, int]] = []
    with patch("sparehash.runtime.power.PowerMonitor"), patch(
        "sparehash.runtime.thermal.find_sensor", return_value=None
    ):
        provider = Providers(config.Config(gaming=config.Gaming(enabled=False)), lambda action: action(),
                             gaming_sink=lambda result, epoch: events.append((result.decision, epoch)))
        try:
            # When the priority query runs
            provider.collect_gaming(0)
        finally:
            provider.close()
    # Then no authoritative blocker is synthesized
    assert events == [("CLEAR", 0)]
    assert provider.gaming_blocked is False


def test_optional_steam_is_evaluated_on_gaming_worker() -> None:
    # Given
    called = Event()
    threads: list[int] = []
    events: list[str] = []

    def scan() -> ScanResult:
        threads.append(get_ident())
        return ScanResult("CLEAR", 100.0)

    def deliver(result: ScanResult, epoch: int) -> None:
        events.append(result.decision)
        called.set()

    with patch("sparehash.runtime.power.PowerMonitor"), patch(
        "sparehash.runtime.thermal.find_sensor", return_value=None,
    ), patch("sparehash.runtime.time.monotonic", return_value=100.0):
        provider = Providers(config.Config(), lambda action: action(),
                             gaming_scan=scan, gaming_sink=deliver)
        try:
            # When: subscribe (Event) before dispatch, then wait on that exact callback.
            provider.request_gaming(7)
            assert called.wait(5)
            # Then
            assert events == ["CLEAR"]
            assert threads and threads[0] != get_ident()
            assert provider.gaming_blocked is False
        finally:
            provider.close()
