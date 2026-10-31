"""Hermetic controller termination: real scanner threads, no host services/IPC."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import Future
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

import pytest

from sparehash import config, runtime
from sparehash.controller import Controller
from sparehash.gaming_scan import ScanResult
from sparehash.lifecycle_lease import LeaseStore
from sparehash.power import Evidence


def _child(directory: Path, mode: str) -> None:
    """Run actual Providers/Controller in a child with every host-I/O port replaced."""
    trace = directory / "trace.jsonl"

    def record(event: str) -> None:
        with trace.open("a") as stream:
            stream.write(json.dumps(event) + "\n")

    gate = Event()  # Never released: a cooperative deadline cannot interrupt it.

    def scan() -> ScanResult:
        record("scanner-entered")
        if mode == "scanner":
            gate.wait()
        return ScanResult("CLEAR", time.monotonic())

    monitor = Mock()
    answer: Future[bool] = Future()
    answer.set_result(False)
    monitor.backend.query.return_value = answer
    monitor.run.side_effect = lambda stop, emit: stop.wait()

    def sysfs(root, now):
        record("observation-entered")
        if mode == "observation":
            gate.wait()
        return Evidence(False, now), Evidence(False, now), True, 0.0

    jobs = Mock()
    jobs.published.return_value = None
    def close_jobs() -> None:
        record("jobs-close")
        if mode == "close-error":
            raise RuntimeError("synthetic teardown failure")

    jobs.close.side_effect = close_jobs
    # Deliberately do not complete queued Jobs: the final stop must not rely on
    # the owner loop processing a completion or a queued STOP succeeding.
    jobs.submit.side_effect = lambda job: record("queued-" + job.action.value)
    store = Mock(spec=LeaseStore)
    store.load.return_value = None

    def controller(state, ports, **kwargs):
        owner = Controller(state, ports, lease_store=store, **kwargs)
        shutdown = owner.shutdown

        def revoke() -> None:
            shutdown()
            assert owner.stopping and not owner.launch_permitted
            assert not owner.authorize()
            record("authority-revoked")

        owner.shutdown = revoke
        return owner

    providers_class = runtime.Providers

    def providers(cfg, enqueue):
        provider = providers_class(cfg, enqueue, gaming_scan=scan)
        close = provider.close

        def close_workers() -> None:
            record("providers-close")
            close()

        provider.close = close_workers
        return provider

    def serve(path, handle, stop, *, bound):
        bound.set()
        stop.wait()

    def stop_miner(argv, timeout):
        assert argv == ("/usr/bin/systemctl", "stop", "sparehash-xmrig.service")
        assert timeout == 5
        record("miner-stop")
        return ""

    with (
        patch.dict(os.environ, {"NOTIFY_SOCKET": "synthetic-unused"}, clear=True),
        patch.object(runtime.os, "geteuid", return_value=0),
        patch.object(runtime.config, "load", return_value=config.Config()),
        patch.object(runtime, "Providers", side_effect=providers),
        patch.object(runtime, "Jobs", return_value=jobs),
        patch.object(runtime, "Controller", side_effect=controller),
        patch.object(runtime.power, "PowerMonitor", return_value=monitor),
        patch.object(runtime.power, "read_sysfs", side_effect=sysfs),
        patch.object(runtime.thermal, "find_sensor", return_value=None),
        patch.object(runtime.ipc, "serve", side_effect=serve),
        patch.object(runtime.systemd, "notify_ready"),
        patch("sparehash.controller.is_retired", return_value=False),
        patch.object(runtime, "checked", side_effect=stop_miner),
        patch.object(runtime, "cleanup", side_effect=lambda: record("cleanup") or 0),
    ):
        # Test-only shortening of a fixed production grace, not an environment
        # setting or a weakened scanner, IPC or miner stop deadline.
        runtime.SHUTDOWN_WORKER_GRACE_SECONDS = 0.5
        raise SystemExit(runtime.run(directory / "state.json"))


@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGINT], ids=["sigterm", "sigint"])
@pytest.mark.parametrize("mode", ["scanner", "observation", "close-error", "healthy"])
def test_termination_stops_miner_before_waiting_for_scanner(
    tmp_path: Path, number: int, mode: str,
) -> None:
    launcher = ("import runpy,sys;sys.path.insert(0,sys.argv[1]);"
                "sys.argv=sys.argv[2:];runpy.run_path(sys.argv[0],run_name='__main__')")
    command = [sys.executable, "-I", "-B", "-c", launcher,
               str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve()),
               str(tmp_path), mode]
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    trace = tmp_path / "trace.jsonl"
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if trace.exists():
                events = trace.read_text()
                if '"scanner-entered"' in events and '"observation-entered"' in events:
                    break
            assert child.poll() is None, child.communicate()
            time.sleep(0.02)
        else:
            pytest.fail("scanner did not enter its real worker thread")
        child.send_signal(number)
        try:
            stdout, stderr = child.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            pytest.fail("controller termination still waits for a blocked worker/interpreter join")
        assert child.returncode == (0 if mode == "healthy" else 1), (stdout, stderr)
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        assert events.index("authority-revoked") < events.index("miner-stop")
        assert events.index("miner-stop") < events.index("jobs-close")
        if mode != "close-error":
            assert events.index("miner-stop") < events.index("providers-close")
        if mode != "healthy":
            assert "cleanup" not in events  # ExecStopPost will recheck empty after process exit.
            assert "worker shutdown" in stderr
        else:
            assert events[-1] == "cleanup"
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_cleanup_requires_empty_cgroup_before_protected_mutations() -> None:
    with (
        patch.object(runtime.os, "geteuid", return_value=0),
        patch.object(runtime, "empty", return_value=False),
        patch.object(runtime, "LeaseManager") as pages,
        patch.object(runtime, "Tree") as tree,
    ):
        assert runtime.cleanup() == 1
        pages.assert_not_called()
        tree.assert_not_called()


if __name__ == "__main__":
    _child(Path(sys.argv[1]), sys.argv[2])
