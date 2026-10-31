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
