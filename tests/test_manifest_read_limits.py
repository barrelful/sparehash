"""Protected-reader regressions on private fixtures, never the installed system."""

from __future__ import annotations

import os
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from sparehash import xmrig_paths
from sparehash.install_manifest import StoreContext, locked_store
from sparehash.xmrig_layout import CollisionError
from sparehash.xmrig_manifest import MAX_METADATA_BYTES_V2, format_manifest, parse_manifest
from sparehash.xmrig_remove import remove_owned_versions
from tests.test_manifest_v2 import _manifest
from tests.xmrig_layout_fixtures import Layout


@pytest.fixture
def large_layout():
    layout = Layout()
    try:
        layout.build(["xmrig-6.26.0"], current="xmrig-6.26.0")
        original = _manifest(8)
        assert original.installer is not None
        action = original.installer.actions[0]
        manifest = replace(original, installer=replace(original.installer, actions=tuple(
            replace(action, sequence=index) for index in range(1, 401))))
        data = format_manifest(manifest)
        assert 64 * 1024 < len(data) < MAX_METADATA_BYTES_V2
        assert parse_manifest(data.decode()) == manifest
        with locked_store(StoreContext(layout.trust)) as store:
            store.write(manifest)
        yield layout, manifest
    finally:
        layout.cleanup()


def test_large_manifest_agrees_across_store_and_protected_readers(large_layout):
    layout, manifest = large_layout
    with locked_store(StoreContext(layout.trust)) as store:
        snapshot = store.read()
        assert snapshot is not None and snapshot.manifest == manifest
    assert layout.trust.manifest() == manifest
    binary = layout.binary("xmrig-6.26.0")
    assert layout.trust.records(binary, (layout.marker, layout.manifest))
    assert layout.trust.authorized_current(layout.marker, layout.manifest) == binary
    with tempfile.TemporaryDirectory() as private:
        result = layout.trust.current_snapshot(Path(private), layout.marker, layout.manifest)
        assert result is not None and result[0].read_bytes() == binary.read_bytes()


def test_old_reader_cap_reproduces_refusal_before_version_mutation(large_layout):
    layout, manifest = large_layout
    before = layout.manifest.read_bytes()
    with patch.object(xmrig_paths, "MAX_METADATA_BYTES_V2", 64 * 1024):
        assert layout.trust.manifest() is None
        with pytest.raises(CollisionError, match="valid protected marker and manifest"):
            remove_owned_versions(layout.trust)
    assert layout.manifest.read_bytes() == before
    assert layout.marker.exists() and layout.binary("xmrig-6.26.0").exists()
    assert layout.trust.manifest() == manifest


def test_large_manifest_authorizes_version_removal_without_dropping_receipts(large_layout):
    layout, manifest = large_layout
    assert remove_owned_versions(layout.trust) == ("xmrig-6.26.0",)
    assert not layout.managed.exists()
    with locked_store(StoreContext(layout.trust)) as store:
        snapshot = store.read()
    assert snapshot is not None and snapshot.manifest == replace(manifest, versions=())


@pytest.mark.parametrize("target", ["marker", "manifest"])
@pytest.mark.parametrize("fault", ["missing", "malformed", "oversized", "symlink", "hardlink", "writable", "fifo", "foreign-owner"])
def test_large_manifest_unsafe_metadata_still_refuses(large_layout, target, fault):
    layout, _ = large_layout
    path = getattr(layout, target)
    trust = layout.trust
    if fault == "missing":
        path.unlink()
    elif fault == "malformed":
        path.write_bytes(b"{}")
    elif fault == "oversized":
        cap = xmrig_paths.MAX_MARKER_BYTES if target == "marker" else MAX_METADATA_BYTES_V2
        # A correct marker padded beyond its own cap must not borrow the manifest cap.
        path.write_bytes((b"/opt/xmrig" if target == "marker" else b"{}") + b" " * cap)
    elif fault in ("symlink", "fifo"):
        path.unlink()
        if fault == "symlink":
            path.symlink_to(layout.binary("xmrig-6.26.0"))
        else:
            os.mkfifo(path)
    elif fault == "hardlink":
        os.link(path, layout.root / "foreign-link")
    elif fault == "writable":
        path.chmod(0o660)
    else:
        trust = replace(trust, uid=os.getuid() + 1)
    assert trust.manifest() is None
    assert not trust.records(layout.binary("xmrig-6.26.0"), (layout.marker, layout.manifest))
    assert trust.authorized_current(layout.marker, layout.manifest) is None
    assert layout.binary("xmrig-6.26.0").exists()


def test_large_manifest_open_inode_cannot_be_replaced_by_valid_path(large_layout):
    layout, _ = large_layout
    valid = layout.manifest.read_bytes()
    layout.manifest.write_bytes(b"{}")
    original_open = os.open
    swapped = False
    def swap(path, flags, *args, **kwargs):
        nonlocal swapped
        fd = original_open(path, flags, *args, **kwargs)
        if path == layout.manifest.name and not swapped:
            swapped = True
            layout.manifest.rename(layout.manifest.with_name("original-invalid"))
            layout.manifest.write_bytes(valid)
            layout.manifest.chmod(0o640)
        return fd
    with patch.object(xmrig_paths.os, "open", side_effect=swap):
        assert layout.trust.manifest() is None
    assert layout.trust.manifest() is not None


def test_large_legacy_schema_does_not_gain_extended_schema_budget(large_layout):
    layout, _ = large_layout
    legacy = b'{"root":"/opt/xmrig","versions":[]}' + b" " * (64 * 1024)
    layout.manifest.write_bytes(legacy)
    assert layout.trust.manifest() is None
