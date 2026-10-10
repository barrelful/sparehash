"""Deterministic power fixtures; no real power events or hardware mutations."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from threading import Event
from typing import final
from unittest.mock import patch

from sparehash.controller_io import Observation, SampleId
from sparehash.power import Evidence, Helpers, PowerMonitor, PowerSample, read_sysfs
from tests.power_maintenance import MaintenanceTests as MaintenanceTests


@final
class FakeBackend:
    def __init__(self) -> None:
        self.live = False
        self.changed = False
        self.value: bool | None = False
        self.calls = self.starts = 0
        self.hanging = False
        self.futures: list[Future[bool | None]] = []
        self.closed = False

    def start(self) -> bool:
        self.starts += 1
        self.live = True
        return True

    def alive(self) -> bool:
        return self.live

    def events(self) -> bool:
        changed, self.changed = self.changed, False
        return changed

    def query(self) -> Future[bool | None]:
        self.calls += 1
        future: Future[bool | None] = Future()
        if not self.hanging:
            future.set_result(self.value)
        self.futures.append(future)
        return future

    def close(self) -> None:
        self.closed = True


@final
class PowerTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "class/power_supply").mkdir(parents=True)
        self.now = 0.0
        self.backend = FakeBackend()
        self.monitor = PowerMonitor(self.root, self.backend, lambda: self.now)

    def supply(self, name: str, fields: tuple[tuple[str, str], ...]) -> Path:
        entry = self.root / "class/power_supply" / name
        entry.mkdir(exist_ok=True)
        for key, value in fields:
            _ = (entry / key).write_text(value)
        return entry

    def battery(self, status: str = "Charging") -> Path:
        return self.supply("BAT0", (("type", "Battery"), ("present", "1"), ("status", status)))

    def ac(self, online: str = "1") -> None:
        _ = self.supply("AC", (("type", "Mains"), ("online", online)))

    def settled(self) -> PowerSample:
        _ = self.monitor.tick()
        _ = self.monitor.tick()
        return self.monitor.tick()

    def test_inhibits_when_sources_disagree_in_either_direction(self) -> None:
        # Given
        for upower, status in ((True, "Charging"), (False, "Discharging")):
            with self.subTest(upower=upower):
                _ = self.battery(status)
                self.ac()
                self.backend.value = upower
                self.backend.changed = True
                # When
                sample = self.settled()
                observation = Observation(SampleId(1), sample.at, sample.battery_present,
                                          sample.upower, sample.sysfs, (), False)
                # Then
                self.assertTrue(sample.blocked)
                self.assertIn(("battery", True), observation.blockers())

    def test_allows_when_inventory_confirms_empty_or_ups_only(self) -> None:
        # Given
        self.backend.value = True
        for ups in (False, True):
            with self.subTest(ups=ups):
                if ups:
                    _ = self.supply("UPS", (("type", "UPS"), ("online", "1")))
                self.backend.changed = True
                # When
                sample = self.settled()
                # Then
                self.assertIs(sample.battery_present, False)
                self.assertFalse(sample.blocked)

    def test_unknown_inhibits_when_inventory_is_missing_or_partial(self) -> None:
        # Given
        roots = (self.root / "missing", self.root)
        _ = self.supply("unreadable", ())
        self.ac()
        for root in roots:
            with self.subTest(root=root):
                monitor = PowerMonitor(root, self.backend, lambda: self.now)
                # When
                sample = monitor.tick()
                # Then
                self.assertIsNone(sample.battery_present)
                self.assertTrue(sample.blocked)

    def test_partial_inventory_with_battery_cannot_clear_when_upower_false(self) -> None:
        # Given
        _ = self.battery()
        self.ac()
        _ = self.supply("unknown", ())
        # When
        sample = self.settled()
        # Then
        self.assertIsNone(sample.battery_present)
        self.assertTrue(sample.blocked)

    def test_blocks_when_charging_or_full_without_mains_even_with_online_ups(self) -> None:
        # Given
        _ = self.supply("UPS", (("type", "UPS"), ("online", "1")))
        for status in ("Charging", "Full", "Not charging"):
            _ = self.battery(status)
            # When
            inventory, evidence, _, _ = read_sysfs(self.root, 1)
            # Then
            self.assertIs(inventory.value, True)
            self.assertIs(evidence.value, True)

    def test_any_discharging_battery_blocks_when_mains_online(self) -> None:
        # Given
        _ = self.battery("Full")
        self.ac()
        _ = self.supply("BAT1", (("type", "Battery"), ("present", "1"), ("status", "Discharging")))
        # When
        sample = self.settled()
        # Then
        self.assertIs(sample.sysfs, True)

    def test_absent_battery_allows_when_inventory_conclusive(self) -> None:
        # Given
        _ = self.supply("BAT0", (("type", "Battery"), ("present", "0")))
        # When
        sample = self.settled()
        # Then
        self.assertIs(sample.battery_present, False)
        self.assertFalse(sample.blocked)

    def test_recognized_external_types_provide_false_when_online(self) -> None:
        # Given
        _ = self.battery("Full")
        for kind in ("Mains", "USB", "USB_C", "USB_PD"):
            _ = self.supply("AC", (("type", kind), ("online", "1")))
            # When
            _, evidence, _, _ = read_sysfs(self.root, 0)
            # Then
            self.assertIs(evidence.value, False)

    def test_unknown_when_online_or_presence_unreadable(self) -> None:
        # Given
        _ = self.supply("BAT0", (("type", "Battery"), ("status", "Full")))
        _ = self.supply("AC", (("type", "Mains"),))
        # When
        sample = self.settled()
        # Then
        self.assertIsNone(sample.battery_present)
        self.assertIsNone(sample.sysfs)
        self.assertTrue(sample.blocked)

    def test_stale_false_is_unknown_when_age_exceeds_ten_seconds(self) -> None:
        # Given
        _ = self.battery()
        self.ac()
        _ = self.settled()
        self.now = 10.01
        # When
        sample = self.monitor.snapshot()
        # Then
        self.assertIsNone(sample.upower)
        self.assertTrue(sample.blocked)
        self.assertIs(Evidence(False, 0).credible(10), False)

    def test_death_invalidates_false_before_restart_and_rereads_current_state(self) -> None:
        # Given
        _ = self.battery()
        self.ac()
        _ = self.settled()
        self.backend.live = False
        self.backend.value = True
        self.now = 1
        # When
        dead = self.monitor.snapshot()
        restarted = self.settled()
        # Then
        self.assertIsNone(dead.upower)
        self.assertIs(restarted.upower, True)
        self.assertTrue(restarted.blocked)
        self.assertEqual(self.backend.starts, 2)

    def test_signal_rereads_both_sources_instead_of_trusting_payload(self) -> None:
        # Given
        entry = self.battery()
        self.ac()
        _ = self.settled()
        _ = (entry / "status").write_text("Discharging")
        self.backend.changed = True
        # When
        sample = self.settled()
        # Then
        self.assertIs(sample.sysfs, True)
        self.assertTrue(sample.blocked)
        self.assertEqual(self.backend.calls, 2)

    def test_sysfs_samples_at_five_seconds_while_query_hangs(self) -> None:
        # Given
        entry = self.battery()
        self.ac()
        self.backend.hanging = True
        _ = self.monitor.tick()
        _ = (entry / "status").write_text("Discharging")
        self.now = 5
        # When
        sample = self.monitor.tick()
        # Then
        self.assertIs(sample.sysfs, True)
        self.assertEqual(sample.sysfs_evidence.at, 5)
        self.assertEqual(self.backend.calls, 1)

    def test_old_query_cannot_clear_when_event_invalidates_generation(self) -> None:
        # Given
        _ = self.battery()
        self.backend.hanging = True
        _ = self.monitor.tick()
        self.backend.changed = True
        self.backend.futures[0].set_result(False)
        # When
        sample = self.monitor.tick()
        # Then
        self.assertIsNone(sample.upower)
        self.assertTrue(sample.blocked)

    def test_run_closes_when_callback_requests_stop(self) -> None:
        # Given
        stop = Event()
        samples: list[PowerSample] = []
        def emit(sample: PowerSample) -> None:
            samples.append(sample)
            stop.set()
        # When
        self.monitor.run(stop, emit)
        # Then
        self.assertEqual(len(samples), 1)
        self.assertTrue(self.backend.closed)

    def test_helper_fallback_and_parse_errors_when_gdbus_unavailable(self) -> None:
        # Given
        helper = Helpers()
        self.addCleanup(helper.close)
        for text, expected in (("b true", True), ("b false", False), ("b garbage", None)):
            with self.subTest(text=text), patch.object(helper, "_call", side_effect=("", "", text)):
                # When
                result = helper.query().result(timeout=1)
                # Then
                self.assertIs(result, expected)

    def test_helper_getall_and_get_parse_current_boolean(self) -> None:
        # Given
        helper = Helpers()
        self.addCleanup(helper.close)
        for responses, expected in ((("({'OnBattery': <true>},)",), True), (("bad", "(<false>,)"), False)):
            with patch.object(helper, "_call", side_effect=responses):
                # When
                result = helper.query().result(timeout=1)
                # Then
                self.assertIs(result, expected)

    def test_unknown_inhibits_when_both_sources_are_unreadable(self) -> None:
        # Given
        _ = self.supply("BAT0", (("type", "Battery"), ("present", "1")))
        self.ac("unreadable")
        self.backend.value = None
        # When
        sample = self.settled()
        # Then
        self.assertIsNone(sample.upower)
        self.assertIsNone(sample.sysfs)
        self.assertTrue(sample.blocked)

    def test_replug_discovers_battery_before_clear_when_previous_inventory_empty(self) -> None:
        # Given
        _ = self.settled()
        _ = self.battery("Discharging")
        self.backend.changed = True
        # When
        sample = self.settled()
        # Then
        self.assertIs(sample.battery_present, True)
        self.assertTrue(sample.blocked)

    def test_helpers_return_unknown_when_all_queries_timeout(self) -> None:
        # Given
        helper = Helpers()
        self.addCleanup(helper.close)
        with patch("sparehash.power.shutil.which", return_value="/usr/bin/gdbus"), patch(
                "sparehash.power.subprocess.run", side_effect=subprocess.TimeoutExpired("fixture", 2)):
            # When
            result = helper.query().result(timeout=1)
            # Then
            self.assertIsNone(result)

    def test_emits_when_only_discharge_rate_changes(self) -> None:
        # Given
        battery = self.battery("Discharging")
        _ = (battery / "power_now").write_text("179000")
        self.ac()
        stop = Event()
        samples: list[PowerSample] = []
        def emit(sample: PowerSample) -> None:
            samples.append(sample)
            if sample.upower is False and sample.discharge_rate_w == 0.179:
                _ = (battery / "power_now").write_text("2000000")
                self.now = 5
            if sample.discharge_rate_w == 2.0 or len(samples) > 3:
                stop.set()
        # When
        self.monitor.run(stop, emit)
        # Then
        self.assertEqual(samples[-1].discharge_rate_w, 2.0)
        self.assertEqual(samples[-1].sysfs, samples[-2].sysfs)
        self.assertEqual(samples[-1].ac_online, samples[-2].ac_online)
