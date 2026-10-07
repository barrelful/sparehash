"""Fail-closed authentication of disposable XMRig release downloads."""
from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Final, Literal, TypeAlias

from .xmrig_release import FetchError, ReleaseClient

XMRIG_GPG_FINGERPRINT: Final = "9AC4CEA8E66E35A5C7CDDC1B446A53638BE94409"
SIGNING_KEY_URL: Final = "https://raw.githubusercontent.com/xmrig/xmrig/master/doc/gpg_keys/xmrig.asc"
METADATA_LIMIT: Final = 2 * 1024 * 1024
Stage: TypeAlias = Literal["key-download", "gpg", "import", "fingerprint", "signature", "sha256", "cleanup"]


class VerifyError(Exception):
    """Authentication failure with a machine-consumable stage."""

    def __init__(self, stage: Stage, detail: str) -> None:
        self.stage: Stage = stage
        self.detail: str = detail
        super().__init__(f"{stage}: {detail}")


def fetch_signing_key() -> bytes:
    """Fetch untrusted key bytes; only the source pin can authorize them."""
    try:
        with io.BytesIO() as output:
            _ = ReleaseClient().transfer(SIGNING_KEY_URL, output, METADATA_LIMIT)
            return output.getvalue()
    except FetchError as exc:
        raise VerifyError("key-download", str(exc)) from exc


def run_gpg(home: Path, args: tuple[str, ...], stage: Stage) -> str:
    """No user config, user keyring, auto retrieval, shell or ambient PATH."""
    executable = shutil.which("gpg", path="/usr/sbin:/usr/bin:/sbin:/bin")
    if executable is None:
        raise VerifyError("gpg", "Install GPG: apt install gpg / dnf install gnupg")
    try:
        result = subprocess.run(
            (executable, "--no-options", "--homedir", str(home), "--batch", "--no-tty", "--no-autostart",
             "--no-auto-key-retrieve", "--auto-key-locate", "clear", *args),
            env={**os.environ, "GNUPGHOME": str(home)}, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerifyError(stage, f"GPG unavailable or timed out: {exc}") from exc
    if result.returncode != 0:
        raise VerifyError(stage, f"GPG exited {result.returncode}")
    return result.stdout


def check_certificates(colons: str) -> None:
    """Read primary fpr records, never confuse a subkey with a certificate."""
    primary_pending = False
    fingerprints: list[str] = []
    for line in colons.splitlines():
        fields = line.split(":")
        if fields[0] in {"pub", "sub"}:
            primary_pending = fields[0] == "pub"
        if fields[0] == "fpr" and primary_pending:
            if len(fields) < 10 or re.fullmatch(r"[0-9A-F]{40}", fields[9]) is None:
                raise VerifyError("fingerprint", "Malformed primary certificate fingerprint")
            fingerprints.append(fields[9])
            primary_pending = False
    if fingerprints.count(XMRIG_GPG_FINGERPRINT) != 1:
        raise VerifyError("fingerprint", "Pinned primary certificate missing or ambiguous")


def check_signature(status: str) -> None:
    """Bind exactly one successful VALIDSIG to the pinned primary certificate."""
    records = [line.removeprefix("[GNUPG:] ").split() for line in status.splitlines()
               if line.startswith("[GNUPG:] ")]
    failures = {"BADSIG", "ERRSIG", "EXPSIG", "EXPKEYSIG", "REVKEYSIG", "NO_PUBKEY",
                "NODATA", "FAILURE", "ERROR", "KEYEXPIRED", "SIGEXPIRED", "KEYREVOKED"}
    valid = [fields for fields in records if fields and fields[0] == "VALIDSIG"]
    good = [fields for fields in records if fields and fields[0] == "GOODSIG"]
    if any(fields and fields[0] in failures for fields in records):
        raise VerifyError("signature", "Bad, expired, revoked or unavailable signature")
    if len(valid) != 1 or len(good) != 1:
        raise VerifyError("signature", "Missing or ambiguous successful signature status")
    fields = valid[0]
    if (len(fields) != 11 or fields[10] != XMRIG_GPG_FINGERPRINT
            or re.fullmatch(r"[0-9A-F]{40}", fields[1]) is None):
        raise VerifyError("signature", "Signature primary certificate is not the pinned signer")


def parse_checksum(sums: bytes, name: str) -> str:
    """Parse the signed sha256sum format with an exact, unique basename match."""
    try:
        text = sums.decode("utf-8")
    except UnicodeError as exc:
        raise VerifyError("sha256", "Checksum file is not UTF-8") from exc
    matches: list[str] = []
    for line in text.splitlines():
        entry = re.fullmatch(r"([0-9A-Fa-f]{64}) [ *]([^\x00\r\n]+)", line)
        if entry is None:
            raise VerifyError("sha256", "Malformed SHA256SUMS entry")
        if entry[2] == name:
            matches.append(entry[1].lower())
    if len(matches) != 1:
        raise VerifyError("sha256", "Missing or duplicate archive checksum")
    return matches[0]


# Four arguments are the plan's public contract: archive, signed data, signature, key.
def verify_release(tarball: Path, sums: Path, sig: Path, keydata: bytes) -> None:
    """Authenticate caller-owned disposable files; delete all three on failure.

    Signed metadata is snapshotted so GPG and the checksum parser consume the same
    bytes. Success leaves the archive for installation; never executes its contents.
    """
    complete = False
    stage: Stage = "import"
    try:
        with tempfile.TemporaryDirectory(prefix="sparehash-gpg-") as directory:
            home = Path(directory)
            home.chmod(0o700)
            if not 0 < len(keydata) <= METADATA_LIMIT:
                raise VerifyError("import", "Empty or oversized signing key")
            key = home / "key.asc"
            _ = key.write_bytes(keydata)
            _ = run_gpg(home, ("--import", str(key)), "import")
            check_certificates(run_gpg(home, ("--with-colons", "--fingerprint", "--list-keys"), "fingerprint"))
            stage = "signature"
            snapshots: list[bytes] = []
            for source, dest in ((sums, home / "SHA256SUMS"), (sig, home / "SHA256SUMS.sig")):
                with source.open("rb") as stream:
                    data = stream.read(METADATA_LIMIT + 1)
                if not 0 < len(data) <= METADATA_LIMIT:
                    raise VerifyError("signature", "Empty or oversized signed metadata")
                _ = dest.write_bytes(data)
                snapshots.append(data)
            status = run_gpg(home, ("--status-fd", "1", "--verify", str(home / "SHA256SUMS.sig"),
                                 str(home / "SHA256SUMS")), "signature")
            check_signature(status)
            stage = "sha256"
            expected = parse_checksum(snapshots[0], tarball.name)
            with tarball.open("rb") as archive:
                actual = hashlib.file_digest(archive, "sha256").hexdigest()
            if actual != expected:
                raise VerifyError("sha256", "sha256 mismatch")
        complete = True
    except OSError as exc:
        raise VerifyError(stage, f"Cannot read/write release verification files: {exc}") from exc
    finally:
        if not complete:
            failures: list[str] = []
            for artifact in (tarball, sums, sig):
                try:
                    artifact.unlink(missing_ok=True)
                except OSError as exc:
                    failures.append(f"{artifact}: {exc}")
            if failures:
                raise VerifyError("cleanup", "; ".join(failures))
