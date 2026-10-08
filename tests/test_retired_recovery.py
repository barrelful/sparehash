"""Controller retired-recovery path: when the lifecycle latch is set, the
controller must stop without calling notify_ready (preserving systemd
Type=notify), and must clear the latch on next install to recover."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import final
from unittest import mock

from sparehash import lifecycle
from sparehash.controller import Controller
from sparehash.controller_io import Ports
from sparehash.state import BlockerState

from tests.controller_fixtures import FakePorts


@final
class RetiredRecoveryTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.latch = Path(self._tmp.name) / "lifecycle.json"
        self._io = FakePorts()
        self._ports = Ports(self._io, self._io, self._io, self._io.clock)
        self._state = BlockerState(self.latch.parent / "state.json", self._io.clock, 0)

    def _fresh_controller(self) -> Controller:
        return Controller(self._state, self._ports)

    def test_start_when_retired_skips_notify_ready(self) -> None:
        # Given
        lifecycle.retire(self.latch)
        # When
        controller = self._fresh_controller()
        with mock.patch("sparehash.controller.is_retired", return_value=True):
            controller.start()
        # Then
        self.assertTrue(controller.stopping)
        self.assertFalse(controller.ready)
        self.assertNotIn("bind", self._io.trace)
        self.assertNotIn("ready", self._io.trace)

    def test_start_when_not_retired_proceeds_normally(self) -> None:
        # Given
        # When
        controller = self._fresh_controller()
        with mock.patch("sparehash.controller.is_retired", return_value=False):
            controller.start()
        # Then
        self.assertFalse(controller.stopping)

    def test_clear_retired_allows_restart(self) -> None:
        # Given
        lifecycle.retire(self.latch)
        self.assertTrue(lifecycle.is_retired(self.latch))
        # When
        lifecycle.clear_retired(self.latch)
        # Then
        self.assertFalse(lifecycle.is_retired(self.latch))
        controller = self._fresh_controller()
        with mock.patch("sparehash.controller.is_retired", return_value=False):
            controller.start()
        self.assertFalse(controller.stopping)


if __name__ == "__main__":
    _ = unittest.main()
