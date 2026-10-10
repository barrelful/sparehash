# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Root-controlled deployment I/O; explicit fixture roots never come from clients."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Final
from uuid import uuid4

from .xmrig_ledger import Artifact, Json, mapping
from .xmrig_paths import PathTrust

PATH: Final = "/usr/sbin:/usr/bin:/sbin:/bin"
ENV: Final = {"PATH": PATH, "LANG": "C", "LC_ALL": "C"}
LOAD_JSON: Final[Callable[..., Json]] = json.loads


class DeploymentError(RuntimeError):
    stage: str

    def __init__(self, stage: str, detail: str) -> None:
        self.stage = stage
        super().__init__(f"{stage}: {detail}")


def command(argv: tuple[str, ...], timeout: float = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False, timeout=timeout, env=ENV)


def checked(argv: tuple[str, ...], timeout: float = 10) -> str:
    result = command(argv, timeout)
    if result.returncode:
        raise DeploymentError("helper", f"{argv[0]} exited {result.returncode}: {result.stderr[:1024]}")
    return result.stdout.strip()


def protected_digest(name: str, data: bytes) -> str:
    """SHA256 of artifact bytes; token-bearing configs strip the API access token first."""
    if name in {"/etc/sparehash/xmrig.json", "/etc/sparehash/xmrig.runtime.json"}:
        document = mapping(LOAD_JSON(data))
        http = mapping(document["http"])
        _ = http.pop("access-token", None)
        data = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def _read_pinned(fd: int, limit: int) -> bytes:
    """Read at most limit bytes from an already-pinned descriptor."""
    chunks: list[bytes] = []
    remaining = limit
    while remaining > 0:
        chunk = os.read(fd, min(remaining, 65536))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def binary(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or path.name != "xmrig" or ".." in path.parts:
        raise DeploymentError("binary", "expected a protected absolute xmrig path")
    with ExitStack() as stack:
        parent = Tree().pin(stack, str(path.parent))
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        _ = stack.callback(os.close, fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or not info.st_mode & 0o111:
            raise DeploymentError("binary", "miner binary is not protected/root-owned/executable")
    return path


@dataclass(frozen=True, slots=True)
class Tree:
    root: Path = Path("/")
    uid: int = 0

    def path(self, name: str) -> Path:
        return self.root / name.removeprefix("/")

    @property
    def trust(self) -> PathTrust:
        return PathTrust(self.root, self.uid)

    def pin(self, stack: ExitStack, name: str) -> int:
        path = Path(name)
        if not path.is_absolute() or ".." in path.parts:
            raise DeploymentError("path", "expected fixed absolute deployment path")
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        _ = stack.callback(os.close, fd)
        for component in (*path.parts[1:], ""):
            info = os.fstat(fd)
            if info.st_uid != self.uid or info.st_mode & 0o022:
                raise DeploymentError("ownership", f"unsafe ancestor of {name}")
            if component:
                fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                _ = stack.callback(os.close, fd)
        return fd

    def read(self, name: str) -> bytes | None:
        with ExitStack() as stack:
            parent = self.pin(stack, str(Path(name).parent))
            try:
                fd = os.open(Path(name).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            except FileNotFoundError:
                return None
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != self.uid or info.st_mode & 0o022 or info.st_size > 2 * 1024 * 1024:
                    raise DeploymentError("file", f"unsafe or oversized {name}")
                return source.read(2 * 1024 * 1024 + 1)

    def directory(self, name: str, gid: int) -> None:
        with ExitStack() as stack:
            parent = self.pin(stack, str(Path(name).parent))
            try:
                os.mkdir(Path(name).name, 0o755, dir_fd=parent)
            except FileExistsError:
                pass
            fd = self.pin(stack, name)
            os.fchown(fd, self.uid, gid)
            os.fchmod(fd, 0o755)
            os.fsync(fd)
            os.fsync(parent)

    def write(self, name: str, payload: tuple[bytes, int, int]) -> None:
        data, mode, gid = payload
        with ExitStack() as stack:
            parent = self.pin(stack, str(Path(name).parent))
            temp = ".sparehash-" + uuid4().hex
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent)
            try:
                with os.fdopen(fd, "wb") as target:
                    os.fchown(target.fileno(), self.uid, gid)
                    os.fchmod(target.fileno(), mode)
                    _ = target.write(data)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temp, Path(name).name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                try:
                    os.unlink(temp, dir_fd=parent)
                except FileNotFoundError:
                    pass

    def receipt(self, name: str, created: bool, *, identity: str | None = None) -> Artifact:
        """Describe one pinned artifact; identity and content come from a single descriptor."""
        with ExitStack() as stack:
            parent = self.pin(stack, str(Path(name).parent))
            try:
                fd = os.open(Path(name).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            except FileNotFoundError as error:
                raise DeploymentError("receipt", f"artifact disappeared: {name}") from error
            except OSError as error:
                raise DeploymentError("receipt", f"unsafe artifact: {name}") from error
            _ = stack.callback(os.close, fd)
            info = os.fstat(fd)
            directory = stat.S_ISDIR(info.st_mode)
            if not directory and not stat.S_ISREG(info.st_mode):
                raise DeploymentError("receipt", f"not a regular artifact: {name}")
            digest = None
            if not directory:
                if info.st_size > 2 * 1024 * 1024:
                    raise DeploymentError("receipt", f"oversized artifact: {name}")
                data = _read_pinned(fd, 2 * 1024 * 1024 + 1)
                after = os.fstat(fd)
                if (len(data) != info.st_size or after.st_ino != info.st_ino
                        or after.st_mtime_ns != info.st_mtime_ns or after.st_ctime_ns != info.st_ctime_ns):
                    raise DeploymentError("receipt", f"artifact changed during read: {name}")
                digest = protected_digest(name, data)
            return Artifact(identity if identity is not None else name, name,
                            "directory" if directory else "file", created,
                            info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_dev, info.st_ino, digest)
