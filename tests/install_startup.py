"""Startup choices through the installer using a private tree and fake systemd."""

import io
from contextlib import redirect_stdout
from unittest import mock

from sparehash import install, ipc
from sparehash.deployment import DeploymentError
from tests.install_fixture import InstallFixture


class StartupTests(InstallFixture):
    def test_defaults_when_interactive_enter_pressed(self) -> None:
        # Given
        with mock.patch("builtins.input", side_effect=["", ""]) as prompt:
            # When
            self.execute(interactive=True)
        # Then
        self.assertEqual(prompt.call_count, 2)
        self.assertTrue(self.enabled)
        self.assertTrue(self.active)
        self.assertEqual([argv[1:] for argv in self.calls if argv[1] in ("daemon-reload", "enable", "start")],
                         [("daemon-reload",), ("enable", "sparehash.service"), ("start", "sparehash.service")])

    def test_disabled_boot_when_operator_declines(self) -> None:
        # Given
        with mock.patch("builtins.input", side_effect=["n", ""]):
            # When
            self.execute(interactive=True)
        # Then
        self.assertFalse(self.enabled)
        self.assertTrue(self.active)
        self.assertIn(("/usr/bin/systemctl", "disable", "sparehash.service"), self.calls)

    def test_inactive_when_operator_declines_start(self) -> None:
        # Given
        output = io.StringIO()
        with mock.patch("builtins.input", side_effect=["", "n"]), redirect_stdout(output):
            # When
            self.execute(interactive=True)
        # Then
        self.assertTrue(self.enabled)
        self.assertFalse(self.active)
        self.assertFalse(any(argv[1] == "start" for argv in self.calls))
        self.assertIn("controller inactive", output.getvalue())
        self.assertIn("mining blocked: yes", output.getvalue())

    def test_both_defaults_when_answers_mode(self) -> None:
        # Given
        with mock.patch("builtins.input", side_effect=AssertionError("unexpected stdin")):
            # When
            self.execute(interactive=False)
        # Then
        self.assertTrue(self.enabled)
        self.assertTrue(self.active)

    def test_summary_when_controller_status_has_blockers_or_fails(self) -> None:
        for reply, expected in (({"ok": True, "ready": True, "blockers": ["battery"]}, "yes"),
                                ({"ok": True, "ready": True, "blockers": []}, "no"),
                                ({"ok": True, "ready": False, "blockers": []}, "yes"),
                                ({"ok": False}, "unknown"), (ipc.IPCError("timeout"), "unknown")):
            # Given
            self.execute()
            output = io.StringIO()
            with mock.patch.object(install, "command", side_effect=self.command), mock.patch.object(
                install, "checked", side_effect=self.checked
            ), mock.patch.object(install, "isolated", return_value=(0, "")), mock.patch.object(
                install.xmrig_config, "validate_rendered"
            ), mock.patch.object(ipc, "call", return_value=reply if isinstance(reply, dict) else None,
                                 side_effect=reply if isinstance(reply, ipc.IPCError) else None) as call, redirect_stdout(output):
                # When
                install.post_install(self.tree, self.cfg)
            # Then
            self.assertIn("boot startup enabled", output.getvalue())
            self.assertIn("controller active", output.getvalue())
            self.assertIn("mining blocked: " + expected, output.getvalue())
            self.assertLessEqual(call.call_args.kwargs["timeout"], 1)

    def test_miner_untouched_when_any_startup_choices(self) -> None:
        for boot, now in (("y", "y"), ("n", "y"), ("y", "n"), ("n", "n")):
            # Given
            self.calls.clear()
            with mock.patch("builtins.input", side_effect=[boot, now]):
                # When
                self.execute(interactive=True)
            # Then
            self.assertFalse(any(argv[1] in ("enable", "start") and argv[2] == "sparehash-xmrig.service"
                                 for argv in self.calls))

    def test_refuses_when_requested_start_is_inactive(self) -> None:
        # Given
        self.execute()
        self.active = False
        with mock.patch.object(install, "command", side_effect=self.command), self.assertRaises(DeploymentError):
            # When / Then
            install.post_install(self.tree, self.cfg)
