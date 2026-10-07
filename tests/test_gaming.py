"""GameMode fixtures and real temporary IPC hooks; no live gaming operations."""

import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Thread
from typing import final
from unittest.mock import Mock, patch

from sparehash import gaming, ipc
from sparehash.__main__ import main
from sparehash.sysdetect import FIXED_PATH
from tests.controller_fixtures import ControllerFixture


@final
class GamingTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.users = tuple(gaming.User.from_name(name) for name in ("root", "nobody"))
        self.runner = Mock(return_value=subprocess.CompletedProcess((), 0, "gamemode is inactive\n"))
        self.probe = gaming.Probe(self.runner, self.root)
        def helper(name: str) -> str:
            return f"/usr/bin/{name}"
        self.helpers = patch("sparehash.gaming.resolve_helper", side_effect=helper)
        self.helpers_started = False

    def bus(self, user: gaming.User) -> None:
        if not self.helpers_started:
            _ = self.helpers.start()
            self.addCleanup(self.helpers.stop)
            self.helpers_started = True
        directory = self.root / str(user.uid)
        directory.mkdir()
        connection = socket.socket(socket.AF_UNIX)
        self.addCleanup(connection.close)
        connection.bind(str(directory / "bus"))

    def test_parser_when_global_response_is_exact(self) -> None:
        # Given / When / Then
        for text, expected in (("gamemode is active\n", True), ("gamemode is inactive\n", False),
                               ("active", None), ("gamemode is active and [1] registered", None),
                               ("gamemode is inactive\ngamemode is active", None), ("", None)):
            with self.subTest(text=text):
                self.assertIs(gaming.parse_status(text), expected)

    def test_union_when_one_user_is_active_and_another_unknown(self) -> None:
        # Given
        for user in self.users:
            self.bus(user)
        self.runner.side_effect = [subprocess.CompletedProcess((), 1, ""),
                                   subprocess.CompletedProcess((), 0, "gamemode is active\n")]
        # When / Then
        self.assertIs(gaming.reconcile(self.users, self.probe), True)
        self.assertEqual(self.runner.call_count, 2)

    def test_observations_when_unknown_preserve_each_uid(self) -> None:
        # Given
        self.bus(self.users[0])
        # When
        result = gaming.observations(self.users, self.probe)
        # Then
        self.assertEqual(result, ((self.users[0].uid, False), (self.users[1].uid, None)))

    def test_union_when_all_users_are_inactive(self) -> None:
        # Given
        for user in self.users:
            self.bus(user)
        # When / Then
        self.assertIs(gaming.reconcile(self.users, self.probe), False)

    def test_missing_bus_when_session_is_unconfirmed_inhibits(self) -> None:
        # Given / When / Then
        for presence in (None, True):
            def session(_user: gaming.User) -> bool | None:
                return presence
            probe = replace(self.probe, session_present=session)
            self.assertIsNone(gaming.reconcile(self.users, probe))
        self.runner.assert_not_called()

    def test_missing_bus_when_logout_confirmed_clears_only_that_uid(self) -> None:
        # Given
        def session(user: gaming.User) -> bool | None:
            return False if user == self.users[0] else None
        probe = replace(self.probe, session_present=session)
        # When / Then
        self.assertEqual(gaming.observations(self.users, probe),
                         ((self.users[0].uid, False), (self.users[1].uid, None)))

    def test_query_when_helper_fails_is_unknown(self) -> None:
        # Given
        self.bus(self.users[0])
        # When / Then
        for failure in (subprocess.TimeoutExpired("gamemoded", 10), OSError("unavailable"),
                        subprocess.SubprocessError("failed"), UnicodeError("invalid text")):
            with self.subTest(failure=failure):
                self.runner.side_effect = failure
                self.assertIsNone(self.probe.query(self.users[0]))

    def test_query_when_response_nonzero_or_malformed_is_unknown(self) -> None:
        # Given
        self.bus(self.users[0])
        # When / Then
        for code, text in ((1, "gamemode is inactive"), (0, "unexpected")):
            self.runner.return_value = subprocess.CompletedProcess((), code, text)
            self.assertIsNone(self.probe.query(self.users[0]))

    def test_query_when_bus_is_regular_file_is_unknown(self) -> None:
        # Given
        directory = self.root / str(self.users[0].uid)
        directory.mkdir()
        _ = (directory / "bus").write_text("")
        # When / Then
        self.assertIsNone(self.probe.query(self.users[0]))
        self.runner.assert_not_called()

    def test_query_when_bus_exists_uses_fixed_bounded_argv(self) -> None:
        # Given
        user = self.users[0]
        self.bus(user)
        # When
        _ = self.probe.query(user)
        # Then
        self.runner.assert_called_once_with(
            ("/usr/bin/runuser", "-u", user.name, "--", "/usr/bin/env",
             f"DBUS_SESSION_BUS_ADDRESS=unix:path={self.root}/{user.uid}/bus", "/usr/bin/gamemoded", "-s"),
            timeout=10.0, check=False, capture_output=True, text=True,
            env={"PATH": FIXED_PATH, "LC_ALL": "C"})

    def test_helpers_when_caller_path_is_hostile_ignore_it(self) -> None:
        # Given
        with patch.dict(os.environ, {"PATH": str(self.root)}), patch("shutil.which", return_value=None) as which:
            # When
            result = gaming.resolve_helper("gamemoded")
            # Then
            self.assertIsNone(result)
            which.assert_called_once_with("gamemoded", path=FIXED_PATH)

    def test_accounts_when_uid_mismatched_or_name_unsafe_are_rejected(self) -> None:
        # Given / When / Then
        for uid, name in ((123456, "root"), (0, "-root"), (0, "root\n"), (0, "missing-sparehash-account")):
            with self.assertRaises(gaming.UserError):
                _ = gaming.User(ipc.Uid(uid), name)

    def test_hook_when_controller_rejects_returns_failure(self) -> None:
        # Given
        with patch.object(ipc, "call", return_value={"ok": False}), contextlib.redirect_stderr(io.StringIO()):
            # When / Then
            self.assertEqual(gaming.gaming_end(), 1)

    def test_cli_when_extra_identity_argument_is_passed_rejects_it(self) -> None:
        # Given
        with contextlib.redirect_stderr(io.StringIO()), patch.object(ipc, "call") as client:
            # When / Then
            with self.assertRaises(SystemExit):
                _ = main(["gaming-start", "--uid", "0"])
            client.assert_not_called()


class GamingControllerTests(ControllerFixture):
    def test_hooks_when_overlap_ends_in_either_order_are_idempotent(self) -> None:
        # Given / When / Then
        for first, last in ((1, 2), (2, 1)):
            self.fresh()
            self.command(ipc.Verb.GAMING_END, first)
            self.command(ipc.Verb.GAMING_START, first)
            generation = self.c.generation
            self.command(ipc.Verb.GAMING_START, first)
            self.assertEqual(self.c.generation, generation)
            self.command(ipc.Verb.GAMING_START, last)
            self.command(ipc.Verb.GAMING_END, first)
            self.assertEqual(self.c.hooks, {last})
            self.assertIn("gaming", self.state.active())
            self.command(ipc.Verb.GAMING_END, last)
            self.assertNotIn("gaming", self.state.active())

    def test_end_when_provider_active_or_unknown_cannot_clear(self) -> None:
        # Given / When / Then
        for active in (True, None):
            self.fresh(replace(self.snapshot(), gaming=((ipc.Uid(1), active),)))
            self.command(ipc.Verb.GAMING_END, 1)
            self.assertIn("gaming", self.state.active())

    def test_restart_when_provider_reports_active_inhibits(self) -> None:
        # Given
        self.c.start()
        # When
        self.fresh(replace(self.snapshot(), gaming=((ipc.Uid(1), True),)))
        # Then
        self.assertIn("gaming", self.state.active())
        self.assertNotIn("start", self.io.trace)

    def test_cli_when_real_socket_authenticates_hooks_is_fast_and_idempotent(self) -> None:
        # Given
        self.fresh()
        root = Path(self.temp.name) / "run"
        root.mkdir(mode=0o700)
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(root / "control.sock"))
            listener.listen()
            listener.settimeout(2)
            def serve() -> None:
                for _ in range(4):
                    with listener.accept()[0] as connection:
                        connection.settimeout(2)
                        peer = ipc.peer_credentials(connection)
                        with connection.makefile("rb") as stream:
                            request = ipc.parse_request(stream.readline(ipc.MAX_FRAME)[:-1])
                        response = self.c.handle(request, peer)
                        connection.sendall(json.dumps(response).encode() + b"\n")
            worker = Thread(target=serve)
            worker.start()
            try:
                # When / Then
                cases: tuple[tuple[str, set[int]], ...] = (("gaming-start", {os.getuid()}), ("gaming-start", {os.getuid()}),
                                                         ("gaming-end", set()), ("gaming-end", set()))
                for command, expected in cases:
                    at = time.monotonic()
                    result = subprocess.run((sys.executable, "-m", "sparehash", command),
                                            env={**os.environ, "SPAREHASH_RUN_DIR": str(root)},
                                            capture_output=True, text=True, timeout=2, check=False)
                    self.assertLess(time.monotonic() - at, 1)
                    self.assertEqual((result.returncode, result.stderr), (0, ""))
                    self.assertEqual(self.c.hooks, expected)
            finally:
                worker.join(2)
            self.assertFalse(worker.is_alive())

    def test_cli_when_socket_absent_fails_quickly_with_guidance(self) -> None:
        # Given
        root = Path(self.temp.name) / "run"
        root.mkdir(mode=0o700)
        # When
        at = time.monotonic()
        result = subprocess.run((sys.executable, "-m", "sparehash", "gaming-start"),
                                env={**os.environ, "SPAREHASH_RUN_DIR": str(root)},
                                capture_output=True, text=True, timeout=2, check=False)
        # Then
        self.assertLess(time.monotonic() - at, 1)
        self.assertEqual(result.returncode, 1)
        self.assertIn("socket access", result.stderr)
