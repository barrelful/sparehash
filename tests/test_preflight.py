from __future__ import annotations

from dataclasses import replace

import pytest

from sparehash.config import Config, Gaming
from sparehash.preflight import GamingDecision, HostFacts, evaluate
from sparehash.sysdetect import Capabilities


@pytest.fixture
def caps() -> Capabilities:
    return Capabilities("x86_64", True, 255, True, "cgroup2", True, True, False, (0,), (), 8, 8_000_000_000, None, None, False)


@pytest.fixture
def facts() -> HostFacts:
    return HostFacts("bazzite", "42", atomic=True, dnf_available=True, supporting_facts=("ID=bazzite", "VERSION_ID=42"))


def test_selects_safe_strategy(caps: Capabilities, facts: HostFacts) -> None:
    result = evaluate(facts, caps, Config(), True, gaming_admission_decision="ADMISSIBLE_CANDIDATE", both_mode_coverage_complete=True)
    assert (result.support, result.gaming, result.readiness) == ("VALIDATED", "FULL", "READY")
    assert result.effects == ()


@pytest.mark.parametrize(
    ("updates", "expected"),
    (({"cgroup_shutdown_proven": False}, "CgroupShutdownUnproven"),
     ({"noexec_paths": ("/opt",)}, "NoExecPath"),
     ({"python_available": False}, "PythonUnavailable"),
     ({"pid1_systemd": False}, "SystemdNotPid1"),
     ({"security_policy_unsafe": True}, "SecurityPolicyUnsafe"),
     ({"identity_available": False}, "IdentityUnavailable"),
     ({"dnf_available": False}, "AtomicDnfNotAvailable")),
)
def test_refuses_invalid_capability_combinations(caps: Capabilities, facts: HostFacts, updates: dict[str, object], expected: str) -> None:
    result = evaluate(replace(facts, **updates), caps, Config(), False)
    assert result.readiness == "REFUSED_UNSAFE"
    assert expected in result.unsafe_reasons


@pytest.mark.parametrize("facts", (HostFacts("nixos", "24.11", nixos=True), HostFacts("debian", "13", proxmox_evidence=True)))
def test_refuses_nixos_and_proxmox(caps: Capabilities, facts: HostFacts) -> None:
    result = evaluate(facts, caps, Config(), True)
    assert result.support == "UNSUPPORTED"
    assert result.readiness == "REFUSED_UNSUPPORTED"


def test_atomic_requires_available_dnf_and_reports_unverified_gaming(caps: Capabilities, facts: HostFacts) -> None:
    result = evaluate(facts, caps, Config(), True)
    assert result.gaming == "UNVERIFIED"
    assert result.readiness == "ACTION_REQUIRED"
    unavailable = evaluate(replace(facts, dnf_available=False), caps, Config(), True)
    assert unavailable.readiness == "REFUSED_UNSAFE"
    assert "AtomicDnfNotAvailable" in unavailable.reason_codes


def test_gaming_requires_complete_admission(caps: Capabilities, facts: HostFacts) -> None:
    assert evaluate(facts, caps, Config(), True, gaming_admission_decision="ADMISSIBLE_CANDIDATE").gaming == "CONSERVATIVE"
    assert evaluate(facts, caps, Config(), True, gaming_admission_decision="NO_DEPENDABLE_ROUTE").gaming == "UNSUPPORTED"
    assert evaluate(facts, caps, Config(gaming=Gaming(enabled=False)), True).gaming == "UNVERIFIED"


def test_missing_facts_are_inconclusive(caps: Capabilities) -> None:
    assert evaluate(None, caps, Config(), True).readiness == "INCONCLUSIVE"


@pytest.mark.parametrize("install_action", (False, True))
def test_atomic_without_dnf_refuses_with_either_action(
    caps: Capabilities, facts: HostFacts, install_action: bool,
) -> None:
    # Given an Atomic host without the required deployment tool.
    unavailable = replace(facts, dnf_available=False)
    # When either a new-install or no-new-install strategy is considered.
    result = evaluate(unavailable, caps, Config(), install_action)
    # Then neither strategy is ready.
    assert result.readiness == "REFUSED_UNSAFE"
    assert "AtomicDnfNotAvailable" in result.reason_codes


@pytest.mark.parametrize(
    "decision", (None, "NO_DEPENDABLE_ROUTE", "BLOCKED_ENVIRONMENT"),
)
def test_complete_coverage_without_admission_is_never_full(
    caps: Capabilities, facts: HostFacts, decision: GamingDecision | None,
) -> None:
    # Given coverage claims without an admissible producer.
    # When the claims are evaluated independently of admission.
    result = evaluate(
        facts, caps, Config(), True,
        gaming_admission_decision=decision, both_mode_coverage_complete=True,
    )
    # Then coverage alone cannot authorize FULL gaming support.
    assert result.gaming != "FULL"
