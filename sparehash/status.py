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

import unittest
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


def test_accepts_explicit_coverage_policy() -> None:
    # Given an explicit conservative coverage policy, status reports the fallback tier.
    cfg = Config.from_dict({"gaming": {"enabled": True, "integration": "conservative"}})
    snapshot = status.StatusSnapshot(coverage_tier=status.coverage_tier(cfg.gaming.integration))
    # When
    result = json.loads(status.format_status(snapshot, True))
    # Then
    assert cfg.gaming.integration is GamingIntegration.CONSERVATIVE
    assert result["coverage_tier"] == "CONSERVATIVE"


def test_refuses_silent_downgrade() -> None:
    # Given an unattended answers file requesting auto (FULL) coverage on a host with
    # no admitted producer, the wizard must refuse rather than silently downgrade.
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "answers.toml"
        _ = path.write_text(
            f'wallet = "{WALLET}"\n'
            + '[pool]\nhost = "pool.example"\n'
            + f'[gaming]\nenabled = true\nusers = ["{ACCOUNT}"]\nintegration = "auto"\n',
            encoding="utf-8",
        )
        # When / Then
        with pytest.raises(WizardError) as caught:
            _ = load_answers(path, CAPS, sensor_probe=lambda: SENSOR, unattended=True)
        assert caught.value.reason == "no_full_gaming_contract_admitted"


def test_v011_migration_pending_acknowledgment() -> None:
    # Given a v0.1.1 gaming section (originally enabled=True) that migrated to the
    # reduced conservative fallback with the guard disabled, the operator must
    # acknowledge the downgrade before unattended Bazzite mining.
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.toml"
        _ = path.write_text('[gaming]\nenabled = false\nintegration = "conservative"\n', encoding="utf-8")
        # When
        cfg = load(path)
        # Then
        assert cfg.gaming.migration_pending_acknowledgment is True
    # An enabled or auto coverage request is not a silent downgrade.
    assert not Config.from_dict({"gaming": {"enabled": True, "integration": "conservative"}}).gaming.migration_pending_acknowledgment
    assert not Config.from_dict({"gaming": {"enabled": False, "integration": "auto"}}).gaming.migration_pending_acknowledgment


@pytest.mark.parametrize("health,epoch", [
    ("OK", 0), ("STALE", 1), ("DISCONNECTED", 2), ("MALFORMED", 3), ("OVERFLOW", 4),
])
def test_status_reports_health_and_epoch(health: Health, epoch: int) -> None:
    # Given a provider coverage sample, status preserves its health and epoch.
    coverage = status.GamingCoverage(
        provider="logind", uid=1000, epoch=epoch, observed_at=12.5,
        state="ACTIVE", health=health, producer_version="v1.2.3",
    )
    # When
    result = json.loads(status.format_status(status.StatusSnapshot(coverage=coverage), True))
    # Then
    assert result["coverage"]["health"] == health
    assert result["coverage"]["epoch"] == epoch


class TestGamingContractDocumentation(unittest.TestCase):
    """Verify gaming coverage admission is documented honestly (task-16 contract)."""

    def test_refuses_silent_downgrade(self):
        """Gaming contract must document NO_DEPENDABLE_ROUTE admission."""
        contract_path = Path("docs/gaming-integration-contract.md")
        self.assertTrue(contract_path.exists(), "gaming-integration-contract.md must exist")

        content = contract_path.read_text()

        # Must document the admission decision
        self.assertIn("NO_DEPENDABLE_ROUTE", content)
        self.assertIn("FULL gaming coverage", content)
        self.assertIn("NOT been demonstrated", content)
