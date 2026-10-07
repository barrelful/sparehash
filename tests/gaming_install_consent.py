import grp
import os
import pwd
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import final
from unittest.mock import Mock, patch

from sparehash import gaming_install as install
from sparehash.config import Config, Gaming
from sparehash.gaming import User
from sparehash.sysdetect import Capabilities, FIXED_PATH


@final
class ConsentTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        (self.home / ".config").mkdir(parents=True)
        self.account = pwd.struct_passwd(("gamer", "x", os.getuid(), os.getgid(), "", str(self.home), "/bin/sh"))
        self.group = grp.struct_group(("sparehash", "x", os.getgid(), []))
        self.cfg = replace(Config(), gaming=Gaming(enabled=True, users=("gamer",)))
        self.caps = Capabilities("x86_64", True, 250, True, "cgroup2", True, True,
                                 False, (0,), (("gamemoded", "/usr/bin/gamemoded"), ("steam", "/usr/games/steam")), 4, 1000, None, None, False)
        self.runner = Mock(return_value=subprocess.CompletedProcess((), 0, ""))
    def accounts(self) -> None:
        for patcher in (patch.object(pwd, "getpwnam", return_value=self.account),
                        patch.object(grp, "getgrnam", return_value=self.group)):
            _ = patcher.start()
            self.addCleanup(patcher.stop)

    def test_detection_when_inventory_present_reports_gamemode_and_steam(self) -> None:
        # Given / When / Then
        self.assertEqual(install.detect_gaming(self.caps), {"gamemode": True, "steam": True})

    def test_consent_when_declined_disables_without_commands_or_home_edits(self) -> None:
        # Given
        self.accounts()
        consent = Mock(return_value=False)
        with patch.object(install, "resolve_helper", return_value=None):
            # When
            result = install.configure(self.cfg, self.caps, consent, runner=self.runner)
        # Then
        self.assertFalse(result.config.gaming.enabled)
        self.assertTrue(result.warnings)
        self.runner.assert_not_called()
        self.assertEqual(list((self.home / ".config").iterdir()), [])

    def test_install_when_detected_manager_available_uses_only_fixed_argv(self) -> None:
        # Given / When / Then
        self.accounts()
        for manager, args in install.INSTALL_ARGS:
            installed = False
            def resolve(name: str) -> str | None:
                return f"/usr/bin/{name}" if name == manager or (name == "gamemoded" and installed) else None
            def run(argv: tuple[str, ...], **_kwargs: str | float | bool) -> subprocess.CompletedProcess[str]:
                nonlocal installed
                installed = True
                return subprocess.CompletedProcess(argv, 0, "")
            runner = Mock(side_effect=run)
            with patch.object(install, "resolve_helper", side_effect=resolve):
                result = install.configure(self.cfg, self.caps, lambda _: True, runner=runner)
            self.assertTrue(result.available)
            runner.assert_called_once_with((f"/usr/bin/{manager}", *args), timeout=300, check=False,
                                           capture_output=True, text=True, env={"PATH": FIXED_PATH, "LC_ALL": "C"})

    def test_install_when_only_query_binary_exists_disables(self) -> None:
        # Given
        self.accounts()
        def resolve(name: str) -> str | None:
            return "/usr/bin/dpkg-query" if name == "dpkg-query" else None
        with patch.object(install, "resolve_helper", side_effect=resolve):
            # When
            result = install.configure(self.cfg, self.caps, lambda _: True, runner=self.runner)
        # Then
        self.assertFalse(result.available)
        self.assertTrue(result.warnings)
        self.runner.assert_not_called()

    def test_install_when_helper_failure_or_absent_after_install_disables(self) -> None:
        # Given / When / Then
        self.accounts()
        for runner in (Mock(return_value=subprocess.CompletedProcess((), 1, "")),
                       Mock(return_value=subprocess.CompletedProcess((), 0, "")),
                       Mock(side_effect=subprocess.TimeoutExpired("apt-get", 300))):
            def resolve(name: str) -> str | None:
                return "/usr/bin/apt-get" if name == "apt-get" else None
            with patch.object(install, "resolve_helper", side_effect=resolve):
                result = install.configure(self.cfg, self.caps, lambda _: True, runner=runner)
            self.assertFalse(result.config.gaming.enabled)
            self.assertTrue(result.warnings)

    def test_configure_when_home_missing_skips_with_warning(self) -> None:
        # Given
        self.accounts()
        (self.home / ".config").rmdir()
        self.home.rmdir()
        with patch.object(install, "resolve_helper", return_value="/usr/bin/gamemoded"):
            # When
            result = install.configure(self.cfg, self.caps, lambda _: False, runner=self.runner)
        # Then
        self.assertFalse(result.available)
        self.assertTrue(result.warnings)
        self.runner.assert_not_called()

    def test_configure_when_home_unsafe_disables_without_clobber(self) -> None:
        # Given
        self.accounts()
        target = self.home / ".config" / "gamemode.ini"
        sentinel = self.home / "sentinel"
        _ = sentinel.write_bytes(b"unrelated")
        target.symlink_to(sentinel)
        with patch.object(install, "resolve_helper", return_value="/usr/bin/gamemoded"):
            # When
            result = install.configure(self.cfg, self.caps, lambda _: False, runner=self.runner)
        # Then
        self.assertFalse(result.available)
        self.assertEqual(sentinel.read_bytes(), b"unrelated")

    def test_configure_when_config_directory_missing_skips_without_creating_it(self) -> None:
        # Given
        self.accounts()
        (self.home / ".config").rmdir()
        with patch.object(install, "resolve_helper", return_value="/usr/bin/gamemoded"):
            # When
            result = install.configure(self.cfg, self.caps, lambda _: False, runner=self.runner)
        # Then
        self.assertFalse(result.available)
        self.assertTrue(result.warnings)
        self.assertFalse((self.home / ".config").exists())

    def test_configure_when_membership_missing_uses_fixed_usermod(self) -> None:
        # Given
        self.accounts()
        self.group = grp.struct_group(("sparehash", "x", os.getgid() + 1, []))
        def resolve(name: str) -> str:
            return f"/usr/bin/{name}"
        with patch.object(grp, "getgrnam", return_value=self.group), patch.object(
            install, "resolve_helper", side_effect=resolve
        ):
            # When
            result = install.configure(self.cfg, self.caps, lambda _: False, runner=self.runner)
        # Then
        self.assertTrue(result.available)
        self.assertEqual(self.runner.call_args.args[0], ("/usr/bin/usermod", "-aG", "sparehash", "gamer"))

    def test_unmerge_when_account_verified_removes_only_owned_file(self) -> None:
        # Given
        self.accounts()
        user = User.from_name("gamer")
        _ = install.merge(user)
        # When
        _ = install.unmerge(user)
        # Then
        self.assertFalse((self.home / ".config" / "gamemode.ini").exists())

    def test_configure_when_disabled_does_not_probe_or_run(self) -> None:
        # Given
        cfg = replace(self.cfg, gaming=replace(self.cfg.gaming, enabled=False))
        with patch.object(install, "resolve_helper") as helper:
            # When
            result = install.configure(cfg, self.caps, lambda _: True, runner=self.runner)
        # Then
        self.assertEqual(result.config, cfg)
        helper.assert_not_called()
        self.runner.assert_not_called()
