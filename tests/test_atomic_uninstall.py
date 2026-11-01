"""Atomic-layout-aware uninstall: pinned ownership, bounded partial recovery.

The fixtures use a synthetic conventional tree and a synthetic Atomic tree where
the platform's ``/opt -> /var/opt`` redirect is represented by an OS symlink
outside the pinned physical root. Removal only ever touches receipt-owned
artifacts inside the physical root; OS links, foreign sentinels and the layout's
own parent directories are preserved. The retained-source layout module is not
on this baseline, so the physical root is supplied through ``PathTrust``.
"""

from __future__ import annotations

import errno
import io
import os
from collections.abc import Callable
from contextlib import redirect_stderr
from dataclasses import dataclass, field
from pathlib import Path
from typing import final
from unittest import mock

import pytest

from sparehash import cli_uninstall, install, lifecycle
from sparehash.cli_uninstall import (
    CONFIG,
    CONTROLLER_UNIT,
    MINER_UNIT,
    PROGRESS_PATH,
    SHIM,
)
from sparehash.deployment import Tree
from sparehash.install_ledger import Ledger
from sparehash.xmrig_ledger import PACKAGE_DIR, GroupIdentity, UserIdentity

UID = 996
GID = 12345
PACKAGE_LEAF = PACKAGE_DIR + "/alpha.py"
CONFIG_TOML = CONFIG[0]
REQUEST = "b" * 32

_SKELETON = (
    "opt", "opt/sparehash", "opt/sparehash/src", "opt/sparehash/src/sparehash",
    "usr", "usr/local", "usr/local/bin", "etc", "etc/sparehash", "etc/systemd",
    "etc/systemd/system", "var", "var/lib", "var/lib/sparehash",
)


def _forbidden(_command: object, /, *, timeout: float) -> object:
    _ = timeout
    raise AssertionError("uninstall must not call controller IPC")


def _matching_user(name: str) -> tuple[int, int] | None:
    return (UID, UID) if name == "xmrig" else None


def _matching_group(name: str) -> int | None:
    return GID if name == "sparehash" else None


def _empty_legacy_latch() -> None:
    return None


@final
@dataclass
class Harness:
    tree: Tree
    calls: list[tuple[str, ...]] = field(default_factory=list)
    empty: bool = True
    user_lookup: Callable[[str], tuple[int, int] | None] = _matching_user
    group_lookup: Callable[[str], int | None] = _matching_group
    errors: str = ""


def _prepare(root: Path) -> Tree:
    for name in _SKELETON:
        (root / name).mkdir(parents=True, exist_ok=True)
    root.chmod(0o755)
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts)):
        if path.is_dir():
            path.chmod(0o755)
    return Tree(root, os.getuid())


def _fixture(tmp_path: Path, layout: str) -> tuple[Tree, Path | None]:
    if layout == "conventional":
        tree = _prepare(tmp_path / "conv")
        os_link: Path | None = None
    else:
        base = tmp_path / "atomic"
        tree = _prepare(base / "var")
        _ = (base / "opt").symlink_to("var/opt")
        os_link = base / "opt"
    sentinel = tree.path("/opt/foreign-sentinel")
    _ = sentinel.write_bytes(b"foreign")
    sentinel.chmod(0o644)
    os.symlink("/dev/null", tree.path("/opt/os-link"))
    return tree, os_link


def _write(tree: Tree, name: str, data: bytes = b"x") -> None:
    path = tree.path(name)
    _ = path.write_bytes(data)
    path.chmod(0o644)


def _build(tree: Tree) -> None:
    for name in (PACKAGE_LEAF, SHIM, CONTROLLER_UNIT, MINER_UNIT, CONFIG_TOML):
        _write(tree, name)
    for name in CONFIG[1:]:
        _write(tree, name, b'{"http": {}}')
    tree.path(SHIM).chmod(0o755)
    ledger = Ledger(tree)
    ledger.initialize()
    install.bootstrap(ledger, ("/opt/sparehash", "/opt/sparehash/src", PACKAGE_DIR, PACKAGE_LEAF))
    receipts = (tree.receipt(SHIM, True),
                tree.receipt(CONTROLLER_UNIT, True),
                tree.receipt(MINER_UNIT, True),
                *(tree.receipt(path, True) for path in CONFIG),
                UserIdentity("user", "xmrig", UID, UID, True),
                GroupIdentity("group", "sparehash", GID, True))
    ledger.applied(0, receipts)


def _latch(tree: Tree) -> Path:
    return tree.path("/var/lib/sparehash/lifecycle.json")


def _run(harness: Harness, *, purge: bool = True) -> int:
    def run(argv: tuple[str, ...]) -> None:
        harness.calls.append(argv)

    infrastructure = cli_uninstall.Infrastructure(
        run=run,
        cgroup_empty=lambda: harness.empty,
        free_hugepages=_empty_legacy_latch,
        unmerge=lambda _name: None,
        user_lookup=harness.user_lookup,
        group_lookup=harness.group_lookup,
    )
    errors = io.StringIO()
    with redirect_stderr(errors):
        result = cli_uninstall.uninstall(purge=purge, geteuid=lambda: 0, trust=harness.tree.trust,
                                         client=_forbidden, infrastructure=infrastructure,
                                         request_id=REQUEST, lifecycle_path=_latch(harness.tree))
    harness.errors = errors.getvalue()
    return result


@pytest.mark.parametrize("layout", ["conventional", "atomic"], ids=["conventional", "atomic"])
def test_accepts_offline_owned_removal(tmp_path: Path, layout: str) -> None:
    tree, os_link = _fixture(tmp_path, layout)
    harness = Harness(tree)
    _build(tree)

    result = _run(harness, purge=True)

    assert result == 0
    for owned in (PACKAGE_LEAF, SHIM, CONTROLLER_UNIT, MINER_UNIT, *CONFIG):
        assert not tree.path(owned).exists(), owned
    assert tree.path("/opt/foreign-sentinel").read_bytes() == b"foreign"
    assert os.path.islink(tree.path("/opt/os-link"))
    assert tree.path("/opt").is_dir()
    if os_link is not None:
        assert os_link.is_symlink(), "platform OS redirect must survive removal"
    assert lifecycle.is_retired(_latch(tree))
    assert not tree.path(PROGRESS_PATH).exists()


def _check_retained(tree: Tree, *survivors: str) -> None:
    for name in survivors:
        assert tree.path(name).exists(), name
    assert lifecycle.is_retired(_latch(tree))


def _case_foreign_symlink_substitution(harness: Harness, _tmp: Path) -> None:
    foreign = harness.tree.path("/opt/foreign-sentinel")
    link = harness.tree.path(SHIM)
    link.unlink()
    os.symlink(str(foreign), link)

    result = _run(harness)

    assert result == 1
    assert foreign.read_bytes() == b"foreign"
    assert link.is_symlink()
    _check_retained(harness.tree)


def _case_foreign_content_tamper(harness: Harness, _tmp: Path) -> None:
    config = harness.tree.path(CONFIG_TOML)
    _ = config.write_bytes(b"tampered")

    result = _run(harness)

    assert result == 1
    assert config.read_bytes() == b"tampered"
    _check_retained(harness.tree, CONFIG_TOML)


def _case_unrecorded_child_refused(harness: Harness, _tmp: Path) -> None:
    extra_name = PACKAGE_DIR + "/foreign.py"
    extra = harness.tree.path(extra_name)
    _ = extra.write_bytes(b"foreign")
    extra.chmod(0o644)

    result = _run(harness)

    assert result == 1
    assert extra.exists()
    _check_retained(harness.tree, PACKAGE_LEAF, extra_name)


def _case_identity_change_during_delete(harness: Harness, _tmp: Path) -> None:
    shim = harness.tree.path(SHIM)
    shim.chmod(0o600)

    result = _run(harness)

    assert result == 1
    _check_retained(harness.tree, SHIM)


def _case_partial_removal_crash(harness: Harness, _tmp: Path) -> None:
    with mock.patch.object(cli_uninstall, "remove_owned_versions", side_effect=OSError("injected crash")):
        first = _run(harness)

    assert first == 1
    assert harness.tree.path(PACKAGE_LEAF).exists()
    assert harness.tree.path(SHIM).exists()
    assert lifecycle.is_retired(_latch(harness.tree))

    second = _run(harness)

    assert second == 0
    assert not harness.tree.path(SHIM).exists()
    assert not harness.tree.path(PROGRESS_PATH).exists()


def _case_mid_uninstall_enospc(harness: Harness, _tmp: Path) -> None:
    original = cli_uninstall.RemovalJournal.save
    counter = {"calls": 0}

    def flaky(journal: cli_uninstall.RemovalJournal) -> None:
        counter["calls"] += 1
        if counter["calls"] == 3:
            raise OSError(errno.ENOSPC, "injected no space")
        original(journal)

    with mock.patch.object(cli_uninstall.RemovalJournal, "save", flaky):
        first = _run(harness)

    assert first == 1
    assert lifecycle.is_retired(_latch(harness.tree))

    second = _run(harness)

    assert second == 0
    assert not harness.tree.path(PACKAGE_LEAF).exists()
    assert not harness.tree.path(SHIM).exists()


def _case_journal_overflow_refused(harness: Harness, _tmp: Path) -> None:
    with mock.patch.object(cli_uninstall, "MAX_PROGRESS_ITEMS", 0):
        result = _run(harness)

    assert result == 1
    _check_retained(harness.tree, PACKAGE_LEAF, SHIM)


def _case_corrupt_journal_refused(harness: Harness, _tmp: Path) -> None:
    journal = harness.tree.path(PROGRESS_PATH)
    _ = journal.write_bytes(b"{ not a journal")
    journal.chmod(0o600)

    result = _run(harness)

    assert result == 1
    assert harness.calls == []
    assert not _latch(harness.tree).exists(), "a corrupt journal refuses before retirement"
    assert harness.tree.path(PACKAGE_LEAF).exists()


def _case_same_name_foreign_account(harness: Harness, _tmp: Path) -> None:
    harness.user_lookup = lambda name: (4242, 4242) if name == "xmrig" else None

    result = _run(harness)

    assert result == 1
    assert ("/usr/sbin/userdel", "xmrig") not in harness.calls
    assert harness.tree.path(PACKAGE_LEAF).exists()
    _check_retained(harness.tree)


def _case_repeat_uninstall_semantics(harness: Harness, _tmp: Path) -> None:
    first = _run(harness, purge=True)
    assert first == 0

    second = _run(harness, purge=True)

    assert second == 1
    assert not harness.tree.path(PROGRESS_PATH).exists()
    assert lifecycle.is_retired(_latch(harness.tree))


_CASES: dict[str, Callable[[Harness, Path], None]] = {
    "foreign_symlink_substitution": _case_foreign_symlink_substitution,
    "foreign_content_tamper": _case_foreign_content_tamper,
    "unrecorded_child_refused": _case_unrecorded_child_refused,
    "identity_change_during_delete": _case_identity_change_during_delete,
    "partial_removal_crash": _case_partial_removal_crash,
    "mid_uninstall_enospc": _case_mid_uninstall_enospc,
    "journal_overflow_refused": _case_journal_overflow_refused,
    "corrupt_journal_refused": _case_corrupt_journal_refused,
    "same_name_foreign_account": _case_same_name_foreign_account,
    "repeat_uninstall_semantics": _case_repeat_uninstall_semantics,
}


@pytest.mark.parametrize("case", sorted(_CASES), ids=sorted(_CASES))
def test_retains_foreign_artifacts_and_partial_retirement(tmp_path: Path, case: str) -> None:
    tree, _os_link = _fixture(tmp_path, "conventional")
    harness = Harness(tree)
    _build(tree)

    _CASES[case](harness, tmp_path)
