"""Schema-2 layout-aware ownership metadata: round trip, refusal, locking, translation."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from sparehash.deployment import DeploymentError, Tree
from sparehash.install_ledger import Ledger
from sparehash.install_manifest import StoreContext, StoreError, locked_store
from sparehash.migration import transform_v1_to_v2_artifact
from sparehash.xmrig_ledger import (
    PACKAGE_DIR,
    Action,
    Artifact,
    GroupIdentity,
    Installer,
    ManifestError,
    Membership,
    UserIdentity,
)
from sparehash.xmrig_manifest import (
    MANIFEST_ROOT,
    Manifest,
    VersionRecord,
    format_manifest,
    inventory_digest,
    parse_manifest,
)
from tests.xmrig_layout_fixtures import DATE, canonical_manifest, source_for

VERSION = "xmrig-6.26.0"
INSTALL_ID = "b" * 32
CONFIG = "/etc/sparehash/config.toml"


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _layout(opt: str = "/opt", local: str = "/usr/local") -> dict[str, str]:
    return {"opt_root": opt, "local_root": local}


def _listing(count: int = 128) -> list[tuple[str, str]]:
    package = [(f"sparehash/m{index:03d}.py", _sha(f"m{index}")) for index in range(count - 8)]
    templates = [(f"systemd/u{index:02d}.in", _sha(f"u{index}")) for index in range(8)]
    return package + templates


def _installer(count: int, *, phase: str = "installing", state: str = "applied") -> Installer:
    artifacts = tuple(
        Artifact(f"package-file:m{index:03d}.py", f"{PACKAGE_DIR}/m{index:03d}.py", "file", True,
                 0, 0, 0o644, 1, 100 + index, "a" * 64) for index in range(count))
    config = Artifact(CONFIG, CONFIG, "file", True, 0, 0, 0o644, 1, 900, "a" * 64)
    actions = (Action(1, INSTALL_ID, DATE, "write-file", CONFIG, state),)
    return Installer(2, phase, (UserIdentity("user", "xmrig", 996, 996, False),
                                GroupIdentity("group", "sparehash", 983, True)),
                     (Membership("osint", 1000, "sparehash", 983, True),), (*artifacts, config), actions)


def _manifest(count: int = 128, *, layout: dict[str, str] | None = None,
              digest: str | None = None, installer: Installer | None = None) -> Manifest:
    listing = _listing(count)
    return Manifest(MANIFEST_ROOT, (VersionRecord(VERSION, DATE, source_for(VERSION)),),
                    installer if installer is not None else _installer(count), schema=2,
                    layout=_layout() if layout is None else layout,
                    payload_version="0.2.0",
                    inventory_digest=inventory_digest(listing) if digest is None else digest)


def _context(root: Path) -> StoreContext:
    from sparehash.xmrig_paths import PathTrust
    return StoreContext(PathTrust(root, os.getuid()), timeout=0, create=True)


def test_accepts_legacy_and_layout_receipts(tmp_path: Path) -> None:
    listing = _listing(128)
    context = _context(tmp_path)

    legacy = Manifest(MANIFEST_ROOT, (VersionRecord(VERSION, DATE, source_for(VERSION)),),
                      Installer(1, "installing", (), (), (), ()))
    with locked_store(context) as store:
        store.write(legacy)
    with locked_store(context) as store:
        snapshot = store.read()
        assert snapshot is not None and snapshot.manifest.schema == 1
        assert snapshot.manifest == legacy

    assert parse_manifest(canonical_manifest([VERSION])) is not None
    assert parse_manifest(canonical_manifest([VERSION])).schema == 1  # type: ignore[union-attr]

    manifest = _manifest(128)
    with locked_store(context) as store:
        store.write(manifest)
    with locked_store(context) as store:
        snapshot = store.read()
        assert snapshot is not None and snapshot.manifest.schema == 2
        assert snapshot.manifest == manifest
        assert snapshot.manifest.layout == _layout()
        assert snapshot.manifest.inventory_digest == inventory_digest(listing)

    published = format_manifest(manifest)
    parsed = parse_manifest(published.decode("utf-8"), inventory=listing)
    assert parsed == manifest
    assert parsed is not None and parsed.inventory_digest == manifest.inventory_digest

    forged = replace(manifest, inventory_digest=_sha("forged"))
    assert parse_manifest(format_manifest(forged).decode("utf-8"), inventory=listing) is None

    with pytest.raises(ManifestError):
        _ = format_manifest(manifest, schema=1)


def test_rejects_forged_or_overbudget_receipts(tmp_path: Path) -> None:
    context = _context(tmp_path)
    good = _manifest(4)

    with locked_store(context) as store:
        store.write(good)
    published = (tmp_path / "var/lib/sparehash/install-manifest.json").read_bytes()

    forged_path = replace(good, installer=replace(good.installer,  # type: ignore[arg-type]
                                                  artifacts=(Artifact(CONFIG, "/tmp/evil", "file", True, 0, 0, 0o644, 1, 2, "a" * 64),)))
    with pytest.raises(ManifestError):
        _ = format_manifest(forged_path)

    forged_kind = replace(good, installer=replace(good.installer,  # type: ignore[arg-type]
                                                  artifacts=(Artifact(CONFIG, CONFIG, "directory", True, 0, 0, 0o755, 1, 2, None),)))
    with pytest.raises(ManifestError):
        _ = format_manifest(forged_kind)

    pending_ready = _manifest(4, installer=replace(_installer(4, phase="ready"), actions=(
        replace(_installer(4).actions[0], state="pending"),)))
    with pytest.raises(ManifestError):
        _ = format_manifest(pending_ready)

    for document in (
        json.dumps({"root": MANIFEST_ROOT, "versions": [], "installer": {
            "schema": 3, "phase": "installing", "identities": [], "memberships": [],
            "artifacts": [], "actions": []}}),
        json.dumps({"root": MANIFEST_ROOT, "versions": [], "payload_version": "0.2.0",
                    "inventory_digest": None, "layout": _layout(), "installer": {
            "schema": 2, "phase": "ready", "identities": [], "memberships": [],
            "artifacts": [], "actions": [{"sequence": 1, "install_id": INSTALL_ID, "at": DATE,
                                          "operation": "write-file", "target": CONFIG, "state": "pending"}]}}),
        json.dumps({"root": MANIFEST_ROOT, "versions": [], "schema": 2, "layout": _layout(),
                    "payload_version": "0.2.0", "inventory_digest": None,
                    "installer": {"schema": 2, "phase": "installing", "identities": [], "memberships": [],
                                  "artifacts": [], "actions": []}}),
    ):
        assert parse_manifest(document) is None

    with pytest.raises(ManifestError):
        _ = format_manifest(good, schema=3)

    os.link(tmp_path / "var/lib/sparehash/install-manifest.json", tmp_path / "linked")
    with locked_store(context) as store, pytest.raises(StoreError):
        _ = store.read()
    os.unlink(tmp_path / "linked")
    assert (tmp_path / "var/lib/sparehash/install-manifest.json").read_bytes() == published

    with locked_store(context) as outer:
        with locked_store(context) as inner:
            inner.write(good)
            assert inner.read() is not None
        assert outer.read() is not None

    tree = Tree(tmp_path, os.getuid())
    ledger = Ledger(tree)
    ledger.initialize()
    near, count = good, 0
    low, high = 0, 4000
    while low + 1 < high:
        middle = (low + high) // 2
        actions = tuple(replace(_installer(4).actions[0], sequence=index) for index in range(1, middle + 1))
        candidate = replace(good, installer=replace(_installer(4), actions=actions))
        try:
            _ = format_manifest(candidate)
        except ManifestError:
            high = middle
        else:
            low, near, count = middle, candidate, middle
    with locked_store(context) as store:
        store.write(near)
    before = (tmp_path / "var/lib/sparehash/install-manifest.json").read_bytes()
    with pytest.raises(DeploymentError) as refusal:
        _ = ledger.begin(CONFIG, "write-file")
    assert refusal.value.stage == "manifest-budget"
    assert (tmp_path / "var/lib/sparehash/install-manifest.json").read_bytes() == before
    assert count > 0


def test_no_nested_lock(tmp_path: Path) -> None:
    context = _context(tmp_path)
    manifest = _manifest(4)
    with locked_store(context) as outer:
        with locked_store(context) as inner:
            inner.write(manifest)
            assert inner.read() is not None
        assert outer.read() is not None
    with locked_store(context) as reopened:
        assert reopened.read() is not None


def test_translation_preserves_unowned() -> None:
    unowned = {"id": "package-file:alpha.py", "path": PACKAGE_DIR + "/alpha.py", "kind": "file",
               "created": False, "uid": 0, "gid": 0, "mode": 0o644, "device": 1, "inode": 2, "sha256": "a" * 64}
    record = transform_v1_to_v2_artifact(unowned, _layout("/var/opt", "/var/usrlocal"))
    assert record["created"] is False
    assert record["reason"] == "unowned"
    assert record["path"] == "/var/opt/sparehash/src/sparehash/alpha.py"

    digestless = {**unowned, "created": True, "sha256": None}
    record = transform_v1_to_v2_artifact(digestless, _layout())
    assert record["created"] is False and record["reason"] == "unowned"

    owned = {**unowned, "created": True}
    record = transform_v1_to_v2_artifact(owned, _layout())
    assert record["created"] is True and record["reason"] is None
    assert record["path"] == PACKAGE_DIR + "/alpha.py"

    with pytest.raises(ManifestError):
        _ = transform_v1_to_v2_artifact(unowned, _layout("/etc", "/usr/local"))
