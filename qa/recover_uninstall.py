# SPDX-License-Identifier: GPL-3.0-or-later
"""One-shot recovery for the f348ed1 retired, source-removed uninstall only.

Not dispatched by the installed CLI. recovery_stage.py verifies protected source
before importing this module. Original authority stays in the canonical manifest
and durable removal journal, never in a caller-supplied replacement receipt.
"""

from __future__ import annotations

import grp
import hashlib
import json
import os
import stat
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import Any

from recovery_stage import ORIGINAL, unique

from sparehash import cli_uninstall as uninstall
from sparehash.deployment import DeploymentError, Tree, checked
from sparehash.hugepages import LeaseManager, _count
from sparehash.install_manifest import StoreContext, locked_store
from sparehash.layout import probe
from sparehash.lifecycle_lease import _decode
from sparehash.runtime import _remove_runtime
from sparehash.runtime_jobs import empty
from sparehash.xmrig_ledger import GroupIdentity, Installer, UserIdentity, leaf_identity
from sparehash.xmrig_manifest import Manifest, VersionRecord, format_manifest, inventory_digest
from sparehash.xmrig_paths import FILE_FLAGS

PLAN = "recovery-v1:"


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise DeploymentError("recovery", reason)


def private_json(tree: Tree, name: str, mode: int, limit: int) -> Any:
    with ExitStack() as stack:
        parent = tree.pin(stack, str(Path(name).parent))
        fd = os.open(Path(name).name, FILE_FLAGS, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == tree.uid and info.st_nlink == 1
                    and stat.S_IMODE(info.st_mode) == mode and 0 < info.st_size <= limit, "unsafe protected state")
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            require(len(data) == info.st_size and info.st_ctime_ns == after.st_ctime_ns
                    and info.st_mtime_ns == after.st_mtime_ns, "state changed during read")
    return json.loads(data, object_pairs_hook=unique)


def inactive() -> None:
    for name in ("sparehash.service", "sparehash-xmrig.service"):
        fields = dict(line.split("=", 1) for line in checked(("/usr/bin/systemctl", "show", name,
                      "--property=LoadState,ActiveState,SubState,MainPID"), 2).splitlines() if "=" in line)
        require(fields.get("LoadState") in ("loaded", "not-found")
                and fields.get("ActiveState") == "inactive" and fields.get("SubState") == "dead"
                and fields.get("MainPID") == "0", "both services must be inactive/dead with PID 0")
    require(empty(), "miner cgroup populated or unverifiable")


def provenance(manifest: Manifest, descriptor: dict, progress: uninstall.RemovalJournal, tree: Tree) -> Installer:
    require(descriptor["original_commit"] == ORIGINAL, "unsupported original source")
    expected = descriptor["original_files"]
    require(manifest.schema == 2 and manifest.inventory_digest in (None, inventory_digest(expected.items())),
            "original source inventory is not f348ed1")
    require(tree.layout is not None and (manifest.layout is None or manifest.layout == {
        "opt_root": str(tree.layout.opt_physical), "local_root": str(tree.layout.local_physical)}),
        "original layout mapping differs from protected filesystem")
    installer = manifest.installer
    require(installer is not None and installer.phase == "ready", "original ready receipts required")
    assert installer is not None
    leaves = {artifact.path: artifact for artifact in installer.artifacts if leaf_identity(artifact.path) is not None}
    require(set(leaves) == {"/opt/sparehash/src/" + name for name in expected}, "original source receipt inventory differs")
    for name, digest in expected.items():
        artifact = leaves["/opt/sparehash/src/" + name]
        require(artifact.created and artifact.uid == tree.uid and artifact.gid == 0
                and artifact.mode == 0o644 and artifact.sha256 == digest,
                "original source receipt hash/ownership differs")
        require(artifact.path in progress.done and not uninstall._present(tree, artifact.path),
                "source absence must be justified by original receipts and write-ahead journal")
    for directory, allowed in (("/opt/sparehash/src/sparehash", set()), ("/opt/sparehash/src/systemd", set()),
                               ("/opt/sparehash/src", {"sparehash", "systemd"}), ("/opt/sparehash", {"src"})):
        if uninstall._present(tree, directory):
            receipt = next((artifact for artifact in installer.artifacts if artifact.path == directory), None)
            require(receipt is not None and uninstall._identity(tree.receipt(directory, receipt.created)) == uninstall._identity(receipt),
                    "remaining source container differs from its receipt")
            with ExitStack() as stack:
                require(set(os.listdir(tree.pin(stack, directory))) <= allowed, "foreign source-container content; retained")
    shim = next((artifact for artifact in installer.artifacts if artifact.path == uninstall.SHIM), None)
    require(shim is not None and shim.created and shim.uid == tree.uid and shim.gid == 0 and shim.mode == 0o755
            and shim.sha256 in descriptor["original_shims"].values(), "original shim provenance differs")
    # Config contents are deliberately neither read nor compared: non-purge recovery
    # never deletes/rewrites private configuration, including owner-edited credentials.
    extra = [artifact for artifact in installer.artifacts if artifact.path in uninstall.EXTRA_FILES]
    require({artifact.path for artifact in extra} == set(uninstall.EXTRA_FILES)
            and all(artifact.created and artifact.uid == tree.uid for artifact in extra),
            "this recovery requires the original owned launcher and both owned units")
    for artifact in installer.artifacts:
        if artifact.path not in uninstall.EXTRA_FILES:
            continue
        if uninstall._present(tree, artifact.path):
            with ExitStack() as stack:
                parent = tree.pin(stack, str(Path(artifact.path).parent))
                fd = os.open(Path(artifact.path).name, FILE_FLAGS, dir_fd=parent)
                stack.callback(os.close, fd)
                require(os.fstat(fd).st_nlink == 1, "linked original artifact; retained")
            require(uninstall._identity(tree.receipt(artifact.path, artifact.created)) == uninstall._identity(artifact),
                    "remaining original artifact differs from its exact receipt")
        else:
            require(artifact.id in progress.done, "remaining artifact missing without write-ahead evidence")
    known = set(leaves) | {artifact.id for artifact in installer.artifacts if artifact.path in uninstall.EXTRA_FILES}
    require(all(item in known or item.startswith(PLAN) for item in progress.done), "unidentified progress entries")
    return installer


def runtime_groups(installer: Installer) -> tuple[int, int]:
    groups = {identity.name: identity.gid for identity in installer.identities if isinstance(identity, GroupIdentity)}
    require(set(groups) == {"xmrig", "sparehash"}, "original runtime group receipts required")
    return groups["sparehash"], groups["xmrig"]


def old_runtime(tree: Tree, installer: Installer, *, remove: bool) -> None:
    ipc_gid, miner_gid = runtime_groups(installer)
    for directory, gid, names, socket_file in (
        ("/run/sparehash", ipc_gid, ("control.sock",), True),
        ("/run/sparehash-miner", miner_gid, ("operation.json", "benchmark.json"), False),
    ):
        try:
            with ExitStack() as stack:
                fd = tree.pin(stack, directory)
                info = os.fstat(fd)
                if (directory == "/run/sparehash" and info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o711
                        and set(os.listdir(fd)) == {"recovery"}):
                    continue  # Staging-only scaffold created when old runtime parent was absent.
                require(info.st_gid == gid and stat.S_IMODE(info.st_mode) == 0o750,
                        "old-layout runtime directory identity differs")
                for name in names:
                    try:
                        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    kind = stat.S_ISSOCK(info.st_mode) if socket_file else stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                    require(kind and info.st_uid == tree.uid and info.st_gid == gid
                            and stat.S_IMODE(info.st_mode) == (0o660 if socket_file else 0o640),
                            "unsafe old-layout runtime artifact")
            if remove:
                _remove_runtime(tree, directory, gid, names, socket_file=socket_file)
        except FileNotFoundError:
            pass


def pages_preflight(tree: Tree, pages: LeaseManager) -> None:
    # No lock file creation and no sysfs/ledger write during read-only preflight.
    with ExitStack() as stack:
        tree.pin(stack, "/var/lib/sparehash")
    if os.path.lexists(pages.ledger_path):
        info = pages.ledger_path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "unsafe hugepage ledger")
        ledger = pages._load()
        require(ledger.applied is not None and ledger.boot_id == pages._boot()
                and ledger.path == str(pages.roots.sys / "kernel/mm/hugepages/hugepages-2048kB/nr_hugepages")
                and _count(Path(ledger.path)) == ledger.applied and pages._empty(), "hugepage lease state is ambiguous")


def version_inventory(tree: Tree) -> dict[str, list[Any]]:
    """Finite deletion plan of exact inodes/content, including directories/current.

    Saved durably in the existing removal journal BEFORE unlinking anything.
    Missing planned entries are legitimate on retry; new or replaced entries are not.
    """
    result: dict[str, list[Any]] = {}
    def walk(fd: int, prefix: str) -> None:
        info = os.fstat(fd)
        result[prefix] = ["directory", info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_dev, info.st_ino, None]
        require(info.st_uid == tree.uid and not info.st_mode & 0o022, "unsafe managed directory")
        for name in sorted(os.listdir(fd)):
            path = prefix + "/" + name if prefix else name
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            require(info.st_uid == tree.uid and not info.st_mode & 0o022 if not stat.S_ISLNK(info.st_mode)
                    else info.st_uid == tree.uid, "foreign/writable managed entry")
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY, dir_fd=fd)
                try:
                    walk(child, path)
                finally:
                    os.close(child)
            elif stat.S_ISLNK(info.st_mode) and path == "current":
                require(info.st_nlink == 1, "linked current entry")
                result[path] = ["symlink", info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_dev, info.st_ino,
                                os.readlink(name, dir_fd=fd)]
            else:
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 128 * 1024 * 1024,
                        "linked/special/oversized managed content")
                raw = os.open(name, FILE_FLAGS, dir_fd=fd)
                with os.fdopen(raw, "rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                    after = os.fstat(stream.fileno())
                require((info.st_ino, info.st_ctime_ns, info.st_mtime_ns) ==
                        (after.st_ino, after.st_ctime_ns, after.st_mtime_ns), "managed entry changed")
                result[path] = ["file", info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_dev, info.st_ino, digest]
    try:
        with ExitStack() as stack:
            walk(tree.pin(stack, "/opt/xmrig"), "")
    except FileNotFoundError:
        require(not os.path.lexists(tree.path("/opt/xmrig")), "missing subtree in managed inventory")
    return result


def plan_versions(tree: Tree, manifest: Manifest, progress: uninstall.RemovalJournal) -> tuple[str, dict]:
    tokens = [item for item in progress.done if item.startswith(PLAN)]
    require(len(tokens) <= 1, "duplicate recovery plan")
    current = version_inventory(tree)
    if tokens:
        plan = json.loads(tokens[0][len(PLAN):], object_pairs_hook=unique)
        require(set(plan) == {"manifest", "versions", "entries"}, "invalid recovery plan schema")
        original = replace(manifest, versions=tuple(VersionRecord(**record) for record in plan["versions"]))
        require(hashlib.sha256(format_manifest(original)).hexdigest() == plan["manifest"], "manifest differs from recovery plan")
        require(not manifest.versions or manifest.versions == original.versions, "partial version authority differs")
        entries = plan["entries"]
        require(isinstance(entries, dict) and "" in entries, "incomplete deletion plan")
        for name, value in entries.items():
            require(name == "" or (not name.startswith("/") and all(part not in ("", ".", "..") for part in name.split("/"))
                    and (name.split("/")[0] in original.names or name in ("current", ".sparehash-managed"))),
                    "unidentified deletion-plan path")
            require(isinstance(value, list) and len(value) == 7 and value[0] in ("file", "directory", "symlink")
                    and all(type(field) is int and field >= 0 for field in value[1:6])
                    and ((value[0] == "directory" and value[6] is None)
                         or (value[0] == "symlink" and name == "current" and value[6] in original.names)
                         or (value[0] == "file" and isinstance(value[6], str) and len(value[6]) == 64)),
                    "malformed deletion-plan identity")
        require(all(name in entries and entries[name] == value for name, value in current.items()),
                "foreign or substituted version content; preserved")
        require("versions" not in progress.phases or not current and not manifest.versions,
                "completed version phase contradicts filesystem/manifest")
        return tokens[0], plan
    require(bool(manifest.versions) and tree.trust.manifest() == manifest, "original protected marker/manifest required")
    require({name for name in current if "/" not in name} == {"", "current", ".sparehash-managed", *manifest.names},
            "unexpected or missing managed root entries")
    require(tree.trust.authorized_current(tree.trust.marker_path, tree.trust.manifest_path) is not None,
            "original current binary is not authorized")
    from dataclasses import asdict

    plan = {"manifest": hashlib.sha256(format_manifest(manifest)).hexdigest(),
            "versions": [asdict(record) for record in manifest.versions], "entries": current}
    return PLAN + json.dumps(plan, sort_keys=True, separators=(",", ":")), plan


def remove_versions(tree: Tree, store: Any, manifest: Manifest, progress: uninstall.RemovalJournal,
                    token: str, plan: dict) -> None:
    if "versions" in progress.phases:
        return
    progress.record(token)  # Full original plan persisted before the first unlink.
    for name in sorted(plan["entries"], key=lambda item: (item.count("/"), len(item)), reverse=True):
        path = "/opt/xmrig" + ("/" + name if name else "")
        try:
            with ExitStack() as stack:
                parent = tree.pin(stack, str(Path(path).parent))
                if plan["entries"][name][0] == "directory":
                    os.rmdir(Path(path).name, dir_fd=parent)
                else:
                    os.unlink(Path(path).name, dir_fd=parent)
                os.fsync(parent)
        except FileNotFoundError:
            pass  # Only exact paths in the previously verified durable plan.
    store.write(replace(manifest, versions=()))
    progress.phase("versions")


def accounts(installer: Installer, *, apply: bool) -> bool:
    infra = uninstall.Infrastructure()
    for identity in installer.identities:
        found = infra.user_lookup(identity.name) if isinstance(identity, UserIdentity) else infra.group_lookup(identity.name)
        expected = (identity.uid, identity.gid) if isinstance(identity, UserIdentity) else identity.gid
        require(found is None or found == expected, "same-name foreign account; retained")
    for membership in installer.memberships:
        user, group = infra.user_lookup(membership.user), infra.group_lookup(membership.group)
        require((user is None or user[0] == membership.uid) and (group is None or group == membership.gid),
                "foreign membership identity")
        if (apply and membership.added and user is not None and group is not None
                and membership.user in grp.getgrnam(membership.group).gr_mem):
            infra.run(("/usr/bin/gpasswd", "-d", membership.user, membership.group))
    if apply:
        for identity in installer.identities:
            if isinstance(identity, UserIdentity) and identity.created and infra.user_lookup(identity.name) is not None:
                infra.run(("/usr/sbin/userdel", identity.name))
        for identity in installer.identities:
            if isinstance(identity, GroupIdentity) and identity.created and infra.group_lookup(identity.name) is not None:
                infra.run(("/usr/sbin/groupdel", identity.name))
    return all(not identity.created or (infra.user_lookup(identity.name) if isinstance(identity, UserIdentity)
               else infra.group_lookup(identity.name)) is None for identity in installer.identities)


def recover(descriptor: dict, *, apply: bool = False, completed_only: bool = False) -> None:
    require(os.geteuid() == 0, "root required")
    with ExitStack() as stack:
        layout = probe(Path("/"))
        stack.callback(layout.close)
        tree = Tree(layout=layout)
        store = stack.enter_context(locked_store(StoreContext(tree.trust, gid=0)))
        inactive()
        latch = private_json(tree, "/var/lib/sparehash/lifecycle.json", 0o644, 4096)
        require(isinstance(latch, dict) and set(latch) in ({"retired", "timestamp"}, {"retired", "timestamp", "operation_id"})
                and latch["retired"] is True and type(latch["timestamp"]) is int, "valid original retirement latch required")
        lease_path = "/var/lib/sparehash/lifecycle-lease.json"
        require("operation_id" not in latch or uninstall._present(tree, lease_path), "retirement operation has no matching lease")
        if uninstall._present(tree, lease_path):
            lease = private_json(tree, lease_path, 0o600, 4096)
            decoded = _decode(json.dumps(lease))
            require(decoded.purpose == "uninstall" and ("operation_id" not in latch or latch["operation_id"] == decoded.token),
                    "conflicting lifecycle lease")
        document = private_json(tree, uninstall.PROGRESS_PATH, 0o600, uninstall.MAX_PROGRESS_BYTES)
        progress = uninstall.RemovalJournal(tree)
        progress.load()
        require(type(document.get("version")) is int and len(document["removed"]) == len(progress.done)
                and len(document["phases"]) == len(progress.phases) and "leaf-receipts" in progress.phases,
                "original completed leaf-removal journal required")
        snapshot = store.read()
        require(snapshot is not None, "canonical manifest missing")
        assert snapshot is not None
        manifest = snapshot.manifest
        installer = provenance(manifest, descriptor, progress, tree)
        token, plan = plan_versions(tree, manifest, progress)
        accounts_gone = accounts(installer, apply=False)
        pages = LeaseManager(empty_proof=empty)
        pages_preflight(tree, pages)
        old_runtime(tree, installer, remove=False)
        complete = ("versions" in progress.phases and accounts_gone
                    and all(not uninstall._present(tree, name) for name in uninstall.EXTRA_FILES))
        if completed_only:
            require(complete, "recovery not complete; staging retained")
        print("Recovery preflight: original provenance/receipts/journals/empty proof/old runtime validated; "
              + ("already removed." if complete else "non-purge recovery eligible."))
        if not apply or complete:
            return
        # Recheck launch exclusion at the mutation boundary. No shim execution,
        # config reads, controller start, or latch clearing anywhere in recovery.
        inactive()
        if uninstall._present(tree, uninstall.CONTROLLER_UNIT):
            checked(("/usr/bin/systemctl", "disable", "sparehash.service"), 10)
        result = pages.cleanup()
        require(result.warning is None, "hugepage cleanup refused; all removal authority retained")
        inactive()
        old_runtime(tree, installer, remove=True)
        remove_versions(tree, store, manifest, progress, token, plan)
        uninstall._remove_owned_files(tree, installer, False, progress)
        require(accounts(installer, apply=True), "owned accounts still present after removal")
        require(all(not uninstall._present(tree, name) for name in uninstall.EXTRA_FILES),
                "owned launcher or unit remains after removal")
        checked(("/usr/bin/systemctl", "daemon-reload"), 10)
        inactive()
        # Keep the exact original journal as durable completion/retry evidence.
        # A separate explicit reinstall replaces ownership; this source cannot
        # manage that new installation because provenance no longer matches.
        print("Recovery complete; configuration and retirement retained. Remove verified staging before reinstall.")
