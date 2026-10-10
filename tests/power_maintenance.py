"""Opt-in power policy regressions using synthetic sysfs, never host hardware."""

import tempfile
import unittest
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from sparehash import config, wizard
from sparehash.controller_io import Observation, SampleId
from sparehash.power import Evidence, PowerSample, blocked, read_sysfs
from sparehash.runtime import Providers
from tests.install_fixture import capabilities


class MaintenanceTests(unittest.TestCase):
    def __init__(self, method_name: str = "runTest") -> None:
        super().__init__(method_name)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.battery = self.root / "class/power_supply/BAT0"
        self.ac = self.root / "class/power_supply/AC"
        self.battery.mkdir(parents=True)
        self.ac.mkdir()
        for field, value in (("type", "Battery"), ("present", "1"), ("status", "Discharging"),
                             ("capacity", "98"), ("power_now", "179000")):
            _ = (self.battery / field).write_text(value)
        _ = (self.ac / "type").write_text("Mains")
        _ = (self.ac / "online").write_text("1")

    def sample(self) -> PowerSample:
        inventory, sysfs, ac, rate = read_sysfs(self.root, 0)
        return PowerSample(0, inventory, Evidence(False, 0), sysfs, ac, rate)

    def test_asus_allowed_only_when_maintenance_mode_enabled(self) -> None:
        # Given
        sample = self.sample()
        # When
        decisions = (sample.blocked, blocked(sample, maintenance_mode=True))
        # Then
        self.assertEqual(sample.discharge_rate_w, 0.179)
        self.assertIs(sample.ac_online, True)
        self.assertEqual(decisions, (True, False))

    def test_blocks_when_ac_unplugged_despite_tiny_rate(self) -> None:
        # Given
        _ = (self.ac / "online").write_text("0")
        sample = self.sample()
        # When
        decisions = (blocked(sample), blocked(sample, maintenance_mode=True))
        # Then
        self.assertEqual(decisions, (True, True))

    def test_blocks_when_discharge_exceeds_one_watt(self) -> None:
        # Given
        _ = (self.battery / "power_now").write_text("1000001")
        sample = self.sample()
        # When
        decision = blocked(sample, maintenance_mode=True)
        # Then
        self.assertTrue(decision)

    def test_allows_when_discharge_is_exactly_one_watt(self) -> None:
        # Given
        _ = (self.battery / "power_now").write_text("1000000")
        sample = self.sample()
        # When
        decision = blocked(sample, maintenance_mode=True)
        # Then
        self.assertFalse(decision)

    def test_blocks_when_upower_reports_battery_despite_online_ac(self) -> None:
        # Given
        sample = replace(self.sample(), upower_evidence=Evidence(True, 0))
        # When
        decision = blocked(sample, maintenance_mode=True)
        # Then
        self.assertTrue(decision)

    def test_blocks_when_any_required_evidence_stale(self) -> None:
        sample = self.sample()
        for field in ("inventory", "upower_evidence", "sysfs_evidence"):
            # Given
            stale = replace(sample, **{field: Evidence(getattr(sample, field).value, -10.01)})
            # When
            decision = blocked(stale, maintenance_mode=True)
            # Then
            self.assertTrue(decision)

    def test_blocks_when_rate_missing_or_invalid(self) -> None:
        for value in (None, "invalid", "nan"):
            # Given
            if value is None:
                (self.battery / "power_now").unlink()
            else:
                _ = (self.battery / "power_now").write_text(value)
            sample = self.sample()
            # When
            decision = blocked(sample, maintenance_mode=True)
            # Then
            self.assertIsNone(sample.discharge_rate_w)
            self.assertTrue(decision)

    def test_blocks_when_ac_unreadable_or_absent(self) -> None:
        for online in (None, "unreadable"):
            # Given
            if online is None:
                (self.ac / "online").unlink()
            else:
                _ = (self.ac / "online").write_text(online)
            sample = self.sample()
            # When
            decision = blocked(sample, maintenance_mode=True)
            # Then
            self.assertIsNone(sample.ac_online)
            self.assertTrue(decision)

    def test_absolute_rate_when_current_and_voltage_fallback(self) -> None:
        # Given
        (self.battery / "power_now").unlink()
        _ = (self.battery / "current_now").write_text("-17900")
        _ = (self.battery / "voltage_now").write_text("10000000")
        # When
        sample = self.sample()
        # Then
        self.assertEqual(sample.discharge_rate_w, 0.179)
        self.assertFalse(blocked(sample, maintenance_mode=True))

    def test_total_rate_blocks_when_multiple_batteries_exceed_threshold(self) -> None:
        # Given
        other = self.battery.parent / "BAT1"
        other.mkdir()
        for field, value in (("type", "Battery"), ("present", "1"), ("status", "Discharging"), ("power_now", "900000")):
            _ = (other / field).write_text(value)
        sample = self.sample()
        # When
        decision = blocked(sample, maintenance_mode=True)
        # Then
        self.assertTrue(decision)

    def test_observation_matches_policy_when_opted_in(self) -> None:
        # Given
        sample = self.sample()
        observation = Observation(SampleId(1), sample.at, sample.battery_present, sample.upower,
                                  sample.sysfs, (), False, sample.ac_online, sample.discharge_rate_w)
        # When
        decisions = (dict(observation.blockers())["battery"],
                     dict(replace(observation, maintenance_mode=True).blockers())["battery"])
        # Then
        self.assertEqual(decisions, (True, False))

    def test_roundtrip_and_answers_when_power_policy_opted_in(self) -> None:
        # Given
        cfg = config.Config(wallet="4" + "1" * 94, pool=config.Pool(host="127.0.0.1"),
                            power=config.Power(True), gaming=config.Gaming(False))
        path = self.root / "answers.toml"
        config.save(cfg, path)
        # When
        parsed = wizard.load_answers(path, capabilities(), sensor_probe=lambda: None)
        # Then
        self.assertEqual(parsed, cfg)
        self.assertTrue(config.Config.from_dict(parsed.to_dict()).power.maintenance_mode)
        self.assertFalse(config.Config().power.maintenance_mode)

    def test_rejects_when_maintenance_mode_has_wrong_type(self) -> None:
        # Given
        path = self.root / "answers.toml"
        _ = path.write_text('wallet = "' + "4" + "1" * 94 + '"\n[pool]\nhost = "127.0.0.1"\n[power]\nmaintenance_mode = 1\n')
        # When / Then
        with self.assertRaises(config.ConfigError):
            _ = config.load(path)
        with self.assertRaises(wizard.WizardError):
            _ = wizard.load_answers(path, capabilities(), sensor_probe=lambda: None)

    def test_provider_passes_policy_and_diagnostics_when_collecting(self) -> None:
        # Given
        evidence = read_sysfs(self.root, 0)
        future: Future[bool | None] = Future()
        future.set_result(False)
        monitor = Mock()
        monitor.backend.query.return_value = future
        observations: list[Observation] = []
        cfg = config.Config(power=config.Power(True), gaming=config.Gaming(False))
        with patch("sparehash.runtime.power.PowerMonitor", return_value=monitor), patch(
            "sparehash.runtime.thermal.find_sensor", return_value=None
        ), patch("sparehash.runtime.power.read_sysfs", return_value=evidence), patch(
            "sparehash.runtime.time.monotonic", return_value=0
        ):
            provider = Providers(cfg, lambda action: action())
            provider.finish = observations.append
            try:
                # When
                provider.collect(SampleId(1), 1)
            finally:
                provider.close()
        # Then
        self.assertEqual(len(observations), 1)
        self.assertIs(observations[0].ac_online, True)
        self.assertEqual(observations[0].discharge_rate_w, 0.179)
        self.assertIn(("battery", False), observations[0].blockers())
