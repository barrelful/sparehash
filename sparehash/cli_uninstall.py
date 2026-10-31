# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Root-only uninstall: controller-independent, receipt-authoritative, fail-closed."""

from __future__ import annotations

import grp
import json
import os
import pwd
import sys
import time
from collections.abc import Callable
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from uuid import uuid4

from . import ipc, lifecycle
from .cli_lifecycle import LifecycleError
from .cli_ops import Client, OperationError
from .deployment import DeploymentError, Tree, checked
from .hugepages import LeaseManager
from .install_manifest import StoreContext, locked_store
from .lifecycle import retire
from .lifecycle_lease import LeaseError, LeaseStore
from .runtime_jobs import empty
from .xmrig_layout import InstallError
from .xmrig_ledger import Artifact, GroupIdentity, Installer, UserIdentity
from .xmrig_paths import PathTrust
from .xmrig_remove import remove_leaf_receipts, remove_owned_versions

UNITS: Final = "/etc/systemd/system"
CONTROLLER_UNIT: Final = UNITS + "/sparehash.service"
MINER_UNIT: Final = UNITS + "/sparehash-xmrig.service"
SHIM: Final = "/usr/local/bin/sparehash"
CONFIG: Final = ("/etc/sparehash/config.toml", "/etc/sparehash/xmrig.json", "/etc/sparehash/xmrig.runtime.json")
EXTRA_FILES: Final = (CONTROLLER_UNIT, MINER_UNIT, SHIM)
_DEFAULT_TRUST: Final = PathTrust()
PROGRESS_PATH: Final = "/var/lib/sparehash/uninstall-progress.json"
PROGRESS_SCHEMA: Final = 1
MAX_PROGRESS_ITEMS: Final = 2048
MAX_PROGRESS_BYTES: Final = 65536
_PROGRESS_PHASES: Final = frozenset(("leaf-receipts", "versions"))
_PROGRESS_FIELDS: Final = frozenset(("version", "phases", "removed"))
_FILE_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def _nss_user(name: str) -> tuple[int, int] | None:
    """Resolve a name to its numeric NSS identity; None when the name is absent."""
    try:
        entry = pwd.getpwnam(name)
    except KeyError:
        return None
    return entry.pw_uid, entry.pw_gid


def _nss_group(name: str) -> int | None:
    """Resolve a group name to its numeric NSS gid; None when the name is absent."""
    try:
        return grp.getgrnam(name).gr_gid
    except KeyError:
        return None


class RemovalJournal:
    """Bounded write-ahead uninstall journal.

    Intent is persisted before each deletion so an interrupted removal resumes
    idempotently: an artifact recorded as removed may legitimately be absent,
    while an unrecorded missing or replaced object refuses rather than being
    adopted. The journal is schema-checked, size-bounded and stored under the
    pinned physical root; no free-form data is accepted.
    """

    def __init__(self, tree: Tree) -> None:
        self.tree = tree
        self.done: set[str] = set()
        self.phases: set[str] = set()

    def load(self) -> None:
        data = self.tree.read(PROGRESS_PATH)
        if data is None:
            return
        if len(data) > MAX_PROGRESS_BYTES:
            raise LifecycleError("progress", "removal journal exceeds the bounded byte budget")
        try:
            document = json.loads(data)
        except (ValueError, UnicodeError) as error:
            raise LifecycleError("progress", "removal journal is corrupt") from error
        if (not isinstance(document, dict) or set(document) != _PROGRESS_FIELDS
                or document.get("version") != PROGRESS_SCHEMA):
            raise LifecycleError("progress", "removal journal schema is invalid")
        phases, removed = document.get("phases"), document.get("removed")
        if (not isinstance(phases, list) or not isinstance(removed, list)
                or not all(isinstance(item, str) and item in _PROGRESS_PHASES for item in phases)
                or not all(isinstance(item, str) for item in removed)):
            raise LifecycleError("progress", "removal journal entries are invalid")
        if len(removed) > MAX_PROGRESS_ITEMS:
            raise LifecycleError("progress", "removal journal exceeds the bounded item budget")
        self.phases = set(phases)
        self.done = set(removed)

    def save(self) -> None:
        if len(self.done) > MAX_PROGRESS_ITEMS:
            raise LifecycleError("progress", "removal journal item budget exhausted")
        encoded = json.dumps({"version": PROGRESS_SCHEMA, "phases": sorted(self.phases),
                              "removed": sorted(self.done)}, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_PROGRESS_BYTES:
            raise LifecycleError("progress", "removal journal byte budget exhausted")
        with ExitStack() as stack:
            parent = self.tree.pin(stack, str(Path(PROGRESS_PATH).parent))
            temporary = ".sparehash-" + uuid4().hex
            raw = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                with os.fdopen(raw, "wb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    _ = stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, Path(PROGRESS_PATH).name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                with suppress(FileNotFoundError):
                    os.unlink(temporary, dir_fd=parent)

    def record(self, item: str) -> None:
        self.done.add(item)
        self.save()

    def phase(self, name: str) -> None:
        self.phases.add(name)
        self.save()

    def clear(self) -> None:
        with ExitStack() as stack:
            parent = self.tree.pin(stack, str(Path(PROGRESS_PATH).parent))
            try:
                os.unlink(Path(PROGRESS_PATH).name, dir_fd=parent)
            except FileNotFoundError:
                return
            os.fsync(parent)



def _run(argv: tuple[str, ...]) -> None:
    _ = checked(argv, 30)


@dataclass(frozen=True, slots=True)
class Infrastructure:
    """Privileged side effects; production defaults, injectable for offline tests."""

    run: Callable[[tuple[str, ...]], None] = _run
    cgroup_empty: Callable[[], bool] = empty
    free_hugepages: Callable[[], object] = lambda: LeaseManager(empty_proof=empty).cleanup()
    unmerge: Callable[[str], object] = lambda name: _gaming_unmerge(name)
    user_lookup: Callable[[str], tuple[int, int] | None] = _nss_user
    group_lookup: Callable[[str], int | None] = _nss_group


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


def _present(tree: Tree, name: str) -> bool:
    """True when the pinned name exists; only a genuine absence is reported False."""
    try:
        with ExitStack() as stack:
            parent = tree.pin(stack, str(Path(name).parent))
            try:
                fd = os.open(Path(name).name, _FILE_FLAGS, dir_fd=parent)
            except FileNotFoundError:
                return False
            except OSError as error:
                raise LifecycleError("receipt", "unsafe artifact: " + name) from error
            os.close(fd)
    except FileNotFoundError:
        return False
    return True


def _remove_file(tree: Tree, artifact: Artifact, progress: RemovalJournal) -> None:
    if not _present(tree, artifact.path):
        if artifact.id in progress.done:
            return
        raise LifecycleError("receipt", "owned artifact missing before removal: " + artifact.path)
    try:
        current = tree.receipt(artifact.path, artifact.created, identity=artifact.id)
    except DeploymentError as error:
        raise LifecycleError("receipt", "unauthorized or missing artifact: " + artifact.path) from error
    if _identity(current) != _identity(artifact):
        raise LifecycleError("receipt", "artifact changed since install: " + artifact.path)
    progress.record(artifact.id)
    with ExitStack() as stack:
        parent = tree.pin(stack, str(Path(artifact.path).parent))
        os.unlink(Path(artifact.path).name, dir_fd=parent)
        os.fsync(parent)


def _remove_owned_files(tree: Tree, installer: Installer, purge: bool, progress: RemovalJournal) -> None:
    wanted: set[str] = set(EXTRA_FILES)
    if purge:
        wanted.update(CONFIG)
    for artifact in installer.artifacts:
        if artifact.created and artifact.kind == "file" and artifact.path in wanted:
            _remove_file(tree, artifact, progress)


def _remove_accounts(run: Callable[[tuple[str, ...]], None], installer: Installer,
                     user_lookup: Callable[[str], tuple[int, int] | None] = _nss_user,
                     group_lookup: Callable[[str], int | None] = _nss_group) -> None:
    for membership in installer.memberships:
        if not membership.added:
            continue
        resolved_member = user_lookup(membership.user)
        if resolved_member is not None and resolved_member[0] != membership.uid:
            raise LifecycleError("account", "membership user identity changed: " + membership.user)
        resolved_group = group_lookup(membership.group)
        if resolved_group is not None and resolved_group != membership.gid:
            raise LifecycleError("account", "membership group identity changed: " + membership.group)
        run(("/usr/bin/gpasswd", "-d", membership.user, membership.group))
    # Remove users before groups so that a user's primary group can be deleted.
    for identity in installer.identities:
        if not identity.created:
            continue
        if isinstance(identity, UserIdentity):
            resolved = user_lookup(identity.name)
            if resolved is not None and resolved[0] != identity.uid:
                raise LifecycleError("account", "same-name foreign user; refusing deletion: " + identity.name)
            run(("/usr/sbin/userdel", identity.name))
    for identity in installer.identities:
        if not identity.created:
            continue
        if isinstance(identity, GroupIdentity):
            resolved_gid = group_lookup(identity.name)
            if resolved_gid is not None and resolved_gid != identity.gid:
                raise LifecycleError("account", "same-name foreign group; refusing deletion: " + identity.name)
            try:
                run(("/usr/sbin/groupdel", identity.name))
            except DeploymentError as error:
                # userdel removes a user's primary group automatically; exit 6 means already gone.
                if "exited 6:" not in str(error):
                    raise


def uninstall(*, purge: bool = False, geteuid: Callable[[], int] = os.geteuid, trust: PathTrust = _DEFAULT_TRUST,
              client: Client = ipc.call, infrastructure: Infrastructure | None = None,
              clock: Callable[[], float] = time.monotonic, pause: Callable[[float], None] = time.sleep,
              request_id: str | None = None, lifecycle_path: Path = lifecycle.LIFECYCLE_PATH) -> int:
    """Stop, verify empty, restore hugepages, then remove only receipt-owned artifacts."""
    if geteuid() != 0:
        print("sparehash: uninstall requires root", file=sys.stderr)
        return 2
    infra = infrastructure or Infrastructure()
    tree = Tree(trust.root, trust.uid)
    try:
        installer = _receipts(trust)
        progress = RemovalJournal(tree)
        progress.load()
        lease = LeaseStore(lifecycle_path.parent / "lifecycle-lease.json").load()
        if lease is not None and lease.purpose != "uninstall":
            raise LeaseError(f"active {lease.purpose} hold retained; uninstall refused")
        retire(lifecycle_path, trust=trust)
        infra.run(("/usr/bin/systemctl", "disable", "--now", "sparehash.service"))
        infra.run(("/usr/bin/systemctl", "stop", "sparehash-xmrig.service"))
        if not infra.cgroup_empty():
            raise LifecycleError("cgroup", "miner cgroup is not empty; artifacts retained")
        _ = infra.free_hugepages()
        shim = next((artifact for artifact in installer.artifacts if artifact.path == SHIM), None)
        if shim is None or _identity(tree.receipt(SHIM, shim.created)) != _identity(shim):
            raise LifecycleError("receipt", "trusted cleanup CLI differs from its receipt")
        infra.run((SHIM, "__cleanup__"))
        for artifact in installer.artifacts:
            if artifact.created and artifact.id.startswith("gaming-ini:"):
                _ = infra.unmerge(artifact.id.removeprefix("gaming-ini:"))
        if "leaf-receipts" not in progress.phases:
            _ = remove_leaf_receipts(trust, tree=tree, progress=progress)
            progress.phase("leaf-receipts")
        if "versions" not in progress.phases:
            _ = remove_owned_versions(trust, tree=tree, progress=progress)
            progress.phase("versions")
        _remove_owned_files(tree, installer, purge, progress)
        _remove_accounts(infra.run, installer, infra.user_lookup, infra.group_lookup)
        infra.run(("/usr/bin/systemctl", "daemon-reload"))
        progress.clear()
    except (OperationError, LifecycleError, LeaseError, InstallError, DeploymentError, OSError) as error:
        print(f"sparehash: uninstall: {error}", file=sys.stderr)
        return 1
    print("SpareHash removed." if purge else "SpareHash removed; configuration retained.")
    return 0
