from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from qa.qa_v020_inventory import collect, validate_required_environment


def _repo(tmp_path: Path, *, environment: bool = True, titles: bool = True) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "qa@example.test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "QA"], check=True)
    (root / "payload.txt").write_text("candidate\n", encoding="utf-8")
    evidence = root / "E"
    evidence.mkdir()
    if environment:
        (evidence / "qa-environment.json").write_text('["authorized-vm"]\n', encoding="utf-8")
    if titles:
        (evidence / "test-titles.json").write_text('["title-a"]\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "payload.txt", "E"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "candidate"], check=True)
    sha = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return root, sha


def test_accepts_candidate(tmp_path: Path) -> None:
    root, sha = _repo(tmp_path)
    output = tmp_path / "out"
    result = collect(root, output, sha)
    saved = json.loads((output / "baseline.json").read_text(encoding="utf-8"))
    assert result["candidate_sha"] == sha
    assert saved["available_disposable_envs"] == ["authorized-vm"]
    assert saved["test_titles"] == ["title-a"]
    assert saved["dirty_paths"] == ""
    validate_required_environment(result)


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("wrong-sha", "candidate SHA mismatch"),
        ("dirty-payload", "dirty candidate paths"),
        ("missing-environment", "missing required disposable environment"),
        ("missing-titles", "missing required test titles"),
    ],
)
def test_rejects_mismatch(tmp_path: Path, case: str, expected: str) -> None:
    root, sha = _repo(
        tmp_path,
        environment=case != "missing-environment",
        titles=case != "missing-titles",
    )
    candidate = sha
    if case == "wrong-sha":
        candidate = "0" * 40
    if case == "dirty-payload":
        (root / "payload.txt").write_text("tampered\n", encoding="utf-8")
    output = tmp_path / "out"
    if case in {"wrong-sha", "dirty-payload"}:
        with pytest.raises(ValueError, match=expected):
            collect(root, output, candidate)
    else:
        result = collect(root, output, candidate)
        with pytest.raises(ValueError, match=expected):
            validate_required_environment(result)
