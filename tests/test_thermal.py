"""Deterministic fixture hwmon discovery, parsing and hysteresis tests."""

from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import final
import unittest
from unittest.mock import patch

from sparehash import thermal


@final
class ThermalTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def hwmon(self, driver: str, labels: tuple[str, ...] = ()) -> Path:
        path = self.root / f"hwmon{len(tuple(self.root.iterdir())) + 7}"
        path.mkdir()
        _ = (path / "name").write_text(driver + "\n", encoding="ascii")
        for index, label in enumerate(labels, 1):
            _ = (path / f"temp{index}_label").write_text(label + "\n", encoding="ascii")
            _ = (path / f"temp{index}_input").write_text("45000\n", encoding="ascii")
        return path

    def sensor(self, raw: str = "45000\n") -> thermal.SensorPath:
        path = self.hwmon("coretemp") / "temp1_input"
        _ = path.write_text(raw, encoding="ascii")
        return thermal.SensorPath(path, "coretemp")

    def test_package_is_preferred_when_core_is_hotter(self) -> None:
        # Given
        path = self.hwmon("coretemp", ("Core 0", "Package", "Package id 0"))
        _ = (path / "temp1_input").write_text("99000\n", encoding="ascii")
        # When
        result = thermal.find_sensor(self.root)
        # Then
        self.assertEqual(result, thermal.SensorPath(path / "temp3_input", "coretemp", "Package id 0"))

    def test_tctl_is_preferred_when_tdie_is_also_present(self) -> None:
        # Given
        path = self.hwmon("k10temp", ("Tdie", "Tctl"))
        # When
        result = thermal.find_sensor(self.root)
        # Then
        self.assertEqual(result, thermal.SensorPath(path / "temp2_input", "k10temp", "Tctl"))

    def test_fallback_is_temp1_when_labels_are_absent_or_unknown(self) -> None:
        for labels in ((), ("GPU", "Core 0")):
            with self.subTest(labels=labels):
                # Given
                path = self.hwmon("zenpower", labels)
                _ = (path / "temp1_input").write_text("50000\n", encoding="ascii")
                # When
                result = thermal.find_sensor(self.root)
                # Then
                self.assertEqual(result, thermal.SensorPath(path / "temp1_input", "zenpower"))
                (path / "name").unlink()

    def test_no_sensor_when_driver_is_foreign_missing_or_unreadable(self) -> None:
        for driver in ("acpitz", "amdgpu", "nvme", "CORETEMP", ""):
            # Given
            _ = self.hwmon(driver, ("Package id 0",))
        missing = self.hwmon("coretemp", ("Package",))
        (missing / "name").unlink()
        unreadable = self.hwmon("k10temp", ("Tctl",))
        (unreadable / "name").unlink()
        (unreadable / "name").mkdir()
        # When
        result = thermal.find_sensor(self.root)
        # Then
        self.assertIsNone(result)

    def test_no_arbitrary_input_when_temp1_and_preferred_labels_are_missing(self) -> None:
        # Given
        path = self.hwmon("coretemp")
        _ = (path / "temp2_input").write_text("99000", encoding="ascii")
        # When
        result = thermal.find_sensor(self.root)
        # Then
        self.assertIsNone(result)

    def test_override_when_no_root_argument(self) -> None:
        # Given
        sensor = self.sensor()
        with patch.dict("os.environ", {"SPAREHASH_HWMON_ROOT": str(self.root)}):
            # When
            result = thermal.find_sensor()
        # Then
        self.assertEqual(result, sensor)

    def test_descriptor_is_immutable_when_assignment_attempted(self) -> None:
        # Given
        sensor = self.sensor()
        # When / Then
        with self.assertRaises(FrozenInstanceError):
            setattr(sensor, "driver", "amdgpu")

    def test_exact_celsius_when_integer_millidegrees(self) -> None:
        for raw, expected in (("45123\n", 45.123), ("85000", 85.0), ("-1000\n", -1.0), ("0", 0.0)):
            with self.subTest(raw=raw):
                # Given
                sensor = self.sensor(raw)
                # When
                result = thermal.read_celsius(sensor)
                # Then
                self.assertEqual(result, expected)

    def test_error_when_malformed_or_out_of_range(self) -> None:
        for raw in ("", "85.0", "85_000", "+85000", " 85000", "85000\n\n", "nan",
                    "85000 C", "200001", "-273151", "9" * 40, "0" * 40, "85000\r\n"):
            with self.subTest(raw=raw):
                # Given
                sensor = self.sensor(raw)
                # When / Then
                with self.assertRaises(thermal.SensorReadError):
                    _ = thermal.read_celsius(sensor)

    def test_unavailable_when_missing_or_disabled(self) -> None:
        for enabled, sensor in ((True, None), (False, self.sensor("99000"))):
            with self.subTest(enabled=enabled):
                # Given
                guard = thermal.ThermalGuard(enabled=enabled, sensor=sensor)
                # When
                result = guard.poll()
                # Then
                if enabled and sensor is None:
                    # Enabled + missing sensor → fail-safe block (active=True, result.available=True)
                    # Guard.available is False because sensor is None, but the result is TRIPPED
                    self.assertEqual(result.status, thermal.ThermalStatus.TRIPPED)
                    self.assertTrue(result.available)
                    self.assertTrue(result.active)
                    self.assertFalse(guard.available)  # guard.available requires sensor is not None
                else:
                    # Disabled or valid sensor → unavailable/inactive
                    self.assertEqual(result.status, thermal.ThermalStatus.UNAVAILABLE)
                    self.assertFalse(result.available)
                    self.assertFalse(result.active)
                    self.assertFalse(guard.available)

    def test_unavailable_is_logged_once_when_polled_repeatedly(self) -> None:
        # Given: enabled guard with no credible CPU sensor
        guard = thermal.ThermalGuard(enabled=True)
        # When
        with self.assertLogs(thermal.LOGGER, level="INFO") as logs:
            results = [guard.poll() for _ in range(3)]
        # Then: warning logged once, and guard actively blocks mining (fail-safe)
        self.assertEqual(len(logs.records), 1)
        self.assertTrue(all(result.active for result in results))

    def test_trip_when_pause_boundary_reached(self) -> None:
        for raw, active in (("84999", False), ("85000", True), ("86000", True)):
            with self.subTest(raw=raw):
                # Given
                guard = thermal.ThermalGuard(enabled=True, sensor=self.sensor(raw))
                # When
                result = guard.poll()
                # Then
                self.assertEqual(result.active, active)
                self.assertTrue(result.available)

    def test_hysteresis_when_previously_tripped(self) -> None:
        for raw, active in (("80000", True), ("75001", True), ("75000", False), ("74000", False)):
            with self.subTest(raw=raw):
                # Given
                sensor = self.sensor("86000")
                guard = thermal.ThermalGuard(enabled=True, sensor=sensor)
                _ = guard.poll()
                _ = sensor.path.write_text(raw, encoding="ascii")
                # When
                result = guard.poll()
                # Then
                self.assertEqual(result.active, active)

    def test_custom_threshold_when_config_differs_from_defaults(self) -> None:
        # Given
        guard = thermal.ThermalGuard(True, 65, 55, self.sensor("65000"))
        # When
        result = guard.poll()
        # Then
        self.assertTrue(result.active)

    def test_fail_safe_and_one_warning_when_input_unreadable(self) -> None:
        # Given: a directory at the selected input fails deterministically even as root.
        sensor = self.sensor()
        guard = thermal.ThermalGuard(enabled=True, sensor=sensor)
        sensor.path.unlink()
        sensor.path.mkdir()
        # When
        with self.assertLogs(thermal.LOGGER, level="WARNING") as logs:
            results = [guard.poll() for _ in range(3)]
        # Then
        self.assertTrue(all(result.active and result.available and result.celsius is None for result in results))
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(logs.records[0].levelname, "WARNING")

    def test_recovery_obeys_resume_threshold_when_read_failure_clears(self) -> None:
        for raw, active in (("80000", True), ("75000", False)):
            with self.subTest(raw=raw):
                # Given
                sensor = self.sensor("malformed")
                guard = thermal.ThermalGuard(enabled=True, sensor=sensor)
                with self.assertLogs(thermal.LOGGER, level="WARNING"):
                    _ = guard.poll()
                _ = sensor.path.write_text(raw, encoding="ascii")
                # When
                result = guard.poll()
                # Then
                self.assertEqual(result.active, active)

    def test_regression_production_seam_no_sensor_block_mining(self) -> None:
        # Regression: enabled thermal guard with no credible sensor must block mining (fail-safe).
        # Goes through the production seam: find_sensor → None → ThermalGuard.poll().active must be True.
        # Uses self.tmp from setUp for temp dir
        root = Path(self.tmp.name)
        sensor = thermal.find_sensor(root)  # → None (empty hwmon dir)
        guard = thermal.ThermalGuard(enabled=True, pause_c=85, resume_c=75, sensor=sensor)
        result = guard.poll()
        # Fail-safe: enabled+missing → active block
        self.assertTrue(result.active, "enabled+missing sensor must block mining (fail-safe)")
        self.assertTrue(result.available)
        self.assertEqual(result.status, thermal.ThermalStatus.TRIPPED)

if __name__ == "__main__":
    _ = unittest.main()
