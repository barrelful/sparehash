"""Canonical storage tests use private roots and real directory flock semantics."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from dataclasses import replace
from typing import final
from unittest import mock

from sparehash import xmrig_install as install
from sparehash.install_manifest import StoreContext, StoreError, locked_store
from sparehash.xmrig_ledger import Action, Installer
from sparehash.xmrig_manifest import Manifest, ManifestError, format_manifest
from tests.test_xmrig_manifest import installer_fixture, manifest_fixture
from tests.xmrig_layout_fixtures import DATE, Layout, release_members, request_for, write_tar


@final
class StoreTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.layout = Layout()
        self.addCleanup(self.layout.cleanup)
        self.layout.build(["xmrig-6.26.0"], current="xmrig-6.26.0")
        self.context = StoreContext(self.layout.trust, timeout=0)

    def test_protected_document_when_published(self) -> None:
        # Given
        manifest = manifest_fixture()
        # When
        with locked_store(self.context) as store:
            store.write(manifest)
        # Then
        info = self.layout.manifest.stat()
        self.assertEqual((info.st_uid, info.st_gid, info.st_mode & 0o777), (os.getuid(), os.getgid(), 0o640))
        self.assertEqual(self.layout.manifest.read_bytes(), format_manifest(manifest))

    def test_refusal_when_existing_metadata_is_unsafe(self) -> None:
        for mutation in ("symlink", "fifo", "hardlink", "writable", "malformed", "oversized", "encoding"):
            with self.subTest(mutation=mutation):
                # Given
                layout = Layout()
                self.addCleanup(layout.cleanup)
                layout.build(["xmrig-6.26.0"])
                if mutation == "symlink":
                    layout.manifest.unlink()
                    layout.manifest.symlink_to(layout.binary("xmrig-6.26.0"))
                if mutation == "fifo":
                    layout.manifest.unlink()
                    os.mkfifo(layout.manifest)
                if mutation == "hardlink":
                    os.link(layout.manifest, layout.root / "linked")
                if mutation == "writable":
                    layout.manifest.chmod(0o666)
                if mutation == "malformed":
                    _ = layout.manifest.write_bytes(b"{}")
                if mutation == "oversized":
                    _ = layout.manifest.write_bytes(b" " * (64 * 1024 + 1))
                if mutation == "encoding":
                    _ = layout.manifest.write_bytes(b"\xff")
                # When / Then
                with locked_store(StoreContext(layout.trust)) as store:
                    with self.assertRaises(StoreError):
                        store.write(manifest_fixture())

    def test_refusal_when_directory_is_writable_or_wrong_owner(self) -> None:
        # Given
        self.layout.manifest.parent.chmod(0o777)
        # When / Then
        with self.assertRaises(StoreError):
            with locked_store(self.context):
                self.fail("unsafe directory accepted")

    def test_refusal_when_directory_owner_does_not_match_authority(self) -> None:
        # Given
        context = replace(self.context, trust=replace(self.layout.trust, uid=os.getuid() + 1))
        # When / Then
        with self.assertRaises(StoreError):
            with locked_store(context):
                self.fail("wrong owner accepted")

    def test_pinned_destination_when_directory_path_is_replaced(self) -> None:
        # Given
        moved = self.layout.root / "original-directory"
        with locked_store(self.context) as store:
            _ = self.layout.manifest.parent.rename(moved)
            self.layout.manifest.parent.mkdir()
            sentinel = self.layout.manifest
            _ = sentinel.write_bytes(b"unrelated")
            # When
            store.write(manifest_fixture())
        # Then
        self.assertEqual(sentinel.read_bytes(), b"unrelated")
        self.assertEqual((moved / "install-manifest.json").read_bytes(), format_manifest(manifest_fixture()))

    def test_timeout_when_another_process_holds_the_directory(self) -> None:
        # Given
        program = (
            "import os,sys; from pathlib import Path; "
            "from sparehash.xmrig_paths import PathTrust; "
            "from sparehash.install_manifest import StoreContext,StoreError,locked_store; "
            "context=StoreContext(PathTrust(Path(sys.argv[1]),os.getuid()),timeout=0);\n"
            "try:\n with locked_store(context): pass\n"
            "except StoreError as error:\n print(error.stage); sys.exit(7)\n")
        with locked_store(self.context):
            # When
            result = subprocess.run((sys.executable, "-c", program, str(self.layout.root)),
                                    capture_output=True, text=True, check=False, timeout=5,
                                    env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
        # Then
        self.assertEqual((result.returncode, result.stdout.strip()), (7, "manifest-lock"))

    def test_prior_ownership_and_mode_when_restored(self) -> None:
        # Given
        self.layout.manifest.chmod(0o600)
        original = self.layout.manifest.read_bytes()
        with locked_store(self.context) as store:
            prior = store.read()
            store.write(manifest_fixture())
            # When
            store.restore(prior)
        # Then
        self.assertEqual(self.layout.manifest.read_bytes(), original)
        self.assertEqual(self.layout.manifest.stat().st_mode & 0o777, 0o600)

    def test_prior_restored_when_interrupted_before_rename(self) -> None:
        # Given
        original = self.layout.manifest.read_bytes()
        rename = os.rename
        attempts: list[str] = []

        def interrupted(src: str, dst: str, *, src_dir_fd: int, dst_dir_fd: int) -> None:
            attempts.append(src)
            if len(attempts) == 1:
                raise KeyboardInterrupt
            rename(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

        # When / Then
        with locked_store(self.context) as store, mock.patch("sparehash.install_manifest.os.rename", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                store.write(manifest_fixture())
        self.assertEqual(self.layout.manifest.read_bytes(), original)
        self.assertFalse(tuple(self.layout.manifest.parent.glob(".*.tmp-*")))


@final
class BootstrapTests(unittest.TestCase):
    def test_prior_manifest_restored_when_current_publication_is_interrupted(self) -> None:
        # Given
        layout = Layout()
        self.addCleanup(layout.cleanup)
        layout.build(["xmrig-6.26.0"], current="xmrig-6.26.0")
        before = layout.manifest.read_bytes()
        archive = write_tar(layout.root / "release.tar.gz", release_members("xmrig-6.26.1"))
        rename = os.rename

        def interrupted(src: str, dst: str, *, src_dir_fd: int, dst_dir_fd: int) -> None:
            if dst == "current":
                raise KeyboardInterrupt
            rename(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

        # When / Then
        with mock.patch.object(install, "verify_release"), mock.patch("sparehash.xmrig_layout.os.rename", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                _ = install.install_release(request_for(archive, (6, 26, 1)), install.InstallContext(layout.trust))
        self.assertEqual(layout.manifest.read_bytes(), before)
        self.assertFalse((layout.managed / "xmrig-6.26.1").exists())

    def test_extension_preserved_when_bootstrapped_before_managed_root(self) -> None:
        # Given
        layout = Layout()
        self.addCleanup(layout.cleanup)
        ledger = Installer(1, "installing", (), (), (),
                           (Action(1, "b" * 32, DATE, "create-group", "group:sparehash", "pending"),))
        manifest = Manifest("/opt/xmrig", (), ledger)
        with locked_store(StoreContext(layout.trust, create=True)) as store:
            store.write(manifest)
        archive = write_tar(layout.root / "release.tar.gz", release_members("xmrig-6.26.0"))
        # When
        with mock.patch.object(install, "verify_release"):
            result = install.install_release(request_for(archive), install.InstallContext(layout.trust))
        # Then
        self.assertEqual(result.manifest.installer, ledger)
        self.assertEqual(install.current_binary(layout.trust), layout.binary("xmrig-6.26.0"))

    def test_refusal_when_manifest_inventory_is_ghosted(self) -> None:
        # Given
        layout = Layout()
        self.addCleanup(layout.cleanup)
        with locked_store(StoreContext(layout.trust, create=True)) as store:
            store.write(manifest_fixture())
        before = layout.manifest.read_bytes()
        archive = write_tar(layout.root / "release.tar.gz", release_members("xmrig-6.26.0"))
        # When / Then
        with mock.patch.object(install, "verify_release"), self.assertRaises(install.CollisionError):
            _ = install.install_release(request_for(archive), install.InstallContext(layout.trust))
        self.assertEqual(layout.manifest.read_bytes(), before)
        self.assertFalse(layout.managed.exists())

    def test_refusal_before_extraction_when_next_ledger_exceeds_budget(self) -> None:
        # Given
        layout = Layout()
        self.addCleanup(layout.cleanup)
        layout.build(["xmrig-6.26.0"], current="xmrig-6.26.0")
        ledger = installer_fixture()
        prior = manifest_fixture()
        lower, upper = 0, 1000
        while lower + 1 < upper:
            count = (lower + upper) // 2
            actions = tuple(replace(ledger.actions[0], sequence=index) for index in range(1, count + 1))
            candidate = replace(prior, installer=replace(ledger, actions=actions))
            try:
                _ = format_manifest(candidate)
            except ManifestError:
                upper = count
            else:
                lower, prior = count, candidate
        with locked_store(StoreContext(layout.trust)) as store:
            store.write(prior)
        target = "xmrig-6.26.1"
        archive = write_tar(layout.root / "release.tar.gz", release_members(target))
        request = replace(request_for(archive, (6, 26, 1)), source="a" * 255)
        before = layout.manifest.read_bytes()
        # When / Then
        with mock.patch.object(install, "verify_release"), self.assertRaises(install.InstallError):
            _ = install.install_release(request, install.InstallContext(layout.trust))
        self.assertFalse((layout.managed / target).exists())
        self.assertEqual(layout.manifest.read_bytes(), before)
