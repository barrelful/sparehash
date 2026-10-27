# SPDX-License-Identifier: GPL-3.0-or-later
# Adapted from Lutris contributors' get_steam_game_pids(), GPL-3.0-or-later.
# Source revision: 1f32fe0e46e2a5d92fad9db2846c7cb7becd20c3
# https://github.com/lutris/lutris/blob/1f32fe0e46e2a5d92fad9db2846c7cb7becd20c3/lutris/runners/steam.py
# Modified 2026-10-25: any AppId, argument boundaries, bounded work, explicit
# failures, candidate identity checks and immutable fresh results. See notices.
# Copyright (c) 2026 SpareHash contributors

"""Read-only, best-effort Steam reaper detection; no absence/completeness proof."""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

MAX_PROCESSES: Final = 16384
MAX_CMDLINE: Final = 65536
SCAN_BUDGET: Final = 0.5
MAX_AGE: Final = 2.0
Decision = Literal["CLEAR", "BLOCKED", "UNKNOWN"]


@dataclass(frozen=True, slots=True)
class ScanResult:
    decision: Decision
    observed_at: float
    duration: float = 0.0
    reason: str = "ok"
    matches: int = 0

    def fresh(self, now: float) -> bool:
        return (math.isfinite(now) and math.isfinite(self.observed_at)
                and math.isfinite(self.duration) and 0 <= self.duration <= SCAN_BUDGET
                and self.observed_at + self.duration <= now
                and 0 <= now - self.observed_at <= MAX_AGE)


def _recognised(cmdline: bytes) -> bool:
    """Tighten Lutris's substring match to the launcher's NUL argument prefix."""
    args = cmdline.split(b"\0")
    if args[0].rsplit(b"/", 1)[-1] != b"reaper":
        return False
    if not cmdline.endswith(b"\0"):
        raise ValueError("incomplete_cmdline")
    prefix = args[1:args.index(b"--")] if b"--" in args else args[1:]
    for index, arg in enumerate(prefix):
        if arg == b"SteamLaunch":
            if index + 1 >= len(prefix):
                raise ValueError("launch_prefix")
            app = prefix[index + 1]
            if not app.startswith(b"AppId=") or not app[6:].isdigit():
                raise ValueError("launch_prefix")
            return True
    return False


def _read(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise OverflowError
    return data


def _identity(path: Path) -> tuple[int, int]:
    info = path.stat()
    fields = _read(path / "stat", 4096).rpartition(b") ")[2].split()
    start = int(fields[19])
    if start <= 0:
        raise ValueError("process_identity")
    return info.st_uid, start


def scan_steam_games(root: Path = Path("/proc"), *,
                     clock: Callable[[], float] = time.monotonic) -> ScanResult:
    """Adapt Lutris's /proc scan without an AppId filter or silent I/O failures.

    Disappeared processes are normal churn. Other unreadable processes, exhausted
    bounds and malformed relevant input prevent a successful negative result.
    Root is a unit-test seam, never configured from user input in production.
    """
    started = clock()
    matches, scanned = 0, 0
    reason = "ok"
    try:
        # An empty/unmounted proc path is not a successful empty process census.
        if not _read(root / "self/stat", 4096):
            raise OverflowError
        with os.scandir(root) as entries:
            for entry in entries:
                if clock() - started > SCAN_BUDGET:
                    reason = "scan_timeout"
                    break
                if not entry.name.isascii() or not entry.name.isdecimal():
                    continue
                scanned += 1
                if scanned > MAX_PROCESSES:
                    reason = "process_limit"
                    break
                path = root / entry.name
                try:
                    cmdline = _read(path / "cmdline", MAX_CMDLINE)
                    if _recognised(cmdline):
                        before = _identity(path)
                        # Re-read after pinning metadata: don't adopt a recycled
                        # PID or an exec that changed the candidate's arguments.
                        current = _read(path / "cmdline", MAX_CMDLINE)
                        if before != _identity(path) or current != cmdline:
                            reason = "identity_changed"
                        else:
                            matches += 1
                except (FileNotFoundError, ProcessLookupError):
                    continue
                except OSError:
                    reason = "process_unreadable"
                except (ValueError, IndexError):
                    reason = "process_malformed"
                except OverflowError:
                    reason = "cmdline_limit"
    except OSError:
        reason = "proc_unavailable"
    except OverflowError:
        reason = "proc_malformed"
    finished = clock()
    duration = finished - started
    if not math.isfinite(duration) or not 0 <= duration <= SCAN_BUDGET:
        reason = "scan_timeout"
    decision: Decision = "BLOCKED" if matches else "CLEAR" if reason == "ok" else "UNKNOWN"
    return ScanResult(decision, started, duration, reason, matches)
