"""Root-only uninstall: lease-held, receipt-authoritative, fail-closed on partial failure."""

from __future__ import annotations

import os
import secrets
import sys
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Final, assert_never

from . import ipc
from .cli_lifecycle import LifecycleError, acquire, await_ready, end_update
from .cli_ops import Client, OperationError
from .deployment import DeploymentError, Tree, checked
from .hugepages import LeaseManager
from .install_manifest import StoreContext, locked_store
from .runtime_jobs import empty
from .xmrig_ledger import Artifact, GroupIdentity, Installer, UserIdentity
from .xmrig_layout import InstallError
from .xmrig_remove import remove_leaf_receipts, remove_owned_versions
from .xmrig_paths import PathTrust

UNITS: Final = "/etc/systemd/system"
CONTROLLER_UNIT: Final = UNITS + "/sparehash.service"
MINER_UNIT: Final = UNITS + "/sparehash-xmrig.service"
SHIM: Final = "/usr/local/bin/sparehash"
CONFIG: Final = ("/etc/sparehash/config.toml", "/etc/sparehash/xmrig.json", "/etc/sparehash/xmrig.runtime.json")
EXTRA_FILES: Final = (CONTROLLER_UNIT, MINER_UNIT, SHIM)
_DEFAULT_TRUST: Final = PathTrust()


def _run(argv: tuple[str, ...]) -> None:
    _ = checked(argv, 30)


@dataclass(frozen=True, slots=True)
class Infrastructure:
    """Privileged side effects; production defaults, injectable for offline tests."""

    run: Callable[[tuple[str, ...]], None] = _run
    cgroup_empty: Callable[[], bool] = empty
    free_hugepages: Callable[[], object] = lambda: LeaseManager(empty_proof=empty).cleanup()
    unmerge: Callable[[str], object] = lambda name: _gaming_unmerge(name)


def _gaming_unmerge(name: str) -> object:
    from .gaming import User
    from .gaming_install import unmerge

    return unmerge(User.from_name(name))


def _receipts(trust: PathTrust) -> Installer:
    with locked_store(StoreContext(trust)) as store:
        prior = store.read()
    if prior is None or prior.manifest.installer is None:
        raise LifecycleError("receipts", "no installer receipts; reinstall before removal")
    return prior.manifest.installer


def _identity(artifact: Artifact) -> tuple[int, int, int, int, int, str | None]:
    return (artifact.uid, artifact.gid, artifact.mode, artifact.device, artifact.inode, artifact.sha256)


def _remove_file(tree: Tree, artifact: Artifact) -> None:
    try:
        current = tree.receipt(artifact.path, artifact.created, identity=artifact.id)
    except DeploymentError as error:
        raise LifecycleError("receipt", "unauthorized or missing artifact: " + artifact.path) from error
    if _identity(current) != _identity(artifact):
        raise LifecycleError("receipt", "artifact changed since install: " + artifact.path)
    with ExitStack() as stack:
        parent = tree.pin(stack, str(Path(artifact.path).parent))
        os.unlink(Path(artifact.path).name, dir_fd=parent)
        os.fsync(parent)


def _remove_owned_files(tree: Tree, installer: Installer, purge: bool) -> None:
    wanted: set[str] = set(EXTRA_FILES)
    if purge:
        wanted.update(CONFIG)
    for artifact in installer.artifacts:
        if artifact.created and artifact.kind == "file" and artifact.path in wanted:
            _remove_file(tree, artifact)


def _remove_accounts(run: Callable[[tuple[str, ...]], None], installer: Installer) -> None:
    for membership in installer.memberships:
        if membership.added:
            run(("/usr/bin/gpasswd", "-d", membership.user, membership.group))
    for identity in installer.identities:
        if not identity.created:
            continue
        match identity:
            case UserIdentity():
                run(("/usr/sbin/userdel", identity.name))
            case GroupIdentity():
                run(("/usr/sbin/groupdel", identity.name))
            case _:
                assert_never(identity)


def uninstall(*, purge: bool = False, geteuid: Callable[[], int] = os.geteuid, trust: PathTrust = _DEFAULT_TRUST,
              client: Client = ipc.call, infrastructure: Infrastructure | None = None,
              clock: Callable[[], float] = time.monotonic, pause: Callable[[float], None] = time.sleep,
              request_id: str | None = None) -> int:
    """Stop, verify empty, restore hugepages, then remove only receipt-owned artifacts."""
    if geteuid() != 0:
        print("sparehash: uninstall requires root", file=sys.stderr)
        return 2
    infra = infrastructure or Infrastructure()
    tree = Tree(trust.root, trust.uid)
    token: str | None = None
    try:
        installer = _receipts(trust)
        token = acquire(client, request_id or secrets.token_hex(16), purpose="uninstall")
        await_ready(client, token, clock=clock, pause=pause)
        if not end_update(client, token, "commit"):
            raise LifecycleError("end-update", "controller could not retire the lifecycle lease")
        token = None
        infra.run(("/usr/bin/systemctl", "disable", "--now", "sparehash.service"))
        infra.run(("/usr/bin/systemctl", "stop", "sparehash-xmrig.service"))
        if not infra.cgroup_empty():
            raise LifecycleError("cgroup", "miner cgroup is not empty; artifacts retained")
        _ = infra.free_hugepages()
        for artifact in installer.artifacts:
            if artifact.created and artifact.id.startswith("gaming-ini:"):
                _ = infra.unmerge(artifact.id.removeprefix("gaming-ini:"))
        _ = remove_leaf_receipts(trust)
        _ = remove_owned_versions(trust)
        _remove_owned_files(tree, installer, purge)
        _remove_accounts(infra.run, installer)
        infra.run(("/usr/bin/systemctl", "daemon-reload"))
    except (OperationError, LifecycleError, InstallError, DeploymentError, OSError) as error:
        if token is not None:
            _ = end_update(client, token, "abort")
        print(f"sparehash: uninstall: {error}", file=sys.stderr)
        return 1
    print("SpareHash removed." if purge else "SpareHash removed; configuration retained.")
    return 0
