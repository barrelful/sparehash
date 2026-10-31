"""Exercise refusal paths only; no privileged VM or lifecycle actions locally."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.syspath_prepend(str(Path("qa").resolve()))
    spec = importlib.util.spec_from_file_location("vm_acceptance", Path("qa/vm_acceptance.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.subprocess, "run", Mock(side_effect=AssertionError("unexpected command")))
    return module


def test_guard_refuses_without_consent_before_command(harness, monkeypatch):
    monkeypatch.delenv("SPAREHASH_QA_DISPOSABLE_VM", raising=False)
    with pytest.raises(ValueError, match="consent"):
        harness.guard("0" * 64)


def test_guard_refuses_unprivileged_before_command(harness, monkeypatch):
    monkeypatch.setenv("SPAREHASH_QA_DISPOSABLE_VM", "YES")
    monkeypatch.setattr(harness.os, "geteuid", lambda: 1000)
    with pytest.raises(ValueError, match="guest root"):
        harness.guard("0" * 64)


def test_guard_refuses_physical_host_before_filesystem_effects(harness, monkeypatch):
    monkeypatch.setenv("SPAREHASH_QA_DISPOSABLE_VM", "YES")
    monkeypatch.setattr(harness.os, "geteuid", lambda: 0)
    harness.subprocess.run.side_effect = None
    harness.subprocess.run.return_value.returncode = 1
    with pytest.raises(ValueError, match="non-VM"):
        harness.guard("0" * 64)
    harness.subprocess.run.assert_called_once_with(["systemd-detect-virt", "--vm", "--quiet"], check=False)


def test_foreground_fixture_uses_accessible_cwd_and_preserves_original_failure(harness, tmp_path):
    fake = Mock()
    fake.evidence = tmp_path
    fake.empty.return_value = True

    def command(argv):
        if argv[0] == "runuser":
            assert argv[4:7] == ["/usr/bin/env", "--chdir=/var/tmp", "/usr/bin/python3"]
            raise RuntimeError("original foreground failure")

    fake.run.side_effect = command
    with pytest.raises(RuntimeError, match="original foreground failure"):
        harness.Acceptance.contention(fake)
    assert fake.run.call_count == 2  # Never stop a contender that was not started.


@pytest.mark.parametrize("phase,current,expected", [("verify", "new", True), ("exercise", "old", False)])
def test_reboot_gate_and_non_reboot_subset_are_distinct(harness, phase, current, expected):
    assert harness.checked_reboot(phase, "old", current) is expected


@pytest.mark.parametrize("phase,current", [("verify", "old"), ("exercise", "new"), ("other", "old")])
def test_reboot_gate_cannot_silently_degrade_to_non_reboot(harness, phase, current):
    with pytest.raises(AssertionError):
        harness.checked_reboot(phase, "old", current)
