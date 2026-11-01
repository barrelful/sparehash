from __future__ import annotations

from dataclasses import replace

import pytest

from sparehash.config import Config, Gaming
from sparehash.preflight import HostFacts, evaluate
from sparehash.sysdetect import Capabilities


@pytest.fixture
def caps() -> Capabilities:
    return Capabilities("x86_64", True, 255, True, "cgroup2", True, True, False, (0,), (), 8, 8_000_000_000, None, None, False)


@pytest.fixture
def facts() -> HostFacts:
    return HostFacts("bazzite", "44", atomic=True, dnf_available=True, supporting_facts=("ID=bazzite", "VERSION_ID=44"))


def test_selects_safe_strategy(caps: Capabilities, facts: HostFacts) -> None:
    result = evaluate(facts, caps, Config(), True)
    assert (result.support, result.gaming, result.readiness) == ("VALIDATED", "BEST_EFFORT", "READY")
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


def test_atomic_requires_available_dnf_independent_of_gaming(caps: Capabilities, facts: HostFacts) -> None:
    result = evaluate(facts, caps, Config(), True)
    assert result.gaming == "BEST_EFFORT"
    assert result.readiness == "READY"
    unavailable = evaluate(replace(facts, dnf_available=False), caps, Config(), True)
    assert unavailable.readiness == "REFUSED_UNSAFE"
    assert "AtomicDnfNotAvailable" in unavailable.reason_codes


@pytest.mark.parametrize("enabled", (True, False))
def test_explicit_detection_switch_is_not_a_completeness_gate(
    caps: Capabilities, facts: HostFacts, enabled: bool,
) -> None:
    result = evaluate(facts, caps, Config(gaming=Gaming(enabled=enabled)), True)
    assert (result.gaming, result.readiness) == ("BEST_EFFORT" if enabled else "DISABLED", "READY")


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
