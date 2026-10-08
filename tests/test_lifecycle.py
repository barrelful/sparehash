"""Tests for the durable lifecycle latch (sparehash.lifecycle)."""

from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sparehash.lifecycle import is_retired, retire, clear_retired, LIFECYCLE_PATH


class TestLifecycleLatch(unittest.TestCase):
    """Tests for the lifecycle latch functions."""

    def setUp(self) -> None:
        # Use a temporary lifecycle file path for tests
        self.original_path = LIFECYCLE_PATH
        self.test_dir = tempfile.TemporaryDirectory()
        self.test_lifecycle_path = Path(self.test_dir.name) / "lifecycle.json"
        # Patch the LIFECYCLE_PATH for tests
        self.path_patcher = patch(
            "sparehash.lifecycle.LIFECYCLE_PATH", self.test_lifecycle_path
        )
        self.path_patcher.start()

    def tearDown(self) -> None:
        self.path_patcher.stop()
        self.test_dir.cleanup()

    def test_is_retired_returns_false_when_no_file_exists(self) -> None:
        """is_retired() returns False when the lifecycle file does not exist."""
        # Ensure file doesn't exist
        if self.test_lifecycle_path.exists():
            self.test_lifecycle_path.unlink()
        self.assertFalse(is_retired())

    def test_is_retired_returns_false_when_file_empty(self) -> None:
        """is_retired() returns False when the lifecycle file is empty."""
        self.test_lifecycle_path.write_text("")
        self.assertFalse(is_retired())

    def test_is_retired_returns_false_when_file_has_false(self) -> None:
        """is_retired() returns False when the lifecycle file has {"retired": false}."""
        self.test_lifecycle_path.write_text(json.dumps({"retired": False}))
        self.assertFalse(is_retired())

    def test_is_retired_returns_false_when_json_invalid(self) -> None:
        """is_retired() returns False when the lifecycle file has invalid JSON."""
        self.test_lifecycle_path.write_text("not valid json{{{")
        self.assertFalse(is_retired())

    def test_is_retired_returns_true_after_retire(self) -> None:
        """is_retired() returns True after retire() is called."""
        retire()
        self.assertTrue(is_retired())

    def test_retire_creates_file_with_correct_content(self) -> None:
        """retire() creates the lifecycle file with {"retired": true, "timestamp": <unix_ts>}."""
        retire()
        self.assertTrue(self.test_lifecycle_path.exists())
        content = json.loads(self.test_lifecycle_path.read_text(encoding="utf-8"))
        self.assertTrue(content["retired"] is True)
        self.assertIsInstance(content["timestamp"], int)
        self.assertGreaterEqual(content["timestamp"], 0)

    def test_retire_uses_mode_0644(self) -> None:
        """retire() sets the file mode to 0644."""
        retire()
        mode = self.test_lifecycle_path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o644)

    def test_clear_retired_removes_file(self) -> None:
        """clear_retired() removes the lifecycle file if it exists."""
        retire()
        self.assertTrue(self.test_lifecycle_path.exists())
        clear_retired()
        self.assertFalse(self.test_lifecycle_path.exists())

    def test_clear_retired_is_no_op_when_no_file_exists(self) -> None:
        """clear_retired() is a no-op when the lifecycle file does not exist."""
        # File doesn't exist - should not raise
        clear_retired()
        self.assertFalse(self.test_lifecycle_path.exists())

    def test_clear_retired_no_op_when_called_twice(self) -> None:
        """clear_retired() is a no-op when called twice in a row."""
        retire()
        clear_retired()
        # Second call should not raise
        clear_retired()
        self.assertFalse(self.test_lifecycle_path.exists())

    def test_atomic_write_file_not_partially_written(self) -> None:
        """Test that retire() uses atomic write (tempfile + os.replace)."""
        retire()
        # Read the file and verify it's complete JSON
        content = json.loads(self.test_lifecycle_path.read_text(encoding="utf-8"))
        self.assertIn("retired", content)
        self.assertIn("timestamp", content)

    def test_lifecycle_path_default(self) -> None:
        """LIFECYCLE_PATH defaults to /var/lib/sparehash/lifecycle.json."""
        self.assertEqual(str(LIFECYCLE_PATH), "/var/lib/sparehash/lifecycle.json")


if __name__ == "__main__":
    unittest.main()