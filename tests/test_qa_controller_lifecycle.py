"""Local acceptance regressions; no system service, mount, or real kill is used."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Final

import pytest

import qa.lifecycle_check as check
from sparehash import ipc

SCRIPT: Final = Path(__file__).resolve().parents[1] / "qa/qa_controller_lifecycle.sh"


def test_tasks_when_parent_empty_includes_populated_descendants(tmp_path: Path) -> None:
    # Given
    _ = (tmp_path / "cgroup.procs").write_text("")
    child = tmp_path / "worker"
    child.mkdir()
    _ = (child / "cgroup.procs").write_text("321\n")
    # When
    tasks = check.tasks(tmp_path)
    # Then
    assert 321 in tasks


def test_tasks_when_recorded_cgroup_removed_counts_as_empty(tmp_path: Path) -> None:
    # Given
    recorded = tmp_path / "removed-service"
    # When
    tasks = check.tasks(recorded)
    # Then
    assert tasks == ()


def test_wait_when_proof_arrives_after_deadline_rejects_it(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    times = iter((100.0, 105.01))
    monkeypatch.setattr(time, "monotonic", lambda: next(times))
    # When / Then
    with pytest.raises(check.LifecycleError):
        check.wait_until(lambda: True, 5)


def test_exchange_when_live_ipc_rejects_action_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    def reject(_request: ipc.Document, *, timeout: float) -> ipc.Document:
        assert timeout == 1
        return {"ok": False, "error": "permission denied"}
    monkeypatch.setattr(ipc, "call", reject)
    # When / Then
    with pytest.raises(check.LifecycleError):
        _ = check.exchange("debug-set", "battery")


def test_blocked_when_only_debug_override_exists_does_not_prove_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setattr(check, "status", lambda: check.Status(2, (), ("manual",), False))
    monkeypatch.setattr(check, "miner_off", lambda: True)
    # When
    persistent = check.blocked("manual")
    # Then
    assert persistent is False


def test_direct_start_when_systemctl_succeeds_still_requires_denial_proof() -> None:
    # Given: source only the harness functions; replace the host action seam.
    command = f"""
source '{SCRIPT}'
timeout() {{ return 0; }}
check() {{ return 17; }}
deny_direct_start
"""
    # When
    result = subprocess.run(["bash", "-c", command], capture_output=True, timeout=5)
    # Then
    assert result.returncode == 17


@pytest.fixture
def restart(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Systemd loss seam: real temporary cgroup/spec files, simulated invocation."""
    group = tmp_path / "system.slice/miner.service"
    group.mkdir(parents=True)
    _ = (group / "cgroup.procs").write_text("222\n")
    spec = tmp_path / "operation.json"
    _ = spec.write_text('{"generation": 2, "mode": "mine"}')
    os.utime(spec, ns=(1, 1))
    properties = {
        (check.CONTROLLER, "MainPID"): "111",
        (check.CONTROLLER, "InvocationID"): "old",
        (check.CONTROLLER, "ActiveState"): "active",
        (check.MINER, "MainPID"): "222",
        (check.MINER, "ActiveState"): "active",
        (check.MINER, "ControlGroup"): "/system.slice/miner.service",
    }
    births: dict[int, str | None] = {222: "original"}

    def kill(pid: int, sig: int) -> None:
        assert (pid, sig) == (111, signal.SIGKILL)
        _ = (group / "cgroup.procs").write_text("")
        births[222] = None
        properties[(check.CONTROLLER, "MainPID")] = "333"
        properties[(check.CONTROLLER, "InvocationID")] = "new"
        properties[(check.MINER, "MainPID")] = "444"
        _ = spec.write_text('{"generation": 2, "mode": "mine"}')
        os.utime(spec, ns=(2, 2))

    def prop(unit: str, name: str) -> str:
        return properties[(unit, name)]

    def birth(pid: int) -> str | None:
        return births.get(pid)

    monkeypatch.setattr(check, "CGROUP", tmp_path)
    monkeypatch.setattr(check, "SPEC", spec)
    monkeypatch.setattr(check, "prop", prop)
    monkeypatch.setattr(check, "status", lambda: check.Status(2, (), (), True))
    monkeypatch.setattr(check, "process_birth", birth)
    monkeypatch.setattr(os, "kill", kill)
    return group


def test_loss_when_systemd_restarts_accepts_new_invocation_with_same_counter(restart: Path) -> None:
    # Given: the numeric generation resets; the new invocation never appears inactive.
    assert restart.is_dir()
    # When
    check.loss()
    # Then
    assert check.prop(check.CONTROLLER, "MainPID") == "333"


def test_loss_when_descendant_survives_fails_before_recovery(
    restart: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    child = restart / "worker"
    child.mkdir()
    _ = (child / "cgroup.procs").write_text("555\n")
    ticks = iter(float(value) for value in range(100))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    def sleep(_duration: float) -> None:
        return None
    monkeypatch.setattr(time, "sleep", sleep)
    # When / Then
    with pytest.raises(check.LifecycleError):
        check.loss()


def test_denied_when_execcondition_skips_but_miner_runs_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setattr(check, "miner_off", lambda: False)
    # When / Then
    with pytest.raises(check.LifecycleError):
        check.main(["denied"])


def test_denied_when_unit_inactive_without_condition_denial_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setattr(check, "miner_off", lambda: True)
    def prop(_unit: str, _name: str) -> str:
        return "code=exited ; status=0"
    monkeypatch.setattr(check, "prop", prop)
    # When / Then
    with pytest.raises(check.LifecycleError):
        check.main(["denied"])
