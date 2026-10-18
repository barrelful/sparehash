# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Bounded, rootless C1 admission probe for full cross-mode gaming integration.

This is a QA-only discovery tool, not a production provider. It never starts a
real Steam session, never patches Steam, never requires per-game setup, adds no
dependency and ships no recurring scanner. It evaluates a single, separately
captured lifecycle *candidate contract* against the C1 admission criteria and
writes a strict ``admission.json`` plus ``overhead.json``.

Decision semantics (plan section C1):

* ``ADMISSIBLE_CANDIDATE`` -- a complete, maintained, BOTH-mode producer
  contract was supplied and every C1 invariant holds. This is a research
  candidate only; it is not platform acceptance.
* ``NO_DEPENDABLE_ROUTE`` -- a contract was evaluated and is missing at least
  one required invariant. ``admission.json`` names every reason code.
* ``BLOCKED_ENVIRONMENT`` -- no authorized capture surface was supplied, so no
  contract could be evaluated. This is a valid research outcome and still
  blocks Wave 2 / full support.

Capture is deliberately out of band. Real capture requires an authorized,
disposable Bazzite/Steam surface and is gated behind ``--capture-source`` (a
directory containing ``contract.json`` and optional raw traces). This probe
only replays that capture; it must never drive Steam itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

ADMISSIBLE_CANDIDATE: Final = "ADMISSIBLE_CANDIDATE"
NO_DEPENDABLE_ROUTE: Final = "NO_DEPENDABLE_ROUTE"
BLOCKED_ENVIRONMENT: Final = "BLOCKED_ENVIRONMENT"

DECISIONS: Final = (ADMISSIBLE_CANDIDATE, NO_DEPENDABLE_ROUTE, BLOCKED_ENVIRONMENT)
MODES: Final = ("gaming_mode", "kde")

# C1 budgets: incremental idle CPU <= 1% of ONE logical CPU over 10 minutes,
# incremental RSS <= 32 MiB. Query/wakeup frequency and session scaling must be
# recorded; a null value means "not yet measured" and is allowed only when the
# run is BLOCKED.
BUDGET_IDLE_CPU_PERCENT_MAX: Final = 1.0
BUDGET_INCREMENTAL_RSS_MIB_MAX: Final = 32.0

# Negative-proof invariant. Only a complete current-epoch inventory may clear
# inhibition; scope/reaper silence, a recurring process scan, or a finite set
# of positive trials cannot prove exhaustive negative coverage.
NEGATIVE_PROOF_COMPLETE_INVENTORY: Final = "complete_inventory"
NEGATIVE_PROOF_REASONS: Final = {
    "scope_reaper_silence": "SCOPES_REAPER_SILENCE",
    "process_scan": "PROCESS_SCAN_NOT_NEGATIVE_PROOF",
    "finite_trials": "FINITE_TRIALS_NOT_NEGATIVE_PROOF",
}

REASON_GAMING_MODE_ONLY: Final = "GAMING_MODE_ONLY"
REASON_KDE_ONLY: Final = "KDE_ONLY"
REASON_MISSING_MODE_COVERAGE: Final = "MISSING_MODE_COVERAGE"
REASON_PRODUCER_FINGERPRINT_MISSING: Final = "PRODUCER_FINGERPRINT_MISSING"
REASON_UNSUPPORTED_PRODUCER: Final = "UNSUPPORTED_PRODUCER"
REASON_MODE_PRODUCER_MISSING: Final = "MODE_PRODUCER_MISSING"
REASON_MISSING_INITIAL_INVENTORY: Final = "MISSING_INITIAL_INVENTORY"
REASON_NO_DEPENDABLE_LIFETIME: Final = "NO_DEPENDABLE_LIFETIME"
REASON_MISSING_LIFETIME_ARGUMENT: Final = "MISSING_LIFETIME_ARGUMENT"
REASON_MISSING_HEALTH: Final = "MISSING_HEALTH"
REASON_MISSING_NEGATIVE_PROOF: Final = "MISSING_NEGATIVE_PROOF"
REASON_OVER_BUDGET: Final = "OVER_BUDGET"

ADMISSION_TOP_KEYS: Final = frozenset(
    {
        "decision",
        "evaluated_at",
        "evidence",
        "both_mode_coverage",
        "producer_fingerprint",
        "lifetime_argument",
        "budgets",
        "unresolved_tests",
        "rationale",
    }
)
COVERAGE_KEYS: Final = frozenset(
    {
        "producer",
        "initial_inventory_complete",
        "lifetime_owner",
        "health_signals",
        "negative_proof_argument",
    }
)
FINGERPRINT_KEYS: Final = frozenset({"name", "version", "source", "supported_by_upstream"})
BUDGET_KEYS: Final = frozenset(
    {"idle_cpu_percent_max", "incremental_rss_mib_max", "subprocess_query_per_minute_max"}
)

# Evidence URLs are the exact upstream/primary sources cited by the plan's
# research index (S2-S5). They are recorded in every admission document so the
# contract is self-describing without the notepad.
EVIDENCE_SOURCES: Final = (
    "sparehash/gaming.py:107-148",
    "sparehash/runtime.py:55-82",
    "https://github.com/ublue-os/bazzite/blob/9258b2c43ff163c9ac024627a1ae76b7d78605fa/Containerfile",
    "https://github.com/ublue-os/bazzite/blob/9258b2c43ff163c9ac024627a1ae76b7d78605fa/system_files/deck/shared/usr/lib/sddm/sddm.conf.d/holo.conf",
    "https://github.com/ValveSoftware/gamescope/commit/6191bc8b12825280a21b7802458f3c2a06d945e6",
    "https://github.com/OpenGamingCollective/gamescope-session/blob/08e65a62ed3d7acc6ae17db637091d402a55ae2a/usr/lib/systemd/user/gamescope-session-plus@.service",
    "https://github.com/systemd/systemd/blob/main/man/org.freedesktop.login1.xml",
    "https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html",
)

UNRESOLVED_C1_TESTS: Final = (
    "Global maintained producer usable while Steam is idle in Gaming Mode",
    "Global maintained producer usable while Steam is idle in KDE",
    "Complete initial producer inventory at startup in both modes",
    "Full native lifetime coverage: launch, gameplay, exit, crash, launcher/child survival",
    "Full Proton lifetime coverage: launch, gameplay, exit, crash, launcher/child survival",
    "Health/monitor-loss signal with bounded reconciliation and reconnect",
    "Versioned producer fingerprint with supported-by-upstream status",
    "Negative-proof invariant from complete inventory, not scope/reaper silence or finite trials",
    "Demonstrated budgets: idle CPU <= 1%, RSS <= 32 MiB, recorded query/wakeup frequency",
    "Mode switch, Steam restart, new UID, logout/relogin, controller restart, reboot in both modes",
)


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """Outcome of applying the C1 criteria to a candidate contract."""

    decision: str
    reasons: tuple[str, ...]


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _number_within(value: object, maximum: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= maximum


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _as_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _evaluate_mode(entry: Mapping[str, object]) -> list[str]:
    reasons: list[str] = []
    if not _nonempty(entry.get("producer")):
        reasons.append(REASON_MODE_PRODUCER_MISSING)
    if entry.get("initial_inventory_complete") is not True:
        reasons.append(REASON_MISSING_INITIAL_INVENTORY)
    if not _nonempty(entry.get("lifetime_owner")):
        reasons.append(REASON_NO_DEPENDABLE_LIFETIME)
    health = entry.get("health_signals")
    if not isinstance(health, list) or not health or not all(_nonempty(item) for item in health):
        reasons.append(REASON_MISSING_HEALTH)
    if not _nonempty(entry.get("negative_proof_argument")):
        reasons.append(REASON_MISSING_NEGATIVE_PROOF)
    return reasons


def evaluate_contract(contract: Mapping[str, object]) -> EvaluationResult:
    """Apply C1 admission criteria to a captured candidate contract.

    Returns ``ADMISSIBLE_CANDIDATE`` only when every invariant holds across
    BOTH modes; otherwise ``NO_DEPENDABLE_ROUTE`` with one reason per failure.
    """
    reasons: list[str] = []

    fingerprint = contract.get("producer_fingerprint")
    if not isinstance(fingerprint, Mapping):
        reasons.append(REASON_PRODUCER_FINGERPRINT_MISSING)
    else:
        if not _nonempty(fingerprint.get("name")) or not _nonempty(fingerprint.get("version")):
            reasons.append(REASON_PRODUCER_FINGERPRINT_MISSING)
        if fingerprint.get("supported_by_upstream") is not True or fingerprint.get("source") in (
            None,
            "",
            "unavailable",
        ):
            reasons.append(REASON_UNSUPPORTED_PRODUCER)

    coverage = contract.get("both_mode_coverage")
    coverage_map = _as_mapping(coverage)
    has_gaming = "gaming_mode" in coverage_map
    has_kde = "kde" in coverage_map
    if has_gaming and not has_kde:
        reasons.append(REASON_GAMING_MODE_ONLY)
    elif has_kde and not has_gaming:
        reasons.append(REASON_KDE_ONLY)
    elif not has_gaming and not has_kde:
        reasons.append(REASON_MISSING_MODE_COVERAGE)
    for mode in MODES:
        entry = coverage_map.get(mode)
        if isinstance(entry, Mapping):
            reasons.extend(_evaluate_mode(entry))

    if not _nonempty(contract.get("lifetime_argument")):
        reasons.append(REASON_MISSING_LIFETIME_ARGUMENT)

    basis = contract.get("negative_proof_basis")
    if basis != NEGATIVE_PROOF_COMPLETE_INVENTORY:
        reasons.append(
            NEGATIVE_PROOF_REASONS.get(str(basis), REASON_MISSING_NEGATIVE_PROOF)
        )

    budgets = contract.get("budgets")
    if not isinstance(budgets, Mapping) or not _number_within(
        budgets.get("idle_cpu_percent_max"), BUDGET_IDLE_CPU_PERCENT_MAX
    ) or not _number_within(
        budgets.get("incremental_rss_mib_max"), BUDGET_INCREMENTAL_RSS_MIB_MAX
    ):
        reasons.append(REASON_OVER_BUDGET)

    if reasons:
        return EvaluationResult(NO_DEPENDABLE_ROUTE, tuple(dict.fromkeys(reasons)))
    return EvaluationResult(ADMISSIBLE_CANDIDATE, ())


def _coverage_block(entry: Mapping[str, object]) -> dict[str, object]:
    producer = entry.get("producer")
    owner = entry.get("lifetime_owner")
    argument = entry.get("negative_proof_argument")
    return {
        "producer": producer if _nonempty(producer) else None,
        "initial_inventory_complete": entry.get("initial_inventory_complete") is True,
        "lifetime_owner": owner if _nonempty(owner) else None,
        "health_signals": _as_string_list(entry.get("health_signals")),
        "negative_proof_argument": argument if _nonempty(argument) else "",
    }


def _fingerprint_block(contract: Mapping[str, object] | None) -> dict[str, object]:
    if contract is None:
        return {
            "name": None,
            "version": None,
            "source": "unavailable",
            "supported_by_upstream": False,
        }
    fingerprint = _as_mapping(contract.get("producer_fingerprint"))
    name, version, source = (
        fingerprint.get("name"),
        fingerprint.get("version"),
        fingerprint.get("source"),
    )
    return {
        "name": name if _nonempty(name) else None,
        "version": version if _nonempty(version) else None,
        "source": source if _nonempty(source) else "unavailable",
        "supported_by_upstream": fingerprint.get("supported_by_upstream") is True,
    }


def _budget_block(contract: Mapping[str, object] | None) -> dict[str, object]:
    if contract is None:
        return {
            "idle_cpu_percent_max": BUDGET_IDLE_CPU_PERCENT_MAX,
            "incremental_rss_mib_max": BUDGET_INCREMENTAL_RSS_MIB_MAX,
            "subprocess_query_per_minute_max": None,
        }
    budgets = _as_mapping(contract.get("budgets"))

    def _pick(key: str, fallback: object) -> object:
        value = budgets.get(key)
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else fallback

    return {
        "idle_cpu_percent_max": _pick(
            "idle_cpu_percent_max", BUDGET_IDLE_CPU_PERCENT_MAX
        ),
        "incremental_rss_mib_max": _pick(
            "incremental_rss_mib_max", BUDGET_INCREMENTAL_RSS_MIB_MAX
        ),
        "subprocess_query_per_minute_max": _pick(
            "subprocess_query_per_minute_max", None
        ),
    }


def _coverage_from(contract: Mapping[str, object] | None) -> dict[str, object]:
    coverage = _as_mapping(contract.get("both_mode_coverage")) if contract is not None else {}
    return {
        mode: _coverage_block(_as_mapping(coverage.get(mode))) for mode in MODES
    }


def build_admission(
    contract: Mapping[str, object] | None,
    *,
    evaluated_at: str,
    evidence: Sequence[str],
    unresolved_tests: Sequence[str] | None = None,
    catalog: Path | None = None,
) -> dict[str, object]:
    """Build a strict ``admission.json`` document.

    ``contract`` is ``None`` when no authorized capture surface exists, which
    yields ``BLOCKED_ENVIRONMENT``. Otherwise the contract is evaluated.
    """
    record_urls = [*EVIDENCE_SOURCES, *[item for item in evidence if _nonempty(item)]]
    if catalog is not None and catalog.exists():
        record_urls.append(str(catalog))

    if contract is None:
        return {
            "decision": BLOCKED_ENVIRONMENT,
            "evaluated_at": evaluated_at,
            "evidence": record_urls,
            "both_mode_coverage": _coverage_from(None),
            "producer_fingerprint": _fingerprint_block(None),
            "lifetime_argument": (
                "No lifetime argument: no authorized Steam/Gaming-Mode/KDE capture "
                "surface was provided, so no producer lifetime could be observed."
            ),
            "budgets": _budget_block(None),
            "unresolved_tests": list(UNRESOLVED_C1_TESTS),
            "rationale": (
                "BLOCKED_ENVIRONMENT: the probe ran rootless with --read-only and no "
                "--capture-source, and the host has no authorized disposable "
                "Bazzite/Steam surface. No candidate contract was evaluated; this is "
                "documented absence, not a mocked admission. Wave 2 / full support "
                "remains blocked (G0)."
            ),
        }

    result = evaluate_contract(contract)
    coverage = _coverage_from(contract)
    lifetime_argument = contract.get("lifetime_argument")
    unresolved = list(unresolved_tests or ())
    if result.decision == ADMISSIBLE_CANDIDATE:
        rationale = (
            "ADMISSIBLE_CANDIDATE: the captured contract names a supported, versioned "
            "producer with complete initial inventory, lifetime owner, health signals "
            "and a complete-inventory negative-proof invariant in BOTH Gaming Mode and "
            "KDE, within budget. This is a research candidate only, not platform "
            "acceptance."
        )
    else:
        unresolved = list(dict.fromkeys([*UNRESOLVED_C1_TESTS, *unresolved]))
        rationale = (
            "NO_DEPENDABLE_ROUTE: the captured contract is missing required C1 "
            "invariants (reasons: " + ", ".join(result.reasons) + "). Finite positive "
            "trials do not prove complete negative coverage. Wave 2 / full support is "
            "blocked; present this to the owner before expanding work."
        )
    return {
        "decision": result.decision,
        "evaluated_at": evaluated_at,
        "evidence": record_urls,
        "both_mode_coverage": coverage,
        "producer_fingerprint": _fingerprint_block(contract),
        "lifetime_argument": lifetime_argument if _nonempty(lifetime_argument) else "",
        "budgets": _budget_block(contract),
        "unresolved_tests": unresolved,
        "rationale": rationale,
    }


def validate_admission(document: Mapping[str, object]) -> None:
    """Reject any document that deviates from the strict admission schema."""
    if set(document.keys()) != ADMISSION_TOP_KEYS:
        raise ValueError(f"admission keys mismatch: {sorted(document.keys())}")
    if document["decision"] not in DECISIONS:
        raise ValueError(f"invalid decision: {document['decision']!r}")
    if not isinstance(document["evidence"], list) or not all(
        isinstance(item, str) for item in document["evidence"]
    ):
        raise ValueError("evidence must be a list of strings")
    coverage = document["both_mode_coverage"]
    if not isinstance(coverage, Mapping) or set(coverage.keys()) != set(MODES):
        raise ValueError("both_mode_coverage must contain exactly gaming_mode and kde")
    for mode in MODES:
        entry = coverage[mode]
        if not isinstance(entry, Mapping) or set(entry.keys()) != COVERAGE_KEYS:
            raise ValueError(f"{mode} coverage keys mismatch")
        if not isinstance(entry["initial_inventory_complete"], bool):
            raise ValueError(f"{mode}.initial_inventory_complete must be bool")
        if not isinstance(entry["health_signals"], list):
            raise ValueError(f"{mode}.health_signals must be a list")
    fingerprint = document["producer_fingerprint"]
    if not isinstance(fingerprint, Mapping) or set(fingerprint.keys()) != FINGERPRINT_KEYS:
        raise ValueError("producer_fingerprint keys mismatch")
    if not isinstance(fingerprint["supported_by_upstream"], bool):
        raise ValueError("supported_by_upstream must be bool")
    budgets = document["budgets"]
    if not isinstance(budgets, Mapping) or set(budgets.keys()) != BUDGET_KEYS:
        raise ValueError("budgets keys mismatch")
    if not isinstance(document["unresolved_tests"], list):
        raise ValueError("unresolved_tests must be a list")
    if not isinstance(document["rationale"], str) or not document["rationale"]:
        raise ValueError("rationale must be a non-empty string")


def _build_overhead(
    decision: str, contract: Mapping[str, object] | None, catalog: Path | None
) -> dict[str, object]:
    return {
        "measured": False,
        "decision": decision,
        "reason": (
            "No capture source: budgets are declared design limits, not measurements. "
            "Real measurement requires an authorized disposable Bazzite/Steam surface."
            if contract is None
            else "Contract replay only: overhead is not measured by this stub."
        ),
        "budgets": _budget_block(contract),
        "measurements": None,
        "catalog": str(catalog) if catalog is not None and catalog.exists() else None,
    }


def _load_contract(capture_source: Path) -> dict[str, object]:
    contract_path = capture_source / "contract.json"
    parsed = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"{contract_path} must contain a JSON object")
    return parsed


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rootless C1 admission probe for full cross-mode gaming integration. "
            "Never starts Steam; evaluates a captured candidate contract."
        )
    )
    parser.add_argument("--output", type=Path, required=True, help="admission.json path")
    parser.add_argument(
        "--read-only",
        action="store_true",
        help=(
            "Perform no host capture or mutation; only the requested evidence files "
            "are written. With no --capture-source this records BLOCKED_ENVIRONMENT."
        ),
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=None,
        help="Optional game-title catalog JSON; recorded as evidence when present.",
    )
    parser.add_argument(
        "--capture-source",
        type=Path,
        default=None,
        help=(
            "Directory containing a previously captured contract.json (and optional "
            "raw traces). Capture MUST be performed separately by an operator on an "
            "authorized disposable Bazzite/Steam surface; this probe never drives "
            "Steam. Without this flag the probe records BLOCKED_ENVIRONMENT."
        ),
    )
    parser.add_argument(
        "--evaluated-at",
        default=None,
        help="Override the ISO-8601 UTC evaluation timestamp (deterministic tests).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    output: Path = args.output
    catalog: Path | None = args.catalog
    capture_source: Path | None = args.capture_source

    contract: dict[str, object] | None = None
    if capture_source is not None:
        try:
            contract = _load_contract(capture_source)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"probe_gaming: capture source unreadable: {exc}", file=sys.stderr)
            return 1

    evaluated_at = args.evaluated_at or datetime.now(UTC).isoformat()
    admission = build_admission(
        contract, evaluated_at=evaluated_at, evidence=(), catalog=catalog
    )
    validate_admission(admission)
    overhead = _build_overhead(str(admission["decision"]), contract, catalog)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(admission, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output.parent / "overhead.json").write_text(
        json.dumps(overhead, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(admission, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
