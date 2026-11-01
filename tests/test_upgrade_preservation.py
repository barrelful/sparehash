"""Reinstall/reconfigure and local-upgrade preserve secrets, settings and manual pause.

A deployment change must never rotate the API token for a read failure, reset the
operator's manual pause, or cross an unrelated lifecycle hold. Completion of a
published upgrade removes only the migration journal and the compatible update
hold; ``state.json`` is never read or written. These tests assert preservation
booleans only, never a secret value.
"""

from __future__ import annotations

import json
import os
import pwd
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from sparehash import config, install, ipc, state, systemd, xmrig_config
from sparehash.deployment import DeploymentError, Tree
from sparehash.install_manifest import StoreContext, locked_store
from sparehash.lifecycle_lease import ActiveLease, LeaseStore
from sparehash.migration import (
    CompletionEvidence,
    MigrationError,
    MigrationJournal,
    MigrationPhase,
    MigrationStore,
    complete_upgrade,
)
from sparehash.xmrig_paths import PathTrust

from tests.install_fixture import capabilities

TOKEN = "ab" * 32
MANUAL_STATE = json.dumps({"manual": True, "reason": "operator", "source": "cli"})


def _cfg(*, custom: bool = False, disabled: bool = False) -> config.Config:
    cpu = config.Cpu(mode="custom", custom_json='{"max-threads-hint": 50}') if custom else config.Cpu()
    return config.Config(wallet="4" + "1" * 94, pool=config.Pool(host="127.0.0.1"),
                         cpu=cpu, gaming=config.Gaming(enabled=not disabled),
                         thermal=config.Thermal(enabled=not disabled))


def _seed_installation(root: Path, cfg: config.Config, *, paused: bool) -> tuple[Path, Path]:
    """Create a protected conventional installation; return (config_dir, state_dir)."""
    for name in ("etc/systemd/system", "etc/sparehash", "var/lib/sparehash", "opt/sparehash/src", "usr/local/bin"):
        (root / name).mkdir(parents=True, exist_ok=True)
    for directory in (root, *(path for path in root.rglob("*") if path.is_dir())):
        directory.chmod(0o755)
    config_path = root / "etc/sparehash/config.toml"
    config.save(cfg, config_path, warn_overwrite=False)
    document = json.dumps({"http": {"enabled": True, "host": "127.0.0.1", "port": cfg.api.port,
                                    "access-token": TOKEN}})
    config_dir = root / "etc/sparehash"
    state_dir = root / "var/lib/sparehash"
    (config_dir / "xmrig.json").write_text(document, encoding="utf-8")
    (config_dir / "xmrig.runtime.json").write_text(document, encoding="utf-8")
    if paused:
        (state_dir / "state.json").write_text(MANUAL_STATE, encoding="utf-8")
    for path in config_dir.iterdir():
        path.chmod(0o644)
    for path in state_dir.iterdir():
        path.chmod(0o600)
    return config_dir, state_dir


def _execute_preserving(root: Path, cfg: config.Config, operation_id: str) -> None:
    """Run the real installer entrypoint in reconfigure/upgrade mode under injected seams."""
    tree = Tree(root, os.getuid())
    calls = {"enabled": True, "active": False}
    account = pwd.struct_passwd(("xmrig", "x", 996, os.getgid(), "", "/nonexistent", "/usr/sbin/nologin"))
    original_fchown = os.fchown

    def group(name: str) -> object:
        _ = name
        return type("Group", (), {"gr_gid": os.getgid()})()

    def checked(argv: tuple[str, ...], timeout: float = 10) -> str:
        assert 0 < timeout <= 60
        if argv[1] in ("enable", "disable"):
            calls["enabled"] = argv[1] == "enable"
        if argv[1] == "start":
            calls["active"] = True
        if argv[1] == "is-enabled":
            return "static" if argv[2].endswith("xmrig.service") else ("enabled" if calls["enabled"] else "disabled")
        return ""

    def command(argv: tuple[str, ...], timeout: float = 10) -> subprocess.CompletedProcess[str]:
        _ = timeout
        if argv[1] == "is-enabled":
            return subprocess.CompletedProcess(argv, 0 if calls["enabled"] else 1,
                                               "enabled" if calls["enabled"] else "disabled", "")
        return subprocess.CompletedProcess(argv, 0 if calls["active"] else 3,
                                           "active" if calls["active"] else "inactive", "")

    def choose(candidate: config.Config, caps: object, consent: bool) -> config.Config:
        _ = caps, consent
        return candidate

    def fchown(fd: int, uid: int, gid: int) -> None:
        _ = uid, gid
        original_fchown(fd, os.getuid(), os.getgid())

    with (mock.patch("sparehash.install.pwd.getpwnam", return_value=account),
          mock.patch("sparehash.install.grp.getgrnam", side_effect=group),
          mock.patch("sparehash.install.os.fchown", side_effect=fchown),
          mock.patch.object(install, "choose", side_effect=choose),
          mock.patch.object(install, "isolated", return_value=(0, "XMRig 6.26.0")),
          mock.patch.object(systemd, "verify_units", return_value=""),
          mock.patch.object(xmrig_config, "validate_rendered"),
          mock.patch.object(install, "checked", side_effect=checked),
          mock.patch.object(install, "command", side_effect=command),
          mock.patch.object(ipc, "call", return_value={"ok": True, "ready": True, "blockers": []})):
        install.execute(tree, capabilities(), cfg, interactive=False, preserve=True,
                        operation_id=operation_id, backup_root=root / "var/lib/sparehash/upgrade-backups")


@pytest.mark.parametrize("case", ["paused", "unpaused", "custom", "no-started", "disabled"])
def test_accepts_paused_and_unpaused_migration(tmp_path: Path, case: str) -> None:
    paused = case == "paused"
    cfg = _cfg(custom=case == "custom", disabled=case == "disabled")
    root = tmp_path / case
    config_dir, state_dir = _seed_installation(root, cfg, paused=paused)
    config_before = (config_dir / "config.toml").read_bytes()
    manual_before = (state_dir / "state.json").read_bytes() if paused else None

    _execute_preserving(root, cfg, "op-" + case)

    # The published token is the same secret; the config and manual pause are byte-identical.
    assert xmrig_config.read_token(config_dir / "xmrig.json") == TOKEN
    assert (config_dir / "config.toml").read_bytes() == config_before
    manual_after = (state_dir / "state.json").read_bytes() if paused else None
    assert manual_after == manual_before
    # The pre-publish backup is private, root read-only, and never used as runtime input.
    backup = root / "var/lib/sparehash/upgrade-backups" / ("op-" + case)
    assert backup.stat().st_mode & 0o777 == 0o700
    assert (backup / "xmrig.json").stat().st_mode & 0o777 == 0o400
    if paused:
        assert (backup / "state.json").stat().st_mode & 0o777 == 0o400
    print(f"preserved token=True config=True manual_state={paused} backup_private=True")


@pytest.mark.parametrize("case", ["missing-token", "mismatched-backup", "drifted-manual-state", "calling-resume"])
def test_refuses_secret_loss_or_blocker_cross_clear(tmp_path: Path, case: str) -> None:
    if case == "missing-token":
        config_dir = tmp_path / "etc/sparehash"
        config_dir.mkdir(parents=True)
        (config_dir / "config.toml").write_text("version = 1\n", encoding="utf-8")
        with pytest.raises(DeploymentError):
            _ = install.capture_preservation(config_dir, tmp_path / "var/lib/sparehash",
                                             operation_id="op", backup_root=tmp_path / "bk")
        return

    if case in ("mismatched-backup", "drifted-manual-state"):
        cfg = _cfg()
        root = tmp_path / "root"
        config_dir, state_dir = _seed_installation(root, cfg, paused=case == "drifted-manual-state")
        preservation = install.capture_preservation(config_dir, state_dir,
                                                    operation_id="op", backup_root=root / "bk")
        if case == "mismatched-backup":
            backup_token = root / "bk" / "op" / "xmrig.json"
            os.chmod(backup_token, 0o600)
            backup_token.write_text(json.dumps({"http": {"access-token": "cd" * 32}}), encoding="utf-8")
        else:
            (state_dir / "state.json").write_text(
                json.dumps({"manual": True, "reason": "drifted", "source": "cli"}), encoding="utf-8")
        with pytest.raises(DeploymentError):
            install.verify_preservation(preservation, config_dir, state_dir)
        return

    # calling-resume: state resume clears only the pause; it never completes a migration.
    state_dir = tmp_path / "var/lib/sparehash"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text(MANUAL_STATE, encoding="utf-8")
    (state_dir / "migration.json").write_text('{"phase": "VERIFIED_AWAITING_OPERATOR"}\n', encoding="utf-8")
    hold = ActiveLease("ab" * 16, "op-21", "update")
    store = LeaseStore(state_dir / "lifecycle-lease.json")
    store.save(hold)
    _ = state.BlockerState(state_file=state_dir / "state.json").resume()
    assert not (state_dir / "state.json").exists()
    assert (state_dir / "migration.json").read_text(encoding="utf-8") == '{"phase": "VERIFIED_AWAITING_OPERATOR"}\n'
    assert store.load() == hold


def _state_dir(tmp_path: Path) -> Path:
    state_dir = tmp_path / "var/lib/sparehash"
    state_dir.mkdir(parents=True)
    for directory in (tmp_path / "var", tmp_path / "var/lib", state_dir, tmp_path):
        directory.chmod(0o755)
    return state_dir


def _journal(hold: ActiveLease) -> MigrationJournal:
    return MigrationJournal(
        operation_id=hold.request_id,
        payload_identity="payload-21",
        previous_layout={"opt_root": "/opt/sparehash"},
        new_layout={"opt_root": "/opt/sparehash/dist"},
        phase=MigrationPhase.PREPARED,
        phase_changed_at=1.0,
        manual_state_preserved=True,
        expected_completion_action="complete-upgrade",
        hold_request_id=hold.request_id,
        hold_token=hold.token,
    )


def test_complete_upgrade_refuses_over_live_manual_state(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    hold = ActiveLease("ab" * 16, "op-21", "update")
    LeaseStore(state_dir / "lifecycle-lease.json").save(hold)
    context = StoreContext(PathTrust(tmp_path, os.getuid()), timeout=0)
    with locked_store(context) as store:
        journals = MigrationStore(store)
        journals.write(_journal(hold))
        for phase in (MigrationPhase.HELD, MigrationPhase.STOPPED_EMPTY, MigrationPhase.STAGED,
                      MigrationPhase.PUBLISHED, MigrationPhase.VERIFIED_AWAITING_OPERATOR):
            _ = journals.advance(phase)
        (state_dir / "state.json").write_text(MANUAL_STATE, encoding="utf-8")
        evidence = CompletionEvidence(payload_identity="payload-21", empty_cgroup=True,
                                      guards_clear=True, manual_state_preserved=True)
        with pytest.raises(MigrationError):
            _ = complete_upgrade("op-21", store, evidence=evidence, state_path=state_dir / "state.json")
        # A preserved manual pause is a valid inhibited state: completion never clears it.
        assert complete_upgrade("op-21", store, evidence=evidence) is MigrationPhase.COMPLETE
    assert not (state_dir / "migration.json").exists()
    assert not (state_dir / "lifecycle-lease.json").exists()
    assert (state_dir / "state.json").read_text(encoding="utf-8") == MANUAL_STATE
