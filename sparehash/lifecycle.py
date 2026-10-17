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


def retire(path: Path | None = None, *, operation_id: str | None = None) -> None:
    """Atomically set the durable retired latch.

    Args:
        path: Optional path to the lifecycle file. Defaults to LIFECYCLE_PATH.
        operation_id: Optional id of the uninstall operation that retired the
            system. When given it is recorded in the latch so a later explicit
            reinstall clear must present the same id. A legacy latch written
            without one stays clearable for backward compatibility.

    Writes {"retired": true, "timestamp": <unix_ts>} (plus "operation_id" when
    supplied) to the lifecycle file with mode 0644, durably: temporary file,
    fsync, replace, directory fsync. The caller must not acknowledge a
    retirement transition until this returns, so an OSError propagates instead
    of silently leaving the system unretired.
    """
    target = path or LIFECYCLE_PATH
    directory = target.parent
    temporary: str | None = None
    record: dict[str, object] = {"retired": True, "timestamp": int(time.time())}
    if operation_id is not None:
        record["operation_id"] = operation_id
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", dir=directory, delete=False
        ) as f:
            temporary = f.name
            json.dump(record, f)
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


def _retirement_record(path: Path) -> dict[str, object] | None:
    """Parse the retirement latch; None when absent, LeaseError when untrustworthy."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as error:
        raise LeaseError(f"retirement record unreadable: {error}") from error
    if not text:
        return None
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as error:
        raise LeaseError(f"retirement record corrupt: {error}") from error
    if not isinstance(decoded, dict):
        raise LeaseError("retirement record must be an object")
    return decoded


def retirement_operation_id(path: Path | None = None) -> str | None:
    """Return the operation id recorded in the retirement latch, if any.

    Args:
        path: Optional path to the lifecycle file. Defaults to LIFECYCLE_PATH.

    Returns None when the latch is absent, empty, or carries no operation id.
    A corrupt or unreadable latch raises LeaseError rather than pretending the
    record is absent.
    """
    record = _retirement_record(path or LIFECYCLE_PATH)
    if record is None:
        return None
    value = record.get("operation_id")
    return value if isinstance(value, str) and value else None


def _require_matching_operation_id(recorded: str | None, operation_id: str | None) -> None:
    """Refuse a clear whose operation id does not match the recorded retention."""
    if recorded is None:
        return
    if operation_id is None:
        raise LeaseError("retirement record requires a matching operation id")
    if operation_id != recorded:
        raise LeaseError("operation id does not match the retirement record")


def clear_retired(path: Path | None = None, *, operation_id: str | None = None) -> None:
    """Remove the lifecycle latch file if it exists.

    Args:
        path: Optional path to the lifecycle file. Defaults to LIFECYCLE_PATH.
        operation_id: Optional id that must match the latch's recorded id.

    A no-op if the file does not exist. When the latch records an operation id,
    the caller must present the same id; a missing or mismatched id raises
    LeaseError and the latch is retained. A legacy latch without a recorded id
    stays clearable. A corrupt latch raises LeaseError rather than being
    silently removed. An OSError propagates: a caller must not acknowledge a
    cleared latch the filesystem refused to remove.
    """
    target = path or LIFECYCLE_PATH
    _require_matching_operation_id(retirement_operation_id(target), operation_id)
    target.unlink(missing_ok=True)


def clear_lifecycle_latch(path: Path | None = None, *, operation_id: str | None = None) -> None:
    """Explicit reinstall boundary: clear a completed uninstall's hold and latch.

    Args:
        path: Optional path to the lifecycle file. Defaults to LIFECYCLE_PATH.
        operation_id: Optional id that must match the retirement record and the
            retained uninstall hold's request id.

    The sidecar lease record is read from the latch's own directory (production
    /var/lib/sparehash/lifecycle-lease.json). Nothing is removed unless the
    latch reports retirement, so an uninstall still in progress keeps its hold.
    A malformed or unreadable record raises LeaseError before any removal, and a
    live hold whose purpose is not "uninstall" raises LeaseError so an active
    update hold survives. When the latch records an operation id, the caller
    must present the same id, and a retained uninstall hold must carry the same
    request id; a mismatch raises LeaseError and nothing is removed. A completed
    UNINSTALL hold is cleared durably before the latch: a failed hold clear
    leaves the retirement latch in force, and a failed latch removal raises
    OSError with the hold already gone but the system still retired.
    """
    target = path or LIFECYCLE_PATH
    if not is_retired(target):
        return
    recorded = retirement_operation_id(target)
    _require_matching_operation_id(recorded, operation_id)
    store = LeaseStore(target.parent / LEASE_PATH.name)
    lease = store.load()
    if lease is not None and lease.purpose != "uninstall":
        raise LeaseError(f"active {lease.purpose} hold retained; lifecycle latch not cleared")
    if lease is not None and operation_id is not None and lease.request_id != operation_id:
        raise LeaseError("lease request id does not match the operation id")
    if lease is not None:
        store.clear()
    clear_retired(target, operation_id=operation_id)
