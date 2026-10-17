from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import TypedDict


class Inventory(TypedDict):
    candidate_sha: str
    dirty_paths: str
    python_version: str
    python_executable: str
    os_release: dict[str, str]
    wheel_metadata: list[dict[str, str]]
    artifact_inventory: list[str]
    source_inventory: list[str]
    available_disposable_envs: list[str] | None
    test_titles: list[str] | None


def _run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, check=True, text=True
    )
    return result.stdout.strip()


def _os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key in {"ID", "ID_LIKE", "VERSION_ID"}:
                values[key] = value.strip().strip('"')
    except OSError:
        pass
    return values


def _optional_json(path: Path) -> list[str] | None:
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
        return parsed
    return None


def collect(root: Path, out_dir: Path, candidate_sha: str) -> Inventory:
    actual_sha = _run_git(root, "rev-parse", "HEAD")
    if actual_sha != candidate_sha:
        raise ValueError(f"candidate SHA mismatch: expected {candidate_sha}, got {actual_sha}")
    dirty_paths = _run_git(root, "status", "--short")
    artifact_files = sorted(
        str(path.relative_to(root))
        for pattern in ("dist/*.whl", "dist/*.tar.gz")
        for path in root.glob(pattern)
    )
    source_files = sorted(
        str(path.relative_to(root))
        for base in ("sparehash", "systemd")
        for path in (root / base).rglob("*")
        if path.is_file() and ".pyc" not in path.name and "__pycache__" not in path.parts
    )
    metadata: list[dict[str, str]] = []
    for wheel in (root / "dist").glob("*.whl") if (root / "dist").exists() else ():
        with zipfile.ZipFile(wheel) as archive:
            metadata_file = next(
                name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
            )
            metadata.append(
                {"file": str(wheel.relative_to(root)), "metadata": archive.read(metadata_file).decode()}
            )

    environment = _optional_json(root / "E/qa-environment.json")
    titles = _optional_json(root / "E/test-titles.json")
    disposable = environment
    test_titles = titles
    if any(not line[3:].startswith("E/") for line in dirty_paths.splitlines()):
        raise ValueError(f"dirty candidate paths: {dirty_paths}")
    inventory: Inventory = {
        "candidate_sha": actual_sha,
        "dirty_paths": dirty_paths,
        "python_version": sys.version,
        "python_executable": sys.executable,
        "os_release": _os_release(),
        "wheel_metadata": metadata,
        "artifact_inventory": artifact_files,
        "source_inventory": source_files,
        "available_disposable_envs": disposable,
        "test_titles": test_titles,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    _ = (out_dir / "baseline.json").write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return inventory


def validate_required_environment(inventory: Inventory) -> None:
    if not inventory["available_disposable_envs"]:
        raise ValueError("missing required disposable environment")
    if not inventory["test_titles"]:
        raise ValueError("missing required test titles")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    out_dir: Path = args.out_dir
    candidate_sha: str = args.candidate_sha
    root: Path = args.root
    try:
        result = collect(root.resolve(), out_dir.resolve(), candidate_sha)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"inventory failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
