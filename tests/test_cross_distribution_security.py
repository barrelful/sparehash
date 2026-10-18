# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Cross-module behavioral adversarial security regression.

These tests exercise complete trust and authorization flows across the
descriptor-pinned path layer, installer receipts, binary/managed-current
verification, protected credentials, the hostile import/state environment, and
the controller's gaming event ordering and stop authority. Every scenario runs
against real temp-tree syscall outcomes and injected fake clocks; none touches
the host filesystem or a running controller, and no production fault injection
is performed.
"""

from __future__ import annotations

import grp
import io
import json
import os
import stat
import threading
from collections.abc import Callable
from contextlib import ExitStack, redirect_stderr
from pathlib import Path
from typing import Final
from unittest.mock import patch

import pytest

from sparehash import config as config_module
from sparehash import runtime
from sparehash import xmrig_verify as verify
from sparehash.controller import Controller
from sparehash.controller_io import (
    Action,
    Completion,
    Job,
    Observation,
    Operation,
    Ports,
    SampleId,
    authorize_miner,
)
from sparehash.deployment import DeploymentError, Tree, protected_digest
from sparehash.install_ledger import Ledger
from sparehash.install_manifest import StoreContext, StoreError, locked_store
from sparehash.ipc import Command, Gid, IPCError, Peer, Uid, Verb
from sparehash.state import BlockerState
from sparehash.systemd import TEMPLATES, CollisionError, check_collisions
from sparehash.xmrig_config import new_token, read_token, render_config, write_xmrig_json
from sparehash.xmrig_layout import (
    InstallError,
    current_target,
    ensure_marker,
    open_managed_root,
    verify_binary,
)
from sparehash.xmrig_manifest import Manifest, VersionRecord, format_manifest, parse_manifest
from sparehash.xmrig_paths import MARKER_NAME, PathTrust

MARKER_TEXT: Final = "/opt/xmrig"
VERSION: Final = "xmrig-6.26.0"
MANIFEST: Final = (
    '{"root":"/opt/xmrig","versions":[{"version":"xmrig-6.26.0",'
    '"installed_at":"2026-10-07T00:00:00Z","source":"github:xmrig/xmrig:v6.26.0"}]}'
)
DIGEST: Final = "a" * 64
Scenario = Callable[[Path], None]


def _chmod(path: Path, mode: int) -> Path:
    path.chmod(mode)
    return path


def _dirs(root: Path, *parts: str) -> Path:
    current = root
    for part in parts:
        current = current / part
        current.mkdir()
        os.chmod(current, 0o755)
    return current


def _tree(root: Path) -> Path:
    """Build a conventional protected managed tree under the given root."""
    root.mkdir()
    os.chmod(root, 0o755)
    managed = _dirs(root, "opt", "xmrig")
    _dirs(root, "var", "lib", "sparehash")
    (managed / MARKER_NAME).write_text(MARKER_TEXT, encoding="utf-8")
    os.chmod(managed / MARKER_NAME, 0o600)
    version_dir = _dirs(managed, VERSION)
    binary = version_dir / "xmrig"
    binary.write_bytes(b"#!/bin/sh\nexit 0\n")
    os.chmod(binary, 0o755)
    (managed / "current").symlink_to(VERSION)
    manifest = root / "var/lib/sparehash/install-manifest.json"
    manifest.write_text(MANIFEST, encoding="utf-8")
    os.chmod(manifest, 0o600)
    return root


def _managed_paths(root: Path) -> tuple[PathTrust, Path, Path]:
    trust = PathTrust(root, os.getuid())
    marker = root / "opt/xmrig" / MARKER_NAME
    manifest = root / "var/lib/sparehash/install-manifest.json"
    return trust, marker, manifest


def _protected_ancestor_attack(tmp_path: Path) -> None:
    root = _tree(tmp_path / "case")
    os.chmod(root / "opt", 0o775)  # group-writable ancestor
    tree = Tree(root, os.getuid())
    with pytest.raises(DeploymentError), ExitStack() as stack:
        tree.pin(stack, "/opt/xmrig")


def _traversal_substitution_attack(tmp_path: Path) -> None:
    root = _tree(tmp_path / "case")
    tree = Tree(root, os.getuid())
    with pytest.raises(DeploymentError), ExitStack() as stack:
        tree.pin(stack, "/opt/xmrig/../var/lib/sparehash")


def _foreign_symlink_ancestor_attack(tmp_path: Path) -> None:
    root = _tree(tmp_path / "case")
    outside = tmp_path / "outside"
    outside.mkdir()
    os.chmod(outside, 0o755)
    _ = (root / "opt").rename(root / "opt-real")
    (root / "opt").symlink_to(outside)
    tree = Tree(root, os.getuid())
    with pytest.raises(OSError), ExitStack() as stack:
        tree.pin(stack, "/opt/xmrig")


def _foreign_owner_attack(tmp_path: Path) -> None:
    root = _tree(tmp_path / "case")
    tree = Tree(root, os.getuid() + 1)
    with pytest.raises(DeploymentError), ExitStack() as stack:
        tree.pin(stack, "/opt/xmrig")


def _descriptor_cross_validation_attack(tmp_path: Path) -> None:
    root = _tree(tmp_path / "case")
    trust, marker, manifest = _managed_paths(root)
    wrong_marker = tmp_path / "elsewhere"
    wrong_marker.write_text(MARKER_TEXT, encoding="utf-8")
    os.chmod(wrong_marker, 0o600)
    assert trust.records(root / "opt/xmrig" / VERSION / "xmrig", (wrong_marker, manifest)) is False


def _marker_forged_attack(tmp_path: Path) -> None:
    root = _tree(tmp_path / "case")
    (root / "opt/xmrig" / MARKER_NAME).write_text("/opt/other", encoding="utf-8")
    trust, marker, manifest = _managed_paths(root)
    assert trust.authorized_current(marker, manifest) is None


def _non_managed_current_attack(tmp_path: Path) -> None:
    root = _tree(tmp_path / "case")
    (root / "opt/xmrig" / "current").unlink()
    (root / "opt/xmrig" / "current").symlink_to("xmrig-9.9.9")
    trust, marker, manifest = _managed_paths(root)
    assert trust.authorized_current(marker, manifest) is None


def _detached_current_attack(tmp_path: Path) -> None:
    root = _tree(tmp_path / "case")
    (root / "opt/xmrig" / "current").unlink()
    (root / "opt/xmrig" / "current").symlink_to("missing-version")
    trust, marker, manifest = _managed_paths(root)
    assert trust.authorized_current(marker, manifest) is None


def _traversal_current_attack(tmp_path: Path) -> None:
    root = _tree(tmp_path / "case")
    outside = _dirs(root, "outside")
    (outside / "xmrig").write_bytes(b"#!/bin/sh\nexit 0\n")
    os.chmod(outside / "xmrig", 0o755)
    (root / "opt/xmrig" / "current").unlink()
    (root / "opt/xmrig" / "current").symlink_to("../outside")
    trust, marker, manifest = _managed_paths(root)
    assert trust.authorized_current(marker, manifest) is None


def _binary_foreign_mode_attack(tmp_path: Path) -> None:
    root = _tree(tmp_path / "case")
    os.chmod(root / "opt/xmrig" / VERSION / "xmrig", 0o775)
    with ExitStack() as stack:
        root_fd, _ = open_managed_root(PathTrust(root, os.getuid()), stack, create=False)
        assert root_fd is not None
        with pytest.raises(InstallError):
            verify_binary(root_fd, VERSION, os.getuid())


def _binary_foreign_owner_attack(tmp_path: Path) -> None:
    root = _tree(tmp_path / "case")
    with ExitStack() as stack:
        root_fd, _ = open_managed_root(PathTrust(root, os.getuid()), stack, create=False)
        assert root_fd is not None
        with pytest.raises(InstallError):
            verify_binary(root_fd, VERSION, os.getuid() + 1)


def _binary_digest_drift_attack(tmp_path: Path) -> None:
    root = _tree(tmp_path / "case")
    tree = Tree(root, os.getuid())
    binary = root / "opt/xmrig" / VERSION / "xmrig"
    before = tree.receipt("/opt/xmrig/xmrig-6.26.0/xmrig", True)
    binary.write_bytes(b"#!/bin/sh\nexit 1\n")
    after = tree.receipt("/opt/xmrig/xmrig-6.26.0/xmrig", True)
    assert before.sha256 is not None and after.sha256 is not None
    assert before.sha256 != after.sha256


def _foreign_receipt_artifact_attack(tmp_path: Path) -> None:
    root = _tree(tmp_path / "case")
    tree = Tree(root, os.getuid())
    fifo = root / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(DeploymentError):
        tree.receipt("/fifo", True)


def _nested_lock_attack(tmp_path: Path) -> None:
    # The merged store contract composes same-thread nesting deliberately
    # (task-8's reentrant _HELD descriptor; covered by
    # test_manifest_v2.py::test_no_nested_lock) but must still refuse an
    # independent holder: a second acquisition from another thread is a real
    # cross-boundary attempt and fails closed while the transaction is held.
    root = _tree(tmp_path / "case")
    context = StoreContext(PathTrust(root, os.getuid()), create=True, timeout=0)
    outcome: list[str] = []

    def second_holder() -> None:
        try:
            with locked_store(context):
                outcome.append("entered")
        except StoreError as error:
            outcome.append(f"refused:{error.stage}")

    with locked_store(context):
        worker = threading.Thread(target=second_holder)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert outcome == ["refused:manifest-lock"]


def _checksum_mismatch_attack(tmp_path: Path) -> None:
    with pytest.raises(verify.VerifyError):
        verify.parse_checksum(b"0" * 64 + b"  xmrig-6.26.0.tar.gz\n", "other-name")
    with pytest.raises(verify.VerifyError):
        verify.parse_checksum(b"deadbeef\n", "xmrig-6.26.0.tar.gz")


def _non_loopback_api_attack(tmp_path: Path) -> None:
    from sparehash.xmrig_config import ApiError, summary

    with pytest.raises(ApiError):
        summary("192.168.1.10", 38087, new_token())


def _corrupt_state_file_attack(tmp_path: Path) -> None:
    foreign = tmp_path / "state.json"
    foreign.write_text("{not json", encoding="utf-8")
    with pytest.warns(RuntimeWarning):
        state = BlockerState(foreign, min_dwell_seconds=0)
    assert not state.mining_allowed()
    assert "manual" in state.active()


def _hostile_import_environment_attack(tmp_path: Path) -> None:
    environment = {
        "PYTHONPATH": str(tmp_path / "foreign"),
        "HOME": str(tmp_path / "foreign-home"),
        "SPAREHASH_RUN_DIR": "/untrusted/run",
        "SPAREHASH_HWMON_ROOT": "/untrusted/hwmon",
        "SPAREHASH_STATE_FILE": str(tmp_path / "foreign-state"),
    }
    with (
        patch.dict(os.environ, environment, clear=True),
        patch.object(runtime.os, "geteuid", return_value=0),
        patch.object(
            runtime.config, "load", side_effect=AssertionError("configuration accessed")
        ) as load,
        redirect_stderr(io.StringIO()) as errors,
    ):
        assert runtime.run() == 1
        assert "NOTIFY_SOCKET missing" in errors.getvalue()
        assert all(
            name not in os.environ
            for name in ("SPAREHASH_RUN_DIR", "SPAREHASH_HWMON_ROOT", "SPAREHASH_STATE_FILE")
        )
        load.assert_not_called()


def _hook_gaming_survives_clear_attack(tmp_path: Path) -> None:
    ports = _Ports()
    state = BlockerState(tmp_path / "state.json", ports.clock, 0)
    controller = Controller(state, Ports(ports, ports, ports, ports.clock))
    _drive_running(controller, ports)
    controller.handle(Command(Verb.GAMING_START), Peer(Uid(0), Gid(0)))
    controller.safety("gaming", False)
    assert "gaming" in controller.state.active()


def _gaming_active_between_sample_and_autostart(tmp_path: Path) -> None:
    ports = _Ports()
    state = BlockerState(tmp_path / "state.json", ports.clock, 0)
    controller = Controller(state, Ports(ports, ports, ports, ports.clock))
    _drive_running(controller, ports)
    assert controller.authorize() is True
    controller.safety("gaming", True)
    assert controller.authorize() is False
    assert not controller.state.auto_start_allowed()
    assert ports.trace[-2:] == ["cancel", "stop"]


def _gaming_unknown_blocks_autostart(tmp_path: Path) -> None:
    ports = _Ports()
    state = BlockerState(tmp_path / "state.json", ports.clock, 0)
    controller = Controller(state, Ports(ports, ports, ports, ports.clock))
    _drive_running(controller, ports)
    controller.safety("gaming", None)
    assert "gaming" in controller.state.active()
    assert not controller.state.auto_start_allowed()


def _stale_observation_cannot_authorize(tmp_path: Path) -> None:
    ports = _Ports()
    state = BlockerState(tmp_path / "state.json", ports.clock, 0)
    controller = Controller(state, Ports(ports, ports, ports, ports.clock))
    _drive_running(controller, ports)
    controller.safety("gaming", True)  # invalidates the generation
    controller.safety("gaming", False)  # clear is not enough on its own
    assert controller.authorize() is False


def _stop_supersedes_queued_start(tmp_path: Path) -> None:
    ports = _Ports()
    state = BlockerState(tmp_path / "state.json", ports.clock, 0)
    controller = Controller(state, Ports(ports, ports, ports, ports.clock))
    controller.start()
    ports.now = 1.0
    controller.observe(Observation(controller.sample, ports.now, True, False, False, (), False))
    assert controller.pending is not None and controller.pending.action is Action.STOP
    controller.complete(Completion(controller.pending, True, True))  # empty proof
    assert controller.pending is not None and controller.pending.action is Action.START
    controller.safety("gaming", True)
    assert controller.pending is not None and controller.pending.action is Action.STOP
    assert not controller.launch_permitted
    assert "cancel" in ports.trace


def _authorize_miner_denied_without_controller(tmp_path: Path) -> None:
    with patch("sparehash.controller_io.call", side_effect=IPCError("socket missing")):
        assert authorize_miner() == 1
    with patch("sparehash.controller_io.call", return_value={"ok": False}):
        assert authorize_miner() == 1
    with patch("sparehash.controller_io.call", return_value={"ok": True}):
        assert authorize_miner() == 0


def _miner_unit_requires_authorization(tmp_path: Path) -> None:
    unit = (TEMPLATES / "sparehash-xmrig.service.in").read_text(encoding="utf-8")
    assert "ExecCondition=+/usr/local/bin/sparehash __authorize_miner__" in unit
    assert "ExecStart=/usr/local/bin/sparehash __miner_exec__" in unit
    assert "BindsTo=sparehash.service" in unit
    assert "User=xmrig" in unit
    assert "[Install]" not in unit  # static unit: never enabled/started directly


def _foreign_unit_attack(tmp_path: Path) -> None:
    directory = tmp_path / "units"
    directory.mkdir()
    (directory / "sparehash.service").write_text("#!/bin/sh\necho foreign\n", encoding="utf-8")
    with pytest.raises(CollisionError):
        check_collisions(directory)


def _token_rotation_does_not_invalidate_receipt(tmp_path: Path) -> None:
    token_a, token_b = "a" * 64, "b" * 64
    document = json.dumps(
        {
            "http": {
                "enabled": True,
                "host": "127.0.0.1",
                "port": 38087,
                "access-token": token_a,
                "restricted": True,
            }
        },
        sort_keys=True,
    )
    rotated = document.replace(token_a, token_b)
    assert protected_digest("/etc/sparehash/xmrig.json", document.encode()) == protected_digest(
        "/etc/sparehash/xmrig.json", rotated.encode()
    )


def _xmrig_json_published_0640(tmp_path: Path) -> None:
    destination = tmp_path / "xmrig.json"
    cfg = config_module.Config()
    token = new_token()
    with (
        patch(
            "sparehash.xmrig_config.grp.getgrnam",
            return_value=grp.struct_group(("sparehash", "x", os.getgid(), [])),
        ),
        patch("sparehash.xmrig_config.os.fchown"),
    ):
        write_xmrig_json(cfg, token, destination)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o640
    assert read_token(destination) == token


def _config_saved_restrictively(tmp_path: Path) -> None:
    destination = tmp_path / "config.toml"
    config_module.save(config_module.Config(), destination, warn_overwrite=False)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o644


def _conventional_pinned_tree(tmp_path: Path) -> None:
    root = _tree(tmp_path / "case")
    trust, marker, manifest = _managed_paths(root)
    tree = Tree(root, os.getuid())
    with ExitStack() as stack:
        tree.pin(stack, "/opt/xmrig")
    assert trust.authorized_current(marker, manifest) == root / "opt/xmrig" / VERSION / "xmrig"
    receipt = tree.receipt("/opt/xmrig/xmrig-6.26.0/xmrig", True)
    assert receipt.sha256 is not None and len(receipt.sha256) == 64


def _valid_marker_and_manifest(tmp_path: Path) -> None:
    root = _tree(tmp_path / "case")
    with ExitStack() as stack:
        root_fd, _ = open_managed_root(PathTrust(root, os.getuid()), stack, create=False)
        assert root_fd is not None
        ensure_marker(root_fd, os.getuid())
        assert current_target(root_fd) == VERSION
    parsed = parse_manifest(MANIFEST)
    assert parsed is not None and parsed.names == (VERSION,)


def _manifest_round_trip_preserves_created(tmp_path: Path) -> None:
    from sparehash.xmrig_ledger import Artifact, Installer

    manifest = Manifest(
        "/opt/xmrig",
        (VersionRecord(VERSION, "2026-10-07T00:00:00Z", "github:xmrig/xmrig:v6.26.0"),),
        Installer(
            1,
            "ready",
            (),
            (),
            (
                Artifact(
                    "/etc/sparehash/config.toml",
                    "/etc/sparehash/config.toml",
                    "file",
                    False,
                    0,
                    0,
                    0o644,
                    1,
                    20,
                    DIGEST,
                ),
            ),
            (),
        ),
    )
    reparsed = parse_manifest(format_manifest(manifest).decode("utf-8"))
    assert reparsed is not None and reparsed.installer is not None
    assert reparsed.installer.artifacts[0].created is False


def _unowned_artifact_kept_created_false(tmp_path: Path) -> None:
    from sparehash.xmrig_ledger import Artifact

    root = _tree(tmp_path / "case")
    ledger = Ledger(Tree(root, os.getuid()))
    ledger.initialize()
    sequence = ledger.begin("/etc/sparehash/config.toml", "write-file")
    artifact = Artifact(
        "/etc/sparehash/config.toml",
        "/etc/sparehash/config.toml",
        "file",
        False,
        os.getuid(),
        os.getgid(),
        0o644,
        0,
        1,
        DIGEST,
    )
    ledger.applied(sequence, (artifact,))
    with locked_store(StoreContext(PathTrust(root, os.getuid()), create=False)) as store:
        snapshot = store.read()
    assert snapshot is not None and snapshot.manifest.installer is not None
    assert snapshot.manifest.installer.artifacts[0].created is False


def _rendered_config_loopback_and_restricted(tmp_path: Path) -> None:
    cfg = config_module.Config()
    rendered = render_config(cfg, "c" * 64, Path(cfg.xmrig.binary))
    http = rendered["http"]
    assert http["host"] == "127.0.0.1"
    assert http["restricted"] is True
    assert rendered["randomx"]["rdmsr"] is False and rendered["randomx"]["wrmsr"] is False


class _Ports:
    """Deterministic recording ports for controller scenarios."""

    def __init__(self) -> None:
        self.now = 0.0
        self.trace: list[str] = []
        self.operation: Operation | None = None
        self.notify_fails = False

    def clock(self) -> float:
        return self.now

    def request(self, sample: SampleId, deadline: float) -> None:
        _ = (sample, deadline)

    def submit(self, job: Job) -> None:
        self.trace.append(job.action.value)
        if job.action is Action.START:
            self.operation = job.operation

    def cancel(self) -> None:
        self.trace.append("cancel")
        self.operation = None

    def published(self) -> Operation | None:
        return self.operation

    def bind(self) -> None:
        self.trace.append("bind")

    def ready(self) -> None:
        self.trace.append("ready")
        if self.notify_fails:
            raise OSError("injected notify failure")


def _drive_running(controller: Controller, ports: _Ports) -> None:
    controller.start()
    ports.now = 1.0
    controller.observe(Observation(controller.sample, ports.now, True, False, False, (), False))
    assert controller.pending is not None and controller.pending.action is Action.STOP
    controller.complete(Completion(controller.pending, True, True))
    assert controller.pending is not None and controller.pending.action is Action.START
    controller.complete(Completion(controller.pending, True, False))


POSITIVE: Final[tuple[tuple[str, Scenario], ...]] = (
    ("conventional_pinned_tree", _conventional_pinned_tree),
    ("valid_marker_and_manifest", _valid_marker_and_manifest),
    ("manifest_round_trip_preserves_created", _manifest_round_trip_preserves_created),
    ("unowned_artifact_kept_created_false", _unowned_artifact_kept_created_false),
    ("xmrig_json_published_0640", _xmrig_json_published_0640),
    ("config_saved_restrictively", _config_saved_restrictively),
    ("token_rotation_does_not_invalidate_receipt", _token_rotation_does_not_invalidate_receipt),
    ("rendered_config_loopback_and_restricted", _rendered_config_loopback_and_restricted),
    ("miner_unit_requires_authorization", _miner_unit_requires_authorization),
)

ATTACKS: Final[tuple[tuple[str, Scenario], ...]] = (
    ("traversal_substitution", _traversal_substitution_attack),
    ("protected_ancestor_drift", _protected_ancestor_attack),
    ("foreign_symlink_ancestor", _foreign_symlink_ancestor_attack),
    ("foreign_owner", _foreign_owner_attack),
    ("descriptor_cross_validation", _descriptor_cross_validation_attack),
    ("marker_forged", _marker_forged_attack),
    ("non_managed_current", _non_managed_current_attack),
    ("detached_current", _detached_current_attack),
    ("traversal_current", _traversal_current_attack),
    ("binary_foreign_mode", _binary_foreign_mode_attack),
    ("binary_foreign_owner", _binary_foreign_owner_attack),
    ("binary_digest_drift", _binary_digest_drift_attack),
    ("foreign_receipt_artifact", _foreign_receipt_artifact_attack),
    ("nested_lock", _nested_lock_attack),
    ("checksum_mismatch", _checksum_mismatch_attack),
    ("non_loopback_api", _non_loopback_api_attack),
    ("corrupt_state_file", _corrupt_state_file_attack),
    ("hostile_import_environment", _hostile_import_environment_attack),
    ("foreign_unit", _foreign_unit_attack),
    ("hook_gaming_survives_clear", _hook_gaming_survives_clear_attack),
    ("gaming_active_between_sample_and_autostart", _gaming_active_between_sample_and_autostart),
    ("gaming_unknown_blocks_autostart", _gaming_unknown_blocks_autostart),
    ("stale_observation_cannot_authorize", _stale_observation_cannot_authorize),
    ("stop_supersedes_queued_start", _stop_supersedes_queued_start),
    ("authorize_miner_denied_without_controller", _authorize_miner_denied_without_controller),
)


@pytest.mark.parametrize(
    "scenario",
    [scenario for _, scenario in POSITIVE],
    ids=[name for name, _ in POSITIVE],
)
def test_accepts_protected_end_to_end_flow(scenario: Scenario, tmp_path: Path) -> None:
    scenario(tmp_path)


@pytest.mark.parametrize(
    "scenario",
    [scenario for _, scenario in ATTACKS],
    ids=[name for name, _ in ATTACKS],
)
def test_rejects_cross_boundary_attacks(scenario: Scenario, tmp_path: Path) -> None:
    scenario(tmp_path)
