"""Installer fixture integration; privileged/helper boundaries never touch the host."""

from __future__ import annotations

import grp
import os
import pwd
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import final
from unittest import mock

from sparehash import config, install, systemd, xmrig_config
from sparehash.deployment import DeploymentError, Tree
from sparehash.install_manifest import StoreContext, locked_store
from sparehash.sysdetect import Capabilities
from sparehash.xmrig_ledger import mapping
from sparehash.xmrig_manifest import parse_manifest
from sparehash.deployment import LOAD_JSON
from tests.install_runtime import RuntimeTests as RuntimeTests


def capabilities() -> Capabilities:
    return Capabilities("x86_64", True, 257, True, "cgroup2", True, False, False,
                        (0,), (), 2, 8 * 1024**3, None, None, False)


@final
class InstallTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tree = Tree(self.root, os.getuid())
        for name in ("etc/systemd/system", "var/lib", "opt/sparehash/src", "usr/local/bin"):
            (self.root / name).mkdir(parents=True, mode=0o755)
        for path in self.root.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)
        self.calls: list[tuple[str, ...]] = []
        self.cfg = config.Config(wallet="4" + "1" * 94, pool=config.Pool(host="127.0.0.1"),
                                 gaming=config.Gaming(enabled=False))

    def checked(self, argv: tuple[str, ...], timeout: float = 10) -> str:
        self.assertTrue(0 < timeout <= 60)
        self.calls.append(argv)
        if argv[1] == "is-enabled":
            return "static" if argv[2] == "sparehash-xmrig.service" else "enabled"
        return ""

    def execute(self, cfg: config.Config | None = None, verify_error: systemd.UnitError | None = None) -> None:
        original = os.fchown
        def chown(fd: int, uid: int, gid: int) -> None:
            _ = uid, gid
            original(fd, os.getuid(), os.getgid())
        account = pwd.struct_passwd(("xmrig", "x", 996, os.getgid(), "", "/nonexistent", "/usr/sbin/nologin"))
        def group(name: str) -> grp.struct_group:
            return grp.struct_group((name, "x", os.getgid(), []))
        def choose(cfg: config.Config, caps: Capabilities, consent: bool) -> config.Config:
            _ = caps, consent
            return cfg
        with (mock.patch("sparehash.install.pwd.getpwnam", return_value=account),
              mock.patch("sparehash.install.grp.getgrnam", side_effect=group),
              mock.patch("sparehash.install.os.fchown", side_effect=chown),
              mock.patch.object(install, "choose", side_effect=choose),
              mock.patch.object(install, "isolated", return_value=(0, "XMRig 6.26.0")),
              mock.patch.object(systemd, "verify_units", return_value="", side_effect=verify_error),
              mock.patch.object(xmrig_config, "validate_rendered"),
              mock.patch.object(install, "checked", side_effect=self.checked)):
            install.execute(self.tree, capabilities(), cfg or self.cfg)

    def test_ready_install_when_fresh(self) -> None:
        # Given / When
        self.execute()
        # Then
        manifest = parse_manifest(self.tree.trust.manifest_path.read_text())
        assert manifest is not None and manifest.installer is not None
        self.assertEqual(manifest.installer.phase, "ready")
        self.assertTrue(all(item.state == "applied" for item in manifest.installer.actions))
        self.assertEqual(len(manifest.installer.identities), 3)
        self.assertIn(("/usr/bin/systemctl", "enable", "sparehash.service"), self.calls)
        self.assertFalse(any(argv[1] in {"start", "enable"} and "sparehash-xmrig.service" in argv for argv in self.calls))

    def test_token_and_config_preserved_when_rerun(self) -> None:
        # Given
        self.execute()
        token = xmrig_config.read_token(self.tree.path("/etc/sparehash/xmrig.json"))
        prior = self.tree.path("/etc/sparehash/config.toml").read_bytes()
        # When
        self.execute()
        # Then
        self.assertEqual(xmrig_config.read_token(self.tree.path("/etc/sparehash/xmrig.json")), token)
        self.assertEqual(self.tree.path("/etc/sparehash/config.toml").read_bytes(), prior)
        self.assertNotIn(token, self.tree.trust.manifest_path.read_text())

    def test_token_refused_when_established_credential_is_missing(self) -> None:
        # Given
        self.execute()
        self.tree.path("/etc/sparehash/xmrig.json").unlink()
        # When / Then
        with self.assertRaises(DeploymentError):
            self.execute()

    def test_token_refused_when_credential_is_garbled(self) -> None:
        # Given
        self.execute()
        _ = self.tree.path("/etc/sparehash/xmrig.json").write_text("{}")
        # When / Then
        with self.assertRaises(DeploymentError):
            self.execute()

    def test_no_units_when_analyzer_rejects(self) -> None:
        # Given
        # When / Then
        with self.assertRaises(systemd.UnitError):
            self.execute(verify_error=systemd.UnitError("fixture analyzer failure"))
        self.assertFalse(self.tree.path("/etc/systemd/system/sparehash.service").exists())
        self.assertFalse(any(argv[1] == "enable" for argv in self.calls))

    def test_refusal_when_root_or_platform_preflight_fails(self) -> None:
        # Given / When / Then
        self.assertEqual(install.main([], geteuid=lambda: 1000), 1)
        for caps in (replace(capabilities(), python_supported=False), replace(capabilities(), systemd_pid1=False),
                     replace(capabilities(), architecture="aarch64")):
            with self.subTest(caps=caps), self.assertRaises(DeploymentError):
                install.preflight(self.tree, caps)

    def test_foreign_unit_untouched_when_collision_occurs(self) -> None:
        # Given
        unit = self.tree.path("/etc/systemd/system/sparehash.service")
        _ = unit.write_bytes(b"[Service]\nExecStart=/usr/bin/true\n")
        # When / Then
        with self.assertRaises(systemd.CollisionError):
            install.preflight(self.tree, capabilities())
        self.assertEqual(unit.read_bytes(), b"[Service]\nExecStart=/usr/bin/true\n")
        self.assertFalse(self.tree.trust.manifest_path.exists())

    def test_login_identity_refused_when_xmrig_is_not_dedicated(self) -> None:
        # Given
        account = pwd.struct_passwd(("xmrig", "x", 1001, 1001, "", "/home/xmrig", "/bin/bash"))
        # When / Then
        with mock.patch("sparehash.install.pwd.getpwnam", return_value=account), self.assertRaises(DeploymentError):
            install.preflight(self.tree, capabilities())

    def test_pending_action_retained_when_mutation_is_interrupted(self) -> None:
        # Given
        from sparehash.install_ledger import Ledger
        ledger = Ledger(self.tree)
        ledger.initialize()
        # When / Then
        with self.assertRaises(KeyboardInterrupt):
            ledger.perform(("/etc/sparehash", "create-directory"), lambda: (_ for _ in ()).throw(KeyboardInterrupt))
        document = mapping(LOAD_JSON(self.tree.trust.manifest_path.read_bytes()))
        installer = mapping(document["installer"])
        self.assertEqual(installer["phase"], "installing")
        with locked_store(StoreContext(self.tree.trust)) as store:
            snapshot = store.read()
            assert snapshot is not None and snapshot.manifest.installer is not None
            self.assertEqual(snapshot.manifest.installer.actions[-1].state, "pending")

    def test_loopback_and_msr_config_when_published(self) -> None:
        # Given / When
        self.execute()
        # Then
        document = mapping(LOAD_JSON(self.tree.path("/etc/sparehash/xmrig.json").read_bytes()))
        self.assertEqual(mapping(document["http"])["host"], "127.0.0.1")
        self.assertEqual(mapping(document["randomx"])["wrmsr"], False)
        self.assertEqual(mapping(document["randomx"])["rdmsr"], False)
        self.assertEqual(self.tree.path("/etc/sparehash/xmrig.runtime.json").read_bytes(), self.tree.path("/etc/sparehash/xmrig.json").read_bytes())

    def test_gaming_receipts_when_existing_user_configuration_is_merged(self) -> None:
        # Given
        from sparehash import gaming_install
        from sparehash.install_ledger import Ledger
        home = self.root / "home/gamer"
        (home / ".config").mkdir(parents=True)
        ini = home / ".config/gamemode.ini"
        _ = ini.write_text("[general]\nrenice=10\n")
        account = pwd.struct_passwd(("gamer", "x", os.getuid(), os.getgid(), "", str(home), "/bin/bash"))
        group = grp.struct_group(("sparehash", "x", os.getgid(), ["gamer"]))
        cfg = replace(self.cfg, gaming=config.Gaming(True, ("gamer",)))
        caps = replace(capabilities(), helper_paths=(("gamemoded", "/usr/bin/gamemoded"),))
        ledger = Ledger(self.tree)
        with mock.patch("sparehash.gaming_install.grp.getgrnam", return_value=group):
            ledger.initialize()
            with mock.patch("sparehash.gaming_install.pwd.getpwnam", return_value=account), mock.patch.object(gaming_install, "resolve_helper", return_value="/usr/bin/gamemoded"):
                # When
                result = gaming_install.configure(cfg, caps, lambda _: False, ledger=ledger)
        # Then
        self.assertTrue(result.available)
        self.assertEqual(len(result.receipts), 3)
        manifest = parse_manifest(self.tree.trust.manifest_path.read_text())
        assert manifest is not None and manifest.installer is not None
        self.assertEqual({artifact.id for artifact in manifest.installer.artifacts}, {"gaming-ini:gamer", "gaming-backup:gamer"})
        self.assertFalse(manifest.installer.memberships[0].added)
        self.assertEqual(manifest.installer.actions[-1].state, "applied")
