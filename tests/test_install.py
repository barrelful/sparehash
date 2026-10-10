"""Installer fixture integration; privileged/helper boundaries never touch the host."""

from __future__ import annotations

import grp
import os
import pwd
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import final
from unittest import mock

from sparehash import config, install, systemd, xmrig_config
from sparehash.deployment import LOAD_JSON, DeploymentError
from sparehash.install_manifest import StoreContext, locked_store
from sparehash.sysdetect import Capabilities
from sparehash.xmrig_ledger import mapping
from sparehash.xmrig_manifest import parse_manifest
from tests.install_fixture import InstallFixture
from tests.install_fixture import capabilities as capabilities
from tests.install_runtime import RuntimeTests as RuntimeTests
from tests.install_startup import StartupTests as StartupTests


@final
class InstallTests(InstallFixture):
    def test_gaming_consent_when_interactive_retries_and_honors_answer(self) -> None:
        # Given
        cfg = replace(self.cfg, gaming=config.Gaming(True))
        with mock.patch("sparehash.gaming_install.resolve_helper", return_value=None), mock.patch(
            "builtins.input", side_effect=["perhaps", " NO ", "", ""]
        ) as prompt:
            # When
            self.execute(cfg, interactive=True)
        # Then
        self.assertEqual(prompt.call_count, 4)
        self.assertFalse(config.load(self.tree.path("/etc/sparehash/config.toml")).gaming.enabled)

    def test_gaming_consent_when_noninteractive_never_reads_stdin(self) -> None:
        # Given
        cfg = replace(self.cfg, gaming=config.Gaming(True))
        with mock.patch("sparehash.gaming_install.resolve_helper", return_value=None), mock.patch(
            "builtins.input", side_effect=AssertionError("uncontrolled stdin")
        ):
            # When
            self.execute(cfg, interactive=False)
        # Then
        self.assertFalse(config.load(self.tree.path("/etc/sparehash/config.toml")).gaming.enabled)

    def test_gaming_consent_when_enter_pressed_accepts_install(self) -> None:
        # Given
        cfg = replace(self.cfg, gaming=config.Gaming(True))
        with mock.patch("sparehash.gaming_install.resolve_helper", side_effect=lambda name: f"/usr/bin/{name}"), mock.patch(
            "sparehash.gaming_install._run"
        ) as run, mock.patch("builtins.input", return_value=""):
            # When
            self.execute(cfg, interactive=True)
        # Then
        self.assertEqual(run.call_args.args[0], ("/usr/bin/apt-get", "install", "-y", "gamemode"))
        self.assertTrue(config.load(self.tree.path("/etc/sparehash/config.toml")).gaming.enabled)

    def test_main_when_answers_supplied_disables_interactive_consent(self) -> None:
        for argv, interactive in (([], True), (["--answers", "answers.toml"], False)):
            # Given
            with self.subTest(interactive=interactive), mock.patch.object(install, "is_retired", return_value=False), mock.patch.object(
                install.platform, "machine", return_value="x86_64"
            ), mock.patch.object(install, "Tree", return_value=self.tree), mock.patch.object(
                install, "preflight"
            ), mock.patch.object(Capabilities, "detect", return_value=capabilities()), mock.patch.object(
                install.wizard, "load_answers", return_value=self.cfg
            ), mock.patch.object(install.wizard, "run_interactive", return_value=self.cfg), mock.patch.object(
                install, "execute"
            ) as execute:
                # When
                code = install.main(argv, geteuid=lambda: 0)
                # Then
                self.assertEqual(code, 0)
                self.assertEqual(execute.call_args.kwargs["interactive"], interactive)

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

    def test_shim_suppresses_bytecode_when_executed(self) -> None:
        # Given the exact PYTHON_SHIM block install.sh writes
        install_sh = Path(__file__).resolve().parents[1] / "install.sh"
        text = install_sh.read_text()
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", text)
        self.assertIn("sys.dont_write_bytecode = True", text)
        marker = "<<'PYTHON_SHIM'\n"
        start = text.index(marker) + len(marker)
        shim = text[start:text.index("\nPYTHON_SHIM", start)]
        self.assertIn("sys.dont_write_bytecode = True", shim)
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            package_directory = temporary_path / "sparehash"
            package_directory.mkdir()
            _ = (package_directory / "__init__.py").write_text("# sparehash package marker\n")
            _ = (package_directory / "__main__.py").write_text(
                "import os, sys\n"
                "from pathlib import Path\n"
                "def main() -> int:\n"
                "    Path(os.environ['SPAREHASH_TEST_MARKER']).write_text(\n"
                "        f'{sys.dont_write_bytecode}')\n"
                "    return 0\n"
            )
            marker_path = temporary_path / "marker"
            script = temporary_path / "shim"
            # When the shim runs as its own child process, twice: once with the
            # install.sh environment and once with bytecode writing left on.
            for environment in ({**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                                {key: value for key, value in os.environ.items()
                                 if key != "PYTHONDONTWRITEBYTECODE"}):
                with self.subTest(dont_write_bytecode=environment.get("PYTHONDONTWRITEBYTECODE")):
                    script.write_text(shim.replace('"/opt/sparehash/src"', repr(str(temporary_path))))
                    marker_path.unlink(missing_ok=True)
                    result = subprocess.run(
                        [sys.executable, str(script)], capture_output=True, text=True,
                        env={**environment, "SPAREHASH_TEST_MARKER": str(marker_path)},
                        check=False, timeout=60)
                    # Then the shim runs, reports bytecode suppression, and writes nothing but the marker
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(marker_path.read_text(), "True")
                    self.assertFalse(list(temporary_path.rglob("__pycache__")))
                    self.assertFalse(list(temporary_path.rglob("*.pyc")))

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
            with mock.patch("sparehash.gaming_install.pwd.getpwnam", return_value=account), mock.patch.object(gaming_install, "resolve_helper", return_value="/usr/bin/gamemoded"), mock.patch("sparehash.gaming.Probe.query", return_value=False):
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
