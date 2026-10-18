"""Local contract tests; these are not enforcing-distro acceptance evidence."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Final

import pytest

from qa.atomic_probe import (
    Evidence,
    Facts,
    Json,
    Manifest,
    RefusalError,
    authorize,
    protected_read,
    read_manifest,
    summarize,
    validate_evidence,
)
from qa.atomic_probe_evidence import CONTEXT_PATHS, legacy_assumption

ISOLATION: Final = ("ProtectSystem=strict", "ProtectHome=yes", "PrivateNetwork=yes",
                    "NoNewPrivileges=yes", "PrivateTmp=yes")
# Exact auditctl -s output from the 2026-10-18 enforcing Bazzite Task-3 run.
AUDIT_CTL: Final = ("enabled 1\nfailure 1\npid 785\nrate_limit 0\nbacklog_limit 64\n"
                    "lost 0\nbacklog 0\nbacklog_wait_time 60000\nbacklog_wait_time_actual 0\n"
                    "loginuid_immutable 0 unlocked\n")
CONTEXTS: Final = (
    ("system_u:object_r:usr_t:s0", "system_u:object_r:usr_t:s0"),
    ("system_u:object_r:usr_t:s0", "system_u:object_r:usr_t:s0"),
    ("system_u:object_r:bin_t:s0", "system_u:object_r:bin_t:s0"),
    ("system_u:object_r:usr_t:s0", "system_u:object_r:usr_t:s0"),
    ("system_u:object_r:usr_t:s0", "system_u:object_r:usr_t:s0"),
)


def legacy_properties(*, status: str = "216", pid: str = "2483", user: str = "99",
                      group: str = "nogroup") -> str:
    """Exact legacy manager record shape; the guest observed 216/nogroup/99/1."""
    return (f"ExecMainPID={pid}\nExecMainStatus={status}\nUser={user}\nGroup={group}\n"
            "PrivateTmp=yes\nPrivateNetwork=yes\nProtectHome=yes\n"
            "ProtectSystem=strict\nNoNewPrivileges=yes\n")


LEGACY_PROPERTIES: Final = legacy_properties()


def write_legacy(directory: Path, *, properties: str | None = None, exit_code: str = "1\n",
                 output: str = "") -> None:
    _ = (directory / "legacy.properties").write_text(
        LEGACY_PROPERTIES if properties is None else properties)
    _ = (directory / "legacy.exit").write_text(exit_code)
    _ = (directory / "legacy.output").write_text(output)


@pytest.fixture
def manifest() -> Manifest:
    return Manifest("disposable-vm", "atomic-task3-nonmining", "a" * 32, "1:2",
                    "b" * 40, "c" * 64, "/var/tmp/offline/xmrig", "d" * 64)


@pytest.fixture
def facts() -> Facts:
    return Facts("a" * 32, "1:2", "b" * 40, "c" * 64, "d" * 64,
                 "Enforcing", 99, 99, "nobody", True)


@pytest.mark.parametrize("change", [
    {"kind": "production"}, {"surface": "atomic"}, {"surface": "unknown"},
    {"machine_id": "f" * 32}, {"root_identity": "2:3"},
    {"candidate_sha": "f" * 40}, {"source_sha256": "f" * 64},
    {"executable_sha256": "f" * 64},
])
def test_refuses_unsafe_manifest(manifest: Manifest, facts: Facts, change: dict[str, str]) -> None:
    # Given
    unsafe = replace(manifest, **change)
    # When / Then: pure authorization has no mutation seam.
    with pytest.raises(RefusalError):
        authorize(unsafe, facts)


FACT_CHANGES: Final[tuple[Callable[[Facts], Facts], ...]] = (
    lambda item: replace(item, selinux="Permissive"),
    lambda item: replace(item, uid=0), lambda item: replace(item, gid=0),
    lambda item: replace(item, group=""), lambda item: replace(item, group="wheel"),
    lambda item: replace(item, guest=False),
)


@pytest.mark.parametrize("change", FACT_CHANGES)
def test_refuses_unsafe_probe(manifest: Manifest, facts: Facts,
                              change: Callable[[Facts], Facts]) -> None:
    # Given
    unsafe = change(facts)
    # When / Then
    with pytest.raises(RefusalError):
        authorize(manifest, unsafe)


@pytest.fixture
def evidence() -> Evidence:
    return Evidence(ISOLATION, 65534, 65534, 42, 0, CONTEXTS, True, "", True)


def test_accepts_enforcing_probe(evidence: Evidence) -> None:
    # Given: synthetic observations, never a claim about the current host.
    # When / Then
    validate_evidence(evidence, (65534, 65534))


EVIDENCE_CHANGES: Final[tuple[Callable[[Evidence], Evidence], ...]] = (
    lambda item: replace(item, properties=("ProtectSystem=strict",)),
    lambda item: replace(item, uid=0), lambda item: replace(item, gid=0),
    lambda item: replace(item, pid=0), lambda item: replace(item, exit_code=1),
    lambda item: replace(item, contexts=()),
    lambda item: replace(item, contexts=(("usr_t", "default_t"),)),
    lambda item: replace(item, audit_known=False),
    lambda item: replace(item, avc="type=AVC msg=audit(1): denied"),
    lambda item: replace(item, cleaned=False),
)


@pytest.mark.parametrize("change", EVIDENCE_CHANGES)
def test_blocks_incomplete_evidence(evidence: Evidence,
                                    change: Callable[[Evidence], Evidence]) -> None:
    # Given
    incomplete = change(evidence)
    # When / Then
    with pytest.raises(RefusalError):
        validate_evidence(incomplete, (65534, 65534))


def test_legacy_accepts_nogroup_unavailable_observation(tmp_path: Path) -> None:
    # Given: exact guest evidence; Bazzite has no legacy nogroup group, so
    # systemd fails before exec with ExecMainStatus=216 (EXIT_GROUP).
    write_legacy(tmp_path)
    # When / Then: an observation, not a harness failure.
    assert legacy_assumption(tmp_path, (99, 99)) == "unsupported"


def test_legacy_accepts_supported_execution(tmp_path: Path) -> None:
    # Given: a platform where Group=nogroup exists; the sandbox runs and its
    # first output line is the wrapper identity.
    write_legacy(tmp_path, properties=legacy_properties(status="0"), exit_code="0\n",
                 output="99 65534 2483\nXMRig fixture\n")
    # When / Then
    assert legacy_assumption(tmp_path, (99, 99)) == "supported"


LEGACY_BLOCKED: Final = (
    pytest.param({"output": "Failed to set group: nogroup\n"}, id="216-nonempty-output"),
    pytest.param({"output": "99 65534 2483\n"}, id="216-identity-output"),
    pytest.param({"exit_code": "0\n"}, id="216-zero-exit"),
    pytest.param({"properties": legacy_properties(user="98")}, id="user-not-identity"),
    pytest.param({"properties": legacy_properties(user="0")}, id="user-zero"),
    pytest.param({"properties": legacy_properties(user="099")}, id="user-noncanonical"),
    pytest.param({"properties": legacy_properties(status="217")}, id="status-217"),
    pytest.param({"properties": legacy_properties(status="1")}, id="status-1"),
    pytest.param({"properties": legacy_properties(status="")}, id="status-empty"),
    pytest.param({"properties": legacy_properties(group="99")}, id="group-numeric"),
    pytest.param({"properties": legacy_properties().replace("Group=nogroup\n", "")},
        id="group-missing"),
    pytest.param({"properties": legacy_properties() + "User=99\n"}, id="duplicate-property"),
    pytest.param({"properties": legacy_properties() + "Extra=yes\n"}, id="unknown-property"),
    pytest.param({"properties": legacy_properties().replace("PrivateNetwork=yes\n", "")},
        id="missing-isolation-property"),
    pytest.param({"exit_code": "signal\n"}, id="exit-malformed"),
    pytest.param({"exit_code": "-1\n"}, id="exit-negative"),
    pytest.param({"properties": legacy_properties(status="0"), "exit_code": "1\n",
                  "output": "99 99 2483\n"}, id="supported-nonzero-exit"),
    pytest.param({"properties": legacy_properties(status="0"), "exit_code": "0\n"},
        id="supported-empty-output"),
    pytest.param({"properties": legacy_properties(status="0"), "exit_code": "0\n",
                  "output": "99 0 2483\n"}, id="supported-zero-gid"),
    pytest.param({"properties": legacy_properties(status="0"), "exit_code": "0\n",
                  "output": "99 99 0\n"}, id="supported-zero-pid"),
    pytest.param({"properties": legacy_properties(status="0"), "exit_code": "0\n",
                  "output": "99 99 9999\n"}, id="supported-pid-mismatch"),
    pytest.param({"properties": legacy_properties(status="0"), "exit_code": "0\n",
                  "output": "98 99 2483\n"}, id="supported-uid-mismatch"),
    pytest.param({"properties": legacy_properties(status="0"), "exit_code": "0\n",
                  "output": "99-99-2483\n"}, id="supported-malformed-line"),
)


@pytest.mark.parametrize("write", LEGACY_BLOCKED)
def test_legacy_blocks_undocumented_outcomes(tmp_path: Path, write: dict[str, str]) -> None:
    # Given: a legacy record that is not one of the two documented outcomes.
    write_legacy(tmp_path, **write)
    # When / Then: fail closed.
    with pytest.raises(RefusalError):
        _ = legacy_assumption(tmp_path, (99, 99))


@pytest.mark.parametrize("opt_in", [False, True])
def test_entrypoint_refuses_before_effects(tmp_path: Path, opt_in: bool) -> None:
    # Given: invalid manifest, unchanged sentinel, and systemctl tripwire.
    sentinel = tmp_path / "sentinel"
    _ = sentinel.write_text("untouched")
    tripwire = tmp_path / "systemctl"
    _ = tripwire.write_text(f"#!/bin/sh\nprintf mutation > '{sentinel}'\n")
    tripwire.chmod(0o755)
    environment = {key: value for key, value in os.environ.items()
                   if key != "SPAREHASH_QA_DISPOSABLE_VM"}
    environment.update(PATH=f"{tmp_path}:/usr/bin:/bin", EVIDENCE_DIR=str(tmp_path / "evidence"))
    if opt_in:
        environment["SPAREHASH_QA_DISPOSABLE_VM"] = "YES"
    # When
    result = subprocess.run(["bash", "qa/qa_atomic_probe.sh", "--environment-manifest",
                             str(tmp_path / "absent.json")], env=environment,
                            capture_output=True, text=True, timeout=5, check=False)
    # Then
    assert result.returncode != 0
    assert "BLOCKED" in result.stderr
    assert sentinel.read_text() == "untouched"
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize("payload", [b"[]", b'{"kind":"disposable-vm"}',
                                    b'{"kind":"production","kind":"disposable-vm"}'])
def test_manifest_rejects_incomplete_or_ambiguous_schema(payload: bytes) -> None:
    # Given / When / Then
    with pytest.raises(RefusalError):
        _ = read_manifest(Path("/unused"), reader=lambda _: payload)


def test_manifest_parses_exact_authority_fields(manifest: Manifest) -> None:
    # Given
    payload = json.dumps(asdict(manifest)).encode()
    # When
    actual = read_manifest(Path("/unused"), reader=lambda _: payload)
    # Then
    assert actual == manifest


def test_manifest_refuses_symlink_before_read(tmp_path: Path) -> None:
    # Given
    link = tmp_path / "manifest"
    link.symlink_to("/etc/passwd")
    # When / Then
    with pytest.raises((OSError, RefusalError)):
        _ = protected_read(link)


@pytest.fixture
def raw_probe(tmp_path: Path, manifest: Manifest, facts: Facts) -> Path:
    contexts_paths = "".join(f"{path}\t{expected}\t{actual}\n"
                             for path, (expected, actual) in zip(CONTEXT_PATHS, CONTEXTS, strict=True))
    records = {
        "identity": f"{facts.uid} {facts.gid}\n", "cleanup": "removed-owned-staging-and-units\n",
        "contexts": "".join(f"{expected}\t{actual}\n" for expected, actual in CONTEXTS),
        "contexts-paths": contexts_paths,
        "candidate.sha": manifest.candidate_sha + "\n",
        "authorization.json": json.dumps({"manifest": asdict(manifest), "facts": asdict(facts),
                                            "helpers": ["/usr/bin/systemctl"]}),
        # Exact 2026-10-18 guest values: nogroup is absent on Bazzite, so the
        # legacy unit dies before exec with EXIT_GROUP and no output.
        "legacy.properties": LEGACY_PROPERTIES,
        "legacy.exit": "1\n", "legacy.output": "",
    }
    # Exact guest PIDs and wall-clock windows for the two executable modes.
    for mode, pid, end in (("version", 2496, "14:45:48"), ("dry-run", 2519, "14:45:49")):
        records.update({
            f"{mode}.properties": f"ExecMainPID={pid}\nExecMainStatus=0\nUser=99\nGroup=99\n" +
                "PrivateTmp=yes\nPrivateNetwork=yes\nProtectHome=yes\n" +
                "ProtectSystem=strict\nNoNewPrivileges=yes\n",
            f"{mode}.output": f"99 99 {pid}\n" + ("XMRig fixture\n" if mode == "version" else ""),
            f"{mode}.exit": "0\n", f"{mode}.audit-known": "yes\n", f"{mode}.avc": "",
            f"{mode}.properties.exit": "0\n",
            f"{mode}.audit-before": AUDIT_CTL, f"{mode}.audit-after": AUDIT_CTL,
            f"{mode}.audit-before.exit": "0\n", f"{mode}.audit-after.exit": "0\n",
            f"{mode}.audit-query": "<no matches>\n", f"{mode}.audit-query.exit": "1\n",
            f"{mode}.audit-window": f"pid={pid}\nstart=10/18/2026 14:45:48\nend=10/18/2026 {end}\n",
            # Exact guest command shape (absolute dates); blocker 2 replaces it.
            f"{mode}.audit-command": (f"/usr/sbin/ausearch\n-m\nAVC,USER_AVC\n-p\n{pid}\n"
                                      f"-ts\n10/18/2026\n14:45:48\n-te\n10/18/2026\n{end}\n--raw\n"),
        })
    for name, value in records.items():
        _ = (tmp_path / name).write_text(value)
    return tmp_path


@pytest.mark.parametrize("tamper", ["none", "manager", "property", "audit", "context", "cleanup",
    "duplicate-property", "duplicate-identity", "malformed-manager", "unknown-manager",
    "raw-disabled", "raw-loss", "raw-backlog", "raw-avc", "query-error", "query-rc",
    "raw-missing", "audit-status-error", "audit-duplicate", "pid-window", "query-command",
    "context-path", "authorization", "candidate", "reviewer-counterexample",
    "legacy-status", "legacy-output", "legacy-group", "legacy-property", "legacy-duplicate",
    "legacy-exit", "legacy-identity", "legacy-missing"])
def test_summary_requires_raw_observations(raw_probe: Path, tamper: str,
                                          decode: Callable[[str], Json] = json.loads) -> None:
    # Given: synthetic raw records, never enforcing-distro acceptance.
    tmp_path = raw_probe
    records = {path.name: path.read_text() for path in tmp_path.iterdir()}
    if tamper == "manager":
        records["version.properties"] = records["version.properties"].replace("User=99", "User=0")
    if tamper == "property":
        records["version.properties"] = records["version.properties"].replace("PrivateNetwork=yes\n", "")
    if tamper == "audit":
        del records["version.audit-known"]
    if tamper == "context":
        records["contexts"] = records["contexts"].splitlines()[0] + "\n"
    if tamper == "cleanup":
        del records["cleanup"]
    replacements = {
        "duplicate-property": ("version.properties", records["version.properties"] + "PrivateNetwork=no\n"),
        "duplicate-identity": ("version.properties", "User=0\n" + records["version.properties"]),
        "malformed-manager": ("version.properties", records["version.properties"] + "User\n"),
        "unknown-manager": ("version.properties", records["version.properties"] + "Unknown=yes\n"),
        "raw-disabled": ("version.audit-before", "enabled 0\npid 17\nlost 0\nbacklog 0\n"),
        "raw-loss": ("version.audit-after", "enabled 1\npid 17\nlost 99\nbacklog 0\n"),
        "raw-backlog": ("version.audit-after", "enabled 1\npid 17\nlost 0\nbacklog 5\n"),
        "raw-avc": ("version.audit-query", 'type=AVC msg=audit(1.1:1): avc: denied { execute } for pid=42 comm="xmrig"\n'),
        "query-error": ("version.audit-query", "Error opening audit log\n"),
        "query-rc": ("version.audit-query.exit", "124\n"),
        "audit-status-error": ("version.audit-before.exit", "1\n"),
        "audit-duplicate": ("version.audit-before", "enabled 0\nenabled 1\npid 17\nlost 0\nbacklog 0\n"),
        "pid-window": ("version.audit-window", records["version.audit-window"].replace("pid=2496", "pid=99")),
        "query-command": ("version.audit-command", records["version.audit-command"].replace("2496\n", "99\n")),
        "context-path": ("contexts-paths", records["contexts-paths"].replace("/var/opt\t", "/foreign\t")),
        "candidate": ("candidate.sha", "f" * 40 + "\n"),
        "legacy-status": ("legacy.properties", LEGACY_PROPERTIES.replace("ExecMainStatus=216", "ExecMainStatus=217")),
        "legacy-output": ("legacy.output", "Failed to set group: nogroup\n"),
        "legacy-group": ("legacy.properties", LEGACY_PROPERTIES.replace("Group=nogroup", "Group=99")),
        "legacy-property": ("legacy.properties", LEGACY_PROPERTIES.replace("PrivateNetwork=yes\n", "")),
        "legacy-duplicate": ("legacy.properties", LEGACY_PROPERTIES + "User=99\n"),
        "legacy-exit": ("legacy.exit", "0\n"),
        "legacy-identity": ("legacy.properties", LEGACY_PROPERTIES.replace("User=99", "User=98")),
    }
    if tamper in replacements:
        name, value = replacements[tamper]
        records[name] = value
    if tamper == "raw-missing":
        del records["version.audit-query"]
    if tamper == "legacy-missing":
        del records["legacy.properties"]
    if tamper == "authorization":
        del records["authorization.json"]
    if tamper == "reviewer-counterexample":
        records["version.properties"] = "PrivateNetwork=no\n" + records["version.properties"]
        records["version.audit-before"] = "enabled 0\nlost 99\nbacklog 100\n"
        records["version.audit-after"] = "enabled 0\nlost 100\nbacklog 100\n"
        records["version.audit-query"] = replacements["raw-avc"][1]
    for path in tmp_path.iterdir():
        if path.name not in records:
            path.unlink()
        else:
            _ = path.write_text(records[path.name])
    # When
    if tamper == "none":
        summarize(tmp_path)
    else:
        with pytest.raises(RefusalError):
            summarize(tmp_path)
    # Then
    result = decode((tmp_path / "probe.json").read_text())
    assert isinstance(result, dict)
    assert result["status"] == ("FEASIBLE" if tamper == "none" else "BLOCKED")
    assert result["release_acceptance"] is False
    if tamper == "none":
        # Guest-observed but non-failing: the legacy assumption is simply
        # unsupported on platforms without the nogroup group.
        assert result["legacy_assumption"] == "unsupported"


def test_summary_records_supported_legacy_assumption(raw_probe: Path) -> None:
    # Given: a platform where the legacy nogroup comparison executes.
    _ = (raw_probe / "legacy.properties").write_text(legacy_properties(status="0"))
    _ = (raw_probe / "legacy.exit").write_text("0\n")
    _ = (raw_probe / "legacy.output").write_text("99 65534 2483\nXMRig fixture\n")
    # When
    summarize(raw_probe)
    # Then
    result = json.loads((raw_probe / "probe.json").read_text())
    assert result["status"] == "FEASIBLE"
    assert result["legacy_assumption"] == "supported"


@pytest.mark.parametrize("mode", ["version", "dry-run"])
@pytest.mark.parametrize("suffix,raw", [
    ("properties", "PrivateNetwork=no\n"),
    ("audit-before", "enabled 0\npid 17\nlost 0\nbacklog 0\n"),
    ("audit-after", "enabled 1\npid 17\nlost 1\nbacklog 0\n"),
    ("audit-query", 'type=AVC msg=audit(1): avc: denied { execute } for pid=42\n'),
    ("audit-query.exit", "0\n"),
])
def test_both_runs_reject_raw_marker_contradictions(raw_probe: Path, mode: str,
                                                   suffix: str, raw: str) -> None:
    # Positive derived markers cannot overrule either run's retained raw facts.
    path = raw_probe / f"{mode}.{suffix}"
    if suffix == "properties":
        raw += path.read_text()
    _ = path.write_text(raw)
    with pytest.raises(RefusalError):
        summarize(raw_probe)
    assert '"status": "BLOCKED"' in (raw_probe / "probe.json").read_text()
