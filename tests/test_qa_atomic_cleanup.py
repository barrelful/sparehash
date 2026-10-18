"""Real tmp-root ownership regressions, not privileged guest cleanup acceptance."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TextIO

import pytest

from qa import atomic_probe_cleanup as staging
from qa.atomic_probe import RefusalError


def test_shell_staging_allows_bounded_binary_before_evidence_limit(tmp_path: Path) -> None:
    # Exercise the shell's actual preceding file-limit directives, not its guard,
    # systemd operations or candidate. The payload is inert synthetic bytes.
    script = Path("qa/qa_atomic_probe.sh").read_text()
    before_stage = script.split('"$HERE/atomic_probe.py" stage', 1)[0]
    limits = [line for line in before_stage.splitlines() if line.startswith("ulimit -f ")]
    snippet = "\n".join((*limits, 'exec "$@"'))
    result = subprocess.run(
        ["bash", "-c", snippet, "staging-limit-fixture", sys.executable, "-B", "-c",
         "from pathlib import Path; import sys; "
         "from qa.atomic_probe_cleanup import create_staging; "
         "create_staging(Path(sys.argv[1]), Path(sys.argv[2]), b'x' * (256 * 1024))",
         str(tmp_path), str(tmp_path / "ownership")],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "xmrig/task3/xmrig").stat().st_size == 256 * 1024
    assert staging.cleanup_staging(tmp_path, tmp_path / "ownership")


@pytest.mark.parametrize("replacement", ["root", "child-symlink", "child-directory",
                                       "binary", "config", "leaf-symlink", "hardlink",
                                       "missing-binary", "missing-child", "extra-file"])
def test_cleanup_blocks_replaced_or_missing_owned_object(tmp_path: Path, replacement: str) -> None:
    # Given: real files, retained originals prevent accidental inode reuse.
    parent = tmp_path / "parent"
    parent.mkdir(mode=0o700)
    receipt = tmp_path / "ownership"
    staging.create_staging(parent, receipt, b"unexecuted fixture")
    root = parent / "xmrig"
    child = root / "task3"
    foreign = tmp_path / "foreign"
    (foreign / "task3").mkdir(parents=True)
    sentinel = foreign / "task3/xmrig"
    _ = sentinel.write_bytes(b"foreign binary")
    _ = (foreign / "task3/config.json").write_bytes(b"foreign config")
    if replacement == "root":
        _ = root.rename(tmp_path / "original-root")
        _ = foreign.rename(root)
        sentinel = root / "task3/xmrig"
    if replacement in ("child-symlink", "child-directory", "missing-child"):
        _ = child.rename(tmp_path / "original-child")
        if replacement == "child-symlink":
            child.symlink_to(foreign / "task3", target_is_directory=True)
        if replacement == "child-directory":
            _ = (foreign / "task3").rename(child)
            sentinel = child / "xmrig"
    if replacement in ("binary", "config", "leaf-symlink", "hardlink", "missing-binary"):
        leaf = child / ("config.json" if replacement == "config" else "xmrig")
        _ = leaf.rename(tmp_path / "original-leaf")
        if replacement in ("binary", "config"):
            _ = leaf.write_bytes(b"foreign leaf")
            sentinel = leaf
        if replacement == "leaf-symlink":
            leaf.symlink_to(sentinel)
        if replacement == "hardlink":
            os.link(sentinel, leaf)
    if replacement == "extra-file":
        sentinel = child / "foreign"
        _ = sentinel.write_bytes(b"foreign extra")
    before = sentinel.read_bytes()
    # When / Then: refuse before unlinking any original or foreign object.
    with pytest.raises(RefusalError):
        _ = staging.cleanup_staging(parent, receipt)
    assert sentinel.read_bytes() == before
    assert root.exists()


@pytest.mark.parametrize("interrupt_after", ["xmrig", "xmrig/task3", "xmrig/task3/xmrig",
                                          "xmrig/task3/config.json"])
def test_cleanup_removes_only_receipted_partial_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                       interrupt_after: str) -> None:
    # Given: interrupt after a real exclusive creation and durable identity record.
    parent = tmp_path / "parent"
    parent.mkdir(mode=0o700)
    receipt = tmp_path / "ownership"
    record = staging.record_creation

    def interrupt(stream: TextIO, owned: staging.Owned) -> None:
        record(stream, owned)
        if owned.path == interrupt_after:
            raise KeyboardInterrupt

    monkeypatch.setattr(staging, "record_creation", interrupt)
    with pytest.raises(KeyboardInterrupt):
        staging.create_staging(parent, receipt, b"unexecuted fixture")
    sentinel = parent / "foreign"
    _ = sentinel.write_bytes(b"untouched")
    # When
    complete = staging.cleanup_staging(parent, receipt)
    # Then: successful partial cleanup is still not complete-run evidence.
    assert complete is False
    assert not (parent / "xmrig").exists()
    assert sentinel.read_bytes() == b"untouched"


def test_cleanup_removes_complete_owned_graph(tmp_path: Path) -> None:
    # Given
    receipt = tmp_path / "ownership"
    staging.create_staging(tmp_path, receipt, b"unexecuted fixture")
    # When
    complete = staging.cleanup_staging(tmp_path, receipt)
    # Then
    assert complete is True
    assert not (tmp_path / "xmrig").exists()


def test_repeated_cleanup_blocks_missing_previously_owned_objects(tmp_path: Path) -> None:
    # Given: completed cleanup; its historical receipt grants no new ownership.
    receipt = tmp_path / "ownership"
    staging.create_staging(tmp_path, receipt, b"unexecuted fixture")
    assert staging.cleanup_staging(tmp_path, receipt)
    # When / Then
    with pytest.raises(RefusalError):
        _ = staging.cleanup_staging(tmp_path, receipt)


def test_cleanup_blocks_unrecorded_creation_window(tmp_path: Path) -> None:
    # Given: interruption between mkdir and recording its identity.
    receipt = tmp_path / "ownership"
    _ = receipt.write_text("")
    (tmp_path / "xmrig").mkdir()
    sentinel = tmp_path / "xmrig/foreign"
    _ = sentinel.write_bytes(b"unknown ownership")
    # When / Then
    with pytest.raises(RefusalError):
        _ = staging.cleanup_staging(tmp_path, receipt)
    assert sentinel.read_bytes() == b"unknown ownership"


def test_cleanup_never_follows_replaced_parent(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir(mode=0o700)
    receipt = tmp_path / "ownership"
    staging.create_staging(parent, receipt, b"inert original")
    _ = parent.rename(tmp_path / "original-parent")
    foreign = tmp_path / "foreign"
    (foreign / "xmrig/task3").mkdir(parents=True)
    sentinel = foreign / "xmrig/task3/xmrig"
    _ = sentinel.write_bytes(b"foreign sentinel")
    parent.symlink_to(foreign, target_is_directory=True)
    with pytest.raises(RefusalError):
        _ = staging.cleanup_staging(parent, receipt)
    assert sentinel.read_bytes() == b"foreign sentinel"
    assert (tmp_path / "original-parent/xmrig/task3/xmrig").read_bytes() == b"inert original"
