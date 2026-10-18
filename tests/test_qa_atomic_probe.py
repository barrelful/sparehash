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


@pytest.fixture
def manifest() -> Manifest:
    return Manifest("disposable-vm", "atomic-task3-nonmining", "a" * 32, "1:2",
                    "b" * 40, "c" * 64, "/var/tmp/offline/xmrig", "d" * 64)


@pytest.fixture
def facts() -> Facts:
    return Facts("a" * 32, "1:2", "b" * 40, "c" * 64, "d" * 64,
                 "Enforcing", 65534, 65534, "nobody", True)


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
    return Evidence(("ProtectSystem=strict", "ProtectHome=yes", "PrivateNetwork=yes",
                     "NoNewPrivileges=yes", "PrivateTmp=yes"),
                    65534, 65534, 42, 0, (("system_u:object_r:usr_t:s0", "system_u:object_r:usr_t:s0"),), True, "", True)


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
    context = "system_u:object_r:usr_t:s0"
    paths = ("/var/opt", "/var/usrlocal", "/var/usrlocal/bin",
             "/var/opt/xmrig/task3/xmrig", "/var/opt/xmrig/task3/config.json")
    records = {
        "identity": "65534 65534\n", "cleanup": "removed-owned-staging-and-units\n",
        "contexts": f"{context}\t{context}\n" * 5,
        "contexts-paths": "".join(f"{path}\t{context}\t{context}\n" for path in paths),
        "candidate.sha": manifest.candidate_sha + "\n",
        "authorization.json": json.dumps({"manifest": asdict(manifest), "facts": asdict(facts),
                                            "helpers": ["/usr/bin/systemctl"]}),
        "legacy.exit": "1\n", "legacy.output": "Failed to resolve group nogroup\n",
    }
    for mode in ("version", "dry-run"):
        records.update({
            f"{mode}.properties": "ProtectSystem=strict\nProtectHome=yes\nPrivateNetwork=yes\n" +
                "NoNewPrivileges=yes\nPrivateTmp=yes\nUser=65534\nGroup=65534\nExecMainPID=42\nExecMainStatus=0\n",
            f"{mode}.output": "65534 65534 42\nXMRig fixture\n",
            f"{mode}.exit": "0\n", f"{mode}.audit-known": "yes\n", f"{mode}.avc": "",
            f"{mode}.properties.exit": "0\n",
            f"{mode}.audit-before": "enabled 1\npid 17\nlost 0\nbacklog 0\n",
            f"{mode}.audit-after": "enabled 1\npid 17\nlost 0\nbacklog 0\n",
            f"{mode}.audit-before.exit": "0\n", f"{mode}.audit-after.exit": "0\n",
            f"{mode}.audit-query": "<no matches>\n", f"{mode}.audit-query.exit": "1\n",
            f"{mode}.audit-window": "pid=42\nstart=10/18/2026 12:00:00\nend=10/18/2026 12:00:30\n",
            f"{mode}.audit-command": "/usr/sbin/ausearch\n-m\nAVC,USER_AVC\n-p\n42\n-ts\n10/18/2026\n12:00:00\n-te\n10/18/2026\n12:00:30\n--raw\n",
        })
    for name, value in records.items():
        _ = (tmp_path / name).write_text(value)
    return tmp_path


@pytest.mark.parametrize("tamper", ["none", "manager", "property", "audit", "context", "cleanup",
    "duplicate-property", "duplicate-identity", "malformed-manager", "unknown-manager",
    "raw-disabled", "raw-loss", "raw-backlog", "raw-avc", "query-error", "query-rc",
    "raw-missing", "audit-status-error", "audit-duplicate", "pid-window", "query-command",
    "context-path", "authorization", "candidate", "reviewer-counterexample"])
def test_summary_requires_raw_observations(raw_probe: Path, tamper: str,
                                          decode: Callable[[str], Json] = json.loads) -> None:
    # Given: synthetic raw records, never enforcing-distro acceptance.
    tmp_path = raw_probe
    records = {path.name: path.read_text() for path in tmp_path.iterdir()}
    if tamper == "manager":
        records["version.properties"] = records["version.properties"].replace("User=65534", "User=0")
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
        "pid-window": ("version.audit-window", records["version.audit-window"].replace("pid=42", "pid=99")),
        "query-command": ("version.audit-command", records["version.audit-command"].replace("42\n", "99\n")),
        "context-path": ("contexts-paths", records["contexts-paths"].replace("/var/opt\t", "/foreign\t")),
        "candidate": ("candidate.sha", "f" * 40 + "\n"),
    }
    if tamper in replacements:
        name, value = replacements[tamper]
        records[name] = value
    if tamper == "raw-missing":
        del records["version.audit-query"]
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
