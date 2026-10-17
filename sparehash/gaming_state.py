# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Versioned gaming clearance, independent of aggregate collection timestamps.

Coverage keys are ``provider:uid`` (canonical decimal UID). Providers own their
complete session inventory; session/topology changes must bump the epoch before
publishing new evidence. Distinct hook peers use distinct provider identities.
Only declared sources can establish clearance. This module performs no I/O and
does not change the controller's four blocker categories.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Literal, assert_never

GamingState = Literal["ACTIVE", "INACTIVE", "UNKNOWN"]
GamingHealth = Literal["OK", "STALE", "DISCONNECTED", "MALFORMED", "OVERFLOW"]
GamingDecision = Literal["BLOCKED", "UNKNOWN", "CLEAR"]

MAX_COVERAGE: Final = 128
MAX_BATCH: Final = 1024
MAX_TOKEN_LENGTH: Final = 256


@dataclass(frozen=True, slots=True)
class GamingEvidence:
    """Immutable producer sample; observed_at uses the host monotonic clock."""

    provider: str
    uid: int
    epoch: int
    sequence: int
    observed_at: float
    state: GamingState
    health: GamingHealth
    completeness: frozenset[str]
    producer_version: str | None


class InvalidMaxAgeError(ValueError):
    """The freshness budget must be finite and positive."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        super().__init__(f"invalid gaming freshness budget: {seconds!r}")


class GamingReducer:
    """Owner-thread accumulator retaining immutable samples, never re-stamping.

    Sequence numbers belong to each provider/UID within an epoch, including
    samples captured by aggregate workers. Replays cannot refresh timestamps;
    a higher sequence with an older observation time cannot replace inhibition.
    Overflow latches UNKNOWN until an explicit epoch bump and full re-inventory.
    Manual acknowledgments only add inhibition; manual persistence/removal and
    hook authentication remain the caller's responsibility.
    """

    def __init__(self, max_age_seconds: float = 5.0) -> None:
        if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
            raise InvalidMaxAgeError(max_age_seconds)
        self.max_age_seconds = max_age_seconds
        self.current_epoch = 0
        self.coverage: tuple[str, ...] = ()
        self._samples: dict[str, GamingEvidence] = {}
        self._manual: set[int] = set()
        self._overflow = False
        self._coverage_valid = False

    def bump_epoch(self) -> int:
        """Invalidate all prior evidence after restart, resume or inventory loss."""
        self.current_epoch += 1
        self._samples.clear()
        self._overflow = False
        return self.current_epoch

    def set_coverage(self, required: tuple[str, ...]) -> None:
        """Declare finite required sources; any change requires fresh inventory."""
        bounded = tuple(dict.fromkeys(required[:MAX_COVERAGE]))
        valid = bool(bounded) and len(required) <= MAX_COVERAGE
        for key in bounded:
            provider, separator, uid = key.rpartition(":")
            valid = (valid and len(key) <= MAX_TOKEN_LENGTH and bool(provider)
                     and ":" not in provider and bool(separator)
                     and uid.isascii() and uid.isdecimal()
                     and uid == str(int(uid)))
        if bounded != self.coverage or valid != self._coverage_valid:
            self.bump_epoch()
            self.coverage = bounded
            self._coverage_valid = valid

    def acknowledge_manual(self, uid: int) -> None:
        """Record an additive manual pause, never permission to mine."""
        if len(self._manual) < MAX_COVERAGE:
            self._manual.add(uid)
        else:
            self._overflow = True

    def reduce(self, evidence_list: Iterable[GamingEvidence], now: float | None = None) -> GamingDecision:
        """Ingest a bounded batch and reduce current samples without mutating them.

        An overflowed batch need not be drained: UNKNOWN is already inhibitory.
        ACTIVE retained in the inspected prefix still dominates that uncertainty.
        Future/old epochs cannot replace current evidence. A producer-version
        change revokes the epoch and requires an explicit current re-inventory.
        """
        at = time.monotonic() if now is None else now
        for index, evidence in enumerate(evidence_list):
            if index >= MAX_BATCH:
                self._overflow = True
                break
            key = f"{evidence.provider}:{evidence.uid}"
            if key not in self.coverage or evidence.epoch != self.current_epoch:
                continue
            if (evidence.sequence < 0 or evidence.uid < 0
                    or len(evidence.completeness) > MAX_COVERAGE
                    or any(len(token) > MAX_TOKEN_LENGTH for token in evidence.completeness)
                    or (evidence.producer_version is not None
                        and len(evidence.producer_version) > MAX_TOKEN_LENGTH)):
                self._overflow = True
                continue
            previous = self._samples.get(key)
            if previous is not None:
                if previous.producer_version != evidence.producer_version:
                    self.bump_epoch()
                    break
                if (evidence.sequence <= previous.sequence
                        or evidence.observed_at < previous.observed_at):
                    continue
            self._samples[key] = evidence
            match evidence.health:
                case "OVERFLOW":
                    self._overflow = True
                case "OK" | "STALE" | "DISCONNECTED" | "MALFORMED":
                    pass
                case unreachable:
                    assert_never(unreachable)

        unknown = (not self._coverage_valid or self._overflow or not math.isfinite(at))
        for key in self.coverage:
            current = self._samples.get(key)
            if current is None:
                unknown = True
                continue
            match current.state:
                case "ACTIVE":
                    return "BLOCKED"
                case "UNKNOWN":
                    unknown = True
                case "INACTIVE":
                    pass
                case unreachable_state:
                    assert_never(unreachable_state)
            unknown |= (current.health != "OK" or key not in current.completeness
                        or not math.isfinite(current.observed_at)
                        or not 0 <= at - current.observed_at <= self.max_age_seconds)
        if self._manual:
            return "BLOCKED"
        return "UNKNOWN" if unknown else "CLEAR"
