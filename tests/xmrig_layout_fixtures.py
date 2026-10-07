"""Private fixtures for managed-install layout tests; never touches the host."""

from __future__ import annotations

import io
import json
import os
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import final

from sparehash.controller_io import Generation, LifecycleBusy
from sparehash.xmrig_install import InstallRequest, ReleaseArtifacts
from sparehash.xmrig_paths import PathTrust

DATE = "2026-10-07T00:00:00Z"


def source_for(version: str) -> str:
    return f"github:xmrig/xmrig:{version}"


def canonical_manifest(versions: list[str], *, root: str = "/opt/xmrig") -> str:
    return json.dumps({
        "root": root,
        "versions": [{"version": v, "installed_at": DATE, "source": source_for(v)} for v in versions],
    }, indent=2) + "\n"


@dataclass(frozen=True, slots=True)
class Member:
    name: str
    data: bytes | None = None
    kind: bytes = tarfile.REGTYPE
    linkname: str = ""


def release_members(version_dir: str) -> list[Member]:
    return [
        Member(f"{version_dir}/", kind=tarfile.DIRTYPE),
        Member(f"{version_dir}/xmrig", b"#!/bin/sh\nexit 0\n"),
        Member(f"{version_dir}/config.json", b"{}"),
    ]


def write_tar(path: Path, members: list[Member]) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for member in members:
            info = tarfile.TarInfo(member.name)
            info.type = member.kind
            info.linkname = member.linkname
            payload = member.data or b""
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload) if member.kind == tarfile.REGTYPE else None)
    return path


def request_for(archive: Path, version: tuple[int, int, int] = (6, 26, 0)) -> InstallRequest:
    version_dir = f"xmrig-{version[0]}.{version[1]}.{version[2]}"
    return InstallRequest(
        ReleaseArtifacts(archive, archive.with_suffix(".sums"), archive.with_suffix(".sig"), b"key"),
        version, source_for(version_dir))


@dataclass
class FakeGate:
    """Records lease ordering; proves the token is held across every mutation."""

    empty: bool = True
    busy: bool = False
    release_ok: bool = True
    calls: list[str] = field(default_factory=list)
    on_release: Callable[[bool], None] | None = None

    def acquire_update(self) -> Generation:
        self.calls.append("acquire")
        if self.busy:
            raise LifecycleBusy()
        return Generation(1)

    def update_ready(self, token: Generation) -> bool:
        _ = token
        self.calls.append("ready")
        return self.empty

    def release_update(self, token: Generation) -> bool:
        _ = token
        self.calls.append("release")
        if self.on_release is not None:
            self.on_release(self.empty)
        return self.release_ok


@final
class Layout:
    """A private temporary anchor with an optional pre-built managed tree."""

    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "opt").mkdir(mode=0o755)
        self.root.chmod(0o755)
        (self.root / "opt").chmod(0o755)
        self.trust = PathTrust(self.root, os.getuid())

    def cleanup(self) -> None:
        self.temp.cleanup()

    @property
    def managed(self) -> Path:
        return self.trust.managed_root

    @property
    def marker(self) -> Path:
        return self.trust.marker_path

    @property
    def manifest(self) -> Path:
        return self.trust.manifest_path

    def binary(self, version_dir: str) -> Path:
        return self.managed / version_dir / "xmrig"

    def build(self, versions: list[str], *, current: str | None = None,
              records: list[str] | None = None, marker_text: str = "/opt/xmrig") -> None:
        self.managed.mkdir(mode=0o755)
        self.managed.chmod(0o755)
        for name in versions:
            directory = self.managed / name
            directory.mkdir(mode=0o755)
            directory.chmod(0o755)
            binary = directory / "xmrig"
            _ = binary.write_bytes(b"#!/bin/sh\nexit 0\n")
            binary.chmod(0o755)
        self.manifest.parent.mkdir(parents=True, mode=0o755)
        for directory in (self.root / "var", self.root / "var/lib", self.manifest.parent):
            directory.chmod(0o755)
        _ = self.manifest.write_text(canonical_manifest(records if records is not None else versions))
        self.manifest.chmod(0o644)
        _ = self.marker.write_text(marker_text)
        self.marker.chmod(0o644)
        if current is not None:
            _ = (self.managed / "current").symlink_to(current)
