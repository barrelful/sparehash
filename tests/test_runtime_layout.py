"""Private-tree runtime ownership/cleanup regressions; no host service effects."""

from __future__ import annotations

import importlib.util
import os
import socket
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from sparehash import runtime
from sparehash.deployment import DeploymentError, Tree
from sparehash.paths import CONTROL_SOCK, MINER_RUN_DIR, RUN_DIR


@pytest.fixture
def tree(tmp_path: Path) -> Tree:
    for relative in ("run", "run/sparehash", "run/sparehash/ipc",
                     "run/sparehash-miner", "run/sparehash-miner/private"):
        (tmp_path / relative).mkdir(mode=0o755)
    for parent in ("run/sparehash", "run/sparehash-miner"):
        (tmp_path / parent).chmod(0o711)
    (tmp_path / "run/sparehash/ipc").chmod(0o750)
    (tmp_path / "run/sparehash-miner/private").chmod(0o750)
    return Tree(tmp_path, os.getuid())


def test_paths_separate_protected_children_from_manager_parents():
    assert str(RUN_DIR) == "/run/sparehash/ipc"
    assert CONTROL_SOCK == RUN_DIR / "control.sock"
    assert str(MINER_RUN_DIR) == "/run/sparehash-miner/private"


def test_traversal_uses_path_descriptors_without_weakening_ancestor_checks(tree: Tree):
    opened = []
    original = os.open

    def record(path, flags, *args, **kwargs):
        opened.append((path, flags))
        return original(path, flags, *args, **kwargs)

    with patch("sparehash.deployment.os.open", side_effect=record), ExitStack() as stack:
        fd = tree.pin(stack, str(MINER_RUN_DIR))
        assert os.fstat(fd).st_uid == os.getuid()
    intermediate = [flags for name, flags in opened if name == "sparehash-miner"]
    assert intermediate and all(flags & os.O_PATH and flags & os.O_NOFOLLOW for flags in intermediate)
    leaf = [flags for name, flags in opened if name == "private"]
    assert leaf and not leaf[-1] & os.O_PATH
    assert leaf[-1] & os.O_DIRECTORY and leaf[-1] & os.O_NOFOLLOW


@pytest.mark.parametrize("fault", ["writable", "symlink"])
def test_path_descriptors_still_refuse_unsafe_intermediate_ancestors(tree: Tree, fault: str):
    ancestor = tree.path("/run/sparehash-miner")
    if fault == "writable":
        ancestor.chmod(0o777)
    else:
        ancestor.rename(ancestor.with_name("foreign"))
        ancestor.symlink_to(ancestor.with_name("foreign"))
    with pytest.raises((OSError, DeploymentError)), ExitStack() as stack:
        tree.pin(stack, str(MINER_RUN_DIR))


def test_cleanup_removes_only_validated_artifacts_and_preserves_foreign_state(tree: Tree):
    directory = tree.path(str(MINER_RUN_DIR))
    for name in ("operation.json", "benchmark.json"):
        tree.write(str(MINER_RUN_DIR / name), (b"owned fixture", 0o640, os.getgid()))
    foreign = directory / "foreign-sentinel"
    foreign.write_bytes(b"preserve")
    parent = directory.parent.stat()
    runtime._remove_runtime(tree, str(MINER_RUN_DIR), os.getgid(), ("operation.json", "benchmark.json"))
    assert sorted(path.name for path in directory.iterdir()) == ["foreign-sentinel"]
    assert foreign.read_bytes() == b"preserve"
    assert directory.parent.stat() == parent


def test_cleanup_removes_empty_child_not_manager_parent(tree: Tree):
    directory = tree.path(str(MINER_RUN_DIR))
    runtime._remove_runtime(tree, str(MINER_RUN_DIR), os.getgid(), ("operation.json", "benchmark.json"))
    assert not directory.exists()
    assert directory.parent.stat().st_mode & 0o777 == 0o711


@pytest.mark.parametrize("fault", ["regular-mode", "symlink", "directory", "child-mode", "child-symlink"])
def test_unsafe_named_artifacts_or_child_are_preserved(tree: Tree, fault: str):
    directory = tree.path(str(MINER_RUN_DIR))
    spec = directory / "operation.json"
    other = directory.parent / "foreign-target"
    other.write_bytes(b"foreign")
    if fault == "regular-mode":
        spec.write_bytes(b"foreign")
        spec.chmod(0o644)
    elif fault == "symlink":
        spec.symlink_to(other)
    elif fault == "directory":
        spec.mkdir()
    elif fault == "child-mode":
        directory.chmod(0o755)
    else:
        directory.rmdir()
        directory.symlink_to(directory.parent)
    with pytest.raises((OSError, RuntimeError)):
        runtime._remove_runtime(tree, str(MINER_RUN_DIR), os.getgid(), ("operation.json",))
    assert other.read_bytes() == b"foreign"
    assert directory.exists()


def test_stale_socket_cleanup_retains_foreign_entries_and_parent(tree: Tree):
    directory = tree.path(str(RUN_DIR))
    foreign = directory / "foreign"
    foreign.write_text("retain")
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(f"/proc/self/fd/{fd}/control.sock")
            (directory / "control.sock").chmod(0o660)
        runtime._remove_runtime(tree, str(RUN_DIR), os.getgid(), ("control.sock",), socket_file=True)
        assert not (directory / "control.sock").exists()
        assert foreign.read_text() == "retain"
        assert directory.parent.stat().st_mode & 0o777 == 0o711
    finally:
        os.close(fd)


def test_regular_foreign_file_is_not_treated_as_stale_socket(tree: Tree):
    target = tree.path(str(CONTROL_SOCK))
    target.write_text("foreign")
    target.chmod(0o660)
    with pytest.raises(OSError, match="artifact"):
        runtime._remove_runtime(tree, str(RUN_DIR), os.getgid(), ("control.sock",), socket_file=True)
    assert target.read_text() == "foreign"


@pytest.mark.parametrize("proof", [False, None])
def test_unknown_or_nonempty_proof_blocks_every_cleanup_mutation(proof):
    with (patch.object(runtime.os, "geteuid", return_value=0),
          patch.object(runtime, "empty", return_value=proof),
          patch.object(runtime, "LeaseManager") as pages,
          patch.object(runtime, "_remove_runtime") as remove):
        assert runtime.cleanup() == 1
        pages.assert_not_called()
        remove.assert_not_called()


def test_page_cleanup_warning_preserves_runtime_evidence():
    pages = Mock()
    pages.cleanup.return_value = SimpleNamespace(warning="empty proof changed")
    with (patch.object(runtime.os, "geteuid", return_value=0),
          patch.object(runtime, "empty", return_value=True),
          patch.object(runtime, "LeaseManager", return_value=pages),
          patch.object(runtime, "_remove_runtime") as remove):
        assert runtime.cleanup() == 1
        remove.assert_not_called()


def test_nonroot_cleanup_never_queries_or_mutates():
    with (patch.object(runtime.os, "geteuid", return_value=1000),
          patch.object(runtime, "empty") as empty,
          patch.object(runtime, "LeaseManager") as pages,
          patch.object(runtime, "_remove_runtime") as remove):
        assert runtime.cleanup() == 1
        empty.assert_not_called()
        pages.assert_not_called()
        remove.assert_not_called()


def test_vm_fixture_handles_initial_stop_before_sentinel_creation(monkeypatch, tmp_path: Path):
    monkeypatch.syspath_prepend(str(Path("qa").resolve()))
    spec = importlib.util.spec_from_file_location("layout_acceptance", Path("qa/runtime_layout_acceptance.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fake = SimpleNamespace(result={})
    module.LayoutAcceptance.foreign_preserved(fake)
    marker = tmp_path / "foreign"
    marker.write_text("foreign runtime-layout fixture\n")
    fake.result["foreign"] = [str(marker)]
    module.LayoutAcceptance.foreign_preserved(fake)
    marker.write_text("changed")
    with pytest.raises(AssertionError):
        module.LayoutAcceptance.foreign_preserved(fake)
