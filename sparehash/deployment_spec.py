# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Protected noninteractive runtime deployment description (schema 1).

``/etc/sparehash/deployment.json`` is the immutable, root-owned, no-follow
contract between a deployer and the runtime. It names the deployment owner, the
finite physical layout, the payload identity and the validated runtime paths and
integrity references. It contains no secrets and never grants imperative removal
authority: the manual installer owns its receipts, while an externally
provisioned test deployment supplies a root-controlled description only.

The module is pure standard library and performs no installer work: it never
imports, executes or mutates the installer, wizard or gaming-install modules, it
never creates :func:`path_for`, and validation only inspects synthetic trees. A
native package that supplies this document directly is a compatibility target,
not a package backend implemented here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Final, Literal, cast

Schema = Literal[1]
DeploymentKind = Literal["manual", "external_provision", "native_package"]

SCHEMA: Final[Schema] = 1
KINDS: Final = frozenset({"manual", "external_provision", "native_package"})
MAX_BYTES: Final = 1024 * 1024
MAX_OWNER_BYTES: Final = 256
MAX_ENTRIES: Final = 64
SOURCE_DIRS: Final = ("sparehash", "systemd")

OWNER: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}")
VERSION: Final = re.compile(r"(?:\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?|dev-[A-Za-z0-9._-]{1,16})")
DIGEST: Final = re.compile(r"[0-9a-f]{64}")
ENTRY: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
CURRENT_LINK: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

LAYOUT_OPT_ROOTS: Final = frozenset({"/opt", "/var/opt"})
LAYOUT_LOCAL_ROOTS: Final = frozenset({"/usr/local", "/var/usrlocal"})

# Finite logical prefixes a runtime path may name (B4/B5); arbitrary
# destinations such as /tmp/... are refused before any I/O.
ALLOWED_LOGICAL: Final = (
    ("opt",),
    ("usr", "local"),
    ("etc", "sparehash"),
    ("etc", "systemd", "system"),
    ("var", "lib", "sparehash"),
    ("var", "opt"),
    ("var", "usrlocal"),
    ("run", "sparehash"),
    ("run", "sparehash-miner"),
)
# Executable/credential-bearing entries may only live under protected roots so a
# caller-controlled description cannot select a privileged binary or secret path.
PROTECTED_LOGICAL: Final = (("opt",), ("usr", "local"), ("var", "opt"), ("var", "usrlocal"))
SENSITIVE: Final = re.compile(r"binary|exec|credential|secret|token")

# The only symlinks that may appear inside a validated runtime path: the finite
# OS redirects (B4) and the single-component XMRig ``current`` version link.
REDIRECTS: Final[dict[tuple[str, ...], frozenset[str]]] = {
    ("opt",): frozenset({"var/opt", "/var/opt"}),
    ("usr", "local"): frozenset({"../var/usrlocal", "/var/usrlocal"}),
}

DEFAULT_ROOT: Final = Path("/")


class DeploymentSpecError(ValueError):
    """Malformed, unsafe or mismatched deployment description; fail closed."""

    field: str

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        super().__init__(f"deployment description {field}: {detail}")


@dataclass(frozen=True, slots=True)
class DeploymentSpec:
    """Immutable schema-1 runtime/deployer contract; every field is required."""

    owner: str
    kind: DeploymentKind
    layout: dict[str, str]
    payload_version: str
    inventory_digest: str
    runtime_paths: dict[str, Path]
    integrity_refs: dict[str, str]
    capabilities: dict[str, bool]
    schema: Schema = SCHEMA


def path_for(root: Path = DEFAULT_ROOT) -> Path:
    """Return ``<root>/etc/sparehash/deployment.json`` without creating it."""
    return root / "etc" / "sparehash" / "deployment.json"


def _matches(parts: tuple[str, ...], prefixes: tuple[tuple[str, ...], ...]) -> bool:
    return any(parts[:len(prefix)] == prefix for prefix in prefixes)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inventory_digest(root: Path) -> str:
    """SHA-256 of the sorted, bounded ``sparehash/`` and ``systemd/`` listing.

    Each entry binds its relative path to the file content digest so a changed,
    added or removed payload byte invalidates the description. Source symlinks
    are refused rather than followed.
    """
    entries: list[str] = []
    for name in SOURCE_DIRS:
        base = root / name
        if not base.is_dir():
            raise DeploymentSpecError("inventory_digest", f"missing source directory {name}")
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames.sort()
            for filename in sorted(filenames):
                path = Path(dirpath) / filename
                if path.is_symlink():
                    raise DeploymentSpecError("inventory_digest", "source symlink is forbidden")
                if len(entries) >= MAX_ENTRIES * 64:
                    raise DeploymentSpecError("inventory_digest", "source listing is oversized")
                rel = path.relative_to(root).as_posix()
                entries.append(f"{rel}\0{_sha256(path.read_bytes())}\n")
    listing = "".join(sorted(entries)).encode("utf-8")
    return _sha256(listing)


def _check_mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise DeploymentSpecError(field, "expected an object")
    return cast("dict[str, object]", value)


def _check_owner(owner: object) -> str:
    if not isinstance(owner, str) or not owner:
        raise DeploymentSpecError("owner", "owner must be a non-empty string")
    if len(owner.encode("utf-8")) > MAX_OWNER_BYTES:
        raise DeploymentSpecError("owner", "owner exceeds 256 bytes")
    if not OWNER.fullmatch(owner):
        raise DeploymentSpecError("owner", "owner must be a bounded root identifier")
    return owner


def _check_kind(kind: object) -> DeploymentKind:
    if not isinstance(kind, str) or kind not in KINDS:
        raise DeploymentSpecError("kind", "unknown deployment kind")
    return cast("DeploymentKind", kind)


def _check_version(version: object) -> str:
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise DeploymentSpecError("payload_version", "unparseable payload version")
    return version


def _check_digest(digest: object, field: str) -> str:
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise DeploymentSpecError(field, "expected a lowercase sha256 digest")
    return digest


def _check_layout(layout: object) -> dict[str, str]:
    mapping = _check_mapping(layout, "layout")
    if set(mapping) != {"opt_root", "local_root"}:
        raise DeploymentSpecError("layout", "layout must be exactly opt_root/local_root")
    option = mapping["opt_root"]
    local = mapping["local_root"]
    if not isinstance(option, str) or option not in LAYOUT_OPT_ROOTS:
        raise DeploymentSpecError("layout", "opt_root is not a finite physical root")
    if not isinstance(local, str) or local not in LAYOUT_LOCAL_ROOTS:
        raise DeploymentSpecError("layout", "local_root is not a finite physical root")
    return {"opt_root": option, "local_root": local}


def _check_capabilities(capabilities: object) -> dict[str, bool]:
    mapping = _check_mapping(capabilities, "capabilities")
    if len(mapping) > MAX_ENTRIES:
        raise DeploymentSpecError("capabilities", "too many capability entries")
    checked: dict[str, bool] = {}
    for key, value in mapping.items():
        if not ENTRY.fullmatch(key):
            raise DeploymentSpecError("capabilities", "invalid capability key")
        if not isinstance(value, bool):
            raise DeploymentSpecError("capabilities", "capability values must be booleans")
        checked[key] = value
    return checked


def _check_runtime_paths(runtime_paths: object) -> dict[str, Path]:
    mapping = _check_mapping(runtime_paths, "runtime_paths")
    if not mapping or len(mapping) > MAX_ENTRIES:
        raise DeploymentSpecError("runtime_paths", "expected 1..64 runtime paths")
    checked: dict[str, Path] = {}
    for key, value in mapping.items():
        if not ENTRY.fullmatch(key):
            raise DeploymentSpecError("runtime_paths", "invalid runtime path key")
        if not isinstance(value, Path) or not value.is_absolute():
            raise DeploymentSpecError("runtime_paths", f"{key} must be an absolute Path")
        if ".." in value.parts:
            raise DeploymentSpecError("runtime_paths", f"{key} contains traversal")
        parts = value.parts[1:]
        if not _matches(parts, ALLOWED_LOGICAL):
            raise DeploymentSpecError("runtime_paths", f"{key} names an arbitrary destination")
        if SENSITIVE.search(key) and not _matches(parts, PROTECTED_LOGICAL):
            raise DeploymentSpecError("runtime_paths", f"{key} escapes protected prefixes")
        checked[key] = value
    return checked


def _check_integrity_refs(integrity_refs: object, runtime_paths: dict[str, Path]) -> dict[str, str]:
    mapping = _check_mapping(integrity_refs, "integrity_refs")
    if len(mapping) > MAX_ENTRIES:
        raise DeploymentSpecError("integrity_refs", "too many integrity references")
    checked: dict[str, str] = {}
    for key, value in mapping.items():
        if not ENTRY.fullmatch(key):
            raise DeploymentSpecError("integrity_refs", "invalid integrity key")
        if key not in runtime_paths:
            raise DeploymentSpecError("integrity_refs", f"{key} does not name a runtime path")
        if not isinstance(value, str) or not DIGEST.fullmatch(value):
            raise DeploymentSpecError("integrity_refs", "expected a lowercase sha256 digest")
        checked[key] = value
    return checked


def _checked(spec: DeploymentSpec) -> DeploymentSpec:
    """Re-validate every field of a constructed spec; never trust caller input."""
    owner = _check_owner(spec.owner)
    kind = _check_kind(spec.kind)
    layout = _check_layout(spec.layout)
    version = _check_version(spec.payload_version)
    digest = _check_digest(spec.inventory_digest, "inventory_digest")
    runtime_paths = _check_runtime_paths(spec.runtime_paths)
    integrity_refs = _check_integrity_refs(spec.integrity_refs, runtime_paths)
    capabilities = _check_capabilities(spec.capabilities)
    return DeploymentSpec(owner, kind, layout, version, digest, runtime_paths,
                          integrity_refs, capabilities, SCHEMA)


def _trusted_symlink(prefix: tuple[str, ...], target: str) -> bool:
    allowed = REDIRECTS.get(prefix)
    if allowed is not None and target in allowed:
        return True
    return prefix[-1:] == ("current",) and "/" not in target and CURRENT_LINK.fullmatch(target) is not None


def _check_symlinks(root: Path, logical: Path) -> None:
    """Refuse a symlink under root that is not a finite trusted redirect."""
    current = root
    for index, part in enumerate(logical.parts[1:]):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return
        except OSError as error:
            raise DeploymentSpecError("runtime_paths", f"unreadable path {logical}") from error
        if stat.S_ISLNK(info.st_mode):
            target = os.readlink(current)
            if not _trusted_symlink(logical.parts[1:index + 1], target):
                raise DeploymentSpecError("runtime_paths", f"untrusted symlink in {logical}")


def validate(spec: DeploymentSpec, root: Path) -> None:
    """Validate ownership, layout, payload identity and pinned runtime paths.

    ``root`` anchors both the ``sparehash/``/``systemd/`` source listing and the
    physical runtime tree. Raises :class:`DeploymentSpecError` on any drift so a
    startup consumer can inhibit rather than repair.
    """
    checked = _checked(spec)
    current = inventory_digest(root)
    if checked.inventory_digest != current:
        raise DeploymentSpecError("inventory_digest", "payload listing has drifted")
    for path in checked.runtime_paths.values():
        _check_symlinks(root, path)


def render(spec: DeploymentSpec) -> bytes:
    """Serialize a deterministic schema-1 JSON document (bounded to 1 MiB)."""
    checked = _checked(spec)
    document: dict[str, object] = {
        "schema": checked.schema,
        "owner": checked.owner,
        "kind": checked.kind,
        "layout": checked.layout,
        "payload_version": checked.payload_version,
        "inventory_digest": checked.inventory_digest,
        "runtime_paths": {key: str(value) for key, value in checked.runtime_paths.items()},
        "integrity_refs": checked.integrity_refs,
        "capabilities": checked.capabilities,
    }
    data = json.dumps(document, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    if len(data) > MAX_BYTES:
        raise DeploymentSpecError("document", "rendered description is oversized")
    return data


def _string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise DeploymentSpecError(field, "expected a string")
    return value


def parse(data: bytes | bytearray) -> DeploymentSpec:
    """Inverse of :func:`render`; strict, bounded and side-effect free."""
    if len(data) > MAX_BYTES:
        raise DeploymentSpecError("document", "description is oversized")
    try:
        raw: object = cast("object", json.loads(bytes(data)))
    except (ValueError, UnicodeDecodeError) as error:
        raise DeploymentSpecError("document", "malformed json") from error
    document = _check_mapping(raw, "document")
    expected = {field.name for field in fields(DeploymentSpec)}
    if set(document) != expected:
        raise DeploymentSpecError("document", "foreign fields in description")
    if document["schema"] != SCHEMA or isinstance(document["schema"], bool):
        raise DeploymentSpecError("schema", "unsupported schema")
    owner = document["owner"]
    if not isinstance(owner, str):
        raise DeploymentSpecError("owner", "owner must be a string")
    if len(owner.encode("utf-8")) > MAX_OWNER_BYTES:
        raise DeploymentSpecError("owner", "owner exceeds 256 bytes")
    runtime = _check_mapping(document["runtime_paths"], "runtime_paths")
    runtime_paths = {key: Path(_string(value, "runtime_paths"))
                     for key, value in runtime.items()}
    refs = _check_mapping(document["integrity_refs"], "integrity_refs")
    integrity_refs = {key: _string(value, "integrity_refs") for key, value in refs.items()}
    caps = _check_mapping(document["capabilities"], "capabilities")
    capabilities = cast("dict[str, bool]", dict(caps))
    layout = _check_mapping(document["layout"], "layout")
    spec = DeploymentSpec(
        owner=owner,
        kind=_check_kind(document["kind"]),
        layout={key: _string(value, "layout") for key, value in layout.items()},
        payload_version=_string(document["payload_version"], "payload_version"),
        inventory_digest=_string(document["inventory_digest"], "inventory_digest"),
        runtime_paths=runtime_paths,
        integrity_refs=integrity_refs,
        capabilities=capabilities,
    )
    return _checked(spec)


def load(root: Path = DEFAULT_ROOT, *, uid: int = 0) -> DeploymentSpec:
    """Read and parse the protected description; no-follow, root-owned, 0644.

    ``uid`` is injectable so synthetic trees can model ownership without root.
    """
    path = path_for(root)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise DeploymentSpecError("file", "description is missing, symlinked or unreadable") from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise DeploymentSpecError("file", "description is not a regular file")
        if info.st_uid != uid:
            raise DeploymentSpecError("file", "description is not owned by the deployer")
        if stat.S_IMODE(info.st_mode) != 0o644:
            raise DeploymentSpecError("file", "description must be mode 0644")
        if info.st_size > MAX_BYTES:
            raise DeploymentSpecError("file", "description is oversized")
        chunks: list[bytes] = []
        remaining = MAX_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(descriptor)
    return parse(b"".join(chunks))


def with_kind(spec: DeploymentSpec, kind: DeploymentKind) -> DeploymentSpec:
    """Return a copy with a different kind; rendering still re-validates."""
    return replace(spec, kind=kind)
