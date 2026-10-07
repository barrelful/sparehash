"""Deterministic safety scenarios; no external helper or host mutation."""

from __future__ import annotations

import socket
import tempfile
import warnings
from contextlib import ExitStack
from dataclasses import replace
from itertools import combinations
from pathlib import Path
from typing import final
from unittest.mock import patch

from sparehash.__main__ import main
from sparehash.controller import Controller
from sparehash.controller_io import Action, Completion, Observation, Ports, Source, run_loop
from sparehash.ipc import Command, DebugCommand, Gid, IPCError, Peer, Uid, Verb
from sparehash.state import BLOCKERS, BlockerState

from tests.controller_fixtures import ControllerFixture, FakePorts, ROOT
from tests.controller_scenarios import ControllerScenarios as ControllerScenarios


@final
class ControllerTests(ControllerFixture):

    def test_start_when_initialized_orders_readiness_before_launch(self) -> None:
        # Given
        self.c.start()
        # When
        self.fresh()
        self.finish()
        # Then
        self.assertLess(self.io.trace.index("bind"), self.io.trace.index("ready"))
        self.assertLess(self.io.trace.index("ready"), self.io.trace.index("start"))

    def test_inhibition_when_each_or_pair_of_blockers_is_active(self) -> None:
        # Given
        cases = [(name,) for name in BLOCKERS] + list(combinations(BLOCKERS, 2))
        # When / Then
        for names in cases:
            with self.subTest(names=names), tempfile.TemporaryDirectory() as directory:
                io = FakePorts()
                state = BlockerState(Path(directory) / "state", io.clock, 0)
                if "manual" in names:
                    _ = state.set("manual", "paused", "test")
                c = Controller(state, Ports(io, io, io, io.clock))
                c.start()
                c.observe(Observation(c.sample, 0, True, "battery" in names, False,
                                      ((Uid(1), "gaming" in names),), "thermal" in names))
                self.assertNotIn("start", io.trace)
                self.assertFalse(c.authorize())

    def test_inhibition_when_required_observations_are_unknown(self) -> None:
        # Given
        self.c.start()
        # When
        self.io.now = 10
        self.c.tick()
        # Then
        self.assertTrue(self.c.ready)
        self.assertEqual(set(self.state.active()), {"battery", "gaming", "thermal"})
        self.assertNotIn("start", self.io.trace)

    def test_battery_when_current_sources_disagree_uses_or(self) -> None:
        # Given / When / Then
        for upower, sysfs in ((True, False), (False, True), (None, None)):
            self.fresh(replace(self.snapshot(), upower=upower, sysfs=sysfs))
            self.assertIn("battery", self.state.active())

    def test_allow_when_inventory_confirms_ups_only(self) -> None:
        # Given / When
        self.fresh(replace(self.snapshot(), battery_present=False, upower=True, sysfs=None))
        # Then
        self.assertNotIn("battery", self.state.active())

    def test_manual_when_persisted_survives_new_controller(self) -> None:
        # Given
        self.command(Verb.PAUSE)
        # When
        state = BlockerState(self.path, self.io.clock, 0)
        # Then
        self.assertIn("manual", state.active())

    def test_manual_when_corrupt_inhibits_resume(self) -> None:
        # Given
        _ = self.path.write_text("bad-json")
        with warnings.catch_warnings(record=True):
            self.c = Controller(BlockerState(self.path, self.io.clock), self.c.ports)
        # When
        with self.assertLogs(level="ERROR"):
            response = self.c.handle(Command(Verb.RESUME), ROOT)
        # Then
        self.assertFalse(response["ok"])
        self.assertTrue(self.c.stopping)

    def test_dwell_when_blocker_clears_prevents_automatic_start(self) -> None:
        # Given
        self.state = BlockerState(self.path, self.io.clock, 60)
        self.c = Controller(self.state, self.c.ports)
        self.running()
        self.c.safety("battery", True)
        self.finish()
        # When
        self.fresh()
        self.finish()
        # Then
        self.assertIsNone(self.c.pending)
        self.assertFalse(self.c.authorize())

    def test_resume_when_other_blocker_exists_never_bypasses_it(self) -> None:
        # Given
        self.running()
        self.command(Verb.PAUSE)
        self.c.safety("battery", True)
        # When
        self.command(Verb.RESUME)
        # Then
        self.assertIn("battery", self.state.active())
        self.assertFalse(self.c.authorize())

    def test_hooks_when_users_overlap_union_is_preserved(self) -> None:
        # Given
        self.running()
        self.command(Verb.GAMING_START, 1)
        self.command(Verb.GAMING_START, 2)
        generation = self.c.generation
        # When
        self.command(Verb.GAMING_END, 1)
        self.command(Verb.GAMING_END, 1)
        # Then
        self.assertIn("gaming", self.state.active())
        self.assertEqual(self.c.generation, generation)

    def test_hook_end_when_provider_has_active_user_cannot_clear_it(self) -> None:
        # Given
        self.fresh(replace(self.snapshot(), gaming=((Uid(2), True), (Uid(1), False))))
        # When
        self.command(Verb.GAMING_END, 2)
        # Then
        self.assertIn("gaming", self.state.active())

    def test_debug_clear_when_real_blocker_exists_preserves_it(self) -> None:
        # Given
        self.fresh(replace(self.snapshot(), thermal=True))
        _ = self.c.handle(DebugCommand(Verb.DEBUG_SET, "thermal"), ROOT)
        # When
        _ = self.c.handle(DebugCommand(Verb.DEBUG_CLEAR, "thermal"), ROOT)
        # Then
        self.assertIn("thermal", self.state.active())

    def test_blocker_when_start_queued_revokes_generation_and_late_completion(self) -> None:
        # Given
        self.c.start()
        self.fresh()
        self.finish()
        queued = self.c.pending
        assert queued is not None
        self.assertTrue(self.c.authorize())
        # When
        self.c.safety("battery", True)
        self.c.complete(Completion(queued, True))
        # Then
        self.assertFalse(self.c.authorize())
        self.assertEqual(self.io.jobs[-1].action, Action.STOP)
        self.assertFalse(self.c.running)

    def test_notify_when_send_fails_never_starts(self) -> None:
        # Given
        self.io.notify_fails = True
        # When
        with self.assertLogs(level="ERROR"):
            self.fresh()
        # Then
        self.assertFalse(self.c.ready)
        self.assertNotIn("start", self.io.trace)

    def test_update_when_cgroup_not_empty_keeps_launch_exclusion(self) -> None:
        # Given
        self.running()
        token = self.c.acquire_update()
        # When
        with self.assertLogs(level="WARNING"):
            self.finish(empty=False)
        # Then
        self.assertFalse(self.c.update_ready(token))
        self.assertFalse(self.c.release_update(token))
        self.assertFalse(self.c.authorize())

    def test_reload_when_stop_succeeds_requires_fresh_reauthorization(self) -> None:
        # Given
        self.running()
        old = self.c.operation
        # When
        self.command(Verb.RELOAD)
        self.finish()
        # Then
        self.assertNotEqual(old, self.c.operation)
        self.assertTrue(self.c.collecting)
        self.assertFalse(self.c.authorize())

    def test_authorization_when_nonroot_or_socket_unavailable_denies(self) -> None:
        # Given
        self.running()
        # When / Then
        self.assertFalse(self.c.handle(Command(Verb.AUTHORIZE_START), Peer(Uid(1), Gid(1)))["ok"])
        with patch("sparehash.controller_io.call", side_effect=IPCError("timeout")):
            self.assertEqual(main(["__authorize_miner__"], geteuid=lambda: 0), 1)

    def test_selector_when_safety_and_completion_ready_dispatches_safety_first(self) -> None:
        # Given
        self.running()
        with ExitStack() as stack:
            left, right = socket.socketpair()
            _ = stack.enter_context(left)
            _ = stack.enter_context(right)
            _ = left.send(b"x")
            _ = right.send(b"x")
            order: list[str] = []
            def safety() -> None:
                _ = left.recv(1)
                order.append("safety")
                self.c.safety("battery", True)
            def completion() -> None:
                _ = right.recv(1)
                order.append("completion")
                self.c.shutdown()
            # When
            run_loop(self.c, (Source(left.fileno(), safety), Source(right.fileno(), completion)))
            # Then
            self.assertEqual(order, ["safety", "completion"])
            self.assertFalse(self.c.authorize())
