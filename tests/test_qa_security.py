"""Isolated security QA regressions; never invoke installed-system QA."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from qa import security_check as qa
from sparehash import xmrig_verify as verify


def test_api_accepts_when_only_miner_port_is_loopback() -> None:
    # Given
    output = 'LISTEN 0 128 0.0.0.0:22 *:* users:(("sshd",pid=12,fd=3))\n'
    output += 'LISTEN 0 128 127.0.0.1:38087 *:* users:(("xmrig",pid=42,fd=3))'
    # When / Then
    qa.check_api(output, 38087, 42)


@pytest.mark.parametrize("output", (
    "",
    'LISTEN 0 128 127.0.0.1:22 *:* users:(("xmrig",pid=42,fd=3))',
    'LISTEN 0 128 127.0.0.1:38087 *:* users:(("other",pid=12,fd=3))',
    'LISTEN 0 128 0.0.0.0:38087 *:* users:(("xmrig",pid=42,fd=3))',
    'LISTEN 0 128 [::]:38087 *:* users:(("xmrig",pid=42,fd=3))',
    'LISTEN 0 128 127.0.0.2:38087 *:* users:(("xmrig",pid=42,fd=3))',
    'LISTEN 0 128 127.0.0.1:38087 *:*',
    'LISTEN 0 128 127.0.0.1:38087 *:* users:(("xmrig",pid=42,fd=3),("other",pid=12,fd=4))',
    'LISTEN 0 128 malformed *:*',
))
def test_api_rejects_when_missing_wrong_owner_or_exposed(output: str) -> None:
    # Given: each parameter violates a different listener boundary.
    # When / Then
    with pytest.raises(qa.CheckFailure):
        qa.check_api(output, 38087, 42)


def test_api_rejects_when_loopback_and_wildcard_both_listen() -> None:
    # Given
    output = '\n'.join(f'LISTEN 0 128 {host}:38087 *:* users:(("xmrig",pid=42,fd=3))'
                       for host in ("127.0.0.1", "*"))
    # When / Then
    with pytest.raises(qa.CheckFailure):
        qa.check_api(output, 38087, 42)


@pytest.mark.parametrize("pid", (0, -1))
def test_miner_rejects_when_mainpid_absent(pid: int) -> None:
    # Given / When / Then
    with pytest.raises(qa.CheckFailure):
        qa.check_miner(pid, 123, Path("/absent/xmrig"))


def test_miner_rejects_when_process_missing() -> None:
    # Given / When / Then
    with pytest.raises(FileNotFoundError):
        qa.check_miner(2**30, 123, Path("/absent/xmrig"))


def test_miner_rejects_when_expected_uid_is_root() -> None:
    # Given / When / Then
    with pytest.raises(qa.CheckFailure):
        qa.check_miner(os.getpid(), 0, Path("/proc/self/exe"))


def test_miner_rejects_when_live_process_uid_wrong() -> None:
    # Given / When / Then
    with pytest.raises(qa.CheckFailure):
        qa.check_miner(os.getpid(), os.getuid() + 1, Path("/proc/self/exe"))


@pytest.mark.parametrize("mismatch", ("mode", "uid", "gid", "kind"))
def test_permissions_reject_when_identity_or_kind_wrong(tmp_path: Path, mismatch: str) -> None:
    # Given
    path = tmp_path / "config"
    path.write_bytes(b"fixture")
    path.chmod(0o640)
    expected = (0o660 if mismatch == "mode" else 0o640,
                os.getuid() + (mismatch == "uid"), os.getgid() + (mismatch == "gid"))
    kind = stat.S_IFSOCK if mismatch == "kind" else stat.S_IFREG
    # When / Then
    with pytest.raises(qa.CheckFailure):
        qa.check_permissions(path, expected, kind)


def test_permissions_accept_when_exact_identity_matches(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "config"
    path.write_bytes(b"fixture")
    path.chmod(0o640)
    # When / Then
    qa.check_permissions(path, (0o640, os.getuid(), os.getgid()), stat.S_IFREG)


def test_permissions_reject_when_path_is_symlink(tmp_path: Path) -> None:
    # Given
    target = tmp_path / "target"
    target.write_bytes(b"fixture")
    target.chmod(0o640)
    link = tmp_path / "config"
    link.symlink_to(target)
    # When / Then
    with pytest.raises(qa.CheckFailure):
        qa.check_permissions(link, (0o640, os.getuid(), os.getgid()), stat.S_IFREG)


def test_permissions_reject_when_path_missing(tmp_path: Path) -> None:
    # Given / When / Then
    with pytest.raises(FileNotFoundError):
        qa.check_permissions(tmp_path / "missing", (0o640, os.getuid(), os.getgid()), stat.S_IFREG)


def test_tree_rejects_when_descendant_writable_or_not_root_owned(tmp_path: Path) -> None:
    # Given: unprivileged ownership is itself invalid; root exercises descendant mode.
    binary = tmp_path / "binary"
    binary.write_bytes(b"fixture")
    binary.chmod(0o777)
    # When / Then
    with pytest.raises(qa.CheckFailure):
        qa.check_tree(tmp_path)


def test_release_controls_when_valid_and_tampered() -> None:
    # Given / When / Then: real GPG certificate and signing subkey, no skipped prerequisites.
    qa.check_release()


def test_release_controls_reject_when_verifier_accepts_everything() -> None:
    # Given
    with patch.object(verify, "verify_release", return_value=None):
        # When / Then
        with pytest.raises(qa.CheckFailure):
            qa.check_release()


def test_release_controls_reject_when_verifier_rejects_everything() -> None:
    # Given
    with patch.object(verify, "verify_release", side_effect=verify.VerifyError("signature", "fixture")):
        # When / Then
        with pytest.raises(verify.VerifyError) as caught:
            qa.check_release()
    assert caught.value.stage == "signature"


@pytest.mark.parametrize("stage", ("sha256", "signature"))
def test_release_controls_reject_when_one_verification_stage_bypassed(stage: verify.Stage) -> None:
    # Given
    original = verify.verify_release

    # Four arguments deliberately mirror verify_release's public callback contract.
    def bypass(archive: Path, sums: Path, signature: Path, key: bytes) -> None:
        corrupt = archive.read_bytes().endswith(b"corrupt") if stage == "sha256" else sums.read_bytes().endswith(b"corrupt\n")
        if corrupt:
            return
        original(archive, sums, signature, key)

    with patch.object(verify, "verify_release", side_effect=bypass):
        # When / Then
        with pytest.raises(qa.CheckFailure):
            qa.check_release()


def test_release_controls_reject_when_failure_stage_wrong() -> None:
    # Given
    original = verify.verify_release

    # Four arguments deliberately mirror verify_release's public callback contract.
    def wrong_stage(archive: Path, sums: Path, signature: Path, key: bytes) -> None:
        try:
            original(archive, sums, signature, key)
        except verify.VerifyError as error:
            raise verify.VerifyError("fingerprint", "wrong rejection boundary") from error

    with patch.object(verify, "verify_release", side_effect=wrong_stage):
        # When / Then
        with pytest.raises(qa.CheckFailure):
            qa.check_release()


def test_release_controls_reject_when_gpg_unavailable() -> None:
    # Given
    with patch.object(subprocess, "run", side_effect=FileNotFoundError("gpg unavailable")):
        # When / Then
        with pytest.raises(FileNotFoundError):
            qa.check_release()
