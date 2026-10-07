"""Descriptor-pinned XMRig path trust and private binary snapshots."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias

Json: TypeAlias = None | bool | int | float | str | list["Json"] | dict[str, "Json"]
_LOAD_JSON: Final[Callable[..., Json]] = json.loads

MAX_BINARY_BYTES: Final = 128 * 1024 * 1024
MAX_METADATA_BYTES: Final = 64 * 1024
DIRECTORY_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
FILE_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
MANIFEST_ROOT: Final = "/opt/xmrig"
MARKER_NAME: Final = ".sparehash-managed"
MANIFEST_RELPATH: Final = Path("var/lib/sparehash/install-manifest.json")
VERSION_PATTERN: Final = re.compile(r"xmrig-\d+\.\d+\.\d+")
INSTALLED_PATTERN: Final = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")
SOURCE_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}")


def _open_path(stack: ExitStack, path: Path, anchor: Path) -> list[int]:
    """Pin every component; the anchor is / in production, a fixture root in tests."""
    parts = path.relative_to(anchor).parts
    current = os.open(anchor, DIRECTORY_FLAGS)
    _ = stack.callback(os.close, current)
    descriptors = [current]
    for index, part in enumerate(parts):
        flags = FILE_FLAGS if index == len(parts) - 1 else DIRECTORY_FLAGS
        current = os.open(part, flags, dir_fd=current)
        _ = stack.callback(os.close, current)
        descriptors.append(current)
    return descriptors


def _unchanged(before: tuple[os.stat_result, ...], descriptors: list[int]) -> bool:
    after = tuple(os.fstat(fd) for fd in descriptors)
    return all((a.st_dev, a.st_ino, a.st_size, a.st_mtime_ns, a.st_ctime_ns, a.st_mode, a.st_uid) ==
               (b.st_dev, b.st_ino, b.st_size, b.st_mtime_ns, b.st_ctime_ns, b.st_mode, b.st_uid)
               for a, b in zip(before, after, strict=True))


def _snapshot_fd(source_fd: int, private: Path, parents: list[int]) -> tuple[Path, bool] | None:
    info = os.fstat(source_fd)
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BINARY_BYTES:
        return None
    descriptors = [*parents, source_fd]
    writable = info.st_uid != 0 or any(os.fstat(fd).st_mode & 0o022 for fd in descriptors)
    snapshot: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=private, prefix="xmrig-probe-", delete=False) as target:
            snapshot = Path(target.name)
            os.fchmod(target.fileno(), 0o500)
            before = (*tuple(os.fstat(fd) for fd in parents), info)
            copied = 0
            while block := os.read(source_fd, 1024 * 1024):
                copied += len(block)
                if copied > MAX_BINARY_BYTES:
                    snapshot.unlink(missing_ok=True)
                    return None
                _ = target.write(block)
        if not _unchanged(before, descriptors):
            snapshot.unlink(missing_ok=True)
            return None
        return snapshot, bool(writable)
    except OSError:
        if snapshot is not None:
            snapshot.unlink(missing_ok=True)
        raise


def snapshot(path: Path, *, private_dir: Path) -> tuple[Path, bool] | None:
    """Snapshot an ordinary absolute candidate without following any symlink."""
    if not path.is_absolute() or path == Path("/"):
        return None
    try:
        with ExitStack() as stack:
            descriptors = _open_path(stack, path, Path("/"))
            return _snapshot_fd(descriptors[-1], private_dir, descriptors[:-1])
    except OSError:
        return None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    seen: set[str] = set()
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError("duplicate manifest key")
        seen.add(key)
        document[key] = value
    return document


@dataclass(frozen=True, slots=True)
class VersionRecord:
    """One manifest-owned version directory with provenance."""

    version: str
    installed_at: str
    source: str


@dataclass(frozen=True, slots=True)
class Manifest:
    """Canonical install manifest: fixed root plus managed version inventory."""

    root: str
    versions: tuple[VersionRecord, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(record.version for record in self.versions)


def parse_manifest(contents: str) -> Manifest | None:
    """Strictly parse the canonical manifest; reject any extra, missing or duplicate drift."""
    try:
        document = _LOAD_JSON(contents, object_pairs_hook=_unique_object)
    except (ValueError, TypeError):
        return None
    if not isinstance(document, dict) or set(document) != {"root", "versions"}:
        return None
    if document["root"] != MANIFEST_ROOT:
        return None
    raw_versions = document["versions"]
    if not isinstance(raw_versions, list):
        return None
    records: list[VersionRecord] = []
    seen: set[str] = set()
    for item in raw_versions:
        if not isinstance(item, dict) or set(item) != {"version", "installed_at", "source"}:
            return None
        version, installed_at, source = item["version"], item["installed_at"], item["source"]
        if (not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None
                or not isinstance(installed_at, str) or INSTALLED_PATTERN.fullmatch(installed_at) is None
                or not isinstance(source, str) or SOURCE_PATTERN.fullmatch(source) is None
                or version in seen):
            return None
        seen.add(version)
        records.append(VersionRecord(version, installed_at, source))
    return Manifest(MANIFEST_ROOT, tuple(records))


@dataclass(frozen=True, slots=True)
class PathTrust:
    """Private filesystem trust boundary; production always uses / and uid 0."""

    root: Path = Path("/")
    uid: int = 0

    @property
    def managed_root(self) -> Path:
        return self.root / "opt/xmrig"

    @property
    def marker_path(self) -> Path:
        return self.managed_root / MARKER_NAME

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_RELPATH

    def _protected(self, info: os.stat_result) -> bool:
        return info.st_uid == self.uid and info.st_mode & 0o022 == 0

    def manifest(self) -> Manifest | None:
        """Read the canonical protected marker and manifest under pinned descriptors."""
        try:
            marker_text = self._metadata(self.marker_path)
            if marker_text is None or marker_text.strip() != MANIFEST_ROOT:
                return None
            manifest_text = self._metadata(self.manifest_path)
            return None if manifest_text is None else parse_manifest(manifest_text)
        except (OSError, UnicodeError, ValueError):
            return None

    def _metadata(self, path: Path, parent_fd: int | None = None) -> str | None:
        """Read only the pinned, fstat-verified regular file and protected parents."""
        with ExitStack() as stack:
            if parent_fd is None:
                descriptors = _open_path(stack, path, self.root)
            else:
                file_fd = os.open(path.name, FILE_FLAGS, dir_fd=parent_fd)
                _ = stack.callback(os.close, file_fd)
                descriptors = [parent_fd, file_fd]
            before = tuple(os.fstat(fd) for fd in descriptors)
            if (not all(self._protected(info) for info in before)
                    or not stat.S_ISREG(before[-1].st_mode)
                    or not 0 < before[-1].st_size <= MAX_METADATA_BYTES):
                return None
            with os.fdopen(os.dup(descriptors[-1]), "rb") as source:
                contents = source.read(MAX_METADATA_BYTES + 1)
            if len(contents) > MAX_METADATA_BYTES or not _unchanged(before, descriptors):
                return None
            return contents.decode("utf-8")

    def records(self, path: Path, metadata: tuple[Path, Path], root_fd: int | None = None) -> bool:
        """Authorize exact managed metadata locations and manifest-recorded paths."""
        marker, manifest = metadata
        if (marker != self.marker_path or manifest != self.manifest_path):
            return False
        try:
            marker_text = self._metadata(marker, root_fd)
            manifest_text = self._metadata(manifest)
            parsed = parse_manifest(manifest_text) if manifest_text is not None else None
            recorded = (marker_text is not None and marker_text.strip() == MANIFEST_ROOT
                        and parsed is not None and path.parent.name in parsed.names
                        and path == self.managed_root / path.parent.name / "xmrig")
            if not recorded or root_fd is not None:
                return recorded
            with ExitStack() as stack:
                descriptors = _open_path(stack, path, self.root)
                infos = tuple(os.fstat(fd) for fd in descriptors)
                return stat.S_ISREG(infos[-1].st_mode) and all(self._protected(info) for info in infos)
        except (OSError, UnicodeError, ValueError):
            return False

    def _current_handles(self, stack: ExitStack, marker: Path, manifest: Path) -> tuple[
            Path, list[int], int, int, tuple[os.stat_result, ...], os.stat_result, os.stat_result] | None:
        """Pin and authorize the manifest-recorded relative current link and its binary."""
        descriptors = _open_path(stack, self.managed_root / ".", self.root)
        root_fd = descriptors[-1]
        before = tuple(os.fstat(fd) for fd in descriptors)
        if (not stat.S_ISDIR(before[-1].st_mode)
                or not all(self._protected(info) for info in before)):
            return None
        link = os.readlink("current", dir_fd=root_fd)
        resolved = self.managed_root / link / "xmrig"
        if ("/" in link or link in {".", ".."}
                or not self.records(resolved, (marker, manifest), root_fd)):
            return None
        version_fd = os.open(link, DIRECTORY_FLAGS, dir_fd=root_fd)
        _ = stack.callback(os.close, version_fd)
        file_fd = os.open("xmrig", FILE_FLAGS, dir_fd=version_fd)
        _ = stack.callback(os.close, file_fd)
        version_info, file_info = os.fstat(version_fd), os.fstat(file_fd)
        if (not stat.S_ISDIR(version_info.st_mode) or not stat.S_ISREG(file_info.st_mode)
                or not self._protected(version_info) or not self._protected(file_info)):
            return None
        return resolved, descriptors, version_fd, file_fd, before, version_info, file_info

    def authorized_current(self, marker: Path, manifest: Path) -> Path | None:
        """Return the manifest-authorized current binary path without copying bytes."""
        try:
            with ExitStack() as stack:
                found = self._current_handles(stack, marker, manifest)
                if found is None:
                    return None
                resolved, descriptors, version_fd, file_fd, before, version_info, file_info = found
                if not _unchanged((*before, version_info, file_info), [*descriptors, version_fd, file_fd]):
                    return None
                return resolved
        except (OSError, ValueError):
            return None

    def current_snapshot(self, private: Path, marker: Path, manifest: Path) -> tuple[Path, bool] | None:
        """Snapshot the authorized relative current link from its pinned root."""
        try:
            with ExitStack() as stack:
                found = self._current_handles(stack, marker, manifest)
                if found is None:
                    return None
                _, descriptors, version_fd, file_fd, before, version_info, file_info = found
                result = _snapshot_fd(file_fd, private, [*descriptors, version_fd])
                if result is not None and not _unchanged((*before, version_info, file_info),
                                                        [*descriptors, version_fd, file_fd]):
                    result[0].unlink(missing_ok=True)
                    return None
                return (result[0], False) if result is not None else None
        except (OSError, ValueError):
            return None
