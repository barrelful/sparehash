"""Durable lifecycle lease: typed parse, restart exclusion, and fail-closed commits.

Covers one persistent external lease per request id, persist-before-begin, restart
inhibition until a fresh empty barrier, uninstall retirement latched before any
acknowledgment, and bounded CLI polling/abort behavior.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from typing import final
from unittest import mock

from sparehash import cli_lifecycle, ipc
from sparehash.controller import Controller
from sparehash.controller_io import Completion, Observation, Ports
from sparehash.lifecycle_lease import ActiveLease, LeaseError, LeaseStore
from sparehash.state import BlockerState
from sparehash.xmrig_install import InstallContext, Installed, InstallRequest, ReleaseArtifacts
from sparehash.xmrig_manifest import Manifest
from sparehash.xmrig_paths import PathTrust
from tests.controller_fixtures import ROOT, FakePorts

MANAGED = Path("/opt/xmrig/current/xmrig")
REQUEST = "cli-request"
TOKEN = "a" * 32


def install_request() -> InstallRequest:
    return InstallRequest(ReleaseArtifacts(Path("/tmp/x.tar.gz"), Path("/tmp/sums"), Path("/tmp/sig"), b"key"),
                          (6, 27, 0), "src")


def install_done(_request: InstallRequest, _context: InstallContext) -> Installed:
    return Installed("xmrig-6.27.0", MANAGED, Manifest("/opt/xmrig", ()), ())


class LeaseHarness(unittest.TestCase):
    """Shared temp state/lease paths with a recording port set and IPC helper."""

    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self._temp: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        root = Path(self._temp.name)
        self.state_path: Path = root / "state.json"
        self.lease_path: Path = root / "lease.json"
        self.latch_path: Path = root / "lifecycle.json"
        self.store: LeaseStore = LeaseStore(self.lease_path)
        self.io: FakePorts = FakePorts()
        self.ports: Ports = Ports(self.io, self.io, self.io, self.io.clock)
        self.retires: int = 0
        self.fail_retire: bool = False

    def retire(self) -> None:
        if self.fail_retire:
            raise OSError("injected retire failure")
        self.retires += 1
        _ = self.latch_path.write_text("{}", encoding="utf-8")

    def controller(self) -> Controller:
        state = BlockerState(self.state_path, self.io.clock, 0)
        return Controller(state, self.ports, lease_store=LeaseStore(self.lease_path), retire_writer=self.retire)

    def send(self, controller: Controller, payload: dict[str, str], peer: ipc.Peer = ROOT) -> ipc.Document:
        return controller.handle(ipc.parse_request(json.dumps(payload).encode()), peer)

    def begin(self, controller: Controller, purpose: str = "update", request_id: str = "request-1") -> ipc.Document:
        return self.send(controller, {"cmd": "begin-update", "purpose": purpose, "request_id": request_id})

    def status(self, controller: Controller, token: str) -> ipc.Document:
        return self.send(controller, {"cmd": "update-status", "token": token})

    def end(self, controller: Controller, token: str, outcome: str) -> ipc.Document:
        return self.send(controller, {"cmd": "end-update", "token": token, "outcome": outcome})

    def finish(self, controller: Controller, success: bool = True, empty: bool = True) -> None:
        job = controller.pending
        assert job is not None
        controller.complete(Completion(job, success, empty))

    def fresh(self, controller: Controller) -> None:
        controller.refresh()
        controller.observe(Observation(controller.sample, self.io.now, True, False, False, (), False))

    def arm(self, controller: Controller) -> None:
        """Drive to an observed, START-published state where authorize() passes."""
        self.fresh(controller)
        self.finish(controller)
        self.finish(controller)

    def token_of(self, response: ipc.Document) -> str:
        token = response.get("token")
        assert isinstance(token, str)
        return token


@final
class LifecycleReplayTests(LeaseHarness):
    def test_begin_replays_same_token_without_reinvalidating(self) -> None:
        # Given an established external hold
        controller = self.controller()
        first = self.begin(controller)
        generation = controller.generation
        jobs = list(self.io.jobs)
        # When the same request replays
        replay = self.begin(controller)
        # Then the token and generation contract both stand unchanged
        self.assertEqual(self.token_of(first), self.token_of(replay))
        self.assertEqual(controller.generation, generation)
        self.assertEqual(self.io.jobs, jobs)
        self.assertEqual(self.store.load(), ActiveLease(self.token_of(first), "request-1", "update"))

    def test_competing_request_and_purpose_rejected_as_busy(self) -> None:
        # Given an active hold for one request id
        controller = self.controller()
        token = self.token_of(self.begin(controller, "update", "request-1"))
        # When a competitor presents a different request id or purpose
        rival = self.begin(controller, "update", "request-2")
        cross = self.begin(controller, "uninstall", "request-1")
        # Then both are refused and the original record survives
        self.assertEqual(rival, {"ok": False, "error": "lifecycle busy"})
        self.assertEqual(cross, {"ok": False, "error": "lifecycle busy"})
        self.assertEqual(self.store.load(), ActiveLease(token, "request-1", "update"))
        self.assertTrue(controller.excluded)

    def test_internal_generation_hold_blocks_begin(self) -> None:
        # Given an internal acquire with no external record yet
        controller = self.controller()
        _ = controller.acquire_update()
        # When an external caller begins during that hold
        response = self.begin(controller)
        # Then the exclusive hold wins and no record appears
        self.assertEqual(response, {"ok": False, "error": "lifecycle busy"})
        self.assertIsNone(self.store.load())

    def test_invalid_purpose_rejected_before_acquisition(self) -> None:
        # Given a fresh controller
        controller = self.controller()
        # When a wire purpose outside the supported set arrives
        response = self.begin(controller, purpose="gaming")
        # Then it fails closed without taking the hold
        self.assertEqual(response, {"ok": False, "error": "invalid purpose"})
        self.assertFalse(controller.excluded)
        self.assertIsNone(self.store.load())

    def test_nonroot_peer_denied_before_dispatch(self) -> None:
        # Given a fresh controller and a non-root peer
        controller = self.controller()
        # When the peer attempts a root-only lifecycle verb
        response = self.send(controller, {"cmd": "begin-update", "purpose": "update", "request_id": "r"},
                             ipc.Peer(ipc.Uid(1000), ipc.Gid(1000)))
        # Then nothing is mutated
        self.assertEqual(response, {"ok": False, "error": "permission denied"})
        self.assertFalse(controller.excluded)
        self.assertIsNone(self.store.load())


@final
class RestartExclusionTests(LeaseHarness):
    def test_restart_adopts_record_and_blocks_until_fresh_empty_barrier(self) -> None:
        # Given a hold established by a first controller process
        first = self.controller()
        token = self.token_of(self.begin(first, "update", "request-1"))
        # When the controller restarts over the same durable record
        second = self.controller()
        with mock.patch("sparehash.controller.is_retired", return_value=False):
            second.start()
        # Then the record is adopted as launch inhibition
        self.assertTrue(second.excluded)
        self.assertFalse(second.authorize())
        # And the same request replays without re-invalidating
        generation = second.generation
        replay = self.begin(second, "update", "request-1")
        self.assertEqual(self.token_of(replay), token)
        self.assertEqual(second.generation, generation)
        # And a competing request cannot hijack the restart
        self.assertEqual(self.begin(second, "update", "request-2"),
                         {"ok": False, "error": "lifecycle busy"})
        # And commit fails closed while the stop proof is still pending
        self.assertEqual(self.end(second, token, "commit"),
                         {"ok": False, "error": "empty barrier not satisfied"})
        self.assertEqual(self.status(second, token), {"ok": True, "status": "pending"})
        self.assertTrue(second.excluded)
        # When the fresh empty barrier finally lands
        self.finish(second)
        # Then only a live proof reports ready and no start is ever published
        self.assertEqual(self.status(second, token), {"ok": True, "status": "ready"})
        self.assertNotIn("start", self.io.trace)
        self.assertFalse(second.authorize())
        # And commit releases the hold durably
        self.assertEqual(self.end(second, token, "commit"), {"ok": True})
        self.assertFalse(second.excluded)
        self.assertIsNone(self.store.load())

    def test_corrupt_record_inhibits_start_and_fails_closed(self) -> None:
        # Given an unreadable durable record
        _ = self.lease_path.write_text("{", encoding="utf-8")
        controller = self.controller()
        # When the controller starts and a client attempts lifecycle verbs
        with mock.patch("sparehash.controller.is_retired", return_value=False):
            controller.start()
        begin = self.begin(controller)
        status = self.status(controller, "a" * 32)
        # Then launch stays inhibited and every verb fails closed
        self.assertTrue(controller.excluded)
        self.assertFalse(controller.stopping)
        self.assertEqual(begin, {"ok": False, "error": "lease record unavailable"})
        self.assertEqual(status, {"ok": False, "error": "lease record unavailable"})

    def test_stale_token_cannot_touch_a_newer_lease(self) -> None:
        # Given a released first hold and an established second hold
        controller = self.controller()
        stale = self.token_of(self.begin(controller, "update", "request-1"))
        self.assertEqual(self.end(controller, stale, "abort"), {"ok": True})
        fresh = self.token_of(self.begin(controller, "update", "request-2"))
        # When the old token is presented for status and end
        # Then every use fails closed and the new hold stands
        self.assertNotEqual(stale, fresh)
        self.assertEqual(self.status(controller, stale), {"ok": False, "error": "invalid or stale token"})
        self.assertEqual(self.end(controller, stale, "commit"), {"ok": False, "error": "invalid or stale token"})
        self.assertEqual(self.end(controller, stale, "abort"), {"ok": False, "error": "invalid or stale token"})
        self.assertTrue(controller.excluded)
        self.assertEqual(self.store.load(), ActiveLease(fresh, "request-2", "update"))
        self.assertEqual(self.status(controller, fresh), {"ok": True, "status": "pending"})

    def test_unknown_token_reports_stale_before_any_begin(self) -> None:
        # Given a controller with no record at all
        controller = self.controller()
        # When a stray token arrives
        # Then it is indistinguishable from a stale one
        self.assertEqual(self.status(controller, "deadbeef"), {"ok": False, "error": "invalid or stale token"})
        self.assertEqual(self.end(controller, "deadbeef", "abort"),
                         {"ok": False, "error": "invalid or stale token"})


@final
class UpdateCommitTests(LeaseHarness):
    def test_commit_requires_barrier_and_forces_fresh_observation(self) -> None:
        # Given a mining-ready controller
        controller = self.controller()
        self.arm(controller)
        self.assertTrue(controller.authorize())
        # When an update hold begins
        token = self.token_of(self.begin(controller))
        # Then authorization is revoked immediately
        self.assertFalse(controller.authorize())
        # And commit before the stop proof fails closed with the hold retained
        self.assertEqual(self.end(controller, token, "commit"),
                         {"ok": False, "error": "empty barrier not satisfied"})
        self.assertTrue(controller.excluded)
        self.assertIsNotNone(self.store.load())
        # When the barrier lands and commit succeeds
        self.finish(controller)
        self.assertEqual(self.end(controller, token, "commit"), {"ok": True})
        # Then the durable record is gone and the hold is released
        self.assertFalse(controller.excluded)
        self.assertIsNone(controller.external)
        self.assertIsNone(self.store.load())
        # And mining cannot resume without a fresh observation
        self.assertFalse(controller.authorize())
        self.assertTrue(controller.collecting)
        # And once re-observed the start path recovers
        self.fresh(controller)
        self.finish(controller)
        self.finish(controller)
        self.assertTrue(controller.authorize())

    def test_abort_before_commit_drops_the_hold(self) -> None:
        # Given an active update hold
        controller = self.controller()
        token = self.token_of(self.begin(controller))
        # When the client aborts before committing
        with mock.patch("sparehash.controller.is_retired", return_value=False):
            response = self.end(controller, token, "abort")
        # Then the hold is fully released and a new request can begin
        self.assertEqual(response, {"ok": True})
        self.assertFalse(controller.excluded)
        self.assertIsNone(self.store.load())
        self.assertIsNone(controller.external)
        retry = self.begin(controller, "update", "request-2")
        self.assertNotEqual(self.token_of(retry), token)

    def test_failed_save_keeps_inhibition(self) -> None:
        # Given a store that cannot persist the record
        controller = self.controller()
        # When begin cannot persist before acknowledging
        with mock.patch.object(controller.store, "save", side_effect=LeaseError("injected save failure")):
            response = self.begin(controller)
        # Then the caller learns nothing usable and the hold stays
        self.assertEqual(response, {"ok": False, "error": "lease record unavailable"})
        self.assertTrue(controller.excluded)
        self.assertIsNone(controller.external)
        self.assertIsNone(self.store.load())
        self.assertEqual(self.begin(controller), {"ok": False, "error": "lifecycle busy"})

    def test_failed_clear_keeps_inhibition(self) -> None:
        # Given an armed hold whose record cannot be removed
        controller = self.controller()
        token = self.token_of(self.begin(controller))
        self.finish(controller)
        # When commit cannot durably drop the record
        with mock.patch.object(controller.store, "clear", side_effect=LeaseError("injected clear failure")):
            response = self.end(controller, token, "commit")
        # Then release is refused and the hold is retained
        self.assertEqual(response, {"ok": False, "error": "lease record unavailable"})
        self.assertTrue(controller.excluded)
        self.assertIsNotNone(self.store.load())


@final
class UninstallCommitTests(LeaseHarness):
    def test_commit_retires_before_ack_and_never_reenables(self) -> None:
        # Given an active uninstall hold with an empty barrier
        controller = self.controller()
        token = self.token_of(self.begin(controller, "uninstall"))
        self.finish(controller)
        self.assertEqual(self.retires, 0)
        # When commit acknowledges
        self.assertEqual(self.end(controller, token, "commit"), {"ok": True})
        # Then retirement was durably latched before the acknowledgment
        self.assertEqual(self.retires, 1)
        self.assertTrue(self.latch_path.exists())
        # And the hold is permanent: record kept, exclusion kept
        self.assertTrue(controller.permanent_hold)
        self.assertTrue(controller.excluded)
        self.assertEqual(self.store.load(), ActiveLease(token, "request-1", "uninstall"))
        # And no tick or resume ever publishes a start
        for _ in range(3):
            controller.tick()
        self.assertEqual(self.send(controller, {"cmd": "resume"}), {"ok": True})
        controller.tick()
        self.assertNotIn("start", self.io.trace)
        self.assertFalse(controller.authorize())

    def test_abort_after_commit_retains_the_permanent_hold(self) -> None:
        # Given a committed uninstall whose acknowledgment was lost
        controller = self.controller()
        token = self.token_of(self.begin(controller, "uninstall"))
        self.finish(controller)
        self.assertEqual(self.end(controller, token, "commit"), {"ok": True})
        # When the client retries with an abort
        self.assertEqual(self.end(controller, token, "abort"), {"ok": True})
        # Then the hold survives the late abort
        self.assertTrue(controller.excluded)
        self.assertTrue(controller.permanent_hold)
        self.assertEqual(self.store.load(), ActiveLease(token, "request-1", "uninstall"))
        # And a commit retry stays idempotent
        self.assertEqual(self.end(controller, token, "commit"), {"ok": True})
        self.assertEqual(self.retires, 2)
        self.assertTrue(controller.excluded)

    def test_commit_before_barrier_never_retires(self) -> None:
        # Given an uninstall hold whose stop proof is pending
        controller = self.controller()
        token = self.token_of(self.begin(controller, "uninstall"))
        # When commit arrives early
        response = self.end(controller, token, "commit")
        # Then nothing is retired and the hold stays
        self.assertEqual(response, {"ok": False, "error": "empty barrier not satisfied"})
        self.assertEqual(self.retires, 0)
        self.assertFalse(controller.permanent_hold)
        self.assertTrue(controller.excluded)

    def test_retire_failure_fails_closed(self) -> None:
        # Given an uninstall hold whose latch write fails
        controller = self.controller()
        self.fail_retire = True
        token = self.token_of(self.begin(controller, "uninstall"))
        self.finish(controller)
        # When commit cannot latch retirement
        response = self.end(controller, token, "commit")
        # Then no acknowledgment, no permanent flag, hold retained
        self.assertEqual(response, {"ok": False, "error": "lease record unavailable"})
        self.assertEqual(self.retires, 0)
        self.assertFalse(controller.permanent_hold)
        self.assertTrue(controller.excluded)
        self.assertIsNotNone(self.store.load())


@final
class ParseBoundsTests(unittest.TestCase):
    def test_rejects_malformed_lifecycle_frames(self) -> None:
        cases: tuple[dict[str, str], ...] = (
            {"cmd": "begin-update", "purpose": "update"},
            {"cmd": "begin-update", "purpose": "", "request_id": "r"},
            {"cmd": "begin-update", "purpose": "p" * 33, "request_id": "r"},
            {"cmd": "begin-update", "purpose": "update", "request_id": "r" * 129},
            {"cmd": "begin-update", "purpose": "update", "request_id": "r", "extra": "1"},
            {"cmd": "update-status", "token": ""},
            {"cmd": "update-status", "token": "z" * 32},
            {"cmd": "update-status", "token": "a" * 65},
            {"cmd": "update-status", "token": "aa", "extra": "1"},
            {"cmd": "end-update", "token": "aa", "outcome": "restart"},
            {"cmd": "end-update", "token": "aa"},
            {"cmd": "end-update", "token": "zz", "outcome": "commit"},
            {"cmd": "end-update", "token": "aa", "outcome": "commit", "extra": "1"},
        )
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ipc.IPCError):
                _ = ipc.parse_request(json.dumps(payload).encode())

    def test_accepts_bounded_hex_tokens(self) -> None:
        # Given the wire contracts existing clients rely on
        # When bounded hex spellings arrive
        short = ipc.parse_request(json.dumps({"cmd": "update-status", "token": "0x1"}).encode())
        long = ipc.parse_request(json.dumps({"cmd": "update-status", "token": "A1" * 32}).encode())
        # Then they parse as typed lifecycle requests
        self.assertIsInstance(short, ipc.LifecycleStatus)
        self.assertIsInstance(long, ipc.LifecycleStatus)

    def test_typed_lifecycle_fields_survive_parse(self) -> None:
        # Given a well-formed begin frame
        frame = json.dumps({"cmd": "begin-update", "purpose": "uninstall", "request_id": "req-9"}).encode()
        # When it parses
        request = ipc.parse_request(frame)
        # Then the typed fields are intact for strict dispatch
        self.assertIsInstance(request, ipc.LifecycleBegin)
        assert isinstance(request, ipc.LifecycleBegin)
        self.assertEqual(request.purpose, "uninstall")
        self.assertEqual(request.request_id, "req-9")


@final
class LeaseStoreTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.path = Path(self._temp.name) / "lease.json"
        self.store = LeaseStore(self.path)

    def test_round_trip_and_idempotent_clear(self) -> None:
        # Given a durable single record
        lease = ActiveLease("deadbeef" * 4, "request-1", "update")
        # When saved, loaded, and cleared twice
        self.store.save(lease)
        self.assertEqual(self.store.load(), lease)
        self.store.clear()
        self.assertIsNone(self.store.load())
        self.store.clear()

    def test_absent_record_is_not_an_error(self) -> None:
        self.assertIsNone(self.store.load())

    def test_untrustworthy_records_raise(self) -> None:
        corrupt: tuple[str, ...] = (
            "{",
            '"a string"',
            '{"token": "zz", "request_id": "r", "purpose": "update"}',
            '{"token": "aa", "request_id": "r"}',
            '{"token": "aa", "request_id": "r", "purpose": "update", "extra": 1}',
            '{"token": "aa", "request_id": "", "purpose": "update"}',
            '{"token": "aa", "request_id": "r", "purpose": "gaming"}',
            '{"token": "aa", "request_id": "' + "r" * 129 + '", "purpose": "update"}',
        )
        for text in corrupt:
            with self.subTest(text=text):
                _ = self.path.write_text(text, encoding="utf-8")
                with self.assertRaises(LeaseError):
                    _ = self.store.load()


@final
class BoundedCliTests(unittest.TestCase):
    @final
    class FakeClient:
        def __init__(self, replies: list[ipc.Document | BaseException]) -> None:
            self.replies = replies
            self.commands: list[ipc.Document] = []

        def __call__(self, command: ipc.Document, /, *, timeout: float) -> ipc.Document:
            _ = timeout
            self.commands.append(command)
            reply: ipc.Document | BaseException = (
                self.replies.pop(0) if self.replies else {"ok": False, "error": "exhausted"}
            )
            if isinstance(reply, BaseException):
                raise reply
            return reply

    def run_update(self, client: FakeClient) -> tuple[int, str]:
        captured = io.StringIO()
        with (mock.patch.object(cli_lifecycle, "current_binary", return_value=MANAGED),
              redirect_stderr(captured)):
            code = cli_lifecycle.update(geteuid=lambda: 0, trust=PathTrust(), client=client,
                                        prepare=lambda _stage: install_request(), install=install_done,
                                        request_id=REQUEST)
        return code, captured.getvalue()

    def test_begin_failure_exits_without_abort(self) -> None:
        # Given a controller that never answers the first exchange
        client = self.FakeClient([ipc.IPCError("connection error")])
        # When the update flow runs
        code, errors = self.run_update(client)
        # Then it exits bounded with no abort attempt
        self.assertEqual(code, 1)
        self.assertEqual([command["cmd"] for command in client.commands], ["begin-update"])
        self.assertIn("lifecycle IPC unavailable", errors)
        self.assertNotIn("abort unreachable", errors)

    def test_unreachable_abort_reports_retained_hold(self) -> None:
        # Given a lease that commits and then loses the transport both times
        client = self.FakeClient([{"ok": True, "token": TOKEN}, {"ok": True, "status": "ready"},
                                  ipc.IPCError("connection error"), ipc.IPCError("connection error")])
        # When the update flow runs
        code, errors = self.run_update(client)
        # Then both exchanges are attempted, the failure is reported, and exit is bounded
        self.assertEqual(code, 1)
        self.assertEqual([command["cmd"] for command in client.commands],
                         ["begin-update", "update-status", "end-update", "end-update"])
        self.assertIn("abort unreachable", errors)
        self.assertIn("controller retains the hold", errors)

    def test_poll_rate_stays_under_transport_limits(self) -> None:
        # Given the transport allows at most RATE_MAX_PER_WINDOW calls per window
        # When the CLI polls for the empty barrier
        # Then one full poll cycle fits strictly inside a single window
        self.assertLess(1 / cli_lifecycle.POLL_INTERVAL, ipc.RATE_MAX_PER_WINDOW)
        # And the total wait horizon stays bounded
        self.assertLessEqual(cli_lifecycle.LEASE_TIMEOUT, 60.0)


if __name__ == "__main__":
    _ = unittest.main()
