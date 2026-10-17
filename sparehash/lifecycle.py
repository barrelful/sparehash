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

STDLIB ONLY: tempfile, os, pathlib, json (plus the sibling lifecycle_lease module
and the descriptor-pinned PathTrust boundary)
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Final
from uuid import uuid4

from .lifecycle_lease import LEASE_PATH, LeaseError, LeaseStore
from .xmrig_paths import PathTrust

LIFECYCLE_PATH: Final = Path("/var/lib/sparehash/lifecycle.json")
_ANCHOR_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


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


def _protected(info: os.stat_result, uid: int) -> bool:
    return info.st_uid == uid and info.st_mode & 0o022 == 0


def _latch_parent(stack: ExitStack, trust: PathTrust, target: Path) -> int:
    """Pin the latch's parent without ever following a foreign link.

    The latch must live inside the layout's physical root. Every ancestor is
    opened with O_NOFOLLOW and validated as owned and not group/world-writable;
    an existing latch that is a symlink, hardlink, directory or foreign file is
    refused rather than replaced or unlinked through.
    """
    try:
        relative = target.relative_to(trust.root)
    except ValueError as error:
        raise LeaseError("retirement record is outside the pinned layout root") from error
    if relative.name in {"", ".", ".."}:
        raise LeaseError("retirement record names no file")
    try:
        fd = os.open(trust.root, _ANCHOR_FLAGS)
    except OSError as error:
        raise LeaseError(f"layout root cannot be pinned: {error}") from error
    _ = stack.callback(os.close, fd)
    if not _protected(os.fstat(fd), trust.uid):
        raise LeaseError("layout root is foreign or writable")
    for part in relative.parent.parts:
        if part in {"", "."}:
            continue
        try:
            fd = os.open(part, _ANCHOR_FLAGS, dir_fd=fd)
        except OSError as error:
            raise LeaseError(f"retirement ancestor is unsafe: {error}") from error
        _ = stack.callback(os.close, fd)
        if not _protected(os.fstat(fd), trust.uid):
            raise LeaseError("retirement ancestor is foreign or writable")
    try:
        existing = os.open(relative.name, _FILE_FLAGS, dir_fd=fd)
    except FileNotFoundError:
        pass
    except OSError as error:
        raise LeaseError(f"retirement record is not a protected file: {error}") from error
    else:
        try:
            info = os.fstat(existing)
            if not stat.S_ISREG(info.st_mode) or not _protected(info, trust.uid) or info.st_nlink != 1:
                raise LeaseError("retirement record is foreign, linked or not a regular file")
        finally:
            os.close(existing)
    return fd


def _write_latch(fd: int, name: str) -> None:
    """Durably replace the latch using only the pinned parent descriptor."""
    temporary = "." + uuid4().hex + ".json"
    try:
        raw = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=fd)
        with os.fdopen(raw, "w", encoding="utf-8") as stream:
            json.dump({"retired": True, "timestamp": int(time.time())}, stream)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o644)
        os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    except OSError:
        try:
            os.unlink(temporary, dir_fd=fd)
        except OSError:
            pass
        raise


def retire(path: Path | None = None, *, trust: PathTrust | None = None) -> None:
    """Atomically set the durable retired latch.

    Args:
        path: Optional path to the lifecycle file. Defaults to LIFECYCLE_PATH.
        trust: Optional layout trust boundary. When supplied, the latch must live
            inside the layout's physical root and every ancestor is pinned with
            O_NOFOLLOW; a foreign link or unprotected ancestor refuses the write.

    Writes {"retired": true, "timestamp": <unix_ts>} to the lifecycle file with
    mode 0644, durably: temporary file, fsync, replace, directory fsync. The
    caller must not acknowledge a retirement transition until this returns, so
    an OSError propagates instead of silently leaving the system unretired.
    """
    target = path or LIFECYCLE_PATH
    if trust is not None:
        with ExitStack() as stack:
            _write_latch(_latch_parent(stack, trust, target), target.name)
        return
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


def clear_retired(path: Path | None = None, *, trust: PathTrust | None = None) -> None:
    """Remove the lifecycle latch file if it exists.

    Args:
        path: Optional path to the lifecycle file. Defaults to LIFECYCLE_PATH.
        trust: Optional layout trust boundary. When supplied, the latch is
            removed from the pinned parent descriptor after the same foreign-link
            and ancestor validation as retirement; a foreign latch is refused.

    A no-op if the file does not exist. An OSError propagates: a caller must not
    acknowledge a cleared latch the filesystem refused to remove.
    """
    target = path or LIFECYCLE_PATH
    if trust is not None:
        with ExitStack() as stack:
            fd = _latch_parent(stack, trust, target)
            try:
                os.unlink(target.name, dir_fd=fd)
            except FileNotFoundError:
                return
            os.fsync(fd)
        return
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
