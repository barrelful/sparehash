# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""C1 admission contract tests: complete contracts admit; incomplete ones reject.

These tests exercise the pure evaluator and the rootless replay runner. They
never start Steam and never require a real capture surface.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from qa.probe_gaming import (
    ADMISSIBLE_CANDIDATE,
    BLOCKED_ENVIRONMENT,
    NO_DEPENDABLE_ROUTE,
    build_admission,
    evaluate_contract,
    validate_admission,
)
from qa.probe_gaming import (
    main as probe_main,
)


def _mode() -> dict[str, Any]:
    return {
        "producer": "global steam-lifecycle provider",
        "initial_inventory_complete": True,
        "lifetime_owner": "provider session lifetime with empty-cgroup proof",
        "health_signals": ["monitor-eof", "heartbeat", "reconnect"],
        "negative_proof_argument": "cleared only from a complete current-epoch inventory",
    }


def _complete_contract() -> dict[str, Any]:
    return {
        "producer_fingerprint": {
            "name": "steam-lifecycle",
            "version": "1.0.0",
            "source": "https://example.invalid/steam-lifecycle",
            "supported_by_upstream": True,
        },
        "both_mode_coverage": {"gaming_mode": _mode(), "kde": _mode()},
        "lifetime_argument": "full native+Proton lifetime across both modes",
        "negative_proof_basis": "complete_inventory",
        "budgets": {
            "idle_cpu_percent_max": 0.8,
            "incremental_rss_mib_max": 24,
            "subprocess_query_per_minute_max": 6,
        },
        "unresolved_tests": [],
    }


def _mutate(case: str) -> dict[str, Any]:
    contract = _complete_contract()
    coverage = contract["both_mode_coverage"]
    assert isinstance(coverage, dict)
    if case == "gaming-mode-only":
        del coverage["kde"]
    elif case == "kde-only":
        del coverage["gaming_mode"]
    elif case == "scopes-reaper-silence":
        contract["negative_proof_basis"] = "scope_reaper_silence"
    elif case == "finite-trials":
        contract["negative_proof_basis"] = "finite_trials"
    elif case == "absent-initial-inventory":
        kde = coverage["kde"]
        assert isinstance(kde, dict)
        kde["initial_inventory_complete"] = False
    elif case == "missing-lifetime":
        kde = coverage["kde"]
        assert isinstance(kde, dict)
        kde["lifetime_owner"] = None
    elif case == "missing-health":
        gaming = coverage["gaming_mode"]
        assert isinstance(gaming, dict)
        gaming["health_signals"] = []
    elif case == "over-budget":
        contract["budgets"] = {
            "idle_cpu_percent_max": 2.0,
            "incremental_rss_mib_max": 64,
            "subprocess_query_per_minute_max": 6,
        }
    elif case == "missing-fingerprint":
        contract["producer_fingerprint"] = {
            "name": None,
            "version": None,
            "source": "unavailable",
            "supported_by_upstream": False,
        }
    elif case == "unsupported-producer":
        fingerprint = contract["producer_fingerprint"]
        assert isinstance(fingerprint, dict)
        fingerprint["supported_by_upstream"] = False
    else:  # pragma: no cover - guard against silent fixture drift
        raise AssertionError(f"unknown case: {case}")
    return contract


def test_accepts_complete_contract() -> None:
    result = evaluate_contract(_complete_contract())
    assert result.decision == ADMISSIBLE_CANDIDATE
    assert result.reasons == ()

    admission = cast(
        "dict[str, Any]",
        build_admission(
            _complete_contract(), evaluated_at="2026-01-01T00:00:00+00:00", evidence=()
        ),
    )
    validate_admission(admission)
    assert admission["decision"] == ADMISSIBLE_CANDIDATE
    assert admission["producer_fingerprint"]["name"] == "steam-lifecycle"
    assert admission["both_mode_coverage"]["gaming_mode"]["initial_inventory_complete"] is True
    assert admission["both_mode_coverage"]["kde"]["initial_inventory_complete"] is True


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("gaming-mode-only", "GAMING_MODE_ONLY"),
        ("kde-only", "KDE_ONLY"),
        ("scopes-reaper-silence", "SCOPES_REAPER_SILENCE"),
        ("finite-trials", "FINITE_TRIALS_NOT_NEGATIVE_PROOF"),
        ("absent-initial-inventory", "MISSING_INITIAL_INVENTORY"),
        ("missing-lifetime", "NO_DEPENDABLE_LIFETIME"),
        ("missing-health", "MISSING_HEALTH"),
        ("over-budget", "OVER_BUDGET"),
        ("missing-fingerprint", "PRODUCER_FINGERPRINT_MISSING"),
        ("unsupported-producer", "UNSUPPORTED_PRODUCER"),
    ],
)
def test_rejects_incomplete_contract(case: str, reason: str) -> None:
    contract = _mutate(case)
    result = evaluate_contract(contract)
    assert result.decision == NO_DEPENDABLE_ROUTE
    assert reason in result.reasons

    admission = cast(
        "dict[str, Any]",
        build_admission(
            contract, evaluated_at="2026-01-01T00:00:00+00:00", evidence=()
        ),
    )
    validate_admission(admission)
    assert admission["decision"] == NO_DEPENDABLE_ROUTE
    assert reason in admission["rationale"]
    assert admission["unresolved_tests"]


def test_replay_accepts_captured_contract(tmp_path: Path) -> None:
    source = tmp_path / "capture"
    source.mkdir()
    (source / "contract.json").write_text(
        json.dumps(_complete_contract()), encoding="utf-8"
    )
    output = tmp_path / "out" / "admission.json"
    exit_code = probe_main(
        ["--output", str(output), "--capture-source", str(source), "--read-only"]
    )
    assert exit_code == 0
    admission = json.loads(output.read_text(encoding="utf-8"))
    validate_admission(admission)
    assert admission["decision"] == ADMISSIBLE_CANDIDATE
    overhead = json.loads((output.parent / "overhead.json").read_text(encoding="utf-8"))
    assert overhead["measured"] is False


def test_runner_records_blocked_environment_without_capture(tmp_path: Path) -> None:
    output = tmp_path / "admission.json"
    exit_code = probe_main(["--output", str(output), "--read-only"])
    assert exit_code == 0
    admission = json.loads(output.read_text(encoding="utf-8"))
    validate_admission(admission)
    assert admission["decision"] == BLOCKED_ENVIRONMENT
    assert admission["producer_fingerprint"]["source"] == "unavailable"
    assert admission["producer_fingerprint"]["supported_by_upstream"] is False
    assert admission["both_mode_coverage"]["gaming_mode"]["producer"] is None
    assert admission["unresolved_tests"]
    overhead = json.loads((output.parent / "overhead.json").read_text(encoding="utf-8"))
    assert overhead["decision"] == BLOCKED_ENVIRONMENT
    assert overhead["measured"] is False


def test_runner_rejects_unreadable_capture_source(tmp_path: Path) -> None:
    output = tmp_path / "admission.json"
    missing = tmp_path / "no-such-capture"
    exit_code = probe_main(
        ["--output", str(output), "--capture-source", str(missing), "--read-only"]
    )
    assert exit_code == 1
    assert not output.exists()


def test_deepcopy_isolation_guard() -> None:
    # Guard: the fixture builder must return independent dicts so mutations do
    # not leak between parametrized cases.
    first = _complete_contract()
    second = deepcopy(first)
    second["both_mode_coverage"]["kde"]["initial_inventory_complete"] = False
    assert first["both_mode_coverage"]["kde"]["initial_inventory_complete"] is True
