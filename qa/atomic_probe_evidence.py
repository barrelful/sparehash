"""Task-3 raw observations: unknown or contradictory records never certify a run."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, TypeAlias

PROPERTIES: Final = ("ProtectSystem=strict", "ProtectHome=yes", "PrivateNetwork=yes",
                     "NoNewPrivileges=yes", "PrivateTmp=yes")
CONTEXT_PATHS: Final = ("/var/opt", "/var/usrlocal", "/var/usrlocal/bin",
                        "/var/opt/xmrig/task3/xmrig", "/var/opt/xmrig/task3/config.json")
MANAGER_KEYS: Final = frozenset((*[item.split("=")[0] for item in PROPERTIES],
                               "User", "Group", "ExecMainPID", "ExecMainStatus"))
AUDIT_KEYS: Final = frozenset(("enabled", "failure", "pid", "rate_limit", "backlog_limit",
                             "lost", "backlog", "backlog_wait_time", "backlog_wait_time_actual",
                             "loginuid_immutable"))
Json: TypeAlias = str | int | float | bool | None | list["Json"] | dict[str, "Json"]


@dataclass(frozen=True, slots=True)
class RefusalError(Exception):
    reason: str

    def __post_init__(self) -> None:
        Exception.__init__(self, f"BLOCKED: {self.reason}")


@dataclass(frozen=True, slots=True)
class Evidence:
    properties: tuple[str, ...]
    uid: int
    gid: int
    pid: int
    exit_code: int
    contexts: tuple[tuple[str, str], ...]
    audit_known: bool
    avc: str
    cleaned: bool


@dataclass(frozen=True, slots=True)
class Manager:
    properties: tuple[str, ...]
    identity: tuple[int, int, int]


def read_raw(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RefusalError("raw observation is not a regular file")
        content = stream.read(65537)
    if len(content) > 65536:
        raise RefusalError("raw observation exceeds bound")
    return content.decode("utf-8")


def manager_record(lines: tuple[str, ...]) -> Manager:
    observed: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if separator != "=" or key not in MANAGER_KEYS or key in observed or not value or "=" in value:
            raise RefusalError("duplicate, unknown or malformed manager field")
        observed[key] = value
    if frozenset(observed) != MANAGER_KEYS or not set(PROPERTIES).issubset(lines):
        raise RefusalError("missing/wrong effective manager property")
    for key in ("User", "Group", "ExecMainPID"):
        if not re.fullmatch(r"[1-9][0-9]*", observed[key]):
            raise RefusalError("manager identity is not canonical nonroot numeric evidence")
    if observed["ExecMainStatus"] != "0":
        raise RefusalError("manager execution failed")
    return Manager(lines, (int(observed["User"]), int(observed["Group"]), int(observed["ExecMainPID"])))


def validate_evidence(evidence: Evidence, identity: tuple[int, int]) -> None:
    keys = [line.partition("=")[0] for line in evidence.properties]
    if len(set(keys)) != len(keys) or any(key not in MANAGER_KEYS for key in keys) or not set(PROPERTIES).issubset(evidence.properties):
        raise RefusalError("contradictory/missing effective mandatory isolation property")
    if identity[0] <= 0 or identity[1] <= 0 or (evidence.uid, evidence.gid) != identity:
        raise RefusalError("execution identity not independently observed")
    if evidence.pid <= 0 or evidence.exit_code != 0:
        raise RefusalError("execution failed or PID unknown")
    if not evidence.contexts or any(not re.fullmatch(r"[^:]+:[^:]+:[^:]+:.+", expected) or expected != actual for expected, actual in evidence.contexts):
        raise RefusalError("installed-policy/actual contexts unknown or mismatched")
    if not evidence.audit_known or evidence.avc.strip() or not evidence.cleaned:
        raise RefusalError("audit unknown/AVC present/cleanup incomplete")


def audit_status(raw: str) -> int:
    observed: dict[str, str] = {}
    for line in raw.splitlines():
        key, separator, value = line.partition(" ")
        if not separator or key not in AUDIT_KEYS or key in observed:
            raise RefusalError("malformed/duplicate audit status field")
        if not re.fullmatch(r"[0-9]+(?: (?:unlocked|locked))?", value):
            raise RefusalError("unknown audit status value")
        observed[key] = value
    if not {"enabled", "pid", "lost", "backlog"}.issubset(observed):
        raise RefusalError("audit status incomplete")
    if observed["enabled"] not in ("1", "2") or observed["lost"] != "0" or observed["backlog"] != "0" or not re.fullmatch(r"[1-9][0-9]*", observed["pid"]):
        raise RefusalError("auditing disabled, unavailable, lossy or backlogged")
    return int(observed["enabled"])


def validate_audit(directory: Path, mode: str, pid: int) -> None:
    def raw(suffix: str) -> str:
        return read_raw(directory / f"{mode}.{suffix}")
    for suffix in ("audit-before.exit", "audit-after.exit", "properties.exit"):
        if raw(suffix) != "0\n":
            raise RefusalError(f"observation command unavailable: {suffix}")
    if audit_status(raw("audit-before")) != audit_status(raw("audit-after")):
        raise RefusalError("audit mode changed during probe")
    window = re.fullmatch(r"pid=([1-9][0-9]*)\nstart=([^\n]+)\nend=([^\n]+)\n", raw("audit-window"))
    if window is None or int(window[1]) != pid:
        raise RefusalError("audit window PID does not match observed execution")
    start, end = (datetime.strptime(window[index], "%m/%d/%Y %H:%M:%S") for index in (2, 3))
    if not 0 <= (end - start).total_seconds() <= 120:
        raise RefusalError("audit window invalid/unbounded")
    command = ("/usr/sbin/ausearch", "-m", "AVC,USER_AVC", "-p", str(pid), "-ts",
               *window[2].split(" "), "-te", *window[3].split(" "), "--raw")
    if tuple(raw("audit-command").splitlines()) != command:
        raise RefusalError("audit query attribution/window not independently retained")
    query = raw("audit-query")
    if query != "<no matches>\n" or raw("audit-query.exit") != "1\n":
        raise RefusalError("raw audit query contains AVC, error, unknown output or exit status")
    if raw("audit-known") != "yes\n" or raw("avc") != "":
        raise RefusalError("derived audit markers disagree with raw evidence")


def contexts_record(directory: Path) -> tuple[tuple[str, str], ...]:
    rows = [line.split("\t") for line in read_raw(directory / "contexts-paths").splitlines()]
    if len(rows) != len(CONTEXT_PATHS) or any(len(row) != 3 for row in rows):
        raise RefusalError("incomplete path-bearing context inventory")
    if tuple(row[0] for row in rows) != CONTEXT_PATHS:
        raise RefusalError("context paths do not match exact probe surface")
    pairs = tuple((row[1], row[2]) for row in rows)
    if read_raw(directory / "contexts") != "".join(f"{expected}\t{actual}\n" for expected, actual in pairs):
        raise RefusalError("context summary contradicts raw path observations")
    return pairs


def summarize_raw(directory: Path, binding: Callable[[Path], tuple[int, int]]) -> None:
    failures: list[str] = []
    try:
        identity = binding(directory)
        _ = int(read_raw(directory / "legacy.exit"))
        if not read_raw(directory / "legacy.output").strip():
            raise RefusalError("legacy comparison unknown")
        contexts = contexts_record(directory)
        for mode in ("version", "dry-run"):
            manager = manager_record(tuple(read_raw(directory / f"{mode}.properties").splitlines()))
            first = read_raw(directory / f"{mode}.output").splitlines()[0]
            if first != " ".join(str(value) for value in manager.identity):
                raise RefusalError("manager identity disagrees with sandbox observation")
            validate_audit(directory, mode, manager.identity[2])
            evidence = Evidence(manager.properties, *manager.identity,
                                int(read_raw(directory / f"{mode}.exit")), contexts, True, "",
                                read_raw(directory / "cleanup") == "removed-owned-staging-and-units\n")
            validate_evidence(evidence, identity)
    except (OSError, ValueError, KeyError, IndexError, RefusalError) as error:
        failures.append(str(error))
    result = {"status": "BLOCKED" if failures else "FEASIBLE", "failures": failures,
              "release_acceptance": False, "scope": "Task 3 only; independent guest verification required"}
    _ = (directory / "probe.json").write_text(json.dumps(result, indent=2) + "\n")
    if failures:
        raise RefusalError("required raw evidence incomplete; see probe.json")
