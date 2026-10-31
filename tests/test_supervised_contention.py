"""Fixture-only supervised benchmark tests: never run work() or host services."""

from __future__ import annotations

import copy
import importlib
import io
import json
import os
import sys
import tarfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path("qa").resolve()))
    return importlib.import_module("run_cpu_contention"), importlib.import_module("contention_safety")


def states(active=False):
    return {name: {"ActiveState": "active" if active else "inactive", "SubState": "running" if active else "dead",
                   "pid": index + 100 if active else 0, "InvocationID": "fixture", "ControlGroup": "/system.slice/" + name,
                   "policy": os.SCHED_IDLE, "nice": 19, "CPUWeight": "", "cgroup": {"cpu.idle": "0", "cpu.weight": "100"}}
            for index, name in enumerate(("sparehash.service", "sparehash-xmrig.service"))}


PARAMS = {"workers": 2, "seconds": 1, "warmup": .5, "repeats": 2, "abort_c": 80}


def phase(label, throughput=100):
    return {**PARAMS, "workload": "fixture", "median_mib_per_second": throughput,
            "samples": [{"mib_per_second": throughput}] * 3, "temperature_max_observed_c": 60,
            "services_before": states(label == "mining")}


def session(runner):
    data = {"schema": runner.SCHEMA, "id": "fixture", "status": "completed", "parameters": PARAMS,
            "tool_sha256": "fixture", "sensor_signature": [["k10temp", "Tctl"]],
            "installed": {"available": False},
            "cpu": dict.fromkeys(("models", "affinity", "logical_cpus", "physical_cores", "python", "openssl", "architecture", "kernel"), "fixture"),
            "foreground": {"membership": "0::/user.slice/session-1.scope", "ancestry": []},
            "phases": {name: phase(name, 90 if name == "mining" else 100) for name in ("baseline_a", "mining", "baseline_b")}}
    data["comparison"] = runner.session_comparison(data)
    return data


@pytest.mark.parametrize("fault", ["state", "pid", "nice", "restart", "controls"])
def test_service_validation_fails_closed(modules, fault):
    _, safety = modules
    pinned = states(True)
    row = copy.deepcopy(pinned)
    miner = row[safety.UNITS[1]]
    if fault == "state":
        miner["SubState"] = "exited"
    elif fault == "pid":
        miner["pid"] = 0
    elif fault == "nice":
        miner["nice"] = 0
    elif fault == "restart":
        miner["InvocationID"] = "new"
    else:
        miner["cgroup"]["cpu.idle"] = "1"
    with pytest.raises(safety.SafetyAbort):
        safety.validate_services(row, "mining", pinned)
    safety.validate_services(states(), "stopped")


def test_thermal_threshold_staleness_and_reader_failure(modules):
    _, safety = modules
    reader = SimpleNamespace(read=Mock(return_value=[60, 79]))
    monitor = safety.ThermalMonitor(reader, 80)
    monitor.sample()
    monitor.check()
    monitor.records[-1]["at"] = time.monotonic() - 2
    with pytest.raises(safety.SafetyAbort, match="stale"):
        monitor.check()
    reader.read.return_value = [80]
    monitor.sample()
    with pytest.raises(safety.SafetyAbort, match="threshold"):
        monitor.check()
    failed = safety.ThermalMonitor(SimpleNamespace(read=Mock(side_effect=OSError)), 80)
    failed.interval = .001
    failed._loop()
    with pytest.raises(safety.SafetyAbort, match="failed"):
        failed.check()


def test_sensor_discovery_and_read_failure(modules, tmp_path):
    _, safety = modules
    with pytest.raises(safety.SafetyAbort):
        safety.Thermometer(tmp_path)
    sensor = tmp_path / "hwmon0"
    sensor.mkdir()
    (sensor / "name").write_text("k10temp\n")
    value = sensor / "temp1_input"
    value.write_text("55000\n")
    thermometer = safety.Thermometer(tmp_path)
    assert thermometer.read() == [55]
    for text in ("NaN", "", "150000", "-1000"):
        value.write_text(text)
        with pytest.raises(safety.SafetyAbort):
            thermometer.read()
    value.unlink()
    with pytest.raises(safety.SafetyAbort):
        thermometer.read()


@pytest.mark.parametrize("interrupt", [False, True])
def test_interaction_flow_and_json(modules, monkeypatch, tmp_path, interrupt):
    runner, _ = modules
    monkeypatch.setattr(runner, "cpu_information", lambda: {"models": ["fixture"], "physical_cores": 12, "affinity": list(range(24))})
    monkeypatch.setattr(runner, "foreground_evidence", lambda: {})
    current = states()
    prompts = []
    def confirm(message, token, monitor):
        nonlocal current
        prompts.append(token)
        if token == "":
            current = states(True)
        elif token == "STOPPED":
            current = states()
    labels = []
    def measure(label, *_args, **_kwargs):
        labels.append(label)
        if interrupt:
            raise KeyboardInterrupt
        return phase(label, 90 if label == "mining" else 100)
    thermal = SimpleNamespace(sensors=[{"driver": "k10temp", "label": "Tctl"}], read=lambda: [50])
    result = runner.run_session(tmp_path, PARAMS, confirm=confirm, phase_runner=measure, reader=lambda: current,
                                thermometer=thermal, identity_reader=lambda: {"available": False})
    assert json.loads((tmp_path / "session.json").read_text())["status"] == result["status"]
    report = (tmp_path / "report.txt").read_text()
    assert "NOT a performance acceptance pass" in report
    if interrupt:
        assert result["status"] == "aborted" and "did NOT stop" in report
    else:
        assert labels == ["baseline_a", "mining", "baseline_b"]
        assert prompts == ["STOPPED", "", "HASHING", "STOPPED"]
        assert result["comparison"]["relative_to_baseline_a"]["degradation_percent"] == 10


def test_preflight_failure_is_saved(modules, monkeypatch, tmp_path):
    runner, safety = modules
    monkeypatch.setattr(runner, "Thermometer", Mock(side_effect=safety.SafetyAbort("No sensor")))
    result = runner.run_session(tmp_path, PARAMS)
    assert result["status"] == "aborted"
    assert json.loads((tmp_path / "session.json").read_text())["error"] == "No sensor"


def test_cleanup_signals_only_owned_handles(modules):
    runner, safety = modules
    owned = [Mock(pid=101), Mock(pid=None)]
    owned[0].is_alive.side_effect = [True, True, False]
    unrelated = Mock(pid=999)
    pool = runner.OwnedWorkers(2, lambda: None)
    pool.processes = owned
    pool.connections = [Mock()]
    pool.close()
    owned[0].terminate.assert_called_once()
    owned[0].kill.assert_called_once()
    owned[1].terminate.assert_not_called()
    unrelated.assert_not_called()
    pool = runner.OwnedWorkers(1, Mock(side_effect=safety.SafetyAbort("thermal")))
    with pytest.raises(safety.SafetyAbort):
        pool.__enter__()
    assert pool.processes == []


def test_like_for_like_comparison_and_drift(modules):
    runner, _ = modules
    old = session(runner)
    new = copy.deepcopy(old)
    new["phases"]["baseline_b"] = phase("baseline_b", 110)
    new["foreground"]["membership"] = "0::/user.slice/session-2.scope"
    result = runner.compare_sessions(old, new)
    assert result["warnings"] and "MEASUREMENT ONLY" in result["verdict"]
    assert result["new_comparison"]["relative_to_baseline_b"]["degradation_percent"] > 18
    for key in ("parameters", "tool_sha256", "sensor_signature"):
        changed = copy.deepcopy(old)
        changed[key] = "different"
        with pytest.raises(ValueError, match="like-for-like"):
            runner.compare_sessions(old, changed)
    new["phases"]["mining"]["median_mib_per_second"] = float("nan")
    with pytest.raises(ValueError, match="Invalid"):
        runner.compare_sessions(old, new)


def test_prompt_cancel_and_thermal_abort(modules, monkeypatch):
    runner, safety = modules
    monkeypatch.setattr(runner.select, "select", lambda *_: ([True], [], []))
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(readline=lambda: "NO\n"))
    with pytest.raises(safety.SafetyAbort, match="cancelled"):
        runner.prompt("fixture", "YES", Mock())
    with pytest.raises(safety.SafetyAbort, match="hot"):
        runner.prompt("fixture", "YES", SimpleNamespace(check=Mock(side_effect=safety.SafetyAbort("hot"))))


def test_read_only_service_query_whitelist(modules, monkeypatch):
    _, safety = modules
    text = "\n\n".join("\n".join([f"Id={name}", "LoadState=loaded", "ActiveState=inactive", "SubState=dead", "MainPID=0"])
                          for name in safety.UNITS)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=text))
    monkeypatch.setattr(safety.subprocess, "run", run)
    safety.validate_services(safety.services(), "stopped")
    argv = run.call_args.args[0]
    assert argv[1] == "show" and "ExecStart" not in " ".join(argv)
    run.return_value.stdout = ""
    with pytest.raises(safety.SafetyAbort):
        safety.services()


def test_installed_identity_never_reads_configuration(modules, tmp_path):
    _, safety = modules
    repo = tmp_path / "repo"
    (repo / "sparehash").mkdir(parents=True)
    (repo / "sparehash/example.py").write_text("# public source")
    # The untrusted installation ancestry must fail before source/config reads.
    result = safety.installed_identity(repo, tmp_path / "installed")
    assert result["available"] is False


def test_installed_identity_matches_only_public_inventory(modules, monkeypatch, tmp_path):
    _, safety = modules
    repo, installed = tmp_path / "repo", tmp_path / "installed"
    for root in (repo, installed):
        (root / "sparehash").mkdir(parents=True)
        (root / "sparehash/example.py").write_bytes(b"# public source\n")
        (root / "sparehash/example.py").chmod(0o644)
    (installed / "secret-config.toml").write_text("must not be read")
    original = Path.lstat
    ancestors = {installed, *installed.parents, installed / "sparehash"}
    def trusted_metadata(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path in ancestors:
            return SimpleNamespace(st_mode=info.st_mode & ~0o022, st_uid=os.getuid())
        return info
    monkeypatch.setattr(Path, "lstat", trusted_metadata)
    blob = io.BytesIO()
    with tarfile.open(fileobj=blob, mode="w") as archive:
        member = tarfile.TarInfo("sparehash/example.py")
        payload = b"# public source\n"
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    def git(argv, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="a" * 40 + "\n" if "rev-parse" in argv else blob.getvalue())
    monkeypatch.setattr(safety.subprocess, "run", git)
    result = safety.installed_identity(repo, installed, expected_uid=os.getuid())
    assert result["available"] and result["file_count"] == 1
    assert result["matching_source_commits"] == ["a" * 40]
    (installed / "sparehash/example.py").write_text("# changed")
    assert safety.installed_identity(repo, installed, expected_uid=os.getuid())["matching_source_commits"] == []


def test_phase_identical_samples_and_service_failure_cleanup(modules, monkeypatch):
    runner, safety = modules
    monkeypatch.setattr(runner, "foreground_evidence", lambda: {"ancestry": []})
    pool = Mock()
    pool.__enter__ = Mock(return_value=pool)
    pool.__exit__ = Mock(return_value=False)
    pool.sample.return_value = {"mib_per_second": 100}
    engine = Mock(return_value=pool)
    monitor = SimpleNamespace(check=lambda: None, trace=lambda: [{"max_c": 55}])
    result = runner.run_phase("baseline_a", PARAMS, monitor, reader=states, engine=engine)
    assert [call.args[0] for call in pool.sample.call_args_list] == [.5, 1, 1]
    assert result["median_mib_per_second"] == 100
    assert result["temperature_max_observed_c"] == 55
    pool.__exit__.assert_called_once()
    pool.__exit__.reset_mock()
    reader = Mock(side_effect=[states(), states(True)])
    with pytest.raises(safety.SafetyAbort):
        runner.run_phase("baseline_a", PARAMS, monitor, reader=reader, engine=engine)
    pool.__exit__.assert_called_once()


def test_partial_worker_start_failure_cleans_started_children(modules):
    runner, _ = modules
    context = Mock()
    parent, child = Mock(), Mock()
    context.Pipe.return_value = parent, child
    process = Mock(pid=100)
    process.start.side_effect = OSError("fixture startup failure")
    process.is_alive.return_value = False
    context.Process.return_value = process
    pool = runner.OwnedWorkers(1, lambda: None, context=context)
    with pytest.raises(OSError):
        pool.__enter__()
    child.close.assert_called_once()
    parent.close.assert_called_once()
    assert process.join.call_count == 2


def test_worker_gather_aborts_without_work(modules, monkeypatch):
    runner, safety = modules
    pool = runner.OwnedWorkers(1, lambda: None)
    connection = Mock()
    connection.recv.return_value = {"kind": "error"}
    pool.connections = [connection]
    monkeypatch.setattr(runner, "wait", lambda *_args, **_kwargs: [connection])
    with pytest.raises(safety.SafetyAbort, match="Unexpected"):
        pool.gather("result", 1)
    connection.recv.side_effect = EOFError
    with pytest.raises(safety.SafetyAbort, match="disappeared"):
        pool.gather("result", 1)


def test_saved_comparison_number_selection_and_outputs(modules, monkeypatch, tmp_path):
    runner, _ = modules
    for index in range(2):
        directory = tmp_path / f"session-{index}"
        directory.mkdir()
        data = session(runner)
        data["id"] = directory.name
        (directory / "session.json").write_text(json.dumps(data))
    answers = iter(["1", "2"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    runner.compare_saved(tmp_path)
    outputs = list(tmp_path.glob("comparison-*/comparison.json"))
    assert len(outputs) == 1
    assert json.loads(outputs[0].read_text())["old_session"] == "session-0"
    assert "MEASUREMENT ONLY" in outputs[0].with_name("report.txt").read_text()


def test_worker_sample_records_elapsed_cpu_and_rejects_priority(modules, monkeypatch):
    runner, safety = modules
    pool = runner.OwnedWorkers(1, lambda: None)
    pool.connections = [Mock()]
    data = {"policy": os.SCHED_OTHER, "nice": 0, "mib": 100, "elapsed": 1,
            "cpu_seconds": .9, "affinity": sorted(os.sched_getaffinity(0))}
    monkeypatch.setattr(pool, "gather", lambda kind, _seconds: [] if kind == "ready" else [{"result": data}])
    result = pool.sample(1)
    assert result["workers"][0]["cpu_seconds"] == .9 and result["elapsed"] > 0
    data["nice"] = 19
    with pytest.raises(safety.SafetyAbort, match="scheduling"):
        pool.sample(1)


def test_main_refuses_root_and_signal_interrupt(modules, monkeypatch):
    runner, safety = modules
    monkeypatch.setattr(sys, "argv", ["runner"])
    monkeypatch.setattr(runner.os, "geteuid", lambda: 0)
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 2
    with pytest.raises(safety.SafetyAbort, match="signal"):
        runner.interrupted(15, None)
