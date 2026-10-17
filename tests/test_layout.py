# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Finite redirect grammar and descriptor pinning tests on private temp roots.

Every tree is synthetic under a pytest ``tmp_path`` anchor owned by the current
user, so no test resolves or touches a real ``/opt``, ``/usr/local`` or mount.
The 8 happy cases pin conventional and both exact Atomic redirect forms; the 8
failure cases refuse extra hops, loops, substituted targets, traversal, unsafe
modes and an unsupported descriptor capability.  ``test_rejects_redirect_races``
deterministically swaps the target, the link and an ancestor between phases and
checks that a foreign sentinel is never touched.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from sparehash import layout
from sparehash.layout import LayoutKind, validate_handle


def _make_dir(path: Path, mode: int = 0o755) -> Path:
    """Create a directory and chmod every new ancestor, beating the process umask."""
    new_directories: list[Path] = []
    current = path
    while not current.exists():
        new_directories.append(current)
        current = current.parent
    for directory in reversed(new_directories):
        directory.mkdir()
    for directory in [*reversed(new_directories), path]:
        directory.chmod(mode)
    return path


def _conventional(root: Path) -> None:
    _make_dir(root / "opt")
    _make_dir(root / "usr" / "local")


def _atomic_relative(root: Path) -> None:
    _make_dir(root / "var" / "opt")
    _make_dir(root / "var" / "usrlocal")
    _make_dir(root / "usr")
    (root / "opt").symlink_to("var/opt")
    (root / "usr" / "local").symlink_to("../var/usrlocal")


def _atomic_absolute(root: Path) -> None:
    _make_dir(root / "var" / "opt")
    _make_dir(root / "var" / "usrlocal")
    _make_dir(root / "usr")
    (root / "opt").symlink_to("/var/opt")
    (root / "usr" / "local").symlink_to("/var/usrlocal")


def _probe(root: Path) -> layout.Layout:
    return layout.probe(root, uid=os.getuid())


def _expect_error(root: Path, *codes: str) -> None:
    with pytest.raises(layout.LayoutError) as caught:
        _ = _probe(root)
    assert all(code in caught.value.reason_codes for code in codes), caught.value.reason_codes


# --------------------------------------------------------------------------- happy


@pytest.mark.parametrize(
    ("builder", "kind", "opt_name", "local_name"),
    [
        (_conventional, LayoutKind.CONVENTIONAL, "opt", "usr/local"),
        (_atomic_relative, LayoutKind.ATOMIC_OPT_VAROPT_USRLOCAL, "var/opt", "var/usrlocal"),
        (_atomic_absolute, LayoutKind.ATOMIC_OPT_VAROPT_USRLOCAL, "var/opt", "var/usrlocal"),
    ],
    ids=["conventional", "atomic-relative", "atomic-absolute"],
)
def test_accepts_known_layouts(builder: Callable[[Path], None], kind: LayoutKind,
                               opt_name: str, local_name: str, tmp_path: Path) -> None:
    builder(tmp_path)
    pinned = _probe(tmp_path)
    try:
        assert pinned.kind is kind
        assert pinned.opt_physical == tmp_path / opt_name
        assert pinned.local_physical == tmp_path / local_name
        assert set(pinned.descriptor_handles) >= {"root", "opt", "local"}
        assert len(pinned.identity) == 32
        # Descriptor/path identity: each handle must agree with its physical path.
        for path in (pinned.opt_physical, pinned.local_physical):
            handle = pinned.open_handle(path)
            assert validate_handle(handle, path)
        assert os.fstat(pinned.descriptor_handles["opt"]).st_ino == os.stat(
            pinned.opt_physical, follow_symlinks=False).st_ino
        assert os.fstat(pinned.descriptor_handles["local"]).st_ino == os.stat(
            pinned.local_physical, follow_symlinks=False).st_ino
    finally:
        pinned.close()


def test_conventional_pins_real_directories(tmp_path: Path) -> None:
    _conventional(tmp_path)
    pinned = _probe(tmp_path)
    try:
        assert pinned.kind is LayoutKind.CONVENTIONAL
        assert pinned.reason_codes == ("opt-conventional", "local-conventional")
        assert pinned.opt_physical == tmp_path / "opt"
        assert pinned.local_physical == tmp_path / "usr" / "local"
        assert os.fstat(pinned.descriptor_handles["opt"]).st_ino == os.stat(
            pinned.opt_physical, follow_symlinks=False).st_ino
    finally:
        pinned.close()


def test_relative_redirect_selects_var_roots(tmp_path: Path) -> None:
    _atomic_relative(tmp_path)
    pinned = _probe(tmp_path)
    try:
        assert pinned.kind is LayoutKind.ATOMIC_OPT_VAROPT_USRLOCAL
        assert pinned.reason_codes == ("opt-redirect-relative", "local-redirect-relative")
        assert pinned.opt_physical == tmp_path / "var" / "opt"
        assert pinned.local_physical == tmp_path / "var" / "usrlocal"
    finally:
        pinned.close()


def test_absolute_redirect_selects_var_roots(tmp_path: Path) -> None:
    _atomic_absolute(tmp_path)
    pinned = _probe(tmp_path)
    try:
        assert pinned.kind is LayoutKind.ATOMIC_OPT_VAROPT_USRLOCAL
        assert pinned.reason_codes == ("opt-redirect-absolute", "local-redirect-absolute")
        assert pinned.opt_physical == tmp_path / "var" / "opt"
        assert pinned.local_physical == tmp_path / "var" / "usrlocal"
    finally:
        pinned.close()


def test_identity_is_stable_without_change(tmp_path: Path) -> None:
    _atomic_relative(tmp_path)
    first = _probe(tmp_path)
    second = _probe(tmp_path)
    try:
        assert first.identity == second.identity
        assert len(first.identity) == 32
    finally:
        first.close()
        second.close()


def test_identity_changes_when_probe_content_changes(tmp_path: Path) -> None:
    _atomic_relative(tmp_path)
    probe_file = tmp_path / "var" / "opt" / "identity-probe"
    probe_file.write_text("first", encoding="utf-8")
    first = _probe(tmp_path)
    probe_file.write_text("second-and-longer", encoding="utf-8")
    second = _probe(tmp_path)
    try:
        assert first.identity != second.identity
    finally:
        first.close()
        second.close()


def test_open_handle_returns_scoped_pinned_descriptor(tmp_path: Path) -> None:
    _atomic_relative(tmp_path)
    pinned = _probe(tmp_path)
    try:
        handle = pinned.open_handle(pinned.opt_physical)
        assert stat.S_ISDIR(os.fstat(handle).st_mode)
        assert validate_handle(handle, pinned.opt_physical)
        (tmp_path / "var" / "opt" / "sentinel").write_text("pinned", encoding="utf-8")
        child = os.open("sentinel", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=handle)
        try:
            assert os.read(child, 64) == b"pinned"
        finally:
            os.close(child)
    finally:
        pinned.close()


def test_open_handle_reissue_closes_previous(tmp_path: Path) -> None:
    _atomic_relative(tmp_path)
    pinned = _probe(tmp_path)
    try:
        first = pinned.open_handle(pinned.opt_physical)
        second = pinned.open_handle(pinned.opt_physical)
        assert first != second
        with pytest.raises(OSError):
            _ = os.fstat(first)
        assert validate_handle(second, pinned.opt_physical)
    finally:
        pinned.close()


def test_validate_handle_accepts_only_its_pinned_path(tmp_path: Path) -> None:
    _conventional(tmp_path)
    pinned = _probe(tmp_path)
    try:
        handle = pinned.descriptor_handles["local"]
        assert validate_handle(handle, pinned.local_physical)
        assert not validate_handle(handle, pinned.opt_physical)
    finally:
        pinned.close()


def test_close_is_idempotent(tmp_path: Path) -> None:
    _conventional(tmp_path)
    pinned = _probe(tmp_path)
    pinned.close()
    assert pinned.descriptor_handles == {}
    pinned.close()
    assert pinned.descriptor_handles == {}


# ------------------------------------------------------------------------- failure


def test_rejects_redirect_races(tmp_path: Path) -> None:
    # Phase 1: a pinned handle keeps the original target when the directory is
    # replaced; the foreign sentinel is never touched by the pinned operation.
    target_root = tmp_path / "target"
    _make_dir(target_root)
    _atomic_relative(target_root)
    (target_root / "var" / "opt" / "foreign-sentinel").write_text("original", encoding="utf-8")
    pinned = _probe(target_root)
    try:
        handle = pinned.open_handle(pinned.opt_physical)
        assert validate_handle(handle, pinned.opt_physical)
        replacement = target_root / "var" / "opt.replacement"
        _make_dir(replacement)
        os.rename(pinned.opt_physical, target_root / "var" / "opt.original")
        os.rename(replacement, pinned.opt_physical)
        assert not validate_handle(handle, pinned.opt_physical)
        sentinel_fd = os.open("foreign-sentinel", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=handle)
        try:
            assert os.read(sentinel_fd, 64) == b"original"
        finally:
            os.close(sentinel_fd)
        assert not (pinned.opt_physical / "foreign-sentinel").exists()
    finally:
        pinned.close()

    # Phase 2: a replaced /opt link refuses on re-probe instead of resolving.
    link_root = tmp_path / "link"
    _make_dir(link_root)
    _atomic_relative(link_root)
    (link_root / "opt").unlink()
    (link_root / "opt").symlink_to("/var/other")
    _expect_error(link_root, "opt-target-unsupported")

    # Phase 3: a re-linked ancestor refuses rather than silently following it.
    ancestor_root = tmp_path / "ancestor"
    _make_dir(ancestor_root)
    _atomic_relative(ancestor_root)
    os.rename(ancestor_root / "var", ancestor_root / "var.real")
    (ancestor_root / "var").symlink_to("var.real")
    _expect_error(ancestor_root, "redirect-loop")


def test_rejects_extra_hop(tmp_path: Path) -> None:
    _make_dir(tmp_path / "var")
    _make_dir(tmp_path / "var" / "real-opt")
    (tmp_path / "var" / "opt").symlink_to("real-opt")
    (tmp_path / "opt").symlink_to("var/opt")
    _make_dir(tmp_path / "usr" / "local")
    _expect_error(tmp_path, "redirect-loop")


def test_rejects_unsupported_target_literal(tmp_path: Path) -> None:
    _make_dir(tmp_path / "var" / "opt")
    (tmp_path / "opt").symlink_to("var/opt/opt")
    _make_dir(tmp_path / "usr" / "local")
    _expect_error(tmp_path, "opt-target-unsupported")


def test_rejects_loop(tmp_path: Path) -> None:
    _make_dir(tmp_path / "var")
    (tmp_path / "opt").symlink_to("var/opt")
    (tmp_path / "var" / "opt").symlink_to("/opt")
    _make_dir(tmp_path / "usr" / "local")
    _expect_error(tmp_path, "redirect-loop")


def test_rejects_different_target(tmp_path: Path) -> None:
    _make_dir(tmp_path / "var" / "other")
    (tmp_path / "opt").symlink_to("/var/other")
    _make_dir(tmp_path / "usr" / "local")
    _expect_error(tmp_path, "opt-target-unsupported")


def test_rejects_dotdot_outside_literal(tmp_path: Path) -> None:
    _make_dir(tmp_path / "usr" / "opt")
    (tmp_path / "opt").symlink_to("usr/opt/..")
    _make_dir(tmp_path / "usr" / "local")
    _expect_error(tmp_path, "opt-target-unsupported")


def test_rejects_regular_file_prefix(tmp_path: Path) -> None:
    (tmp_path / "opt").write_text("not-a-directory", encoding="utf-8")
    _make_dir(tmp_path / "usr" / "local")
    _expect_error(tmp_path, "opt-not-directory")


def test_rejects_unsafe_directory_mode(tmp_path: Path) -> None:
    _make_dir(tmp_path / "opt", mode=0o777)
    _make_dir(tmp_path / "usr" / "local")
    _expect_error(tmp_path, "opt-untrusted")


def test_rejects_unsafe_ancestor_mode(tmp_path: Path) -> None:
    _make_dir(tmp_path / "var" / "opt")
    _make_dir(tmp_path / "var", mode=0o775)
    _make_dir(tmp_path / "usr" / "local")
    (tmp_path / "opt").symlink_to("/var/opt")
    _expect_error(tmp_path, "opt-untrusted")


def test_rejects_foreign_handle_scope(tmp_path: Path) -> None:
    _conventional(tmp_path)
    pinned = _probe(tmp_path)
    try:
        with pytest.raises(layout.LayoutError) as caught:
            _ = pinned.open_handle(tmp_path / "etc")
        assert "unscoped-handle" in caught.value.reason_codes
    finally:
        pinned.close()


def test_inconclusive_without_descriptor_capability(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    _conventional(tmp_path)
    monkeypatch.setattr(
        layout, "_capabilities",
        lambda: layout.DescriptorCapabilities(nofollow=False, dir_fd=True))
    with pytest.raises(layout.LayoutInconclusiveError) as caught:
        _ = _probe(tmp_path)
    assert "descriptor-capability" in caught.value.reason_codes
