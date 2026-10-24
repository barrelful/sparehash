# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Owner-session construction of the Steam log producer.

Root never reads these paths: the caller passes the session owner's home, and
every open is validated again by the reader. Client discovery is one bounded
same-UID lookup at start, never a recurring scan.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from .gaming_steam import SteamLogProducer, canonical_source

LOG_RELATIVE: Final = Path(".local/share/Steam/logs/gameprocess_log.txt")
MAX_SCAN: Final = 4096


def steam_client_pid(uid: int) -> int | None:
    """Return the one live Steam client PID owned by ``uid``, or None."""
    if uid < 0:
        return None
    found: list[int] = []
    scanned = 0
    try:
        entries = os.scandir("/proc")
    except OSError:
        return None
    with entries:
        for entry in entries:
            scanned += 1
            if scanned > MAX_SCAN:
                return None
            if not entry.name.isdigit():
                continue
            try:
                if entry.stat().st_uid != uid:
                    continue
                with open(f"/proc/{entry.name}/comm", "rb") as stream:
                    if stream.read(64).strip() != b"steam":
                        continue
            except OSError:
                continue
            found.append(int(entry.name))
            if len(found) > 1:
                return None
    return found[0] if found else None


def build_producer(home: str | os.PathLike[str], uid: int) -> SteamLogProducer | None:
    """Build one producer for an owner session, or None when unavailable.

    None is the conservative outcome: the guard then keeps its pre-existing
    behaviour, so an unavailable log can never widen clearance.
    """
    if uid < 0:
        return None
    client = steam_client_pid(uid)
    if client is None:
        return None
    try:
        source = canonical_source(Path(home) / LOG_RELATIVE)
    except (OSError, ValueError):
        return None
    if not source.is_absolute():
        return None
    return SteamLogProducer(source, client)
