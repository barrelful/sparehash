# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Manifest-owned /opt/xmrig install, gated update flow and rollback."""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Protocol

from .controller_io import Generation, LifecycleBusy
from .install_manifest import StoreContext, StoreError, locked_store
from .xmrig_manifest import ManifestError, format_manifest
from .xmrig_archive import ArchiveError, extract_archive, remove_tree
from .xmrig_layout import (
    BINARY_NAME,
    CollisionError,
    InstallError,
    Publication,
    current_target,
    dir_exists,
    ensure_marker,
    open_managed_root,
    prune as prune_versions,
    publish,
    require_version_dir,
    verify_binary,
)
from .xmrig_paths import MANIFEST_ROOT, Manifest, PathTrust, VersionRecord
from .xmrig_verify import verify_release

__all__ = [
    "CollisionError",
    "InstallContext",
    "InstallError",
    "InstallRequest",
    "Installed",
    "ReleaseArtifacts",
    "UpdateGate",
    "current_binary",
    "install_release",
    "rollback",
    "update_flow",
]


class UpdateGate(Protocol):
    """Todo13 owner-thread lifecycle lease; the installer never drives it itself."""

    def acquire_update(self) -> Generation:
        """Acquire exclusive launch inhibition and request a STOP; raises LifecycleBusy."""
        ...

    def update_ready(self, token: Generation) -> bool:
        """True only after the matching stop completed with whole-cgroup-empty proof."""
        ...

    def release_update(self, token: Generation) -> bool:
        """Release only with still-valid empty proof, then re-authorize and recollect."""
        ...


@dataclass(frozen=True, slots=True)
class ReleaseArtifacts:
    tarball: Path
    sums: Path
    sig: Path
    keydata: bytes


@dataclass(frozen=True, slots=True)
class InstallRequest:
    artifacts: ReleaseArtifacts
    version: tuple[int, int, int]
    source: str


@dataclass(frozen=True, slots=True)
class Installed:
    version: str
    binary: Path
    manifest: Manifest
    pruned: tuple[str, ...]


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class InstallContext:
    trust: PathTrust = PathTrust()
    gate: UpdateGate | None = None
    retain: int = 2
    now: Callable[[], str] = _utcnow


_DEFAULT_TRUST: Final = PathTrust()
_DEFAULT_CONTEXT: Final = InstallContext()


def _run_gated(gate: UpdateGate, mutate: Callable[[], Installed]) -> Installed:
    try:
        token = gate.acquire_update()
    except LifecycleBusy as exc:
        raise InstallError("busy", "another update, reload or benchmark holds the lifecycle lease") from exc
    try:
        if not gate.update_ready(token):
            raise InstallError("cgroup", "miner cgroup is not confirmed empty; no files changed")
        result = mutate()
    finally:
        released = gate.release_update(token)
    if not released:
        raise InstallError("lease", "update lease could not be released with a valid empty proof")
    return result


def _new_manifest(request: InstallRequest, manifest: Manifest, version_name: str, now: str) -> Manifest:
    if version_name in manifest.names:
        return manifest
    return Manifest(MANIFEST_ROOT, (*manifest.versions, VersionRecord(version_name, now, request.source)), manifest.installer)


def _apply(request: InstallRequest, context: InstallContext, version_name: str, *, prune: bool) -> Installed:
    trust = context.trust
    with ExitStack() as stack:
        store = stack.enter_context(locked_store(StoreContext(trust, create=True)))
        prior = store.read()
        root_fd, root_parent = open_managed_root(trust, stack, create=False)
        created_root = root_fd is None
        manifest = trust.manifest()
        if root_fd is None:
            if prior is not None and (prior.manifest.versions or prior.manifest.installer is None):
                raise CollisionError("root", "install manifest exists without a managed root")
            manifest = prior.manifest if prior is not None else Manifest(MANIFEST_ROOT, ())
            prior_link = None
        else:
            if manifest is None:
                raise CollisionError("foreign", f"{trust.managed_root} exists but is not SpareHash-managed")
            prior_link = current_target(root_fd)
            if prior_link is not None and trust.authorized_current(trust.marker_path, trust.manifest_path) is None:
                raise CollisionError("current", "existing current symlink is not manifest-authorized")
        published = False
        extracted = False
        new_manifest = _new_manifest(request, manifest, version_name, context.now())
        _ = format_manifest(new_manifest)
        if root_fd is None:
            root_fd, root_parent = open_managed_root(trust, stack, create=True)
            if root_fd is None:
                raise InstallError("root", "managed root could not be created")
        try:
            if version_name not in manifest.names:
                if dir_exists(root_fd, version_name):
                    raise CollisionError("collision", f"unrecorded version directory {version_name} already exists")
                try:
                    extract_archive(request.artifacts.tarball, root_fd, version_name, trust.uid)
                except ArchiveError as exc:
                    raise InstallError("archive", exc.detail) from exc
                extracted = True
            verify_binary(root_fd, version_name, trust.uid)
            os.fchmod(root_fd, 0o755)
            version_fd = os.open(version_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            _ = stack.callback(os.close, version_fd)
            os.fchmod(version_fd, 0o755)
            ensure_marker(root_fd, trust.uid)
            publish(root_fd, store, Publication(new_manifest, version_name))
            published = True
            pruned = (prune_versions(root_fd, manifest, new_manifest, (version_name, prior_link),
                                     context.retain, trust.uid) if prune else ())
            return Installed(version_name, trust.managed_root / version_name / BINARY_NAME, new_manifest, pruned)
        except BaseException as exc:
            if not published:
                if created_root:
                    try:
                        remove_tree(root_parent, trust.managed_root.name)
                    except OSError as cleanup:
                        raise InstallError("rollback", f"first install failed and cleanup failed: {cleanup}") from exc
                elif extracted:
                    _discard_version(root_fd, version_name)
            raise


def _discard_version(root_fd: int, version_name: str) -> None:
    try:
        remove_tree(root_fd, version_name)
    except FileNotFoundError:
        return


def _install(request: InstallRequest, context: InstallContext, *, prune: bool) -> Installed:
    if prune and context.gate is None:
        raise InstallError("gate", "update flow requires an injected lifecycle gate; no files changed")
    verify_release(request.artifacts.tarball, request.artifacts.sums, request.artifacts.sig, request.artifacts.keydata)
    version_name = f"xmrig-{request.version[0]}.{request.version[1]}.{request.version[2]}"

    def mutate() -> Installed:
        try:
            return _apply(request, context, version_name, prune=prune)
        except StoreError as error:
            raise CollisionError(error.stage, error.detail) from error
        except ManifestError as error:
            raise InstallError("manifest", str(error)) from error

    if context.gate is None:
        return mutate()
    return _run_gated(context.gate, mutate)


def install_release(request: InstallRequest, context: InstallContext = _DEFAULT_CONTEXT) -> Installed:
    """Install a signed release into an absent managed root; never prunes."""
    return _install(request, context, prune=False)


def update_flow(request: InstallRequest, context: InstallContext = _DEFAULT_CONTEXT) -> Installed:
    """Install a signed release under an injected lifecycle lease, then prune owned old versions."""
    return _install(request, context, prune=True)


def current_binary(trust: PathTrust = _DEFAULT_TRUST) -> Path | None:
    """Return the manifest-authorized current binary path, or None when unmanaged."""
    return trust.authorized_current(trust.marker_path, trust.manifest_path)


def rollback(trust: PathTrust = _DEFAULT_TRUST, *, gate: UpdateGate | None = None) -> Installed:
    """Under an injected lease, point current at the previous version and remove the rejected one."""
    if gate is None:
        raise InstallError("gate", "rollback requires an injected lifecycle gate; no files changed")

    def mutate() -> Installed:
        try:
            return _rollback(trust)
        except StoreError as error:
            raise CollisionError(error.stage, error.detail) from error
        except ManifestError as error:
            raise InstallError("manifest", str(error)) from error

    return _run_gated(gate, mutate)


def _rollback(trust: PathTrust) -> Installed:
    with ExitStack() as stack:
        store = stack.enter_context(locked_store(StoreContext(trust)))
        _ = store.read()
        root_fd, _ = open_managed_root(trust, stack, create=False)
        if root_fd is None:
            raise CollisionError("root", "no managed installation to roll back")
        manifest = trust.manifest()
        if manifest is None:
            raise CollisionError("foreign", "managed root is not SpareHash-managed")
        current = trust.authorized_current(trust.marker_path, trust.manifest_path)
        if current is None:
            raise CollisionError("current", "current symlink is not manifest-authorized")
        current_name = current.parent.name
        names = list(manifest.names)
        index = names.index(current_name)
        if index == 0:
            raise InstallError("rollback", "no previous recorded version to roll back to")
        previous_name = names[index - 1]
        new_manifest = Manifest(MANIFEST_ROOT, tuple(r for r in manifest.versions if r.version != current_name), manifest.installer)
        publish(root_fd, store, Publication(new_manifest, previous_name))
        if require_version_dir(root_fd, current_name, trust.uid):
            remove_tree(root_fd, current_name)
        return Installed(previous_name, trust.managed_root / previous_name / BINARY_NAME, new_manifest,
                         (current_name,))
