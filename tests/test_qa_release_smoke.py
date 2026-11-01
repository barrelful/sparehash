"""Release smoke safety guard; never invoke destructive VM actions in unit tests."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_release_smoke_refuses_when_disposable_vm_opt_in_missing(tmp_path: Path) -> None:
    # Given
    script = Path(__file__).resolve().parents[1] / "qa/qa_release_smoke.sh"
    evidence = tmp_path / "evidence"
    environment = {key: value for key, value in os.environ.items()
                   if key != "SPAREHASH_QA_DISPOSABLE_VM"}
    environment["EVIDENCE_DIR"] = str(evidence)
    # When
    result = subprocess.run(["bash", str(script)], env=environment,
                            capture_output=True, text=True, timeout=5, check=False)
    # Then
    assert result.returncode != 0
    assert "SPAREHASH_QA_DISPOSABLE_VM=YES" in result.stderr
    assert not evidence.exists()


@pytest.mark.parametrize("extra,needle", [
    ([], "--yes"), (["--yes"], "--source-sha256"),
    (["--yes", "--source-sha256", "0" * 64], "source SHA-256 mismatch"),
])
def test_release_smoke_refuses_before_evidence_or_host_effects(tmp_path: Path, extra: list[str], needle: str) -> None:
    script = Path(__file__).resolve().parents[1] / "qa/qa_release_smoke.sh"
    evidence = tmp_path / "must-not-exist"
    result = subprocess.run(["bash", str(script), "--evidence", str(evidence), *extra],
                            env={**os.environ, "SPAREHASH_QA_DISPOSABLE_VM": "YES"},
                            capture_output=True, text=True, timeout=5)
    assert result.returncode != 0 and needle in result.stderr
    assert not evidence.exists()


def test_source_digest_is_deterministic_and_detects_modified_payload(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("release_smoke", source / "qa/release_smoke.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    digest = module.source_sha256()
    result = subprocess.run([sys.executable, str(source / "qa/release_smoke.py"), "--print-source-sha256"],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0 and result.stdout.strip() == digest
    # A source inventory is the identity, not an unverified candidate.sha label.
    import shutil
    for name in ("sparehash", "systemd", "qa"):
        shutil.copytree(source / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "install.sh", "pyproject.toml"):
        shutil.copyfile(source / name, tmp_path / name)
    assert module.source_sha256(tmp_path) == digest
    with (tmp_path / "sparehash/gaming_scan.py").open("a") as stream:
        stream.write("\n# changed fixture\n")
    assert module.source_sha256(tmp_path) != digest


def test_wrapper_does_not_execute_helpers_from_caller_path(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "qa/qa_release_smoke.sh"
    marker = tmp_path / "hostile-helper-ran"
    helper = tmp_path / "realpath"
    helper.write_text(f"#!/bin/sh\nprintf ran > '{marker}'\n")
    helper.chmod(0o755)
    result = subprocess.run(["/bin/bash", str(script)],
                            env={"PATH": str(tmp_path), "SPAREHASH_QA_DISPOSABLE_VM": "YES"},
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 2 and "--yes" in result.stderr
    assert not marker.exists()
