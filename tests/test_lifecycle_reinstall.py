"""Explicit reinstall clear: completed uninstall hold and retired latch together.

Covers the recovery gap left by the durable external uninstall lease: after a
committed uninstall retains both lifecycle.json and lifecycle-lease.json, the
install --clear-lifecycle-latch boundary must remove the completed hold so a
fresh controller can mine, while every failure keeps the system retired.
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from typing import final
from unittest import mock

from sparehash import install, lifecycle
from sparehash.controller import Controller
from sparehash.controller_io import Completion, Observation, Ports
from sparehash.lifecycle_lease import LEASE_PATH, ActiveLease, LeaseError, LeaseStore
from sparehash.state import BlockerState

from tests.controller_fixtures import FakePorts

UNINSTALL = ActiveLease("a" * 32, "request-1", "uninstall")
UPDATE = ActiveLease("b" * 32, "request-1", "update")


@final
class ReinstallClearTests(unittest.TestCase):
    """The explicit clear boundary against retired latch and durable hold fixtures."""

    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self._temp: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        root = Path(self._temp.name)
        self.latch: Path = root / "lifecycle.json"
        self.lease_path: Path = root / "lifecycle-lease.json"
        self.store: LeaseStore = LeaseStore(self.lease_path)
        self.io: FakePorts = FakePorts()
        self.state_path: Path = root / "state.json"

    def retire_with(self, lease: ActiveLease | None) -> None:
        lifecycle.retire(self.latch)
        if lease is not None:
            self.store.save(lease)

    def fresh_controller(self) -> Controller:
        state = BlockerState(self.state_path, self.io.clock, 0)
        return Controller(state, Ports(self.io, self.io, self.io, self.io.clock),
                          lease_store=LeaseStore(self.lease_path))

    def run_install(self, *flags: str) -> tuple[int, str]:
        """Run install.main on the fixture latch, stopping at the first probe."""
        captured = io.StringIO()
        with (mock.patch("sparehash.lifecycle.LIFECYCLE_PATH", self.latch),
              mock.patch("sparehash.install.platform.machine", return_value="x86_64"),
              mock.patch("sparehash.install.Capabilities.detect", side_effect=OSError("injected stop")),
              redirect_stderr(captured)):
            code = install.main([*flags], geteuid=lambda: 0)
        return code, captured.getvalue()

    def test_completed_uninstall_clear_lifts_the_hold_for_a_fresh_controller(self) -> None:
        # Given a completed uninstall: retired latch plus retained durable hold
        self.retire_with(UNINSTALL)
        # When the explicit reinstall clear runs
        lifecycle.clear_lifecycle_latch(self.latch)
        # Then the hold and the latch are both gone
        self.assertIsNone(self.store.load())
        self.assertFalse(lifecycle.is_retired(self.latch))
        # And a fresh controller starts uninhibited
        controller = self.fresh_controller()
        with mock.patch("sparehash.lifecycle.LIFECYCLE_PATH", self.latch):
            controller.start()
        self.assertFalse(controller.excluded)
        self.assertFalse(controller.stopping)
        self.assertFalse(controller.permanent_hold)
        # And it can reach launch authorization on a clear observation
        controller.refresh()
        controller.observe(Observation(controller.sample, self.io.now, True, False, False, (), False))
        job = controller.pending
        assert job is not None
        controller.complete(Completion(job, True, True))
        self.assertTrue(controller.authorize())

    def test_failed_hold_clear_retains_latch_and_hold(self) -> None:
        # Given a completed uninstall whose sidecar removal fails
        self.retire_with(UNINSTALL)
        # When
        with mock.patch.object(LeaseStore, "clear", side_effect=LeaseError("injected clear failure")):
            with self.assertRaises(LeaseError):
                lifecycle.clear_lifecycle_latch(self.latch)
        # Then the hold and the retirement latch both stand
        self.assertEqual(self.store.load(), UNINSTALL)
        self.assertTrue(lifecycle.is_retired(self.latch))

    def test_active_update_hold_survives_the_explicit_clear(self) -> None:
        # Given a retired system that also carries a live update hold
        self.retire_with(UPDATE)
        # When
        with self.assertRaises(LeaseError):
            lifecycle.clear_lifecycle_latch(self.latch)
        # Then the update hold and the latch are untouched
        self.assertEqual(self.store.load(), UPDATE)
        self.assertTrue(lifecycle.is_retired(self.latch))

    def test_malformed_record_keeps_the_system_retired(self) -> None:
        # Given a retired latch with an untrustworthy sidecar record
        lifecycle.retire(self.latch)
        _ = self.lease_path.write_text("{", encoding="utf-8")
        # When
        with self.assertRaises(LeaseError):
            lifecycle.clear_lifecycle_latch(self.latch)
        # Then nothing is removed
        self.assertTrue(lifecycle.is_retired(self.latch))
        self.assertEqual(self.lease_path.read_text(encoding="utf-8"), "{")

    def test_missing_latch_never_erases_an_active_hold(self) -> None:
        # Given an uninstall in progress: durable hold, no retired latch yet
        self.store.save(UNINSTALL)
        # When the explicit clear is called
        lifecycle.clear_lifecycle_latch(self.latch)
        # Then the hold is untouched
        self.assertEqual(self.store.load(), UNINSTALL)

    def test_failed_latch_removal_keeps_retirement_after_the_hold_clear(self) -> None:
        # Given a completed uninstall whose latch removal fails at the filesystem
        self.retire_with(UNINSTALL)
        original = Path.unlink

        def failing_unlink(target: Path, missing_ok: bool = False) -> None:
            if target.name == self.latch.name:
                raise OSError("injected latch failure")
            original(target, missing_ok)

        # When the explicit clear runs
        with mock.patch.object(Path, "unlink", failing_unlink), self.assertRaises(OSError):
            lifecycle.clear_lifecycle_latch(self.latch)
        # Then the hold was cleared first and retirement still inhibits startup
        self.assertIsNone(self.store.load())
        self.assertTrue(lifecycle.is_retired(self.latch))

    def test_clear_retired_surfaces_filesystem_failure(self) -> None:
        # Given a latch whose removal fails
        lifecycle.retire(self.latch)
        # When
        with mock.patch.object(Path, "unlink", side_effect=OSError("injected unlink failure")):
            with self.assertRaises(OSError):
                lifecycle.clear_retired(self.latch)
        # Then the failure is visible and the latch still inhibits startup
        self.assertTrue(lifecycle.is_retired(self.latch))

    def test_store_path_is_derived_beside_the_production_latch(self) -> None:
        # Given the production latch location
        # When the sidecar path is derived beside it
        # Then it is exactly the durable lease record path
        self.assertEqual(lifecycle.LIFECYCLE_PATH.parent / LEASE_PATH.name, LEASE_PATH)
        self.assertEqual(str(LEASE_PATH), "/var/lib/sparehash/lifecycle-lease.json")

    def test_explicit_flag_clears_both_records_before_any_install_work(self) -> None:
        # Given a retired system with a completed uninstall hold
        self.retire_with(UNINSTALL)
        # When the installer runs with the explicit flag and stops at the first probe
        code, errors = self.run_install("--clear-lifecycle-latch")
        # Then the clear already happened before any install work
        self.assertEqual(code, 1)
        self.assertIn("injected stop", errors)
        self.assertNotIn("lifecycle latch retained", errors)
        self.assertFalse(lifecycle.is_retired(self.latch))
        self.assertIsNone(self.store.load())

    def test_explicit_flag_reports_a_malformed_record_without_a_traceback(self) -> None:
        # Given a retired latch with a corrupt sidecar record
        lifecycle.retire(self.latch)
        _ = self.lease_path.write_text("{", encoding="utf-8")
        # When the installer runs with the explicit flag
        code, errors = self.run_install("--clear-lifecycle-latch")
        # Then the failure is visible, bounded, and retirement stands
        self.assertEqual(code, 1)
        self.assertIn("lifecycle latch retained", errors)
        self.assertNotIn("Traceback", errors)
        self.assertTrue(lifecycle.is_retired(self.latch))
        self.assertEqual(self.lease_path.read_text(encoding="utf-8"), "{")

    def test_explicit_flag_without_a_latch_leaves_an_active_hold(self) -> None:
        # Given an uninstall hold with no retired latch yet
        self.store.save(UNINSTALL)
        # When the installer runs with the explicit flag
        code, errors = self.run_install("--clear-lifecycle-latch")
        # Then the flag clears nothing and the hold stands
        self.assertEqual(code, 1)
        self.assertNotIn("lifecycle latch cleared", errors)
        self.assertEqual(self.store.load(), UNINSTALL)


if __name__ == "__main__":
    _ = unittest.main()
