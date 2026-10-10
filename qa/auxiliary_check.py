#!/usr/bin/env python3
"""Strict auxiliary acceptance checks for the SpareHash QA shell suites.

Every subcommand parses real bytes at its trust boundary and fails closed:
exit 0 only when the inspected artifact satisfies its contract, exit 1 with a
named reason otherwise. There is no waiver path and no "warn but pass" mode;
each suite turns these verdicts into its own exit code.

Subcommands:
  status FILE               `sparehash status --json` contract: strict JSON and
                            the required field types/states.
  ipc FILE                  Controller IPC status document: strict JSON plus the
                            ok/ready/generation/blockers/reasons/gaming types.
  action RC LABEL [ALLOWED] A captured command exit status must be allowed
                            (default allowed set is 0; ALLOWED is a comma list).
  bytes FILE SHA256         A captured file must still match its recorded digest.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Final

BLOCKERS: Final = frozenset(("battery", "gaming", "manual", "thermal"))
CONTROLLER_STATES: Final = frozenset(("ready", "starting", "unavailable"))
THERMAL_STATES: Final = frozenset(("available", "UNAVAILABLE"))
HUGEPAGES_STATES: Final = frozenset(("unavailable", "partial", "satisfied"))
UPS_GUARDS: Final = frozenset(("unavailable", "desktop/UPS-only", "battery/unknown"))
USAGE: Final = "usage: auxiliary_check.py {status|ipc|action|bytes} ..."


class CheckError(ValueError):
    """A contract violation with a stable, human-readable reason."""


def _reject_constant(value: str) -> None:
    raise CheckError(f"non-finite JSON constant {value}")


def _load(path: str) -> object:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as error:
        raise CheckError(f"cannot read {path}: {error.strerror or error}") from error
    try:
        return json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as error:
        raise CheckError(f"invalid JSON in {path}: {error}") from error


def _required(data: dict[str, object], field: str) -> object:
    if field not in data:
        raise CheckError(f"missing required field {field!r}")
    return data[field]


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CheckError(f"{label} must be an object")
    return value


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise CheckError(f"{label} must be an array")
    return value


def _string(value: object, label: str, allowed: frozenset[str] | None = None) -> str:
    if not isinstance(value, str):
        raise CheckError(f"{label} must be a string")
    if allowed is not None and value not in allowed:
        raise CheckError(f"{label} must be one of {sorted(allowed)}, got {value!r}")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise CheckError(f"{label} must be a boolean")
    return value


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CheckError(f"{label} must be an integer")
    return value


def _number_or_null(value: object, label: str) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CheckError(f"{label} must be a number or null")
    return value


def _string_or_null(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _string(value, label)


def _check_power(power: object) -> None:
    sources = _object(power, "power")
    for name in ("inventory", "upower", "sysfs"):
        if name not in sources:
            raise CheckError(f"missing required field 'power.{name}'")
        evidence = _object(sources[name], f"power.{name}")
        for field in ("value", "stale"):
            if field not in evidence:
                raise CheckError(f"missing required field 'power.{name}.{field}'")
        value = evidence["value"]
        if value is not None and type(value) is not bool:
            raise CheckError(f"power.{name}.value must be a boolean or null")
        _boolean(evidence["stale"], f"power.{name}.stale")


def _check_scheduler(scheduler: object) -> None:
    data = _object(scheduler, "scheduler")
    for field in ("pid", "policy", "nice", "cpu_weight", "cpu_idle"):
        if field not in data:
            raise CheckError(f"missing required field 'scheduler.{field}'")
        _number_or_null(data[field], f"scheduler.{field}")
    if "requested" not in data:
        raise CheckError("missing required field 'scheduler.requested'")
    _string(data["requested"], "scheduler.requested")


def _check_blocker_pairs(value: object, label: str) -> None:
    entries = _array(value, label)
    for index, entry in enumerate(entries):
        pair = _array(entry, f"{label}[{index}]")
        if len(pair) != 2:
            raise CheckError(f"{label}[{index}] must be a [name, reason] pair")
        _string(pair[0], f"{label}[{index}][0]", BLOCKERS)
        _string(pair[1], f"{label}[{index}][1]")


def _check_status(data: dict[str, object]) -> None:
    _string(_required(data, "controller"), "controller", CONTROLLER_STATES)
    _string(_required(data, "miner"), "miner")
    _check_blocker_pairs(_required(data, "blockers"), "blockers")
    _check_power(_required(data, "power"))
    _string(_required(data, "ups_guard"), "ups_guard", UPS_GUARDS)
    _string(_required(data, "thermal_status"), "thermal_status", THERMAL_STATES)
    _string(_required(data, "hugepages_attainment"), "hugepages_attainment", HUGEPAGES_STATES)
    _string(_required(data, "api"), "api")
    _string_or_null(_required(data, "pool"), "pool")
    _string_or_null(_required(data, "dns_hint"), "dns_hint")
    _string_or_null(_required(data, "thermal_sensor"), "thermal_sensor")
    _string_or_null(_required(data, "numa_warning"), "numa_warning")
    _string(_required(data, "msr"), "msr")
    _number_or_null(_required(data, "temperature_c"), "temperature_c")
    _number_or_null(_required(data, "hashrate"), "hashrate")
    for field in ("hugepages_actual", "hugepages_target"):
        value = _required(data, field)
        if value is not None:
            _integer(value, field)
    nodes = _required(data, "numa_nodes")
    if nodes is not None:
        for index, node in enumerate(_array(nodes, "numa_nodes")):
            _integer(node, f"numa_nodes[{index}]")
    _check_scheduler(_required(data, "scheduler"))
    for index, entry in enumerate(_array(_required(data, "gaming"), "gaming")):
        record = _object(entry, f"gaming[{index}]")
        for field in ("uid", "user", "active", "stale"):
            if field not in record:
                raise CheckError(f"missing required field 'gaming[{index}].{field}'")
        _integer(record["uid"], f"gaming[{index}].uid")
        _string(record["user"], f"gaming[{index}].user")
        active = record["active"]
        if active is not None and type(active) is not bool:
            raise CheckError(f"gaming[{index}].active must be a boolean or null")
        _boolean(record["stale"], f"gaming[{index}].stale")


def _status(path: str) -> None:
    data = _object(_load(path), "status document")
    _check_status(data)


def _check_ipc(data: dict[str, object]) -> None:
    if data.get("ok") is not True:
        raise CheckError(f"controller reported ok={data.get('ok')!r}: {data.get('error', 'no error field')!r}")
    _boolean(_required(data, "ready"), "ready")
    _boolean(_required(data, "running"), "running")
    _integer(_required(data, "generation"), "generation")
    blockers = [_string(name, "blockers[]", BLOCKERS) for name in _array(_required(data, "blockers"), "blockers")]
    if len(set(blockers)) != len(blockers):
        raise CheckError("blockers contains duplicates")
    reasons = _object(_required(data, "reasons"), "reasons")
    if set(reasons) != set(blockers):
        raise CheckError(f"reasons {sorted(reasons)} must match blockers {sorted(blockers)}")
    for name, reason in reasons.items():
        _string(reason, f"reasons[{name}]")
    _array(_required(data, "debug"), "debug")
    age = _number_or_null(_required(data, "observation_age"), "observation_age")
    if age is not None and age < 0:
        raise CheckError(f"observation_age must not be negative: {age}")
    for index, entry in enumerate(_array(_required(data, "gaming"), "gaming")):
        pair = _array(entry, f"gaming[{index}]")
        if len(pair) != 2:
            raise CheckError(f"gaming[{index}] must be a [uid, active] pair")
        _integer(pair[0], f"gaming[{index}][0]")
        if pair[1] is not None and type(pair[1]) is not bool:
            raise CheckError(f"gaming[{index}][1] must be a boolean or null")


def _ipc(path: str) -> None:
    _check_ipc(_object(_load(path), "ipc document"))


def _action(argv: list[str]) -> None:
    if len(argv) not in (2, 3):
        raise CheckError(USAGE)
    label = argv[1]
    try:
        code = int(argv[0], 10)
    except ValueError as error:
        raise CheckError(f"captured exit status {argv[0]!r} is not an integer") from error
    allowed_text = argv[2] if len(argv) == 3 else "0"
    try:
        allowed = {int(piece, 10) for piece in allowed_text.split(",") if piece.strip()}
    except ValueError as error:
        raise CheckError(f"allowed exit statuses {allowed_text!r} are not integers") from error
    if not allowed:
        raise CheckError(f"allowed exit statuses {allowed_text!r} are empty")
    if code not in allowed:
        raise CheckError(f"action {label!r} exited {code}; allowed {sorted(allowed)}")


def _bytes(argv: list[str]) -> None:
    if len(argv) != 2:
        raise CheckError(USAGE)
    path, expected = argv
    if len(expected) != 64 or any(character not in "0123456789abcdefABCDEF" for character in expected):
        raise CheckError(f"expected digest {expected!r} is not a lowercase or uppercase sha256 hex digest")
    try:
        payload = Path(path).read_bytes()
    except OSError as error:
        raise CheckError(f"cannot read {path}: {error.strerror or error}") from error
    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected.lower():
        raise CheckError(f"changed bytes: {path} now hashes to {digest}, recorded {expected.lower()}")


def main(argv: list[str]) -> int:
    if len(argv) < 1:
        print(USAGE, file=sys.stderr)
        return 2
    command, rest = argv[0], argv[1:]
    try:
        match command:
            case "status":
                if len(rest) != 1:
                    raise CheckError(USAGE)
                _status(rest[0])
            case "ipc":
                if len(rest) != 1:
                    raise CheckError(USAGE)
                _ipc(rest[0])
            case "action":
                _action(rest)
            case "bytes":
                _bytes(rest)
            case _:
                print(USAGE, file=sys.stderr)
                return 2
    except CheckError as error:
        print(f"FAIL: auxiliary {command}: {error}", file=sys.stderr)
        return 1
    print(f"auxiliary ok: {command} {' '.join(rest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
