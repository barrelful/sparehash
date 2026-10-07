"""Pinned canonical-manifest storage; directory flock owns the entire transaction.

Production uses root:sparehash. Explicit temporary-root contexts are test seams,
never environment overrides. This module grants no /opt/xmrig adoption authority.
"""

from __future__ import annotations

import fcntl
import grp
import os
import stat
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from uuid import uuid4

from .xmrig_manifest import MAX_METADATA_BYTES, Manifest, format_manifest, parse_manifest
from .xmrig_paths import DIRECTORY_FLAGS, FILE_FLAGS, PathTrust

NAME: Final = "install-manifest.json"
MODE: Final = 0o640


class StoreError(RuntimeError):
    stage: str
    detail: str

    def __init__(self, stage: str, detail: str) -> None:
        self.stage, self.detail = stage, detail
        super().__init__(f"{stage}: {detail}")


@dataclass(frozen=True, slots=True)
class StoreContext:
    trust: PathTrust = PathTrust()
    gid: int | None = None
    timeout: float = 10.0
    create: bool = False

    def group(self) -> int:
        if self.gid is not None:
            return self.gid
        if self.trust.root != Path("/"):
            return os.getgid()
        try:
            return grp.getgrnam("sparehash").gr_gid
        except KeyError as error:
            raise StoreError("manifest-owner", "create the sparehash system group before publication") from error


DEFAULT_CONTEXT: Final = StoreContext()


@dataclass(frozen=True, slots=True)
class Snapshot:
    data: bytes
    manifest: Manifest
    uid: int
    gid: int
    mode: int


@dataclass(frozen=True, slots=True)
class ManifestStore:
    """Only usable within locked_store; all operations use its pinned directory."""

    fd: int
    uid: int
    gid: int

    def read(self) -> Snapshot | None:
        try:
            fd = os.open(NAME, FILE_FLAGS, dir_fd=self.fd)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise StoreError("manifest", f"unsafe metadata entry: {error}") from error
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != self.uid
                    or before.st_mode & 0o022 or before.st_nlink != 1
                    or not 0 < before.st_size <= MAX_METADATA_BYTES):
                raise StoreError("manifest", "metadata is not a bounded protected owned regular file")
            data = source.read(MAX_METADATA_BYTES + 1)
            after = os.fstat(source.fileno())
        if (before.st_size != len(data) or len(data) > MAX_METADATA_BYTES
                or before.st_mtime_ns != after.st_mtime_ns or before.st_ctime_ns != after.st_ctime_ns):
            raise StoreError("manifest", "metadata changed during read")
        try:
            parsed = parse_manifest(data.decode("utf-8"))
        except UnicodeError as error:
            raise StoreError("manifest", "metadata is not UTF-8") from error
        if parsed is None:
            raise StoreError("manifest", "metadata is not a canonical legacy or extended document")
        return Snapshot(data, parsed, before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode))

    def write(self, manifest: Manifest) -> None:
        data = format_manifest(manifest)
        prior = self.read()
        try:
            self._replace(Snapshot(data, manifest, self.uid, self.gid, MODE))
        except (OSError, KeyboardInterrupt):
            self.restore(prior)
            raise

    def restore(self, prior: Snapshot | None) -> None:
        if prior is None:
            with suppress(FileNotFoundError):
                os.unlink(NAME, dir_fd=self.fd)
            os.fsync(self.fd)
        else:
            self._replace(prior)

    def _replace(self, snapshot: Snapshot) -> None:
        temp = f".{NAME}.tmp-{uuid4().hex}"
        created = False
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         MODE, dir_fd=self.fd)
            created = True
            with os.fdopen(fd, "wb") as target:
                os.fchown(target.fileno(), snapshot.uid, snapshot.gid)
                os.fchmod(target.fileno(), snapshot.mode)
                _ = target.write(snapshot.data)
                target.flush()
                os.fsync(target.fileno())
            os.rename(temp, NAME, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            if created:
                with suppress(FileNotFoundError):
                    os.unlink(temp, dir_fd=self.fd)


def _protected_directory(fd: int, uid: int) -> None:
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
        raise StoreError("manifest-directory", "refusing foreign or writable metadata directory")


@contextmanager
def locked_store(context: StoreContext = DEFAULT_CONTEXT) -> Iterator[ManifestStore]:
    """Pin all ancestors, then acquire LOCK_EX with a bounded nonblocking retry."""
    if not 0 <= context.timeout <= 60:
        raise StoreError("manifest-lock", "timeout must be between zero and 60 seconds")
    gid = context.group()
    with ExitStack() as stack:
        fd = os.open(context.trust.root, DIRECTORY_FLAGS)
        _ = stack.callback(os.close, fd)
        _protected_directory(fd, context.trust.uid)
        parts = context.trust.manifest_path.parent.relative_to(context.trust.root).parts
        for index, part in enumerate(parts):
            created = False
            try:
                next_fd = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not context.create:
                    raise
                os.mkdir(part, 0o755, dir_fd=fd)
                created = True
                os.fsync(fd)
                next_fd = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
            _ = stack.callback(os.close, next_fd)
            _protected_directory(next_fd, context.trust.uid)
            if created and index == len(parts) - 1:
                os.fchown(next_fd, context.trust.uid, gid)
                os.fchmod(next_fd, 0o755)
                os.fsync(next_fd)
            fd = next_fd
        deadline = time.monotonic() + context.timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as error:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise StoreError("manifest-lock", "another installer holds the metadata directory lock") from error
                time.sleep(min(0.02, remaining))
        try:
            yield ManifestStore(fd, context.trust.uid, gid)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
