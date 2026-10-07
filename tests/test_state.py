"""Behavioral tests for the central blocker registry."""

from __future__ import annotations

import itertools
import tempfile
import unittest
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sparehash.state import BLOCKERS, BlockerState, StatePersistenceError


class FakeClock:
    now: float

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@contextmanager
def state_fixture() -> Iterator[tuple[Path, FakeClock, BlockerState]]:
    with tempfile.TemporaryDirectory() as directory:
        state_file = Path(directory) / "state.json"
        clock = FakeClock()
        yield state_file, clock, BlockerState(state_file, clock)


class BlockerStateTests(unittest.TestCase):
    def test_each_blocker_inhibits_mining(self) -> None:
        with state_fixture() as (state_file, clock, _):
            for blocker in BLOCKERS:
                with self.subTest(blocker=blocker):
                    state = BlockerState(state_file, clock)
                    _ = state.set(blocker, "reason", "test")
                    self.assertFalse(state.mining_allowed())

    def test_all_pairs_and_individual_clear_preserve_overlap(self) -> None:
        with state_fixture() as (state_file, clock, _):
            for left, right in itertools.combinations(("battery", "gaming", "thermal"), 2):
                with self.subTest(left=left, right=right):
                    state = BlockerState(state_file, clock)
                    _ = state.set(left, left, "test")
                    _ = state.set(right, right, "test")
                    transition = state.clear(left)
                    self.assertFalse(state.mining_allowed())
                    self.assertIn("other blockers", transition.message)
                    _ = state.clear(right)
                    self.assertTrue(state.mining_allowed())

    def test_manual_pause_survives_reconstruction(self) -> None:
        with state_fixture() as (state_file, clock, state):
            _ = state.set("manual", "operator pause", "cli")
            reloaded = BlockerState(state_file, clock)
            self.assertEqual(reloaded.active()["manual"][0], "operator pause")
            self.assertFalse(reloaded.mining_allowed())
            self.assertEqual(state_file.stat().st_mode & 0o777, 0o640)

    def test_corrupt_persistence_fails_safe_until_explicit_recovery(self) -> None:
        with state_fixture() as (state_file, clock, _):
            _ = state_file.write_text("{broken", encoding="utf-8")
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                state = BlockerState(state_file, clock)
            self.assertTrue(caught)
            self.assertFalse(state.mining_allowed())
            with self.assertRaises(StatePersistenceError):
                _ = state.clear("manual")
            _ = state.recover_corrupt_state(confirm_clear=True)
            self.assertTrue(state.mining_allowed())

    def test_corrupt_manual_recovery_keeps_battery_blocker_and_message(self) -> None:
        with state_fixture() as (state_file, clock, _):
            _ = state_file.write_text("{broken", encoding="utf-8")
            with warnings.catch_warnings(record=True):
                warnings.simplefilter("always")
                state = BlockerState(state_file, clock)
            _ = state.set("battery", "on battery", "power")

            transition = state.recover_corrupt_state(confirm_clear=True)

            self.assertFalse(state.mining_allowed())
            self.assertIn("remains stopped", transition.message)
            self.assertNotIn("Mining started", transition.message)
            self.assertEqual(state.active()["battery"][0], "on battery")
            self.assertNotIn("manual", state.active())

    def test_unknown_blocker_is_rejected(self) -> None:
        with state_fixture() as (_, _, state):
            with self.assertRaises(ValueError):
                _ = state.set("unknown", "reason", "test")

    def test_dwell_and_manual_resume_do_not_override_hard_blockers(self) -> None:
        with state_fixture() as (_, clock, state):
            state.automatic_stop()
            clock.now += 59
            self.assertFalse(state.auto_start_allowed())
            _ = state.set("manual", "pause", "test")
            _ = state.resume()
            self.assertTrue(state.auto_start_allowed())
            _ = state.set("battery", "battery", "power")
            _ = state.set("manual", "pause", "test")
            _ = state.resume()
            self.assertFalse(state.auto_start_allowed())


if __name__ == "__main__":
    _ = unittest.main()
