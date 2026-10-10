# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Strict legacy/Option A manifest parsing; no filesystem ownership authority.

Each nested record has exactly its dataclass fields. No API token is accepted.
The installer section records receipts only; B6 marker checks live in PathTrust.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Callable, Final, Literal, assert_never

from .xmrig_ledger import (
    HEX32, HEX64, OPERATIONS, USERNAME, Action, Artifact, GroupIdentity,
    Installer, Json, ManifestError as ManifestError, Membership, UserIdentity,
    boolean, choice, date, fixed_target, integer, items, keys, mapping, text,
)

MANIFEST_ROOT: Final = "/opt/xmrig"
MAX_METADATA_BYTES: Final = 64 * 1024
VERSION_PATTERN: Final = re.compile(r"xmrig-\d+\.\d+\.\d+")
SOURCE_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}")
_LOAD_JSON: Final[Callable[..., Json]] = json.loads
IDENTITY_KINDS: Final[tuple[Literal["user", "group"], ...]] = ("user", "group")
ARTIFACT_KINDS: Final[tuple[Literal["file", "directory"], ...]] = ("file", "directory")
PHASES: Final[tuple[Literal["installing", "ready"], ...]] = ("installing", "ready")


@dataclass(frozen=True, slots=True)
class VersionRecord:
    version: str
    installed_at: str
    source: str


@dataclass(frozen=True, slots=True)
class Manifest:
    root: str
    versions: tuple[VersionRecord, ...]
    installer: Installer | None = None

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(record.version for record in self.versions)


def _unique_object(pairs: list[tuple[str, Json]]) -> dict[str, Json]:
    result: dict[str, Json] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError("duplicate key")
        result[key] = value
    return result


def _identity(value: Json) -> UserIdentity | GroupIdentity:
    record = mapping(value)
    kind: Literal["user", "group"] = choice(record["kind"], IDENTITY_KINDS)
    gid, created = integer(record["gid"]), boolean(record["created"])
    match kind:
        case "user":
            keys(record, "kind name uid gid created")
            return UserIdentity(kind, choice(record["name"], ("xmrig",)), integer(record["uid"]), gid, created)
        case "group":
            keys(record, "kind name gid created")
            return GroupIdentity(kind, choice(record["name"], ("xmrig", "sparehash")), gid, created)
    assert_never(kind)


def _membership(value: Json) -> Membership:
    record = mapping(value)
    keys(record, "user uid group gid added")
    user = text(record["user"])
    if USERNAME.fullmatch(user) is None:
        raise ManifestError("membership user")
    return Membership(user, integer(record["uid"]), choice(record["group"], ("sparehash",)),
                      integer(record["gid"]), boolean(record["added"]))


def _artifact(value: Json) -> Artifact:
    record = mapping(value)
    keys(record, "id path kind created uid gid mode device inode sha256")
    identity, path = text(record["id"]), text(record["path"])
    kind: Literal["file", "directory"] = choice(record["kind"], ARTIFACT_KINDS)
    suffix = identity.partition(":")
    fixed = fixed_target(identity)
    if fixed is not None:
        if path != fixed[0]:
            raise ManifestError("artifact path")
        directory = fixed[1] == "directory"
    elif suffix[0] in {"gaming-ini", "gaming-backup", "gaming-config-directory"}:
        if not suffix[1] or USERNAME.fullmatch(suffix[2]) is None:
            raise ManifestError("gaming artifact id")
        ending = {"gaming-ini": "/.config/gamemode.ini", "gaming-backup": "/.config/gamemode.ini.sparehash-bak",
                  "gaming-config-directory": "/.config"}[suffix[0]]
        if not path.endswith(ending):
            raise ManifestError("gaming artifact path")
        directory = suffix[0] == "gaming-config-directory"
    else:
        raise ManifestError("artifact id")
    if (not path.startswith("/") or path.startswith("//") or len(path) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in path)
            or str(PurePosixPath(path)) != path or ".." in PurePosixPath(path).parts):
        raise ManifestError("artifact path")
    if (kind == "directory") != directory:
        raise ManifestError("artifact kind")
    digest = None if record["sha256"] is None else text(record["sha256"])
    if (directory and digest is not None) or (not directory and (digest is None or HEX64.fullmatch(digest) is None)):
        raise ManifestError("artifact sha256")
    return Artifact(identity, path, kind, boolean(record["created"]), integer(record["uid"]),
                    integer(record["gid"]), integer(record["mode"], 0o777), integer(record["device"]),
                    integer(record["inode"]), digest)


def _action(value: Json) -> Action:
    record = mapping(value)
    keys(record, "sequence install_id at operation target state")
    sequence, install_id = integer(record["sequence"]), text(record["install_id"])
    if sequence == 0 or HEX32.fullmatch(install_id) is None:
        raise ManifestError("action identity")
    return Action(sequence, install_id, date(record["at"]), choice(record["operation"], OPERATIONS),
                  text(record["target"]), choice(record["state"], ("pending", "applied", "reverted")))


def _pending_target(target: str) -> str | None:
    fixed = fixed_target(target)
    if fixed is not None:
        return fixed[1]
    if target in {"user:xmrig", "group:xmrig", "group:sparehash"}:
        return target.partition(":")[0]
    parts = target.split(":")
    if len(parts) == 2 and USERNAME.fullmatch(parts[1]) is not None:
        if parts[0] in {"gaming-ini", "gaming-backup"}:
            return "file"
        if parts[0] == "gaming-config-directory":
            return "directory"
    if len(parts) == 3 and parts[0] == "membership" and parts[2] == "sparehash" and USERNAME.fullmatch(parts[1]) is not None:
        return "membership"
    return None


def _installer(value: Json) -> Installer:
    record = mapping(value)
    keys(record, "schema phase identities memberships artifacts actions")
    if integer(record["schema"]) != 1:
        raise ManifestError("installer schema")
    identities = tuple(_identity(item) for item in items(record["identities"]))
    memberships = tuple(_membership(item) for item in items(record["memberships"]))
    artifacts = tuple(_artifact(item) for item in items(record["artifacts"]))
    actions = tuple(_action(item) for item in items(record["actions"]))
    phase: Literal["installing", "ready"] = choice(record["phase"], PHASES)
    unique = ([(item.kind, item.name) for item in identities],
              [(item.user, item.group) for item in memberships],
              [item.id for item in artifacts], [item.path for item in artifacts])
    if any(len(values) != len(set(values)) for values in unique):
        raise ManifestError("duplicate receipt")
    targets: dict[str, str] = {item.id: item.kind for item in artifacts}
    targets.update({f"{item.kind}:{item.name}": item.kind for item in identities})
    targets.update({f"membership:{item.user}:{item.group}": "membership" for item in memberships})
    previous = 0
    for action in actions:
        required = {"create-user": {"user"}, "create-group": {"group"}, "add-membership": {"membership"},
                    "create-directory": {"directory"}, "write-file": {"file"}, "merge-gaming": {"file"},
                    "daemon-reload": {"file"}, "enable-controller": {"file"},
                    "disable-controller": {"file"}, "start-controller": {"file"}}[action.operation]
        target_kind = targets.get(action.target)
        if action.state == "pending" and target_kind is None:
            target_kind = _pending_target(action.target)
        if action.sequence <= previous or target_kind not in required:
            raise ManifestError("action reference or sequence")
        if action.operation in {"enable-controller", "disable-controller", "start-controller", "daemon-reload"} and action.target != "/etc/systemd/system/sparehash.service":
            raise ManifestError("systemd action target")
        if action.operation == "merge-gaming" and not action.target.startswith("gaming-ini:"):
            raise ManifestError("gaming action target")
        previous = action.sequence
        if phase == "ready" and action.state == "pending":
            raise ManifestError("pending ready action")
    return Installer(1, phase, identities, memberships, artifacts, actions)


def parse_manifest(contents: str) -> Manifest | None:
    """Reject malformed metadata, including duplicate keys at every nesting level."""
    try:
        if len(contents.encode("utf-8")) > MAX_METADATA_BYTES:
            return None
        document = mapping(_LOAD_JSON(contents, object_pairs_hook=_unique_object))
        expected = "root versions installer" if "installer" in document else "root versions"
        keys(document, expected)
        if document["root"] != MANIFEST_ROOT:
            return None
        records: list[VersionRecord] = []
        for value in items(document["versions"]):
            record = mapping(value)
            keys(record, "version installed_at source")
            version, source = text(record["version"]), text(record["source"])
            if VERSION_PATTERN.fullmatch(version) is None or SOURCE_PATTERN.fullmatch(source) is None:
                return None
            records.append(VersionRecord(version, date(record["installed_at"]), source))
        if len({item.version for item in records}) != len(records):
            return None
        installer = _installer(document["installer"]) if "installer" in document else None
        return Manifest(MANIFEST_ROOT, tuple(records), installer)
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        return None


def format_manifest(manifest: Manifest) -> bytes:
    """Serialize the full canonical document, refusing drift or size overflow."""
    document = asdict(manifest)
    if manifest.installer is None:
        del document["installer"]
    result = (json.dumps(document, indent=2) + "\n").encode("utf-8")
    if parse_manifest(result.decode("utf-8")) is None:
        raise ManifestError("publication")
    return result
