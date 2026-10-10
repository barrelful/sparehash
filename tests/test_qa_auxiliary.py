"""Auxiliary acceptance checker regressions; temp files only, never the host."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

AUXILIARY: Final = Path(__file__).resolve().parents[1] / "qa/auxiliary_check.py"


def status_document() -> dict[str, object]:
    """A complete, valid `sparehash status --json` document."""
    return {
        "controller": "ready",
        "miner": "running",
        "blockers": [["gaming", "unsafe or unknown observation"]],
        "power": {
            "inventory": {"value": False, "stale": False},
            "upower": {"value": False, "stale": False},
            "sysfs": {"value": False, "stale": False},
        },
        "gaming": [{"uid": 1000, "user": "gamer", "active": True, "stale": False}],
        "thermal_sensor": "/sys/class/hwmon/hwmon0/temp1_input",
        "temperature_c": 41.0,
        "hugepages_actual": 1280,
        "hugepages_target": 1280,
        "numa_nodes": [0],
        "scheduler": {
            "pid": 123,
            "policy": 5,
            "nice": 19,
            "cpu_weight": 1,
            "cpu_idle": 0,
            "requested": "SCHED_IDLE; Nice=19; CPUWeight=idle if supported, otherwise 1",
        },
        "api": "available",
        "hashrate": 1024.0,
        "pool": "127.0.0.1:3333",
        "dns_hint": None,
        "msr": "unavailable in v1",
        "thermal_status": "available",
        "numa_warning": None,
        "hugepages_attainment": "satisfied",
        "ups_guard": "desktop/UPS-only",
    }


def ipc_document() -> dict[str, object]:
    """A complete, valid controller IPC status document."""
    return {
        "ok": True,
        "ready": True,
        "blockers": ["gaming"],
        "debug": [],
        "generation": 4,
        "running": False,
        "reasons": {"gaming": "unsafe or unknown observation"},
        "observation_age": 0.5,
        "gaming": [[1000, True]],
    }


def run(*arguments: str) -> subprocess.CompletedProcess[str]:
    # Given the checker, When it inspects a fixture, Then the verdict is a process exit code
    return subprocess.run(
        [sys.executable, str(AUXILIARY), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def write(tmp_path: Path, name: str, payload: str) -> Path:
    target = tmp_path / name
    target.write_text(payload, encoding="utf-8")
    return target


def test_status_accepts_complete_document_when_types_hold(tmp_path: Path) -> None:
    # Given a complete status document
    target = write(tmp_path, "status.json", json.dumps(status_document()))
    # When / Then
    result = run("status", str(target))
    assert result.returncode == 0, result.stderr
    assert "FAIL" not in result.stderr


def test_status_fails_when_document_is_not_json(tmp_path: Path) -> None:
    # Given bytes that are not JSON at all
    target = write(tmp_path, "status.json", "controller: ready\nminer: running\n")
    # When / Then
    result = run("status", str(target))
    assert result.returncode == 1
    assert "invalid JSON" in result.stderr


def test_status_fails_when_json_constant_is_not_finite(tmp_path: Path) -> None:
    # Given a document carrying a non-finite constant
    document = status_document()
    document["temperature_c"] = float("nan")
    target = write(tmp_path, "status.json", json.dumps(document))
    # When / Then
    result = run("status", str(target))
    assert result.returncode == 1
    assert "non-finite" in result.stderr


def test_status_fails_when_a_required_field_has_the_wrong_type(tmp_path: Path) -> None:
    # Given a document whose miner field is not a string
    document = status_document()
    document["miner"] = 7
    target = write(tmp_path, "status.json", json.dumps(document))
    # When / Then
    result = run("status", str(target))
    assert result.returncode == 1
    assert "miner must be a string" in result.stderr


def test_status_fails_when_a_required_field_is_missing(tmp_path: Path) -> None:
    # Given a document without the dns hint field
    document = status_document()
    del document["dns_hint"]
    target = write(tmp_path, "status.json", json.dumps(document))
    # When / Then
    result = run("status", str(target))
    assert result.returncode == 1
    assert "missing required field 'dns_hint'" in result.stderr


def test_status_fails_when_blockers_are_not_name_reason_pairs(tmp_path: Path) -> None:
    # Given blockers rendered as a bare string instead of [name, reason] pairs
    document = status_document()
    document["blockers"] = "gaming"
    target = write(tmp_path, "status.json", json.dumps(document))
    # When / Then
    result = run("status", str(target))
    assert result.returncode == 1
    assert "blockers must be an array" in result.stderr


def test_ipc_accepts_acknowledged_document_when_contract_holds(tmp_path: Path) -> None:
    # Given a controller status document
    target = write(tmp_path, "ipc.json", json.dumps(ipc_document()))
    # When / Then
    assert run("ipc", str(target)).returncode == 0


def test_ipc_fails_when_the_controller_rejects_the_request(tmp_path: Path) -> None:
    # Given a rejection from the controller
    document = ipc_document()
    document["ok"] = False
    document["error"] = "lifecycle busy"
    target = write(tmp_path, "ipc.json", json.dumps(document))
    # When / Then
    result = run("ipc", str(target))
    assert result.returncode == 1
    assert "lifecycle busy" in result.stderr


def test_ipc_fails_when_generation_is_not_an_integer(tmp_path: Path) -> None:
    # Given a generation rendered as a string
    document = ipc_document()
    document["generation"] = "4"
    target = write(tmp_path, "ipc.json", json.dumps(document))
    # When / Then
    result = run("ipc", str(target))
    assert result.returncode == 1
    assert "generation must be an integer" in result.stderr


def test_ipc_fails_when_reasons_do_not_match_blockers(tmp_path: Path) -> None:
    # Given reasons advertising a blocker the blockers array does not list
    document = ipc_document()
    document["reasons"] = {"thermal": "limit reached"}
    target = write(tmp_path, "ipc.json", json.dumps(document))
    # When / Then
    result = run("ipc", str(target))
    assert result.returncode == 1
    assert "must match blockers" in result.stderr


@pytest.mark.parametrize("code,allowed,accepted", [
    ("0", "0", True),
    ("3", "0", False),
    ("3", "0,3", True),
    ("2", "0,3", False),
    ("1", "0", False),
])
def test_action_requires_an_allowed_exit_status(code: str, allowed: str, accepted: bool) -> None:
    # Given a captured exit status and the statuses the contract permits
    # When / Then
    result = run("action", code, "sparehash uninstall", allowed)
    assert (result.returncode == 0) is accepted, result.stderr


def test_action_fails_when_the_exit_status_is_not_an_integer(tmp_path: Path) -> None:
    # Given a captured exit status that is not a number
    # When / Then
    result = run("action", "signal-9", "sparehash status", "0")
    assert result.returncode == 1
    assert "not an integer" in result.stderr


def test_bytes_accept_unchanged_file(tmp_path: Path) -> None:
    # Given a recorded digest of a file
    target = tmp_path / "config.toml"
    payload = b'version = 1\nwallet = "4" + "1" * 94\n'
    target.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    # When / Then
    assert run("bytes", str(target), digest).returncode == 0


def test_bytes_fail_when_the_file_changed(tmp_path: Path) -> None:
    # Given a recorded digest and different current bytes
    target = tmp_path / "config.toml"
    target.write_bytes(b"version = 1\n")
    digest = hashlib.sha256(b"version = 2\n").hexdigest()
    # When / Then
    result = run("bytes", str(target), digest)
    assert result.returncode == 1
    assert "changed bytes" in result.stderr


def test_bytes_fail_when_the_file_is_missing(tmp_path: Path) -> None:
    # Given a recorded digest and no file at all
    target = tmp_path / "absent.toml"
    digest = "0" * 64
    # When / Then
    result = run("bytes", str(target), digest)
    assert result.returncode == 1
    assert "cannot read" in result.stderr


def test_unknown_subcommand_is_a_usage_error(tmp_path: Path) -> None:
    # Given an unrecognized subcommand
    # When / Then
    result = run("waiver", str(tmp_path))
    assert result.returncode == 2
    assert "usage" in result.stderr
