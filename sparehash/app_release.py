# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors
"""Application archive trust, deliberately independent of XMRig trust."""
from __future__ import annotations

import ast
import gzip
import io
import json
import os
import re
import stat
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .xmrig_release import FetchError, ReleaseClient
from .xmrig_verify import VerifyError, run_gpg

# OWNER PREREQUISITE: independently confirm the maintainer's full primary
# fingerprint and public certificate. Empty trust MUST refuse production updates.
# Never put a test certificate here, or derive trust from candidate contents.
APP_GPG_FINGERPRINT: Final = ""
APP_PUBLIC_KEY: Final = b""
RELEASES_URL: Final = "https://api.github.com/repos/barrelful/sparehash/releases"
ARCHIVE_LIMIT: Final = 32 * 1024 * 1024
EXPANDED_LIMIT: Final = 128 * 1024 * 1024
SIGNATURE_LIMIT: Final = 2 * 1024 * 1024
FILE_LIMIT: Final = 4096


class AppReleaseError(Exception):
    """Release identity, authenticity or archive contract refusal."""


def version_tuple(version: str) -> tuple[int, int, int]:
    if len(version) > 60 or re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version) is None:
        raise AppReleaseError("Expected numeric MAJOR.MINOR.PATCH version")
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


@dataclass(frozen=True)
class AppRelease:
    version: str
    url: str
    archive_url: str
    signature_url: str


def select_release(entries: list[object]) -> AppRelease:
    """Select published numeric releases, including GitHub pre-releases."""
    choices: dict[tuple[int, int, int], AppRelease] = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("draft") is not False:
            continue
        tag = entry.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith("v"):
            continue
        try:
            number = version_tuple(tag[1:])
        except AppReleaseError:
            continue
        name = f"sparehash-{tag[1:]}-source.tar.gz"
        assets = entry.get("assets")
        if not isinstance(assets, list) or len(assets) > 1000:
            raise AppReleaseError("Malformed release assets")
        selected: dict[str, str] = {}
        for asset in assets:
            if not isinstance(asset, dict) or asset.get("name") not in (name, name + ".asc"):
                continue
            asset_name = str(asset["name"])
            expected = f"https://github.com/barrelful/sparehash/releases/download/{tag}/{asset_name}"
            if asset.get("browser_download_url") != expected or asset_name in selected:
                raise AppReleaseError("Foreign or duplicate application release asset")
            selected[asset_name] = expected
        if set(selected) != {name, name + ".asc"}:
            continue
        if number in choices:
            raise AppReleaseError("Ambiguous numeric release identity")
        choices[number] = AppRelease(tag[1:], f"https://github.com/barrelful/sparehash/releases/tag/{tag}",
                                     selected[name], selected[name + ".asc"])
    if not choices:
        raise AppReleaseError("No published numeric release has the required source archive/signature pair")
    return choices[max(choices)]


def discover(client: ReleaseClient | None = None) -> AppRelease:
    """Bound discovery to ten 100-release pages; never use /releases/latest."""
    transport = client or ReleaseClient()
    entries: list[object] = []
    try:
        for page in range(1, 11):
            with io.BytesIO() as output:
                transport.transfer(f"{RELEASES_URL}?per_page=100&page={page}", output, SIGNATURE_LIMIT)
                batch = json.loads(output.getvalue())
            if not isinstance(batch, list) or len(batch) > 100:
                raise AppReleaseError("Malformed GitHub releases page")
            entries.extend(batch)
            if len(batch) < 100:
                return select_release(entries)
        raise AppReleaseError("Release discovery exceeded ten-page bound")
    except (FetchError, ValueError, UnicodeError, RecursionError) as error:
        raise AppReleaseError(f"Release discovery failed: {error}") from error


def snapshot(source: Path, destination: Path, limit: int) -> None:
    """No-follow bounded snapshot of a regular local input; originals survive."""
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
            raise AppReleaseError("Local release input must be a nonempty bounded regular file")
        with destination.open("xb") as output:
            total = 0
            while chunk := stream.read(min(65536, limit - total + 1)):
                total += len(chunk)
                if total > limit:
                    raise AppReleaseError("Local release input exceeds size bound")
                output.write(chunk)
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise AppReleaseError("Local release input changed during snapshot")


def _literal(path: Path, name: str) -> str:
    """Parse identity as data, not imports from candidate code."""
    try:
        nodes = ast.parse(path.read_bytes()).body
        values = [ast.literal_eval(node.value) for node in nodes if isinstance(node, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)]
    except (OSError, ValueError, SyntaxError, RecursionError) as error:
        raise AppReleaseError(f"Invalid candidate {name}") from error
    if len(values) != 1 or not isinstance(values[0], str):
        raise AppReleaseError(f"Missing or ambiguous candidate {name}")
    return values[0]


def extract_archive(archive: Path, destination: Path) -> tuple[str, str]:
    """Manually extract ordinary files only, after bounded gzip expansion."""
    destination.mkdir(mode=0o700)
    seen: set[str] = set()
    total = 0
    try:
        with tempfile.TemporaryFile() as raw:
            with gzip.open(archive, "rb") as compressed:
                while chunk := compressed.read(min(65536, EXPANDED_LIMIT - total + 1)):
                    total += len(chunk)
                    if total > EXPANDED_LIMIT:
                        raise AppReleaseError("Expanded archive exceeds size bound")
                    raw.write(chunk)
            raw.seek(0)
            with tarfile.open(fileobj=raw, mode="r:") as source:
                for count, member in enumerate(source, 1):
                    parts = member.name.rstrip("/").split("/")
                    name = "/".join(parts)
                    if (count > FILE_LIMIT or not name or name in seen or any(p in ("", ".", "..") for p in parts)
                            or "\\" in name or "\x00" in name or not (member.isfile() or member.isdir())
                            or member.size < 0 or member.size > EXPANDED_LIMIT):
                        raise AppReleaseError("Unsafe, duplicate or oversized archive member")
                    seen.add(name)
                    target = destination.joinpath(*parts)
                    if member.isdir():
                        target.mkdir(mode=0o755, parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                        stream = source.extractfile(member)
                        if stream is None:
                            raise AppReleaseError("Missing archive member contents")
                        with stream, target.open("xb") as output:
                            while chunk := stream.read(65536):
                                output.write(chunk)
                        target.chmod(0o755 if name == "install.sh" else 0o644)
        from .bootstrap import MAX_MODULES, _collect_source
        from .xmrig_ledger import UNIT_TEMPLATES

        inventory = _collect_source(destination, MAX_MODULES)
        if not all(f"systemd/{name}" in inventory for name in UNIT_TEMPLATES):
            raise AppReleaseError("Candidate is missing required unit templates")
        version = _literal(destination / "sparehash/__init__.py", "__version__")
        version_tuple(version)
        commit = _literal(destination / "sparehash/_build.py", "COMMIT")
        if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
            raise AppReleaseError("Candidate requires a full source commit stamp")
        return version, commit
    except (OSError, tarfile.TarError, EOFError) as error:
        raise AppReleaseError(f"Cannot safely extract source archive: {error}") from error


def stage_release(staging: Path, *, local: Path | None = None,
                  release: AppRelease | None = None, client: ReleaseClient | None = None) -> tuple[Path, str, str]:
    """One online/offline authentication pipeline. Caller owns private staging."""
    if staging.is_symlink() or not staging.is_dir() or staging.stat().st_mode & 0o077:
        raise AppReleaseError("Release staging must be a private directory")
    archive, signature = staging / "release.tar.gz", staging / "release.tar.gz.asc"
    try:
        if local is not None:
            if release is not None:
                raise AppReleaseError("Choose either local or online release")
            snapshot(local, archive, ARCHIVE_LIMIT)
            snapshot(Path(str(local) + ".asc"), signature, SIGNATURE_LIMIT)
        elif release is not None:
            transport = client or ReleaseClient()
            for url, target, limit in ((release.archive_url, archive, ARCHIVE_LIMIT),
                                       (release.signature_url, signature, SIGNATURE_LIMIT)):
                with target.open("xb") as stream:
                    transport.transfer(url, stream, limit)
        else:
            raise AppReleaseError("No release selected")
        verify_archive(archive, signature)
        source = staging / "source"
        version, commit = extract_archive(archive, source)
        if release is not None and version != release.version:
            raise AppReleaseError("Authenticated archive version does not match selected online release")
        return source, version, commit
    except (OSError, FetchError) as error:
        raise AppReleaseError(f"Cannot stage application release: {error}") from error


def verify_archive(archive: Path, signature: Path, *, key: bytes = APP_PUBLIC_KEY,
                   fingerprint: str = APP_GPG_FINGERPRINT) -> None:
    """Authenticate disposable staged inputs; never delete caller originals.

    Explicit trust arguments are for the local release builder and isolated
    tests, not CLI/environment options on the installed updater.
    """
    if re.fullmatch(r"[0-9A-F]{40}", fingerprint) is None or not 0 < len(key) <= 2 * 1024 * 1024:
        raise AppReleaseError("Maintainer signing key is not configured; owner confirmation required")
    try:
        with tempfile.TemporaryDirectory(prefix="sparehash-app-gpg-") as directory:
            home = Path(directory)
            home.chmod(0o700)
            certificate = home / "key.asc"
            certificate.write_bytes(key)
            run_gpg(home, ("--import", str(certificate)), "import")
            listing = run_gpg(home, ("--with-colons", "--fingerprint", "--list-keys"), "fingerprint")
            primaries: list[str] = []
            primary = False
            for line in listing.splitlines():
                fields = line.split(":")
                if fields[0] in {"pub", "sub"}:
                    primary = fields[0] == "pub"
                elif fields[0] == "fpr" and primary:
                    primaries.append(fields[9])
                    primary = False
            if primaries != [fingerprint]:
                raise AppReleaseError("Application certificate does not match pinned primary signer")
            status = run_gpg(home, ("--status-fd", "1", "--verify", str(signature.absolute()),
                                    str(archive.absolute())), "signature")
            records = [line.removeprefix("[GNUPG:] ").split() for line in status.splitlines()
                       if line.startswith("[GNUPG:] ")]
            valid = [row for row in records if row and row[0] == "VALIDSIG"]
            bad = {"BADSIG", "ERRSIG", "EXPSIG", "EXPKEYSIG", "REVKEYSIG", "NO_PUBKEY",
                   "NODATA", "FAILURE", "ERROR", "KEYEXPIRED", "SIGEXPIRED", "KEYREVOKED"}
            if (any(row and row[0] in bad for row in records) or len(valid) != 1
                    or len(valid[0]) != 11 or valid[0][10] != fingerprint
                    or len([row for row in records if row and row[0] == "GOODSIG"]) != 1):
                raise AppReleaseError("Archive signature does not authenticate the pinned application signer")
    except (VerifyError, OSError, IndexError) as error:
        raise AppReleaseError(f"Application signature verification failed: {error}") from error
