# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Durable lifecycle latch for SpareHash.

The lifecycle latch is a file-based mechanism at /var/lib/sparehash/lifecycle.json
that persists across restarts. After a successful uninstall, the latch is set so
the controller refuses to start until the latch is cleared. This prevents the
controller from restarting after an uninstall has completed.

A completed uninstall also retains its durable lifecycle lease beside the latch;
the explicit reinstall clear removes the completed hold and the latch together,
in the order that leaves the system retired whenever any step fails.

STDLIB ONLY: tempfile, os, pathlib, json (plus the sibling lifecycle_lease module)
"""

from __future__ import annotations

import json
import os
import time
import tempfile
from pathlib import Path
from typing import Final

from .lifecycle_lease import LEASE_PATH, LeaseError, LeaseStore

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


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def retire(path: Path | None = None) -> None:
    """Atomically set the durable retired latch.

    Args:
        path: Optional path to the lifecycle file. Defaults to LIFECYCLE_PATH.

    Writes {"retired": true, "timestamp": <unix_ts>} to the lifecycle file with
    mode 0644, durably: temporary file, fsync, replace, directory fsync. The
    caller must not acknowledge a retirement transition until this returns, so
    an OSError propagates instead of silently leaving the system unretired.
    """
    target = path or LIFECYCLE_PATH
    directory = target.parent
    temporary: str | None = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", dir=directory, delete=False
        ) as f:
            temporary = f.name
            json.dump({"retired": True, "timestamp": int(time.time())}, f)
            f.flush()
            os.fsync(f.fileno())
            os.fchmod(f.fileno(), 0o644)
        os.replace(temporary, target)
        _fsync_directory(directory)
    except OSError:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        raise


def clear_retired(path: Path | None = None) -> None:
    """Remove the lifecycle latch file if it exists.

    Args:
        path: Optional path to the lifecycle file. Defaults to LIFECYCLE_PATH.

    A no-op if the file does not exist. An OSError propagates: a caller must not
    acknowledge a cleared latch the filesystem refused to remove.
    """
    target = path or LIFECYCLE_PATH
    target.unlink(missing_ok=True)


def clear_lifecycle_latch(path: Path | None = None) -> None:
    """Explicit reinstall boundary: clear a completed uninstall's hold and latch.

    Args:
        path: Optional path to the lifecycle file. Defaults to LIFECYCLE_PATH.

    The sidecar lease record is read from the latch's own directory (production
    /var/lib/sparehash/lifecycle-lease.json). Nothing is removed unless the
    latch reports retirement, so an uninstall still in progress keeps its hold.
    A malformed or unreadable record raises LeaseError before any removal, and a
    live hold whose purpose is not "uninstall" raises LeaseError so an active
    update hold survives. A completed UNINSTALL hold is cleared durably before
    the latch: a failed hold clear leaves the retirement latch in force, and a
    failed latch removal raises OSError with the hold already gone but the
    system still retired.
    """
    target = path or LIFECYCLE_PATH
    if not is_retired(target):
        return
    store = LeaseStore(target.parent / LEASE_PATH.name)
    lease = store.load()
    if lease is not None and lease.purpose != "uninstall":
        raise LeaseError(f"active {lease.purpose} hold retained; lifecycle latch not cleared")
    if lease is not None:
        store.clear()
    clear_retired(target)
