"""Availability is unprivileged; authentication precedes candidate parsing."""
from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from sparehash import app_release as release
from sparehash.__main__ import main
from sparehash.xmrig_release import FetchError, ReleaseClient


def entry(version: str, *, signed: bool = True, draft: bool = False) -> dict:
    name = f"sparehash-{version}-source.tar.gz"
    names = [name, name + ".asc"] if signed else [name]
    return {"tag_name": f"v{version}", "draft": draft, "prerelease": True, "assets": [
        {"name": n, "browser_download_url": f"https://github.com/barrelful/sparehash/releases/download/v{version}/{n}"}
        for n in names]}


def test_integer_selection_includes_prereleases_ignores_unsigned_and_drafts() -> None:
    selected = release.select_release([entry("0.9.0"), entry("0.10.0"), entry("1.0.0", signed=False),
                                       entry("2.0.0", draft=True), entry("3.0.0-dev")])
    assert selected.version == "0.10.0"
    with pytest.raises(release.AppReleaseError, match="No published"):
        release.select_release([entry("0.2.1", signed=False)])


def test_foreign_or_duplicate_asset_refused() -> None:
    raw = entry("0.3.1")
    raw["assets"][0]["browser_download_url"] = "https://attacker.invalid/source.tar.gz"
    with pytest.raises(release.AppReleaseError, match="Foreign"):
        release.select_release([raw])
    raw = entry("0.3.1")
    raw["assets"].append(raw["assets"][0])
    with pytest.raises(release.AppReleaseError, match="duplicate"):
        release.select_release([raw])


def test_paginated_discovery_and_failure() -> None:
    urls: list[str] = []

    def transfer(self, url, output, limit):
        urls.append(url)
        data = ([entry("0.2.1", signed=False)] * 100) if len(urls) == 1 else [entry("0.3.1")]
        output.write(json.dumps(data).encode())
        return 1

    with patch.object(ReleaseClient, "transfer", transfer):
        assert release.discover().version == "0.3.1"
    assert len(urls) == 2 and all("/releases?" in url and "/latest" not in url for url in urls)
    with patch.object(ReleaseClient, "transfer", side_effect=FetchError("HTTP 403 rate limit")), pytest.raises(
        release.AppReleaseError, match="rate limit",
    ):
        release.discover()


def test_check_does_not_read_private_state_or_privilege(capsys) -> None:
    selected = release.select_release([entry("999.0.0")])
    with patch("sparehash.cli_update.discover", return_value=selected), patch(
        "sparehash.deployment.Tree.read", side_effect=AssertionError("no private reads"),
    ):
        assert main(["update", "--check"], geteuid=lambda: pytest.fail("no UID probe")) == 0
    assert "Update available" in capsys.readouterr().out
    with patch("sparehash.cli_update.discover", side_effect=release.AppReleaseError("offline")):
        assert main(["update", "--check"], geteuid=lambda: 1000) == 1
    assert "offline" in capsys.readouterr().err


@pytest.mark.parametrize("args", [["--check", "--from", "x"], ["--check", "--recover"], ["--recover", "--from", "x"]])
def test_modes_are_exclusive(args) -> None:
    with pytest.raises(SystemExit) as ended:
        main(["update", *args])
    assert ended.value.code == 2


def make_archive(path: Path, extras: list[tarfile.TarInfo] | None = None, *, version: str = "0.3.1") -> None:
    from sparehash.xmrig_ledger import UNIT_TEMPLATES

    files = {"sparehash/__init__.py": f'__version__ = "{version}"\n'.encode(),
             "sparehash/_build.py": f'COMMIT = "{"a" * 40}"\n'.encode(),
             "sparehash/__main__.py": b'raise AssertionError("never execute candidate")\n',
             **{f"systemd/{name}": b"fixture" for name in UNIT_TEMPLATES}}
    with tarfile.open(path, "w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        for info in extras or []:
            archive.addfile(info, io.BytesIO(b"x" * info.size) if info.isfile() else None)


@pytest.mark.parametrize("name,kind", [("../outside", "file"), ("/outside", "file"), ("foo/../../outside", "file"),
                                      ("sparehash/__init__.py", "file"), ("link", "symlink"),
                                      ("hard", "hardlink"), ("fifo", "fifo")])
def test_unsafe_extraction_refuses(tmp_path: Path, name: str, kind: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = {"file": tarfile.REGTYPE, "symlink": tarfile.SYMTYPE,
                 "hardlink": tarfile.LNKTYPE, "fifo": tarfile.FIFOTYPE}[kind]
    info.linkname = "outside"
    archive = tmp_path / "input.tar.gz"
    make_archive(archive, [info])
    with pytest.raises(release.AppReleaseError):
        release.extract_archive(archive, tmp_path / "source")
    assert not (tmp_path / "outside").exists()


def test_valid_extraction_checks_identity_and_modes(tmp_path: Path) -> None:
    archive = tmp_path / "input.tar.gz"
    make_archive(archive)
    source = tmp_path / "source"
    assert release.extract_archive(archive, source) == ("0.3.1", "a" * 40)
    assert (source / "sparehash/__main__.py").stat().st_mode & 0o777 == 0o644
    assert source.stat().st_mode & 0o777 == 0o700


def test_snapshot_refuses_links_fifo_and_bound(tmp_path: Path) -> None:
    import os

    original = tmp_path / "input"
    original.write_bytes(b"original")
    link = tmp_path / "link"
    link.symlink_to(original)
    with pytest.raises(OSError):
        release.snapshot(link, tmp_path / "copy", 100)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    for path, limit in ((fifo, 100), (original, 1)):
        with pytest.raises(release.AppReleaseError):
            release.snapshot(path, tmp_path / "copy", limit)
    assert original.read_bytes() == b"original"


def test_untrusted_release_never_extracts_or_deletes_originals(tmp_path: Path) -> None:
    archive = tmp_path / "original.tar.gz"
    archive.write_bytes(b"untrusted")
    signature = Path(str(archive) + ".asc")
    signature.write_bytes(b"bad signature")
    stage = tmp_path / "stage"
    stage.mkdir(mode=0o700)
    with patch.object(release, "extract_archive", side_effect=AssertionError("not before trust")), pytest.raises(release.AppReleaseError):
        release.stage_release(stage, local=archive)
    assert archive.read_bytes() == b"untrusted" and signature.read_bytes() == b"bad signature"


def test_authenticated_version_mismatch_and_expansion_bounds(tmp_path: Path) -> None:
    archive = tmp_path / "input.tar.gz"
    make_archive(archive)
    selected = release.select_release([entry("0.3.2")])

    def transfer(self, url, output, limit):
        output.write(archive.read_bytes() if url.endswith(".gz") else b"signature")
        return 1

    stage = tmp_path / "stage"
    stage.mkdir(mode=0o700)
    with patch.object(release, "verify_archive"), patch.object(ReleaseClient, "transfer", transfer), pytest.raises(
        release.AppReleaseError, match="does not match",
    ):
        release.stage_release(stage, release=selected)
    with patch.object(release, "EXPANDED_LIMIT", 100), pytest.raises(release.AppReleaseError, match="Expanded"):
        release.extract_archive(archive, tmp_path / "bounded")
