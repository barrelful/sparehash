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
                    "write-file", "merge-gaming", "daemon-reload", "enable-controller"]
OPERATIONS: Final[tuple[Operation, ...]] = (
    "create-user", "create-group", "add-membership", "create-directory",
    "write-file", "merge-gaming", "daemon-reload", "enable-controller")
PATHS: Final = frozenset((
    "/opt/sparehash", "/opt/sparehash/src", "/usr/local/bin/sparehash",
    "/etc/sparehash", "/var/lib/sparehash", "/etc/sparehash/config.toml",
    "/etc/sparehash/xmrig.json", "/etc/sparehash/xmrig.runtime.json",
    "/etc/systemd/system/sparehash.service", "/etc/systemd/system/sparehash-xmrig.service"))
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
