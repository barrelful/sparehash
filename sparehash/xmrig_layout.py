"""Descriptor-safe mutations of the manifest-owned /opt/xmrig managed tree."""

from __future__ import annotations

import os
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Final, assert_never
from uuid import uuid4

from .xmrig_archive import DIR_MODE, DIRECTORY_FLAGS, remove_tree
from .xmrig_paths import MANIFEST_ROOT, MARKER_NAME, Manifest, PathTrust
from .xmrig_manifest import format_manifest as format_manifest
from .install_manifest import ManifestStore

CURRENT_NAME: Final = "current"
BINARY_NAME: Final = "xmrig"
MANIFEST_MODE: Final = 0o640


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
        created = False
        parent_fd = fd
        try:
            fd = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
        except FileNotFoundError:
            if not create:
                return None, parent_fd
            os.mkdir(part, DIR_MODE, dir_fd=fd)
            created = True
            fd = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
        except OSError as exc:
            raise CollisionError("root", f"managed root path is unsafe: {exc}") from exc
        _ = stack.callback(os.close, fd)
        _require_owned(fd, trust.uid, "managed root")
        if created:
            os.fchmod(fd, DIR_MODE)
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


def _unlink(fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=fd)
    except FileNotFoundError:
        return


@dataclass(frozen=True, slots=True)
class Publication:
    manifest: Manifest
    version: str


def publish(root_fd: int, store: ManifestStore, publication: Publication) -> None:
    """Publish manifest first, then the relative current link; a failed link restores it."""
    _ = format_manifest(publication.manifest)
    prior = store.read()
    prior_link = current_target(root_fd)
    temp_link = f".{CURRENT_NAME}.tmp-{uuid4().hex}"
    os.symlink(publication.version, temp_link, dir_fd=root_fd)
    try:
        store.write(publication.manifest)
        changed = False
        try:
            os.rename(temp_link, CURRENT_NAME, src_dir_fd=root_fd, dst_dir_fd=root_fd)
            changed = True
            os.fsync(root_fd)
        except (OSError, KeyboardInterrupt) as exc:
            if changed:
                if prior_link is None:
                    _unlink(root_fd, CURRENT_NAME)
                else:
                    os.symlink(prior_link, temp_link, dir_fd=root_fd)
                    os.rename(temp_link, CURRENT_NAME, src_dir_fd=root_fd, dst_dir_fd=root_fd)
                os.fsync(root_fd)
            store.restore(prior)
            match exc:
                case KeyboardInterrupt():
                    raise
                case OSError():
                    raise InstallError("publish", f"cannot publish current link: {exc}") from exc
            assert_never(exc)
    finally:
        _unlink(root_fd, temp_link)


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
