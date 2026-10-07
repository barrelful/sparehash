"""Real GPG signatures in private test keyrings; no user keyring access."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar, final
from unittest.mock import patch

from sparehash import xmrig_verify as verify
from sparehash.xmrig_release import FetchError, ReleaseClient


@unittest.skipUnless(shutil.which("gpg"), "GPG not installed")
@final
class VerifyTests(unittest.TestCase):
    keys: ClassVar[tempfile.TemporaryDirectory[str]]
    home: ClassVar[Path]
    pinned: ClassVar[str]
    attacker: ClassVar[str]
    public: ClassVar[bytes]
    bundle: ClassVar[bytes]

    @classmethod
    def gpg(cls, *args: str) -> bytes:
        return subprocess.run(
            ("/usr/bin/gpg", "--no-options", "--homedir", str(cls.home), "--batch", "--yes",
             "--pinentry-mode", "loopback", "--passphrase", "", *args),
            env={**os.environ, "GNUPGHOME": str(cls.home)}, capture_output=True,
            timeout=30, check=True,
        ).stdout

    @classmethod
    def prepare_keys(cls) -> None:
        cls.keys = tempfile.TemporaryDirectory(prefix="sparehash-test-keys-")
        cls.addClassCleanup(cls.keys.cleanup)
        cls.home = Path(cls.keys.name)
        cls.home.chmod(0o700)
        cls.addClassCleanup(cls.stop_agent)
        _ = cls.gpg("--quick-generate-key", "Pinned fixture <pinned@example.invalid>", "ed25519", "cert", "0")
        _ = cls.gpg("--quick-generate-key", "Attacker fixture <attacker@example.invalid>", "ed25519", "sign", "0")
        listing = cls.gpg("--with-colons", "--list-keys").decode()
        cls.pinned, cls.attacker = [line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:")]
        _ = cls.gpg("--quick-add-key", cls.pinned, "ed25519", "sign", "0")
        cls.public = cls.gpg("--armor", "--export", cls.pinned)
        cls.bundle = cls.gpg("--armor", "--export")

    setUpClass = prepare_keys

    @classmethod
    def stop_agent(cls) -> None:
        _ = subprocess.run(("gpgconf", "--homedir", str(cls.home), "--kill", "all"),
                           env={**os.environ, "GNUPGHOME": str(cls.home)}, timeout=10, check=True)

    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.directory: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory(prefix="sparehash-test-release-")
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.archive: Path = root / "fixture.tar.gz"
        self.sums: Path = root / "SHA256SUMS"
        self.sig: Path = root / "SHA256SUMS.sig"

    def prepare_fixture(self) -> None:
        _ = self.archive.write_bytes(b"fixture archive bytes")
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        _ = self.sums.write_text(f"{digest}  {self.archive.name}\n", encoding="utf-8")
        self.sign(self.pinned)
        self.addCleanup(patch.stopall)
        _ = patch.object(verify, "XMRIG_GPG_FINGERPRINT", self.pinned).start()

    setUp = prepare_fixture

    def sign(self, signer: str) -> None:
        _ = self.gpg("--local-user", signer, "--output", str(self.sig), "--detach-sign", str(self.sums))

    def assert_rejected(self, stage: verify.Stage, key: bytes | None = None) -> None:
        # When
        with self.assertRaises(verify.VerifyError) as caught:
            verify.verify_release(self.archive, self.sums, self.sig, self.public if key is None else key)
        # Then
        self.assertEqual(caught.exception.stage, stage)
        self.assertFalse(any(path.exists() for path in (self.archive, self.sums, self.sig)))

    def test_accepts_when_pinned_primary_owns_signing_subkey(self) -> None:
        # Given: fixture sums signed by a real signing subkey
        # When
        verify.verify_release(self.archive, self.sums, self.sig, self.public)
        # Then
        self.assertEqual(self.archive.read_bytes(), b"fixture archive bytes")

    def test_rejects_when_imported_certificate_has_wrong_fingerprint(self) -> None:
        # Given
        with patch.object(verify, "XMRIG_GPG_FINGERPRINT", "A" * 40):
            self.assert_rejected("fingerprint")

    def test_rejects_when_archive_byte_tampered(self) -> None:
        # Given
        _ = self.archive.write_bytes(b"Fixture archive bytes")
        self.assert_rejected("sha256")

    def test_rejects_when_signed_sums_tampered(self) -> None:
        # Given
        _ = self.sums.write_bytes(self.sums.read_bytes() + b"tampered\n")
        self.assert_rejected("signature")

    def test_rejects_when_signature_corrupt(self) -> None:
        # Given
        _ = self.sig.write_bytes(b"not an OpenPGP signature")
        self.assert_rejected("signature")

    def test_rejects_when_bundle_contains_pin_but_attacker_signs(self) -> None:
        # Given: both certificates imported; GPG accepts attacker's signature
        self.sign(self.attacker)
        self.assert_rejected("signature", self.bundle)

    def test_rejects_when_signature_contains_multiple_signers(self) -> None:
        # Given
        original = self.sig.read_bytes()
        self.sign(self.attacker)
        _ = self.sig.write_bytes(original + self.sig.read_bytes())
        self.assert_rejected("signature", self.bundle)

    def test_rejects_when_signing_key_invalid(self) -> None:
        # Given
        self.assert_rejected("import", b"not a key")

    def test_rejects_when_gpg_missing(self) -> None:
        # Given
        with patch.object(shutil, "which", return_value=None):
            self.assert_rejected("gpg")

    def test_rejects_when_gpg_times_out(self) -> None:
        # Given
        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("gpg", 30)):
            self.assert_rejected("import")

    def test_rejects_when_checksum_missing_duplicate_or_malformed(self) -> None:
        # Given
        for content in ("0" * 64 + "  other.tar.gz\n", self.sums.read_text() * 2, "malformed\n"):
            with self.subTest(content=content):
                _ = self.archive.write_bytes(b"fixture archive bytes")
                _ = self.sums.write_text(content)
                self.sign(self.pinned)
                self.assert_rejected("sha256")

    def test_keyring_is_private_and_removed_when_verification_fails(self) -> None:
        # Given
        homes: list[Path] = []
        original = verify.run_gpg

        def inspect(home: Path, args: tuple[str, ...], stage: verify.Stage) -> str:
            homes.append(home)
            self.assertEqual(home.stat().st_mode & 0o777, 0o700)
            return original(home, args, stage)

        _ = self.archive.write_bytes(b"tampered")
        # When
        with patch.object(verify, "run_gpg", side_effect=inspect):
            self.assert_rejected("sha256")
        # Then
        self.assertTrue(homes)
        self.assertFalse(any(home.exists() for home in homes))


class StatusTests(unittest.TestCase):
    def test_rejects_when_status_missing_ambiguous_wrong_or_expired(self) -> None:
        # Given
        pin = verify.XMRIG_GPG_FINGERPRINT
        valid = f"[GNUPG:] VALIDSIG {'B' * 40} 2026-01-01 1 0 4 0 22 8 00 {pin}\n"
        good = f"[GNUPG:] GOODSIG {pin[-16:]} fixture\n"
        for status in ("", good, valid, good + valid * 2,
                       good + valid.replace(pin, "A" * 40), good + valid.rsplit(" ", 1)[0],
                       *(good + valid + f"[GNUPG:] {flag} fixture\n" for flag in
                         ("BADSIG", "ERRSIG", "EXPSIG", "EXPKEYSIG", "REVKEYSIG", "KEYEXPIRED", "SIGEXPIRED"))):
            with self.subTest(status=status):
                # When / Then
                with self.assertRaises(verify.VerifyError) as caught:
                    verify.check_signature(status)
                self.assertEqual(caught.exception.stage, "signature")

    def test_accepts_when_primary_certificate_itself_signs(self) -> None:
        # Given
        pin = verify.XMRIG_GPG_FINGERPRINT
        status = f"[GNUPG:] GOODSIG {pin[-16:]} fixture\n[GNUPG:] VALIDSIG {pin} 2026-01-01 1 0 4 0 22 8 00 {pin}\n"
        # When / Then
        verify.check_signature(status)

    def test_rejects_when_subkey_fingerprint_masquerades_as_primary(self) -> None:
        # Given
        colons = f"pub:::::::::\nfpr:::::::::{'A' * 40}:\nsub:::::::::\nfpr:::::::::{verify.XMRIG_GPG_FINGERPRINT}:\n"
        # When / Then
        with self.assertRaises(verify.VerifyError) as caught:
            verify.check_certificates(colons)
        self.assertEqual(caught.exception.stage, "fingerprint")

    def test_checksum_accepts_when_binary_marker_and_exact_name_match(self) -> None:
        # Given
        digest = "a" * 64
        # When
        actual = verify.parse_checksum(f"{digest} *archive.tar.gz\n".encode(), "archive.tar.gz")
        # Then
        self.assertEqual(actual, digest)

    def test_fetch_reports_stage_when_transport_fails(self) -> None:
        # Given
        with patch.object(ReleaseClient, "transfer", side_effect=FetchError("network unavailable")):
            # When / Then
            with self.assertRaises(verify.VerifyError) as caught:
                _ = verify.fetch_signing_key()
        self.assertEqual(caught.exception.stage, "key-download")
