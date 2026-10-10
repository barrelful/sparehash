"""Root-only uninstall ordering and fail-closed behavior on a private tree."""

from __future__ import annotations

import os
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from typing import final
from unittest import mock

from sparehash import cli_uninstall, install, ipc, lifecycle
from sparehash.cli_ops import Client
from sparehash.deployment import DeploymentError, Tree
from sparehash.install_ledger import Ledger
from sparehash.install_manifest import locked_store
from sparehash.lifecycle_lease import ActiveLease, LeaseStore
from sparehash.xmrig_ledger import PACKAGE_DIR, GroupIdentity, UserIdentity
from sparehash.xmrig_manifest import Manifest

TOKEN = "a" * 64
REQUEST = "b" * 32
PACKAGE_LEAF = PACKAGE_DIR + "/alpha.py"
CONFIG = "/etc/sparehash/config.toml"


def forbidden_client(command: ipc.Document, /, *, timeout: float) -> ipc.Document:
    _ = command, timeout
    raise AssertionError("uninstall must not call controller IPC")


@final
class FakeInfrastructure:
    def __init__(self, empty: bool = True) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.empty = empty

    def run(self, argv: tuple[str, ...]) -> None:
        self.calls.append(argv)

    def cgroup_empty(self) -> bool:
        self.calls.append(("empty",))
        return self.empty

    def free_hugepages(self) -> None:
        self.calls.append(("hugepages",))

    def unmerge(self, name: str) -> None:
        self.calls.append(("unmerge", name))


def make_tree(root: Path) -> Tree:
    for name in ("opt", "opt/sparehash", "opt/sparehash/src", "opt/sparehash/src/sparehash",
                 "usr", "usr/local", "usr/local/bin", "etc", "etc/sparehash", "etc/systemd",
                 "etc/systemd/system", "var", "var/lib", "var/lib/sparehash"):
        (root / name).mkdir(parents=True, exist_ok=True)
    root.chmod(0o755)
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts)):
        if path.is_dir():
            path.chmod(0o755)
    return Tree(root, os.getuid())


@final
class UninstallTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tree = make_tree(self.root)

    def write(self, name: str) -> str:
        path = self.tree.path(name)
        _ = path.write_bytes(b"x")
        path.chmod(0o644)
        return name

    def build(self, *, legacy: bool = False) -> None:
        for name in (PACKAGE_LEAF, "/usr/local/bin/sparehash", "/etc/systemd/system/sparehash.service",
                     "/etc/systemd/system/sparehash-xmrig.service", CONFIG):
            _ = self.write(name)
        ledger = Ledger(self.tree)
        ledger.initialize()
        if legacy:
            with locked_store(ledger.context()) as store:
                store.write(Manifest("/opt/xmrig", ()))
            return
        install.bootstrap(ledger, ("/opt/sparehash", "/opt/sparehash/src", PACKAGE_DIR, PACKAGE_LEAF))
        receipts = (self.tree.receipt("/usr/local/bin/sparehash", True),
                    self.tree.receipt("/etc/systemd/system/sparehash.service", True),
                    self.tree.receipt("/etc/systemd/system/sparehash-xmrig.service", True),
                    self.tree.receipt(CONFIG, True),
                    UserIdentity("user", "xmrig", 996, 996, True),
                    GroupIdentity("group", "sparehash", 12345, True))
        ledger.applied(0, receipts)

    def run_uninstall(self, *, purge: bool = False, root: int = 0, empty: bool = True,
                      legacy: bool = False, build: bool = True, stop_failure: bool = False,
                      groupdel_exit: int | None = None) -> tuple[int, Client, FakeInfrastructure]:
        """Use a private tree and reject every controller call, including abort/release."""
        if build:
            self.build(legacy=legacy)
        client = forbidden_client
        fake = FakeInfrastructure(empty)

        def run(argv: tuple[str, ...]) -> None:
            # Record the call first so ordering survives the failure.
            fake.calls.append(argv)
            self.assertTrue(lifecycle.is_retired(self.root / "lifecycle.json"))
            if argv[:2] in (("/usr/bin/systemctl", "disable"), ("/usr/bin/systemctl", "stop")):
                self.assertTrue(self.tree.path(PACKAGE_LEAF).exists())
            if stop_failure and argv[:2] == ("/usr/bin/systemctl", "stop"):
                raise DeploymentError("helper", "injected stop failure")
            if groupdel_exit is not None and argv[0] == "/usr/sbin/groupdel":
                raise DeploymentError("helper", f"{argv[0]} exited {groupdel_exit}: groupdel: fixture failure")

        infrastructure = cli_uninstall.Infrastructure(run=run, cgroup_empty=fake.cgroup_empty,
                                                      free_hugepages=fake.free_hugepages, unmerge=fake.unmerge)
        result = cli_uninstall.uninstall(purge=purge, geteuid=lambda: root, trust=self.tree.trust,
                                         client=client, infrastructure=infrastructure, request_id=REQUEST,
                                         lifecycle_path=self.root / "lifecycle.json")
        return result, client, fake

    def commands(self, fake: FakeInfrastructure) -> list[tuple[str, ...]]:
        return [call for call in fake.calls if call[0] not in {"hugepages", "empty"}]

    def test_removes_receipt_owned_artifacts_and_retains_config(self) -> None:
        # Given a receipted install
        # When uninstalling without purge
        result, _client, fake = self.run_uninstall()
        # Then only receipt-owned artifacts are gone and config is kept
        self.assertEqual(result, 0)
        self.assertFalse(self.tree.path(PACKAGE_LEAF).exists())
        self.assertFalse(self.tree.path("/opt/sparehash").exists())
        self.assertFalse(self.tree.path("/usr/local/bin/sparehash").exists())
        self.assertFalse(self.tree.path("/etc/systemd/system/sparehash.service").exists())
        self.assertTrue(self.tree.path(CONFIG).exists())
        self.assertEqual(fake.calls[:4], [("/usr/bin/systemctl", "disable", "--now", "sparehash.service"),
                                          ("/usr/bin/systemctl", "stop", "sparehash-xmrig.service"),
                                          ("empty",), ("hugepages",)])
        calls = self.commands(fake)
        self.assertIn(("hugepages",), fake.calls)
        self.assertIn(("/usr/sbin/userdel", "xmrig"), calls)
        self.assertIn(("/usr/sbin/groupdel", "sparehash"), calls)
        self.assertEqual(calls[-1], ("/usr/bin/systemctl", "daemon-reload"))

    def test_purge_also_removes_config(self) -> None:
        # Given a receipted install
        # When uninstalling with purge
        result, _client, _fake = self.run_uninstall(purge=True)
        # Then the configuration is removed too
        self.assertEqual(result, 0)
        self.assertFalse(self.tree.path(CONFIG).exists())

    def test_retains_artifacts_when_cgroup_is_not_empty(self) -> None:
        # Given a miner cgroup that is not empty
        # When uninstalling
        result, _client, fake = self.run_uninstall(empty=False)
        # Then nothing destructive runs after the failed proof
        self.assertEqual(result, 1)
        self.assertTrue(self.tree.path(PACKAGE_LEAF).exists())
        self.assertTrue(self.tree.path("/etc/systemd/system/sparehash.service").exists())
        self.assertNotIn(("/usr/bin/systemctl", "daemon-reload"), self.commands(fake))
        self.assertNotIn(("/usr/sbin/userdel", "xmrig"), self.commands(fake))
        self.assertTrue(lifecycle.is_retired(self.root / "lifecycle.json"))
        self.assertNotIn(("hugepages",), fake.calls)

    def test_refuses_legacy_install_without_receipts(self) -> None:
        # Given a legacy manifest without an installer section
        # When uninstalling
        result, _client, fake = self.run_uninstall(legacy=True)
        # Then it refuses before retirement or privileged effects
        self.assertEqual(result, 1)
        self.assertEqual(fake.calls, [])
        self.assertFalse((self.root / "lifecycle.json").exists())
        self.assertTrue(self.tree.path(PACKAGE_LEAF).exists())

    def test_requires_root_before_any_removal(self) -> None:
        # Given an unprivileged caller
        # When uninstalling
        result, _client, fake = self.run_uninstall(root=1000)
        # Then it refuses with no IPC and no privileged call
        self.assertEqual(result, 2)
        self.assertFalse((self.root / "lifecycle.json").exists())
        self.assertEqual(fake.calls, [])

    def test_userdel_runs_before_groupdel(self) -> None:
        # Given a receipted install with created user and group
        # When uninstalling
        result, _client, fake = self.run_uninstall()
        # Then users are removed before their primary groups
        self.assertEqual(result, 0)
        calls = self.commands(fake)
        userdel_index = next((i for i, call in enumerate(calls) if call[0] == "/usr/sbin/userdel"), -1)
        groupdel_index = next((i for i, call in enumerate(calls) if call[0] == "/usr/sbin/groupdel"), -1)
        self.assertNotEqual(userdel_index, -1)
        self.assertNotEqual(groupdel_index, -1)
        self.assertLess(userdel_index, groupdel_index)

    def test_tolerates_groupdel_exit_six_when_userdel_removed_primary_group(self) -> None:
        # Given a receipted install where userdel already removed the primary group
        # When uninstalling and groupdel reports exit 6
        result, _client, fake = self.run_uninstall(groupdel_exit=6)
        # Then the already-removed group is tolerated, in order, and the run completes
        self.assertEqual(result, 0)
        calls = self.commands(fake)
        userdel_index = next((i for i, call in enumerate(calls) if call[0] == "/usr/sbin/userdel"), -1)
        groupdel_index = next((i for i, call in enumerate(calls) if call[0] == "/usr/sbin/groupdel"), -1)
        self.assertNotEqual(userdel_index, -1)
        self.assertNotEqual(groupdel_index, -1)
        self.assertLess(userdel_index, groupdel_index)
        self.assertEqual(calls[-1], ("/usr/bin/systemctl", "daemon-reload"))
        self.assertTrue((self.root / "lifecycle.json").is_file())

    def test_groupdel_exit_one_is_propagated(self) -> None:
        # Given a groupdel failure that is not the already-removed case
        # When uninstalling
        result, _client, fake = self.run_uninstall(groupdel_exit=1)
        # Then the failure stops the run before daemon-reload but retains retirement
        self.assertEqual(result, 1)
        calls = self.commands(fake)
        self.assertIn(("/usr/sbin/userdel", "xmrig"), calls)
        self.assertNotIn(("/usr/bin/systemctl", "daemon-reload"), calls)
        self.assertTrue(lifecycle.is_retired(self.root / "lifecycle.json"))

    def test_groupdel_exit_sixty_is_not_tolerated(self) -> None:
        # Given a groupdel failure whose exit code merely starts with 6
        # When uninstalling
        result, _client, fake = self.run_uninstall(groupdel_exit=60)
        # Then only exit 6 is tolerated; exit 60 must stop the uninstall
        self.assertEqual(result, 1, "exit 60 was tolerated as the already-removed group: the tolerance check matches the prefix of the exit code, not the code itself")
        self.assertNotIn(("/usr/bin/systemctl", "daemon-reload"), self.commands(fake))
        self.assertTrue(lifecycle.is_retired(self.root / "lifecycle.json"))

    def test_stopped_controller_removal_succeeds_without_ipc(self) -> None:
        # Given a fresh install with no sidecar and no controller cooperation
        # When
        result, _client, _fake = self.run_uninstall()
        # Then removal succeeds without manufacturing a lease
        self.assertEqual(result, 0)
        self.assertFalse(self.tree.path(PACKAGE_LEAF).exists())
        self.assertFalse((self.root / "lifecycle-lease.json").exists())

    def test_running_controller_removal_retains_existing_uninstall_hold(self) -> None:
        # Given a running controller's durable uninstall hold
        store = LeaseStore(self.root / "lifecycle-lease.json")
        hold = ActiveLease(TOKEN, REQUEST, "uninstall")
        store.save(hold)
        # When root stops services without asking the controller
        result, _client, fake = self.run_uninstall()
        # Then removal succeeds, preserving the exact hold
        self.assertEqual(result, 0)
        self.assertEqual(store.load(), hold)
        self.assertEqual(fake.calls[1], ("/usr/bin/systemctl", "stop", "sparehash-xmrig.service"))

    def test_refuses_untrusted_or_update_hold_before_any_effect(self) -> None:
        # Given either a competing hold or an unreadable/corrupt sidecar
        path = self.root / "lifecycle-lease.json"
        for state in ("update", "corrupt", "unreadable"):
            with self.subTest(state=state):
                if state == "update":
                    LeaseStore(path).save(ActiveLease(TOKEN, REQUEST, "update"))
                elif state == "corrupt":
                    _ = path.write_text("{", encoding="utf-8")
                else:
                    path.unlink()
                    path.mkdir()
                errors = io.StringIO()
                # When (real LeaseStore parsing, including real read OSError)
                with mock.patch.object(cli_uninstall, "retire") as retirement, redirect_stderr(errors):
                    result, _client, fake = self.run_uninstall()
                # Then no retirement, service action, or removal occurs
                self.assertEqual(result, 1)
                retirement.assert_not_called()
                self.assertEqual(fake.calls, [])
                self.assertTrue(self.tree.path(PACKAGE_LEAF).exists())
                self.assertTrue(self.tree.path("/usr/local/bin/sparehash").exists())
                self.assertTrue(errors.getvalue().startswith("sparehash: uninstall: "))
                self.assertFalse((self.root / "lifecycle.json").exists())

    def test_retirement_failure_refuses_before_service_effects(self) -> None:
        # Given a failing durable retirement write
        errors = io.StringIO()
        # When
        with (mock.patch.object(cli_uninstall, "retire", side_effect=OSError("injected persistence failure")),
              redirect_stderr(errors)):
            result, _client, fake = self.run_uninstall()
        # Then artifacts remain and no systemctl command ran
        self.assertEqual(result, 1)
        self.assertEqual(fake.calls, [])
        self.assertTrue(self.tree.path(PACKAGE_LEAF).exists())
        self.assertIn("injected persistence failure", errors.getvalue())

    def test_failed_stop_retains_artifacts_and_latch_then_offline_retry_succeeds(self) -> None:
        # Given a stop helper that fails before removal
        errors = io.StringIO()
        # When
        with redirect_stderr(errors):
            result, _client, fake = self.run_uninstall(stop_failure=True)
        # Then retirement and artifacts survive, with no proof/cleanup attempted
        self.assertEqual(result, 1)
        self.assertTrue(lifecycle.is_retired(self.root / "lifecycle.json"))
        self.assertTrue(self.tree.path(PACKAGE_LEAF).exists())
        self.assertTrue(self.tree.path("/etc/systemd/system/sparehash.service").exists())
        self.assertEqual(len(fake.calls), 2)
        self.assertIn("injected stop failure", errors.getvalue())
        # When retrying the same receipts without rebuilding or controller IPC
        result, _client, _fake = self.run_uninstall(build=False)
        # Then the retry completes while still retired
        self.assertEqual(result, 0)
        self.assertTrue(lifecycle.is_retired(self.root / "lifecycle.json"))

    def test_foreign_and_tampered_package_content_is_preserved(self) -> None:
        # Given an installed tree with either foreign or hash-mismatched content
        for foreign in (True, False):
            with self.subTest(foreign=foreign):
                self.build()
                path = self.tree.path(PACKAGE_DIR + "/foreign.py" if foreign else PACKAGE_LEAF)
                _ = path.write_bytes(b"foreign or tampered")
                # When
                result, _client, fake = self.run_uninstall(build=False)
                # Then receipt preflight preserves the content and the retired state
                self.assertEqual(result, 1)
                self.assertEqual(path.read_bytes(), b"foreign or tampered")
                self.assertTrue(self.tree.path(PACKAGE_LEAF).exists())
                self.assertTrue(lifecycle.is_retired(self.root / "lifecycle.json"))
                self.assertNotIn(("/usr/sbin/userdel", "xmrig"), fake.calls)
                if foreign:
                    path.unlink()

if __name__ == "__main__":
    _ = unittest.main()
