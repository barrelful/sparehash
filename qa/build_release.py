# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors
"""Build, stamp, sign and verify one clean exact tag/commit. Keys stay local."""
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
from sparehash.app_release import APP_GPG_FINGERPRINT, verify_archive, version_tuple  # noqa: E402


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--tag", help="exact numeric release tag")
    selection.add_argument("--commit", help="full source SHA for pre-tag artifact preparation; creates no tag")
    parser.add_argument("--sign", required=True, help="full primary fingerprint in your local keyring")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if args.tag is not None and re.fullmatch(r"v\d+\.\d+\.\d+", args.tag) is None:
        parser.error("expected exact vMAJOR.MINOR.PATCH tag")
    if args.commit is not None and re.fullmatch(r"[0-9a-f]{40}", args.commit) is None:
        parser.error("--commit requires a full lowercase 40-character SHA, not a moving ref")
    if re.fullmatch(r"[0-9A-F]{40}", args.sign) is None:
        parser.error("--sign requires the full uppercase primary fingerprint")
    if args.sign != APP_GPG_FINGERPRINT:
        parser.error("--sign must match the installed application primary signer pin")
    commit = git("rev-parse", f"{args.tag or args.commit}^{{commit}}")
    if git("status", "--porcelain", "--untracked-files=normal") or git("rev-parse", "HEAD") != commit:
        parser.error("build requires a clean checkout at the exact selected tag/commit")
    version_tree = ast.parse(git("show", f"{commit}:sparehash/__init__.py"))
    versions = [ast.literal_eval(node.value) for node in version_tree.body
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__"
                                                       for t in node.targets)]
    if len(versions) != 1 or not isinstance(versions[0], str):
        parser.error("source must declare exactly one literal package version")
    version_tuple(versions[0])
    if args.tag is not None and versions != [args.tag[1:]]:
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
    with tempfile.TemporaryDirectory(prefix="sparehash-build-") as directory:
        # This is our own archive of the selected clean Git commit, not an
        # untrusted update. Prepare packages before any hardware interaction.
        with tarfile.open(archive) as source:
            source.extractall(directory, filter="data")
        subprocess.run(["uv", "build", "--out-dir", str(args.output.absolute()), directory],
                       check=True, timeout=120)
    try:
        subprocess.run(["gpg", "--batch", "--armor", "--local-user", args.sign, "--output", str(signature),
                        "--detach-sign", str(archive)], check=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        signature.unlink(missing_ok=True)
        print(f"Signing did not complete ({type(error).__name__}); prepared archive/packages retained at {args.output}. "
              "Use normal local pinentry/touch to sign; never supply a PIN in command arguments.", file=sys.stderr)
        return 1
    # Production must authenticate against the installed pin/certificate, never
    # a key exported from the signing keyring or supplied by the candidate.
    verify_archive(archive, signature)
    print(f"Verified {archive.name} and {signature.name}; source commit {commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
