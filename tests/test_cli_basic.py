"""Offline CLI operations: injected helpers and disposable Unix sockets only."""

import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Thread
from typing import Literal
from unittest.mock import Mock, call, patch

from sparehash import cli_ops as ops, ipc
from sparehash.__main__ import main


class CliBasicTests(unittest.TestCase):
    def test_stack_when_root_uses_only_fixed_commands(self) -> None:
        # Given
        cases: tuple[tuple[Literal["start", "stop"], tuple[str, ...]], ...] = (
            ("start", ("enable", "--now", "sparehash.service")),
            ("stop", ("stop", "sparehash-xmrig.service", "sparehash.service")))
        for command, args in cases:
            runner = Mock(return_value=subprocess.CompletedProcess((), 0, "", ""))
            helpers = ops.Helpers(runner, lambda _name: "/usr/bin/systemctl")
            with self.subTest(command=command), contextlib.redirect_stdout(io.StringIO()):
                # When
                code = ops.stack(command, helpers, lambda: 0)
                # Then
                self.assertEqual(code, 0)
                runner.assert_called_once_with(("/usr/bin/systemctl", *args), timeout=30.0,
                                              check=False, capture_output=True, text=True, shell=False,
                                              env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin:/usr/games", "LC_ALL": "C",
                                                   "SYSTEMD_PAGER": "cat", "SYSTEMD_COLORS": "0"})

    def test_stack_when_nonroot_never_resolves_or_runs_helper(self) -> None:
        # Given
        runner, resolver = Mock(), Mock()
        for command in ("start", "stop"):
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()):
                # When
                code = ops.stack(command, ops.Helpers(runner, resolver), lambda: 1000)
                # Then
                self.assertEqual(code, 2)
                runner.assert_not_called()
                resolver.assert_not_called()

    def test_start_when_nonroot_subprocess_denies_without_host_action(self) -> None:
        # Given
        if os.geteuid() == 0:
            self.skipTest("real nonroot subprocess requires nonroot test runner")
        # When
        result = subprocess.run((sys.executable, "-m", "sparehash", "start"),
                                capture_output=True, text=True, timeout=3, check=False)
        # Then
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires root", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_stack_when_helper_fails_reports_nonzero_without_confirmation(self) -> None:
        # Given
        failures = (subprocess.CompletedProcess((), 4, "", "unit missing"),
                    subprocess.TimeoutExpired("systemctl", 30), OSError("missing"))
        for failure in failures:
            runner = Mock(return_value=failure) if isinstance(failure, subprocess.CompletedProcess) else Mock(side_effect=failure)
            out, err = io.StringIO(), io.StringIO()
            with self.subTest(failure=failure), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                # When
                code = ops.stack("start", ops.Helpers(runner, lambda _name: "/usr/bin/systemctl"), lambda: 0)
                # Then
                self.assertEqual(code, 1)
                self.assertEqual(out.getvalue(), "")
                self.assertIn("systemctl", err.getvalue())

    def test_helper_when_system_path_missing_ignores_caller_path(self) -> None:
        # Given
        runner = Mock()
        with patch.dict(os.environ, {"PATH": "/hostile"}), patch.object(shutil, "which", return_value=None) as which:
            # When / Then
            with self.assertRaises(ops.OperationError):
                ops.Helpers(runner).execute(("systemctl", "enable", "--now", "sparehash.service"))
            which.assert_called_once_with("systemctl", path="/usr/sbin:/usr/bin:/sbin:/bin:/usr/games")
            runner.assert_not_called()

    def test_pause_when_accepted_only_calls_pause(self) -> None:
        # Given
        client = Mock(return_value={"ok": True})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            # When
            code = ops.manual("pause", client)
        # Then
        self.assertEqual(code, 0)
        self.assertIn("paused manually", out.getvalue())
        client.assert_called_once_with({"cmd": "pause"}, timeout=1)

    def test_resume_when_current_registry_is_empty_reports_only_clearance(self) -> None:
        # Given
        client = Mock(side_effect=[{"ok": True}, {"ok": True, "blockers": [], "reasons": {}}])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            # When
            code = ops.manual("resume", client)
        # Then
        self.assertEqual(code, 0)
        self.assertIn("no blockers active", out.getvalue())
        self.assertNotIn("Mining resumed", out.getvalue())
        self.assertEqual(client.call_args_list, [call({"cmd": "resume"}, timeout=1),
                                                call({"cmd": "status"}, timeout=1)])

    def test_resume_when_status_is_invalid_or_fails_never_claims_clear(self) -> None:
        # Given
        statuses: tuple[ipc.Document, ...] = ({"ok": False}, {"ok": True},
            {"ok": True, "blockers": [], "reasons": {"battery": "on battery"}},
            {"ok": True, "blockers": ["battery"], "reasons": {}},
            {"ok": True, "blockers": ["unknown"], "reasons": {"unknown": "unsafe"}})
        for status in statuses:
            client = Mock(side_effect=[{"ok": True}, status])
            out = io.StringIO()
            with self.subTest(status=status), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                # When
                code = ops.manual("resume", client)
                # Then
                self.assertEqual(code, 1)
                self.assertEqual(out.getvalue(), "")

    def test_manual_when_denied_or_socket_down_is_nonzero_and_redacted(self) -> None:
        # Given
        token = "a" * 64
        for command in ("pause", "resume"):
            for client in (Mock(return_value={"ok": False, "error": "permission denied " + token}),
                           Mock(side_effect=ipc.IPCError("connection error"))):
                out, err = io.StringIO(), io.StringIO()
                with self.subTest(command=command), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    # When
                    code = ops.manual(command, client)
                    # Then
                    self.assertEqual(code, 1)
                    self.assertEqual(out.getvalue(), "")
                    self.assertNotIn(token, err.getvalue())
                    self.assertTrue(err.getvalue())

    def test_logs_when_follow_requested_streams_fixed_units(self) -> None:
        # Given
        for follow in (False, True):
            runner = Mock(return_value=subprocess.CompletedProcess((), 0))
            # When
            code = ops.logs(follow, ops.Helpers(runner, lambda _name: "/usr/bin/journalctl"))
            # Then
            self.assertEqual(code, 0)
            runner.assert_called_once_with(("/usr/bin/journalctl", "-u", "sparehash.service", "-u",
                "sparehash-xmrig.service", "-n", "100") + (("--follow",) if follow else ()),
                timeout=None if follow else 30.0, check=False, capture_output=False,
                text=True, env=ops.ENV, shell=False)

    def test_logs_when_helper_fails_or_interrupted_returns_failure(self) -> None:
        # Given
        for failure, expected in ((subprocess.CompletedProcess[str]((), 1), 1), (KeyboardInterrupt(), 130)):
            runner = Mock(side_effect=failure) if isinstance(failure, KeyboardInterrupt) else Mock(return_value=failure)
            with contextlib.redirect_stderr(io.StringIO()):
                # When
                code = ops.logs(True, ops.Helpers(runner, lambda _name: "/usr/bin/journalctl"))
            # Then
            self.assertEqual(code, expected)

    def test_dispatch_when_logs_flags_are_parsed_uses_follow(self) -> None:
        # Given
        for args, expected in ((["logs"], False), (["logs", "-f"], True), (["logs", "--follow"], True)):
            with patch.object(ops, "logs", return_value=0) as logs:
                # When
                code = main(args)
                # Then
                self.assertEqual(code, 0)
                logs.assert_called_once_with(expected)

    def test_parser_when_units_paths_or_extra_arguments_supplied_rejects(self) -> None:
        # Given
        for args in (("logs", "-u", "ssh"), ("start", "sparehash-xmrig.service"), ("resume", "--force")):
            # When
            result = subprocess.run((sys.executable, "-m", "sparehash", *args),
                                    capture_output=True, text=True, timeout=3, check=False)
            # Then
            self.assertEqual(result.returncode, 2)
            self.assertIn("unrecognized arguments", result.stderr)

    def test_cli_when_temporary_socket_reports_blocker_uses_fresh_status(self) -> None:
        # Given
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            root.mkdir(mode=0o700)
            requests: list[str] = []
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(root / "control.sock"))
                listener.listen()
                listener.settimeout(3)
                def serve() -> None:
                    for response in ({"ok": True}, {"ok": True, "blockers": ["thermal"],
                                                    "reasons": {"thermal": "thermal limit reached"}}):
                        with listener.accept()[0] as connection:
                            connection.settimeout(3)
                            with connection.makefile("rb") as stream:
                                request = ipc.parse_request(stream.readline(ipc.MAX_FRAME)[:-1])
                            requests.append(request.verb.value)
                            connection.sendall(json.dumps(response).encode() + b"\n")
                worker = Thread(target=serve)
                worker.start()
                try:
                    # When
                    result = subprocess.run((sys.executable, "-m", "sparehash", "resume"),
                        env={**os.environ, "SPAREHASH_RUN_DIR": str(root)}, capture_output=True,
                        text=True, timeout=3, check=False)
                finally:
                    worker.join(4)
                # Then
                self.assertFalse(worker.is_alive())
                self.assertEqual((result.returncode, result.stderr), (0, ""))
                self.assertEqual(requests, ["resume", "status"])
                self.assertIn("thermal (thermal limit reached)", result.stdout)
                self.assertNotIn("no blockers", result.stdout)

    def test_cli_when_private_socket_absent_reports_access_error(self) -> None:
        # Given
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            root.mkdir(mode=0o700)
            # When
            result = subprocess.run((sys.executable, "-m", "sparehash", "pause"),
                env={**os.environ, "SPAREHASH_RUN_DIR": str(root)}, capture_output=True,
                text=True, timeout=3, check=False)
            # Then
            self.assertEqual(result.returncode, 1)
            self.assertIn("socket access", result.stderr)
            self.assertEqual(result.stdout, "")
