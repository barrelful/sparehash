"""Descriptor-safe extraction and removal of release archives inside a managed root."""

from __future__ import annotations

import os
import stat
import tarfile
from pathlib import Path
from typing import Final

DIRECTORY_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
DIR_MODE: Final = 0o755
FILE_MODE: Final = 0o644
BINARY_MODE: Final = 0o755
COPY_BLOCK: Final = 1024 * 1024
MAX_ARCHIVE_BYTES: Final = 128 * 1024 * 1024
MAX_ENTRY_BYTES: Final = 32 * 1024 * 1024
MAX_EXPANDED_BYTES: Final = 256 * 1024 * 1024
MAX_ENTRIES: Final = 4096


class ArchiveError(Exception):
    """Rejected or unreadable release archive; any partial version tree is removed."""

    detail: str

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def extract_archive(archive: Path, parent_fd: int, name: str, uid: int) -> None:
    """Create ``name`` under the pinned parent and fill it from a verified tar.

    Only regular files and directories are created. Any rejection removes the
    whole freshly created version directory so no partial tree is ever adopted.
    """
    os.mkdir(name, DIR_MODE, dir_fd=parent_fd)
    version_fd = os.open(name, DIRECTORY_FLAGS, dir_fd=parent_fd)
    try:
        info = os.fstat(version_fd)
        if info.st_uid != uid or info.st_mode & 0o022:
            raise ArchiveError("version directory is not protected")
        _extract_into(archive, version_fd, name)
    except BaseException:
        os.close(version_fd)
        remove_tree(parent_fd, name)
        raise
    os.close(version_fd)


def _extract_into(archive: Path, version_fd: int, prefix: str) -> None:
    with os.fdopen(os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_ARCHIVE_BYTES:
            raise ArchiveError("archive is not a bounded regular file")
        with tarfile.open(fileobj=stream, mode="r:*") as tar:
            _extract_members(tar, version_fd, prefix)


def _extract_members(tar: tarfile.TarFile, version_fd: int, prefix: str) -> None:
    members = tar.getmembers()
    if len(members) > MAX_ENTRIES:
        raise ArchiveError("archive entry count exceeds budget")
    cache: dict[str, int] = {}
    held: list[int] = []
    seen: set[str] = set()
    expanded = 0
    try:
        for member in members:
            relative = _safe_relative(member.name, prefix, is_dir=member.isdir())
            if relative is None:
                raise ArchiveError(f"unsafe archive member {member.name!r}")
            if member.issym() or member.islnk() or member.ischr() or member.isblk() or member.isfifo() or member.issparse():
                raise ArchiveError(f"non-regular archive member {member.name!r}")
            if member.isdir():
                if relative in seen:
                    raise ArchiveError(f"duplicate archive member {member.name!r}")
                seen.add(relative)
                if relative:
                    _ = _subdir(cache, held, version_fd, relative)
                continue
            if not member.isfile():
                raise ArchiveError(f"unsupported archive member type {member.name!r}")
            if relative in seen or member.size > MAX_ENTRY_BYTES:
                raise ArchiveError(f"duplicate or oversized archive member {member.name!r}")
            seen.add(relative)
            expanded += member.size
            if expanded > MAX_EXPANDED_BYTES:
                raise ArchiveError("expanded archive exceeds size budget")
            _write_member(tar, member, version_fd, cache, held, relative)
    finally:
        for fd in reversed(held):
            os.close(fd)


def _subdir(cache: dict[str, int], held: list[int], root_fd: int, relative: str) -> int:
    if relative in cache:
        return cache[relative]
    parent_rel, _, name = relative.rpartition("/")
    parent = root_fd if not parent_rel else _subdir(cache, held, root_fd, parent_rel)
    try:
        fd = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
    except FileNotFoundError:
        os.mkdir(name, DIR_MODE, dir_fd=parent)
        fd = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
    held.append(fd)
    cache[relative] = fd
    return fd


def _write_member(tar: tarfile.TarFile, member: tarfile.TarInfo, version_fd: int,
                  cache: dict[str, int], held: list[int], relative: str) -> None:
    parent_rel, _, leaf = relative.rpartition("/")
    parent_fd = version_fd if not parent_rel else _subdir(cache, held, version_fd, parent_rel)
    source = tar.extractfile(member)
    if source is None:
        raise ArchiveError(f"archive member {member.name!r} has no readable data")
    mode = BINARY_MODE if leaf == "xmrig" else FILE_MODE
    target = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent_fd)
    try:
        os.fchmod(target, mode)
        with os.fdopen(target, "wb", closefd=False) as sink:
            written = 0
            while block := source.read(COPY_BLOCK):
                written += len(block)
                if written > MAX_ENTRY_BYTES:
                    raise ArchiveError(f"archive member {member.name!r} exceeds size limit")
                _ = sink.write(block)
            if written != member.size:
                raise ArchiveError(f"archive member {member.name!r} is truncated")
    finally:
        os.close(target)


def _safe_relative(name: str, prefix: str, *, is_dir: bool) -> str | None:
    if not name or name.startswith("/") or "\\" in name:
        return None
    parts = name.split("/")
    if is_dir and parts and parts[-1] == "":
        parts = parts[:-1]
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    if parts[0] != prefix:
        return None
    return "/".join(parts[1:])


def remove_tree(parent_fd: int, name: str) -> None:
    """Recursively remove one entry without following symlinks; other names untouched."""
    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if stat.S_ISDIR(info.st_mode):
        child = os.open(name, DIRECTORY_FLAGS, dir_fd=parent_fd)
        try:
            with os.scandir(child) as entries:
                for entry in entries:
                    remove_tree(child, entry.name)
        finally:
            os.close(child)
        os.rmdir(name, dir_fd=parent_fd)
    else:
        os.unlink(name, dir_fd=parent_fd)
