"""Injected-filesystem current-link tests; candidates are never executed."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import final
from unittest import mock

from sparehash import xmrig_detect as detect
from sparehash import xmrig_paths as paths
from sparehash.xmrig_manifest import format_manifest
from tests.test_xmrig_manifest import manifest_fixture


@final
class XmrigCurrentTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.trust = paths.PathTrust(self.root, os.getuid())
        self.binary = self.trust.managed_root / "xmrig-6.26.0/xmrig"
        self.binary.parent.mkdir(parents=True, mode=0o755)
        self.original = b"#!/bin/sh\nexit 99\n"
        _ = self.binary.write_bytes(self.original)
        self.binary.chmod(0o755)
        self.current = self.trust.managed_root / "current"
        self.current.symlink_to(self.binary.parent.name)
        self.candidate = self.current / "xmrig"
        self.marker = self.trust.managed_root / ".sparehash-managed"
        _ = self.marker.write_text("/opt/xmrig", encoding="utf-8")
        self.manifest = self.root / "var/lib/sparehash/install-manifest.json"
        self.manifest.parent.mkdir(parents=True, mode=0o755)
        for directory in (self.root / "opt", self.trust.managed_root,
                          self.root / "var", self.root / "var/lib", self.manifest.parent):
            directory.chmod(0o755)
        _ = self.manifest.write_text(
            '{"root":"/opt/xmrig","versions":[{"version":"xmrig-6.26.0","installed_at":"2026-10-07T00:00:00Z","source":"github:xmrig/xmrig:v6.26.0"}]}',
            encoding="utf-8")
        self.marker.chmod(0o600)
        self.manifest.chmod(0o600)
        self.observed: list[bytes] = []

    def _probe(self, snapshot: Path, *, timeout: int, uid: int,
               required_properties: tuple[str, ...]) -> str:
        self.assertNotEqual(snapshot, self.candidate)
        self.assertNotEqual(snapshot, self.binary)
        self.assertEqual((timeout, uid), (15, 65534))
        self.assertEqual(set(required_properties), {"ProtectSystem=strict", "ProtectHome=yes",
                                                  "PrivateNetwork=yes", "NoNewPrivileges=yes", "PrivateTmp=yes"})
        self.observed.append(snapshot.read_bytes())
        return "XMRig 6.26.0"

    def _detect(self, probe: detect.IsolationProbe | None = None,
                trust: paths.PathTrust | None = None) -> list[detect.Install]:
        with (mock.patch.object(detect, "_PATH_TRUST", trust or self.trust),
              mock.patch.object(detect, "candidate_paths", return_value=(self.candidate,)),
              mock.patch.object(detect, "_package_owned", return_value=False)):
            return detect.detect_installations(probe=probe or self._probe, marker=self.marker, manifest=self.manifest)

    def _assert_unverified(self, installs: list[detect.Install]) -> None:
        self.assertEqual(len(installs), 1)
        self.assertEqual(installs[0].path, self.candidate)
        self.assertFalse(installs[0].verified)
        self.assertIsNone(installs[0].version)
        self.assertEqual(self.observed, [])

    def test_current_is_verified_when_relative_link_is_manifest_authorized(self) -> None:
        # Given: protected metadata and a recorded version beneath the fixture root.
        # When: detection snapshots the authorized current candidate.
        installs = self._detect()
        # Then: the injected probe sees copied bytes, never the candidate executable.
        self.assertEqual(self.observed, [self.original])
        self.assertEqual(installs[0], detect.Install(self.candidate, (6, 26, 0), detect.ManagedBy.SPAREHASH, True, False))
        self.assertEqual(detect.classify(installs, consent=True).recommendation, detect.Recommendation.USE_EXISTING)

    def test_current_is_verified_when_extended_manifest_records_the_target(self) -> None:
        # Given
        _ = self.manifest.write_bytes(format_manifest(manifest_fixture()))
        # When
        installs = self._detect()
        # Then
        self.assertTrue(installs[0].verified)
        self.assertEqual(self.observed, [self.original])

    def test_current_is_unverified_when_marker_is_forged(self) -> None:
        # Given
        _ = self.marker.write_text("/opt/other", encoding="utf-8")
        # When
        installs = self._detect()
        # Then
        self._assert_unverified(installs)

    def test_current_is_unverified_when_manifest_disagrees(self) -> None:
        # Given: a canonical manifest that records a different version than the link.
        _ = self.manifest.write_text(
            '{"root":"/opt/xmrig","versions":[{"version":"xmrig-6.21.2","installed_at":"2026-10-07T00:00:00Z","source":"github:xmrig/xmrig:v6.21.2"}]}',
            encoding="utf-8")
        # When
        installs = self._detect()
        # Then
        self._assert_unverified(installs)

    def test_current_is_unverified_when_link_traverses_parent(self) -> None:
        # Given: an existing traversal target, so unsafe presence must remain visible.
        outside = self.root / "opt/outside"
        outside.mkdir()
        _ = (outside / "xmrig").write_bytes(self.original)
        self.current.unlink()
        self.current.symlink_to("../outside")
        # When
        installs = self._detect()
        # Then
        self._assert_unverified(installs)

    def test_candidate_is_unverified_when_symlink_is_not_current(self) -> None:
        # Given
        self.candidate = self.trust.managed_root / "arbitrary"
        self.candidate.symlink_to(self.binary)
        # When
        installs = self._detect()
        # Then
        self._assert_unverified(installs)

    def test_current_is_unverified_when_metadata_or_tree_is_writable(self) -> None:
        for path in (self.marker, self.manifest, self.manifest.parent, self.trust.managed_root, self.binary.parent, self.binary):
            with self.subTest(path=path):
                # Given
                mode = path.stat().st_mode & 0o777
                path.chmod(mode | 0o020)
                try:
                    # When
                    installs = self._detect()
                    # Then
                    self._assert_unverified(installs)
                finally:
                    path.chmod(mode)

    def test_current_is_unverified_when_owner_is_not_trusted(self) -> None:
        # Given
        trust = paths.PathTrust(self.root, os.getuid() + 1)
        # When
        installs = self._detect(trust=trust)
        # Then
        self._assert_unverified(installs)

    def test_current_is_unverified_when_binary_is_symlink(self) -> None:
        # Given
        target = self.root / "outside"
        _ = target.write_bytes(self.original)
        self.binary.unlink()
        self.binary.symlink_to(target)
        # When
        installs = self._detect()
        # Then
        self._assert_unverified(installs)

    def test_metadata_is_unverified_when_path_is_swapped_after_open(self) -> None:
        # Given: the opened inode is forged; a later pathname lookup would see valid text.
        _ = self.marker.write_text("/opt/forged", encoding="utf-8")
        real_open = os.open

        def swapping_open(path: str | os.PathLike[str], flags: int, mode: int = 0o777,
                          *, dir_fd: int | None = None) -> int:
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            if path == self.marker.name:
                _ = self.marker.rename(self.marker.with_name("old-marker"))
                _ = self.marker.write_text("/opt/xmrig", encoding="utf-8")
                self.marker.chmod(0o600)
            return descriptor

        # When
        with mock.patch("sparehash.xmrig_paths.os.open", side_effect=swapping_open):
            installs = self._detect()
        # Then
        self._assert_unverified(installs)

    def test_snapshot_keeps_original_bytes_when_source_is_replaced_during_probe(self) -> None:
        # Given
        def replacing_probe(snapshot: Path, *, timeout: int, uid: int,
                            required_properties: tuple[str, ...]) -> str:
            self.binary.unlink()
            _ = self.binary.write_bytes(b"replacement")
            return self._probe(snapshot, timeout=timeout, uid=uid, required_properties=required_properties)

        # When
        installs = self._detect(probe=replacing_probe)
        # Then
        self.assertTrue(installs[0].verified)
        self.assertEqual(self.observed, [self.original])
