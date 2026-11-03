"""App publication/recovery on private receipt-owned trees; never host services."""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from sparehash import upgrade
from sparehash.deployment import DeploymentError, Tree
from sparehash.install_ledger import Ledger
from sparehash.lifecycle_lease import ActiveLease, LeaseStore
from sparehash.update_record import BACKUP, JOURNAL, STAGE_PREFIX, load, save
from sparehash.xmrig_ledger import leaf_identity
from tests.install_fixture import capabilities


@pytest.fixture
def installation(tmp_path: Path, monkeypatch):
    # Tree's production paths remain unchanged. Only this private root and
    # process-owned UID/GID are used, even when pytest itself is unprivileged.
    root = tmp_path / "root"
    root.mkdir(mode=0o755)
    tree = Tree(root, os.getuid())
    real_chown = os.fchown
    monkeypatch.setattr(os, "fchown", lambda fd, uid, gid: real_chown(fd, os.getuid(), os.getgid()))
    for name in ("opt/sparehash/src/sparehash", "opt/sparehash/src/systemd", "usr/local/bin",
                 "etc/systemd/system", "etc/sparehash", "var/lib/sparehash"):
        (root / name).mkdir(parents=True, exist_ok=True)
    for path in root.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
    source = tmp_path / "candidate"
    (source / "sparehash").mkdir(parents=True)
    (source / "systemd").mkdir()
    repo = Path(__file__).resolve().parents[1]
    for version, commit, target in (("0.3.0", "a" * 40, tree.path(upgrade.SOURCE)),
                                    ("0.3.1", "b" * 40, source)):
        files = {"__init__.py": f'__version__ = "{version}"\n', "_build.py": f'COMMIT = "{commit}"\n',
                 "__main__.py": "# entrypoint\n", "upgrade.py": "# known good recovery\n",
                 "runtime.py": "# runtime\n", "cli_update.py": "# update\n", "config.py": "# config\n"}
        if version == "0.3.0":
            files["obsolete.py"] = "# old module\n"
        else:
            files["added.py"] = "# new module\n"
        for name, content in files.items():
            (target / "sparehash" / name).write_text(content)
            (target / "sparehash" / name).chmod(0o644)
        for name in upgrade.UNIT_TEMPLATES:
            (target / "systemd" / name).write_bytes((repo / "systemd" / name).read_bytes())
            (target / "systemd" / name).chmod(0o644)
    tree.write(upgrade.LAUNCHER, (b"#!/usr/bin/python3\n# old launcher\n", 0o755, os.getgid()))
    for unit in upgrade.systemd.UNIT_NAMES:
        tree.write(upgrade.UNITS + "/" + unit, (b"# Managed by SpareHash\n# old unit\n", 0o644, os.getgid()))
    for name, data in (("/etc/sparehash/config.toml", b"# config\n"),
                       ("/etc/sparehash/xmrig.json", b'{"secret":"test only"}\n'),
                       ("/etc/sparehash/xmrig.runtime.json", b'{"secret":"test only"}\n'),
                       ("/var/lib/sparehash/state.json", b'{"manual":true}\n')):
        tree.write(name, (data, 0o640, os.getgid()))
    ledger = Ledger(tree)
    ledger.initialize()
    names = [upgrade.SOURCE, upgrade.SOURCE + "/sparehash", upgrade.SOURCE + "/systemd", upgrade.LAUNCHER,
             *(upgrade.UNITS + "/" + n for n in upgrade.systemd.UNIT_NAMES)]
    names += [upgrade.SOURCE + "/" + str(path.relative_to(tree.path(upgrade.SOURCE)))
              for path in tree.path(upgrade.SOURCE).rglob("*") if path.is_file()]
    for name in names:
        identity = leaf_identity(name) or name
        artifact = tree.receipt(name, True, identity=identity)
        ledger.perform((identity, "create-directory" if artifact.kind == "directory" else "write-file"),
                       lambda artifact=artifact: (artifact,))
    ledger.ready()
    state = {"active": False, "boot": "disabled", "starts": 0, "stops": 0}

    def stop():
        state["active"] = False
        state["stops"] += 1

    def ready():
        assert tree.read(JOURNAL) is not None, "startup must remain inhibited during verification"
        state["active"] = True
        state["starts"] += 1

    infra = upgrade.Infrastructure(active=lambda: state["active"], boot=lambda: state["boot"],
                                   stop=stop, ready=ready, proof=lambda: None, reload=lambda: None,
                                   verify_units=lambda path: None, verify_identity=lambda path: None,
                                   validate_config=lambda path: None)
    return tree, source, state, infra


def prepare(installation, *, active=False):
    tree, source, state, infra = installation
    state["active"] = active
    stage = STAGE_PREFIX + "c" * 32
    stage_digest = upgrade.candidate_bundle(tree, stage, source, capabilities())
    with patch.object(upgrade, "acquire", return_value="token"), patch.object(upgrade, "await_ready"):
        record = upgrade.prepare(tree, stage, stage_digest, infra)
    return record


@pytest.mark.parametrize("active", [False, True])
def test_update_preserves_activity_boot_secrets_and_receipts(installation, active) -> None:
    tree, _, state, infra = installation
    preserved = upgrade.preserved(tree)
    record = prepare(installation, active=active)
    assert record.backup_digest and load(tree).phase == "prepared"
    assert upgrade.publish(tree, infra)
    assert state["active"] is active and state["boot"] == "disabled"
    assert upgrade.identity(tree.path(upgrade.SOURCE)) == ("0.3.1", "b" * 40)
    assert not tree.path(upgrade.SOURCE + "/sparehash/obsolete.py").exists()
    assert tree.path(upgrade.SOURCE + "/sparehash/added.py").is_file()
    upgrade.validate_live(tree, upgrade._manifest(tree))
    assert all(a.created for a in upgrade._manifest(tree).installer.artifacts)
    assert upgrade.preserved(tree) == preserved
    assert load(tree) is None and LeaseStore(tree.path("/var/lib/sparehash/lifecycle-lease.json")).load() is None
    assert tree.path(BACKUP).is_dir() and not tree.path(record.stage).exists()
    assert not upgrade.recover(tree, infra), "retained backup must not provide post-success rollback"
    assert upgrade.identity(tree.path(upgrade.SOURCE))[0] == "0.3.1"


def test_ordinary_activation_failure_automatically_restores(installation) -> None:
    tree, _, state, infra = installation
    prepare(installation, active=True)
    real_ready = infra.ready

    def ready():
        if upgrade.identity(tree.path(upgrade.SOURCE))[0] == "0.3.1":
            raise DeploymentError("activation", "signed candidate fails")
        real_ready()

    assert not upgrade.publish(tree, replace(infra, ready=ready))
    assert upgrade.identity(tree.path(upgrade.SOURCE)) == ("0.3.0", "a" * 40)
    assert state["active"] and state["boot"] == "disabled"
    upgrade.validate_live(tree, upgrade._manifest(tree))
    assert load(tree) is None


def test_interrupted_publication_recovers_missing_cli_and_source(installation) -> None:
    tree, _, _, infra = installation
    record = prepare(installation)
    save(tree, record, phase="installing")
    tree.path(upgrade.LAUNCHER).unlink()
    tree.path(upgrade.SOURCE + "/sparehash/__main__.py").unlink()
    # A killed Tree.write may leave a partial protected temporary before rename.
    tree.write(upgrade.SOURCE + "/sparehash/.sparehash-" + "e" * 32, (b"partial candidate", 0o644, os.getgid()))
    tree.write(upgrade.SOURCE + "/sparehash/__init__.py", (b'__version__ = "0.3.1"\n', 0o644, os.getgid()))
    assert upgrade.recover(tree, infra)
    upgrade.validate_live(tree, upgrade._manifest(tree))
    assert upgrade.identity(tree.path(upgrade.SOURCE)) == ("0.3.0", "a" * 40)
    assert not upgrade.recover(tree, infra)
    assert not list(tree.path(upgrade.SOURCE + "/sparehash").glob(".sparehash-*"))


def test_interrupted_restoration_is_repeatable(installation) -> None:
    tree, _, _, infra = installation
    record = prepare(installation)
    save(tree, record, phase="installing")
    original = Tree.write
    failed = False

    def write(self, name, data):
        nonlocal failed
        if name.startswith(upgrade.SOURCE + "/") and not failed:
            failed = True
            raise OSError("write failure")
        return original(self, name, data)

    with patch.object(Tree, "write", write), pytest.raises(OSError, match="write failure"):
        upgrade.recover(tree, infra)
    assert load(tree).phase == "restoring"
    assert upgrade.recover(tree, infra)
    assert upgrade.identity(tree.path(upgrade.SOURCE))[0] == "0.3.0"


def test_committed_failure_never_restores_previous_version(installation) -> None:
    tree, _, _, infra = installation
    prepare(installation)
    real = upgrade.finalise
    with patch.object(upgrade, "finalise", side_effect=OSError("interrupted finalisation")), pytest.raises(OSError):
        upgrade.publish(tree, infra)
    assert load(tree).phase == "committed"
    assert upgrade.identity(tree.path(upgrade.SOURCE))[0] == "0.3.1"
    real(tree, load(tree), infra)
    assert load(tree) is None and upgrade.identity(tree.path(upgrade.SOURCE))[0] == "0.3.1"


def test_corrupt_backup_or_foreign_live_content_keeps_inhibition(installation) -> None:
    tree, _, _, infra = installation
    record = prepare(installation)
    save(tree, record, phase="installing")
    tree.write(BACKUP + "/src/sparehash/upgrade.py", (b"tampered", 0o644, os.getgid()))
    with pytest.raises(DeploymentError, match="inventory"):
        upgrade.recover(tree, infra)
    assert load(tree) is not None
    assert LeaseStore(tree.path("/var/lib/sparehash/lifecycle-lease.json")).load() is not None


def test_unrelated_hold_and_modified_live_source_refuse_before_downtime(installation) -> None:
    tree, source, state, infra = installation
    store = LeaseStore(tree.path("/var/lib/sparehash/lifecycle-lease.json"))
    store.save(ActiveLease("a" * 32, "unrelated", "update"))
    stage = STAGE_PREFIX + "c" * 32
    stamp = upgrade.candidate_bundle(tree, stage, source, capabilities())
    with pytest.raises(DeploymentError, match="unrelated"):
        upgrade.prepare(tree, stage, stamp, infra)
    assert state["stops"] == 0 and load(tree) is None
    store.clear()
    tree.write(upgrade.SOURCE + "/sparehash/obsolete.py", (b"foreign", 0o644, os.getgid()))
    with pytest.raises(DeploymentError, match="modified"):
        upgrade.prepare(tree, stage, stamp, infra)
    assert state["stops"] == 0 and load(tree) is None


def test_journal_corruption_and_missing_hold_inhibit(installation) -> None:
    tree, _, _, _ = installation
    store = LeaseStore(tree.path("/var/lib/sparehash/lifecycle-lease.json"))
    assert not store.update_pending()
    tree.write(JOURNAL, (b"{corrupt", 0o600, os.getgid()))
    assert store.update_pending() and store.load() is None
    with pytest.raises(DeploymentError, match="corrupt"):
        load(tree)


def test_cleanup_resumes_without_deleting_foreign_contents(installation) -> None:
    tree, source, _, _ = installation
    stage = STAGE_PREFIX + "c" * 32
    stamp = upgrade.candidate_bundle(tree, stage, source, capabilities())
    tree.path(stage + "/src/sparehash/__main__.py").unlink()
    foreign = tree.path(stage + "/foreign")
    foreign.write_text("foreign")
    with pytest.raises(DeploymentError, match="foreign"):
        upgrade.discard_bundle(tree, stage, backup=False, expected=stamp)
    assert foreign.read_text() == "foreign"
    foreign.unlink()  # Only withdraw our own test sentinel.
    upgrade.discard_bundle(tree, stage, backup=False, expected=stamp)
    assert not tree.path(stage).exists()


def test_unpaused_state_is_not_created_and_required_credentials_are_not_invented(installation) -> None:
    tree, source, state, infra = installation
    tree.path("/var/lib/sparehash/state.json").unlink()
    prepare(installation)
    assert upgrade.publish(tree, infra)
    assert not tree.path("/var/lib/sparehash/state.json").exists()
    tree.path("/etc/sparehash/xmrig.json").unlink()
    stage = STAGE_PREFIX + "d" * 32
    stamp = upgrade.candidate_bundle(tree, stage, source, capabilities())
    stops = state["stops"]
    with pytest.raises(DeploymentError, match="credentials missing"):
        upgrade.prepare(tree, stage, stamp, infra)
    assert state["stops"] == stops and load(tree) is None


def test_backup_copy_failure_restores_activity_without_partial_backup_authority(installation) -> None:
    tree, _, state, _ = installation
    write = Tree.write
    failed = False

    def fail_once(self, name, payload):
        nonlocal failed
        if name.startswith(BACKUP + "/src/") and not failed:
            failed = True
            raise OSError("backup copy failed")
        return write(self, name, payload)

    with patch.object(Tree, "write", fail_once), pytest.raises(OSError, match="backup copy failed"):
        prepare(installation, active=True)
    assert state["active"] and state["boot"] == "disabled"
    assert load(tree) is None and not tree.path(BACKUP).exists()
    upgrade.validate_live(tree, upgrade._manifest(tree))


def test_fresh_reinstall_drops_removed_application_and_action_receipts(installation) -> None:
    from sparehash.install_manifest import StoreContext, locked_store

    tree, _, _, _ = installation
    ledger = Ledger(tree)
    config = tree.receipt("/etc/sparehash/config.toml", True)
    ledger.perform((config.id, "write-file"), lambda: (config,))
    ledger.ready()
    original = upgrade._manifest(tree)
    for artifact in original.installer.artifacts:
        if upgrade._is_app(artifact) and artifact.kind == "file":
            tree.path(artifact.path).unlink()
    ledger.initialize(fresh=True)
    with locked_store(StoreContext(tree.trust)) as store:
        installer = store.read().manifest.installer
    assert installer.phase == "installing"
    assert installer.artifacts == (config,)
    assert installer.actions == () and installer.identities == ()


def test_fresh_install_cannot_adopt_remaining_application_files(installation) -> None:
    tree, _, _, _ = installation
    before = tree.read("/var/lib/sparehash/install-manifest.json")
    with pytest.raises(DeploymentError, match="remnants"):
        Ledger(tree).initialize(fresh=True)
    assert tree.read("/var/lib/sparehash/install-manifest.json") == before


def test_backup_worker_inherits_operation_lock_and_reserved_identity(installation) -> None:
    import subprocess
    from contextlib import ExitStack

    tree, _, _, _ = installation
    record = prepare(installation)
    with ExitStack() as stack:
        fd = tree.pin(stack, "/opt/sparehash")
        with patch.object(upgrade.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            assert upgrade.run_from_backup(tree, fd) == 0
        assert run.call_args.kwargs["pass_fds"] == (fd,)
        assert run.call_args.args[0][-2:] == (record.operation_id, str(fd))
