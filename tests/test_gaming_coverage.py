# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Coverage-tier configuration, status and unattended-refusal behavior.

The v0.2.0 gaming model adds ``gaming.integration`` (auto|conservative|gamemode)
while retaining the public ``enabled``/``users`` keys. Status reports a coverage
tier and an optional provider sample; unattended installs may not request the
unadmitted ``auto`` (FULL) tier without explicit operator action.
"""

import json
import pwd
import tempfile
from pathlib import Path
from typing import Literal

import pytest

from sparehash import status
from sparehash.config import Config, GamingIntegration, load
from sparehash.sysdetect import Capabilities
from sparehash.thermal import SensorPath
from sparehash.wizard import WizardError, load_answers

WALLET = "4" + "A" * 94
ACCOUNT = pwd.getpwuid(0).pw_name
SENSOR = SensorPath(Path("/fixture/temp1_input"), "coretemp", "Package id 0")
CAPS = Capabilities("x86_64", True, 255, True, "cgroup2", True, True, False,
                    (0,), (), 8, 8 * 1024**3, None, None, True)

Health = Literal["OK", "STALE", "DISCONNECTED", "MALFORMED", "OVERFLOW"]


# --- task-16 documentation-honesty tests (converted from unittest) ---

def test_gaming_contract_documents_admission() -> None:
    """Gaming contract must document NO_DEPENDABLE_ROUTE admission."""
    contract_path = Path("docs/gaming-integration-contract.md")
    assert contract_path.exists(), "gaming-integration-contract.md must exist"
    content = contract_path.read_text()
    assert "NO_DEPENDABLE_ROUTE" in content
    assert "FULL gaming coverage" in content
    assert "NOT been demonstrated" in content
    assert "conservative fallback" in content.lower()
    assert "conservative session inhibition" in content.lower()
    assert "FULL gaming safety" not in content
    assert "full support" not in content.lower()


def test_gaming_contract_documents_latency_objectives() -> None:
    """Gaming contract must frame latency as objectives, not guarantees."""
    content = Path("docs/gaming-integration-contract.md").read_text()
    assert "1 second" in content
    assert "5 second" in content
    assert "engineering objectives" in content.lower()
    assert "not hard real-time" in content.lower()
    assert "not zero game-start overlap" in content.lower()


def test_gaming_contract_documents_manual_pause_independence() -> None:
    """Gaming contract must document manual pause independence."""
    content = Path("docs/gaming-integration-contract.md").read_text()
    assert "manual pause" in content.lower()
    assert "independent" in content.lower()
    assert "cannot bypass" in content.lower()


def test_gaming_contract_documents_fallback_only_status() -> None:
    """Gaming contract must document fallback-only NO-GO status."""
    content = Path("docs/gaming-integration-contract.md").read_text()
    assert "NO-GO" in content
    assert "reduced-function" in content.lower()
    assert "pending owner decision" in content.lower()


def test_gaming_contract_documents_unknowns_and_limitations() -> None:
    """Gaming contract must document unknowns and limitations."""
    content = Path("docs/gaming-integration-contract.md").read_text()
    assert "session bus requirement" in content.lower()
    assert "lingering teardown" in content.lower()
    assert "monitor loss" in content.lower()
    assert "uncovered workloads" in content.lower()


def test_readme_documents_admission_status() -> None:
    """README must document gaming admission status."""
    content = Path("README.md").read_text()
    assert "conservative fallback" in content.lower()
    assert "NOT been demonstrated" in content
    assert "NO_DEPENDABLE_ROUTE" in content
    assert "docs/gaming-integration-contract.md" in content


def test_security_md_documents_gaming_limitations() -> None:
    """SECURITY.md must document gaming coverage limitations."""
    content = Path("SECURITY.md").read_text()
    assert "conservative fallback" in content.lower()
    assert "NO_DEPENDABLE_ROUTE" in content
    assert "NOT been demonstrated" in content
    assert "docs/gaming-integration-contract.md" in content


def test_architecture_documents_gaming_module() -> None:
    """Architecture doc must document gaming module and admission."""
    content = Path("docs/architecture.md").read_text()
    assert "sparehash/gaming.py" in content
    assert "gaming observation" in content.lower()
    assert "NO_DEPENDABLE_ROUTE" in content
    assert "conservative session inhibition" in content.lower()
    assert "1 second" in content
    assert "5 second" in content


# --- task-17 coverage-tier tests ---

def test_accepts_explicit_coverage_policy() -> None:
    cfg = Config.from_dict({"gaming": {"enabled": True, "integration": "conservative"}})
    snapshot = status.StatusSnapshot(coverage_tier=status.coverage_tier(cfg.gaming.integration))
    result = json.loads(status.format_status(snapshot, True))
    assert cfg.gaming.integration is GamingIntegration.CONSERVATIVE
    assert result["coverage_tier"] == "CONSERVATIVE"


def test_refuses_silent_downgrade() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "answers.toml"
        _ = path.write_text(
            f'wallet = "{WALLET}"\n'
            + '[pool]\nhost = "pool.example"\n'
            + f'[gaming]\nenabled = true\nusers = ["{ACCOUNT}"]\nintegration = "auto"\n',
            encoding="utf-8",
        )
        with pytest.raises(WizardError) as caught:
            _ = load_answers(path, CAPS, sensor_probe=lambda: SENSOR, unattended=True)
        assert caught.value.reason == "no_full_gaming_contract_admitted"


def test_v011_migration_pending_acknowledgment() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.toml"
        _ = path.write_text('[gaming]\nenabled = false\nintegration = "conservative"\n', encoding="utf-8")
        cfg = load(path)
        assert cfg.gaming.migration_pending_acknowledgment is True
    assert not Config.from_dict({"gaming": {"enabled": True, "integration": "conservative"}}).gaming.migration_pending_acknowledgment
    assert not Config.from_dict({"gaming": {"enabled": False, "integration": "auto"}}).gaming.migration_pending_acknowledgment


@pytest.mark.parametrize("health,epoch", [
    ("OK", 0), ("STALE", 1), ("DISCONNECTED", 2), ("MALFORMED", 3), ("OVERFLOW", 4),
])
def test_status_reports_health_and_epoch(health: Health, epoch: int) -> None:
    coverage = status.GamingCoverage(
        provider="logind", uid=1000, epoch=epoch, observed_at=12.5,
        state="ACTIVE", health=health, producer_version="v1.2.3",
    )
    result = json.loads(status.format_status(status.StatusSnapshot(coverage=coverage), True))
    assert result["coverage"]["health"] == health
    assert result["coverage"]["epoch"] == epoch
