# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Protected source bootstrap: consent, stable staging, then receipted publication.

``install.sh`` only selects a fixed environment and a source root. This module is
the first trust boundary: it records explicit consent, copies the selected local
release into a private root-owned staging directory using no-follow reads, verifies
that staging against the source inventory, enforces the 128-module cap, and only
then publishes the staged payload through the installer ledger. It never removes a
foreign entry and never writes outside its own managed protected paths.

Publication is deliberately not one atomic rename: each artifact and its receipt
is an individually fsynced step on the single canonical store, so an interruption
leaves the staging tree intact and a recoverable, receipted partial installation.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Final, final
from uuid import uuid4

from .deployment import DeploymentError, Tree
from .install_ledger import Ledger, Receipt
from .install_manifest import StoreContext
from .xmrig_ledger import PACKAGE_DIR, PACKAGE_LEAF, TEMPLATE_DIR, UNIT_TEMPLATES
from .xmrig_paths import FILE_FLAGS

MAX_MODULES: Final = 128
STAGING_PREFIX: Final = ".staging-"
IGNORED_ENTRIES: Final = frozenset(("__pycache__",))
PACKAGE_SUBDIR: Final = "sparehash"
TEMPLATE_SUBDIR: Final = "systemd"
SHIM_SOURCE: Final = "/opt/sparehash/src"
SHIM_PATH: Final = "/usr/local/bin/sparehash"
MANAGED_DIRECTORIES: Final = ("/opt/sparehash", "/opt/sparehash/src", PACKAGE_DIR, TEMPLATE_DIR)

SHIM_TEMPLATE: Final = """#!/usr/bin/python3
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, "{source}")

from sparehash.__main__ import main

raise SystemExit(main())
"""


class ConsentRefused(DeploymentError):
    """Explicit installation consent was not granted; no host mutation is permitted."""


def source_digest(path: Path) -> str:
    """No-follow SHA-256 of a protected one-link regular source file."""
    descriptor = os.open(path, FILE_FLAGS)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise DeploymentError("source", f"unsafe source file: {path}")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1 << 20):
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
                info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns):
            raise DeploymentError("source", f"source changed during read: {path}")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _iter_entries(root: Path, label: str) -> Iterator[tuple[str, Path]]:
    """Yield protected regular entries, ignoring VCS/bytecode artifacts."""
    try:
        entries = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError as error:
        raise DeploymentError("source", f"cannot read {label} directory: {error}") from error
    for entry in entries:
        if entry.name in IGNORED_ENTRIES or entry.name.startswith("."):
            continue
        if entry.is_symlink() or not entry.is_file():
            raise DeploymentError("source", f"unexpected {label} entry: {entry.name}")
        yield entry.name, entry


def _collect_source(src_root: Path, module_cap: int) -> dict[str, Path]:
    """Strict inventory of a source or staging tree, or fail closed."""
    package = src_root / PACKAGE_SUBDIR
    templates = src_root / TEMPLATE_SUBDIR
    for directory in (package, templates):
        if directory.is_symlink() or not directory.is_dir():
            raise DeploymentError("source", f"source directory is missing or unsafe: {directory}")
    collected: dict[str, Path] = {}
    modules = 0
    for name, path in _iter_entries(package, "package"):
        if PACKAGE_LEAF.fullmatch(name) is None:
            raise DeploymentError("source", f"unexpected package entry: {name}")
        modules += 1
        if modules > module_cap:
            raise DeploymentError("bootstrap", f"package exceeds the {module_cap}-module cap")
        collected[f"{PACKAGE_SUBDIR}/{name}"] = path
    for name, path in _iter_entries(templates, "template"):
        if name not in UNIT_TEMPLATES:
            raise DeploymentError("source", f"unexpected template entry: {name}")
        collected[f"{TEMPLATE_SUBDIR}/{name}"] = path
    if f"{PACKAGE_SUBDIR}/__main__.py" not in collected:
        raise DeploymentError("source", "source package has no __main__ entrypoint")
    return collected


def staging_path(tree: Tree) -> Path:
    """A private, unique staging directory adjacent to the managed source tree."""
    return tree.path("/opt/sparehash") / (STAGING_PREFIX + uuid4().hex)


@final
class Bootstrapper:
    """Consent-gated staging and receipted publication of the protected source payload."""

    def __init__(self, tree: Tree, *, module_cap: int = MAX_MODULES) -> None:
        self.tree = tree
        self.module_cap = module_cap
        self._consented = False

    @property
    def group(self) -> int:
        return 0 if self.tree.uid == 0 else os.getgid()

    def consent(self, granted: bool) -> None:
        """Record explicit installation consent; any other value refuses before writes."""
        if granted is not True:
            raise ConsentRefused("consent", "installation consent was refused; no host mutation performed")
        self._consented = True

    def _require_consent(self, action: str) -> None:
        if not self._consented:
            raise ConsentRefused("consent", f"no recorded consent for {action}; refusing before any write")

    def _make_staging(self, staging_root: Path) -> None:
        parent = staging_root.parent
        if not parent.exists():
            parent.mkdir(parents=True, mode=0o755)
            os.chmod(parent, 0o755)
        try:
            staging_root.mkdir(mode=0o700)
        except FileExistsError as error:
            raise DeploymentError("staging", f"staging path already exists; refusing to reuse: {staging_root}") from error

    def _copy_file(self, source: Path, destination: Path) -> None:
        descriptor = os.open(source, FILE_FLAGS)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise DeploymentError("source", f"unsafe source file: {source}")
            target = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            with os.fdopen(target, "wb") as handle:
                os.fchmod(handle.fileno(), 0o644)
                while chunk := os.read(descriptor, 1 << 20):
                    _ = handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(descriptor)

    def _fsync_directory(self, path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def stage_source(self, src_root: Path, staging_root: Path) -> Path:
        """Copy and verify the selected source into a protected staging directory."""
        self._require_consent("source staging")
        sources = _collect_source(src_root, self.module_cap)
        before = {relative: source_digest(path) for relative, path in sources.items()}
        self._make_staging(staging_root)
        try:
            for relative, path in sources.items():
                destination = staging_root / relative
                destination.parent.mkdir(mode=0o755, exist_ok=True)
                self._copy_file(path, destination)
            # Re-read the source after copying: a mid-stage mutation fails closed.
            after = {relative: source_digest(path) for relative, path in _collect_source(src_root, self.module_cap).items()}
            if after != before:
                raise DeploymentError("source", "source changed during staging; refusing to publish")
            staged = {relative: source_digest(path) for relative, path in _collect_source(staging_root, self.module_cap).items()}
            if staged != before:
                raise DeploymentError("staging", "staged payload does not match the verified source inventory")
            for directory in (staging_root / PACKAGE_SUBDIR, staging_root / TEMPLATE_SUBDIR, staging_root):
                self._fsync_directory(directory)
            return staging_root
        except BaseException:
            shutil.rmtree(staging_root, ignore_errors=True)
            raise

    def _ensure_directory(self, ledger: Ledger, name: str, context: StoreContext | None) -> None:
        path = self.tree.path(name)
        try:
            info = path.lstat()
        except FileNotFoundError:
            def create(name: str = name) -> tuple[Receipt, ...]:
                self.tree.directory(name, self.group)
                return (self.tree.receipt(name, True),)
            ledger.perform((name, "create-directory"), create, context=context)
            return
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_uid != self.tree.uid or info.st_mode & 0o022):
            raise DeploymentError("bootstrap", f"refusing foreign or unsafe directory: {name}")
        ledger.applied(0, (self.tree.receipt(name, False),), context=context)

    def _install_file(self, ledger: Ledger, name: str, identity: str, data: bytes, mode: int, gid: int,
                      context: StoreContext | None) -> None:
        old = self.tree.read(name)
        if old is not None:
            if old != data:
                raise DeploymentError("bootstrap", f"refusing to overwrite changed or foreign {name}")
            info = self.tree.path(name).lstat()
            if info.st_uid == self.tree.uid and info.st_gid == gid and info.st_mode & 0o777 == mode:
                ledger.applied(0, (self.tree.receipt(name, False, identity=identity),), context=context)
                return
        def mutate() -> tuple[Receipt, ...]:
            self.tree.write(name, (data, mode, gid))
            return (self.tree.receipt(name, old is None, identity=identity),)
        ledger.perform((identity, "write-file"), mutate, context=context)

    def _shim(self) -> bytes:
        return SHIM_TEMPLATE.format(source=str(self.tree.path(SHIM_SOURCE))).encode("utf-8")

    def publish(self, staging_root: Path, ledger: Ledger, context: StoreContext | None = None) -> None:
        """Publish the verified staging tree through the ledger; never delete foreign entries."""
        self._require_consent("source publication")
        staged = _collect_source(staging_root, self.module_cap)
        for path in staged.values():
            _ = source_digest(path)
        for name in MANAGED_DIRECTORIES:
            self._ensure_directory(ledger, name, context)
        gid = self.group
        for relative, path in sorted(staged.items()):
            data = path.read_bytes()
            subdirectory, _, leaf = relative.partition("/")
            if subdirectory == PACKAGE_SUBDIR:
                target = PACKAGE_DIR + "/" + leaf
                identity = "package-file:" + leaf
            else:
                target = TEMPLATE_DIR + "/" + leaf
                identity = "unit-template:" + leaf
            self._install_file(ledger, target, identity, data, 0o644, gid, context)
        self._install_file(ledger, SHIM_PATH, SHIM_PATH, self._shim(), 0o755, gid, context)
