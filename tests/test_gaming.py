"""Verified accounts and real temporary IPC hooks; no live gaming operations."""

import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from sparehash import gaming, ipc
from sparehash.__main__ import main
from tests.controller_fixtures import ControllerFixture


class GamingTests(unittest.TestCase):
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
