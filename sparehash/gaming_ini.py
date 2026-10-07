"""Lossless GameMode hook edits confined to pinned directory descriptors.

An owner can rename the pinned directory after the final comparison. Publication
then lands in that original inode, never its replacement; this is not a namespace
lock. Missing home/.config is deliberately skipped, not created by root.
"""

from __future__ import annotations

import configparser
import os
import re
import secrets
import stat
from contextlib import ExitStack
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

NAME: Final = "gamemode.ini"
BACKUP: Final = NAME + ".sparehash-bak"
DIR_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
READ_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
LIMIT: Final = 1024 * 1024
MARKER: Final = "# SpareHash-created GameMode configuration\n"
HOOKS: Final = {"start": "sg sparehash -c '/usr/local/bin/sparehash gaming-start'",
               "end": "sg sparehash -c '/usr/local/bin/sparehash gaming-end'"}


class UnsafePathError(Exception):
    def __init__(self, path: Path, reason: str) -> None:
        self.path: Path = path
        self.reason: str = reason
        super().__init__(f"{path}: {reason}")


@dataclass(frozen=True, slots=True)
class Home:
    """Trusted installer/NSS account destination, never a hook-supplied path."""

    path: Path
    uid: int
    gid: int

    def __post_init__(self) -> None:
        if (not self.path.is_absolute() or self.path == Path("/")
                or ".." in self.path.parts or self.path.anchor != "/"
                or "\x00" in str(self.path) or min(self.uid, self.gid) < 0):
            raise UnsafePathError(self.path, "invalid home or identity")


@dataclass(frozen=True, slots=True)
class EditReceipt:
    created: bool
    configuration: tuple[bytes, os.stat_result]
    backup: tuple[bytes, os.stat_result] | None = None


def _layout(lines: list[str]) -> tuple[int | None, int, list[tuple[int, str]]]:
    custom: int | None = None
    entries: list[tuple[int, str]] = []
    indent = 1000000
    for i, line in enumerate(lines):
        header = re.fullmatch(r"\s*\[([^]]+)\]\s*(?:[#;].*)?", line.strip())
        if header:
            if custom is not None:
                return custom, i, entries
            if header[1] == "custom":
                custom = i
            indent = 1000000
        elif custom is not None and line.strip() and not line.lstrip().startswith(("#", ";")):
            width = len(line) - len(line.lstrip())
            entry = re.match(r"^\s*([^\s=:#;][^=:]*?)\s*[=:]", line)
            if width <= indent and entry:
                entries.append((i, entry[1].strip().lower()))
                indent = width
    return custom, len(lines), entries


def transform(data: bytes, enabled: bool) -> bytes:
    """Edit only exact hook commands in custom start/end, retaining other text."""
    text = data.decode("utf-8")
    parser = configparser.RawConfigParser(interpolation=None, strict=True)
    parser.read_string(text)
    lines = text.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in text else "\n"
    _, end, entries = _layout(lines)
    removed: set[int] = set()
    for n, (start, key) in enumerate(entries):
        if key not in HOOKS:
            continue
        stop = entries[n + 1][0] if n + 1 < len(entries) else end
        entry = re.match(r"^(\s*[^=:]+[=:])(.*?)(\r?\n)?$", lines[start])
        if entry and entry[2].strip() == HOOKS[key]:
            lines[start] = entry[1] + newline
        removed.update(i for i in range(start + 1, stop) if lines[i].strip() == HOOKS[key])
    cleaned = [line for i, line in enumerate(lines) if i not in removed]
    if not enabled:
        result = "".join(cleaned)
        empty = MARKER + "[custom]\nstart=\nend=\n"
        return b"" if result == empty else result.encode("utf-8")
    if not data:
        cleaned = [MARKER, "[custom]\n"]
    custom, end, entries = _layout(cleaned)
    if custom is None:
        if cleaned and not cleaned[-1].endswith("\n"):
            cleaned[-1] += newline
        cleaned.append("[custom]" + newline)
        custom = len(cleaned) - 1
        end = len(cleaned)
    for key, hook in HOOKS.items():
        _, end, entries = _layout(cleaned)
        start = next((i for i, name in entries if name == key), None)
        insert = end
        indent = "    "
        if start is None:
            if insert and not cleaned[insert - 1].endswith("\n"):
                cleaned[insert - 1] += newline
            cleaned.insert(insert, key + "=" + newline)
            insert += 1
        else:
            indent = cleaned[start][:len(cleaned[start]) - len(cleaned[start].lstrip())] + "    "
            insert = next((i for i, _ in entries if i > start), end)
            if not cleaned[insert - 1].endswith("\n"):
                cleaned[insert - 1] += newline
        cleaned.insert(insert, indent + hook + newline)
    return "".join(cleaned).encode("utf-8")


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _pin(stack: ExitStack, home: Home) -> list[tuple[int, str, int]]:
    fd = os.open("/", DIR_FLAGS)
    _ = stack.callback(os.close, fd)
    if not stat.S_ISDIR(os.fstat(fd).st_mode):
        raise UnsafePathError(home.path, "non-directory root")
    chain: list[tuple[int, str, int]] = []
    for part in (*home.path.parts[1:], ".config"):
        child = os.open(part, DIR_FLAGS, dir_fd=fd)
        _ = stack.callback(os.close, child)
        if not stat.S_ISDIR(os.fstat(child).st_mode):
            raise UnsafePathError(home.path, "non-directory ancestor")
        chain.append((fd, part, child))
        fd = child
    return chain


def _read(directory: int, name: str) -> tuple[bytes, os.stat_result] | None:
    try:
        fd = os.open(name, READ_FLAGS, dir_fd=directory)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
            raise UnsafePathError(Path(name), "nonregular or oversized file")
        data = stream.read(LIMIT + 1)
        if len(data) > LIMIT or _signature(os.fstat(stream.fileno())) != _signature(info):
            raise UnsafePathError(Path(name), "file changed during read")
        return data, info


def check_identity(chain: list[tuple[int, str, int]], prior: tuple[bytes, os.stat_result] | None) -> None:
    """Descriptor-relative comparison only; writes never use these fresh opens."""
    with ExitStack() as stack:
        for parent, name, pinned in chain:
            try:
                fd = os.open(name, DIR_FLAGS, dir_fd=parent)
            except FileNotFoundError as exc:
                raise UnsafePathError(Path(name), "ancestor disappeared") from exc
            _ = stack.callback(os.close, fd)
            if _identity(os.fstat(fd)) != _identity(os.fstat(pinned)):
                raise UnsafePathError(Path(name), "ancestor replaced")
    current = _read(chain[-1][2], NAME)
    same = current is None and prior is None
    if current is not None and prior is not None:
        same = current[0] == prior[0] and _signature(current[1]) == _signature(prior[1])
    if not same:
        raise UnsafePathError(Path(NAME), "configuration replaced or changed")


def _write(directory: int, name: str, payload: tuple[bytes, os.stat_result]) -> os.stat_result:
    data, info = payload
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    with os.fdopen(fd, "wb") as stream:
        owned = os.fstat(stream.fileno())
        try:
            os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
            _ = stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except OSError:
            _cleanup(directory, name, owned)
            raise
        return os.fstat(stream.fileno())


def _cleanup(directory: int, name: str, owned: os.stat_result) -> None:
    try:
        current = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    if _identity(current) == _identity(owned):
        os.unlink(name, dir_fd=directory)


def edit(home: Home, enabled: bool, *, record: Callable[[EditReceipt], None] | None = None) -> bool:
    """Merge/unmerge using identical protection; return whether content changed."""
    try:
        with ExitStack() as stack:
            chain = _pin(stack, home)
            directory = chain[-1][2]
            prior = _read(directory, NAME)
            data = prior[0] if prior else b""
            updated = transform(data, enabled)
            backup: tuple[bytes, os.stat_result] | None = None
            if data == updated:
                check_identity(chain, prior)
                if record is not None and prior is not None:
                    record(EditReceipt(False, prior))
                return False
            check_identity(chain, prior)
            if prior and enabled and _read(directory, BACKUP) is None:
                try:
                    backup = (prior[0], _write(directory, BACKUP, prior))
                except FileExistsError:
                    if _read(directory, BACKUP) is None:
                        raise UnsafePathError(home.path, "backup disappeared")
            info = prior[1] if prior else os.stat_result((stat.S_IFREG | 0o644, 0, 0, 1, home.uid, home.gid, 0, 0, 0, 0))
            temp = ".gamemode.ini.sparehash-" + secrets.token_hex(16)
            owned = _write(directory, temp, (updated, info))
            published = False
            try:
                check_identity(chain, prior)
                current = os.stat(temp, dir_fd=directory, follow_symlinks=False)
                if _identity(current) != _identity(owned):
                    raise UnsafePathError(home.path, "temporary file replaced")
                if updated:
                    os.replace(temp, NAME, src_dir_fd=directory, dst_dir_fd=directory)
                    published = True
                    if record is not None:
                        record(EditReceipt(prior is None, (updated, owned), backup))
                else:
                    os.unlink(NAME, dir_fd=directory)
            finally:
                if not published:
                    _cleanup(directory, temp, owned)
            return True
    except (configparser.Error, UnicodeError) as exc:
        raise UnsafePathError(home.path, "malformed GameMode ini") from exc
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise UnsafePathError(home.path, f"unsafe or unavailable filesystem (errno {exc.errno})") from exc
