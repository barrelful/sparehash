"""Inhibited local-release upgrade: staged publication, preserved choices, deterministic recovery.

Historical internal transaction regressions, not supported public CLI workflows.
The happy path exercises the retained upgrade/completion functions directly;
entrypoints on a private receipt-owned tree. The failure matrix injects a fault at
each durable publication boundary and proves the compatible hold and private
staging survive, then that recovery either rolls forward or refuses a staging it
cannot trust. No host mutation: roots, services and empty proof are injected.
"""

from __future__ import annotations

import errno
import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from sparehash import install, upgrade
from sparehash.deployment import Tree
from sparehash.install_ledger import Ledger
from sparehash.xmrig_manifest import parse_manifest

OPID = "op-upgrade-1"
EXPECTED_LEAVES = frozenset((
    "sparehash/__init__.py",
    "sparehash/alpha.py",
    "sparehash/beta.py",
    "systemd/sparehash.service.in",
    "systemd/sparehash-xmrig.service.in",
))


class Fixture:
    """A private, non-root v0.1.1 installation plus a selected local release."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.tree = Tree(root, os.getuid())
        self.source = root / "release"
        self.manual = b'{"manual": true, "reason": "maintenance", "source": "operator"}\n'

    def write(self, relative: str, data: bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(data)
        path.chmod(0o644)
        return path

    def write_source(self, relative: str, data: bytes) -> None:
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(data)
        path.chmod(0o644)

    def build(self) -> None:
        for name in ("opt/sparehash/src/sparehash", "opt/sparehash/src/systemd", "usr/local/bin",
                     "etc/systemd/system", "etc/sparehash", "var/lib/sparehash"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o755)
        for path in self.root.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)
        self.write("opt/sparehash/src/sparehash/__init__.py", b"")
        self.write("opt/sparehash/src/sparehash/alpha.py", b"OLD = 1\n")
        self.write("opt/sparehash/src/systemd/sparehash.service.in", b"old-controller\n")
        self.write("opt/sparehash/src/systemd/sparehash-xmrig.service.in", b"old-miner\n")
        self.write("usr/local/bin/sparehash", b"#!/usr/bin/python3\n")
        self.write("etc/systemd/system/sparehash.service", b"# Managed by SpareHash\nold\n")
        self.write("etc/systemd/system/sparehash-xmrig.service", b"# Managed by SpareHash\nold\n")
        self.write("etc/sparehash/config.toml", b'version = 1\nwallet = ""\n')
        self.write("var/lib/sparehash/state.json", self.manual)
        self.write_source("sparehash/__init__.py", b"")
        self.write_source("sparehash/alpha.py", b"NEW = 2\n")
        self.write_source("sparehash/beta.py", b"BETA = 3\n")
        self.write_source("systemd/sparehash.service.in", b"new-controller\n")
        self.write_source("systemd/sparehash-xmrig.service.in", b"new-miner\n")
        ledger = Ledger(self.tree)
        ledger.initialize()
        ledger.applied(0, (self.tree.receipt("/opt/sparehash/src", True),))

    def staging(self, operation_id: str) -> Path:
        return self.root / "opt/sparehash" / (".upgrade-" + operation_id)

    def target(self) -> Path:
        return self.root / "opt/sparehash/src"

    def state(self) -> Path:
        return self.root / "var/lib/sparehash/state.json"

    def lease(self) -> Path:
        return self.root / "var/lib/sparehash/lifecycle-lease.json"

    def journal(self) -> Path:
        return self.root / "var/lib/sparehash/migration.json"

    def leaves(self, base: Path) -> frozenset[str]:
        return frozenset(str(path.relative_to(base)) for path in base.rglob("*") if path.is_file())

    def context(self, operation_id: str) -> upgrade.UpgradeContext:
        return upgrade.UpgradeContext(
            operation_id=operation_id, source_root=self.source, target_root=self.root,
            staging_root=self.staging(operation_id), preserve_startup_choices=True)

    def prepare(self, operation_id: str) -> tuple[upgrade.UpgradeContext, upgrade.MigrationJournal]:
        context = self.context(operation_id)
        _ = upgrade.acquire_compatible_hold(operation_id, lease=upgrade.lease_for(self.root))
        _ = upgrade.begin(context)
        staged = upgrade.stage_source(context, self.tree)
        _ = upgrade.verify_pre_publication(context, self.tree, upgrade.deployment_spec(context, self.tree, staged))
        return context, upgrade.read_journal(self.root, operation_id)

    def publish(self, context: upgrade.UpgradeContext, journal: upgrade.MigrationJournal) -> upgrade.MigrationJournal:
        return upgrade.publish(context, journal, Ledger(self.tree).context())

    def full_upgrade(self, operation_id: str) -> None:
        install.upgrade_install(self.tree, operation_id, source_root=self.source)


def run_internal(fx: Fixture, complete: bool, calls: list[tuple[str, ...]]) -> int:
    def command(argv_: tuple[str, ...], timeout: float = 10) -> subprocess.CompletedProcess[str]:
        _ = timeout
        calls.append(argv_)
        return subprocess.CompletedProcess(argv_, 0, "", "")

    with (mock.patch.object(install, "Tree", return_value=fx.tree),
          mock.patch.object(install, "is_retired", return_value=False),
          mock.patch.object(install.platform, "machine", return_value="x86_64"),
          mock.patch.object(install, "upgrade_source_root", return_value=fx.source),
          mock.patch.object(install, "empty_proof", return_value=True),
          mock.patch.object(install, "command", side_effect=command)):
        if complete:
            install.complete_install(fx.tree, OPID)
        else:
            install.upgrade_install(fx.tree, OPID, source_root=fx.source)
        return 0


def _no_mutation_calls(calls: list[tuple[str, ...]]) -> bool:
    return not any(len(argv) > 1 and argv[1] in {"enable", "disable", "start", "daemon-reload"} for argv in calls)


def _fail_once() -> object:
    real = upgrade._fsync
    state = {"raised": False}

    def fake(descriptor: int) -> None:
        if not state["raised"]:
            state["raised"] = True
            raise OSError(errno.EIO, "Injected fsync failure")
        real(descriptor)

    return fake


def _assert_held_and_staged(fx: Fixture, operation_id: str) -> upgrade.MigrationJournal:
    assert upgrade.lease_for(fx.root).load() is not None, "compatible hold must be retained"
    assert fx.staging(operation_id).is_dir(), "private staging must be preserved"
    return upgrade.read_journal(fx.root, operation_id)


def test_accepts_complete_local_upgrade(tmp_path: Path) -> None:
    # Given an established v0.1.1 tree with preserved startup/manual state
    fx = Fixture(tmp_path)
    fx.build()
    before_config = (fx.root / "etc/sparehash/config.toml").read_bytes()
    before_units = {path.name: path.read_bytes() for path in (fx.root / "etc/systemd/system").iterdir()}
    calls: list[tuple[str, ...]] = []
    # When the upgrade stages and publishes under a compatible hold
    assert run_internal(fx, False, calls) == 0
    # Then a published journal and a still-present private staging exist
    assert fx.staging(OPID).is_dir()
    assert upgrade.read_journal(fx.root, OPID).phase == "published"
    assert upgrade.lease_for(fx.root).load().purpose == "update"
    assert _no_mutation_calls(calls)
    # And the old startup choices, config and units are untouched
    assert (fx.root / "etc/sparehash/config.toml").read_bytes() == before_config
    assert {path.name: path.read_bytes() for path in (fx.root / "etc/systemd/system").iterdir()} == before_units
    # And the manual pause state is byte-identical
    assert fx.state().read_bytes() == fx.manual
    # And no foreign leaf was published
    assert fx.leaves(fx.target()) == EXPECTED_LEAVES
    assert (fx.target() / "sparehash/alpha.py").read_bytes() == b"NEW = 2\n"
    manifest = parse_manifest((fx.root / "var/lib/sparehash/install-manifest.json").read_text())
    assert manifest is not None and manifest.installer is not None
    by_id = {item.id: item for item in manifest.installer.artifacts}
    assert by_id["/opt/sparehash/src"].created
    assert not by_id["package-file:beta.py"].created
    # When the explicit completion runs
    assert run_internal(fx, True, calls) == 0
    # Then staging and the old-tree backup are cleaned only after COMPLETE
    assert not fx.staging(OPID).exists()
    assert not (fx.staging(OPID).parent / (fx.staging(OPID).name + ".old")).exists()
    assert not fx.journal().exists()
    assert upgrade.lease_for(fx.root).load() is None
    # And the preserved choices and manual state still stand
    assert (fx.root / "etc/sparehash/config.toml").read_bytes() == before_config
    assert fx.state().read_bytes() == fx.manual


CASES = ("enospc", "fsync", "foreign-leaf", "manual-drift", "missing-hold",
         "wrong-operation-id", "target-replaced", "staging-digest-mismatch", "empty-proof-missing")


@pytest.mark.parametrize("case", CASES)
def test_recovers_interrupted_publication(tmp_path: Path, case: str) -> None:
    fx = Fixture(tmp_path)
    fx.build()
    operation_id = "op-" + case

    if case == "enospc":
        context, journal = fx.prepare(operation_id)
        error = OSError(errno.ENOSPC, "No space left on device")
        with mock.patch.object(upgrade, "_materialize", side_effect=error):
            with pytest.raises(upgrade.UpgradeError):
                _ = fx.publish(context, journal)
        _ = _assert_held_and_staged(fx, operation_id)
        install.recover_install(fx.tree, operation_id)
        assert fx.leaves(fx.target()) == EXPECTED_LEAVES
    elif case == "fsync":
        context, journal = fx.prepare(operation_id)
        with mock.patch.object(upgrade, "_fsync", side_effect=_fail_once()):
            with pytest.raises(upgrade.UpgradeError):
                _ = fx.publish(context, journal)
        _ = _assert_held_and_staged(fx, operation_id)
        install.recover_install(fx.tree, operation_id)
        assert fx.leaves(fx.target()) == EXPECTED_LEAVES
    elif case == "foreign-leaf":
        fx.write_source("sparehash/notes.txt", b"foreign\n")
        with pytest.raises(upgrade.UpgradeError):
            fx.full_upgrade(operation_id)
        assert upgrade.lease_for(fx.root).load() is not None
        assert not fx.staging(operation_id).exists()
        with pytest.raises(upgrade.UpgradeError):
            install.recover_install(fx.tree, operation_id)
    elif case == "manual-drift":
        fx.full_upgrade(operation_id)
        drifted = b'{"manual": false}\n'
        _ = fx.state().write_bytes(drifted)
        install.recover_install(fx.tree, operation_id)
        assert fx.state().read_bytes() == drifted
    elif case == "missing-hold":
        fx.full_upgrade(operation_id)
        fx.lease().unlink()
        with pytest.raises(upgrade.UpgradeError):
            install.recover_install(fx.tree, operation_id)
    elif case == "wrong-operation-id":
        fx.full_upgrade(operation_id)
        with pytest.raises(upgrade.UpgradeError):
            install.recover_install(fx.tree, "op-other")
        assert fx.lease().exists()
    elif case == "target-replaced":
        context, journal = fx.prepare(operation_id)
        sentinel = fx.target() / "sparehash/foreign.txt"
        _ = sentinel.write_bytes(b"sentinel\n")
        with pytest.raises(upgrade.UpgradeError):
            _ = fx.publish(context, journal)
        _ = _assert_held_and_staged(fx, operation_id)
        with pytest.raises(upgrade.UpgradeError):
            _ = upgrade.recover_from_staging(operation_id, fx.root)
        assert sentinel.read_bytes() == b"sentinel\n"
    elif case == "staging-digest-mismatch":
        fx.full_upgrade(operation_id)
        tampered = fx.staging(operation_id) / "sparehash/alpha.py"
        _ = tampered.write_bytes(b"TAMPERED\n")
        with pytest.raises(upgrade.UpgradeError):
            _ = upgrade.recover_from_staging(operation_id, fx.root)
        assert fx.lease().exists()
    elif case == "empty-proof-missing":
        fx.full_upgrade(operation_id)
        with pytest.raises(upgrade.UpgradeError):
            install.complete_install(fx.tree, operation_id, proof=lambda: False)
        assert upgrade.read_journal(fx.root, operation_id).phase == "published"
        _ = _assert_held_and_staged(fx, operation_id)
    else:
        raise AssertionError(f"unhandled case {case}")
