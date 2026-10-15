# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Pin the finite trusted OS prefix redirects and their canonical physical roots.

Logical artifact identities keep their ``/opt`` or ``/usr/local`` spelling.  Only
the two documented OS redirects are followed, and only to their already-known
canonical targets.  Every real ancestor and target is pinned with directory
descriptors opened ``O_NOFOLLOW`` so a substituted link, ancestor or target is
detected rather than silently resolved.  There is no ``realpath``/``resolve``
call, no arbitrary prefix and no environment override in this module.

Accepted literal link targets (nothing else):

* ``/opt``       -> absent (conventional), ``var/opt``, or ``/var/opt``
* ``/usr/local`` -> absent (conventional), ``../var/usrlocal``, or ``/var/usrlocal``

The single ``..`` in ``../var/usrlocal`` is a documented literal, not a general
traversal permission.  An extra hop, a loop, a different target, a non-directory
prefix, or an unsafe owner/mode refuses with :class:`LayoutError`.  A platform
without the required descriptor capability returns :class:`LayoutInconclusiveError`
instead of guessing.

``LayoutKind`` names which prefixes are physically redirected:

=========================== ============ =============
kind                        ``/opt``     ``/usr/local``
=========================== ============ =============
``CONVENTIONAL``            no           no
``ATOMIC_OPT``              relative     no
``ATOMIC_OPT_VAROPT``       absolute     no
``ATOMIC_USRLOCAL_VARUSRLOCAL`` no        yes
``ATOMIC_OPT_VAROPT_USRLOCAL`` yes        yes
=========================== ============ =============
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import stat
import struct
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

__all__ = [
    "DescriptorCapabilities",
    "Layout",
    "LayoutError",
    "LayoutInconclusiveError",
    "LayoutKind",
    "probe",
    "validate_handle",
]

_DIRECTORY_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_PROBE_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_OPT_RELATIVE: Final = "var/opt"
_OPT_ABSOLUTE: Final = "/var/opt"
_LOCAL_RELATIVE: Final = "../var/usrlocal"
_LOCAL_ABSOLUTE: Final = "/var/usrlocal"
_IDENTITY_PROBE: Final = "identity-probe"
_IDENTITY_PROBE_LIMIT: Final = 4096


class LayoutKind(Enum):
    """Finite classification of the physical prefix mapping in effect."""

    CONVENTIONAL = "conventional"
    ATOMIC_OPT = "atomic-opt"
    ATOMIC_OPT_VAROPT = "atomic-opt-varopt"
    ATOMIC_OPT_VAROPT_USRLOCAL = "atomic-opt-varopt-usrlocal"
    ATOMIC_USRLOCAL_VARUSRLOCAL = "atomic-usrlocal-varusrlocal"


class LayoutError(RuntimeError):
    """A redirect, ancestor or target is not in the trusted finite grammar."""

    reason_codes: tuple[str, ...]

    def __init__(self, *reason_codes: str) -> None:
        self.reason_codes = tuple(reason_codes)
        super().__init__("layout: " + ", ".join(self.reason_codes))


class LayoutInconclusiveError(LayoutError):
    """The platform lacks the descriptor capability to decide safely."""


@dataclass(frozen=True, slots=True)
class DescriptorCapabilities:
    """Seam for the descriptor primitives this module depends on."""

    nofollow: bool
    dir_fd: bool


def _capabilities() -> DescriptorCapabilities:
    return DescriptorCapabilities(
        nofollow=hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"),
        dir_fd=(os.open in os.supports_dir_fd
                and os.stat in os.supports_dir_fd
                and os.readlink in os.supports_dir_fd),
    )


def _close_quietly(descriptor: int) -> None:
    with contextlib.suppress(OSError):
        os.close(descriptor)


def _require_protected(info: os.stat_result, uid: int, path: Path, stage: str) -> None:
    """Require the owner and reject group/other writable directories."""
    if info.st_uid != uid or info.st_mode & 0o022:
        raise LayoutError(f"{stage}-untrusted", str(path))


def _require_owned(info: os.stat_result, uid: int, path: Path, stage: str) -> None:
    """Symlink permissions are meaningless, so only ownership is enforced."""
    if info.st_uid != uid:
        raise LayoutError(f"{stage}-untrusted", str(path))


def _stat_leaf(parent_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise LayoutError("prefix-unreadable", name) from error


def _open_directory(parent_fd: int, name: str, root: Path, uid: int, stage: str) -> int:
    """Open one directory component without following a link, then vet it."""
    info = _stat_leaf(parent_fd, name)
    if info is None:
        raise LayoutError(f"{stage}-unopenable", name)
    if stat.S_ISLNK(info.st_mode):
        raise LayoutError("redirect-loop", name)
    if not stat.S_ISDIR(info.st_mode):
        raise LayoutError(f"{stage}-not-directory", name)
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        code = "redirect-loop" if error.errno in (errno.ELOOP, errno.ENOTDIR) \
            else f"{stage}-unopenable"
        raise LayoutError(code, name) from error
    try:
        _require_protected(os.fstat(descriptor), uid, root, stage)
    except BaseException:
        _close_quietly(descriptor)
        raise
    return descriptor


def _pin_under(anchor_fd: int, components: tuple[str, ...], root: Path, uid: int,
               stage: str) -> int:
    """Pin every component below a trusted anchor, never following a link."""
    current = os.dup(anchor_fd)
    try:
        for component in components:
            following = _open_directory(current, component, root, uid, stage)
            _close_quietly(current)
            current = following
        return current
    except BaseException:
        _close_quietly(current)
        raise


def _resolve_opt(root_fd: int, root: Path, uid: int, handles: dict[str, int]) -> tuple[Path, str]:
    info = _stat_leaf(root_fd, "opt")
    if info is None:
        return root / "opt", "absent"
    if stat.S_ISLNK(info.st_mode):
        _require_owned(info, uid, root / "opt", "opt-link")
        target = os.readlink("opt", dir_fd=root_fd)
        if target == _OPT_RELATIVE:
            form = "redirect-relative"
        elif target == _OPT_ABSOLUTE:
            form = "redirect-absolute"
        else:
            raise LayoutError("opt-target-unsupported", target)
        handles["opt"] = _pin_under(root_fd, ("var", "opt"), root, uid, "opt")
        return root / "var" / "opt", form
    if not stat.S_ISDIR(info.st_mode):
        raise LayoutError("opt-not-directory", str(root / "opt"))
    handles["opt"] = _open_directory(root_fd, "opt", root, uid, "opt")
    return root / "opt", "conventional"


def _resolve_local(root_fd: int, root: Path, uid: int,
                   handles: dict[str, int]) -> tuple[Path, str]:
    usr_info = _stat_leaf(root_fd, "usr")
    if usr_info is None or not stat.S_ISDIR(usr_info.st_mode):
        raise LayoutError("usr-not-directory")
    usr_fd = _open_directory(root_fd, "usr", root, uid, "usr")
    handles["usr"] = usr_fd
    info = _stat_leaf(usr_fd, "local")
    if info is None:
        return root / "usr" / "local", "absent"
    if stat.S_ISLNK(info.st_mode):
        _require_owned(info, uid, root / "usr" / "local", "local-link")
        target = os.readlink("local", dir_fd=usr_fd)
        if target == _LOCAL_RELATIVE:
            form = "redirect-relative"
        elif target == _LOCAL_ABSOLUTE:
            form = "redirect-absolute"
        else:
            raise LayoutError("local-target-unsupported", target)
        handles["local"] = _pin_under(root_fd, ("var", "usrlocal"), root, uid, "local")
        return root / "var" / "usrlocal", form
    if not stat.S_ISDIR(info.st_mode):
        raise LayoutError("local-not-directory", str(root / "usr" / "local"))
    handles["local"] = _open_directory(usr_fd, "local", root, uid, "local")
    return root / "usr" / "local", "conventional"


def _classify(opt_form: str, local_form: str) -> LayoutKind:
    opt_redirected = opt_form.startswith("redirect")
    local_redirected = local_form.startswith("redirect")
    if opt_redirected and local_redirected:
        return LayoutKind.ATOMIC_OPT_VAROPT_USRLOCAL
    if opt_redirected:
        return (LayoutKind.ATOMIC_OPT if opt_form == "redirect-relative"
                else LayoutKind.ATOMIC_OPT_VAROPT)
    if local_redirected:
        return LayoutKind.ATOMIC_USRLOCAL_VARUSRLOCAL
    return LayoutKind.CONVENTIONAL


def _read_identity_probe(directory_fd: int) -> bytes | None:
    """Read a bounded ``identity-probe`` child if it is a regular file."""
    try:
        descriptor = os.open(_IDENTITY_PROBE, _PROBE_FLAGS, dir_fd=directory_fd)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _IDENTITY_PROBE_LIMIT:
            return None
        return os.read(descriptor, _IDENTITY_PROBE_LIMIT + 1)
    finally:
        _close_quietly(descriptor)


def _identity(kind: LayoutKind, handles: dict[str, int], opt_form: str,
              local_form: str) -> bytes:
    """SHA-256 over the pinned directory identities and any bounded probe content."""
    digest = hashlib.sha256()
    digest.update(kind.name.encode("ascii"))
    for key in ("root", "opt", "local"):
        digest.update(key.encode("ascii"))
        descriptor = handles.get(key)
        if descriptor is None:
            digest.update(b"|absent|")
            continue
        info = os.fstat(descriptor)
        digest.update(struct.pack("<QQI", info.st_dev, info.st_ino, info.st_mode))
        probe_bytes = _read_identity_probe(descriptor)
        if probe_bytes is not None:
            digest.update(b"|probe|")
            digest.update(probe_bytes)
    digest.update(opt_form.encode("ascii"))
    digest.update(local_form.encode("ascii"))
    return digest.digest()


@dataclass(slots=True)
class Layout:
    """A pinned physical mapping; I/O is performed through its descriptors."""

    kind: LayoutKind
    opt_physical: Path
    local_physical: Path
    descriptor_handles: dict[str, int]
    identity: bytes
    reason_codes: tuple[str, ...]

    def _key_for(self, path: Path) -> str | None:
        if path == self.opt_physical:
            return "opt"
        if path == self.local_physical:
            return "local"
        return None

    def open_handle(self, path: Path) -> int:
        """Return a descriptor scoped to a pinned root, closing a previous one.

        The returned descriptor is a duplicate of the pinned, no-follow directory
        handle.  Re-issuing a handle for the same root closes the earlier one so
        the layout never leaks descriptors.  ``close()`` releases every handle.
        """
        key = self._key_for(path)
        if key is None or key not in self.descriptor_handles:
            raise LayoutError("unscoped-handle", str(path))
        descriptor = os.dup(self.descriptor_handles[key])
        stale = self.descriptor_handles.pop(f"scoped:{key}", None)
        if stale is not None and stale != descriptor:
            _close_quietly(stale)
        self.descriptor_handles[f"scoped:{key}"] = descriptor
        return descriptor

    def close(self) -> None:
        """Idempotently release every pinned and scoped descriptor."""
        for descriptor in self.descriptor_handles.values():
            _close_quietly(descriptor)
        self.descriptor_handles.clear()


def validate_handle(fd: int, expected_path: Path) -> bool:
    """Confirm a pinned descriptor still matches the object at ``expected_path``.

    Both device/inode and link count/mode are compared, so a replaced directory,
    a substituted link leaf or a re-mounted target is detected between open and
    use.  ``expected_path`` is inspected with ``follow_symlinks=False``.
    """
    try:
        pinned = os.fstat(fd)
        expected = os.stat(expected_path, follow_symlinks=False)
    except OSError:
        return False
    return (pinned.st_dev == expected.st_dev
            and pinned.st_ino == expected.st_ino
            and pinned.st_nlink == expected.st_nlink
            and stat.S_IFMT(pinned.st_mode) == stat.S_IFMT(expected.st_mode)
            and stat.S_IMODE(pinned.st_mode) == stat.S_IMODE(expected.st_mode))


def probe(root: Path, *, uid: int = 0) -> Layout:
    """Pin the redirects below ``root`` and return their physical ``Layout``.

    ``root`` is ``/`` in production and a private fixture anchor in tests.  A
    refusal raises :class:`LayoutError`; a missing descriptor capability raises
    :class:`LayoutInconclusiveError`.  The returned handles stay open until
    :meth:`Layout.close`.
    """
    capabilities = _capabilities()
    if not (capabilities.nofollow and capabilities.dir_fd):
        raise LayoutInconclusiveError("descriptor-capability")
    anchor = Path(root)
    try:
        root_fd = os.open(anchor, _DIRECTORY_FLAGS)
    except OSError as error:
        raise LayoutError("root-unopenable", str(error)) from error
    handles: dict[str, int] = {"root": root_fd}
    try:
        _require_protected(os.fstat(root_fd), uid, anchor, "root")
        opt_physical, opt_form = _resolve_opt(root_fd, anchor, uid, handles)
        local_physical, local_form = _resolve_local(root_fd, anchor, uid, handles)
        kind = _classify(opt_form, local_form)
        identity = _identity(kind, handles, opt_form, local_form)
        return Layout(
            kind=kind,
            opt_physical=opt_physical,
            local_physical=local_physical,
            descriptor_handles=handles,
            identity=identity,
            reason_codes=(f"opt-{opt_form}", f"local-{local_form}"),
        )
    except BaseException:
        for descriptor in handles.values():
            _close_quietly(descriptor)
        raise
