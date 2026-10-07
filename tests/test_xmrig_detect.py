"""Contract tests for XMRig detection without executing candidates."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from typing import Final

from sparehash import xmrig_detect as detect
from tests.test_xmrig_current import XmrigCurrentTests as XmrigCurrentTests


class FakeRunner:
    def __init__(self, owned: bool = False) -> None:
        self.owned: Final = owned
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], *, check: bool = False, capture_output: bool = True,
                 text: bool = True, timeout: int = 3, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        _ = (check, capture_output, text, timeout, env)
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0 if self.owned else 1, "xmrig package", "")


class XmrigDetectTests(unittest.TestCase):
    def test_present_unsafe_candidates_remain_visible_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            malicious = root / "xmrig"
            marker = root / "executed"
            source = f"#!/bin/sh\ntouch {marker}\n".encode()
            _ = malicious.write_bytes(source)
            _ = malicious.chmod(0o755)
            link = root / "link"
            _ = link.symlink_to(malicious)
            fifo = root / "fifo"
            os.mkfifo(fifo)
            observed: list[tuple[Path, int, tuple[str, ...]]] = []

            def probe(snapshot: Path, *, timeout: int, uid: int,
                      required_properties: tuple[str, ...]) -> str | None:
                _ = timeout
                observed.append((snapshot, uid, required_properties))
                return "XMRig 6.21.2"

            with mock.patch.object(detect, "candidate_paths", return_value=(malicious, link, fifo)):
                absent_probe = detect.detect_installations(runner=FakeRunner())
                probed = detect.detect_installations(probe=probe, runner=FakeRunner())
            self.assertFalse(marker.exists())
            self.assertEqual(malicious.read_bytes(), source)
            self.assertEqual(len(absent_probe), 3)
            self.assertTrue(all(item.version is None and not item.verified for item in absent_probe))
            self.assertEqual([item.path for item in probed], [malicious, link, fifo])
            self.assertEqual(probed[0].version, (6, 21, 2))
            self.assertTrue(all(item.writable_source for item in probed[1:]))
            self.assertEqual(detect.classify(probed, consent=True).recommendation,
                             detect.Recommendation.PROTECTED_COPY_CONSENT)
            self.assertEqual(len(observed), 1)
            self.assertEqual(observed[0][1], 65534)
            self.assertEqual(len(observed[0][2]), 5)
            self.assertNotEqual(observed[0][0], malicious)

    def test_writable_candidate_is_not_recommended_for_adoption(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            candidate = Path(temp) / "xmrig"
            _ = candidate.write_bytes(b"candidate bytes")
            _ = candidate.chmod(0o777)

            def probe(snapshot: Path, *, timeout: int, uid: int,
                      required_properties: tuple[str, ...]) -> str:
                _ = (snapshot, timeout, uid, required_properties)
                return "XMRig 6.21.2"

            with mock.patch.object(detect, "candidate_paths", return_value=(candidate,)):
                result = detect.detect_installations(probe=probe, runner=FakeRunner())
            classification = detect.classify(result, consent=True)
            self.assertTrue(result[0].writable_source)
            self.assertEqual(classification.install, result[0])
            self.assertEqual(classification.recommendation, detect.Recommendation.PROTECTED_COPY_CONSENT)

    def test_parse_version_accepts_banner_and_rejects_garbage(self) -> None:
        self.assertEqual(detect.parse_version("XMRig 6.21.2"), (6, 21, 2))
        self.assertEqual(detect.parse_version("XMRig version 6.26.0"), (6, 26, 0))
        self.assertIsNone(detect.parse_version("unknown"))

    def test_version_policy_marks_fortify_floor(self) -> None:
        self.assertFalse(detect.is_supported((6, 21, 1)))
        self.assertTrue(detect.is_supported((6, 21, 2)))
        old = detect.Install(Path("/usr/bin/xmrig"), (6, 21, 1), detect.ManagedBy.USER, True, False,
                             detect.FORTIFY_WARNING)
        self.assertEqual(detect.classify([old]).recommendation, detect.Recommendation.INSTALL_UPSTREAM)
        self.assertIn("RandomX crash", detect.classify([old]).reason)

    def test_candidate_dedup_fixed_paths(self) -> None:
        paths = detect.candidate_paths(path_value="/usr/bin:/bin")
        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual(paths[0], Path("/usr/bin/xmrig"))

    def test_package_owned_candidate_is_distro_managed(self) -> None:
        runner = FakeRunner(owned=True)
        result = detect.package_owned(Path("/usr/bin/xmrig"), runner=detect.metadata_adapter(runner))
        self.assertTrue(result)
        self.assertTrue(all(argv[0].startswith("/") for argv in runner.calls))

    def test_exact_protected_marker_and_manifest_agreement_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            xmrig_root = root / "opt/xmrig"
            binary = xmrig_root / "6.26.0/xmrig"
            binary.parent.mkdir(parents=True)
            _ = binary.touch()
            marker = xmrig_root / ".sparehash-managed"
            manifest = root / "var/lib/sparehash/install-manifest.json"
            manifest.parent.mkdir(parents=True)
            _ = marker.write_text(str(xmrig_root))
            _ = manifest.write_text(str(binary))
            _ = marker.chmod(0o600)
            _ = manifest.chmod(0o600)
            with mock.patch("sparehash.xmrig_detect._package_owned", return_value=False):
                self.assertEqual(detect.ownership(binary, runner=detect.metadata_adapter(FakeRunner()),
                                                  marker=marker, manifest=manifest), detect.ManagedBy.USER)
                if os.geteuid() == 0:
                    _ = manifest.write_text(
                        '{"root":"/opt/xmrig","versions":[{"version":"xmrig-6.26.0","installed_at":"2026-10-07T00:00:00Z","source":"github:xmrig/xmrig:v6.26.0"}]}')
                    _ = marker.write_text("/opt/xmrig")
                    installed = Path("/opt/xmrig/xmrig-6.26.0/xmrig")
                    self.assertEqual(detect.ownership(binary, runner=detect.metadata_adapter(FakeRunner()),
                                                      marker=marker, manifest=manifest), detect.ManagedBy.USER)
                    self.assertEqual(detect.ownership(installed, runner=detect.metadata_adapter(FakeRunner()),
                                                       marker=marker, manifest=manifest), detect.ManagedBy.USER)
                _ = marker.write_text(str(root))
                self.assertEqual(detect.ownership(binary, runner=detect.metadata_adapter(FakeRunner()),
                                                 marker=marker, manifest=manifest), detect.ManagedBy.USER)

    def test_missing_binary_is_fresh_install(self) -> None:
        result = detect.classify([])
        self.assertEqual(result.recommendation, detect.Recommendation.INSTALL_UPSTREAM)

    def test_unsafe_visible_candidate_is_not_fresh_install(self) -> None:
        candidate = Path("/tmp/present-xmrig")
        install = detect.Install(candidate, None, detect.ManagedBy.USER, False, True, "unsafe path")
        result = detect.classify([install])
        self.assertIsNotNone(result.install)
        assert result.install is not None
        self.assertEqual(result.install.path, candidate)
        self.assertIn(str(candidate), result.reason)

    def test_classifier_requires_consent_and_protected_source(self) -> None:
        install = detect.Install(Path("/usr/bin/xmrig"), (6, 21, 2), detect.ManagedBy.DISTRO, True, False)
        self.assertEqual(detect.classify([install]).recommendation, detect.Recommendation.PROTECTED_COPY_CONSENT)
        self.assertEqual(detect.classify([install], consent=True).recommendation, detect.Recommendation.USE_EXISTING)
        writable = detect.Install(install.path, install.version, install.managed_by, True, True)
        self.assertEqual(detect.classify([writable], consent=True).recommendation,
                         detect.Recommendation.PROTECTED_COPY_CONSENT)

    def test_snapshot_refuses_symlink_and_snapshots_regular_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary = root / "candidate"
            _ = binary.write_bytes(b"binary")
            _ = binary.chmod(0o755)
            link = root / "link"
            _ = link.symlink_to(binary)
            self.assertIsNone(detect.snapshot(link, private_dir=root))
            snapshot = detect.snapshot(binary, private_dir=root)
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertEqual(snapshot[0].read_bytes(), b"binary")
            _ = snapshot[0].unlink()

    def test_snapshot_refuses_group_or_world_writable_candidates_for_adoption(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "candidate"
            _ = path.write_bytes(b"binary")
            _ = path.chmod(0o777)
            result = detect.snapshot(path, private_dir=Path(temp))
            self.assertIsNotNone(result)
            assert result is not None
            self.assertTrue(result[1])
            _ = result[0].unlink()

    def test_forged_marker_and_manifest_do_not_claim_sparehash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary = root / "xmrig"
            _ = binary.touch()
            marker = root / ".sparehash-managed"
            manifest = root / "install-manifest.json"
            _ = marker.write_text(str(root))
            _ = manifest.write_text(str(binary))
            _ = marker.chmod(0o666)
            self.assertEqual(detect.ownership(binary, runner=detect.metadata_adapter(FakeRunner()),
                                              marker=marker, manifest=manifest),
                              detect.ManagedBy.USER)

    def test_mismatched_current_manifest_is_unverified_and_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "outside"
            _ = target.write_bytes(b"candidate")
            marker = root / "marker"
            manifest = root / "manifest"
            _ = marker.write_text("/opt/xmrig")
            _ = manifest.write_text(
                '{"root":"/wrong","versions":[{"version":"xmrig-6.26.0","installed_at":"2026-10-07T00:00:00Z","source":"github:xmrig/xmrig:v6.26.0"}]}')
            _ = marker.chmod(0o600)
            _ = manifest.chmod(0o600)
            with mock.patch.object(detect, "_package_owned", return_value=False):
                owner = detect.ownership(Path("/opt/xmrig/xmrig-6.26.0/xmrig"),
                                         runner=detect.metadata_adapter(FakeRunner()), marker=marker,
                                         manifest=manifest)
            self.assertEqual(owner, detect.ManagedBy.USER)
            install = detect.Install(Path("/opt/xmrig/current/xmrig"), None, owner, False, True,
                                     "manifest mismatch")
            classification = detect.classify([install])
            self.assertEqual(classification.install, install)
            self.assertIn("manifest mismatch", classification.reason)

    def test_probe_receives_only_snapshot_nobody_and_fails_closed_without_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            candidate = Path(temp) / "xmrig"
            _ = candidate.write_bytes(b"candidate bytes")
            _ = candidate.chmod(0o755)
            observed: list[tuple[Path, int, int]] = []

            def probe(snapshot: Path, *, timeout: int, uid: int,
                      required_properties: tuple[str, ...]) -> str:
                observed.append((snapshot, timeout, uid))
                self.assertNotEqual(snapshot, candidate)
                self.assertEqual(uid, 65534)
                self.assertEqual(len(required_properties), 5)
                return "XMRig 6.21.2"

            with mock.patch.object(detect, "candidate_paths", return_value=(candidate,)):
                results = detect.detect_installations(probe=probe, runner=FakeRunner())
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0].version, (6, 21, 2))
                self.assertEqual(observed[0][2], 65534)
                missing_probe = detect.detect_installations(runner=FakeRunner())
                self.assertEqual(missing_probe[0].version, None)

    def test_timeout_probe_is_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            candidate = Path(temp) / "xmrig"
            _ = candidate.write_bytes(b"candidate bytes")
            _ = candidate.chmod(0o755)
            with mock.patch.object(detect, "candidate_paths", return_value=(candidate,)):
                def timeout_probe(snapshot: Path, *, timeout: int, uid: int,
                                  required_properties: tuple[str, ...]) -> str:
                    _ = (snapshot, uid, required_properties)
                    raise subprocess.TimeoutExpired("systemd-run", timeout)

                results = detect.detect_installations(probe=timeout_probe, runner=FakeRunner())
                self.assertIsNone(results[0].version)
                self.assertFalse(results[0].verified)


if __name__ == "__main__":
    _ = unittest.main()
