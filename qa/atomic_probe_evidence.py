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
from typing import Final, Literal, TypeAlias

PROPERTIES: Final = ("ProtectSystem=strict", "ProtectHome=yes", "PrivateNetwork=yes",
                     "NoNewPrivileges=yes", "PrivateTmp=yes")
CONTEXT_PATHS: Final = ("/var/opt", "/var/usrlocal", "/var/usrlocal/bin",
                        "/var/opt/xmrig/task3/xmrig", "/var/opt/xmrig/task3/config.json")
# First three entries are redirect roots inspected as-is; the last two are the
# task-staged files whose SELinux user field reflects the creating domain.
STAGED_CONTEXT_PATHS: Final = CONTEXT_PATHS[3:]
STAGED_EXPECTED_USER: Final = "system_u"
STAGED_ACCEPTED_USERS: Final = ("system_u", "unconfined_u")
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


def legacy_assumption(directory: Path, identity: tuple[int, int]) -> Literal["supported", "unsupported"]:
    """Interpret ONLY the two documented legacy Group=nogroup outcomes.

    On platforms without the legacy ``nogroup`` group (Bazzite/Fedora)
    systemd cannot resolve the group and fails before exec with
    ExecMainStatus=216 (EXIT_GROUP), empty output and a nonzero wrapper
    exit; the legacy assumption is UNSUPPORTED there, an observation and
    not a harness failure.  Where ``nogroup`` exists the unit executes,
    exits zero and its first output line is the wrapper identity.  Any
    other status, contradictory output/exit, duplicated or missing
    manager properties, or a Group other than exactly ``nogroup`` blocks.
    """
    lines = tuple(read_raw(directory / "legacy.properties").splitlines())
    observed: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if separator != "=" or key not in MANAGER_KEYS or key in observed or not value or "=" in value:
            raise RefusalError("duplicate, unknown or malformed legacy manager field")
        observed[key] = value
    if frozenset(observed) != MANAGER_KEYS or not set(PROPERTIES).issubset(lines):
        raise RefusalError("legacy comparison missing mandatory manager property")
    if observed["Group"] != "nogroup":
        raise RefusalError("legacy comparison did not retain Group=nogroup")
    if not re.fullmatch(r"[1-9][0-9]*", observed["User"]) or int(observed["User"]) != identity[0]:
        raise RefusalError("legacy comparison identity does not match observed execution")
    if not re.fullmatch(r"[1-9][0-9]*", observed["ExecMainPID"]):
        raise RefusalError("legacy comparison main PID unknown")
    exit_text = read_raw(directory / "legacy.exit")
    if not re.fullmatch(r"[0-9]+\n", exit_text):
        raise RefusalError("legacy comparison exit status malformed")
    exit_code = int(exit_text)
    output = read_raw(directory / "legacy.output")
    status = observed["ExecMainStatus"]
    if status == "216":
        # EXIT_GROUP: systemd failed to resolve/set the legacy group before
        # exec.  Nothing ran, so empty output and a nonzero exit are expected.
        if output or exit_code == 0:
            raise RefusalError("legacy nogroup-unavailable observation contradicted")
        return "unsupported"
    if status == "0":
        # The sandbox ran: exit zero and the first output line is its
        # identity, consistent with the recorded manager properties.
        if exit_code != 0:
            raise RefusalError("legacy comparison exit status contradicts manager result")
        first = output.splitlines()
        if not first:
            raise RefusalError("legacy comparison output missing identity line")
        identity_line = re.fullmatch(r"([1-9][0-9]*) ([1-9][0-9]*) ([1-9][0-9]*)", first[0])
        if identity_line is None:
            raise RefusalError("legacy comparison identity line malformed")
        if (int(identity_line[1]) != int(observed["User"])
                or int(identity_line[3]) != int(observed["ExecMainPID"])):
            raise RefusalError("legacy comparison identity contradicts manager record")
        return "supported"
    raise RefusalError("legacy comparison status is not a documented outcome")


def split_context(value: str) -> tuple[str, str, str, str]:
    """Exactly four non-empty user:role:type:level fields (MLS levels may hold colons)."""
    parts = value.split(":", 3)
    if len(parts) != 4 or any(not part for part in parts):
        raise RefusalError("malformed SELinux context")
    return parts[0], parts[1], parts[2], parts[3]


def validate_contexts(contexts: tuple[tuple[str, str], ...]) -> None:
    """Two-class installed-policy/actual comparison over CONTEXT_PATHS order.

    Redirect roots are inspected, never written, and require exact equality.
    Task-staged files are created by the probe's own staging: their SELinux
    user field records the creating domain (root's system_u or an unconfined
    caller's unconfined_u) and does not participate in type enforcement.
    Equivalence is therefore exact role:type:level equality with expected user
    system_u and actual user system_u/unconfined_u; the mandatory no-AVC audit
    evidence independently establishes that no policy denial occurred.  Any
    role/type/level difference, unknown actual user, expected user other than
    system_u, or malformed four-field context blocks.
    """
    if len(contexts) != len(CONTEXT_PATHS):
        raise RefusalError("installed-policy/actual context inventory incomplete")
    for path, (expected, actual) in zip(CONTEXT_PATHS, contexts, strict=True):
        expected_user, expected_role, expected_type, expected_level = split_context(expected)
        actual_user, actual_role, actual_type, actual_level = split_context(actual)
        if path in STAGED_CONTEXT_PATHS:
            if (expected_user != STAGED_EXPECTED_USER or actual_user not in STAGED_ACCEPTED_USERS
                    or (expected_role, expected_type, expected_level)
                    != (actual_role, actual_type, actual_level)):
                raise RefusalError("task-staged context not equivalent to installed policy")
        elif expected != actual:
            raise RefusalError("redirect-root context differs from installed policy")


def validate_evidence(evidence: Evidence, identity: tuple[int, int]) -> None:
    keys = [line.partition("=")[0] for line in evidence.properties]
    if len(set(keys)) != len(keys) or any(key not in MANAGER_KEYS for key in keys) or not set(PROPERTIES).issubset(evidence.properties):
        raise RefusalError("contradictory/missing effective mandatory isolation property")
    if identity[0] <= 0 or identity[1] <= 0 or (evidence.uid, evidence.gid) != identity:
        raise RefusalError("execution identity not independently observed")
    if evidence.pid <= 0 or evidence.exit_code != 0:
        raise RefusalError("execution failed or PID unknown")
    validate_contexts(evidence.contexts)
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
    try:
        start, end = (datetime.strptime(window[index], "%m/%d/%Y %H:%M:%S") for index in (2, 3))
    except ValueError as error:
        raise RefusalError("audit window is not a valid wall-clock bound") from error
    if not 0 <= (end - start).total_seconds() <= 120:
        raise RefusalError("audit window invalid/unbounded")
    # The harness exports LC_ALL=C.  ausearch parses absolute dates through
    # strptime(3) %x, which rejects the retained %m/%d/%Y wall-clock form
    # ("Error parsing start date (10/18/2026)"); the query therefore uses the
    # locale-independent recent/now keywords.  The audit-window above still
    # records and validates wall-clock bounds, PID attribution and the
    # <=120 s execution window, so the raw evidence chain stays intact.
    # No-match capture shapes (stderr is merged into the capture): audit 4.2.1
    # (Fedora 44) `ausearch --raw` prints nothing and exits 1; older or
    # non-raw shapes print "<no matches>" and exit 1.  Real failures against
    # the exact command tuple below ("Error parsing start date", usage
    # errors) always write non-empty text, so the empty capture is
    # unambiguous only together with that command tuple and the validated
    # window/PID bounds.
    command = ("/usr/sbin/ausearch", "-m", "AVC,USER_AVC", "-p", str(pid), "-ts",
               "recent", "-te", "now", "--raw")
    if tuple(raw("audit-command").splitlines()) != command:
        raise RefusalError("audit query attribution/window not independently retained")
    query = raw("audit-query")
    if query not in ("<no matches>\n", "") or raw("audit-query.exit") != "1\n":
        raise RefusalError("raw audit query contains AVC, error, unknown output or exit status")
    if raw("audit-known") != "yes\n" or raw("avc") != "":
        raise RefusalError("derived audit markers disagree with raw evidence")


def contexts_record(directory: Path) -> tuple[tuple[str, str], ...]:
    rows = [line.split("\t") for line in read_raw(directory / "contexts-paths").splitlines()]
    if len(rows) != len(CONTEXT_PATHS) or any(len(row) != 3 for row in rows):
        raise RefusalError("incomplete path-bearing context inventory")
    if tuple(row[0] for row in rows) != CONTEXT_PATHS:
        raise RefusalError("context paths do not match exact probe surface")
    # CONTEXT_PATHS order is the contract validate_contexts relies on:
    # first three redirect roots, last two task-staged files.
    pairs = tuple((row[1], row[2]) for row in rows)
    if read_raw(directory / "contexts") != "".join(f"{expected}\t{actual}\n" for expected, actual in pairs):
        raise RefusalError("context summary contradicts raw path observations")
    return pairs


def summarize_raw(directory: Path, binding: Callable[[Path], tuple[int, int]]) -> None:
    failures: list[str] = []
    legacy: Literal["supported", "unsupported"] | None = None
    try:
        identity = binding(directory)
        legacy = legacy_assumption(directory, identity)
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
              "legacy_assumption": legacy,
              "release_acceptance": False, "scope": "Task 3 only; independent guest verification required"}
    _ = (directory / "probe.json").write_text(json.dumps(result, indent=2) + "\n")
    if failures:
        raise RefusalError("required raw evidence incomplete; see probe.json")
