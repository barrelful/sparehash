"""Local runner regressions; service operations are never executed."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Final

import pytest

QA: Final = Path(__file__).resolve().parents[1] / "qa"


@pytest.mark.parametrize("body", ["exit 7", "echo SKIP: unavailable; exit 0", "echo FAIL: broken; exit 0"])
def test_suite_rejected_when_exit_or_coverage_fails(tmp_path: Path, body: str) -> None:
    # Given
    suite = tmp_path / "suite.sh"
    _ = suite.write_text(f"#!/bin/bash\n{body}\n")
    suite.chmod(0o755)
    # When
    result = subprocess.run(["bash", "-c", f"source '{QA}/run_all.sh'; "
                             f"SCRIPT_DIR='{tmp_path}'; EVIDENCE_DIR='{tmp_path}'; run_suite suite.sh"],
                            capture_output=True, text=True, check=False)
    # Then
    assert result.returncode != 0


def test_suite_rejected_when_missing(tmp_path: Path) -> None:
    # Given / When
    result = subprocess.run(["bash", "-c", f"source '{QA}/run_all.sh'; "
                             f"SCRIPT_DIR='{tmp_path}'; EVIDENCE_DIR='{tmp_path}'; run_suite missing.sh"],
                            capture_output=True, text=True, check=False)
    # Then
    assert result.returncode != 0


def test_runner_order_when_all_suites_succeed(tmp_path: Path) -> None:
    # Given: replace only the VM action seams, recording their execution order.
    command = f"""
source '{QA}/run_all.sh'
EVIDENCE_DIR='{tmp_path}'
qa_preflight() {{ :; }}
qa_answers_contract() {{ :; }}
qa_quiesce() {{ :; }}
qa_setup() {{ :; }}
qa_pool_start() {{ :; }}
qa_pool_stop() {{ :; }}
qa_fixture_overlays() {{ :; }}
qa_cleanup() {{ exit $?; }}
qa_install_acceptance() {{ echo install; echo idempotency; }}
qa_restart() {{ :; }}
qa_wait_miner() {{ :; }}
qa_assert_power() {{ :; }}
qa_status() {{ echo status-evidence; }}
qa_verify_final_status() {{ :; }}
run_suite() {{ echo "$1"; }}
main
"""
    # When
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True, check=True)
    # Then
    lines = result.stdout.splitlines()
    assert lines.index("install") < lines.index("idempotency") < lines.index("qa_power_shim.sh")
    assert lines.index("qa_power_shim.sh") < lines.index("status-evidence") < lines.index("qa_status_output.sh")
    assert lines.index("qa_status_output.sh") < lines.index("qa_uninstall.sh")


@pytest.mark.parametrize("body", [
    "echo 'UNAVAILABLE: provider restart'; exit 0",
    "echo 'unsupported contract: no provider'; exit 0",
])
def test_suite_rejected_when_coverage_words_reported(tmp_path: Path, body: str) -> None:
    # Given
    suite = tmp_path / "suite.sh"
    _ = suite.write_text(f"#!/bin/bash\n{body}\n")
    suite.chmod(0o755)
    # When
    result = subprocess.run(["bash", "-c", f"source '{QA}/run_all.sh'; "
                             f"SCRIPT_DIR='{tmp_path}'; EVIDENCE_DIR='{tmp_path}'; run_suite suite.sh"],
                            capture_output=True, text=True, check=False)
    # Then
    assert result.returncode != 0


@pytest.mark.parametrize("body", [
    "echo 'PASS: failed initial readiness keeps miner off'; exit 0",
    "echo '  documented final line present: MSR: unavailable in v1'; exit 0",
])
def test_suite_accepted_when_passing_diagnostics_contain_lookalike_words(tmp_path: Path, body: str) -> None:
    # Given
    suite = tmp_path / "suite.sh"
    _ = suite.write_text(f"#!/bin/bash\n{body}\n")
    suite.chmod(0o755)
    # When
    result = subprocess.run(["bash", "-c", f"source '{QA}/run_all.sh'; "
                             f"SCRIPT_DIR='{tmp_path}'; EVIDENCE_DIR='{tmp_path}'; run_suite suite.sh"],
                            capture_output=True, text=True, check=False)
    # Then
    assert result.returncode == 0


def test_cleanup_preserves_failure_when_no_runtime_owned() -> None:
    # Given / When
    result = subprocess.run(["bash", "-c", f"source '{QA}/lib/runtime.sh'; trap qa_cleanup EXIT; exit 37"],
                            capture_output=True, text=True, check=False)
    # Then
    assert result.returncode == 37


def test_runner_refuses_when_vm_consent_absent() -> None:
    # Given
    environment = dict(os.environ)
    environment.pop("SPAREHASH_QA_DISPOSABLE_VM", None)
    # When
    result = subprocess.run(["bash", str(QA / "run_all.sh")], env=environment,
                            capture_output=True, text=True, check=False)
    # Then
    assert result.returncode != 0
    assert "SPAREHASH_QA_DISPOSABLE_VM=YES" in result.stderr


def test_cleanup_retains_fixtures_when_stop_fails(tmp_path: Path) -> None:
    # Given
    controller = tmp_path / "controller"
    _ = controller.write_text("#!/bin/sh\nexit 9\n")
    controller.chmod(0o755)
    command = f"source '{QA}/lib/runtime.sh'; QA_ROOT='{tmp_path}'; " \
              f"CONTROLLER_BIN='{controller}'; QA_OWNS_RUNTIME=1; trap qa_cleanup EXIT; exit 37"
    # When
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True, check=False)
    # Then
    assert result.returncode == 37
    assert controller.exists()
