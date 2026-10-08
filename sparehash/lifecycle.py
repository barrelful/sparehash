"""Durable lifecycle latch for SpareHash.

The lifecycle latch is a file-based mechanism at /var/lib/sparehash/lifecycle.json
that persists across restarts. After a successful uninstall, the latch is set so
the controller refuses to start until the latch is cleared. This prevents the
controller from restarting after an uninstall has completed.

STDLIB ONLY: tempfile, os, pathlib, json
"""

from __future__ import annotations

import json
import os
import time
import tempfile
from pathlib import Path
from typing import Final

LIFECYCLE_PATH: Final = Path("/var/lib/sparehash/lifecycle.json")


def is_retired(path: Path | None = None) -> bool:
    """Return True if the lifecycle latch is set.

    Args:
        path: Optional path to the lifecycle file. Defaults to LIFECYCLE_PATH.

    Returns True if the lifecycle file exists and contains
    {"retired": true}. Returns False otherwise (file missing,
    file empty, or file contains {"retired": false}).
    """
    target = path or LIFECYCLE_PATH
    try:
        text = target.read_text(encoding="utf-8").strip()
        if not text:
            return False
        data = json.loads(text)
        return data.get("retired") is True
    except (OSError, json.JSONDecodeError):
        return False


def retire(path: Path | None = None) -> None:
    """Atomically set the durable retired latch.

    Args:
        path: Optional path to the lifecycle file. Defaults to LIFECYCLE_PATH.

    Writes {"retired": true, "timestamp": <unix_ts>} to the lifecycle file.
    The file is mode 0644 and written atomically (tempfile + os.replace).
    Handles OSError gracefully (silently fails; the latch is a best-effort guard).
    """
    target = path or LIFECYCLE_PATH
    try:
        dir_path = target.parent
        dir_path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", dir=dir_path, delete=False
        ) as f:
            json.dump({"retired": True, "timestamp": int(time.time())}, f)
            tmp_path = f.name
        os.replace(tmp_path, target)
        try:
            os.chmod(str(target), 0o644)
        except OSError:
            pass
    except OSError:
        pass


def clear_retired(path: Path | None = None) -> None:
    """Remove the lifecycle latch file if it exists.

    Args:
        path: Optional path to the lifecycle file. Defaults to LIFECYCLE_PATH.

    A no-op if the file does not exist. Handles OSError gracefully.
    """
    target = path or LIFECYCLE_PATH
    try:
        target.unlink(missing_ok=True)
    except OSError:
        pass