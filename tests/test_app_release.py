"""Real detached application signatures, isolated from user and XMRig trust."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from sparehash.__main__ import main
from sparehash.app_release import AppReleaseError, verify_archive


def test_rootless_offline_version(capsys) -> None:
    def forbidden() -> int:
        raise AssertionError("version must not inspect privilege")

    with patch("socket.socket", side_effect=AssertionError("no network")), pytest.raises(SystemExit) as ended:
        main(["--version"], geteuid=forbidden)
    assert ended.value.code == 0
    from sparehash import __version__

    assert capsys.readouterr().out == f"SpareHash {__version__} (commit unknown)\n"


def test_production_trust_is_not_test_trust(tmp_path: Path) -> None:
    with pytest.raises(AppReleaseError, match="owner confirmation"):
        verify_archive(tmp_path / "missing", tmp_path / "missing.asc")


@pytest.fixture(scope="module")
def signing_key(tmp_path_factory):
    if shutil.which("gpg") is None:
        pytest.skip("GPG required")
    home = tmp_path_factory.mktemp("app-test-key")
    home.chmod(0o700)

    def gpg(*args: str) -> bytes:
        return subprocess.check_output(
            ["gpg", "--no-options", "--homedir", str(home), "--batch", "--yes",
             "--pinentry-mode", "loopback", "--passphrase", "", *args],
            env={**os.environ, "GNUPGHOME": str(home)}, stderr=subprocess.PIPE, timeout=30,
        )

    gpg("--quick-generate-key", "Application test only <app@example.invalid>", "ed25519", "cert", "0")
    pin = next(row.split(":")[9] for row in gpg("--with-colons", "--list-keys").decode().splitlines()
               if row.startswith("fpr:"))
    gpg("--quick-add-key", pin, "ed25519", "sign", "0")
    try:
        yield gpg, pin, gpg("--armor", "--export", pin), home
    finally:
        subprocess.run(["gpgconf", "--homedir", str(home), "--kill", "all"], check=True, timeout=10)


def test_detached_signature_tampering_and_wrong_pin(signing_key, tmp_path: Path) -> None:
    gpg, pin, key, _ = signing_key
    archive = tmp_path / "renamed.tar.gz"
    signature = tmp_path / "renamed.tar.gz.asc"
    archive.write_bytes(b"complete archive bytes")
    gpg("--armor", "--local-user", pin, "--output", str(signature), "--detach-sign", str(archive))
    verify_archive(archive, signature, key=key, fingerprint=pin)
    with pytest.raises(AppReleaseError, match="pinned primary"):
        verify_archive(archive, signature, key=key, fingerprint="A" * 40)
    archive.write_bytes(b"tampered archive bytes")
    with pytest.raises(AppReleaseError):
        verify_archive(archive, signature, key=key, fingerprint=pin)
    assert archive.read_bytes() == b"tampered archive bytes"
    assert signature.is_file(), "verification must not delete user inputs"


def test_real_signed_offline_staging(signing_key, tmp_path: Path) -> None:
    from sparehash import app_release
    from tests.test_app_discovery import make_archive

    gpg, pin, key, _ = signing_key
    archive = tmp_path / "renamed.tar.gz"
    signature = Path(str(archive) + ".asc")
    make_archive(archive)
    gpg("--armor", "--local-user", pin, "--output", str(signature), "--detach-sign", str(archive))
    stage = tmp_path / "private"
    stage.mkdir(mode=0o700)
    verifier = app_release.verify_archive
    with patch.object(app_release, "verify_archive", side_effect=lambda a, s: verifier(a, s, key=key, fingerprint=pin)):
        source, version, commit = app_release.stage_release(stage, local=archive)
    assert source == stage / "source" and version == "0.3.1" and commit == "a" * 40
    assert archive.is_file() and signature.is_file()


def test_clean_tag_builder_stamps_and_verifies(signing_key, tmp_path: Path, monkeypatch) -> None:
    import tarfile

    from qa import build_release as builder
    from sparehash import __version__

    _, pin, key, home = signing_key
    root = tmp_path / "repository"
    (root / "sparehash").mkdir(parents=True)
    (root / "qa").mkdir()
    (root / "sparehash/__init__.py").write_text(f'__version__ = "{__version__}"\n')
    (root / "sparehash/_build.py").write_text('COMMIT = "unknown"\n')
    (root / "pyproject.toml").write_text('''[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[project]
name = "sparehash"
dynamic = ["version"]
dependencies = []
[tool.hatch.version]
path = "sparehash/__init__.py"
''')
    for args in (("init",), ("add", "."),
                 ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture"),
                 ("tag", f"v{__version__}")):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    output = tmp_path / "artifacts"
    monkeypatch.setattr(builder, "ROOT", root)
    monkeypatch.setenv("GNUPGHOME", str(home))
    monkeypatch.setattr("sys.argv", ["build_release.py", "--tag", f"v{__version__}", "--sign", pin,
                                      "--output", str(output)])
    assert builder.main() == 0
    archive = output / f"sparehash-{__version__}-source.tar.gz"
    verify_archive(archive, Path(str(archive) + ".asc"), key=key, fingerprint=pin)
    with tarfile.open(archive) as source:
        stream = source.extractfile("sparehash/_build.py")
        assert stream is not None and commit.encode() in stream.read()
    import zipfile

    with zipfile.ZipFile(next(output.glob("*.whl"))) as wheel:
        assert commit.encode() in wheel.read("sparehash/_build.py")
        metadata = wheel.read(f"sparehash-{__version__}.dist-info/METADATA")
        assert f"Version: {__version__}".encode() in metadata
        assert b"Requires-Dist:" not in metadata
    # The builder refuses overwrites and dirty source rather than mislabelling.
    with pytest.raises(SystemExit):
        builder.main()
    (root / "sparehash/__init__.py").write_text("# dirty\n")
    with pytest.raises(SystemExit):
        builder.main()
