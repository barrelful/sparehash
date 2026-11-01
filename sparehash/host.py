"""Bounded, read-only host and deployment inventory."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

FIXED_PATH: Final = "/usr/sbin:/usr/bin:/sbin:/bin"
MAX_RELEASE_BYTES: Final = 16_384
MAX_DISPLAY_BYTES: Final = 256
HELPERS: Final = ("systemctl", "rpm", "dpkg-query", "pveversion", "ostree", "bootc")


class Runner(Protocol):
    """Narrow runner seam for callers; detect deliberately never calls it."""

    def __call__(self, *args: str, **kwargs: str) -> subprocess.CompletedProcess[str]: ...


@dataclass(frozen=True, slots=True)
class OsReleaseFacts:
    identifier: str | None
    id_like: tuple[str, ...]
    name: str | None
    version_id: str | None
    link_chain: tuple[str, ...]
    source: str | None


@dataclass(frozen=True, slots=True)
class DeploymentModel:
    kind: str | None
    certified_desktop: bool


@dataclass(frozen=True, slots=True)
class MountFacts:
    atomic: bool | None
    bootc: bool | None
    ostree: bool | None


@dataclass(frozen=True, slots=True)
class ContainerFacts:
    kind: str | None
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProxmoxFacts:
    detected: bool | None
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HostFacts:
    os_release: OsReleaseFacts
    deployment: DeploymentModel
    mounts: MountFacts
    container: ContainerFacts
    proxmox: ProxmoxFacts
    nixos_detected: bool | None
    helpers_available: tuple[tuple[str, str | None], ...]
    helpers_missing: tuple[str, ...]
    reason: str | None
    inconclusive: bool

    @classmethod
    def detect(
        cls,
        *,
        root: str | Path = "/",
        runner: Runner | None = None,
    ) -> HostFacts:
        """Collect host facts from a pinned filesystem root; runner is unused."""
        del runner
        base = Path(root)
        release, chain, source, reason = _os_release(base)
        parsed = _parse_release(release)
        path_facts = _inventory_paths(base)
        proxmox_evidence = tuple(name for name, present in path_facts.items() if name.startswith("pve:") and present)
        proxmox = ProxmoxFacts(
            detected=True if proxmox_evidence else (False if all(name in path_facts for name in ("pve:/etc/pve", "pve:/run/pve-cluster")) else None),
            evidence=proxmox_evidence,
        )
        is_nixos = parsed[0] == "nixos"
        atomic = bool(path_facts.get("atomic:/run/ostree-booted") or path_facts.get("atomic:/sysroot/ostree/deploy"))
        bootc = bool(path_facts.get("atomic:/usr/lib/ostree-boot"))
        ostree = bool(path_facts.get("atomic:/run/ostree/deploy") or path_facts.get("atomic:/sysroot/ostree/repo"))
        container_evidence = tuple(name for name in ("container:/.dockerenv", "container:/run/.containerenv") if path_facts.get(name))
        helpers = tuple((name, _helper(base, name)) for name in HELPERS)
        distro = parsed[0]
        certified = distro in {"debian", "ubuntu", "linuxmint"} and proxmox.detected is False and not any((atomic, bootc, ostree, container_evidence))
        if reason is None and proxmox.detected is True and distro in {"debian", "ubuntu", "linuxmint"}:
            reason = "Proxmox evidence conflicts with desktop distribution identity"
        elif reason is None and parsed[3]:
            reason = parsed[3]
        elif reason is None and release is not None and parsed[0] is None:
            reason = "os-release has no usable distribution ID"
        elif reason is None and release is not None and not re.fullmatch(r"[a-z0-9._-]+", parsed[0] or ""):
            reason = "unsafe distribution ID"
        return cls(
            os_release=OsReleaseFacts(distro, parsed[1], parsed[2], parsed[4], chain, source),
            deployment=DeploymentModel("nixos" if is_nixos else "atomic" if atomic else "bootc" if bootc else "ostree" if ostree else "conventional", certified),
            mounts=MountFacts(atomic if path_facts.get("atomic_inventory_known") else None, bootc if path_facts.get("atomic_inventory_known") else None, ostree if path_facts.get("atomic_inventory_known") else None),
            container=ContainerFacts("container" if container_evidence else None, container_evidence),
            proxmox=proxmox,
            nixos_detected=is_nixos if parsed[0] is not None else None,
            helpers_available=helpers,
            helpers_missing=tuple(name for name, resolved in helpers if resolved is None),
            reason=reason,
            inconclusive=reason is not None or parsed[0] is None or proxmox.detected is None,
        )


def _safe_read(path: Path, limit: int) -> str | None:
    """Read a regular file without following the final path component."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            return None
        return data.decode("utf-8")
    except (OSError, UnicodeError):
        return None


def _os_release(root: Path) -> tuple[str | None, tuple[str, ...], str | None, str | None]:
    etc = root / "etc/os-release"
    chain: list[str] = []
    current = etc
    for _ in range(2):
        try:
            info = current.lstat()
        except OSError:
            break
        chain.append(str(current.relative_to(root)))
        if not stat.S_ISLNK(info.st_mode):
            text = _safe_read(current, MAX_RELEASE_BYTES)
            return text, tuple(chain), str(current.relative_to(root)), None if text is not None else "os-release unreadable, oversized, or invalid UTF-8"
        target = os.readlink(current)
        candidate = Path(target)
        if candidate.is_absolute():
            candidate = root / str(candidate).lstrip("/")
        else:
            candidate = current.parent / candidate
        try:
            if candidate.resolve(strict=False).is_relative_to(root.resolve()):
                current = candidate
            else:
                return None, tuple(chain), None, "os-release link escapes pinned root"
        except OSError:
            return None, tuple(chain), None, "os-release link cannot be resolved"
    fallback = root / "usr/lib/os-release"
    if not chain and fallback.exists():
        text = _safe_read(fallback, MAX_RELEASE_BYTES)
        return text, ("usr/lib/os-release",), "usr/lib/os-release", None if text is not None else "os-release unreadable, oversized, or invalid UTF-8"
    return None, tuple(chain), None, "os-release missing or link chain exceeds supported depth"


def _parse_release(text: str | None) -> tuple[str | None, tuple[str, ...], str | None, str | None, str | None]:
    if text is None:
        return None, (), None, "os-release unavailable", None
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=(.*)", line)
        if match is None:
            return None, (), None, "malformed os-release field", None
        key, raw = match.groups()
        if key in values:
            return None, (), None, "duplicate os-release field", None
        if raw.startswith(('"', "'")):
            if len(raw) < 2 or raw[-1] != raw[0]:
                return None, (), None, "malformed os-release quoting", None
            raw = raw[1:-1]
        if any(ord(char) < 32 or ord(char) == 127 for char in raw):
            return None, (), None, "control character in os-release field", None
        values[key] = raw
    ident = values.get("ID", "").casefold()
    likes = tuple(value.casefold() for value in values.get("ID_LIKE", "").split())
    display = _sanitize(values.get("NAME"))
    return ident or None, likes, display, None, values.get("VERSION_ID")


def _sanitize(text: str | None) -> str | None:
    if text is None:
        return None
    clean = "".join(char for char in text if ord(char) >= 32 and ord(char) != 127)
    return clean.encode("utf-8")[:MAX_DISPLAY_BYTES].decode("utf-8", errors="ignore")


def _inventory_paths(root: Path) -> dict[str, bool]:
    paths = (
        "atomic:/run/ostree-booted", "atomic:/sysroot/ostree/deploy", "atomic:/run/ostree/deploy",
        "atomic:/usr/lib/ostree-boot", "atomic:/sysroot/ostree/repo", "container:/.dockerenv",
        "container:/run/.containerenv", "pve:/etc/pve", "pve:/run/pve-cluster",
    )
    result = {key: (root / key.split(":", 1)[1].lstrip("/")).exists() for key in paths}
    result["atomic_inventory_known"] = True
    return result


def _helper(root: Path, name: str) -> str | None:
    for directory in FIXED_PATH.split(":"):
        candidate = root / directory.lstrip("/") / name
        try:
            if stat.S_ISREG(candidate.stat(follow_symlinks=False).st_mode) and os.access(candidate, os.X_OK, follow_symlinks=False):
                return str(candidate)
        except OSError:
            continue
    return shutil.which(name, path=FIXED_PATH) if root == Path("/") else None
