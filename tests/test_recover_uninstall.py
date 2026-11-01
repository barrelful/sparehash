"""One-off recovery boundaries on private fixtures; never the installed system."""

import hashlib
import importlib
import io
import json
import os
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from sparehash.cli_uninstall import RemovalJournal
from sparehash.deployment import DeploymentError, Tree
from sparehash.install_manifest import StoreContext, locked_store
from tests.xmrig_layout_fixtures import Layout


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path("qa").resolve()))
    return importlib.import_module("recover_uninstall"), importlib.import_module("recovery_stage")


def bundle(stage, *, extra=None):
    files = {"sparehash/__init__.py": b"", "sparehash/__main__.py": b"# public",
             "qa/recover_uninstall.py": b"# recover", "qa/recovery_stage.py": b"# gate"}
    descriptor = {"schema": 1, "commit": "a" * 40, "original_commit": stage.ORIGINAL,
                  "original_files": {}, "original_shims": {},
                  "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    files["bundle.json"] = json.dumps(descriptor).encode()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        if extra:
            archive.addfile(extra)
    blob = stream.getvalue()
    return blob, hashlib.sha256(blob).hexdigest()


@pytest.mark.parametrize("fault", ["digest", "traversal", "symlink", "duplicate", "unexpected", "commit"])
def test_bundle_refuses_untrusted_members_and_identity(modules, fault):
    _, stage = modules
    member = None
    if fault not in ("digest", "commit"):
        member = tarfile.TarInfo({"traversal": "../escape", "symlink": "sparehash/linked.py",
                                  "duplicate": "bundle.json", "unexpected": "wallet.txt"}[fault])
        if fault == "symlink":
            member.type, member.linkname = tarfile.SYMTYPE, "/etc/passwd"
    blob, digest = bundle(stage, extra=member)
    with pytest.raises(ValueError):
        stage.unpack(blob, "0" * 64 if fault == "digest" else digest, "b" * 40 if fault == "commit" else "a" * 40)


def test_verified_bundle_roundtrip(modules):
    _, stage = modules
    blob, digest = bundle(stage)
    descriptor, files = stage.unpack(blob, digest, "a" * 40)
    assert descriptor["original_commit"] == stage.ORIGINAL and len(files) == 4


@pytest.mark.parametrize("fault", ["mode", "symlink", "hardlink", "oversized", "duplicate"])
def test_protected_state_refusal(modules, tmp_path, fault):
    recovery, _ = modules
    path = tmp_path / "journal"
    path.write_text('{"retired":true}')
    path.chmod(0o600)
    if fault == "mode":
        path.chmod(0o644)
    elif fault == "symlink":
        path.rename(tmp_path / "foreign")
        path.symlink_to(tmp_path / "foreign")
    elif fault == "hardlink":
        os.link(path, tmp_path / "foreign")
    elif fault == "oversized":
        path.write_bytes(b" " * 4097)
    else:
        path.write_text('{"retired":true,"retired":false}')
    with pytest.raises((OSError, ValueError, DeploymentError)):
        recovery.private_json(Tree(tmp_path, os.getuid()), "/journal", 0o600, 4096)


def test_interrupted_version_removal_resumes_only_durable_plan(modules):
    recovery, _ = modules
    layout = Layout()
    try:
        layout.build(["xmrig-6.26.0"], current="xmrig-6.26.0")
        tree = Tree(layout.root, os.getuid())
        progress = RemovalJournal(tree)
        progress.phase("leaf-receipts")
        with locked_store(StoreContext(layout.trust)) as store:
            snapshot = store.read()
            assert snapshot is not None
            manifest = snapshot.manifest
            token, plan = recovery.plan_versions(tree, manifest, progress)
            original = os.unlink
            def interrupt(path, *args, **kwargs):
                original(path, *args, **kwargs)
                raise KeyboardInterrupt
            with patch.object(recovery.os, "unlink", side_effect=interrupt), pytest.raises(KeyboardInterrupt):
                recovery.remove_versions(tree, store, manifest, progress, token, plan)
            assert not layout.binary("xmrig-6.26.0").exists()
            resumed = RemovalJournal(tree)
            resumed.load()
            token, plan = recovery.plan_versions(tree, manifest, resumed)
            recovery.remove_versions(tree, store, manifest, resumed, token, plan)
            assert not layout.managed.exists()
            after = store.read()
            assert after is not None and not after.manifest.versions
            recovery.plan_versions(tree, after.manifest, resumed)
    finally:
        layout.cleanup()


def test_version_retry_preserves_new_foreign_file(modules):
    recovery, _ = modules
    layout = Layout()
    try:
        layout.build(["xmrig-6.26.0"], current="xmrig-6.26.0")
        tree = Tree(layout.root, os.getuid())
        progress = RemovalJournal(tree)
        manifest = layout.trust.manifest()
        assert manifest is not None
        token, _ = recovery.plan_versions(tree, manifest, progress)
        progress.record(token)
        foreign = layout.managed / "foreign"
        foreign.write_text("retain")
        foreign.chmod(0o600)
        with pytest.raises(DeploymentError, match="foreign"):
            recovery.plan_versions(tree, manifest, progress)
        assert foreign.read_text() == "retain"
    finally:
        layout.cleanup()


def test_unknown_cgroup_or_active_service_refuses(modules, monkeypatch):
    recovery, _ = modules
    monkeypatch.setattr(recovery, "checked", lambda *_: "LoadState=loaded\nActiveState=inactive\nSubState=dead\nMainPID=0")
    monkeypatch.setattr(recovery, "empty", lambda: False)
    with pytest.raises(DeploymentError, match="unverifiable"):
        recovery.inactive()
    monkeypatch.setattr(recovery, "checked", lambda *_: "LoadState=loaded\nActiveState=active\nSubState=running\nMainPID=123")
    with pytest.raises(DeploymentError, match="inactive"):
        recovery.inactive()
