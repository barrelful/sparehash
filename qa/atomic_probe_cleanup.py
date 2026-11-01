"""Task-3 exclusive staging and identity-bound, no-follow removal of four objects."""

from __future__ import annotations

import os
import re
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TextIO

from qa.atomic_probe_evidence import RefusalError, read_raw

PATHS: Final = ("xmrig", "xmrig/task3", "xmrig/task3/xmrig", "xmrig/task3/config.json")
CONFIG: Final = b'{"autosave":false,"background":false,"donate-level":0,"cpu":{"enabled":true,"huge-pages":false},"opencl":false,"cuda":false,"randomx":{"1gb-pages":false,"rdmsr":false,"wrmsr":false},"pools":[{"url":"127.0.0.1:1","user":"TASK3-DUMMY","pass":"x","tls":false}],"http":{"enabled":true,"host":"127.0.0.1","port":38087,"restricted":true,"access-token":"0000000000000000000000000000000000000000000000000000000000000000"}}\n'


@dataclass(frozen=True, slots=True)
class Owned:
    path: str
    device: int
    inode: int
    mode: int
    uid: int

    @classmethod
    def capture(cls, path: str, info: os.stat_result) -> Owned:
        return cls(path, info.st_dev, info.st_ino, info.st_mode, info.st_uid)


def record_creation(stream: TextIO, owned: Owned) -> None:
    _ = stream.write(f"{owned.path}\t{owned.device}\t{owned.inode}\t{owned.mode}\t{owned.uid}\n")
    stream.flush()
    os.fsync(stream.fileno())


def pin_parent(stack: ExitStack, parent: Path) -> int:
    if not parent.is_absolute() or ".." in parent.parts:
        raise RefusalError("staging parent must be absolute without traversal")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    _ = stack.callback(os.close, fd)
    for component in parent.parts[1:]:
        fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        _ = stack.callback(os.close, fd)
    info = os.fstat(fd)
    if info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise RefusalError("staging parent is not controlled by caller")
    return fd


def create_staging(parent: Path, receipt: Path, binary: bytes) -> None:
    with ExitStack() as stack:
        parent_fd = pin_parent(stack, parent)
        receipt_fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        stream = stack.enter_context(os.fdopen(receipt_fd, "w", encoding="utf-8"))
        for path in PATHS:
            name = path.rsplit("/", 1)[-1]
            directory = path in PATHS[:2]
            if directory:
                os.mkdir(name, 0o755, dir_fd=parent_fd)
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            else:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
            _ = stack.callback(os.close, fd)
            os.fchmod(fd, 0o755 if directory else (0o555 if path == PATHS[2] else 0o444))
            record_creation(stream, Owned.capture(path, os.fstat(fd)))
            if directory:
                parent_fd = fd
            else:
                payload = memoryview(binary if path == PATHS[2] else CONFIG)
                while payload:
                    written = os.write(fd, payload)
                    if written <= 0:
                        raise RefusalError("staging write made no progress")
                    payload = payload[written:]
                os.fsync(fd)
        _ = stream.write("COMPLETE\n")
        stream.flush()
        os.fsync(stream.fileno())


def load_ownership(receipt: Path) -> tuple[tuple[Owned, ...], bool]:
    text = read_raw(receipt)
    if not text.endswith("\n"):
        raise RefusalError("empty/truncated ownership receipt")
    lines = text.splitlines()
    complete = lines[-1] == "COMPLETE"
    rows = lines[:-1] if complete else lines
    if not 1 <= len(rows) <= len(PATHS) or (complete and len(rows) != len(PATHS)):
        raise RefusalError("unknown ownership receipt extent")
    owned: list[Owned] = []
    for path, row in zip(PATHS, rows, strict=False):
        fields = row.split("\t")
        if len(fields) != 5 or fields[0] != path or any(not re.fullmatch(r"[0-9]+", value) for value in fields[1:]):
            raise RefusalError("malformed/unknown ownership receipt")
        item = Owned(path, *(int(value) for value in fields[1:]))
        if item.uid != os.geteuid() or item.inode <= 0:
            raise RefusalError("ownership receipt does not belong to caller")
        owned.append(item)
    return tuple(owned), complete


def check_entry(parent_fd: int, owned: Owned) -> None:
    info = os.stat(owned.path.rsplit("/", 1)[-1], dir_fd=parent_fd, follow_symlinks=False)
    if Owned.capture(owned.path, info) != owned:
        raise RefusalError(f"replaced owned entry: {owned.path}")
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise RefusalError(f"owned leaf acquired another link: {owned.path}")


def cleanup_staging(parent: Path, receipt: Path) -> bool:
    """Validate the whole original graph before unlink; missing objects block too."""
    try:
        owned, complete = load_ownership(receipt)
        with ExitStack() as stack:
            parent_fd = pin_parent(stack, parent)
            parents: dict[str, int] = {"": parent_fd}
            directories: dict[str, int] = {}
            for item in owned:
                prefix, _, name = item.path.rpartition("/")
                containing = parents[prefix]
                check_entry(containing, item)
                directory = item.path in PATHS[:2]
                if directory != stat.S_ISDIR(item.mode) or (not directory and not stat.S_ISREG(item.mode)):
                    raise RefusalError("ownership object type mismatch")
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                fd = os.open(name, flags | (os.O_DIRECTORY if directory else 0), dir_fd=containing)
                _ = stack.callback(os.close, fd)
                if Owned.capture(item.path, os.fstat(fd)) != item:
                    raise RefusalError("entry changed while pinning owned object")
                if directory:
                    parents[item.path] = fd
                    directories[item.path] = fd
            for prefix, fd in directories.items():
                expected = {item.path.rsplit("/", 1)[-1] for item in owned if item.path.rpartition("/")[0] == prefix}
                if set(os.listdir(fd)) != expected:
                    raise RefusalError("unreceipted child; cleanup retains all staging")
            for item in reversed(owned):
                containing = parents[item.path.rpartition("/")[0]]
                # Recheck live ancestry and entry through the same pinned parents.
                for ancestor in owned[:2]:
                    if ancestor.path in directories:
                        check_entry(parents[ancestor.path.rpartition("/")[0]], ancestor)
                check_entry(containing, item)
                name = item.path.rsplit("/", 1)[-1]
                if item.path in directories:
                    os.rmdir(name, dir_fd=containing)
                    del directories[item.path]
                else:
                    os.unlink(name, dir_fd=containing)
                os.fsync(containing)
        return complete
    except (OSError, ValueError, KeyError) as error:
        raise RefusalError(f"ownership cleanup incomplete: {error}") from error
