"""Central pure blocker registry with persisted manual pause state."""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Final, Literal, TypedDict, cast

BLOCKERS: Final = ("battery", "gaming", "manual", "thermal")
MIN_DWELL_SECONDS: Final = 60.0
STATE_FILE: Final = Path(os.environ.get("SPAREHASH_STATE_FILE", "/var/lib/sparehash/state.json"))
MINING_STOPPED_BATTERY: Final = "Mining stopped: on battery."
MINING_STOPPED_GAMING: Final = "Mining stopped: GameMode session active."
MINING_PAUSED_MANUALLY: Final = "Mining paused manually."
MINING_STOPPED_THERMAL: Final = "Mining stopped: thermal limit reached."
MINING_STARTED: Final = "Mining started: no blockers active."
MINING_STILL_BLOCKED: Final = "Mining remains stopped: other blockers are active."


class StatePersistenceError(ValueError):
    """Persisted blocker state is invalid or cannot be safely accessed."""


class RawManualState(TypedDict):
    manual: Literal[True]
    reason: str
    source: str


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _parse_json(source: Path) -> RawManualState:
    """Parse the persisted manual pause document at its trust boundary."""
    with source.open(encoding="utf-8") as stream:
        decoded = json.loads(stream.read(), parse_constant=_reject_constant)
    if not isinstance(decoded, dict):
        raise ValueError("manual pause state must be an object")
    raw = cast(dict[str, str | bool], decoded)
    if raw.get("manual") is not True:
        raise ValueError("manual pause flag must be true")
    reason, origin = raw.get("reason"), raw.get("source")
    if not isinstance(reason, str) or not isinstance(origin, str):
        raise ValueError("manual pause metadata must be strings")
    return {"manual": True, "reason": reason, "source": origin}


@dataclass(frozen=True, slots=True)
class Blocker:
    """Immutable reason and monotonic activation timestamp."""

    reason: str
    since_ts: float
    source: str


@dataclass(frozen=True, slots=True)
class Transition:
    """Human-readable consequence of a blocker state change."""

    blocker: str
    active: bool
    message: str
    changed: bool


class BlockerState:
    """Mutable accumulator; mutation is the purpose of the central registry."""

    def __init__(
        self,
        state_file: str | Path = STATE_FILE,
        clock: Callable[[], float] = time.monotonic,
        min_dwell_seconds: float = MIN_DWELL_SECONDS,
    ) -> None:
        self._state_file: Path = Path(state_file)
        self._clock: Callable[[], float] = clock
        self._min_dwell_seconds: float = min_dwell_seconds
        if not math.isfinite(min_dwell_seconds) or min_dwell_seconds < 0:
            raise ValueError("minimum dwell must be finite and non-negative")
        self._active: dict[str, Blocker] = {}
        self._last_automatic_stop: float | None = None
        self._manual_resume_requested: bool = False
        self._persistence_error: StatePersistenceError | None = None
        self._load_manual()

    def _load_manual(self) -> None:
        try:
            payload = _parse_json(self._state_file)
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as exc:
            self._inhibit_corrupt_state(f"cannot read manual pause state: {exc}")
            return
        except ValueError as exc:
            self._inhibit_corrupt_state(str(exc))
            return
        self._active["manual"] = Blocker(payload["reason"], self._clock(), payload["source"])

    def _inhibit_corrupt_state(self, message: str) -> None:
        self._persistence_error = StatePersistenceError(message)
        self._active["manual"] = Blocker(message, self._clock(), "persistence-error")
        warnings.warn(message, RuntimeWarning, stacklevel=2)

    @staticmethod
    def _require_name(name: str) -> None:
        if name not in BLOCKERS:
            raise ValueError(f"unknown blocker: {name}")

    @staticmethod
    def _message(name: str, active: bool) -> str:
        if active:
            match name:
                case "battery":
                    return MINING_STOPPED_BATTERY
                case "gaming":
                    return MINING_STOPPED_GAMING
                case "manual":
                    return MINING_PAUSED_MANUALLY
                case "thermal":
                    return MINING_STOPPED_THERMAL
                case _:
                    raise ValueError(f"unknown blocker: {name}")
        return MINING_STARTED

    def set(self, name: str, reason: str, source: str) -> Transition:
        """Activate a blocker, persisting manual state before publishing it."""
        self._require_name(name)
        prior = self._active.get(name)
        current = Blocker(reason, self._clock(), source)
        if prior is not None and (prior.reason, prior.source) == (reason, source):
            return Transition(name, True, self._message(name, True), False)
        if name == "manual":
            self._write_manual(reason, source)
        self._active[name] = current
        return Transition(name, True, self._message(name, True), True)

    def clear(self, name: str) -> Transition:
        """Clear one blocker without affecting overlapping blockers."""
        self._require_name(name)
        if name == "manual" and self._persistence_error is not None:
            raise self._persistence_error
        changed = name in self._active
        if changed and name == "manual":
            self._remove_manual()
        _ = self._active.pop(name, None)
        message = MINING_STARTED if not self._active else MINING_STILL_BLOCKED
        return Transition(name, False, message, changed)

    def resume(self) -> Transition:
        """Clear manual pause and bypass dwell only; other blockers remain authoritative."""
        transition = self.clear("manual")
        self._manual_resume_requested = True
        return Transition("manual", False, transition.message, transition.changed)

    def active(self) -> dict[str, tuple[str, float]]:
        """Return a snapshot of active blocker reasons and activation times."""
        return {name: (item.reason, item.since_ts) for name, item in self._active.items()}

    def mining_allowed(self) -> bool:
        """Report whether no active or fail-safe blocker remains."""
        return not self._active

    def automatic_stop(self) -> None:
        """Record the monotonic instant an automatic stop occurred."""
        self._last_automatic_stop = self._clock()
        self._manual_resume_requested = False

    def auto_start_allowed(self, now: float | None = None) -> bool:
        """Apply the minimum dwell unless an explicit manual resume bypasses it."""
        if not self.mining_allowed():
            return False
        if self._manual_resume_requested:
            self._manual_resume_requested = False
            return True
        stopped_at = self._last_automatic_stop
        if stopped_at is None:
            return True
        current = self._clock() if now is None else now
        return current >= stopped_at and current - stopped_at >= self._min_dwell_seconds

    def recover_corrupt_state(self, *, confirm_clear: bool = False) -> Transition:
        """Explicitly acknowledge corrupt persistence and remove its fail-safe pause."""
        if not confirm_clear:
            raise ValueError("explicit confirmation required to clear corrupt state")
        try:
            self._state_file.unlink(missing_ok=True)
        except OSError as exc:
            raise StatePersistenceError(f"cannot clear corrupt manual pause: {exc}") from exc
        _ = self._active.pop("manual", None)
        self._persistence_error = None
        message = MINING_STARTED if self.mining_allowed() else MINING_STILL_BLOCKED
        return Transition("manual", False, message, True)

    def _write_manual(self, reason: str, source: str) -> None:
        temporary_name: str | None = None
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self._state_file.parent, delete=False) as temporary:
                temporary_name = temporary.name
                json.dump({"manual": True, "reason": reason, "source": source}, temporary)
                temporary.flush()
                os.fchmod(temporary.fileno(), 0o640)
            os.replace(temporary_name, self._state_file)
        except OSError as exc:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)
            raise StatePersistenceError(f"cannot persist manual pause: {exc}") from exc

    def _remove_manual(self) -> None:
        try:
            self._state_file.unlink(missing_ok=True)
        except OSError as exc:
            raise StatePersistenceError(f"cannot clear manual pause: {exc}") from exc
