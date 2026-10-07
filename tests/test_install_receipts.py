"""Strict package/template receipt grammar, bootstrap provenance and receipt-safe removal."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import final

from sparehash import install
from sparehash.deployment import DeploymentError, Tree
from sparehash.install_ledger import Ledger
from sparehash.install_manifest import StoreContext, locked_store
from sparehash.xmrig_ledger import PACKAGE_DIR, TEMPLATE_DIR, bootstrap_target, fixed_target, leaf_identity
from sparehash.xmrig_layout import CollisionError
from sparehash.xmrig_manifest import parse_manifest
from sparehash.xmrig_remove import remove_leaf_receipts

PACKAGE = PACKAGE_DIR + "/"
TEMPLATE = TEMPLATE_DIR + "/"
BWRAP = shutil.which("bwrap")


def artifact(identity: str, path: str, kind: str = "file", created: bool = True,
             sha256: str | None = "a" * 64) -> dict[str, object]:
    return {"id": identity, "path": path, "kind": kind, "created": created, "uid": 0, "gid": 0,
            "mode": 0o644, "device": 1, "inode": 2, "sha256": None if kind == "directory" else sha256}


def document(artifacts: list[dict[str, object]], actions: list[dict[str, object]] | None = None,
             phase: str = "ready") -> str:
    return json.dumps({"root": "/opt/xmrig", "versions": [],
                       "installer": {"schema": 1, "phase": phase, "identities": [], "memberships": [],
                                     "artifacts": artifacts, "actions": actions or []}})


def action(sequence: int, operation: str, target: str, state: str = "applied") -> dict[str, object]:
    return {"sequence": sequence, "install_id": "b" * 32, "at": "2026-10-07T00:00:00Z",
            "operation": operation, "target": target, "state": state}


@final
class ReceiptGrammarTests(unittest.TestCase):
    def test_fixed_target_maps_only_the_fixed_vocabulary(self) -> None:
        # Given / When / Then
        self.assertEqual(fixed_target("package-file:alpha.py"), (PACKAGE + "alpha.py", "file"))
        self.assertEqual(fixed_target("unit-template:sparehash.service.in"), (TEMPLATE + "sparehash.service.in", "file"))
        self.assertEqual(fixed_target("/opt/sparehash/src/sparehash"), ("/opt/sparehash/src/sparehash", "directory"))
        for rejected in ("package-file:../alpha.py", "package-file:alpha.txt", "package-file:sub/alpha.py",
                         "unit-template:other.in", "package-file:", "package-file:alpha.py/x", "/etc/passwd"):
            with self.subTest(rejected=rejected):
                self.assertIsNone(fixed_target(rejected))

    def test_leaf_identity_maps_exact_installed_leaves(self) -> None:
        # Given / When / Then
        self.assertEqual(leaf_identity(PACKAGE + "alpha.py"), "package-file:alpha.py")
        self.assertEqual(leaf_identity(TEMPLATE + "sparehash-xmrig.service.in"), "unit-template:sparehash-xmrig.service.in")
        self.assertIsNone(leaf_identity(PACKAGE + "nested/alpha.py"))
        self.assertIsNone(leaf_identity(PACKAGE + "alpha.so"))
        self.assertIsNone(leaf_identity(TEMPLATE + "other.in"))

    def test_bootstrap_target_maps_only_shell_created_paths(self) -> None:
        # Given / When / Then
        self.assertEqual(bootstrap_target("/opt/sparehash"), ("directory", "/opt/sparehash", 0o755))
        self.assertEqual(bootstrap_target(PACKAGE_DIR), ("directory", PACKAGE_DIR, 0o755))
        self.assertEqual(bootstrap_target(TEMPLATE_DIR), ("directory", TEMPLATE_DIR, 0o755))
        self.assertEqual(bootstrap_target("/usr/local/bin/sparehash"), ("file", "/usr/local/bin/sparehash", 0o755))
        self.assertEqual(bootstrap_target(PACKAGE + "alpha.py"), ("file", "package-file:alpha.py", 0o644))
        self.assertEqual(bootstrap_target(TEMPLATE + "sparehash.service.in"),
                         ("file", "unit-template:sparehash.service.in", 0o644))
        for rejected in ("/var/lib/sparehash", "/etc/sparehash/config.toml",
                         "/etc/systemd/system/sparehash.service", "/tmp/alpha.py", PACKAGE + "alpha.txt"):
            with self.subTest(rejected=rejected):
                self.assertIsNone(bootstrap_target(rejected))

    def test_parse_manifest_accepts_typed_leaf_receipts(self) -> None:
        # Given
        text = document([artifact("package-file:alpha.py", PACKAGE + "alpha.py"),
                         artifact("unit-template:sparehash.service.in", TEMPLATE + "sparehash.service.in")])
        # When
        manifest = parse_manifest(text)
        # Then
        assert manifest is not None and manifest.installer is not None
        self.assertEqual([item.id for item in manifest.installer.artifacts],
                         ["package-file:alpha.py", "unit-template:sparehash.service.in"])

    def test_parse_manifest_rejects_id_path_or_kind_drift(self) -> None:
        # Given
        cases = (
            document([artifact("package-file:alpha.py", PACKAGE + "beta.py")]),
            document([artifact("package-file:alpha.py", PACKAGE + "../alpha.py")]),
            document([artifact("package-file:sub/alpha.py", PACKAGE + "sub/alpha.py")]),
            document([artifact("unit-template:other.in", TEMPLATE + "other.in")]),
            document([artifact("package-file:alpha.py", PACKAGE + "alpha.py", kind="directory", sha256=None)]),
            document([artifact("package-file:alpha.py", PACKAGE + "alpha.py", sha256=None)]),
        )
        for text in cases:
            with self.subTest(text=text[:80]):
                # When / Then
                self.assertIsNone(parse_manifest(text))

    def test_pending_action_maps_typed_leaf_target_only(self) -> None:
        # Given
        typed = document([], [action(1, "write-file", "package-file:alpha.py", "pending")], phase="installing")
        raw_path = document([], [action(1, "write-file", PACKAGE + "alpha.py", "pending")], phase="installing")
        # When / Then
        self.assertIsNotNone(parse_manifest(typed))
        self.assertIsNone(parse_manifest(raw_path))


def make_tree(root: Path) -> Tree:
    for name in ("opt", "opt/sparehash", "opt/sparehash/src", "opt/sparehash/src/sparehash",
                 "opt/sparehash/src/systemd", "var/lib/sparehash"):
        (root / name).mkdir(parents=True, exist_ok=True)
    root.chmod(0o755)
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts)):
        if path.is_dir():
            path.chmod(0o755)
    return Tree(root, os.getuid())


@final
class BootstrapReceiptTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tree = make_tree(self.root)

    def write(self, name: str, data: bytes = b"x") -> str:
        path = self.tree.path(name)
        _ = path.write_bytes(data)
        path.chmod(0o644)
        return name

    def test_bootstrap_records_only_declared_created_leaves(self) -> None:
        # Given
        alpha = self.write(PACKAGE + "alpha.py")
        template = self.write(TEMPLATE + "sparehash.service.in")
        ledger = Ledger(self.tree)
        ledger.initialize()
        # When
        install.bootstrap(ledger, ("/opt/sparehash", "/opt/sparehash/src", PACKAGE_DIR, TEMPLATE_DIR, alpha, template))
        # Then
        with locked_store(StoreContext(self.tree.trust)) as store:
            snapshot = store.read()
        assert snapshot is not None and snapshot.manifest.installer is not None
        by_id = {item.id: item for item in snapshot.manifest.installer.artifacts}
        self.assertEqual(by_id["package-file:alpha.py"].path, alpha)
        self.assertTrue(by_id["package-file:alpha.py"].created)
        self.assertIsNotNone(by_id["package-file:alpha.py"].sha256)
        self.assertEqual(by_id["unit-template:sparehash.service.in"].path, template)
        self.assertEqual(by_id["/opt/sparehash/src/sparehash"].kind, "directory")

    def test_bootstrap_leaves_created_false_when_path_not_declared(self) -> None:
        # Given an existing bootstrap directory that this invocation did not create
        ledger = Ledger(self.tree)
        ledger.initialize()
        # When
        install.bootstrap(ledger, ())
        # Then
        with locked_store(StoreContext(self.tree.trust)) as store:
            snapshot = store.read()
        assert snapshot is not None and snapshot.manifest.installer is not None
        by_id = {item.id: item for item in snapshot.manifest.installer.artifacts}
        self.assertFalse(by_id["/opt/sparehash"].created)

    def test_bootstrap_rejects_unbounded_target(self) -> None:
        # Given
        ledger = Ledger(self.tree)
        ledger.initialize()
        for name in ("/etc/passwd", PACKAGE + "alpha.sh", PACKAGE + "../alpha.py",
                     "/var/lib/sparehash", "/etc/sparehash/config.toml",
                     "/etc/systemd/system/sparehash.service"):
            with self.subTest(name=name), self.assertRaises(DeploymentError):
                # When
                install.bootstrap(ledger, (name,))

    def test_bootstrap_rejects_unprotected_leaf_mode(self) -> None:
        # Given an installable leaf writable by group/other
        name = self.write(PACKAGE + "alpha.py")
        self.tree.path(name).chmod(0o666)
        ledger = Ledger(self.tree)
        ledger.initialize()
        # When / Then
        with self.assertRaises(DeploymentError):
            install.bootstrap(ledger, (name,))

    def test_rerun_leaves_existing_package_file_unreceipted(self) -> None:
        # Given a package file that pre-existed this invocation and is not declared as created
        name = self.write(PACKAGE + "alpha.py")
        ledger = Ledger(self.tree)
        ledger.initialize()
        # When
        install.bootstrap(ledger, ())
        # Then observation does not fabricate package-file authority
        with locked_store(StoreContext(self.tree.trust)) as store:
            snapshot = store.read()
        assert snapshot is not None and snapshot.manifest.installer is not None
        self.assertNotIn("package-file:alpha.py", {item.id for item in snapshot.manifest.installer.artifacts})
        with self.assertRaises(CollisionError):
            _ = remove_leaf_receipts(self.tree.trust)
        self.assertTrue(self.tree.path(name).exists())

    def test_observation_never_promotes_created_false(self) -> None:
        # Given an existing directory receipt recorded as not created
        ledger = Ledger(self.tree)
        ledger.initialize()
        ledger.applied(0, (self.tree.receipt("/opt/sparehash", False),))
        # When the bootstrap re-observes it without declaring creation
        install.bootstrap(ledger, ())
        # Then provenance stays false
        with locked_store(StoreContext(self.tree.trust)) as store:
            snapshot = store.read()
        assert snapshot is not None and snapshot.manifest.installer is not None
        by_id = {item.id: item for item in snapshot.manifest.installer.artifacts}
        self.assertFalse(by_id["/opt/sparehash"].created)


@unittest.skipUnless(BWRAP is not None, "bwrap is required for the install.sh argument test")
@final
class BootstrapShellTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_install_sh_preserves_answers_and_declares_each_created_path(self) -> None:
        # Given a repo copy whose final exec is replaced by an argument dump
        repo = self.root / "repo"
        (repo / "sparehash").mkdir(parents=True)
        (repo / "systemd").mkdir()
        modules = ("alpha.py", "beta.py")
        for name in modules:
            _ = (repo / "sparehash" / name).write_text("value = 1\n")
        for name in ("sparehash.service.in", "sparehash-xmrig.service.in"):
            _ = (repo / "systemd" / name).write_text("[Unit]\n")
        source = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
        _ = (repo / "install.sh").write_text(source.replace('exec /usr/local/bin/sparehash install "$@"',
                                                             'printf "%s\\n" "$@"'))
        answers = self.root / "answers.toml"
        _ = answers.write_text("version = 1\n")
        bindings: list[str] = []
        for name in ("opt", "usr-local", "run-systemd"):
            directory = self.root / name
            directory.mkdir()
            directory.chmod(0o755)
            bindings.append(str(directory))
        (self.root / "usr-local" / "bin").mkdir(mode=0o755)
        # When
        result = subprocess.run(
            [str(BWRAP), "--unshare-user", "--uid", "0", "--gid", "0", "--ro-bind", "/", "/",
             "--bind", bindings[0], "/opt", "--bind", bindings[1], "/usr/local",
             "--bind", bindings[2], "/run/systemd/system", "--dev", "/dev", "--proc", "/proc",
             "/bin/sh", str(repo / "install.sh"), "--answers", str(answers)],
            capture_output=True, text=True, timeout=90, check=False)
        # Then
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertIn("--answers", lines)
        self.assertEqual(lines[lines.index("--answers") + 1], str(answers))
        self.assertLess(lines.index("--answers"), lines.index("--bootstrap-created"))
        for path in ("/opt/sparehash", "/opt/sparehash/src", "/opt/sparehash/src/sparehash",
                     "/opt/sparehash/src/systemd", "/usr/local/bin/sparehash",
                     *(PACKAGE + name for name in modules),
                     TEMPLATE + "sparehash.service.in", TEMPLATE + "sparehash-xmrig.service.in"):
            with self.subTest(path=path):
                self.assertIn(path, lines)


if __name__ == "__main__":
    _ = unittest.main()
