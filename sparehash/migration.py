# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Compatible v0.1.1 update hold and durable migration journal with explicit phases.

This module owns the upgrade transaction *record*, not the upgrade itself. It
keeps the launch-inhibiting hold in the exact three-field v0.1.1 document that
old code already parses (``lifecycle-lease.json`` with ``purpose="update"``) and
records progress in a separate, strictly bounded, root-only ``migration.json``
beside it. Completion is explicit: only a caller that holds the pinned store
transaction and passes fresh evidence (matching payload identity, empty miner
cgroup, clear guards, unchanged manual pause) plus a matching update hold may
durably remove the journal and the hold. Completion never reads or writes
``state.json``, never sends PAUSE/RESUME and never aborts an update.

Locking: every journal and hold read/write goes through one already-open
``ManifestStore`` transaction (the ``StoreContext`` -> ``locked_store`` ->
``ManifestStore`` chain from :mod:`sparehash.install_manifest`). No helper in
this module calls ``locked_store`` again, so a caller that already holds the
``/var/lib/sparehash`` directory flock never nests flock, and no old-runtime
startup is attempted while the store lock is held.

STDLIB ONLY: json, os, stat, string, time, math, dataclasses, enum, contextlib,
typing plus the sibling install_manifest/lifecycle_lease/xmrig_paths modules.
"""

from __future__ import annotations

import json
import math
import os
import stat
import string
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Final, Literal, cast

from .install_manifest import ManifestStore
from .lifecycle_lease import ActiveLease, is_purpose
from .xmrig_paths import FILE_FLAGS

NAME: Final = "migration.json"
HOLD_NAME: Final = "lifecycle-lease.json"
MAX_JOURNAL_BYTES: Final = 64 * 1024
MAX_HOLD_BYTES: Final = 4 * 1024
MIGRATION_PURPOSE: Final[Literal["update"]] = "update"
_LAYOUT_MAX_ENTRIES: Final = 8
_KEY_MAX: Final = 64
_VALUE_MAX: Final = 256
_ID_MAX: Final = 128
_TOKEN_MAX: Final = 64
_REQUEST_MAX: Final = 128
_SAFE_ID: Final = frozenset(string.ascii_letters + string.digits + "-_.:")
_HEX: Final = frozenset(string.hexdigits)
_HOLD_FIELDS: Final = frozenset(("purpose", "request_id", "token"))
_FIELDS: Final = frozenset((
    "operation_id", "payload_identity", "previous_layout", "new_layout", "phase",
    "phase_changed_at", "manual_state_preserved", "expected_completion_action",
    "hold_request_id", "hold_token",
))
_CHANGE_FIELDS: Final = frozenset((
    "payload_identity", "previous_layout", "new_layout", "manual_state_preserved",
    "expected_completion_action", "hold_request_id", "hold_token",
))


class MigrationError(RuntimeError):
    """Migration journal, compatible hold or completion evidence is untrustworthy."""


class MigrationPhase(Enum):
    """Strict forward state machine; COMPLETE is only reachable via completion."""

    PREPARED = "PREPARED"
    HELD = "HELD"
    STOPPED_EMPTY = "STOPPED_EMPTY"
    STAGED = "STAGED"
    PUBLISHED = "PUBLISHED"
    VERIFIED_AWAITING_OPERATOR = "VERIFIED_AWAITING_OPERATOR"
    COMPLETE = "COMPLETE"


_ORDER: Final = tuple(MigrationPhase)


@dataclass(frozen=True, slots=True)
class MigrationJournal:
    """Root-only record of one migration: identity, layouts, phase and hold binding."""

    operation_id: str
    payload_identity: str
    previous_layout: dict[str, str]
    new_layout: dict[str, str]
    phase: MigrationPhase
    phase_changed_at: float
    manual_state_preserved: bool
    expected_completion_action: str | None
    hold_request_id: str
    hold_token: str


@dataclass(frozen=True, slots=True)
class CompletionEvidence:
    """Fresh, caller-computed completion guards; this module never reads state.json."""

    payload_identity: str
    empty_cgroup: bool
    guards_clear: bool
    manual_state_preserved: bool


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _is_text(value: object, limit: int) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= limit
            and all(" " <= char <= "~" for char in value))


def _is_safe_id(value: object, limit: int = _ID_MAX) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= limit
            and all(char in _SAFE_ID for char in value))


def _is_hex(value: object) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= _TOKEN_MAX
            and all(char in _HEX for char in value))


def _is_layout(value: object) -> bool:
    if not isinstance(value, dict) or len(value) > _LAYOUT_MAX_ENTRIES:
        return False
    return all(_is_text(key, _KEY_MAX) and _is_text(item, _VALUE_MAX)
               for key, item in value.items())


def _decode_journal(text: str) -> MigrationJournal:
    """Parse the persisted journal once; every failure is fail-closed."""
    try:
        decoded = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise MigrationError(f"migration journal corrupt: {error}") from error
    if not isinstance(decoded, dict) or set(decoded) != _FIELDS:
        raise MigrationError("migration journal fields invalid")
    phase_value = decoded["phase"]
    try:
        phase = MigrationPhase(phase_value) if isinstance(phase_value, str) else None
    except ValueError as error:
        raise MigrationError("migration journal phase invalid") from error
    changed = decoded["phase_changed_at"]
    preserved = decoded["manual_state_preserved"]
    action = decoded["expected_completion_action"]
    if (phase is None
            or not _is_safe_id(decoded["operation_id"])
            or not _is_safe_id(decoded["payload_identity"])
            or not _is_layout(decoded["previous_layout"]) or not _is_layout(decoded["new_layout"])
            or isinstance(changed, bool) or not isinstance(changed, (int, float))
            or not math.isfinite(changed) or changed < 0
            or not isinstance(preserved, bool)
            or (action is not None and not _is_text(action, _VALUE_MAX))
            or not _is_safe_id(decoded["hold_request_id"])
            or not _is_hex(decoded["hold_token"])):
        raise MigrationError("migration journal values invalid")
    return MigrationJournal(
        operation_id=decoded["operation_id"],
        payload_identity=decoded["payload_identity"],
        previous_layout=dict(decoded["previous_layout"]),
        new_layout=dict(decoded["new_layout"]),
        phase=phase,
        phase_changed_at=float(changed),
        manual_state_preserved=preserved,
        expected_completion_action=action,
        hold_request_id=decoded["hold_request_id"],
        hold_token=decoded["hold_token"],
    )


def _encode_journal(journal: MigrationJournal) -> str:
    return json.dumps({
        "operation_id": journal.operation_id,
        "payload_identity": journal.payload_identity,
        "previous_layout": journal.previous_layout,
        "new_layout": journal.new_layout,
        "phase": journal.phase.value,
        "phase_changed_at": journal.phase_changed_at,
        "manual_state_preserved": journal.manual_state_preserved,
        "expected_completion_action": journal.expected_completion_action,
        "hold_request_id": journal.hold_request_id,
        "hold_token": journal.hold_token,
    })


def _require_next(current: MigrationPhase, target: MigrationPhase) -> None:
    if target is MigrationPhase.COMPLETE:
        raise MigrationError("COMPLETE requires verified completion evidence, not advance()")
    if _ORDER.index(target) != _ORDER.index(current) + 1:
        raise MigrationError(f"invalid migration transition: {current.value} -> {target.value}")


class MigrationStore:
    """Protected no-follow journal bound to one open ``ManifestStore`` transaction.

    Build it inside ``locked_store(context)`` with the yielded store: that open
    transaction is the single store context every journal read/write uses. This
    class never calls ``locked_store`` itself, so no flock is nested and no
    old-runtime startup is attempted under the held lock.
    """

    def __init__(self, store: ManifestStore) -> None:
        self._store: ManifestStore = store

    def _open(self, name: str) -> int | None:
        try:
            return os.open(name, FILE_FLAGS, dir_fd=self._store.fd)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise MigrationError(f"migration record unreadable: {error}") from error

    def read(self) -> MigrationJournal | None:
        """Return the journal, None when absent, MigrationError when untrustworthy."""
        descriptor = self._open(NAME)
        if descriptor is None:
            return None
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != self._store.uid
                    or info.st_mode & 0o022 or info.st_nlink != 1
                    or not 0 < info.st_size <= MAX_JOURNAL_BYTES):
                raise MigrationError("migration journal is not a bounded protected single-link file")
            data = source.read(MAX_JOURNAL_BYTES + 1)
        if len(data) > MAX_JOURNAL_BYTES:
            raise MigrationError("migration journal exceeds the bounded size")
        try:
            text = data.decode("utf-8")
        except UnicodeError as error:
            raise MigrationError("migration journal is not UTF-8") from error
        return _decode_journal(text)

    def write(self, journal: MigrationJournal) -> None:
        """Validate, then durably publish mode-0640 root-owned journal; no nested flock."""
        payload = _encode_journal(journal)
        _ = _decode_journal(payload)
        data = payload.encode("utf-8")
        if len(data) > MAX_JOURNAL_BYTES:
            raise MigrationError("migration journal exceeds the bounded size")
        temporary = f".{NAME}.tmp-{os.urandom(8).hex()}"
        created = False
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o640, dir_fd=self._store.fd)
            created = True
            with os.fdopen(descriptor, "wb") as target:
                os.fchown(target.fileno(), self._store.uid, self._store.uid)
                os.fchmod(target.fileno(), 0o640)
                _ = target.write(data)
                target.flush()
                os.fsync(target.fileno())
            os.rename(temporary, NAME, src_dir_fd=self._store.fd, dst_dir_fd=self._store.fd)
            os.fsync(self._store.fd)
        except OSError as error:
            raise MigrationError(f"migration journal cannot be persisted: {error}") from error
        finally:
            if created:
                with suppress(FileNotFoundError):
                    os.unlink(temporary, dir_fd=self._store.fd)

    def advance(self, phase: MigrationPhase, **changes: object) -> MigrationJournal:
        """Durably move one phase forward, applying explicit field changes."""
        current = self.read()
        if current is None:
            raise MigrationError("no migration journal to advance")
        _require_next(current.phase, phase)
        unknown = set(changes) - _CHANGE_FIELDS
        if unknown:
            raise MigrationError(f"unsupported migration journal change: {sorted(unknown)}")
        updates = cast("dict[str, Any]", changes)
        candidate = replace(current, phase=phase, phase_changed_at=time.time(), **updates)
        self.write(candidate)
        return candidate


def _decode_hold(text: str) -> ActiveLease:
    """Parse the exact three-field legacy hold document; failures are fail-closed."""
    try:
        decoded = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise MigrationError(f"update hold corrupt: {error}") from error
    if not isinstance(decoded, dict) or set(decoded) != _HOLD_FIELDS:
        raise MigrationError("update hold fields invalid")
    token, request_id, purpose = decoded["token"], decoded["request_id"], decoded["purpose"]
    if (not _is_hex(token)
            or not isinstance(request_id, str) or not 0 < len(request_id) <= _REQUEST_MAX
            or not isinstance(purpose, str) or not is_purpose(purpose)):
        raise MigrationError("update hold values invalid")
    return ActiveLease(token, request_id, purpose)


def _read_hold(store: ManifestStore) -> ActiveLease | None:
    """Read the compatible v0.1.1 update hold with pinned no-follow I/O."""
    try:
        descriptor = os.open(HOLD_NAME, FILE_FLAGS, dir_fd=store.fd)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise MigrationError(f"update hold unreadable: {error}") from error
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != store.uid
                or info.st_mode & 0o022 or info.st_nlink != 1
                or not 0 < info.st_size <= MAX_HOLD_BYTES):
            raise MigrationError("update hold is not a bounded protected single-link file")
        data = source.read(MAX_HOLD_BYTES + 1)
    if len(data) > MAX_HOLD_BYTES:
        raise MigrationError("update hold exceeds the bounded size")
    try:
        return _decode_hold(data.decode("utf-8"))
    except UnicodeError as error:
        raise MigrationError(f"update hold not UTF-8: {error}") from error


def acquire_compatible_hold(store: ManifestStore, *, request_id: str | None = None) -> ActiveLease:
    """Adopt the existing v0.1.1 update hold as the compatible migration hold.

    ``store`` is the single open store transaction (see :class:`MigrationStore`).
    A matching request id replays idempotently; a different request id never
    steals an existing hold; a missing hold or a non-``update`` purpose refuses.
    This helper writes no journal, so a refusal leaves the directory unchanged.
    """
    hold = _read_hold(store)
    if hold is None:
        raise MigrationError("no compatible v0.1.1 update hold to adopt")
    if hold.purpose != MIGRATION_PURPOSE:
        raise MigrationError(f"existing {hold.purpose} hold is not a compatible update hold")
    if request_id is not None and hold.request_id != request_id:
        raise MigrationError("request id does not match the existing update hold")
    return hold


def _unlink(store: ManifestStore, name: str) -> None:
    try:
        os.unlink(name, dir_fd=store.fd)
        os.fsync(store.fd)
    except FileNotFoundError:
        return
    except OSError as error:
        raise MigrationError(f"cannot remove {name}: {error}") from error


def complete_migration(
    operation_id: str,
    store: ManifestStore,
    *,
    evidence: CompletionEvidence,
    invalidate_authorization: Callable[[], None] | None = None,
) -> MigrationPhase:
    """Explicit root-only completion after revalidating every guard.

    ``store`` is the single open store transaction. The journal must be at
    ``VERIFIED_AWAITING_OPERATOR`` for this operation, with a recorded completion
    action and a preserved manual pause; the fresh evidence must agree. Only the
    matching update hold and the migration journal are removed (hold first, so a
    crash leaves the self-describing journal as the fail-closed inhibitor).
    ``state.json`` is never read or written; PAUSE/RESUME and update abort are
    never sent. ``invalidate_authorization`` is invoked last when supplied.
    """
    journal = MigrationStore(store).read()
    if journal is None:
        raise MigrationError("no migration journal; completion refused")
    if journal.operation_id != operation_id:
        raise MigrationError("operation id does not match the migration journal")
    if journal.phase is not MigrationPhase.VERIFIED_AWAITING_OPERATOR:
        raise MigrationError(f"migration is not verified for completion: {journal.phase.value}")
    if journal.expected_completion_action is None:
        raise MigrationError("migration completion action is not recorded")
    if not journal.manual_state_preserved:
        raise MigrationError("recorded manual pause was not preserved")
    if evidence.payload_identity != journal.payload_identity:
        raise MigrationError("payload identity changed since preparation")
    if not evidence.empty_cgroup:
        raise MigrationError("miner cgroup is not proven empty")
    if not evidence.guards_clear:
        raise MigrationError("migration guards are not clear")
    if not evidence.manual_state_preserved:
        raise MigrationError("manual pause state drifted during migration")
    hold = _read_hold(store)
    if hold is None:
        raise MigrationError("compatible update hold is missing")
    if hold.purpose != MIGRATION_PURPOSE:
        raise MigrationError(f"existing {hold.purpose} hold is not a compatible update hold")
    if hold.request_id != journal.hold_request_id or hold.token != journal.hold_token:
        raise MigrationError("update hold does not match the migration journal")
    _unlink(store, HOLD_NAME)
    _unlink(store, NAME)
    if invalidate_authorization is not None:
        invalidate_authorization()
    return MigrationPhase.COMPLETE


def complete_upgrade(
    operation_id: str,
    store: ManifestStore,
    *,
    evidence: CompletionEvidence,
    state_path: Path | None = None,
    invalidate_authorization: Callable[[], None] | None = None,
) -> MigrationPhase:
    """Root-only complete-upgrade: remove only the journal and the update hold.

    Completion is allowed while a protected manual pause is still active: that
    pause is an authoritative inhibited state, and this function never reads or
    writes ``state.json``. When ``state_path`` is supplied, an existing protected
    manual state refuses completion: no upgrade entrypoint may be the operation
    that crosses a live, preserved manual pause. Missing or unreadable required
    evidence still refuses through :func:`complete_migration`.
    """
    if state_path is not None and state_path.exists():
        raise MigrationError("refusing to complete an upgrade across a protected manual state")
    return complete_migration(operation_id, store, evidence=evidence,
                              invalidate_authorization=invalidate_authorization)
