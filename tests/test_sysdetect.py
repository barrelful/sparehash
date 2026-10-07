"""Contract tests for bounded capability detection."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

from sparehash.sysdetect import Capabilities, TIMEOUT_SECONDS


class FakeRunner:
    def __init__(self, responses: dict[str, str | BaseException] | None = None) -> None:
        self.responses: dict[str, str | BaseException] = responses or {}
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        name = Path(argv[0]).name
        key = " ".join(argv[1:])
        response = self.responses.get(f"{name} {key}", self.responses.get(name, ""))
        if isinstance(response, BaseException):
            raise response
        return subprocess.CompletedProcess(argv, 0, response, "")


class SysdetectTests(unittest.TestCase):
    def fixture(self) -> tempfile.TemporaryDirectory[str]:
        return tempfile.TemporaryDirectory()

    def test_architecture_and_python_gate(self) -> None:
        with self.fixture() as tmp:
            result = Capabilities.detect(root=tmp, proc_root=Path(tmp) / "proc", environ={},
                                         runner=FakeRunner(), architecture="aarch64", python_version=(3, 12))
        self.assertFalse(result.python_supported)
        self.assertEqual(result.architecture, "aarch64")

    def test_systemd_version_and_loadcredential(self) -> None:
        runner = FakeRunner({"systemctl --version": "systemd 250 (250.1)",
                             "systemctl show --property=LoadCredential": "LoadCredential="})
        with self.fixture() as tmp:
            result = Capabilities.detect(root=tmp, proc_root=Path(tmp) / "proc", environ={}, runner=runner)
        self.assertEqual(result.systemd_version, 250)
        self.assertTrue(result.load_credential)
        self.assertTrue(all(call[1]["timeout"] == TIMEOUT_SECONDS for call in runner.calls))
        self.assertTrue(all("shell" not in call[1] or call[1]["shell"] is False for call in runner.calls))

    def test_degraded_systemd_still_identifies_pid1(self) -> None:
        with self.fixture() as tmp:
            proc = Path(tmp) / "proc/1"
            proc.mkdir(parents=True)
            _ = (proc / "comm").write_text("systemd\n")
            result = Capabilities.detect(root=tmp, proc_root=Path(tmp) / "proc", environ={}, runner=FakeRunner())
        self.assertTrue(result.systemd_pid1)

    def test_timeout_and_malformed_outputs_are_unavailable(self) -> None:
        runner = FakeRunner({"systemctl --version": subprocess.TimeoutExpired("systemctl", 10),
                             "nproc": "many"})
        with self.fixture() as tmp:
            result = Capabilities.detect(root=tmp, proc_root=Path(tmp) / "proc", environ={}, runner=runner)
        self.assertIsNone(result.systemd_version)
        self.assertIsNone(result.nproc)
        self.assertFalse(result.cpuweight_idle)

    def test_ups_only_is_not_battery_and_missing_inventory_unknown(self) -> None:
        with self.fixture() as tmp:
            sysfs = Path(tmp) / "sys"
            supply = sysfs / "class/power_supply/UPS"
            supply.mkdir(parents=True)
            _ = (supply / "type").write_text("Mains\n")
            result = Capabilities.detect(root=tmp, proc_root=Path(tmp) / "proc", environ={}, runner=FakeRunner())
            missing = Capabilities.detect(root=tmp, proc_root=Path(tmp) / "proc", environ={"SPAREHASH_SYSFS_ROOT": str(Path(tmp) / "absent")}, runner=FakeRunner())
        self.assertFalse(result.battery_present)
        self.assertIsNone(missing.battery_present)

    def test_empty_readable_power_inventory_has_no_battery(self) -> None:
        with self.fixture() as tmp:
            (Path(tmp) / "sys/class/power_supply").mkdir(parents=True)
            result = Capabilities.detect(root=tmp, proc_root=Path(tmp) / "proc", environ={}, runner=FakeRunner())
        self.assertFalse(result.battery_present)

    def test_battery_type_detected(self) -> None:
        with self.fixture() as tmp:
            supply = Path(tmp) / "sys/class/power_supply/BAT0"
            supply.mkdir(parents=True)
            _ = (supply / "type").write_text("Battery\n")
            result = Capabilities.detect(root=tmp, proc_root=Path(tmp) / "proc", environ={}, runner=FakeRunner())
        self.assertTrue(result.battery_present)

    def test_partial_power_inventory_is_unknown(self) -> None:
        with self.fixture() as tmp:
            supplies = Path(tmp) / "sys/class/power_supply"
            for name in ("UPS", "Unreadable"):
                (supplies / name).mkdir(parents=True)
            _ = (supplies / "UPS/type").write_text("Mains\n")
            result = Capabilities.detect(root=tmp, proc_root=Path(tmp) / "proc", environ={}, runner=FakeRunner())
        self.assertIsNone(result.battery_present)

    def test_cgroup2_requires_cpu_idle_and_probe(self) -> None:
        with self.fixture() as tmp:
            root = Path(tmp)
            proc = root / "proc/1"
            proc.mkdir(parents=True)
            _ = (proc / "mountinfo").write_text("1 0 0:1 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n")
            _ = (proc / "cgroup").write_text("0::/\n")
            no_idle = Capabilities.detect(root=root, proc_root=root / "proc", environ={}, runner=FakeRunner(), euid=0)
            (root / "sys/fs/cgroup").mkdir(parents=True)
            _ = (root / "sys/fs/cgroup/cpu.idle").write_text("0\n")
            runner = FakeRunner()
            yes_idle_no_probe = Capabilities.detect(root=root, proc_root=root / "proc", environ={}, runner=runner, euid=0)
        self.assertEqual(no_idle.cgroup_hierarchy, "cgroup2")
        self.assertFalse(no_idle.cpuweight_idle)
        self.assertFalse(yes_idle_no_probe.cpuweight_idle)

    def test_cpuweight_requires_explicit_positive_probe(self) -> None:
        with self.fixture() as tmp:
            root = Path(tmp)
            proc = root / "proc/1"
            proc.mkdir(parents=True)
            _ = (proc / "mountinfo").write_text("1 0 0:1 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n")
            cpu_idle = root / "sys/fs/cgroup/cpu.idle"
            cpu_idle.parent.mkdir(parents=True)
            _ = cpu_idle.write_text("0\n")
            def supported(env: Mapping[str, str]) -> bool:
                return bool(env == {})
            result = Capabilities.detect(root=root, proc_root=root / "proc", environ={}, runner=FakeRunner(), euid=0,
                                         manager_probe=supported)
        self.assertTrue(result.cpuweight_idle)

    def test_cpuweight_false_probe_stays_unavailable(self) -> None:
        with self.fixture() as tmp:
            root = Path(tmp)
            proc = root / "proc/1"
            proc.mkdir(parents=True)
            _ = (proc / "mountinfo").write_text("1 0 0:1 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n")
            cpu_idle = root / "sys/fs/cgroup/cpu.idle"
            cpu_idle.parent.mkdir(parents=True)
            _ = cpu_idle.write_text("0\n")
            def unsupported(env: Mapping[str, str]) -> bool:
                return bool(env.get("manager-enabled", False))
            result = Capabilities.detect(root=root, proc_root=root / "proc", environ={}, runner=FakeRunner(), euid=0,
                                         manager_probe=unsupported)
        self.assertFalse(result.cpuweight_idle)

    def test_cpu_idle_obeys_sysfs_override(self) -> None:
        with self.fixture() as tmp:
            root = Path(tmp)
            proc = root / "proc/1"
            proc.mkdir(parents=True)
            _ = (proc / "mountinfo").write_text("1 0 0:1 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n")
            custom = root / "custom/fs/cgroup/cpu.idle"
            custom.parent.mkdir(parents=True)
            _ = custom.write_text("0\n")
            def supported(env: Mapping[str, str]) -> bool:
                return bool(env.get("SPAREHASH_SYSFS_ROOT"))
            result = Capabilities.detect(root=root, proc_root=root / "proc", environ={"SPAREHASH_SYSFS_ROOT": str(root / "custom")},
                                         runner=FakeRunner(), euid=0, manager_probe=supported)
        self.assertTrue(result.cpuweight_idle)

    def test_numa_online_memory_nodes(self) -> None:
        with self.fixture() as tmp:
            node_dir = Path(tmp) / "sys/devices/system/node"
            for index in (0, 1):
                node = node_dir / f"node{index}"
                node.mkdir(parents=True)
                _ = (node / "meminfo").write_text(f"Node {index} MemTotal: 1024 kB\n")
            result = Capabilities.detect(root=tmp, proc_root=Path(tmp) / "proc", environ={}, runner=FakeRunner())
        self.assertEqual(result.numa_nodes, (0, 1))

    def test_existing_xmrig_path_and_summary(self) -> None:
        with self.fixture() as tmp:
            binary = Path(tmp) / "opt/xmrig/current/xmrig"
            binary.parent.mkdir(parents=True)
            _ = binary.touch()
            result = Capabilities.detect(root=tmp, proc_root=Path(tmp) / "proc", environ={}, runner=FakeRunner())
        self.assertEqual(result.xmrig_path, str(binary))
        self.assertEqual(result.summary()["xmrig_path"], str(binary))

    def test_empty_numa_inventory_is_unknown_and_helpers_are_listed(self) -> None:
        with self.fixture() as tmp:
            nodes = Path(tmp) / "sys/devices/system/node"
            nodes.mkdir(parents=True)
            result = Capabilities.detect(root=tmp, proc_root=Path(tmp) / "proc", environ={}, runner=FakeRunner())
        self.assertIsNone(result.numa_nodes)
        self.assertTrue({"gdbus", "busctl", "gamemoded", "gamemoderun", "steam", "gpg", "ss",
                         "runuser", "stress-ng"}.issubset(dict(result.helper_paths)))

    def test_malformed_or_zero_numa_memory_is_unknown(self) -> None:
        with self.fixture() as tmp:
            node_dir = Path(tmp) / "sys/devices/system/node"
            for index, contents in ((0, ""), (1, "Node 1 MemTotal: 0 kB\n"), (2, "unparseable\n")):
                node = node_dir / f"node{index}"
                node.mkdir(parents=True)
                _ = (node / "meminfo").write_text(contents)
            result = Capabilities.detect(root=tmp, proc_root=Path(tmp) / "proc", environ={}, runner=FakeRunner())
        self.assertIsNone(result.numa_nodes)

    def test_offline_numa_node_is_ignored(self) -> None:
        with self.fixture() as tmp:
            node_dir = Path(tmp) / "sys/devices/system/node"
            for index, online in ((0, "1"), (1, "0")):
                node = node_dir / f"node{index}"
                node.mkdir(parents=True)
                _ = (node / "meminfo").write_text(f"Node {index} MemTotal: 1024 kB\n")
                _ = (node / "online").write_text(online)
            result = Capabilities.detect(root=tmp, proc_root=Path(tmp) / "proc", environ={}, runner=FakeRunner())
        self.assertEqual(result.numa_nodes, (0,))

    def test_busctl_upower_property_parses_boolean(self) -> None:
        runner = FakeRunner({"busctl --system get-property org.freedesktop.UPower /org/freedesktop/UPower org.freedesktop.UPower OnBattery": "b true"})
        with self.fixture() as tmp:
            result = Capabilities.detect(root=tmp, proc_root=Path(tmp) / "proc", environ={}, runner=runner)
        self.assertTrue(result.upower_available)
        self.assertTrue(any("OnBattery" in " ".join(call[0]) for call in runner.calls))


if __name__ == "__main__":
    _ = unittest.main()
