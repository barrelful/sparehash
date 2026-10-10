# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Safe discovery and version policy for existing XMRig binaries."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol

from .xmrig_paths import MAX_BINARY_BYTES as MAX_BINARY_BYTES, PathTrust, snapshot as snapshot

MIN_VERSION: Final = (6, 21, 2)
FIXED_PATH: Final = "/usr/sbin:/usr/bin:/sbin:/bin:/usr/games"
PROBE_TIMEOUT: Final = 15
FORTIFY_WARNING: Final = "this version has a RandomX crash fixed in 6.21.2"
CANDIDATES: Final = ("/usr/bin/xmrig", "/usr/local/bin/xmrig", "/opt/xmrig/current/xmrig")
_PATH_TRUST: Final = PathTrust()


class ManagedBy(StrEnum):
    SPAREHASH = "sparehash"
    DISTRO = "distro"
    USER = "user"


class Recommendation(StrEnum):
    USE_EXISTING = "use-existing"
    INSTALL_UPSTREAM = "install-upstream"
    PROTECTED_COPY_CONSENT = "protected-copy-consent"


@dataclass(frozen=True, slots=True)
class Install:
    path: Path
    version: tuple[int, int, int] | None
    managed_by: ManagedBy
    verified: bool
    writable_source: bool
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class Classification:
    recommendation: Recommendation
    install: Install | None
    reason: str


class IsolationProbe(Protocol):
    def __call__(self, snapshot: Path, *, timeout: int, uid: int, required_properties: tuple[str, ...]) -> str | None: ...


class Capabilities(Protocol):
    @property
    def xmrig_path(self) -> str | None: ...


class MetadataRunner(Protocol):
    def __call__(self, argv: list[str], *, check: bool, capture_output: bool, text: bool,
                 timeout: int, env: dict[str, str]) -> subprocess.CompletedProcess[str]: ...


class SystemRunner(Protocol):
    def __call__(self, argv: list[str], *, check: bool, capture_output: bool, text: bool,
                 timeout: int, env: dict[str, str]) -> subprocess.CompletedProcess[str]: ...


def _metadata_runner(argv: list[str], *, check: bool, capture_output: bool, text: bool,
                     timeout: int, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=check, capture_output=capture_output, text=text,
                          timeout=timeout, env=env)


def metadata_adapter(runner: SystemRunner) -> MetadataRunner:
    return runner


def ownership(path: Path, *, runner: MetadataRunner, marker: Path, manifest: Path) -> ManagedBy:
    if _PATH_TRUST.records(path, (marker, manifest)):
        return ManagedBy.SPAREHASH
    return ManagedBy.DISTRO if _package_owned(path, runner=runner) else ManagedBy.USER


def package_owned(path: Path, *, runner: MetadataRunner) -> bool:
    return _package_owned(path, runner=runner)


def parse_version(text: str) -> tuple[int, int, int] | None:
    """Parse an XMRig semantic version from bounded command output."""
    match = re.search(r"\bXMRig(?:\s+version)?\s+(\d+)\.(\d+)\.(\d+)\b", text, re.IGNORECASE)
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def unverified(reason: str = "XMRig version is unverified or unsupported.") -> Classification:
    return Classification(Recommendation.INSTALL_UPSTREAM, None, reason)


def is_supported(version: tuple[int, int, int]) -> bool:
    """Return whether XMRig is at or above the security minimum."""
    return version >= MIN_VERSION


def candidate_paths(caps: Capabilities | None = None, *, path_value: str | None = None) -> tuple[Path, ...]:
    """Enumerate only fixed PATH and approved absolute candidates, deduplicated."""
    found = shutil.which("xmrig", path=FIXED_PATH if path_value is None else path_value)
    configured = None if caps is None else caps.xmrig_path
    preferred = (Path(configured),) if configured else ()
    raw = preferred + ((Path(found),) if found else ()) + tuple(Path(item) for item in CANDIDATES)
    return tuple(dict.fromkeys(raw))


def _package_owned(path: Path, *, runner: MetadataRunner) -> bool:
    """Ask fixed package metadata tools whether this exact path is owned."""
    for argv in (["dpkg-query", "-S", str(path)], ["rpm", "-qf", str(path)]):
        tool = shutil.which(argv[0], path=FIXED_PATH)
        if tool is None:
            continue
        try:
            result = runner([tool, *argv[1:]], check=False, capture_output=True, text=True, timeout=3,
                            env={"PATH": FIXED_PATH})
        except (OSError, subprocess.SubprocessError, TimeoutError):
            continue
        if result.returncode == 0 and result.stdout.strip():
            return True
    return False


def detect_installations(
    caps: Capabilities | None = None, *, probe: IsolationProbe | None = None,
    runner: SystemRunner | None = None, marker: Path | None = None,
    manifest: Path | None = None,
) -> list[Install]:
    """Discover configured, fixed-PATH, and approved candidates; absence is normal.

    Todo11/Todo18 production seam: inject ``IsolationProbe(snapshot, *, timeout, uid, required_properties)``.
    The implementation must use systemd-run transient unit as nobody (uid 65534),
    with ProtectSystem=strict, ProtectHome=yes, PrivateNetwork=yes,
    NoNewPrivileges=yes, PrivateTmp=yes, bounded output/time, and unconditional
    unit cleanup. No default adapter exists; omission means unverified.
    """
    metadata_runner: MetadataRunner = _metadata_runner if runner is None else metadata_adapter(runner)
    managed_marker = marker or Path("/opt/xmrig/.sparehash-managed")
    install_manifest = manifest or Path("/var/lib/sparehash/install-manifest.json")
    results: list[Install] = []
    with tempfile.TemporaryDirectory(prefix="sparehash-xmrig-") as scratch:
        private = Path(scratch)
        private.chmod(0o755)
        for path in candidate_paths(caps):
            is_current = path == _PATH_TRUST.managed_root / "current/xmrig"
            snapshot_result = (_PATH_TRUST.current_snapshot(private, managed_marker, install_manifest)
                               if is_current else snapshot(path, private_dir=private))
            if snapshot_result is None:
                if os.path.lexists(path):
                    results.append(Install(path, None, ManagedBy.USER, False, True,
                                           "Present XMRig candidate could not be safely snapshotted."))
                continue
            private_snapshot, writable = snapshot_result
            version = None
            if probe is not None:
                try:
                    output = probe(private_snapshot, timeout=PROBE_TIMEOUT, uid=65534,
                                   required_properties=("ProtectSystem=strict", "ProtectHome=yes",
                                                       "PrivateNetwork=yes", "NoNewPrivileges=yes", "PrivateTmp=yes"))
                except (OSError, subprocess.SubprocessError, TimeoutError):
                    output = None
                version = parse_version(output or "")
            private_snapshot.unlink(missing_ok=True)
            managed = (ManagedBy.SPAREHASH if is_current else
                       ownership(path, runner=metadata_runner, marker=managed_marker, manifest=install_manifest))
            results.append(Install(path, version, managed, version is not None, writable,
                                   FORTIFY_WARNING if version is not None and not is_supported(version) else None))
    return results


def classify(installs: list[Install], *, consent: bool = False) -> Classification:
    """Choose safe reuse, protected-copy consent, or verified upstream install."""
    supported = next((item for item in installs if item.verified and item.version is not None
                      and is_supported(item.version)), None)
    if supported is None and not installs:
        return Classification(Recommendation.INSTALL_UPSTREAM, None, "No existing XMRig; fresh installation is normal.")
    if supported is not None and not supported.writable_source and consent:
        return Classification(Recommendation.USE_EXISTING, supported, "Supported existing installation explicitly accepted.")
    if supported is not None:
        return Classification(Recommendation.PROTECTED_COPY_CONSENT, supported,
                              "Use only after explicit consent to a protected copy; source remains untouched.")
    reason = FORTIFY_WARNING if any(item.version is not None and not is_supported(item.version) for item in installs) else "XMRig version is unverified or unsupported."
    retained = next((item for item in installs if item.version is None), None)
    if retained is not None:
        detail = f"Unverified candidate at {retained.path}: {retained.warning or reason}"
        return Classification(Recommendation.PROTECTED_COPY_CONSENT, retained, detail)
    outdated = next((item for item in installs if item.version is not None and not is_supported(item.version)), None)
    return Classification(Recommendation.INSTALL_UPSTREAM, outdated, reason)
