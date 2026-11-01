"""Single-detector schema, actionable migration and honest gaming documentation."""

from pathlib import Path
from unittest.mock import patch

import pytest

from sparehash import config, status
from sparehash.wizard import WizardError, load_answers
from tests.test_wizard import CAPS, SENSOR, WALLET


@pytest.mark.parametrize("enabled", (True, False))
def test_deprecated_defaults_are_readable_but_never_written(tmp_path: Path, enabled: bool) -> None:
    text = f'[gaming]\nenabled = {str(enabled).lower()}\nintegration = "auto"\nsteam_log = false\n'
    path = tmp_path / "config.toml"
    path.write_text(text)
    with pytest.warns(UserWarning, match="deprecated") as seen:
        cfg = config.load(path)
    assert len(seen) == 2
    assert cfg.gaming == config.Gaming(enabled=enabled)
    assert cfg.to_dict()["gaming"] == {"enabled": enabled, "users": []}
    config.save(cfg, path, warn_overwrite=False)
    assert "integration" not in path.read_text() and "steam_log" not in path.read_text()
    assert config.load(path) == cfg


@pytest.mark.parametrize("legacy", [
    'integration = "conservative"', 'integration = "gamemode"',
    'integration = "other"', 'integration = true',
    'steam_log = true', 'steam_log = "false"', 'steam_log = 0',
])
@pytest.mark.parametrize("enabled", (True, False))
def test_retired_policy_is_not_silently_replaced(legacy: str, enabled: bool, tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(f'[gaming]\nenabled = {str(enabled).lower()}\n{legacy}\n')
    with pytest.raises(config.ConfigError, match="retired.*best-effort.*Remove"):
        config.load(path)


@pytest.mark.parametrize("unattended", (True, False))
def test_answers_accept_best_effort_without_accounts_or_steam(tmp_path: Path, unattended: bool) -> None:
    path = tmp_path / "answers.toml"
    path.write_text(f'wallet = "{WALLET}"\n[pool]\nhost = "pool.example"\n')
    cfg = load_answers(path, CAPS, sensor_probe=lambda: SENSOR, unattended=unattended)
    assert cfg.gaming == config.Gaming()
    path.write_text(path.read_text() + '[gaming]\nintegration = "auto"\nsteam_log = false\n')
    with pytest.warns(UserWarning, match="deprecated"):
        assert load_answers(path, CAPS, sensor_probe=lambda: SENSOR, unattended=unattended) == cfg


@pytest.mark.parametrize("legacy", ['integration = "conservative"', 'integration = "gamemode"', 'steam_log = true'])
def test_answers_refuse_retired_policy_with_migration_guidance(tmp_path: Path, legacy: str) -> None:
    path = tmp_path / "answers.toml"
    path.write_text(f'wallet = "{WALLET}"\n[pool]\nhost = "pool.example"\n[gaming]\n{legacy}\n')
    with pytest.raises(WizardError, match="retired.*best-effort.*Remove"):
        load_answers(path, CAPS, unattended=True)


def test_disconnected_status_does_not_claim_a_successful_scan() -> None:
    snapshot = status.StatusSnapshot()
    assert snapshot.coverage_tier == "UNVERIFIED"
    assert snapshot.gaming_detection is None


@pytest.mark.parametrize("enabled", (True, False))
def test_public_status_separates_disabled_and_best_effort(enabled: bool) -> None:
    from sparehash.power import Evidence
    from tests.test_status import PowerHelper

    raw = {"ok": True, "ready": True, "gaming_detection": {"detector": "steam-reaper", "enabled": enabled}}
    with patch("sparehash.status.read_sysfs", return_value=(Evidence(False, 0), Evidence(False, 0), None, None)), patch(
        "sparehash.status.Helpers", PowerHelper,
    ), patch("sparehash.status.find_sensor", return_value=None), patch("sparehash.status.load", return_value=config.Config()):
        result = status.LiveProvider(controller_reader=lambda: raw, resolver=lambda _: True).snapshot()
    assert result.coverage_tier == ("BEST_EFFORT" if enabled else "DISABLED")


def test_gaming_contract_is_best_effort_and_preserves_strict_protections() -> None:
    text = Path("docs/gaming-integration-contract.md").read_text().lower()
    for phrase in ("best-effort", "steam reaper", "non-steam", "manual pause", "independent",
                   "1 second", "5 second", "engineering objectives", "not hard real-time",
                   "not zero game-start overlap", "cannot bypass", "migration"):
        assert phrase in text
