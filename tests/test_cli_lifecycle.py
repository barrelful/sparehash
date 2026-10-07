"""Root-only update adapter contract over a fake lifecycle IPC transport."""

from __future__ import annotations

from pathlib import Path
import unittest
from collections.abc import Callable
from typing import final
from unittest import mock

from sparehash import cli_lifecycle, ipc
from sparehash.controller_io import Generation
from sparehash.xmrig_install import InstallContext, InstallError, InstallRequest, Installed, ReleaseArtifacts
from sparehash.xmrig_manifest import Manifest
from sparehash.xmrig_paths import PathTrust

TOKEN = "a" * 64
REQUEST = "b" * 32
MANAGED = Path("/opt/xmrig/current/xmrig")


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
class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def pause(self, seconds: float) -> None:
        self.now += 100 if seconds else 0


def request_fixture() -> InstallRequest:
    return InstallRequest(ReleaseArtifacts(Path("/tmp/x.tar.gz"), Path("/tmp/sums"), Path("/tmp/sig"), b"key"),
                          (6, 27, 0), "src")


def installed_fixture() -> Installed:
    return Installed("xmrig-6.27.0", MANAGED, Manifest("/opt/xmrig", ()), ())


def prepare_fixture(_stage: Path) -> InstallRequest:
    return request_fixture()


def install_fixture(_request: InstallRequest, _context: InstallContext) -> Installed:
    return installed_fixture()


@final
class UpdateAdapterTests(unittest.TestCase):
    def run_update(self, client: FakeClient, *, root: int = 0, managed: bool = True,
                   install: Callable[[InstallRequest, InstallContext], Installed] | None = None,
                   clock: Clock | None = None) -> int:
        engine = install if install is not None else install_fixture
        timer = clock or Clock()
        with mock.patch.object(cli_lifecycle, "current_binary", return_value=MANAGED if managed else None):
            return cli_lifecycle.update(geteuid=lambda: root, trust=PathTrust(), client=client,
                                        prepare=prepare_fixture, install=engine,
                                        clock=timer, pause=timer.pause, request_id=REQUEST)

    def test_commits_after_ready_and_holds_the_token_through_install(self) -> None:
        # Given a controller that reports ready and a recording installer
        client = FakeClient([{"ok": True, "token": TOKEN}, {"ok": True, "status": "ready"}, {"ok": True}])
        gates: list[cli_lifecycle.HeldLease] = []

        def install(_request: InstallRequest, context: InstallContext) -> Installed:
            assert isinstance(context.gate, cli_lifecycle.HeldLease)
            gates.append(context.gate)
            return installed_fixture()

        # When
        result = self.run_update(client, install=install)
        # Then
        self.assertEqual(result, 0)
        self.assertEqual([command["cmd"] for command in client.commands],
                         ["begin-update", "update-status", "end-update"])
        self.assertEqual(client.commands[0]["request_id"], REQUEST)
        self.assertEqual(client.commands[-1], {"cmd": "end-update", "token": TOKEN, "outcome": "commit"})
        self.assertEqual(gates[0].token, Generation(int(TOKEN, 16)))

    def test_requires_root_before_any_ipc(self) -> None:
        # Given
        client = FakeClient([{"ok": True, "token": TOKEN}])
        # When
        result = self.run_update(client, root=1000)
        # Then
        self.assertEqual(result, 2)
        self.assertEqual(client.commands, [])

    def test_refuses_unmanaged_install_before_any_ipc(self) -> None:
        # Given
        client = FakeClient([{"ok": True, "token": TOKEN}])
        # When
        result = self.run_update(client, managed=False)
        # Then
        self.assertEqual(result, 1)
        self.assertEqual(client.commands, [])

    def test_aborts_when_publication_fails(self) -> None:
        # Given a ready lease whose install fails
        client = FakeClient([{"ok": True, "token": TOKEN}, {"ok": True, "status": "ready"}, {"ok": True}])

        def failing(_request: InstallRequest, _context: InstallContext) -> Installed:
            raise InstallError("archive", "boom")

        # When
        result = self.run_update(client, install=failing)
        # Then
        self.assertEqual(result, 1)
        self.assertEqual(client.commands[-1], {"cmd": "end-update", "token": TOKEN, "outcome": "abort"})

    def test_aborts_when_readiness_never_arrives(self) -> None:
        # Given a lease that never becomes ready
        client = FakeClient([{"ok": True, "token": TOKEN}, {"ok": True, "status": "pending"}, {"ok": True}])
        # When
        result = self.run_update(client, clock=Clock())
        # Then
        self.assertEqual(result, 1)
        self.assertEqual(client.commands[-1], {"cmd": "end-update", "token": TOKEN, "outcome": "abort"})

    def test_reports_busy_without_abort_when_begin_is_refused(self) -> None:
        # Given a controller that refuses the lease
        client = FakeClient([{"ok": False, "error": "lifecycle busy"}])
        # When
        result = self.run_update(client)
        # Then
        self.assertEqual(result, 1)
        self.assertEqual([command["cmd"] for command in client.commands], ["begin-update"])

    def test_held_lease_gate_is_bound_to_the_token(self) -> None:
        # Given
        gate = cli_lifecycle.HeldLease(Generation(5))
        # When / Then
        self.assertEqual(gate.acquire_update(), Generation(5))
        self.assertTrue(gate.update_ready(Generation(5)))
        self.assertFalse(gate.update_ready(Generation(6)))
        self.assertTrue(gate.release_update(Generation(5)))


if __name__ == "__main__":
    _ = unittest.main()
