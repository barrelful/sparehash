# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Installer-only isolated binary probes and verified upstream release selection."""

from __future__ import annotations

import os
import selectors
import stat
import subprocess
import tempfile
import time
from dataclasses import replace
from contextlib import ExitStack
from pathlib import Path
from typing import Final
from uuid import uuid4

from .config import Config
from .deployment import ENV, DeploymentError, Tree, command, binary as trusted_binary
from .sysdetect import Capabilities
from .xmrig_config import DryRunRequest, DryRunResult, PROPERTIES
from .xmrig_detect import ManagedBy, detect_installations, is_supported, parse_version
from .xmrig_install import InstallRequest, ReleaseArtifacts, current_binary, install_release
from .xmrig_release import download, fetch_latest_release, select_asset
from .xmrig_verify import fetch_signing_key


PROBE_USER: Final = "nobody"
PROBE_STAGE_ROOT: Final = "/opt/sparehash"
PASSWD_FILES: Final = ("etc/passwd", "usr/lib/passwd", "usr/etc/passwd")
GROUP_FILES: Final = ("etc/group", "usr/lib/group", "usr/etc/group")
PROBE_UID: Final = 65534
PROBE_ROOT: Final = Path("/")
IDENTITY_LIMIT: Final = 1024 * 1024
# Groups that expose privileged hardware, configuration or live sparehash/xmrig
# credentials to a candidate probe; a probe identity may never join one.
SENSITIVE_GROUPS: Final = frozenset({
    "sudo", "wheel", "root", "adm", "disk", "kmem", "kvm", "netdev", "network",
    "audio", "video", "input", "render", "sparehash", "xmrig",
})


def _read_identity_file(path: Path) -> str:
    """Read one bounded, no-follow identity file; reject links, nonregular and oversized."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise DeploymentError("identity", f"unreadable identity file: {path.name}") from error
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > IDENTITY_LIMIT:
            raise DeploymentError("identity", f"unsafe identity file: {path.name}")
        chunks: list[bytes] = []
        remaining = IDENTITY_LIMIT
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(fd)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeError as error:
        raise DeploymentError("identity", f"malformed identity file: {path.name}") from error


def _passwd_identity(text: str, user: str) -> tuple[int, int] | None:
    for line in text.splitlines():
        fields = line.split(":")
        if len(fields) >= 4 and fields[0] == user:
            try:
                return int(fields[2]), int(fields[3])
            except ValueError as error:
                raise DeploymentError("identity", "malformed passwd identity") from error
    return None


def _group_memberships(text: str, user: str, primary_gid: int) -> tuple[str, frozenset[str]]:
    primary = ""
    sensitive: set[str] = set()
    for line in text.splitlines():
        fields = line.split(":")
        if len(fields) < 4:
            continue
        name, gid_text, members = fields[0], fields[2], fields[3]
        try:
            gid = int(gid_text)
        except ValueError:
            continue
        if gid == primary_gid:
            primary = name
        if name in SENSITIVE_GROUPS and (
            gid == primary_gid or user in {member.strip() for member in members.split(",") if member.strip()}
        ):
            sensitive.add(name)
    return primary, frozenset(sensitive)


def _identity_text(root: Path, names: tuple[str, ...]) -> str:
    """Concatenate every present identity file, first match winning.

    Atomic desktops such as Bazzite keep the account database in ``/usr/lib``
    (nss-altfiles) and leave ``/etc/passwd`` as a stub, so the NSS-visible truth
    spans several files. An absent candidate is skipped; an unreadable or unsafe
    one still refuses.
    """
    present: list[str] = []
    for name in names:
        path = root / name
        if not path.exists():
            continue
        present.append(_read_identity_file(path))
    if not present:
        raise DeploymentError("identity", f"missing identity file: {names[0]}")
    return "\n".join(present)


def resolve_probe_identity(root: Path) -> tuple[int, int]:
    """Resolve the real unprivileged probe identity from NSS files, or refuse.

    Reads ``root/etc/passwd`` and ``root/etc/group`` with bounded no-follow reads;
    never uses ``subprocess`` or the process account database. Returns the actual
    ``(uid, gid)`` of the dedicated ``nobody`` account only when it is non-root,
    has a real primary group and no privileged or credential-readable group
    memberships. Raises :class:`DeploymentError` rather than inventing an identity
    or falling back to root.
    """
    entry = _passwd_identity(_identity_text(root, PASSWD_FILES), PROBE_USER)
    if entry is None:
        raise DeploymentError("identity", "no dedicated unprivileged nobody account")
    uid, gid = entry
    if uid == 0:
        raise DeploymentError("identity", "probe identity must not be root")
    primary, sensitive = _group_memberships(_identity_text(root, GROUP_FILES), PROBE_USER, gid)
    if not primary or primary in SENSITIVE_GROUPS:
        raise DeploymentError("identity", "probe identity has no safe primary group")
    if sensitive:
        raise DeploymentError("identity", "probe identity has privileged or credential-reading memberships")
    return uid, gid


def isolated(binary: Path, arguments: tuple[str, ...], *, root: Path = PROBE_ROOT) -> tuple[int, str]:
    if os.geteuid() != 0:
        raise DeploymentError("probe", "root is required for the isolated nobody sandbox")
    uid, gid = resolve_probe_identity(root)
    unit = "sparehash-version-" + uuid4().hex
    argv = ("/usr/bin/systemd-run", "--quiet", "--pipe", "--wait", "--collect", f"--unit={unit}",
            f"--property=User={uid}", f"--property=Group={gid}", "--property=RuntimeMaxSec=10s",
            "--property=TimeoutStartSec=10s", "--property=TimeoutStopSec=2s", "--property=MemoryMax=256M",
            *(f"--property={item}" for item in PROPERTIES), str(binary), *arguments)
    data = bytearray()
    try:
        with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=ENV) as process:
            assert process.stdout is not None
            try:
                deadline = time.monotonic() + 15
                with selectors.DefaultSelector() as selector:
                    _ = selector.register(process.stdout, selectors.EVENT_READ)
                    while time.monotonic() < deadline:
                        if not selector.select(0.1):
                            continue
                        chunk = os.read(process.stdout.fileno(), 16385 - len(data))
                        if not chunk:
                            return process.wait(timeout=2), data.decode("utf-8", "replace")
                        data.extend(chunk)
                        if len(data) > 16384:
                            raise DeploymentError("probe", "sandbox output exceeds 16 KiB")
                raise DeploymentError("probe", "sandbox probe timed out")
            finally:
                if process.poll() is None:
                    process.kill()
                    _ = process.wait(timeout=2)
    finally:
        _ = command(("/usr/bin/systemctl", "stop", unit), 5)


def version_probe(snapshot: Path, *, timeout: int, uid: int, required_properties: tuple[str, ...],
                  root: Path = PROBE_ROOT) -> str | None:
    expected, _ = resolve_probe_identity(root)
    if uid != expected or timeout > 15 or set(required_properties) != set(PROPERTIES):
        return None
    with tempfile.TemporaryDirectory(prefix=".sparehash-probe-", dir=PROBE_STAGE_ROOT) as directory:
        stage = Path(directory)
        stage.chmod(0o755)
        binary = stage / "xmrig"
        _ = binary.write_bytes(snapshot.read_bytes())
        binary.chmod(0o555)
        code, output = isolated(binary, ("--version",), root=root)
        return output if code == 0 else None


def dry_run(request: DryRunRequest, *, root: Path = PROBE_ROOT) -> DryRunResult:
    uid, _ = resolve_probe_identity(root)
    if request.uid != uid:
        raise DeploymentError("probe", "dry-run expectation does not match the resolved probe identity")
    with tempfile.TemporaryDirectory(prefix=".sparehash-probe-", dir=PROBE_STAGE_ROOT) as directory:
        stage = Path(directory)
        stage.chmod(0o755)
        binary, config = stage / "xmrig", stage / "config.json"
        _ = binary.write_bytes(request.snapshot.read_bytes())
        _ = config.write_bytes(request.config.read_bytes())
        binary.chmod(0o555)
        config.chmod(0o444)
        code, output = isolated(binary, ("--config", str(config), "--dry-run"), root=root)
        return DryRunResult(code, uid, PROPERTIES, True, output)


def download_release(stage: Path) -> InstallRequest:
    """Fetch, select and download the latest signed release into a staging directory."""
    release = fetch_latest_release()
    archive_url, sums_url, sig_url = select_asset(release.assets)
    version = parse_version("XMRig " + release.tag_name.removeprefix("v"))
    if version is None or not is_supported(version):
        raise DeploymentError("xmrig", "upstream version is unsupported")
    archive = stage / archive_url.rsplit("/", 1)[-1]
    sums, sig = stage / "SHA256SUMS", stage / "SHA256SUMS.sig"
    for url, target in ((archive_url, archive), (sums_url, sums), (sig_url, sig)):
        download(url, target)
    return InstallRequest(ReleaseArtifacts(archive, sums, sig, fetch_signing_key()), version,
                          "github:xmrig/xmrig:" + release.tag_name)


def choose(cfg: Config, caps: Capabilities, use_existing: bool) -> Config:
    uid, _ = resolve_probe_identity(PROBE_ROOT)
    candidates = detect_installations(caps, probe=version_probe, uid=uid)
    for candidate in candidates:
        if (candidate.verified and candidate.version is not None and is_supported(candidate.version)
                and not candidate.writable_source and (use_existing or candidate.managed_by is ManagedBy.SPAREHASH)):
            binary = current_binary() if candidate.managed_by is ManagedBy.SPAREHASH else candidate.path
            if binary is None:
                raise DeploymentError("xmrig", "managed current link lost authorization")
            _ = trusted_binary(str(binary))
            if candidate.managed_by is ManagedBy.SPAREHASH:
                with ExitStack() as stack:
                    for directory in ("/opt/xmrig", str(binary.parent)):
                        fd = Tree().pin(stack, directory)
                        os.fchmod(fd, 0o755)
            print(f"xmrig: using verified {candidate.version} at {binary}")
            return replace(cfg, xmrig=replace(cfg.xmrig, binary=str(binary)))
    with tempfile.TemporaryDirectory(prefix="sparehash-release-") as directory:
        installed = install_release(download_release(Path(directory)))
    print(f"xmrig: installed signed {installed.version}")
    return replace(cfg, xmrig=replace(cfg.xmrig, binary=str(installed.binary)))
