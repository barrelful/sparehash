"""System/peripheral battery inventory regressions; synthetic sysfs only."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from sparehash.controller import Controller
from sparehash.controller_io import Action, Completion, Observation, Ports
from sparehash.ipc import Command, Verb
from sparehash.lifecycle_lease import LeaseStore
from sparehash.power import Evidence, PowerMonitor, PowerSample, blocked, read_sysfs
from sparehash.state import BlockerState
from tests.controller_fixtures import ROOT, FakePorts
from tests.test_power import FakeBackend


def _supply(root: Path, name: str, fields: dict[str, str]) -> Path:
    entry = root / "class/power_supply" / name
    entry.mkdir(parents=True, exist_ok=True)
    for field, value in fields.items():
        (entry / field).write_text(value)
    return entry


def _device(root: Path) -> Path:
    # Exact reported inventory: no present property, no usable status or mains.
    return _supply(root, "hidpp_battery_0", {
        "type": "Battery\n", "scope": "Device\n", "status": "Unknown\n", "online": "0\n",
    })


def _system(root: Path, *, status: str = "Charging", scope: str | None = "System\n") -> Path:
    fields = {"type": "Battery", "present": "1", "status": status, "power_now": "200000"}
    if scope is not None:
        fields["scope"] = scope
    return _supply(root, "BAT0", fields)


def _sample(root: Path, now: float = 0) -> PowerSample:
    inventory, sysfs, ac, rate = read_sysfs(root, now)
    return PowerSample(now, inventory, Evidence(False, now), sysfs, ac, rate)


def test_device_only_reported_inventory_has_no_system_battery(tmp_path: Path) -> None:
    _device(tmp_path)
    sample = _sample(tmp_path)
    assert sample.battery_present is False
    assert sample.sysfs is None  # Do not invent system discharge or online AC evidence.
    assert sample.ac_online is None
    assert sample.discharge_rate_w is None
    assert not sample.blocked


@pytest.mark.parametrize("status", ["Discharging", "Charging"])
@pytest.mark.parametrize("scope", [None, "System", "System\n"])
def test_device_does_not_mask_or_pollute_system_battery(
    tmp_path: Path, status: str, scope: str | None,
) -> None:
    device = _device(tmp_path)
    # Even a known discharging/high-draw peripheral is not system discharge.
    (device / "present").write_text("1")
    (device / "status").write_text("Discharging")
    (device / "power_now").write_text("9000000")
    _system(tmp_path, status=status, scope=scope)
    _supply(tmp_path, "AC", {"type": "Mains", "online": "1"})
    sample = _sample(tmp_path)
    assert sample.battery_present is True
    assert sample.sysfs is (status == "Discharging")
    assert sample.blocked is (status == "Discharging")
    assert sample.ac_online is True
    assert sample.discharge_rate_w == 0.2  # Peripheral rate is excluded.
    assert replace(sample, upower_evidence=Evidence(True, 0)).blocked


@pytest.mark.parametrize("status", ["Charging", "Full", "Not charging"])
def test_device_does_not_supply_mains_for_system_battery(tmp_path: Path, status: str) -> None:
    _device(tmp_path)
    _system(tmp_path, status=status)
    sample = _sample(tmp_path)
    assert sample.battery_present is True
    assert sample.sysfs is True
    assert sample.ac_online is None
    assert sample.blocked


@pytest.mark.parametrize("scope", [
    "Unknown", "", "device", "Device\x00", "Device\nSystem\n", "Device ",
    " System", "System\r\n", "Device\r\n",
])
@pytest.mark.parametrize("present", ["0", "1"])
def test_ambiguous_scope_cannot_prove_system_battery_absence(
    tmp_path: Path, scope: str, present: str,
) -> None:
    battery = _system(tmp_path, scope=scope)
    (battery / "present").write_text(present)
    _supply(tmp_path, "AC", {"type": "Mains", "online": "1"})
    sample = _sample(tmp_path)
    assert sample.battery_present is None
    assert sample.blocked
    assert blocked(sample, maintenance_mode=True)


@pytest.mark.parametrize("fault", ["unreadable", "non-ascii", "directory", "overlong"])
def test_scope_read_failure_is_not_a_legacy_missing_attribute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    scope = _system(tmp_path) / "scope"
    _supply(tmp_path, "AC", {"type": "Mains", "online": "1"})
    if fault == "unreadable":
        original = Path.open

        def fail(target, *args, **kwargs):
            if target == scope:
                raise PermissionError("synthetic scope read failure")
            return original(target, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fail)
    elif fault == "non-ascii":
        scope.write_bytes(b"\xff")
    elif fault == "directory":
        scope.unlink()
        scope.mkdir()
    else:
        scope.write_text("Device\n" + "x" * 100)
    sample = _sample(tmp_path)
    assert sample.battery_present is None
    assert sample.blocked


@pytest.mark.parametrize("scope", [None, "System"])
@pytest.mark.parametrize("missing", [("present",), ("status",), ("present", "status")])
def test_missing_system_battery_properties_still_inhibit(
    tmp_path: Path, scope: str | None, missing: tuple[str, ...],
) -> None:
    _device(tmp_path)
    battery = _system(tmp_path, scope=scope)
    for field in missing:
        (battery / field).unlink()
    _supply(tmp_path, "AC", {"type": "Mains", "online": "1"})
    sample = _sample(tmp_path)
    assert sample.battery_present is not False
    assert sample.sysfs is None
    assert sample.blocked
    assert blocked(sample, maintenance_mode=True)


@pytest.mark.parametrize("kind", [None, "", "Unknown", "unrecognized-supply"])
def test_unknown_type_cannot_prove_absence_even_with_device_scope(
    tmp_path: Path, kind: str | None,
) -> None:
    fields = {"scope": "Device"}
    if kind is not None:
        fields["type"] = kind
    _supply(tmp_path, "unknown", fields)
    assert _sample(tmp_path).battery_present is None
    assert _sample(tmp_path).blocked


def test_empty_complete_inventory_is_distinct_from_missing_directory(tmp_path: Path) -> None:
    (tmp_path / "class/power_supply").mkdir(parents=True)
    assert _sample(tmp_path).battery_present is False
    assert not _sample(tmp_path).blocked
    missing = _sample(tmp_path / "unavailable")
    assert missing.battery_present is None and missing.blocked


@pytest.mark.parametrize("partial", [True, False])
def test_failed_directory_enumeration_never_proves_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, partial: bool,
) -> None:
    device = _device(tmp_path)
    directory = device.parent
    original = Path.iterdir

    def entries():
        if partial:
            yield device
        raise PermissionError("synthetic enumeration failure")

    monkeypatch.setattr(Path, "iterdir", lambda path: entries() if path == directory else original(path))
    sample = _sample(tmp_path)
    assert sample.battery_present is None and sample.sysfs is None
    assert sample.ac_online is None and sample.discharge_rate_w is None
    assert sample.blocked


def test_device_only_plus_unreadable_entry_does_not_clear(tmp_path: Path) -> None:
    _device(tmp_path)
    _supply(tmp_path, "unreadable", {})
    assert _sample(tmp_path).battery_present is None
    assert _sample(tmp_path).blocked


def test_device_only_inventory_still_expires(tmp_path: Path) -> None:
    _device(tmp_path)
    backend = FakeBackend()
    now = 0.0
    monitor = PowerMonitor(tmp_path, backend, lambda: now)
    assert monitor.tick().battery_present is False
    now = 10.01
    stale = monitor.snapshot()
    assert stale.battery_present is None and stale.blocked
    assert monitor.tick().battery_present is False


@pytest.mark.parametrize("manual", [True, False])
def test_scope_recovery_preserves_controller_dwell_and_manual_hold(tmp_path: Path, manual: bool) -> None:
    device = _device(tmp_path)
    io = FakePorts()
    backend = FakeBackend()
    monitor = PowerMonitor(tmp_path, backend, io.clock)
    state = BlockerState(tmp_path / "state.json", io.clock, 60)
    store = Mock(spec=LeaseStore)
    store.load.return_value = None
    owner = Controller(state, Ports(io, io, io, io.clock), lease_store=store)

    def observe() -> PowerSample:
        for _ in range(3):
            sample = monitor.tick()
        owner.safety("battery", sample.blocked)
        owner.refresh()
        observation = Observation(owner.sample, io.now, sample.battery_present,
                                  sample.upower, sample.sysfs, (), False,
                                  sample.ac_online, sample.discharge_rate_w)
        assert dict(observation.blockers())["battery"] == sample.blocked
        owner.observe(observation)
        return sample

    def finish() -> None:
        assert owner.pending is not None
        owner.complete(Completion(owner.pending, True, True))

    with patch("sparehash.controller.is_retired", return_value=False):
        owner.start()
    assert observe().battery_present is False
    finish()  # Empty STOP proof, then the initial allowed START.
    assert owner.pending is not None and owner.pending.action is Action.START
    finish()
    assert owner.running

    io.now = 5
    (device / "scope").write_text("Unknown")
    assert observe().blocked
    assert owner.pending is not None and owner.pending.action is Action.STOP
    assert not owner.authorize()
    finish()
    assert "battery" in state.active()
    if manual:
        assert owner.handle(Command(Verb.PAUSE), ROOT)["ok"] is True

    io.now = 10
    (device / "scope").write_text("Device\n")
    assert observe().battery_present is False
    assert "battery" not in state.active()
    finish()  # Recovery still requires the existing empty proof.
    assert owner.pending is None
    assert not owner.authorize()

    io.now = 64.9
    observe()
    assert owner.pending is None
    io.now = 65
    observe()
    if manual:
        assert "manual" in state.active()
        assert owner.pending is None and not owner.authorize()
    else:
        assert owner.pending is not None and owner.pending.action is Action.START
        assert owner.authorize()
