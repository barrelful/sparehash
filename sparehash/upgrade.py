# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors
"""One app transaction and previous-app backup; recovery restores, never rolls forward.

The journal reserves mutations under short manifest locks. Service/IPC waits never
hold that lock. Publication runs from the verified protected backup source, not
the source being replaced. Committed transactions can only finish finalisation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import time
from collections.abc import Callable
from contextlib import ExitStack, suppress
from dataclasses import dataclass, replace
from pathlib import Path

from . import config, ipc, systemd, xmrig_config
from .app_release import _literal, version_tuple
from .bootstrap import MAX_MODULES, SHIM_SOURCE, SHIM_TEMPLATE, _collect_source
from .cli_lifecycle import acquire, await_ready
from .deployment import ENV, DeploymentError, Tree, checked, command
from .hugepages import LeaseManager
from .install_manifest import StoreContext, locked_store
from .lifecycle import is_retired
from .lifecycle_lease import ActiveLease, LeaseStore
from .runtime_jobs import empty
from .sysdetect import Capabilities
from .update_record import BACKUP, JOURNAL, PRESERVED, UpdateRecord, load, save
from .xmrig_ledger import PACKAGE_LEAF, UNIT_TEMPLATES, Action, Artifact, leaf_identity
from .xmrig_manifest import Manifest, format_manifest, inventory_digest, parse_manifest

SOURCE = "/opt/sparehash/src"
LAUNCHER = "/usr/local/bin/sparehash"
UNITS = "/etc/systemd/system"
FALLBACK = "sudo /usr/bin/python3 -I -B /opt/sparehash/.update-backup/src/sparehash/__main__.py update --recover"
INDEX = "inventory.json"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unlink(tree: Tree, path: str, *, missing_ok: bool = False) -> None:
    with ExitStack() as stack:
        parent = tree.pin(stack, str(Path(path).parent))
        try:
            os.unlink(Path(path).name, dir_fd=parent)
        except FileNotFoundError:
            if not missing_ok:
                raise
        os.fsync(parent)


def _rmdir(tree: Tree, path: str) -> None:
    with ExitStack() as stack:
        parent = tree.pin(stack, str(Path(path).parent))
        os.rmdir(Path(path).name, dir_fd=parent)
        os.fsync(parent)


def pending(tree: Tree) -> None:
    """Call under the existing store lock before any competing mutation."""
    try:
        data = tree.read(JOURNAL)
    except FileNotFoundError:
        return
    if data is not None:
        raise DeploymentError("update", "pending app update; run sparehash update --recover first")


def _property(name: str) -> str:
    return checked(("/usr/bin/systemctl", "show", "sparehash.service", f"--property={name}", "--value"))


def _active() -> bool:
    state = _property("ActiveState")
    if state not in ("active", "inactive", "failed"):
        raise DeploymentError("service", f"controller is {state}; wait for its current transition")
    return state == "active"


def _boot() -> str:
    state = command(("/usr/bin/systemctl", "is-enabled", "sparehash.service")).stdout.strip()
    if state not in ("enabled", "disabled"):
        raise DeploymentError("service", f"unsupported controller startup state: {state}")
    return state


def _stop() -> None:
    checked(("/usr/bin/systemctl", "stop", "sparehash.service", "sparehash-xmrig.service"), 60)


def _proof() -> None:
    if not empty():
        raise DeploymentError("update-stop", "miner cgroup is not empty; mining remains inhibited")
    result = LeaseManager(empty_proof=empty).cleanup()
    if result.warning is not None:
        raise DeploymentError("update-stop", f"owned hugepage cleanup failed: {result.warning}")


def _ready() -> None:
    checked(("/usr/bin/systemctl", "start", "sparehash.service"), 60)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            row = ipc.call({"cmd": "status"}, timeout=1)
            if row.get("ok") is True and row.get("ready") is True and row.get("running") is False and empty():
                return
        except ipc.IPCError:
            pass
        time.sleep(0.2)
    raise DeploymentError("update-activation", "controller readiness/empty proof failed under update inhibition")


def _version(source: Path) -> None:
    version, commit = identity(source)
    try:
        for module in (source / "sparehash").glob("*.py"):
            compile(module.read_bytes(), str(module), "exec", dont_inherit=True)
    except SyntaxError as error:
        raise DeploymentError("update-preflight", f"candidate module syntax failed: {error.filename}") from error
    output = checked(("/usr/bin/python3", "-I", "-B", str(source / "sparehash/__main__.py"), "--version"), 30)
    if output != f"SpareHash {version} (commit {commit})":
        raise DeploymentError("update-identity", "installed isolated CLI identity mismatch")


def _configuration(path: Path) -> object:
    cfg = config.load(path)
    token = xmrig_config.read_token(path.with_name("xmrig.json"))
    if xmrig_config.read_token(path.with_name("xmrig.runtime.json")) != token:
        raise DeploymentError("update-config", "credential copies disagree; automatic rotation is refused")
    return cfg


@dataclass(frozen=True)
class Infrastructure:
    active: Callable[[], bool] = _active
    boot: Callable[[], str] = _boot
    stop: Callable[[], None] = _stop
    ready: Callable[[], None] = _ready
    proof: Callable[[], None] = _proof
    reload: Callable[[], object] = lambda: checked(("/usr/bin/systemctl", "daemon-reload"), 30)
    verify_units: Callable[[Path], object] = systemd.verify_units
    verify_identity: Callable[[Path], None] = _version
    validate_config: Callable[[Path], object] = _configuration


def identity(source: Path) -> tuple[str, str]:
    version = _literal(source / "sparehash/__init__.py", "__version__")
    version_tuple(version)
    commit = _literal(source / "sparehash/_build.py", "COMMIT")
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise DeploymentError("update-identity", "installation/candidate requires a full source commit stamp")
    return version, commit


def preserved(tree: Tree) -> dict[str, str | None]:
    return {name: None if (data := tree.read(name)) is None else digest(data) for name in PRESERVED}


def _preservation(tree: Tree, record: UpdateRecord, infra: Infrastructure) -> None:
    if preserved(tree) != record.preserved or infra.boot() != record.boot:
        raise DeploymentError("update-preservation", "configuration/manual state or boot preference changed; hold retained")


def _bundle_allowed(name: str, backup: bool) -> bool:
    if name in ("launcher", *("units/" + unit.removesuffix(".in") for unit in UNIT_TEMPLATES)):
        return True
    if name == "manifest.json":
        return backup
    parts = name.split("/")
    return (len(parts) == 3 and parts[0] == "src" and
            ((parts[1] == "sparehash" and PACKAGE_LEAF.fullmatch(parts[2]) is not None)
             or (parts[1] == "systemd" and parts[2] in UNIT_TEMPLATES)))


def _scan_bundle(tree: Tree, root: str, backup: bool, *, complete: bool = True) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    expected_dirs = {"src", "src/sparehash", "src/systemd", "units"}
    # Pin ancestors and reject foreign ownership/modes before walking; no links
    # are followed by the walk, and every leaf is read through Tree's pins.
    with ExitStack() as stack:
        tree.pin(stack, root)
    for base, dirs, files in os.walk(tree.path(root), followlinks=False):
        for name in dirs:
            path = Path(base) / name
            relative = str(path.relative_to(tree.path(root)))
            if relative not in expected_dirs or path.is_symlink():
                raise DeploymentError("update-bundle", "foreign directory in update material")
        for name in files:
            relative = str((Path(base) / name).relative_to(tree.path(root)))
            if relative == INDEX:
                continue
            if not _bundle_allowed(relative, backup) or len(result) >= MAX_MODULES + 6:
                raise DeploymentError("update-bundle", "foreign or excessive update material")
            receipt = tree.receipt(root + "/" + relative, True)
            if receipt.kind != "file" or receipt.uid != tree.uid or receipt.mode & 0o022:
                raise DeploymentError("update-bundle", "unsafe update material")
            result[relative] = {"sha256": receipt.sha256, "mode": receipt.mode, "gid": receipt.gid}
    required = {"src/sparehash/__main__.py", "src/sparehash/__init__.py", "src/sparehash/_build.py",
                "src/sparehash/upgrade.py", "launcher", *("units/" + name.removesuffix(".in") for name in UNIT_TEMPLATES)}
    if backup:
        required.add("manifest.json")
    if complete and not required <= result.keys():
        raise DeploymentError("update-bundle", "incomplete update material")
    return result


def seal(tree: Tree, root: str, *, backup: bool) -> str:
    index = _scan_bundle(tree, root, backup)
    data = json.dumps(index, sort_keys=True, separators=(",", ":")).encode()
    tree.write(root + "/" + INDEX, (data, 0o600, 0))
    # Files and their immediate parent are fsynced by Tree.write; flush all
    # created directory entries before announcing a ready backup in the journal.
    for directory in ("src/sparehash", "src/systemd", "src", "units", ""):
        with ExitStack() as stack:
            os.fsync(tree.pin(stack, root + ("/" + directory if directory else "")))
    return digest(data)


def bundle(tree: Tree, root: str, expected: str | None = None, *, backup: bool) -> dict[str, dict[str, object]]:
    data = tree.read(root + "/" + INDEX)
    if data is None or (expected is not None and digest(data) != expected):
        raise DeploymentError("update-backup", "update inventory missing or digest mismatched")
    current = _scan_bundle(tree, root, backup)
    if json.dumps(current, sort_keys=True, separators=(",", ":")).encode() != data:
        raise DeploymentError("update-backup", "update material no longer matches protected inventory")
    return current


def discard_bundle(tree: Tree, root: str, *, backup: bool, expected: str | None = None) -> None:
    """Delete only validated leaves; replay a partially interrupted cleanup."""
    data = tree.read(root + "/" + INDEX)
    if data is None:
        # Index is removed only after all leaves/subdirectories. A crash between
        # index unlink and final rmdir leaves at most this empty owned directory.
        if any(tree.path(root).iterdir()):
            raise DeploymentError("update-cleanup", "unindexed nonempty update material retained")
        _rmdir(tree, root)
        return
    if expected is not None and digest(data) != expected:
        raise DeploymentError("update-cleanup", "update inventory changed")
    try:
        entries = json.loads(data)
        if not isinstance(entries, dict) or len(entries) > MAX_MODULES + 6 or not all(
            isinstance(name, str) and _bundle_allowed(name, backup) and isinstance(meta, dict)
            and set(meta) == {"sha256", "mode", "gid"} for name, meta in entries.items()
        ):
            raise ValueError("invalid cleanup inventory")
    except (ValueError, UnicodeError) as error:
        raise DeploymentError("update-cleanup", "invalid cleanup inventory") from error
    current = _scan_bundle(tree, root, backup, complete=False)
    if not all(name in entries and meta == entries[name] for name, meta in current.items()):
        raise DeploymentError("update-cleanup", "foreign or modified update material retained")
    for relative in current:
        _unlink(tree, root + "/" + relative)
    for name in ("src/sparehash", "src/systemd", "src", "units"):
        with suppress(FileNotFoundError):
            _rmdir(tree, root + "/" + name)
    _unlink(tree, root + "/" + INDEX)
    _rmdir(tree, root)


def _new_bundle(tree: Tree, root: str) -> None:
    if os.path.lexists(tree.path(root)):
        raise DeploymentError("update-bundle", "refusing existing update material: " + root)
    tree.directory(root, 0)
    with ExitStack() as stack:
        fd = tree.pin(stack, root)
        os.fchmod(fd, 0o700)
        os.fsync(fd)
    for name in ("src", "src/sparehash", "src/systemd", "units"):
        tree.directory(root + "/" + name, 0)


def candidate_bundle(tree: Tree, root: str, source: Path, caps: Capabilities) -> str:
    """Only authenticated runtime source/templates enter the transaction bundle."""
    files = _collect_source(source, MAX_MODULES)
    for required in ("__init__.py", "_build.py", "__main__.py", "upgrade.py", "cli_update.py", "runtime.py", "config.py"):
        if "sparehash/" + required not in files:
            raise DeploymentError("update-candidate", "missing required runtime module: " + required)
    identity(source)
    _new_bundle(tree, root)
    for relative, path in files.items():
        tree.write(root + "/src/" + relative, (path.read_bytes(), 0o644, 0))
    units = systemd.render(caps, templates=source / "systemd")
    for unit in systemd.UNIT_NAMES:
        tree.write(root + "/units/" + unit, (units[unit].encode(), 0o644, 0))
    shim = SHIM_TEMPLATE.format(source=str(tree.path(SHIM_SOURCE))).encode()
    tree.write(root + "/launcher", (shim, 0o755, 0))
    return seal(tree, root, backup=False)


def _live_name(relative: str) -> str:
    if relative.startswith("src/"):
        return SOURCE + "/" + relative.removeprefix("src/")
    if relative.startswith("units/"):
        return UNITS + "/" + relative.removeprefix("units/")
    if relative == "launcher":
        return LAUNCHER
    raise DeploymentError("update-path", "not an application publication path")


def _is_app(artifact: Artifact) -> bool:
    return (artifact.path == SOURCE or artifact.path.startswith(SOURCE + "/")
            or artifact.path == LAUNCHER or artifact.path in (UNITS + "/" + unit for unit in systemd.UNIT_NAMES))


def _manifest(tree: Tree) -> Manifest:
    with locked_store(StoreContext(tree.trust)) as store:
        prior = store.read()
    if prior is None or prior.manifest.installer is None or prior.manifest.installer.phase != "ready":
        raise DeploymentError("update-receipts", "requires a ready receipt-owned installation")
    if any(action.state != "applied" for action in prior.manifest.installer.actions):
        raise DeploymentError("update-receipts", "installation has unresolved actions")
    return prior.manifest


def validate_live(tree: Tree, manifest: Manifest) -> None:
    installer = manifest.installer
    assert installer is not None
    if set(os.listdir(tree.path(SOURCE))) != {"sparehash", "systemd"}:
        raise DeploymentError("update-receipts", "foreign source-root contents")
    for directory in ("sparehash", "systemd"):
        if any(not entry.is_file() or entry.is_symlink()
               or (PACKAGE_LEAF.fullmatch(entry.name) is None if directory == "sparehash" else entry.name not in UNIT_TEMPLATES)
               for entry in (tree.path(SOURCE) / directory).iterdir()):
            raise DeploymentError("update-receipts", "foreign source directory contents")
    expected = {a.path for a in installer.artifacts if _is_app(a)}
    inventory = _collect_source(tree.path(SOURCE), MAX_MODULES)
    actual = {SOURCE, SOURCE + "/sparehash", SOURCE + "/systemd", LAUNCHER,
              *(SOURCE + "/" + name for name in inventory), *(UNITS + "/" + unit for unit in systemd.UNIT_NAMES)}
    if expected != actual:
        raise DeploymentError("update-receipts", "foreign/missing application files or directory receipts")
    for artifact in installer.artifacts:
        if _is_app(artifact) and tree.receipt(artifact.path, artifact.created, identity=artifact.id) != artifact:
            raise DeploymentError("update-receipts", "modified or foreign application artifact: " + artifact.path)
    version, _ = identity(tree.path(SOURCE))
    if version_tuple(version) < (0, 3, 0):
        raise DeploymentError("update-version", "updates require a fresh v0.3+ installation")


def _backup(tree: Tree, manifest: Manifest) -> str:
    if os.path.lexists(tree.path(BACKUP)):
        discard_bundle(tree, BACKUP, backup=True)
    _new_bundle(tree, BACKUP)
    assert manifest.installer is not None
    for artifact in manifest.installer.artifacts:
        if not _is_app(artifact) or artifact.kind != "file":
            continue
        if artifact.path.startswith(SOURCE + "/"):
            relative = "src/" + artifact.path.removeprefix(SOURCE + "/")
        elif artifact.path == LAUNCHER:
            relative = "launcher"
        else:
            relative = "units/" + Path(artifact.path).name
        data = tree.read(artifact.path)
        if data is None:
            raise DeploymentError("update-backup", "application file disappeared")
        tree.write(BACKUP + "/" + relative, (data, artifact.mode, artifact.gid))
    tree.write(BACKUP + "/manifest.json", (format_manifest(manifest), 0o600, 0))
    return seal(tree, BACKUP, backup=True)


def _clean_unfinished_backup(tree: Tree) -> None:
    """A pre-publication copy failure never creates restoration authority.

    Validate any unsealed partial copy against the still-intact old installation
    before deleting it. A sealed prior backup has its own bounded inventory.
    """
    if not os.path.lexists(tree.path(BACKUP)):
        return
    for directory in (BACKUP, BACKUP + "/src/sparehash", BACKUP + "/src/systemd", BACKUP + "/units"):
        _clean_write_temps(tree, directory)
    if tree.read(BACKUP + "/" + INDEX) is not None:
        discard_bundle(tree, BACKUP, backup=True)
        return
    entries = _scan_bundle(tree, BACKUP, True, complete=False)
    manifest_bytes = format_manifest(_manifest(tree))
    for name, meta in entries.items():
        expected = manifest_bytes if name == "manifest.json" else tree.read(_live_name(name))
        if expected is None or digest(expected) != meta["sha256"]:
            raise DeploymentError("update-backup", "unsealed backup contains foreign content; retained")
    data = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    tree.write(BACKUP + "/" + INDEX, (data, 0o600, 0))
    discard_bundle(tree, BACKUP, backup=True)


def _ensure_hold(tree: Tree, record: UpdateRecord) -> None:
    store = LeaseStore(tree.path("/var/lib/sparehash/lifecycle-lease.json"))
    current = store.load()
    if current is not None:
        if current.purpose != "update" or current.request_id != record.operation_id:
            raise DeploymentError("update-hold", "unrelated lifecycle hold retained")
        return
    store.save(ActiveLease(secrets.token_hex(16), record.operation_id, "update"))


def prepare(tree: Tree, stage: str, stage_digest: str, infra: Infrastructure) -> UpdateRecord:
    """Reserve, quiesce, capture last manual pause, then durably back up."""
    active, boot = infra.active(), infra.boot()
    with locked_store(StoreContext(tree.trust)):
        pending(tree)
        if is_retired(tree.path("/var/lib/sparehash/lifecycle.json")):
            raise DeploymentError("update", "retired installation")
        if tree.read("/var/lib/sparehash/uninstall-progress.json") is not None:
            raise DeploymentError("update", "partial removal must be resolved first")
        lease = LeaseStore(tree.path("/var/lib/sparehash/lifecycle-lease.json"))
        if lease.load() is not None:
            raise DeploymentError("update", "unrelated lifecycle hold retained")
        manifest = _manifest(tree)
        validate_live(tree, manifest)
        if os.path.lexists(tree.path(BACKUP)):
            bundle(tree, BACKUP, backup=True)
        for directory in (UNITS, str(Path(LAUNCHER).parent)):
            if any(name.startswith(".sparehash-") for name in os.listdir(tree.path(directory))):
                raise DeploymentError("update-receipts", "pre-existing application write temporaries require inspection")
        snapshot = preserved(tree)
        if any(snapshot[name] is None for name in PRESERVED[:3]):
            raise DeploymentError("update-config", "required configuration/credentials missing; no downtime applied")
        infra.validate_config(tree.path("/etc/sparehash/config.toml"))
        bundle(tree, stage, stage_digest, backup=False)
        record = UpdateRecord(stage.rsplit("-", 1)[-1], "prepared", active, boot, stage_digest, "", snapshot)
        save(tree, record)
    try:
        if active:
            try:
                token = acquire(ipc.call, record.operation_id)
                await_ready(ipc.call, token)
            except (RuntimeError, ipc.IPCError, OSError):
                # An unreachable controller is not proof of emptiness. Stop it
                # and establish the same durable inhibition offline instead.
                infra.stop()
        infra.stop()
        infra.proof()
        with locked_store(StoreContext(tree.trust)):
            _ensure_hold(tree, record)
            record = save(tree, record, preserved=preserved(tree))
            manifest = _manifest(tree)
            validate_live(tree, manifest)
            backup_digest = _backup(tree, manifest)
            record = save(tree, record, backup_digest=backup_digest)
        print("Broken-CLI recovery: " + FALLBACK, flush=True)
        return record
    except Exception:
        # Before publication the old source is untouched. Recovery must prove
        # that installation and operational state before releasing inhibition.
        recover(tree, infra)
        raise


def _old_manifest(tree: Tree) -> Manifest:
    data = tree.read(BACKUP + "/manifest.json")
    manifest = parse_manifest(data.decode()) if data is not None else None
    if manifest is None or manifest.installer is None:
        raise DeploymentError("update-backup", "backup manifest is invalid")
    return manifest


def _clean_write_temps(tree: Tree, directory: str) -> None:
    """Reap this transaction's interrupted protected atomic-write temporaries.

    Preflight refuses pre-existing temporaries in the shared publication parents;
    the durable journal excludes competing product mutations thereafter. Only
    Tree.write's exact random-name grammar and protected regular files qualify.
    Unknown names, links and foreign contents remain refusal conditions.
    """
    try:
        names = os.listdir(tree.path(directory))
    except FileNotFoundError:
        return
    temps = [name for name in names if re.fullmatch(r"\.sparehash-[0-9a-f]{32}", name)]
    if len(temps) > 64:
        raise DeploymentError("update-recovery", "excessive atomic-write temporaries; retained")
    for name in temps:
        path = directory + "/" + name
        receipt = tree.receipt(path, True)
        if receipt.kind != "file" or receipt.uid != tree.uid or receipt.mode not in (0o600, 0o644, 0o755):
            raise DeploymentError("update-recovery", "foreign atomic-write temporary retained")
        _unlink(tree, path)


def _publish(tree: Tree, source: str, wanted: dict[str, dict[str, object]],
             allowed: dict[str, set[str]]) -> None:
    # Before each replay, reject foreign leaves rather than delete a guessed
    # partially published tree. Every existing file must match old or candidate.
    for directory in (SOURCE + "/sparehash", SOURCE + "/systemd", UNITS, str(Path(LAUNCHER).parent)):
        _clean_write_temps(tree, directory)
    current = {"launcher", *("units/" + unit for unit in systemd.UNIT_NAMES)}
    for directory in (SOURCE, SOURCE + "/sparehash", SOURCE + "/systemd"):
        if not os.path.lexists(tree.path(directory)):
            tree.directory(directory, 0)
    for base, dirs, files in os.walk(tree.path(SOURCE), followlinks=False):
        for name in dirs:
            relative = str((Path(base) / name).relative_to(tree.path(SOURCE)))
            if relative not in {"sparehash", "systemd"} or (Path(base) / name).is_symlink():
                raise DeploymentError("update-publish", "foreign live source directory")
        for name in files:
            relative = "src/" + str((Path(base) / name).relative_to(tree.path(SOURCE)))
            if not _bundle_allowed(relative, False):
                raise DeploymentError("update-publish", "foreign live source leaf")
            current.add(relative)
    for relative in current:
        data = tree.read(_live_name(relative))
        if data is not None and (relative not in allowed or digest(data) not in allowed[relative]):
            raise DeploymentError("update-publish", "foreign live content; hold retained: " + relative)
    for relative, meta in wanted.items():
        if relative == "manifest.json":
            continue
        data = tree.read(source + "/" + relative)
        if data is None or digest(data) != meta["sha256"]:
            raise DeploymentError("update-publish", "publication material disappeared or changed")
        tree.write(_live_name(relative), (data, int(str(meta["mode"])), int(str(meta["gid"]))))
    for relative in current - wanted.keys():
        _unlink(tree, _live_name(relative), missing_ok=True)
    for relative, meta in wanted.items():
        if relative == "manifest.json":
            continue
        installed = tree.receipt(_live_name(relative), True)
        if (installed.sha256, installed.mode, installed.gid) != (meta["sha256"], meta["mode"], meta["gid"]):
            raise DeploymentError("update-publish", "published bytes/mode/ownership differ from verified material")


def _refresh(tree: Tree, original: Manifest, wanted: dict[str, dict[str, object]]) -> None:
    assert original.installer is not None
    installer = original.installer
    prior = {a.id: a for a in installer.artifacts}
    artifacts = [a for a in installer.artifacts if not _is_app(a)]
    names = [SOURCE, SOURCE + "/sparehash", SOURCE + "/systemd"]
    names += [_live_name(name) for name in wanted if name != "manifest.json"]
    for name in names:
        identity_id = leaf_identity(name) or name
        old = prior.get(identity_id)
        artifacts.append(tree.receipt(name, old.created if old is not None else True, identity=identity_id))
    # Replace only app action references; no ever-growing update history and no
    # dangling action target when a module is removed. Non-app records survive.
    app_ids = {a.id for a in installer.artifacts if _is_app(a)}
    actions = [a for a in installer.actions if a.target not in app_ids]
    seed = installer.actions[0] if installer.actions else None
    if seed is None:
        raise DeploymentError("update-receipts", "installation has no action identity")
    for artifact in artifacts:
        if _is_app(artifact):
            actions.append(Action(0, seed.install_id, seed.at,
                                  "create-directory" if artifact.kind == "directory" else "write-file", artifact.id, "applied"))
    actions = [replace(action, sequence=i) for i, action in enumerate(actions, 1)]
    version, _ = identity(tree.path(SOURCE))
    listing = [(relative, digest(path.read_bytes())) for relative, path in _collect_source(tree.path(SOURCE), MAX_MODULES).items()]
    updated = replace(original, payload_version=version, inventory_digest=inventory_digest(listing),
                      installer=replace(installer, artifacts=tuple(artifacts), actions=tuple(actions)))
    with locked_store(StoreContext(tree.trust)) as store:
        store.write(updated)


def _allowed(*inventories: dict[str, dict[str, object]]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for inventory in inventories:
        for name, meta in inventory.items():
            if name != "manifest.json":
                result.setdefault(name, set()).add(str(meta["sha256"]))
    return result


def _verify(tree: Tree, record: UpdateRecord, infra: Infrastructure) -> None:
    validate_live(tree, _manifest(tree))
    infra.verify_identity(tree.path(SOURCE))
    infra.verify_units(tree.path(UNITS))
    infra.validate_config(tree.path("/etc/sparehash/config.toml"))
    _preservation(tree, record, infra)
    if record.active:
        infra.ready()
    elif infra.active():
        raise DeploymentError("update-activity", "previously stopped controller unexpectedly active")
    infra.proof()


def finalise(tree: Tree, record: UpdateRecord, infra: Infrastructure) -> None:
    """No restoration is legal once the committed record is durable."""
    if record.phase != "committed":
        raise DeploymentError("update", "cannot finalise an incomplete transaction")
    _ensure_hold(tree, record)
    infra.stop()
    infra.proof()
    _verify(tree, replace(record, active=False), infra)
    # Clear the durable lease only while stopped. A new controller starts under
    # journal inhibition alone, so it has no stale in-memory external lease.
    # Journal deletion is the LAST durable step; an interruption before it is
    # repeatably finalised, never interpreted as permission to restore history.
    LeaseStore(tree.path("/var/lib/sparehash/lifecycle-lease.json")).clear()
    if record.active:
        infra.ready()
    _preservation(tree, record, infra)
    if os.path.lexists(tree.path(record.stage)):
        discard_bundle(tree, record.stage, backup=False, expected=record.stage_digest)
    with locked_store(StoreContext(tree.trust)):
        _unlink(tree, JOURNAL)


def publish(tree: Tree, infra: Infrastructure) -> bool:
    """Execute only from the known-good backup. False means restored failure."""
    record = load(tree)
    if record is None or record.phase != "prepared" or not record.backup_digest:
        raise DeploymentError("update", "no prepared complete backup")
    old = bundle(tree, BACKUP, record.backup_digest, backup=True)
    new = bundle(tree, record.stage, record.stage_digest, backup=False)
    original = _old_manifest(tree)
    try:
        infra.stop()
        infra.proof()
        _ensure_hold(tree, record)
        _preservation(tree, record, infra)
        record = save(tree, record, phase="installing")
        _publish(tree, record.stage, new, _allowed(old, new))
        _refresh(tree, original, new)
        infra.reload()
        _verify(tree, record, infra)
        record = save(tree, record, phase="committed")
    except Exception:
        recover(tree, infra)
        return False
    finalise(tree, record, infra)
    return True


def recover(tree: Tree, infra: Infrastructure) -> bool:
    """Restore one interrupted transaction, or finalise its durable commitment."""
    with locked_store(StoreContext(tree.trust)):
        record = load(tree)
        if record is None:
            return False
        _ensure_hold(tree, record)
    if record.phase == "committed":
        finalise(tree, record, infra)
        return True
    infra.stop()
    infra.proof()
    if record.phase == "prepared":
        # Never mistake a partial backup for a ready recovery source. No live
        # writes occurred; validate the still-intact installation directly.
        validate_live(tree, _manifest(tree))
        record = save(tree, record, preserved=preserved(tree))
        if not record.backup_digest:
            _clean_unfinished_backup(tree)
    else:
        old = bundle(tree, BACKUP, record.backup_digest, backup=True)
        new = bundle(tree, record.stage, record.stage_digest, backup=False)
        original = _old_manifest(tree)
        record = save(tree, record, phase="restoring")
        _publish(tree, BACKUP, old, _allowed(old, new))
        _refresh(tree, original, old)
        infra.reload()
    _verify(tree, record, infra)
    record = save(tree, record, phase="committed")
    finalise(tree, record, infra)
    return True


def worker() -> int:
    """Fixed internal dispatch, root checked by __main__; no caller paths."""
    try:
        tree = Tree()
        if Path(__file__).resolve() != tree.path(BACKUP + "/src/sparehash/upgrade.py").resolve():
            raise DeploymentError("update-worker", "publication must run from the known-good backup source")
        return 0 if publish(tree, Infrastructure()) else 1
    except KeyboardInterrupt:
        print("Update interrupted; mining inhibited. Recovery: " + FALLBACK, flush=True)
        return 1
    except (RuntimeError, OSError, systemd.UnitError, subprocess.SubprocessError) as error:
        print(f"Update retained for recovery: {error}\n{FALLBACK}", flush=True)
        return 1


def run_from_backup(tree: Tree) -> int:
    record = load(tree)
    if record is None:
        raise DeploymentError("update", "journal disappeared")
    bundle(tree, BACKUP, record.backup_digest, backup=True)
    result = subprocess.run(("/usr/bin/python3", "-I", "-B",
                             str(tree.path(BACKUP + "/src/sparehash/__main__.py")), "__app_update__"),
                            env=ENV, check=False)
    return result.returncode
