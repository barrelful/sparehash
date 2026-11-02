# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors
"""Build, stamp, sign and verify one clean exact-tag release. Keys stay local."""
from __future__ import annotations

import argparse
import ast
import io
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sparehash.app_release import verify_archive  # noqa: E402


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--sign", required=True, help="full primary fingerprint in your local keyring")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if re.fullmatch(r"v\d+\.\d+\.\d+", args.tag) is None:
        parser.error("expected exact vMAJOR.MINOR.PATCH tag")
    if re.fullmatch(r"[0-9A-F]{40}", args.sign) is None:
        parser.error("--sign requires the full uppercase primary fingerprint")
    commit = git("rev-parse", f"{args.tag}^{{commit}}")
    if git("status", "--porcelain", "--untracked-files=normal") or git("rev-parse", "HEAD") != commit:
        parser.error("build requires a clean checkout at the exact selected tag")
    version_tree = ast.parse(git("show", f"{commit}:sparehash/__init__.py"))
    versions = [ast.literal_eval(node.value) for node in version_tree.body
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__"
                                                       for t in node.targets)]
    if versions != [args.tag[1:]]:
        parser.error("tag does not match authoritative package version")
    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output / f"sparehash-{versions[0]}-source.tar.gz"
    signature = Path(str(archive) + ".asc")
    if archive.exists() or signature.exists():
        parser.error("refusing to overwrite an existing release artifact")
    raw = subprocess.check_output(["git", "-C", str(ROOT), "archive", "--format=tar", commit])
    with tarfile.open(fileobj=io.BytesIO(raw)) as source, tarfile.open(archive, "w:gz") as target:
        for member in source:
            if member.name == "sparehash/_build.py":
                continue
            target.addfile(member, source.extractfile(member) if member.isfile() else None)
        stamp = f'# Generated from exact clean source commit.\nCOMMIT = "{commit}"\n'.encode()
        info = tarfile.TarInfo("sparehash/_build.py")
        info.size, info.mode = len(stamp), 0o644
        target.addfile(info, io.BytesIO(stamp))
    subprocess.run(["gpg", "--batch", "--armor", "--local-user", args.sign, "--output", str(signature),
                    "--detach-sign", str(archive)], check=True, timeout=120)
    key = subprocess.check_output(["gpg", "--batch", "--armor", "--export", args.sign], timeout=30)
    verify_archive(archive, signature, key=key, fingerprint=args.sign)
    with tempfile.TemporaryDirectory(prefix="sparehash-build-") as directory:
        # This is our own just-built, verified git archive, not an untrusted update.
        with tarfile.open(archive) as source:
            source.extractall(directory, filter="data")
        subprocess.run(["uv", "build", "--out-dir", str(args.output.absolute()), directory],
                       check=True, timeout=120)
    print(f"Verified {archive.name} and {signature.name}; source commit {commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
