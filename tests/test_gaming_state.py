"""Deterministic gaming clearance without live sessions or service effects."""

from dataclasses import FrozenInstanceError, dataclass, replace
from itertools import repeat
from typing import Literal

import pytest

from sparehash.gaming_state import (
    MAX_BATCH,
    MAX_COVERAGE,
    GamingEvidence,
    GamingReducer,
)


def sample(epoch: int = 1) -> GamingEvidence:
    return GamingEvidence(
        provider="gamemode", uid=1000, epoch=epoch, sequence=1,
        observed_at=100.0, state="INACTIVE", health="OK",
        completeness=frozenset({"gamemode:1000"}), producer_version="1",
    )


def configured() -> GamingReducer:
    reducer = GamingReducer()
    reducer.set_coverage(("gamemode:1000",))
    return reducer


def test_accepts_complete_current_epoch() -> None:
    # Given
    reducer = configured()
    evidence = sample(reducer.current_epoch)
    # When
    result = reducer.reduce([evidence], now=100.0)
    # Then
    assert result == "CLEAR"


@dataclass(frozen=True, slots=True)
class FailureCase:
    evidence: tuple[GamingEvidence, ...]
    previous: tuple[GamingEvidence, ...] = ()
    required: tuple[str, ...] = ("gamemode:1000",)
    restart: bool = False
    overflow: bool = False
    expected: Literal["UNKNOWN", "BLOCKED"] = "UNKNOWN"


@pytest.mark.parametrize("case", [FailureCase((evidence,)) for evidence in [
    replace(sample(), state="UNKNOWN"),
    replace(sample(), health="STALE"),
    replace(sample(), health="DISCONNECTED"),
    replace(sample(), health="MALFORMED"),
    replace(sample(), health="OVERFLOW"),
    replace(sample(), observed_at=94.99),
    replace(sample(), observed_at=101.0),
    replace(sample(), observed_at=float("nan")),
    replace(sample(), observed_at=float("inf")),
    replace(sample(), epoch=0),
    replace(sample(), epoch=2),
    replace(sample(), completeness=frozenset()),
    replace(sample(), completeness=frozenset({"gamemode:1001"})),
    replace(sample(), uid=1001),
    replace(sample(), provider="advisory"),
    replace(sample(), sequence=-1),
    replace(sample(), completeness=frozenset(str(i) for i in range(MAX_COVERAGE + 1))),
    replace(sample(), producer_version="x" * 257),
]] + [
    FailureCase((sample(),), previous=(replace(sample(), state="ACTIVE", sequence=2),),
                expected="BLOCKED"),
    FailureCase((sample(),), previous=(sample(),), restart=True),
    FailureCase((sample(),), overflow=True),
    FailureCase((sample(),), required=("gamemode:1000", "gamemode:1001")),
    FailureCase((sample(),), required=("gamemode:1000", "session:1000")),
    FailureCase((sample(), replace(sample(), provider="hook-peer-2", state="ACTIVE")),
                required=("gamemode:1000", "hook-peer-1:1000", "hook-peer-2:1000"),
                expected="BLOCKED"),
])
def test_rejects_stale_or_incomplete_clearance(case: FailureCase) -> None:
    # Given
    reducer = GamingReducer()
    reducer.set_coverage(case.required)
    reducer.reduce(case.previous, now=100.0)
    if case.restart:
        reducer.bump_epoch()
    if case.overflow:
        reducer.reduce(repeat(sample(), MAX_BATCH + 1), now=100.0)
    # When
    result = reducer.reduce(case.evidence, now=100.0)
    # Then
    assert result == case.expected


@pytest.mark.parametrize("state", ["ACTIVE", "UNKNOWN", "INACTIVE"])
def test_manual_does_not_clear(state: str) -> None:
    # Given
    reducer = configured()
    base = sample()
    evidence = base if state == "INACTIVE" else replace(
        base, state="ACTIVE" if state == "ACTIVE" else "UNKNOWN",
    )
    reducer.acknowledge_manual(1000)
    # When
    result = reducer.reduce([evidence], now=100.0)
    # Then
    assert result == "BLOCKED"


@pytest.mark.parametrize("sequence,observed_at", [(1, 101.0), (2, 99.0), (0, 101.0)])
def test_delayed_aggregate_cannot_overwrite_watcher(sequence: int, observed_at: float) -> None:
    # Given
    reducer = configured()
    reducer.reduce([replace(sample(), state="ACTIVE", sequence=2)], now=100.0)
    delayed = replace(sample(), sequence=sequence, observed_at=observed_at)
    # When
    result = reducer.reduce([delayed], now=101.0)
    # Then
    assert result == "BLOCKED"


def test_replay_cannot_refresh_observation_time() -> None:
    # Given
    reducer = configured()
    reducer.reduce([sample()], now=100.0)
    # When
    result = reducer.reduce([replace(sample(), observed_at=106.0)], now=106.0)
    # Then
    assert result == "UNKNOWN"


def test_epoch_restart_requires_positive_reinventory() -> None:
    # Given
    reducer = configured()
    reducer.reduce([sample()], now=100.0)
    reducer.bump_epoch()
    # When
    result = reducer.reduce([sample()], now=100.0)
    # Then
    assert result == "UNKNOWN"


def test_coverage_change_invalidates_previous_inventory() -> None:
    # Given
    reducer = configured()
    reducer.reduce([sample()], now=100.0)
    reducer.set_coverage(("gamemode:1000", "session:1001"))
    # When
    result = reducer.reduce([sample()], now=100.0)
    # Then
    assert result == "UNKNOWN"


@pytest.mark.parametrize("coverage", [(), ("gamemode:1000",)])
def test_initial_or_empty_inventory_is_unknown(coverage: tuple[str, ...]) -> None:
    # Given
    reducer = GamingReducer()
    reducer.set_coverage(coverage)
    # When
    result = reducer.reduce([], now=100.0)
    # Then
    assert result == "UNKNOWN"


def test_overlapping_uids_providers_and_hook_peers_are_unioned() -> None:
    # Given
    reducer = GamingReducer()
    keys = ("gamemode:1000", "gamemode:1001", "hook-peer-1:1000", "hook-peer-2:1000")
    reducer.set_coverage(keys)
    evidence = [
        replace(sample(), provider=key.rsplit(":", 1)[0], uid=int(key.rsplit(":", 1)[1]),
                completeness=frozenset({key}))
        for key in keys
    ]
    evidence[-1] = replace(evidence[-1], state="ACTIVE")
    reducer.reduce(evidence, now=100.0)
    # When: one peer's end cannot end another peer's contribution.
    result = reducer.reduce([replace(evidence[-2], sequence=2)], now=100.0)
    # Then
    assert result == "BLOCKED"


def test_active_dominates_missing_and_unhealthy_coverage() -> None:
    # Given
    reducer = GamingReducer()
    reducer.set_coverage(("gamemode:1000", "session:1001"))
    # When
    result = reducer.reduce([replace(sample(), state="ACTIVE", health="DISCONNECTED")], now=100.0)
    # Then
    assert result == "BLOCKED"


def test_newer_complete_sample_clears_only_its_source() -> None:
    # Given
    reducer = configured()
    reducer.reduce([replace(sample(), state="ACTIVE")], now=100.0)
    # When
    result = reducer.reduce([replace(sample(), sequence=2, observed_at=101.0)], now=101.0)
    # Then
    assert result == "CLEAR"


def test_producer_change_invalidates_epoch() -> None:
    # Given
    reducer = configured()
    reducer.reduce([sample()], now=100.0)
    # When
    result = reducer.reduce([replace(sample(), sequence=2, producer_version="2")], now=100.0)
    # Then
    assert result == "UNKNOWN"


def test_queue_overflow_is_latched_until_reinventory() -> None:
    # Given
    reducer = configured()
    reducer.reduce(repeat(sample(), MAX_BATCH + 1), now=100.0)
    # When
    result = reducer.reduce([replace(sample(), sequence=2)], now=100.0)
    # Then
    assert result == "UNKNOWN"


def test_inventory_overflow_is_unknown() -> None:
    # Given
    reducer = GamingReducer()
    reducer.set_coverage(tuple(f"gamemode:{uid}" for uid in range(MAX_COVERAGE + 1)))
    # When
    result = reducer.reduce([sample()], now=100.0)
    # Then
    assert result == "UNKNOWN"


def test_fresh_current_inventory_recovers_after_overflow_epoch() -> None:
    # Given
    reducer = configured()
    reducer.reduce(repeat(sample(), MAX_BATCH + 1), now=100.0)
    epoch = reducer.bump_epoch()
    # When
    result = reducer.reduce([sample(epoch)], now=100.0)
    # Then
    assert result == "CLEAR"


@pytest.mark.parametrize("field", ["observed_at", "state", "completeness"])
def test_evidence_is_immutable(field: str) -> None:
    # Given
    evidence = sample()
    # When / Then
    with pytest.raises(FrozenInstanceError):
        setattr(evidence, field, 106.0)
