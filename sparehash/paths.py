# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Fixed production paths for Sparehash.

Artifact identities (receipts, configs, display strings) keep their logical
``/opt`` or ``/usr/local`` spelling.  Only descriptor-anchored I/O walks follow
the pinned :class:`~sparehash.layout.Layout` physical redirect.
"""

from __future__ import annotations

import os
from contextlib import ExitStack
from pathlib import Path
from typing import Final

from .layout import Layout, LayoutError, LayoutKind, probe, validate_handle

_REDIRECTABLE: Final = (Path("/opt"), Path("/usr/local"))


def _is_under(path: Path, prefix: Path) -> bool:
    return path == prefix or path.is_relative_to(prefix)


def _require_undrifted(layout: Layout) -> None:
    """Refuse when a pinned prefix no longer matches its physical path."""
    for key, physical in (("opt", layout.opt_physical), ("local", layout.local_physical)):
        handle = layout.descriptor_handles.get(key)
        if handle is not None and not validate_handle(handle, physical):
            raise LayoutError("consumer-drift", str(physical))


def physical_path(path: Path, root: Path, layout: Layout | None) -> Path:
    """Map a logical artifact name to its pinned physical path.

    A pinned prefix whose physical path no longer matches its pinned handle
    refuses with :class:`LayoutError` rather than falling back to a substituted
    target.  ``layout=None`` keeps the conventional spelling (no probe).
    """
    if layout is None:
        return path
    _require_undrifted(layout)
    if layout.kind is LayoutKind.CONVENTIONAL:
        return path
    for logical, physical in ((root / "opt", layout.opt_physical),
                              (root / "usr/local", layout.local_physical)):
        if _is_under(path, logical):
            return physical / path.relative_to(logical)
    return path


def pin_prefix(stack: ExitStack, path: Path, root: Path,
               layout: Layout | None) -> tuple[int, tuple[str, ...]]:
    """Start a no-follow walk at the matching pinned physical prefix.

    Production lazily probes the real root only when the requested path can be
    redirected (under ``/opt`` or ``/usr/local``); the stack owns every layout
    it creates.  An explicit fixture root without a layout keeps the
    conventional walk, so private temp trees never probe the host.
    """
    selected = layout
    if selected is None and root == Path("/") and any(_is_under(path, prefix)
                                                     for prefix in _REDIRECTABLE):
        selected = probe(root)
        _ = stack.callback(selected.close)
    mapped = physical_path(path, root, selected)
    if selected is not None:
        for key, physical in (("opt", selected.opt_physical), ("local", selected.local_physical)):
            if mapped.is_relative_to(physical) and key in selected.descriptor_handles:
                fd = os.dup(selected.descriptor_handles[key])
                _ = stack.callback(os.close, fd)
                return fd, mapped.relative_to(physical).parts
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    _ = stack.callback(os.close, fd)
    return fd, mapped.relative_to(root).parts

ETC_DIR: Final = Path("/etc/sparehash")
CONFIG_TOML: Final = ETC_DIR / "config.toml"
XMRIG_JSON: Final = ETC_DIR / "xmrig.json"
XMRIG_RUNTIME_JSON: Final = ETC_DIR / "xmrig.runtime.json"

STATE_FILE: Final = Path("/var/lib/sparehash/state.json")
STATE_DIR: Final = STATE_FILE.parent
HUGEPAGE_LEASE_FILE: Final = STATE_DIR / "hugepage-lease.json"
INSTALL_MANIFEST_FILE: Final = STATE_DIR / "install-manifest.json"
RUN_DIR: Final = Path("/run/sparehash")
CONTROL_SOCK: Final = RUN_DIR / "control.sock"
MINER_RUN_DIR: Final = Path("/run/sparehash-miner")

OPT_DIR: Final = Path("/opt/sparehash")
OPT_SRC: Final = OPT_DIR / "src"
XMRIG_OPT: Final = Path("/opt/xmrig")
XMRIG_CURRENT: Final = XMRIG_OPT / "current" / "xmrig"
CLI_PATH: Final = Path("/usr/local/bin/sparehash")

UNIT_CONTROLLER: Final = "sparehash.service"
UNIT_MINER: Final = "sparehash-xmrig.service"

SPAREHASH_SYSFS_ROOT: Final = "SPAREHASH_SYSFS_ROOT"
SPAREHASH_HWMON_ROOT: Final = "SPAREHASH_HWMON_ROOT"
SPAREHASH_RUN_DIR: Final = "SPAREHASH_RUN_DIR"
SPAREHASH_STATE_FILE: Final = "SPAREHASH_STATE_FILE"
