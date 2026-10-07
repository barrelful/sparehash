"""Managed /opt/xmrig layout, update and rollback tests on a private temp root.

Every call injects ``InstallContext(trust=...)`` pointing at a private temporary
anchor; no test ever resolves a real /opt, systemd, root or network resource.
"""

from __future__ import annotations

import json
import os
import tarfile
import unittest
from pathlib import Path
from typing import final
from unittest import mock

from sparehash import xmrig_archive as archive
from sparehash import xmrig_install as install
from sparehash.xmrig_paths import VersionRecord, parse_manifest
from sparehash.xmrig_verify import VerifyError
from tests.xmrig_layout_fixtures import (
    DATE,
    FakeGate,
    Layout,
    Member,
    canonical_manifest,
    release_members,
    request_for,
    source_for,
    write_tar,
)

V1 = "xmrig-6.26.0"
V2 = "xmrig-6.26.1"
V3 = "xmrig-6.26.2"
V4 = "xmrig-6.26.3"


@final
class ManifestSchemaTests(unittest.TestCase):
    def test_canonical_manifest_parses_and_drift_is_rejected(self) -> None:
        valid = parse_manifest(canonical_manifest([V1]))
        assert valid is not None
        self.assertEqual(valid.versions, (VersionRecord(V1, DATE, source_for(V1)),))
        drift = [canonical_manifest([V1]).replace("/opt/xmrig", "/opt/other", 1),
                 json.dumps({"root": "/opt/xmrig", "versions": [], "extra": 1}),
                 '{"root":"/opt/xmrig","root":"/opt/xmrig","versions":[]}',
                 canonical_manifest([V1, V1]), canonical_manifest(["xmrig-6.26"]),
                 json.dumps({"root": "/opt/xmrig", "versions": {}}), "{"]
        for text in drift:
            with self.subTest(text=text):
                self.assertIsNone(parse_manifest(text))


@final
class InstallLayoutTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.layout = Layout()
        self.addCleanup(self.layout.cleanup)

    def _archive(self, version_dir: str = V1, members: list[Member] | None = None) -> Path:
        path = self.layout.root / f"{version_dir}.tar.gz"
        _ = write_tar(path, release_members(version_dir) if members is None else members)
        return path

    def _context(self, gate: FakeGate | None = None) -> install.InstallContext:
        return install.InstallContext(trust=self.layout.trust, gate=gate)

    def _install(self, request: install.InstallRequest,
                 context: install.InstallContext | None = None) -> install.Installed:
        with mock.patch.object(install, "verify_release"):
            return install.install_release(request, context or self._context())

    def _update(self, request: install.InstallRequest,
                context: install.InstallContext) -> install.Installed:
        with mock.patch.object(install, "verify_release"):
            return install.update_flow(request, context)

    def test_first_install_creates_managed_tree(self) -> None:
        result = self._install(request_for(self._archive()))
        self.assertEqual(result.version, V1)
        self.assertEqual(str((self.layout.managed / "current").readlink()), V1)
        self.assertEqual(self.layout.binary(V1).stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.layout.managed.stat().st_mode & 0o022, 0)
        self.assertEqual(self.layout.marker.stat().st_mode & 0o022, 0)
        manifest = parse_manifest(self.layout.manifest.read_text())
        assert manifest is not None
        self.assertEqual(manifest.names, (V1,))
        self.assertEqual(install.current_binary(self.layout.trust), self.layout.binary(V1))

    def test_unverified_artifact_prevents_install(self) -> None:
        with mock.patch.object(install, "verify_release", side_effect=VerifyError("sha256", "bad")):
            with self.assertRaises(VerifyError):
                _ = install.install_release(request_for(self._archive()), self._context())
        self.assertFalse(self.layout.managed.exists())

    def test_existing_unmanaged_root_is_rejected_untouched(self) -> None:
        self.layout.managed.mkdir(mode=0o755)
        foreign = self.layout.managed / "keep.txt"
        _ = foreign.write_bytes(b"foreign")
        with self.assertRaises(install.CollisionError):
            _ = self._install(request_for(self._archive()))
        self.assertEqual(foreign.read_bytes(), b"foreign")
        self.assertFalse(self.layout.marker.exists())

    def test_forged_marker_is_rejected(self) -> None:
        self.layout.build([V1], current=V1, marker_text="/opt/other")
        before = self.layout.manifest.read_bytes()
        with self.assertRaises(install.CollisionError):
            _ = self._install(request_for(self._archive()))
        self.assertEqual(self.layout.manifest.read_bytes(), before)

    def test_user_writable_root_is_rejected(self) -> None:
        self.layout.build([V1], current=V1)
        self.layout.managed.chmod(0o777)
        with self.assertRaises(install.CollisionError):
            _ = self._install(request_for(self._archive()))

    def test_symlinked_root_is_rejected(self) -> None:
        target = self.layout.root / "elsewhere"
        target.mkdir(mode=0o755)
        self.layout.managed.symlink_to(target)
        with self.assertRaises(install.CollisionError):
            _ = self._install(request_for(self._archive()))

    def test_unrecorded_version_directory_is_rejected(self) -> None:
        self.layout.build([V1], records=[])
        extra = self.layout.managed / V2
        extra.mkdir(mode=0o755)
        sentinel = extra / "keep"
        _ = sentinel.write_bytes(b"keep")
        with self.assertRaises(install.CollisionError):
            _ = self._install(request_for(self._archive(V2), version=(6, 26, 1)))
        self.assertEqual(sentinel.read_bytes(), b"keep")

    def test_current_link_to_unrecorded_target_is_rejected(self) -> None:
        self.layout.build([V1], records=[V1], current=V2)
        with self.assertRaises(install.CollisionError):
            _ = self._install(request_for(self._archive()))

    def test_first_install_failure_rolls_back_created_root(self) -> None:
        sentinel = self.layout.root / "opt/sentinel"
        _ = sentinel.write_bytes(b"untouched")
        bad = [Member(f"{V1}/xmrig", b"x"), Member(f"{V1}/../escape", b"y")]
        with self.assertRaises(install.InstallError):
            _ = self._install(request_for(self._archive(members=bad)))
        self.assertFalse(self.layout.managed.exists())
        self.assertEqual(sentinel.read_bytes(), b"untouched")

    def test_unsafe_archive_members_are_rejected(self) -> None:
        cases = {
            "traversal": [Member(f"{V1}/../evil", b"x")],
            "absolute": [Member("/etc/passwd", b"x")],
            "symlink": [Member(f"{V1}/link", kind=tarfile.SYMTYPE, linkname="/etc/passwd")],
            "hardlink": [Member(f"{V1}/hard", kind=tarfile.LNKTYPE, linkname=f"{V1}/xmrig")],
            "fifo": [Member(f"{V1}/pipe", kind=tarfile.FIFOTYPE)],
            "char": [Member(f"{V1}/dev", kind=tarfile.CHRTYPE)],
            "normalized-alias": [Member(f"{V1}//xmrig", b"x")],
            "duplicate": [Member(f"{V1}/xmrig", b"x"), Member(f"{V1}/xmrig", b"y")],
        }
        for label, members in cases.items():
            with self.subTest(label=label):
                layout = Layout()
                self.addCleanup(layout.cleanup)
                path = write_tar(layout.root / "bad.tar.gz", members)
                context = install.InstallContext(trust=layout.trust)
                with mock.patch.object(install, "verify_release"):
                    with self.assertRaises(install.InstallError):
                        _ = install.install_release(request_for(path), context)
                self.assertFalse(layout.managed.exists())

    def test_oversized_and_sparse_members_are_rejected(self) -> None:
        with mock.patch.object(archive, "MAX_ENTRY_BYTES", 4):
            request = request_for(self._archive(members=[Member(f"{V1}/xmrig", b"0123456789")]))
            with self.assertRaises(install.InstallError):
                _ = self._install(request)
        self.assertFalse(self.layout.managed.exists())
        request = request_for(self._archive(members=[Member(f"{V1}/xmrig", b"x")]))
        with mock.patch.object(tarfile.TarInfo, "issparse", return_value=True):
            with self.assertRaises(install.InstallError):
                _ = self._install(request)
        self.assertFalse(self.layout.managed.exists())

    def test_reinstall_same_version_is_idempotent(self) -> None:
        _ = self._install(request_for(self._archive()))
        before = self.layout.manifest.read_bytes()
        _ = self._install(request_for(self._archive()))
        self.assertEqual(self.layout.manifest.read_bytes(), before)
        self.assertEqual(str((self.layout.managed / "current").readlink()), V1)

    def test_second_install_keeps_both_versions(self) -> None:
        _ = self._install(request_for(self._archive()))
        _ = self._install(request_for(self._archive(V2), version=(6, 26, 1)))
        self.assertTrue(self.layout.binary(V1).exists())
        self.assertTrue(self.layout.binary(V2).exists())
        self.assertEqual(str((self.layout.managed / "current").readlink()), V2)
        manifest = parse_manifest(self.layout.manifest.read_text())
        assert manifest is not None
        self.assertEqual(manifest.names, (V1, V2))

    def test_update_prunes_owned_older_versions_under_lease(self) -> None:
        self.layout.build([V1, V2, V3], current=V3)
        unrelated = self.layout.managed / "unrelated"
        unrelated.mkdir(mode=0o755)
        sentinel = unrelated / "keep"
        _ = sentinel.write_bytes(b"keep")
        gate = FakeGate()
        current_at_release: list[str] = []
        gate.on_release = lambda _empty: current_at_release.append(
            str((self.layout.managed / "current").readlink()))
        request = request_for(self._archive(V4), version=(6, 26, 3))
        result = self._update(request, self._context(gate))
        self.assertEqual(result.pruned, (V1, V2))
        self.assertEqual(gate.calls, ["acquire", "ready", "release"])
        self.assertEqual(current_at_release, [V4])
        self.assertFalse((self.layout.managed / V1).exists())
        self.assertFalse((self.layout.managed / V2).exists())
        self.assertTrue(self.layout.binary(V3).exists())
        self.assertTrue(self.layout.binary(V4).exists())
        self.assertEqual(sentinel.read_bytes(), b"keep")

    def test_update_without_valid_lease_leaves_tree_unchanged(self) -> None:
        cases = {"absent": None, "busy": FakeGate(busy=True), "not-empty": FakeGate(empty=False)}
        for label, gate in cases.items():
            with self.subTest(label=label):
                layout = Layout()
                self.addCleanup(layout.cleanup)
                layout.build([V1, V2, V3], current=V3)
                before = layout.manifest.read_bytes()
                path = write_tar(layout.root / "r.tar.gz", release_members(V4))
                context = install.InstallContext(trust=layout.trust, gate=gate)
                with mock.patch.object(install, "verify_release"):
                    with self.assertRaises(install.InstallError):
                        _ = install.update_flow(request_for(path, version=(6, 26, 3)), context)
                self.assertEqual(layout.manifest.read_bytes(), before)
                self.assertEqual(str((layout.managed / "current").readlink()), V3)
                self.assertTrue(layout.binary(V1).exists())
                self.assertTrue(layout.binary(V2).exists())
                self.assertFalse((layout.managed / V4).exists())

    def test_update_publish_failure_restores_prior_link_and_manifest(self) -> None:
        self.layout.build([V1, V2, V3], current=V3)
        before = self.layout.manifest.read_bytes()
        real_rename = os.rename

        def failing_rename(src: str, dst: str, src_dir_fd: int | None = None,
                           dst_dir_fd: int | None = None) -> None:
            if dst == "current":
                raise OSError("link publish blocked")
            real_rename(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

        request = request_for(self._archive(V4), version=(6, 26, 3))
        with self.assertRaises(install.InstallError):
            with mock.patch("sparehash.xmrig_layout.os.rename", side_effect=failing_rename):
                _ = self._update(request, self._context(FakeGate()))
        self.assertEqual(self.layout.manifest.read_bytes(), before)
        self.assertEqual(str((self.layout.managed / "current").readlink()), V3)
        self.assertFalse((self.layout.managed / V4).exists())

    def test_release_failure_surfaces_typed_error(self) -> None:
        self.layout.build([V1, V2], current=V2)
        request = request_for(self._archive(V3), version=(6, 26, 2))
        with self.assertRaises(install.InstallError) as caught:
            _ = self._update(request, self._context(FakeGate(release_ok=False)))
        self.assertEqual(caught.exception.stage, "lease")
        self.assertEqual(str((self.layout.managed / "current").readlink()), V3)

    def test_rollback_returns_to_previous_under_lease(self) -> None:
        self.layout.build([V1, V2], current=V2)
        gate = FakeGate()
        result = install.rollback(self.layout.trust, gate=gate)
        self.assertEqual(result.version, V1)
        self.assertEqual(gate.calls, ["acquire", "ready", "release"])
        self.assertEqual(str((self.layout.managed / "current").readlink()), V1)
        self.assertFalse((self.layout.managed / V2).exists())

    def test_rollback_without_gate_is_denied(self) -> None:
        self.layout.build([V1, V2], current=V2)
        with self.assertRaises(install.InstallError):
            _ = install.rollback(self.layout.trust)
        self.assertEqual(str((self.layout.managed / "current").readlink()), V2)


if __name__ == "__main__":
    _ = unittest.main()
