# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Inhibited local-release upgrade publication with deterministic recovery.

Publication is a journaled sequence of individually fsynced object writes, never
one atomic multi-object rename. The compatible lifecycle hold is acquired before
any installed mutation, and every failure retains that hold plus the private
staging tree so the same local release can be rolled forward. Recovery refuses a
missing or digest-mismatched staging. The staged tree and the old-tree backup are
removed only at explicit COMPLETE.

The transaction is self-contained on the current receipt infrastructure: it
stages the new source payload beside ``/opt/sparehash/src``, refuses foreign
leaves, unexpected symlinks and sub-tree mutation, publishes through one passed
``StoreContext`` (never a nested ``locked_store``), and refreshes old/new inode
receipts while preserving original ``created``/owned flags. It never invokes the
old installer and never claims the several published objects commit atomically.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import stat
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Final, Literal, TypeAlias

from .deployment import DeploymentError, Tree
from .install_manifest import StoreContext, locked_store
from .lifecycle_lease import ActiveLease, LeaseStore
from .xmrig_ledger import PACKAGE_LEAF, UNIT_TEMPLATES, Artifact, leaf_identity
from .xmrig_manifest import Manifest

Phase: TypeAlias = Literal["prepared", "staged", "published", "complete"]

SOURCE_LOGICAL: Final = "/opt/sparehash/src"
SOURCE_PARENT_LOGICAL: Final = "/opt/sparehash"
CLI_LOGICAL: Final = "/usr/local/bin/sparehash"
JOURNAL_RELATIVE: Final = "var/lib/sparehash/migration.json"
LEASE_RELATIVE: Final = "var/lib/sparehash/lifecycle-lease.json"
RUNTIME_PATHS: Final[tuple[str, ...]] = (
    SOURCE_LOGICAL,
    CLI_LOGICAL,
    "/etc/systemd/system/sparehash.service",
    "/etc/systemd/system/sparehash-xmrig.service",
    "/etc/sparehash/config.toml",
)
_OPERATION: Final = re.compile(r"[A-Za-z0-9._-]{1,128}")
_JOURNAL_MAX: Final = 64 * 1024
_JOURNAL_FIELDS: Final = frozenset(
    ("operation_id", "phase", "target", "staged_source", "inventory_digest", "old_receipts", "new_receipts")
)
_PHASES: Final[tuple[Phase, ...]] = ("prepared", "staged", "published", "complete")


class UpgradeError(DeploymentError):
    """A staged upgrade step failed; the hold and staging are retained for recovery."""


@dataclass(frozen=True, slots=True)
class UpgradeContext:
    operation_id: str
    source_root: Path
    target_root: Path
    staging_root: Path
    preserve_startup_choices: bool


@dataclass(frozen=True, slots=True)
class StagedSource:
    path: Path
    digest: str
    leaves: tuple[tuple[str, str], ...]

    def inventory(self) -> dict[str, str]:
        return dict(self.leaves)


@dataclass(frozen=True, slots=True)
class DeploymentSpec:
    runtime_paths: tuple[str, ...]
    inventory: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class MigrationJournal:
    operation_id: str
    phase: str
    target: str
    staged_source: str
    inventory_digest: str
    old_receipts: tuple[dict[str, object], ...]
    new_receipts: tuple[dict[str, object], ...]


def validate_operation_id(value: str) -> str:
    if _OPERATION.fullmatch(value) is None:
        raise UpgradeError("operation", "operation id must be 1..128 of [A-Za-z0-9._-]")
    return value


def lease_for(root: Path) -> LeaseStore:
    """The old-readable update sidecar lives beside the record on the same root."""
    return LeaseStore(root / LEASE_RELATIVE)


def journal_path(root: Path) -> Path:
    return root / JOURNAL_RELATIVE


def acquire_compatible_hold(operation_id: str, *, lease: LeaseStore | None = None) -> ActiveLease:
    """Acquire the pinned three-field update hold before any installed mutation.

    An existing record is only replayed when it is the same operation; any other
    or corrupt hold refuses rather than being adopted or cleared.
    """
    validate_operation_id(operation_id)
    store = lease if lease is not None else LeaseStore()
    existing = store.load()
    if existing is not None:
        if existing.purpose == "update" and existing.request_id == operation_id:
            return existing
        raise UpgradeError("hold", f"unrelated {existing.purpose} hold retained; recovery uses its own request id")
    record = ActiveLease(token=secrets.token_hex(16), request_id=operation_id, purpose="update")
    store.save(record)
    return record


def begin(ctx: UpgradeContext) -> MigrationJournal:
    """Persist the PREPARED journal before staging has any side effect."""
    validate_operation_id(ctx.operation_id)
    journal = MigrationJournal(ctx.operation_id, "prepared", SOURCE_LOGICAL, str(ctx.staging_root), "", (), ())
    write_journal(ctx.target_root, journal)
    return journal


def deployment_spec(ctx: UpgradeContext, layout: Tree, staged: StagedSource) -> DeploymentSpec:
    """Declare the managed runtime paths and the staged payload the upgrade installs."""
    _ = ctx, layout
    return DeploymentSpec(RUNTIME_PATHS, staged.inventory())


def stage_source(ctx: UpgradeContext, layout: Tree) -> StagedSource:
    """Copy the selected local release into a private 0700 staging next to the target.

    Foreign leaves, unexpected symlinks, extra directories and any sub-tree
    mutation observed during the copy are refused; a refused staging is removed.
    """
    validate_operation_id(ctx.operation_id)
    source = ctx.source_root
    if os.path.islink(source) or not source.is_dir():
        raise UpgradeError("source", f"selected source is not a protected directory: {source}")
    source_digest, leaves = _scan(source)
    staging = ctx.staging_root
    _validate_staging_location(staging, layout)
    if staging.exists():
        if any(staging.iterdir()):
            raise UpgradeError("staging", "private staging is not empty; resolve or recover the prior attempt first")
        os.chmod(staging, 0o700)
    else:
        staging.mkdir(mode=0o700, parents=True)
        os.chmod(staging, 0o700)
    for relative, _ in leaves:
        _write_leaf(staging / relative, (source / relative).read_bytes(), 0o600)
    staged_digest, staged_leaves = _scan(staging)
    if staged_digest != source_digest or staged_leaves != leaves:
        _discard(staging)
        raise UpgradeError("staging", "staged payload does not match the source inventory; refused")
    rescan, _ = _scan(source)
    if rescan != source_digest:
        _discard(staging)
        raise UpgradeError("staging", "source subtree mutated during staging; refused")
    journal = read_journal(ctx.target_root, ctx.operation_id)
    journal = replace(journal, phase="staged", staged_source=str(staging), inventory_digest=source_digest)
    write_journal(ctx.target_root, journal)
    return StagedSource(staging, source_digest, leaves)


def verify_pre_publication(ctx: UpgradeContext, layout: Tree, deployment_spec: DeploymentSpec) -> str:
    """Confirm the staged payload matches the contract and every runtime path resolves.

    Each declared runtime path must exist beneath the layout's physical root as a
    real (non-link) object; a missing or redirected path refuses before mutation.
    """
    staging = ctx.staging_root
    if not staging.is_dir() or os.path.islink(staging):
        raise UpgradeError("verify", "private staging is missing")
    digest, leaves = _scan(staging)
    inventory = dict(leaves)
    if set(inventory) != set(deployment_spec.inventory):
        raise UpgradeError("verify", "deployment inventory does not match the staged payload")
    for relative, expected in deployment_spec.inventory.items():
        if inventory.get(relative) != expected:
            raise UpgradeError("verify", f"staged digest mismatch: {relative}")
    for logical in deployment_spec.runtime_paths:
        physical = layout.path(logical)
        if os.path.islink(physical):
            raise UpgradeError("verify", f"runtime path is an unexpected symlink: {logical}")
        if not physical.exists():
            raise UpgradeError("verify", f"runtime path does not resolve beneath the layout root: {logical}")
        if not _within(physical, layout.root):
            raise UpgradeError("verify", f"runtime path escapes the layout root: {logical}")
    return digest


def publish(ctx: UpgradeContext, journal: MigrationJournal, store: StoreContext) -> MigrationJournal:
    """Publish the staged source through one passed StoreContext, never nesting the lock.

    The old source tree is renamed to a bounded backup before the new tree is
    written; any filesystem failure rolls the backup back and leaves the private
    staging untouched for verified roll-forward recovery. Receipts are refreshed
    in the same single transaction with original ``created`` flags preserved.
    """
    staging = Path(journal.staged_source)
    if not staging.is_dir() or os.path.islink(staging):
        raise UpgradeError("publish", "staged source is missing")
    digest, _ = _scan(staging)
    if digest != journal.inventory_digest:
        raise UpgradeError("publish", "staged source digest no longer matches the journal")
    layout = Tree(ctx.target_root, os.getuid())
    target = ctx.target_root / journal.target.removeprefix("/")
    backup = staging.parent / (staging.name + ".old")
    if backup.exists():
        raise UpgradeError("publish", "a prior old-tree backup is present; recover or complete it first")
    with locked_store(store) as handle:
        prior = handle.read()
        if prior is None or prior.manifest.installer is None:
            raise UpgradeError("publish", "no protected installation manifest; refusing to migrate an unowned tree")
        old_receipts = _snapshot(layout, journal.target)
        try:
            if target.exists():
                _rename(target, backup)
            _materialize(staging, target)
        except OSError as error:
            _rollback(target, backup)
            raise UpgradeError("publish", f"publication failed ({error.strerror or error}); staging preserved") from error
        new_receipts = _snapshot(layout, journal.target)
        updated = _merge_receipts(prior.manifest, journal.target, old_receipts, new_receipts)
        handle.write(updated)
        published = replace(
            journal,
            phase="published",
            old_receipts=tuple(dict(asdict(receipt)) for receipt in old_receipts),
            new_receipts=tuple(dict(asdict(receipt)) for receipt in new_receipts),
        )
        write_journal(ctx.target_root, published)
        return published


def recover_from_staging(operation_id: str, root: Path) -> MigrationJournal:
    """Roll the recorded staging forward to the target, or refuse an unusable staging.

    A missing staging directory or a recorded inventory digest that no longer
    matches the staged tree is a refusal; the hold and journal are retained.
    """
    journal = read_journal(root, operation_id)
    if journal.phase == "complete":
        raise UpgradeError("recovery", "operation is already complete")
    staging = Path(journal.staged_source)
    if not staging.is_dir() or os.path.islink(staging):
        raise UpgradeError("recovery", "recorded staging is gone; cannot recover")
    digest, _ = _scan(staging)
    if digest != journal.inventory_digest:
        raise UpgradeError("recovery", "recorded staging digest mismatches; refusing recovery")
    target = root / journal.target.removeprefix("/")
    if target.exists():
        current, _ = _scan(target)
        if current == journal.inventory_digest:
            published = replace(journal, phase="published")
            write_journal(root, published)
            return published
    backup = staging.parent / (staging.name + ".old")
    try:
        if target.exists():
            if backup.exists():
                shutil.rmtree(target, ignore_errors=True)
            else:
                _rename(target, backup)
        _materialize(staging, target)
    except OSError as error:
        raise UpgradeError("recovery", f"roll-forward failed ({error.strerror or error}); staging preserved") from error
    published = replace(journal, phase="published")
    write_journal(root, published)
    return published


def complete(root: Path, operation_id: str, *, lease: LeaseStore | None = None,
             empty_proof: Callable[[], bool] | None = None) -> MigrationJournal:
    """Durably commit a published upgrade: remove only the journal and matching hold.

    The published payload must still match the recorded digest and a fresh empty
    proof must hold; a missing journal, mismatched hold or unreadable evidence is
    a refusal, never a force-clear. Staging and backup are removed here only.
    """
    journal = read_journal(root, operation_id)
    if journal.phase != "published":
        raise UpgradeError("complete", "publication is not complete; run --recover or finish --upgrade")
    store = lease if lease is not None else LeaseStore()
    hold = store.load()
    if hold is None or hold.purpose != "update" or hold.request_id != operation_id:
        raise UpgradeError("complete", "matching update hold is absent; refusing completion")
    target = root / journal.target.removeprefix("/")
    if not target.is_dir():
        raise UpgradeError("complete", "published source tree is missing")
    digest, _ = _scan(target)
    if digest != journal.inventory_digest:
        raise UpgradeError("complete", "published source digest mismatches; refusing completion")
    if empty_proof is None or not empty_proof():
        raise UpgradeError("complete", "fresh empty proof is unavailable; miner may still be running")
    remove_journal(root)
    store.clear()
    staging = Path(journal.staged_source)
    backup = staging.parent / (staging.name + ".old")
    for candidate in (staging, backup):
        if candidate.exists():
            shutil.rmtree(candidate, ignore_errors=True)
    _fsync_dir(staging.parent)
    return replace(journal, phase="complete")


def read_journal(root: Path, operation_id: str) -> MigrationJournal:
    validate_operation_id(operation_id)
    data = _read_bounded(journal_path(root))
    if data is None:
        raise UpgradeError("journal", "migration journal is absent")
    try:
        document = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        raise UpgradeError("journal", f"migration journal is corrupt: {error}") from error
    if not isinstance(document, dict) or set(document) != _JOURNAL_FIELDS:
        raise UpgradeError("journal", "migration journal fields are invalid")
    phase = document.get("phase")
    if not isinstance(phase, str) or phase not in _PHASES:
        raise UpgradeError("journal", "migration journal phase is invalid")
    operation = document.get("operation_id")
    if not isinstance(operation, str) or operation != operation_id:
        raise UpgradeError("journal", "migration journal operation id mismatches")
    target, staged, digest = (document.get("target"), document.get("staged_source"), document.get("inventory_digest"))
    if not isinstance(target, str) or not isinstance(staged, str) or not isinstance(digest, str):
        raise UpgradeError("journal", "migration journal paths are invalid")
    return MigrationJournal(
        operation_id,
        phase,
        target,
        staged,
        digest,
        _receipts(document.get("old_receipts")),
        _receipts(document.get("new_receipts")),
    )


def write_journal(root: Path, journal: MigrationJournal) -> None:
    payload = json.dumps(
        {
            "operation_id": journal.operation_id,
            "phase": journal.phase,
            "target": journal.target,
            "staged_source": journal.staged_source,
            "inventory_digest": journal.inventory_digest,
            "old_receipts": list(journal.old_receipts),
            "new_receipts": list(journal.new_receipts),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > _JOURNAL_MAX:
        raise UpgradeError("journal", "migration journal would exceed its bounded capacity")
    path = journal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / (path.name + ".tmp-" + secrets.token_hex(8))
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as target:
            _ = target.write(payload)
            target.flush()
            _fsync(target.fileno())
        os.rename(temp, path)
        _fsync_dir(path.parent)
    except OSError as error:
        if temp.exists():
            temp.unlink(missing_ok=True)
        raise UpgradeError("journal", f"migration journal cannot be persisted: {error}") from error


def remove_journal(root: Path) -> None:
    path = journal_path(root)
    try:
        path.unlink(missing_ok=True)
        _fsync_dir(path.parent)
    except OSError as error:
        raise UpgradeError("journal", f"migration journal cannot be removed: {error}") from error


def _receipts(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list):
        raise UpgradeError("journal", "migration journal receipts are invalid")
    records: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            raise UpgradeError("journal", "migration journal receipts are invalid")
        records.append({str(key): item[key] for key in item})
    return tuple(records)


def _validate_staging_location(staging: Path, layout: Tree) -> None:
    parent = layout.path(SOURCE_PARENT_LOGICAL)
    if not _within(staging, parent):
        raise UpgradeError("staging", "private staging must be adjacent to the installed source tree")


def _within(candidate: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(candidate), os.path.abspath(parent)]) == os.path.abspath(parent)
    except ValueError:
        return False


def _relative_leaf(relative: str) -> bool:
    parts = relative.split("/")
    if len(parts) != 2:
        return False
    if parts[0] == "sparehash":
        return PACKAGE_LEAF.fullmatch(parts[1]) is not None
    if parts[0] == "systemd":
        return parts[1] in UNIT_TEMPLATES
    return False


def _scan(root: Path) -> tuple[str, tuple[tuple[str, str], ...]]:
    if os.path.islink(root) or not root.is_dir():
        raise UpgradeError("inventory", f"not a protected directory: {root}")
    leaves: list[tuple[str, str]] = []
    for base, dirs, files in os.walk(root):
        dirs.sort()
        for name in dirs:
            full = Path(base) / name
            if os.path.islink(full):
                raise UpgradeError("inventory", f"unexpected symlink: {full}")
            relative = str(full.relative_to(root))
            if relative not in {"sparehash", "systemd"}:
                raise UpgradeError("inventory", f"unexpected directory: {relative}")
        for name in sorted(files):
            full = Path(base) / name
            if os.path.islink(full):
                raise UpgradeError("inventory", f"unexpected symlink: {full}")
            relative = str(full.relative_to(root))
            if not _relative_leaf(relative):
                raise UpgradeError("inventory", f"foreign leaf: {relative}")
            leaves.append((relative, _sha256_file(full)))
    leaves.sort()
    digest = hashlib.sha256("\n".join(f"{relative} {digest}" for relative, digest in leaves).encode()).hexdigest()
    return digest, tuple(leaves)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _write_leaf(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as target:
        _ = target.write(data)
        target.flush()
        _fsync(target.fileno())
        os.fchmod(target.fileno(), mode)


def _materialize(staging: Path, target: Path) -> None:
    """Write the staged payload into a fresh target directory, one fsynced leaf at a time."""
    digest, leaves = _scan(staging)
    _ = digest
    os.mkdir(target, 0o755)
    os.chmod(target, 0o755)
    for relative, _ in leaves:
        _write_leaf(target / relative, (staging / relative).read_bytes(), 0o644)
    for directory in sorted({(target / relative).parent for relative, _ in leaves}):
        if directory != target:
            os.chmod(directory, 0o755)
    _fsync_dir(target)
    _fsync_dir(target.parent)


def _rollback(target: Path, backup: Path) -> None:
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    if backup.exists():
        _rename(backup, target)
    _fsync_dir(target.parent)


def _rename(source: Path, destination: Path) -> None:
    os.rename(source, destination)


def _fsync(descriptor: int) -> None:
    os.fsync(descriptor)


def _fsync_dir(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _fsync(descriptor)
    finally:
        os.close(descriptor)


def _discard(staging: Path) -> None:
    shutil.rmtree(staging, ignore_errors=True)


def _snapshot(layout: Tree, logical_target: str) -> tuple[Artifact, ...]:
    physical = layout.path(logical_target)
    if not physical.is_dir():
        return ()
    _, leaves = _scan(physical)
    receipts = [layout.receipt(logical_target, False)]
    for relative, _ in leaves:
        logical = logical_target + "/" + relative
        identity = leaf_identity(logical)
        if identity is None:
            continue
        receipts.append(layout.receipt(logical, False, identity=identity))
    return tuple(receipts)


def _merge_receipts(manifest: Manifest, logical_target: str,
                    old_receipts: tuple[Artifact, ...], new_receipts: tuple[Artifact, ...]) -> Manifest:
    installer = manifest.installer
    if installer is None:
        raise UpgradeError("publish", "installation manifest has no installer ledger")
    previous = {receipt.id: receipt for receipt in (*old_receipts, *installer.artifacts)}
    kept = {
        artifact.id: artifact
        for artifact in installer.artifacts
        if not (artifact.path == logical_target or artifact.path.startswith(logical_target + "/"))
    }
    for receipt in new_receipts:
        prior = previous.get(receipt.id)
        kept[receipt.id] = replace(receipt, created=receipt.created or (prior.created if prior is not None else False))
    artifacts = tuple(sorted(kept.values(), key=lambda artifact: artifact.id))
    return replace(manifest, installer=replace(installer, artifacts=artifacts))


def _read_bounded(path: Path) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise UpgradeError("journal", f"unsafe migration journal: {error}") from error
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > _JOURNAL_MAX:
            raise UpgradeError("journal", "migration journal is not a bounded protected regular file")
        return source.read(_JOURNAL_MAX + 1)
