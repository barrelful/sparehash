"""Installer-only isolated binary probes and verified upstream release selection."""

from __future__ import annotations

import os
import pwd
import selectors
import subprocess
import tempfile
import time
from dataclasses import replace
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

from .config import Config
from .deployment import ENV, DeploymentError, Tree, command, binary as trusted_binary
from .sysdetect import Capabilities
from .xmrig_config import DryRunRequest, DryRunResult, PROPERTIES
from .xmrig_detect import ManagedBy, detect_installations, is_supported, parse_version
from .xmrig_install import InstallRequest, ReleaseArtifacts, current_binary, install_release
from .xmrig_release import download, fetch_latest_release, select_asset
from .xmrig_verify import fetch_signing_key


def isolated(binary: Path, arguments: tuple[str, ...]) -> tuple[int, str]:
    if os.geteuid() != 0 or pwd.getpwnam("nobody").pw_uid != 65534:
        raise DeploymentError("probe", "root and the dedicated nobody sandbox are required")
    unit = "sparehash-version-" + uuid4().hex
    argv = ("/usr/bin/systemd-run", "--quiet", "--pipe", "--wait", "--collect", f"--unit={unit}",
            "--property=User=nobody", "--property=Group=nogroup", "--property=RuntimeMaxSec=10s",
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


def version_probe(snapshot: Path, *, timeout: int, uid: int, required_properties: tuple[str, ...]) -> str | None:
    if uid != 65534 or timeout > 15 or set(required_properties) != set(PROPERTIES):
        return None
    with tempfile.TemporaryDirectory(prefix=".sparehash-probe-", dir="/var/lib/sparehash") as directory:
        stage = Path(directory)
        stage.chmod(0o755)
        binary = stage / "xmrig"
        _ = binary.write_bytes(snapshot.read_bytes())
        binary.chmod(0o555)
        code, output = isolated(binary, ("--version",))
        return output if code == 0 else None


def dry_run(request: DryRunRequest) -> DryRunResult:
    with tempfile.TemporaryDirectory(prefix=".sparehash-probe-", dir="/var/lib/sparehash") as directory:
        stage = Path(directory)
        stage.chmod(0o755)
        binary, config = stage / "xmrig", stage / "config.json"
        _ = binary.write_bytes(request.snapshot.read_bytes())
        _ = config.write_bytes(request.config.read_bytes())
        binary.chmod(0o555)
        config.chmod(0o444)
        code, output = isolated(binary, ("--config", str(config), "--dry-run"))
        return DryRunResult(code, 65534, PROPERTIES, True, output)


def choose(cfg: Config, caps: Capabilities, use_existing: bool) -> Config:
    candidates = detect_installations(caps, probe=version_probe)
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
    release = fetch_latest_release()
    archive_url, sums_url, sig_url = select_asset(release.assets)
    version = parse_version("XMRig " + release.tag_name.removeprefix("v"))
    if version is None or not is_supported(version):
        raise DeploymentError("xmrig", "upstream version is unsupported")
    with tempfile.TemporaryDirectory(prefix="sparehash-release-") as directory:
        stage = Path(directory)
        archive = stage / archive_url.rsplit("/", 1)[-1]
        sums, sig = stage / "SHA256SUMS", stage / "SHA256SUMS.sig"
        for url, target in ((archive_url, archive), (sums_url, sums), (sig_url, sig)):
            download(url, target)
        installed = install_release(InstallRequest(ReleaseArtifacts(archive, sums, sig, fetch_signing_key()), version, "github:xmrig/xmrig:" + release.tag_name))
    print(f"xmrig: installed signed {installed.version}")
    return replace(cfg, xmrig=replace(cfg.xmrig, binary=str(installed.binary)))
