"""Real temporary Unix socket contract tests."""

from __future__ import annotations

import grp
import json
import os
import socket
import stat
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from collections.abc import Callable
from typing import Final, final
from unittest.mock import patch

from sparehash import ipc

LOAD_JSON: Final[Callable[..., ipc.Document]] = json.loads


@final
class IPCTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.stack = ExitStack()
        self.root = Path()
        self.run_dir = Path()
        self.path = Path()
        self.thread = threading.Thread()
        self.ownership: list[tuple[str, int, int]] = []
        self.requests: list[tuple[ipc.Request, ipc.Peer]] = []
        self.errors: list[ipc.IPCError] = []
        self.stop = threading.Event()

    def setup_fixture(self) -> None:
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.run_dir = self.root / "run"
        self.run_dir.mkdir(mode=0o700)
        self.path = self.run_dir / "control.sock"
        self.stack.enter_context(patch.dict(os.environ, SPAREHASH_RUN_DIR=str(self.run_dir)))
        _ = self.stack.enter_context(patch.object(grp, "getgrnam", return_value=grp.struct_group(("sparehash", "x", 12345, []))))
        _ = self.stack.enter_context(patch.object(os, "fchown", side_effect=self.directory_owner))
        _ = self.stack.enter_context(patch.object(os, "chown", side_effect=self.socket_owner))

    setUp = setup_fixture

    def directory_owner(self, fd: int, uid: int, gid: int) -> None:
        self.assertTrue(stat.S_ISDIR(os.fstat(fd).st_mode))
        self.ownership.append(("directory", uid, gid))

    def socket_owner(self, name: str, uid: int, gid: int, *, dir_fd: int, follow_symlinks: bool) -> None:
        self.assertEqual(name, "control.sock")
        self.assertFalse(follow_symlinks)
        self.assertTrue(stat.S_ISSOCK(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode))
        self.ownership.append(("socket", uid, gid))

    def handler(self, request: ipc.Request, peer: ipc.Peer) -> ipc.Document:
        self.requests.append((request, peer))
        return {"ok": True, "verb": request.verb.value}

    def start(self) -> None:
        ready = threading.Event()
        listen = socket.socket.listen

        def listening(sock: socket.socket, backlog: int) -> None:
            listen(sock, backlog)
            ready.set()

        def run() -> None:
            try:
                ipc.serve(self.path, self.handler, self.stop)
            except ipc.IPCError as exc:
                self.errors.append(exc)
                ready.set()

        _ = self.stack.enter_context(patch.object(socket.socket, "listen", listening))
        self.thread = threading.Thread(target=run)
        self.thread.start()
        self.addCleanup(self.finish)
        self.assertTrue(ready.wait(2), "server readiness timeout")
        self.assertEqual(self.errors, [])

    def finish(self) -> None:
        self.stop.set()
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.errors, [])

    def raw(self, frame: bytes) -> ipc.Document:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(self.path))
            client.sendall(frame)
            response = bytearray()
            while not response.endswith(b"\n"):
                response.extend(client.recv(4096))
            return LOAD_JSON(response)

    def test_dispatches_each_verb_once_when_root_peer(self) -> None:
        # Given
        _ = self.stack.enter_context(patch.object(ipc, "peer_credentials", return_value=ipc.Peer(ipc.Uid(0), ipc.Gid(12345))))
        self.start()
        for verb in ipc.Verb:
            with self.subTest(verb=verb):
                command: ipc.Document = {"cmd": verb.value}
                if verb in ipc.DEBUG:
                    command["blocker"] = "battery"
                elif verb is ipc.Verb.BEGIN_UPDATE:
                    command["purpose"] = "test"
                    command["request_id"] = "abc"
                elif verb is ipc.Verb.UPDATE_STATUS:
                    command["token"] = "0x1"
                elif verb is ipc.Verb.END_UPDATE:
                    command["token"] = "0x1"
                    command["outcome"] = "commit"
                # When
                response = ipc.call(command)
                # Then
                self.assertEqual(response, {"ok": True, "verb": verb.value})
        self.assertEqual([request.verb for request, _ in self.requests], list(ipc.Verb))

    def test_denies_protected_verbs_when_nonroot_peer(self) -> None:
        # Given
        _ = self.stack.enter_context(patch.object(ipc, "peer_credentials", return_value=ipc.Peer(ipc.Uid(65534), ipc.Gid(12345))))
        self.start()
        for verb in ipc.ROOT_ONLY:
            with self.subTest(verb=verb):
                command: ipc.Document = {"cmd": verb.value}
                if verb in ipc.DEBUG:
                    command["blocker"] = "thermal"
                elif verb is ipc.Verb.BEGIN_UPDATE:
                    command["purpose"] = "test"
                    command["request_id"] = "abc"
                elif verb is ipc.Verb.UPDATE_STATUS:
                    command["token"] = "0x1"
                elif verb is ipc.Verb.END_UPDATE:
                    command["token"] = "0x1"
                    command["outcome"] = "commit"
                # When
                response = ipc.call(command)
                # Then
                self.assertEqual(response, {"ok": False, "error": "permission denied"})
        self.assertEqual(self.requests, [])

    def test_delivers_kernel_identity_when_normal_verb(self) -> None:
        # Given
        self.start()
        # When
        _ = ipc.call({"cmd": "status"})
        # Then
        self.assertEqual(self.requests[0][1], ipc.Peer(ipc.Uid(os.getuid()), ipc.Gid(os.getgid())))

    def test_rejects_invalid_frames_without_dispatch(self) -> None:
        # Given
        self.start()
        frames = ((b"x" * 5120 + b"\n", "oversized frame"), (b"{\n", "invalid JSON"),
                  (b'{"cmd":"exec","x":"rm -rf"}\n', "unknown verb"),
                  (b'{"cmd":"status","uid":0}\n', "unexpected field"),
                  (b'{"cmd":"benchmark","path":"/tmp/x"}\n', "unexpected field"),
                  (b'{"cmd":"debug-set","blocker":"invalid"}\n', "invalid blocker"),
                  (b'{"cmd":"debug-clear"}\n', "invalid blocker"),
                  (b'{"cmd":"status","cmd":"pause"}\n', "duplicate field"),
                  (b'[]\n', "expected object"),
                  (b'{"cmd":"status"}\n{"cmd":"pause"}\n', "multiple commands"))
        for frame, error in frames:
            with self.subTest(error=error):
                # When
                response = self.raw(frame)
                # Then
                self.assertEqual(response, {"ok": False, "error": error})
        self.assertEqual(self.requests, [])

    def test_permissions_and_cleanup_when_server_exits(self) -> None:
        # Given
        self.start()
        self.assertEqual(stat.S_IMODE(self.run_dir.stat().st_mode), 0o750)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o660)
        self.assertEqual(self.ownership, [("directory", 0, 12345), ("socket", 0, 12345)])
        # When
        self.finish()
        # Then
        self.assertFalse(self.path.exists())

    def test_preserves_replacement_when_socket_inode_changes(self) -> None:
        # Given
        self.start()
        self.path.unlink()
        _ = self.path.write_text("replacement")
        # When
        self.finish()
        # Then
        self.assertEqual(self.path.read_text(), "replacement")

    def test_refuses_preoccupied_path_when_file_or_symlink(self) -> None:
        for symlink in (False, True):
            with self.subTest(symlink=symlink):
                # Given
                target = self.root / "target"
                _ = target.write_text("untouched")
                if symlink:
                    self.path.symlink_to(target)
                else:
                    _ = self.path.write_text("untouched")
                # When / Then
                with self.assertRaises(ipc.IPCError):
                    ipc.serve(self.path, self.handler, self.stop)
                self.assertEqual(self.path.read_text(), "untouched")
                self.path.unlink()

    def test_fails_closed_when_ownership_syscall_fails(self) -> None:
        # Given
        _ = self.stack.enter_context(patch.object(os, "chown", side_effect=PermissionError()))
        # When / Then
        with self.assertRaises(ipc.IPCError):
            ipc.serve(self.path, self.handler, self.stop)
        self.assertFalse(self.path.exists())

    def test_refuses_arbitrary_path_when_not_fixed_or_qa(self) -> None:
        # Given
        alternate = self.root / "other.sock"
        # When / Then
        with self.assertRaises(ipc.IPCError):
            ipc.serve(alternate, self.handler, self.stop)
        self.assertFalse(alternate.exists())

    def test_client_times_out_when_peer_never_replies(self) -> None:
        # Given
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(self.path))
            listener.listen(1)
            # When / Then
            with self.assertRaises(ipc.IPCError) as caught:
                _ = ipc.call({"cmd": "status"}, timeout=0.02)
            self.assertEqual(caught.exception.code, "timeout")

    def test_server_bounds_partial_frame_when_client_stalls(self) -> None:
        # Given
        _ = self.stack.enter_context(patch.object(ipc, "CONNECTION_TIMEOUT", 0.02))
        self.start()
        # When
        response = self.raw(b'{"cmd":')
        # Then
        self.assertFalse(response["ok"])
        self.assertEqual(self.requests, [])

    def test_debug_request_is_distinct_when_valid_blocker(self) -> None:
        # Given
        _ = self.stack.enter_context(patch.object(ipc, "peer_credentials", return_value=ipc.Peer(ipc.Uid(0), ipc.Gid(12345))))
        self.start()
        # When
        _ = ipc.call({"cmd": "debug-clear", "blocker": "battery"})
        # Then
        self.assertEqual(self.requests[0][0], ipc.DebugCommand(ipc.Verb.DEBUG_CLEAR, "battery"))
