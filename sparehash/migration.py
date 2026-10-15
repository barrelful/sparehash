# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Prevalidated schema-1 to schema-2 receipt translation; never infers ownership."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Final, cast

from .xmrig_ledger import HEX64, LOGICAL_LOCAL, LOGICAL_OPT, Json, ManifestError, apply_layout
from .xmrig_manifest import layout_roots

_FIELDS: Final = ("id", "path", "kind", "created", "uid", "gid", "mode", "device", "inode", "sha256")
_INTEGERS: Final = ("uid", "gid", "mode", "device", "inode")
_KINDS: Final = ("file", "directory")


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ManifestError("artifact " + field)
    return value


def _path(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//") or len(value) > 4096:
        raise ManifestError("artifact " + field)
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ManifestError("artifact " + field)
    if str(PurePosixPath(value)) != value or ".." in PurePosixPath(value).parts:
        raise ManifestError("artifact " + field)
    return value


def _fields(value: dict[str, object]) -> dict[str, object]:
    if set(value) != set(_FIELDS):
        raise ManifestError("artifact keys")
    identity = value["id"]
    if not isinstance(identity, str) or not identity or len(identity) > 4096:
        raise ManifestError("artifact id")
    kind = value["kind"]
    if kind not in _KINDS:
        raise ManifestError("artifact kind")
    created = value["created"]
    if not isinstance(created, bool):
        raise ManifestError("artifact created")
    digest = value["sha256"]
    if digest is not None and (not isinstance(digest, str) or HEX64.fullmatch(digest) is None):
        raise ManifestError("artifact sha256")
    return {"id": identity, "path": _path(value["path"], "path"), "kind": kind, "created": created,
            **{name: _integer(value[name], name) for name in _INTEGERS}, "sha256": digest}


def _physical(logical: str, physical: str, opt_root: str, local_root: str) -> None:
    if (not physical.startswith("/") or len(physical) > 4096
            or str(PurePosixPath(physical)) != physical or ".." in PurePosixPath(physical).parts):
        raise ManifestError("artifact physical path")
    if logical == LOGICAL_OPT or logical.startswith(LOGICAL_OPT + "/"):
        expected = opt_root
    elif logical == LOGICAL_LOCAL or logical.startswith(LOGICAL_LOCAL + "/"):
        expected = local_root
    else:
        return
    if physical != expected and not physical.startswith(expected + "/"):
        raise ManifestError("artifact physical root")


def transform_v1_to_v2_artifact(v1_artifact: dict[str, object], layout: dict[str, str]) -> dict[str, object]:
    """Translate one logical schema-1 receipt onto the selected physical layout.

    The shape, the logical path and the resulting physical path are validated; the
    original ``created`` flag is carried through. An artifact with no receipt proof
    (not created, or a file without a content digest) is returned unowned with a
    reason code. Translation never invents ownership and never marks ``created``.
    """
    roots = layout_roots(cast(Json, layout))
    artifact = _fields(v1_artifact)
    logical = artifact["path"]
    assert isinstance(logical, str)
    physical = apply_layout(logical, roots["opt_root"], roots["local_root"])
    _physical(logical, physical, roots["opt_root"], roots["local_root"])
    owned = artifact["created"] is True and (artifact["kind"] == "directory" or artifact["sha256"] is not None)
    return {**artifact, "path": physical, "logical_path": logical, "created": owned,
            "reason": None if owned else "unowned"}
