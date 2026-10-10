# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Single durable external lifecycle lease: one active hold, fail-closed reads.

The record is the restart-surviving identity of an external update or uninstall
hold: a unique hex token that never aliases a controller generation, the
caller's request id for idempotent replay, and a supported purpose. This module
owns the record's trust boundary: a corrupt, truncated, or unreadable record
raises LeaseError instead of pretending the hold is gone, and every mutation is
durable (temporary file, fsync, replace, directory fsync) before a caller may
acknowledge it. There is deliberately no history: only the single active record.

STDLIB ONLY: json, os, string, tempfile, contextlib, dataclasses, pathlib, typing
"""

from __future__ import annotations

import json
import os
import string
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, TypeGuard

Purpose = Literal["update", "uninstall"]
LEASE_PATH: Final = Path("/var/lib/sparehash/lifecycle-lease.json")
PURPOSES: Final[tuple[Purpose, ...]] = ("update", "uninstall")
_TOKEN_MAX: Final = 64
_REQUEST_MAX: Final = 128
_FIELDS: Final = frozenset(("purpose", "request_id", "token"))


class LeaseError(Exception):
    """Active lease record or retirement latch cannot be trusted."""


def is_purpose(value: str) -> TypeGuard[Purpose]:
    """Bound a wire purpose string to the supported mutation set."""
    return value in PURPOSES


@dataclass(frozen=True, slots=True)
class ActiveLease:
    """Minimal active record: no generations, no request history."""

    token: str
    request_id: str
    purpose: Purpose


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _hex_token(token: str) -> bool:
    return 0 < len(token) <= _TOKEN_MAX and all(char in string.hexdigits for char in token)


def _decode(text: str) -> ActiveLease:
    """Parse the persisted document once; every failure is fail-closed."""
    try:
        decoded = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise LeaseError(f"lease record corrupt: {error}") from error
    if not isinstance(decoded, dict):
        raise LeaseError("lease record must be an object")
    if set(decoded) != _FIELDS:
        raise LeaseError("lease record fields invalid")
    token, request_id, purpose = (decoded.get("token"), decoded.get("request_id"), decoded.get("purpose"))
    if (not isinstance(token, str) or not _hex_token(token)
            or not isinstance(request_id, str) or not 0 < len(request_id) <= _REQUEST_MAX
            or not isinstance(purpose, str) or not is_purpose(purpose)):
        raise LeaseError("lease record fields invalid")
    return ActiveLease(token, request_id, purpose)


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class LeaseStore:
    """Boundary parser and durable writer for the single active lease record."""

    def __init__(self, path: Path = LEASE_PATH) -> None:
        self._path: Path = path

    def load(self) -> ActiveLease | None:
        """Return the active record, None when absent, LeaseError when untrustworthy."""
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError) as error:
            raise LeaseError(f"lease record unreadable: {error}") from error
        return _decode(text)

    def save(self, lease: ActiveLease) -> None:
        """Durably publish the record; failure raises before any acknowledgment."""
        payload = json.dumps({"token": lease.token, "request_id": lease.request_id, "purpose": lease.purpose})
        temporary: str | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self._path.parent,
                                             delete=False) as stream:
                temporary = stream.name
                _ = stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
            _fsync_directory(self._path.parent)
        except OSError as error:
            if temporary is not None:
                with suppress(OSError):
                    os.unlink(temporary)
            raise LeaseError(f"lease record cannot be persisted: {error}") from error

    def clear(self) -> None:
        """Durably remove the record; failure raises so the hold is retained."""
        try:
            self._path.unlink(missing_ok=True)
            _fsync_directory(self._path.parent)
        except FileNotFoundError:
            return
        except OSError as error:
            raise LeaseError(f"lease record cannot be removed: {error}") from error
