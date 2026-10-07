"""Lifecycle edge cases loaded by the focused test_controller command."""

from dataclasses import replace
from typing import final
from unittest.mock import patch

from sparehash.__main__ import main
from sparehash.controller import Controller
from sparehash.controller_io import Action, Completion, LifecycleBusy, Mode, SampleId
from sparehash.ipc import Command, DebugCommand, Verb
from sparehash.state import BlockerState

from tests.controller_fixtures import ControllerFixture, ROOT


@final
class ControllerScenarios(ControllerFixture):
    def test_resume_when_only_manual_blocks_bypasses_dwell(self) -> None:
        # Given
        self.state = BlockerState(self.path, self.io.clock, 60)
        self.c = Controller(self.state, self.c.ports)
        self.running()
        self.command(Verb.PAUSE)
        self.finish()
        # When
        self.command(Verb.RESUME)
        self.finish()
        self.fresh()
        # Then
        self.assertEqual(self.io.jobs[-1].action, Action.START)
        self.assertTrue(self.c.authorize())

    def test_jobs_when_stop_fails_retries_are_bounded(self) -> None:
        # Given
        self.running()
        self.c.safety("battery", True)
        count = len(self.io.jobs)
        # When
        with self.assertLogs(level="WARNING"):
            for _ in range(3):
                self.finish(success=False)
                self.io.now += 1
                self.c.tick()
        # Then
        self.assertEqual(len(self.io.jobs) - count, 2)
        self.assertFalse(self.c.empty)
        self.assertIsNone(self.c.pending)

    def test_jobs_when_start_fails_revokes_permission_before_retry(self) -> None:
        # Given
        self.c.start()
        self.fresh()
        self.finish()
        # When
        with self.assertLogs(level="WARNING"):
            self.finish(success=False)
        # Then
        self.assertFalse(self.c.authorize())
        self.assertIsNone(self.io.published())
        self.io.now += 1
        self.c.tick()
        self.assertEqual(self.io.jobs[-1].action, Action.STOP)

    def test_jobs_when_deadline_expires_rejects_late_completion(self) -> None:
        # Given
        self.running()
        token = self.c.acquire_update()
        job = self.c.pending
        assert job is not None
        # When
        self.io.now = job.deadline
        with self.assertLogs(level="WARNING"):
            self.c.tick()
        self.c.complete(Completion(job, True, True))
        # Then
        self.assertFalse(self.c.update_ready(token))

    def test_update_when_barrier_succeeds_requires_new_snapshot(self) -> None:
        # Given
        self.running()
        token = self.c.acquire_update()
        self.finish()
        self.assertTrue(self.c.update_ready(token))
        # When
        released = self.c.release_update(token)
        # Then
        self.assertTrue(released)
        self.assertTrue(self.c.collecting)
        self.assertFalse(self.c.authorize())
        self.assertNotEqual(self.c.generation, token)

    def test_update_when_blocker_changes_invalidates_old_token(self) -> None:
        # Given
        self.running()
        token = self.c.acquire_update()
        self.finish()
        # When
        self.c.safety("battery", True)
        # Then
        self.assertFalse(self.c.update_ready(token))
        self.assertFalse(self.c.release_update(token))
        self.assertTrue(self.c.excluded)

    def test_update_when_lease_owned_rejects_competing_updater_and_reload(self) -> None:
        # Given
        self.running()
        token = self.c.acquire_update()
        self.finish()
        # When / Then
        with self.assertRaises(LifecycleBusy):
            _ = self.c.acquire_update()
        self.assertFalse(self.c.handle(Command(Verb.RELOAD), ROOT)["ok"])
        self.assertTrue(self.c.update_ready(token))

    def test_benchmark_when_requested_serializes_same_unit_mode(self) -> None:
        # Given
        self.running()
        # When
        accepted = self.c.handle(Command(Verb.BENCHMARK), ROOT)
        self.finish()
        self.fresh()
        # Then
        self.assertTrue(accepted["ok"])
        self.assertEqual(self.io.jobs[-1].operation.mode, Mode.BENCHMARK)
        self.assertTrue(self.c.authorize())
        self.assertFalse(self.c.authorize())

    def test_benchmark_when_blocker_arrives_returns_to_mine_only_after_stop(self) -> None:
        # Given
        self.running()
        self.command(Verb.BENCHMARK)
        self.finish()
        self.fresh()
        self.finish()
        # When
        self.c.safety("battery", True)
        self.finish()
        self.fresh(replace(self.snapshot(), upower=True))
        # Then
        self.assertEqual(self.c.mode, Mode.MINE)
        self.assertFalse(self.c.authorize())
        self.assertEqual(self.io.jobs[-1].action, Action.STOP)

    def test_benchmark_when_completed_restarts_normal_only_after_refresh(self) -> None:
        # Given
        self.running()
        self.command(Verb.BENCHMARK)
        self.finish()
        self.fresh()
        self.finish()
        operation = self.c.operation
        # When
        self.c.benchmark_finished(operation)
        self.finish()
        # Then
        self.assertEqual(self.c.mode, Mode.MINE)
        self.assertTrue(self.c.collecting)
        self.assertFalse(self.c.authorize())

    def test_reconciliation_when_snapshot_stales_inhibits_permission(self) -> None:
        # Given
        self.running()
        # When
        self.io.now = 11
        self.c.tick()
        # Then
        self.assertFalse(self.c.authorize())
        self.assertEqual(set(self.state.active()), {"battery", "gaming", "thermal"})

    def test_observation_when_previous_query_returns_cannot_clear_new_event(self) -> None:
        # Given
        self.running()
        self.c.refresh()
        previous = self.snapshot()
        self.c.safety("battery", True)
        # When
        self.c.observe(previous)
        # Then
        self.assertIn("battery", self.state.active())
        self.assertFalse(self.c.authorize())

    def test_observation_when_future_or_wrong_sample_is_rejected(self) -> None:
        # Given
        self.c.start()
        # When
        self.c.observe(replace(self.snapshot(), at=1))
        self.c.observe(replace(self.snapshot(), sample=SampleId(999)))
        # Then
        self.assertFalse(self.c.ready)

    def test_debug_manual_when_real_pause_exists_never_clears_persistence(self) -> None:
        # Given
        self.command(Verb.PAUSE)
        _ = self.c.handle(DebugCommand(Verb.DEBUG_SET, "manual"), ROOT)
        # When
        _ = self.c.handle(DebugCommand(Verb.DEBUG_CLEAR, "manual"), ROOT)
        # Then
        self.assertIn("manual", BlockerState(self.path).active())

    def test_duplicate_when_pause_or_hook_repeats_does_not_queue_more_jobs(self) -> None:
        # Given
        self.running()
        self.command(Verb.PAUSE)
        before = len(self.io.jobs)
        # When
        self.command(Verb.PAUSE)
        self.c.apply()
        # Then
        self.assertEqual(len(self.io.jobs), before)

    def test_authorization_when_publication_generation_mismatches_denies(self) -> None:
        # Given
        self.running()
        old = self.c.operation
        self.command(Verb.RELOAD)
        self.finish()
        self.fresh()
        self.io.operation = old
        # When
        allowed = self.c.authorize()
        # Then
        self.assertFalse(allowed)

    def test_execcondition_when_reply_is_not_literal_true_denies(self) -> None:
        # Given / When / Then
        for reply, expected in (({"ok": True}, 0), ({"ok": 1}, 1), ({"ok": False}, 1), ({}, 1)):
            with patch("sparehash.controller_io.call", return_value=reply):
                self.assertEqual(main(["__authorize_miner__"], geteuid=lambda: 0), expected)

    def test_shutdown_when_start_is_pending_denies_and_stops(self) -> None:
        # Given
        self.c.start()
        self.fresh()
        self.finish()
        # When
        self.c.shutdown()
        # Then
        self.assertFalse(self.c.authorize())
        self.assertEqual(self.io.jobs[-1].action, Action.STOP)

    def test_clear_when_stop_is_inflight_still_requires_current_empty_barrier(self) -> None:
        # Given
        self.running()
        self.c.safety("battery", True)
        old = self.c.pending
        assert old is not None
        # When
        self.fresh()
        self.c.complete(Completion(old, True, True))
        # Then
        self.assertFalse(self.c.running)
        self.assertEqual(self.io.jobs[-1].action, Action.STOP)
        self.assertFalse(self.c.authorize())

    def test_gaming_when_one_live_user_is_unknown_inhibits_start(self) -> None:
        # Given / When
        self.fresh(replace(self.snapshot(), gaming=((ROOT.uid, None),)))
        # Then
        self.assertIn("gaming", self.state.active())
        self.assertFalse(self.c.authorize())

    def test_automatic_start_when_dwell_elapsed_and_new_snapshot_safe(self) -> None:
        # Given
        self.state = BlockerState(self.path, self.io.clock, 60)
        self.c = Controller(self.state, self.c.ports)
        self.running()
        self.c.safety("battery", True)
        self.finish()
        self.fresh()
        self.finish()
        # When
        self.io.now = 60
        self.fresh()
        # Then
        self.assertEqual(self.io.jobs[-1].action, Action.START)
