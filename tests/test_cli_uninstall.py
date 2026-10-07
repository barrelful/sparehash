"""Root-only uninstall ordering and fail-closed behavior on a private tree."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import final

from sparehash import cli_uninstall, install, ipc
from sparehash.deployment import Tree
from sparehash.install_ledger import Ledger
from sparehash.install_manifest import locked_store
from sparehash.xmrig_ledger import PACKAGE_DIR, GroupIdentity, UserIdentity
from sparehash.xmrig_manifest import Manifest

TOKEN = "a" * 64
REQUEST = "b" * 32
PACKAGE_LEAF = PACKAGE_DIR + "/alpha.py"
CONFIG = "/etc/sparehash/config.toml"


@final
class FakeClient:
    def __init__(self, responses: list[ipc.Document]) -> None:
        self.responses = responses
        self.commands: list[ipc.Document] = []

    def __call__(self, command: ipc.Document, /, *, timeout: float) -> ipc.Document:
        _ = timeout
        self.commands.append(command)
        return self.responses.pop(0) if self.responses else {"ok": False, "error": "exhausted"}


@final
class FakeInfrastructure:
    def __init__(self, empty: bool = True) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.empty = empty

    def run(self, argv: tuple[str, ...]) -> None:
        self.calls.append(argv)

    def cgroup_empty(self) -> bool:
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
                      legacy: bool = False) -> tuple[int, FakeClient, FakeInfrastructure]:
        self.build(legacy=legacy)
        client = FakeClient([{"ok": True, "token": TOKEN}, {"ok": True, "status": "ready"}, {"ok": True}])
        fake = FakeInfrastructure(empty)
        infrastructure = cli_uninstall.Infrastructure(run=fake.run, cgroup_empty=fake.cgroup_empty,
                                                      free_hugepages=fake.free_hugepages, unmerge=fake.unmerge)
        result = cli_uninstall.uninstall(purge=purge, geteuid=lambda: root, trust=self.tree.trust,
                                         client=client, infrastructure=infrastructure, request_id=REQUEST)
        return result, client, fake

    def commands(self, fake: FakeInfrastructure) -> list[tuple[str, ...]]:
        return [call for call in fake.calls if call[0] != "hugepages"]

    def test_removes_receipt_owned_artifacts_and_retains_config(self) -> None:
        # Given a receipted install
        # When uninstalling without purge
        result, client, fake = self.run_uninstall()
        # Then only receipt-owned artifacts are gone and config is kept
        self.assertEqual(result, 0)
        self.assertFalse(self.tree.path(PACKAGE_LEAF).exists())
        self.assertFalse(self.tree.path("/opt/sparehash").exists())
        self.assertFalse(self.tree.path("/usr/local/bin/sparehash").exists())
        self.assertFalse(self.tree.path("/etc/systemd/system/sparehash.service").exists())
        self.assertTrue(self.tree.path(CONFIG).exists())
        self.assertEqual([command["cmd"] for command in client.commands],
                         ["begin-update", "update-status", "end-update"])
        self.assertEqual(client.commands[0]["purpose"], "uninstall")
        self.assertEqual(client.commands[-1]["outcome"], "commit")
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

    def test_refuses_legacy_install_without_receipts(self) -> None:
        # Given a legacy manifest without an installer section
        # When uninstalling
        result, client, _fake = self.run_uninstall(legacy=True)
        # Then it refuses before acquiring any lease
        self.assertEqual(result, 1)
        self.assertEqual(client.commands, [])
        self.assertTrue(self.tree.path(PACKAGE_LEAF).exists())

    def test_requires_root_before_any_removal(self) -> None:
        # Given an unprivileged caller
        # When uninstalling
        result, client, fake = self.run_uninstall(root=1000)
        # Then it refuses with no IPC and no privileged call
        self.assertEqual(result, 2)
        self.assertEqual(client.commands, [])
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    _ = unittest.main()
