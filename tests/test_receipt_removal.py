"""Full-preflight removal of typed package/template receipts on private trees."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import final

from sparehash import install
from sparehash.install_ledger import Ledger
from sparehash.install_manifest import locked_store
from sparehash.xmrig_ledger import PACKAGE_DIR, TEMPLATE_DIR
from sparehash.xmrig_layout import CollisionError
from sparehash.xmrig_manifest import Manifest
from sparehash.xmrig_remove import remove_leaf_receipts
from tests.test_install_receipts import PACKAGE, TEMPLATE, make_tree


@final
class RemovalTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tree = make_tree(self.root)

    def build(self, created: bool = True) -> list[str]:
        names = [PACKAGE + "alpha.py", PACKAGE + "beta.py", TEMPLATE + "sparehash.service.in"]
        for name in names:
            path = self.tree.path(name)
            _ = path.write_bytes(name.encode())
            path.chmod(0o644)
        ledger = Ledger(self.tree)
        ledger.initialize()
        if created:
            install.bootstrap(ledger, ("/opt/sparehash", "/opt/sparehash/src", PACKAGE_DIR, TEMPLATE_DIR, *names))
        else:
            with locked_store(ledger.context()) as store:
                store.write(Manifest("/opt/xmrig", ()))
        return names

    def test_removes_only_authorized_leaves_then_empty_directories(self) -> None:
        # Given
        names = self.build()
        # When
        removed = remove_leaf_receipts(self.tree.trust)
        # Then
        self.assertEqual(sorted(removed), sorted(names))
        for name in names:
            self.assertFalse(self.tree.path(name).exists())
        self.assertFalse(self.tree.path(PACKAGE_DIR).exists())
        self.assertFalse(self.tree.path("/opt/sparehash").exists())

    def test_refuses_when_authorized_file_changed(self) -> None:
        # Given
        names = self.build()
        _ = self.tree.path(names[0]).write_bytes(b"tampered")
        # When / Then
        with self.assertRaises(CollisionError):
            _ = remove_leaf_receipts(self.tree.trust)
        self.assertTrue(self.tree.path(names[1]).exists())

    def test_refuses_when_unrecorded_child_present(self) -> None:
        # Given
        names = self.build()
        extra = self.tree.path(PACKAGE + "extra.py")
        _ = extra.write_bytes(b"unrecorded")
        extra.chmod(0o644)
        # When / Then
        with self.assertRaises(CollisionError):
            _ = remove_leaf_receipts(self.tree.trust)
        self.assertTrue(self.tree.path(names[0]).exists())
        self.assertTrue(extra.exists())

    def test_refuses_unrecorded_template_before_deleting_any_package_file(self) -> None:
        # Given an unrecorded template discovered after the package leaves were receipted
        names = self.build()
        extra = self.tree.path(TEMPLATE + "foreign.in")
        _ = extra.write_bytes(b"unrecorded")
        extra.chmod(0o644)
        # When / Then the full preflight refuses before the earlier package files are touched
        with self.assertRaises(CollisionError):
            _ = remove_leaf_receipts(self.tree.trust)
        for name in names:
            with self.subTest(name=name):
                self.assertTrue(self.tree.path(name).exists())
        self.assertTrue(extra.exists())

    def test_refuses_unknown_upper_descendant_before_deleting_any_leaf(self) -> None:
        # Given foreign content under the parent source directory
        names = self.build()
        foreign = self.tree.path("/opt/sparehash/src/foreign")
        foreign.mkdir(mode=0o755)
        # When / Then
        with self.assertRaises(CollisionError):
            _ = remove_leaf_receipts(self.tree.trust)
        for name in names:
            with self.subTest(name=name):
                self.assertTrue(self.tree.path(name).exists())
        self.assertTrue(foreign.exists())

    def test_refuses_when_created_directory_identity_changed(self) -> None:
        # Given a created package directory whose protection changed after install
        names = self.build()
        self.tree.path(PACKAGE_DIR).chmod(0o700)
        # When / Then
        with self.assertRaises(CollisionError):
            _ = remove_leaf_receipts(self.tree.trust)
        self.assertTrue(self.tree.path(names[0]).exists())

    def test_directory_receipt_alone_cannot_authorize_leaf_deletion(self) -> None:
        # Given files present under a created directory with no per-file receipts
        names = [PACKAGE + "alpha.py", TEMPLATE + "sparehash.service.in"]
        for name in names:
            path = self.tree.path(name)
            _ = path.write_bytes(name.encode())
            path.chmod(0o644)
        ledger = Ledger(self.tree)
        ledger.initialize()
        install.bootstrap(ledger, ("/opt/sparehash", "/opt/sparehash/src", PACKAGE_DIR, TEMPLATE_DIR))
        # When / Then a directory receipt alone grants no authority over its children
        with self.assertRaises(CollisionError):
            _ = remove_leaf_receipts(self.tree.trust)
        for name in names:
            with self.subTest(name=name):
                self.assertTrue(self.tree.path(name).exists())
        # When exact child receipts are added the same removal succeeds
        install.bootstrap(ledger, (*names,))
        self.assertEqual(sorted(remove_leaf_receipts(self.tree.trust)), sorted(names))

    def test_refuses_legacy_install_without_file_receipts(self) -> None:
        # Given a package tree with no installer receipts
        _ = self.build(created=False)
        # When / Then
        with self.assertRaises(CollisionError):
            _ = remove_leaf_receipts(self.tree.trust)
        self.assertTrue(self.tree.path(PACKAGE + "alpha.py").exists())


if __name__ == "__main__":
    _ = unittest.main()
