"""Offline unit deployment contracts; no real manager commands."""

from __future__ import annotations

import configparser
import shutil
import socket
import subprocess
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from dataclasses import replace
from functools import partial
from pathlib import Path
from unittest.mock import patch

from sparehash import systemd
from sparehash.sysdetect import FIXED_PATH, Capabilities


def capabilities() -> Capabilities:
    return Capabilities("x86_64", True, 252, True, "cgroup2", True,
                        True, False, (0,), (), 4, 4096, None, None, True)


def parsed(text: str) -> configparser.ConfigParser:
    result = configparser.ConfigParser(interpolation=None)
    result.read_string(text)
    return result


class UnitTests(unittest.TestCase):
    def test_matrix_when_capabilities_differ(self) -> None:
        # Given
        for hierarchy in ("cgroup1", "cgroup2", None):
            for version in (247, 252, None):
                for idle in (False, True):
                    for credential in (False, True):
                        with self.subTest(hierarchy=hierarchy, version=version, idle=idle, credential=credential):
                            caps = replace(capabilities(), cgroup_hierarchy=hierarchy,
                                           systemd_version=version, cpuweight_idle=idle,
                                           load_credential=credential)
                            # When
                            units = systemd.render(caps)
                            service = parsed(units["sparehash-xmrig.service"])["Service"]
                            # Then
                            weights = hierarchy == "cgroup2" and idle
                            self.assertEqual(service.get("CPUWeight"), "idle" if weights else None)
                            self.assertEqual(service.get("IOWeight"), "1" if weights else None)
                            self.assertEqual(service.get("LoadCredential"),
                                             "xmrig.json:/etc/sparehash/xmrig.json" if credential and version == 252 else None)

    def test_controller_when_rendered(self) -> None:
        # Given / When
        unit = parsed(systemd.render(capabilities())["sparehash.service"])
        # Then
        self.assertEqual(dict(unit["Unit"]), {k.lower(): v for k, v in {
            "Description": "SpareHash controller", "StartLimitIntervalSec": "10min", "StartLimitBurst": "10"}.items()})
        self.assertEqual(dict(unit["Service"]), {k.lower(): v for k, v in {
            "Type": "notify", "NotifyAccess": "main", "TimeoutStartSec": "30s", "User": "root", "Group": "root",
            "ExecStart": "/usr/local/bin/sparehash __controller__",
            "ExecStopPost": "/usr/local/bin/sparehash __cleanup__", "Restart": "on-failure", "RestartSec": "5s",
            "StateDirectory": "sparehash", "RuntimeDirectory": "sparehash sparehash-miner",
            "RuntimeDirectoryMode": "0750", "Environment": "PYTHONDONTWRITEBYTECODE=1",
            "NoNewPrivileges": "yes", "ProtectSystem": "strict",
            "ProtectHome": "read-only", "PrivateTmp": "yes", "RestrictAddressFamilies": "AF_UNIX AF_INET AF_INET6"}.items()})
        self.assertEqual(dict(unit["Install"]), {"wantedby": "multi-user.target"})

    def test_miner_when_rendered(self) -> None:
        # Given / When
        unit = parsed(systemd.render(capabilities())["sparehash-xmrig.service"])
        # Then
        self.assertEqual(dict(unit["Unit"]), {k.lower(): v for k, v in {
            "Description": "SpareHash unprivileged XMRig", "BindsTo": "sparehash.service",
            "After": "sparehash.service network-online.target", "Wants": "network-online.target",
            "StartLimitIntervalSec": "10min", "StartLimitBurst": "5"}.items()})
        self.assertEqual(dict(unit["Service"]), {k.lower(): v for k, v in {
            "Type": "simple", "User": "xmrig", "Group": "xmrig",
            "ExecCondition": "+/usr/local/bin/sparehash __authorize_miner__",
            "ExecStart": "/usr/local/bin/sparehash __miner_exec__", "Restart": "on-failure", "RestartSec": "30s",
            "Nice": "19", "CPUSchedulingPolicy": "idle", "IOSchedulingClass": "idle", "CPUWeight": "idle",
            "IOWeight": "1", "LoadCredential": "xmrig.json:/etc/sparehash/xmrig.json", "OOMScoreAdjust": "500",
            "Environment": "PYTHONDONTWRITEBYTECODE=1", "NoNewPrivileges": "yes", "ProtectSystem": "strict",
            "ProtectHome": "yes", "PrivateTmp": "yes", "PrivateDevices": "yes",
            "RestrictAddressFamilies": "AF_UNIX AF_INET AF_INET6", "RestrictNamespaces": "yes",
            "LockPersonality": "yes", "KillMode": "control-group", "TimeoutStopSec": "3s",
            "SendSIGKILL": "yes"}.items()})
        self.assertEqual(unit.sections(), ["Unit", "Service"])

    def test_foreign_units_when_marker_is_missing_or_not_first(self) -> None:
        # Given
        for name in (*systemd.UNIT_NAMES, "xmrig.service"):
            for content in ("[Service]\n", "\n# Managed by SpareHash\n", "# Managed by SpareHash forged\n"):
                with self.subTest(name=name, content=content), tempfile.TemporaryDirectory() as tmp:
                    directory = Path(tmp)
                    _ = (directory / name).write_text(content)
                    # When / Then
                    with self.assertRaises(systemd.CollisionError):
                        systemd.install_units(systemd.render(capabilities()), directory)

    def test_symlink_when_collision_checked(self) -> None:
        # Given
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "xmrig.service").symlink_to(directory / "absent")
            # When / Then
            with self.assertRaises(systemd.CollisionError):
                systemd.check_collisions(directory)

    def test_install_when_destination_is_explicit(self) -> None:
        # Given
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            units = systemd.render(capabilities())
            _ = (directory / "xmrig.service").write_text(systemd.MARKER + "\n[Service]\n")
            # When
            systemd.install_units(units, directory)
            # Then
            for name, text in units.items():
                self.assertEqual((directory / name).read_text(), text)
                self.assertEqual((directory / name).stat().st_mode & 0o777, 0o644)

    def test_verification_when_analyzer_rejects(self) -> None:
        # Given
        runner = ProbeRunner("reject")
        # When / Then
        with self.assertRaises(systemd.UnitError):
            _ = systemd.verify_units(Path("/fixture"), runner)

    def test_notification_when_ready(self) -> None:
        # Given
        with tempfile.TemporaryDirectory() as tmp, socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
            address = str(Path(tmp) / "notify")
            receiver.bind(address)
            receiver.settimeout(1)
            # When
            systemd.notify_ready(address)
            # Then
            self.assertEqual(receiver.recv(64), b"READY=1")

    def test_readonly_when_render_and_summary_called(self) -> None:
        # Given
        with patch.object(systemd, "probe_cpuweight_idle", side_effect=AssertionError("Unexpected probe")) as probe:
            caps = capabilities()
            # When
            _ = systemd.render(caps)
            _ = caps.summary()
            # Then
            probe.assert_not_called()


class ProbeRunner:
    """Recording manager boundary with selectable failures, not a real systemd."""

    outcome: str

    def __init__(self, outcome: str = "success") -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: Sequence[str], *, timeout: float,
                 env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(tuple(argv))
        if timeout != 10 or env != {"PATH": FIXED_PATH, "LANG": "C"}:
            raise AssertionError("unbounded or inherited environment")
        if self.outcome == "timeout" and "stop" not in argv:
            raise subprocess.TimeoutExpired(argv, timeout)
        code = 1 if self.outcome == "reject" or (self.outcome == "cleanup" and "stop" in argv) else 0
        output = "Result=success\nExecMainCode=1\nExecMainStatus=0\nControlGroup=/system.slice/probe\nCPUWeight=0\nActiveState=active\n"
        if self.outcome == "garbage":
            output = "invalid"
        if self.outcome == "traversal":
            output = output.replace("/system.slice/probe", "/../outside")
        return subprocess.CompletedProcess(argv, code, output, "rejected" if code else "")


class ProbeTests(unittest.TestCase):
    def test_callback_when_installer_passes_to_detect(self) -> None:
        # Given
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cgroup = root / "sys/fs/cgroup"
            cgroup.mkdir(parents=True)
            _ = (cgroup / "cpu.idle").write_text("0")
            effective = cgroup / "system.slice/probe"
            effective.mkdir(parents=True)
            _ = (effective / "cpu.idle").write_text("1")
            proc = root / "proc/1"
            proc.mkdir(parents=True)
            _ = (proc / "mountinfo").write_text("1 0 0:1 / /sys/fs/cgroup rw - cgroup2 cgroup rw")
            runner = ProbeRunner()
            callback = partial(systemd.probe_cpuweight_idle, runner=runner,
                               context=systemd.ProbeContext(0, cgroup, True))
            # When
            caps = Capabilities.detect(root=root, proc_root=proc.parent, euid=0,
                                       runner=lambda *args, **kwargs: subprocess.CompletedProcess([], 1, "", ""),
                                       manager_probe=callback)
            # Then
            self.assertTrue(caps.cpuweight_idle)

    def test_probe_when_manager_and_effective_kernel_accept(self) -> None:
        # Given
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _ = (root / "cpu.idle").write_text("0")
            group = root / "system.slice/probe"
            group.mkdir(parents=True)
            _ = (group / "cpu.idle").write_text("1")
            runner = ProbeRunner()
            # When
            result = systemd.probe_cpuweight_idle({}, runner, systemd.ProbeContext(0, root, True))
            # Then
            self.assertTrue(result)
            self.assertIn("/usr/bin/true", runner.calls[0])
            self.assertIn("--property=CPUWeight=idle", runner.calls[0])
            self.assertIn("--property=CollectMode=inactive-or-failed", runner.calls[0])
            unit = runner.calls[0][1].removeprefix("--unit=")
            self.assertEqual(runner.calls[-1], ("/usr/bin/systemctl", "stop", unit))

    def test_probe_when_uncertain_always_cleans(self) -> None:
        # Given
        for outcome in ("reject", "timeout", "garbage", "traversal", "cleanup", "success"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _ = (root / "cpu.idle").write_text("0")
                runner = ProbeRunner(outcome)
                # When
                result = systemd.probe_cpuweight_idle({}, runner, systemd.ProbeContext(0, root, True))
                # Then
                self.assertFalse(result)
                self.assertEqual(runner.calls[-1][1], "stop")

    def test_probe_when_preconditions_missing_never_runs(self) -> None:
        # Given
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for uid, v2 in ((1000, True), (0, False), (0, True)):
                runner = ProbeRunner()
                # When
                result = systemd.probe_cpuweight_idle({}, runner, systemd.ProbeContext(uid, root, v2))
                # Then
                self.assertFalse(result)
                self.assertEqual(runner.calls, [])


class AnalyzerTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze unavailable")
    def test_rendered_units_when_verified_in_fixture_root(self) -> None:
        # Given
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / "etc/systemd/system"
            directory.mkdir(parents=True)
            binary = root / "usr/local/bin/sparehash"
            binary.parent.mkdir(parents=True)
            _ = binary.write_bytes(b"Analyzer-only executable-presence fixture; never executed\n")
            binary.chmod(0o755)
            for target in ("sysinit", "basic", "shutdown", "network-online", "multi-user"):
                _ = (directory / f"{target}.target").write_text("[Unit]\nDefaultDependencies=no\n")
            systemd.install_units(systemd.render(capabilities()), directory)
            # When
            result = subprocess.run(("/usr/bin/systemd-analyze", "verify", f"--root={root}",
                                     *(str(directory / name) for name in systemd.UNIT_NAMES)),
                                    capture_output=True, text=True, timeout=10, check=False)
            # Then
            self.assertEqual(result.returncode, 0, result.stderr)
