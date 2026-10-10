# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Immutable installer receipt vocabulary and JSON boundary primitives.

Artifact IDs are fixed deployment paths or gaming-{ini,backup,config-directory}:USER.
Action targets also admit user:xmrig, group:{xmrig,sparehash}, and
membership:USER:sparehash, each referencing a corresponding receipt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal, TypeAlias, TypeVar, assert_never

Json: TypeAlias = None | bool | int | float | str | list["Json"] | dict[str, "Json"]
T = TypeVar("T", bound=str)
Phase = Literal["installing", "ready"]
ActionState = Literal["pending", "applied", "reverted"]
Operation = Literal["create-user", "create-group", "add-membership", "create-directory",
                    "write-file", "merge-gaming", "daemon-reload", "enable-controller", "disable-controller", "start-controller"]
OPERATIONS: Final[tuple[Operation, ...]] = (
    "create-user", "create-group", "add-membership", "create-directory",
    "write-file", "merge-gaming", "daemon-reload", "enable-controller", "disable-controller", "start-controller")
DIRECTORIES: Final = frozenset((
    "/opt/sparehash", "/opt/sparehash/src", "/opt/sparehash/src/sparehash",
    "/opt/sparehash/src/systemd", "/etc/sparehash", "/var/lib/sparehash"))
PATHS: Final = frozenset((
    *DIRECTORIES, "/usr/local/bin/sparehash", "/etc/sparehash/config.toml",
    "/etc/sparehash/xmrig.json", "/etc/sparehash/xmrig.runtime.json",
    "/etc/systemd/system/sparehash.service", "/etc/systemd/system/sparehash-xmrig.service"))
PACKAGE_DIR: Final = "/opt/sparehash/src/sparehash"
TEMPLATE_DIR: Final = "/opt/sparehash/src/systemd"
UNIT_TEMPLATES: Final = ("sparehash.service.in", "sparehash-xmrig.service.in")
BOOTSTRAP_DIRS: Final = ("/opt/sparehash", "/opt/sparehash/src", PACKAGE_DIR, TEMPLATE_DIR)
BOOTSTRAP_SHIM: Final = "/usr/local/bin/sparehash"
PACKAGE_LEAF: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\.py")
ArtifactKind = Literal["file", "directory"]
USERNAME: Final = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
HEX64: Final = re.compile(r"[0-9a-f]{64}")
HEX32: Final = re.compile(r"[0-9a-f]{32}")
DATE: Final = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z")


class ManifestError(ValueError):
    """Malformed or oversized canonical metadata; never authorize its contents."""

    field: str

    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(f"Invalid manifest field: {field}")


@dataclass(frozen=True, slots=True)
class UserIdentity:
    kind: Literal["user"]
    name: Literal["xmrig"]
    uid: int
    gid: int
    created: bool


@dataclass(frozen=True, slots=True)
class GroupIdentity:
    kind: Literal["group"]
    name: Literal["xmrig", "sparehash"]
    gid: int
    created: bool


@dataclass(frozen=True, slots=True)
class Membership:
    user: str
    uid: int
    group: Literal["sparehash"]
    gid: int
    added: bool


@dataclass(frozen=True, slots=True)
class Artifact:
    id: str
    path: str
    kind: Literal["file", "directory"]
    created: bool
    uid: int
    gid: int
    mode: int
    device: int
    inode: int
    sha256: str | None


@dataclass(frozen=True, slots=True)
class Action:
    sequence: int
    install_id: str
    at: str
    operation: Operation
    target: str
    state: ActionState


@dataclass(frozen=True, slots=True)
class Installer:
    schema: Literal[1]
    phase: Phase
    identities: tuple[UserIdentity | GroupIdentity, ...]
    memberships: tuple[Membership, ...]
    artifacts: tuple[Artifact, ...]
    actions: tuple[Action, ...]


def mapping(value: Json) -> dict[str, Json]:
    match value:
        case dict():
            return value
        case None | bool() | int() | float() | str() | list():
            raise ManifestError("object")
    assert_never(value)


def items(value: Json) -> list[Json]:
    match value:
        case list():
            return value
        case None | bool() | int() | float() | str() | dict():
            raise ManifestError("array")
    assert_never(value)


def text(value: Json) -> str:
    match value:
        case str():
            return value
        case None | bool() | int() | float() | list() | dict():
            raise ManifestError("string")
    assert_never(value)


def integer(value: Json, maximum: int = 2**63 - 1) -> int:
    match value:
        case bool():
            raise ManifestError("integer")
        case int() if 0 <= value <= maximum:
            return value
        case None | int() | float() | str() | list() | dict():
            raise ManifestError("integer")
    assert_never(value)


def boolean(value: Json) -> bool:
    match value:
        case bool():
            return value
        case None | int() | float() | str() | list() | dict():
            raise ManifestError("boolean")
    assert_never(value)


def choice(value: Json, choices: tuple[T, ...]) -> T:
    for candidate in choices:
        if value == candidate:
            return candidate
    raise ManifestError("enum")


def keys(document: dict[str, Json], expected: str) -> None:
    if set(document) != set(expected.split()):
        raise ManifestError("keys")


def date(value: Json) -> str:
    result = text(value)
    if DATE.fullmatch(result) is None:
        raise ManifestError("date")
    try:
        _ = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as error:
        raise ManifestError("date") from error
    return result


def fixed_target(identity: str) -> tuple[str, ArtifactKind] | None:
    """Exact (path, kind) for a fixed receipt id, or None when outside the vocabulary."""
    if identity in PATHS:
        return identity, "directory" if identity in DIRECTORIES else "file"
    prefix, separator, leaf = identity.partition(":")
    if prefix == "package-file" and separator == ":" and PACKAGE_LEAF.fullmatch(leaf) is not None:
        return PACKAGE_DIR + "/" + leaf, "file"
    if prefix == "unit-template" and separator == ":" and leaf in UNIT_TEMPLATES:
        return TEMPLATE_DIR + "/" + leaf, "file"
    return None


def leaf_identity(path: str) -> str | None:
    """Exact typed receipt id for an installed package/template leaf path, or None."""
    package = PACKAGE_DIR + "/"
    if path.startswith(package):
        leaf = path.removeprefix(package)
        if PACKAGE_LEAF.fullmatch(leaf) is not None:
            return "package-file:" + leaf
        return None
    template = TEMPLATE_DIR + "/"
    if path.startswith(template):
        leaf = path.removeprefix(template)
        if leaf in UNIT_TEMPLATES:
            return "unit-template:" + leaf
    return None


def bootstrap_target(path: str) -> tuple[ArtifactKind, str, int] | None:
    """Exact (kind, receipt identity, mode) for a path install.sh itself creates, or None.

    The shell bootstrap owns only four directories, the CLI shim and package/template
    leaves; configuration, deployed units and runtime state are never bootstrap targets.
    """
    if path in BOOTSTRAP_DIRS:
        return "directory", path, 0o755
    if path == BOOTSTRAP_SHIM:
        return "file", path, 0o755
    identity = leaf_identity(path)
    if identity is None:
        return None
    return "file", identity, 0o644
