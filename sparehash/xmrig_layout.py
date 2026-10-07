"""Descriptor-safe mutations of the manifest-owned /opt/xmrig managed tree."""

from __future__ import annotations

import os
import stat
from contextlib import ExitStack
from pathlib import Path
from typing import Final

from .xmrig_archive import DIR_MODE, DIRECTORY_FLAGS, remove_tree
from .xmrig_paths import MANIFEST_ROOT, MARKER_NAME, Manifest, PathTrust
from .xmrig_manifest import format_manifest as format_manifest

CURRENT_NAME: Final = "current"
BINARY_NAME: Final = "xmrig"
MANIFEST_MODE: Final = 0o640
MAX_MANIFEST_BYTES: Final = 64 * 1024


class InstallError(Exception):
    """Typed install failure with a machine-consumable stage."""

    stage: str
    detail: str

    def __init__(self, stage: str, detail: str) -> None:
        self.stage = stage
        self.detail = detail
        super().__init__(f"{stage}: {detail}")


class CollisionError(InstallError):
    """Untrusted, unrecorded or corrupt content already occupies a managed path."""


def _require_owned(fd: int, uid: int, what: str) -> None:
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
        raise CollisionError("ownership", f"{what} is not a protected owned directory")


def open_managed_root(trust: PathTrust, stack: ExitStack, *, create: bool) -> tuple[int | None, int]:
    """Pin the managed root and its parent; create only when explicitly allowed."""
    fd = os.open(trust.root, DIRECTORY_FLAGS)
    _ = stack.callback(os.close, fd)
    parent_fd = fd
    for part in trust.managed_root.relative_to(trust.root).parts:
        parent_fd = fd
        try:
            fd = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
        except FileNotFoundError:
            if not create:
                return None, parent_fd
            os.mkdir(part, DIR_MODE, dir_fd=fd)
            fd = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
        except OSError as exc:
            raise CollisionError("root", f"managed root path is unsafe: {exc}") from exc
        _require_owned(fd, trust.uid, "managed root")
    return fd, parent_fd


def ensure_marker(root_fd: int, uid: int) -> None:
    """Create the root marker once, or validate the existing one is ours and intact."""
    try:
        fd = os.open(MARKER_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd)
    except FileNotFoundError:
        marker = os.open(MARKER_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         MANIFEST_MODE, dir_fd=root_fd)
        try:
            os.fchmod(marker, MANIFEST_MODE)
            _ = os.write(marker, MANIFEST_ROOT.encode("utf-8"))
        finally:
            os.close(marker)
        return
    except OSError as exc:
        raise CollisionError("marker", f"managed marker is unsafe: {exc}") from exc
    try:
        info = os.fstat(fd)
        with os.fdopen(os.dup(fd), "rb") as source:
            content = source.read(1024).decode("utf-8", "replace")
    finally:
        os.close(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022
            or content.strip() != MANIFEST_ROOT):
        raise CollisionError("marker", "managed marker is missing, foreign or malformed")


def current_target(root_fd: int) -> str | None:
    try:
        return os.readlink(CURRENT_NAME, dir_fd=root_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CollisionError("current", f"existing current is not a symlink: {exc}") from exc


def dir_exists(root_fd: int, name: str) -> bool:
    try:
        fd = os.open(name, DIRECTORY_FLAGS, dir_fd=root_fd)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise CollisionError("collision", f"unrecorded path {name!r} is unsafe: {exc}") from exc
    os.close(fd)
    return True


def verify_binary(root_fd: int, version_name: str, uid: int) -> None:
    version_fd = os.open(version_name, DIRECTORY_FLAGS, dir_fd=root_fd)
    try:
        _require_owned(version_fd, uid, f"version {version_name}")
        file_fd = os.open(BINARY_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=version_fd)
        try:
            info = os.fstat(file_fd)
        finally:
            os.close(file_fd)
    except OSError as exc:
        raise InstallError("binary", f"{version_name}/xmrig is missing or unsafe: {exc}") from exc
    finally:
        os.close(version_fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
        raise InstallError("binary", f"{version_name}/xmrig is missing or unprotected")


def require_version_dir(root_fd: int, name: str, uid: int) -> bool:
    """True when a manifest-owned version directory exists and is protected."""
    try:
        fd = os.open(name, DIRECTORY_FLAGS, dir_fd=root_fd)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise InstallError("prune", f"recorded version {name!r} is unsafe: {exc}") from exc
    try:
        _require_owned(fd, uid, f"version {name}")
    finally:
        os.close(fd)
    return True


def _open_dir(stack: ExitStack, path: Path, anchor: Path, uid: int) -> int:
    fd = os.open(anchor, DIRECTORY_FLAGS)
    _ = stack.callback(os.close, fd)
    for part in path.relative_to(anchor).parts:
        try:
            fd = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
        except FileNotFoundError:
            os.mkdir(part, DIR_MODE, dir_fd=fd)
            fd = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
        _require_owned(fd, uid, "manifest directory")
    return fd


def _read_prior(dir_fd: int, name: str) -> bytes | None:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        return stream.read(MAX_MANIFEST_BYTES + 1)


def _write_temp(dir_fd: int, name: str, data: bytes) -> str:
    temp = f".{name}.tmp-{os.getpid()}"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, MANIFEST_MODE, dir_fd=dir_fd)
    try:
        os.fchmod(fd, MANIFEST_MODE)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            _ = stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)
    return temp


def _unlink(fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=fd)
    except FileNotFoundError:
        return


def _restore_manifest(dir_fd: int, name: str, prior: bytes | None) -> None:
    if prior is None:
        _unlink(dir_fd, name)
        return
    temp = _write_temp(dir_fd, name, prior)
    os.rename(temp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)


def publish(root_fd: int, trust: PathTrust, manifest: Manifest, version_name: str) -> None:
    """Publish manifest first, then the relative current link; a failed link restores it."""
    data = format_manifest(manifest)
    manifest_path = trust.manifest_path
    with ExitStack() as stack:
        dir_fd = _open_dir(stack, manifest_path.parent, trust.root, trust.uid)
        prior = _read_prior(dir_fd, manifest_path.name)
        temp_manifest = _write_temp(dir_fd, manifest_path.name, data)
        temp_link = f".{CURRENT_NAME}.tmp-{os.getpid()}"
        _unlink(root_fd, temp_link)
        os.symlink(version_name, temp_link, dir_fd=root_fd)
        try:
            os.rename(temp_manifest, manifest_path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except OSError as exc:
            _unlink(dir_fd, temp_manifest)
            _unlink(root_fd, temp_link)
            raise InstallError("publish", f"cannot publish manifest: {exc}") from exc
        try:
            os.rename(temp_link, CURRENT_NAME, src_dir_fd=root_fd, dst_dir_fd=root_fd)
        except OSError as exc:
            _restore_manifest(dir_fd, manifest_path.name, prior)
            _unlink(root_fd, temp_link)
            raise InstallError("publish", f"cannot publish current link: {exc}") from exc


def prune(root_fd: int, old: Manifest, new: Manifest, protect: tuple[str | None, ...],
          retain: int, uid: int) -> tuple[str, ...]:
    """Remove only manifest-owned versions beyond the retained/protected set."""
    names = list(new.names)
    keep: set[str] = set(names[-retain:]) if retain > 0 else set()
    keep.update(name for name in protect if name is not None)
    pruned: list[str] = []
    for name in old.names:
        if name in keep:
            continue
        if require_version_dir(root_fd, name, uid):
            remove_tree(root_fd, name)
        pruned.append(name)
    return tuple(pruned)
