# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors
"""Application archive trust, deliberately independent of XMRig trust."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Final

from .xmrig_verify import VerifyError, run_gpg

# OWNER PREREQUISITE: independently confirm the maintainer's full primary
# fingerprint and public certificate. Empty trust MUST refuse production updates.
# Never put a test certificate here, or derive trust from candidate contents.
APP_GPG_FINGERPRINT: Final = ""
APP_PUBLIC_KEY: Final = b""


class AppReleaseError(Exception):
    """Release identity, authenticity or archive contract refusal."""


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
