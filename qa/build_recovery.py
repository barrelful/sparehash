#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unprivileged deterministic recovery bundle from exact Git objects, never the worktree."""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path

ORIGINAL = "f348ed165358668d8101cd121cf07ee2f001295f"


def sources(commit: str, *, recovery: bool = False) -> dict[str, bytes]:
    paths = ["sparehash", "systemd"] + (["qa/recover_uninstall.py", "qa/recovery_stage.py"] if recovery else [])
    blob = subprocess.check_output(["git", "archive", commit, *paths])
    result = {}
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        for member in archive:
            if member.isfile() and re.fullmatch(r"(?:sparehash/[A-Za-z_][A-Za-z0-9_]*\.py|systemd/sparehash(?:-xmrig)?\.service\.in|qa/(?:recover_uninstall|recovery_stage)\.py)", member.name):
                stream = archive.extractfile(member)
                assert stream is not None
                result[member.name] = stream.read()
    return result


def build(commit: str, output: Path) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError("a full reviewed commit is required")
    files, old = sources(commit, recovery=True), sources(ORIGINAL)
    tree = ast.parse(old["sparehash/bootstrap.py"])
    template = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.AnnAssign)
                    and node.value is not None and isinstance(node.target, ast.Name) and node.target.id == "SHIM_TEMPLATE")
    descriptor = {"schema": 1, "commit": commit, "original_commit": ORIGINAL,
                  "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
                  "original_files": {name: hashlib.sha256(data).hexdigest() for name, data in old.items()},
                  "original_shims": {root: hashlib.sha256(template.format(source=root + "/sparehash/src").encode()).hexdigest()
                                     for root in ("/opt", "/var/opt")}}
    files["bundle.json"] = (json.dumps(descriptor, sort_keys=True, separators=(",", ":")) + "\n").encode()
    with output.open("xb") as destination, tarfile.open(fileobj=destination, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(data), 0o600
            archive.addfile(member, io.BytesIO(data))
    return hashlib.sha256(output.read_bytes()).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("commit")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(build(args.commit, args.output), args.output)
