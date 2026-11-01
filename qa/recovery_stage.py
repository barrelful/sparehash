# SPDX-License-Identifier: GPL-3.0-or-later
"""Standalone verified-bundle gate. Bootstrap only this file AFTER external SHA verification."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import sys
import tarfile
from pathlib import Path

BASE = Path("/run/sparehash/recovery")
ORIGINAL = "f348ed165358668d8101cd121cf07ee2f001295f"
FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def protected(path: Path, mode: int, directory: bool = False) -> None:
    info = path.lstat()
    kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    if not kind or info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != mode:
        raise ValueError("unsafe protected recovery object: " + str(path))


def unique(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate bundle key")
        result[key] = value
    return result


def unpack(blob: bytes, digest: str, commit: str) -> tuple[dict, dict[str, bytes]]:
    if len(blob) > 4 * 1024 * 1024 or hashlib.sha256(blob).hexdigest() != digest:
        raise ValueError("archive differs from the independently supplied SHA-256")
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as archive:
        for member in archive:
            if (not member.isfile() or member.name in files or member.size > 256 * 1024
                    or not re.fullmatch(r"bundle\.json|sparehash/[A-Za-z_][A-Za-z0-9_]*\.py|systemd/sparehash(?:-xmrig)?\.service\.in|qa/(?:recover_uninstall|recovery_stage)\.py", member.name)):
                raise ValueError("unexpected/linked/oversized archive member")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("unreadable member")
            files[member.name] = stream.read()
    descriptor = json.loads(files.pop("bundle.json"), object_pairs_hook=unique)
    if (set(descriptor) != {"schema", "commit", "original_commit", "files", "original_files", "original_shims"}
            or type(descriptor["schema"]) is not int or descriptor["schema"] != 1
            or descriptor["commit"] != commit or descriptor["original_commit"] != ORIGINAL
            or not 4 <= len(files) <= 130 or descriptor["files"] != {
                name: hashlib.sha256(data).hexdigest() for name, data in files.items()}):
        raise ValueError("bundle inventory/provenance differs")
    return descriptor, files


def verified_source(stage: Path, digest: str, commit: str) -> tuple[dict, dict[str, bytes]]:
    if stage != BASE / commit or re.fullmatch(r"[0-9a-f]{40}", commit) is None or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("fixed recovery staging path/commit/digest required")
    for path in (Path("/"), Path("/run"), Path("/run/sparehash")):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("unsafe staging ancestor")
    for path in (BASE, stage):
        protected(path, 0o700, True)
    if {path.name for path in BASE.iterdir()} != {commit}:
        raise ValueError("foreign or second recovery staging entry; preserved")
    for name in ("archive.tar", "stage.py"):
        protected(stage / name, 0o600)
    with os.fdopen(os.open(stage / "archive.tar", FILE), "rb") as stream:
        blob = stream.read(4 * 1024 * 1024 + 1)
    descriptor, files = unpack(blob, digest, commit)
    if (stage / "stage.py").read_bytes() != files["qa/recovery_stage.py"]:
        raise ValueError("bootstrap does not match verified archive")
    if {path.name for path in stage.iterdir()} - {"archive.tar", "stage.py", "source"}:
        raise ValueError("foreign staging entries; preserved")
    return descriptor, files


def source_tree(stage: Path, files: dict[str, bytes]) -> Path:
    source = stage / "source"
    if not source.exists():
        source.mkdir(mode=0o700)
    protected(source, 0o700, True)
    expected = {"sparehash", "systemd", "qa"}
    if {path.name for path in source.iterdir()} - expected:
        raise ValueError("foreign source entries")
    for directory in expected:
        parent = source / directory
        if not parent.exists():
            parent.mkdir(mode=0o700)
        protected(parent, 0o700, True)
        if {path.name for path in parent.iterdir()} - {Path(name).name for name in files if name.startswith(directory + "/")}:
            raise ValueError("unexpected staged source member")
    for name, data in files.items():
        path = source / name
        if not os.path.lexists(path):
            with path.open("xb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        protected(path, 0o600)
        if path.read_bytes() != data:
            raise ValueError("staged source differs; refusing to overwrite")
    return source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("commit")
    parser.add_argument("sha256", help="independently published archive SHA-256")
    parser.add_argument("action", choices=("preflight", "apply", "remove-staging"))
    args = parser.parse_args()
    try:
        if os.geteuid() != 0:
            raise ValueError("root is required")
        stage = BASE / args.commit
        if Path(__file__).absolute() != stage / "stage.py":
            raise ValueError("execute only the verified bootstrap in the approved staging hierarchy")
        descriptor, files = verified_source(stage, args.sha256, args.commit)
        source = source_tree(stage, files)
        sys.path.insert(0, str(source))
        sys.path.insert(0, str(source / "qa"))
        from recover_uninstall import recover

        recover(descriptor, apply=args.action == "apply", completed_only=args.action == "remove-staging")
        if args.action == "remove-staging":
            # Reverify every byte/entry before unlinking only the finite verified inventory.
            verified_source(stage, args.sha256, args.commit)
            source_tree(stage, files)
            for name in files:
                (source / name).unlink()
            for name in ("sparehash", "systemd", "qa"):
                (source / name).rmdir()
            source.rmdir()
            (stage / "archive.tar").unlink()
            (stage / "stage.py").unlink()
            stage.rmdir()
            BASE.rmdir()  # Nonempty/foreign entries are never recursively removed.
            from sparehash.cli_uninstall import RemovalJournal
            from sparehash.deployment import Tree

            RemovalJournal(Tree()).clear()  # Only after complete removal AND verified staging disposal.
            print("Verified temporary recovery source removed.")
        return 0
    except (Exception, KeyboardInterrupt) as error:
        print("Recovery refused/interrupted:", type(error).__name__, str(error), file=sys.stderr)
        print("Leave services stopped. Preserve journals and staging; do not clear the retirement latch.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
