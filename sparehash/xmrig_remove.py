"""B6 version-tree and installer-receipt removal; caller must hold the lifecycle lease."""

from __future__ import annotations

import os
import stat
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import Final

from .deployment import DeploymentError, Tree
from .install_manifest import StoreContext, locked_store
from .xmrig_archive import remove_tree
from .xmrig_layout import CollisionError, current_target, open_managed_root, verify_binary
from .xmrig_ledger import Artifact, Installer, PACKAGE_DIR, TEMPLATE_DIR, fixed_target, leaf_identity
from .xmrig_paths import DIRECTORY_FLAGS, MARKER_NAME, PathTrust

LEAF_DIRS: Final = (PACKAGE_DIR, TEMPLATE_DIR)
PRUNE_DIRS: Final = (PACKAGE_DIR, TEMPLATE_DIR, "/opt/sparehash/src", "/opt/sparehash")
CONTAINER_CHILDREN: Final = {"/opt/sparehash": frozenset(("src",)),
                             "/opt/sparehash/src": frozenset(("sparehash", "systemd"))}


def _validate_tree(fd: int, uid: int) -> None:
    """Reject unsafe descendants before any deletion, not midway through removal."""
    with os.scandir(fd) as entries:
        for entry in entries:
            info = entry.stat(follow_symlinks=False)
            if info.st_uid != uid or info.st_mode & 0o022:
                raise CollisionError("ownership", "version tree contains foreign or writable content")
            if stat.S_ISDIR(info.st_mode):
                with ExitStack() as stack:
                    child = os.open(entry.name, DIRECTORY_FLAGS, dir_fd=fd)
                    _ = stack.callback(os.close, child)
                    _validate_tree(child, uid)
            elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise CollisionError("ownership", "version tree contains linked or special content")


def remove_owned_versions(trust: PathTrust) -> tuple[str, ...]:
    """Use the canonical store lock and pinned root; retain all installer receipts."""
    with ExitStack() as stack:
        store = stack.enter_context(locked_store(StoreContext(trust)))
        prior = store.read()
        if prior is None:
            raise CollisionError("manifest", "no manifest authority for XMRig removal")
        root_fd, parent = open_managed_root(trust, stack, create=False)
        if root_fd is None:
            if prior.manifest.versions:
                raise CollisionError("root", "manifest references a missing managed root")
            return ()
        manifest = trust.manifest()
        if manifest is None or manifest != prior.manifest:
            raise CollisionError("marker", "valid protected marker and manifest are required")
        names = manifest.names
        target = current_target(root_fd)
        if target is not None and trust.authorized_current(trust.marker_path, trust.manifest_path) is None:
            raise CollisionError("current", "current is not manifest-authorized")
        allowed = {*names, MARKER_NAME}
        if target is not None:
            allowed.add("current")
        if set(os.listdir(root_fd)) != allowed:
            raise CollisionError("inventory", "missing recorded or unrecorded root content; no files removed")
        for name in names:
            child = os.open(name, DIRECTORY_FLAGS, dir_fd=root_fd)
            with ExitStack() as version:
                _ = version.callback(os.close, child)
                info = os.fstat(child)
                if info.st_uid != trust.uid or info.st_mode & 0o022:
                    raise CollisionError("ownership", "version directory is foreign or writable")
                _validate_tree(child, trust.uid)
                verify_binary(root_fd, name, trust.uid)
        for name in names:
            remove_tree(root_fd, name)
        if target is not None:
            os.unlink("current", dir_fd=root_fd)
        os.unlink(MARKER_NAME, dir_fd=root_fd)
        os.fsync(root_fd)
        os.rmdir(trust.managed_root.name, dir_fd=parent)
        os.fsync(parent)
        store.write(replace(manifest, versions=()))
        return names


def _fields(artifact: Artifact) -> tuple[int, int, int, int, int, str | None]:
    return (artifact.uid, artifact.gid, artifact.mode, artifact.device, artifact.inode, artifact.sha256)


def _current(tree: Tree, path: str) -> Artifact:
    try:
        return tree.receipt(path, True)
    except DeploymentError as error:
        raise CollisionError("receipt", "authorized artifact is missing or unsafe: " + path) from error


def _authorized_leaves(tree: Tree, installer: Installer) -> dict[str, Artifact]:
    authorized: dict[str, Artifact] = {}
    for artifact in installer.artifacts:
        if not artifact.created or artifact.kind != "file" or leaf_identity(artifact.path) is None:
            continue
        if fixed_target(artifact.id) != (artifact.path, "file"):
            raise CollisionError("receipt", "receipt does not match the fixed grammar: " + artifact.id)
        if _fields(_current(tree, artifact.path)) != _fields(artifact):
            raise CollisionError("receipt", "authorized artifact changed since install: " + artifact.path)
        authorized[artifact.path] = artifact
    return authorized


def _authorized_directories(tree: Tree, installer: Installer) -> dict[str, Artifact]:
    authorized: dict[str, Artifact] = {}
    for artifact in installer.artifacts:
        if not artifact.created or artifact.kind != "directory" or artifact.path not in PRUNE_DIRS:
            continue
        try:
            current = tree.receipt(artifact.path, True)
        except DeploymentError:
            continue
        if _fields(current) != _fields(artifact):
            raise CollisionError("receipt", "created directory changed since install: " + artifact.path)
        authorized[artifact.path] = artifact
    return authorized


def _allowed_children(directory: str, leaves: dict[str, Artifact]) -> frozenset[str] | set[str]:
    if directory in CONTAINER_CHILDREN:
        return CONTAINER_CHILDREN[directory]
    return {Path(path).name for path in leaves if str(Path(path).parent) == directory}


def remove_leaf_receipts(trust: PathTrust) -> tuple[str, ...]:
    """Remove only created package/template file receipts after a full preflight of every container."""
    tree = Tree(trust.root, trust.uid)
    with ExitStack() as stack:
        store = stack.enter_context(locked_store(StoreContext(trust)))
        prior = store.read()
        if prior is None or prior.manifest.installer is None:
            raise CollisionError("receipt", "no per-file installer receipts; reinstall before removal")
        leaves = _authorized_leaves(tree, prior.manifest.installer)
        directories = _authorized_directories(tree, prior.manifest.installer)
        roots: dict[str, int] = {}
        for directory in (*LEAF_DIRS, *CONTAINER_CHILDREN):
            try:
                roots[directory] = tree.pin(stack, directory)
            except FileNotFoundError:
                continue
            except (DeploymentError, OSError) as error:
                raise CollisionError("receipt", "unsafe directory: " + directory) from error
        for directory, fd in roots.items():
            if not set(os.listdir(fd)) <= set(_allowed_children(directory, leaves)):
                raise CollisionError("receipt", "unrecorded content under " + directory + "; nothing removed")
        removed: list[str] = []
        for directory in LEAF_DIRS:
            fd = roots.get(directory)
            if fd is None:
                continue
            for path in sorted(path for path in leaves if str(Path(path).parent) == directory):
                os.unlink(Path(path).name, dir_fd=fd)
                removed.append(path)
            os.fsync(fd)
        for directory in sorted(directories, key=lambda item: item.count("/"), reverse=True):
            parent = roots.get(str(Path(directory).parent))
            if parent is None:
                parent = tree.pin(stack, str(Path(directory).parent))
            try:
                os.rmdir(Path(directory).name, dir_fd=parent)
            except OSError:
                continue
            os.fsync(parent)
        return tuple(removed)
