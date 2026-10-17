"""Migration journal and compatible v0.1.1 hold tests on private roots only."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from sparehash.install_manifest import StoreContext, locked_store
from sparehash.lifecycle_lease import ActiveLease, LeaseStore
from sparehash.migration import (
    CompletionEvidence,
    MigrationError,
    MigrationJournal,
    MigrationPhase,
    MigrationStore,
    acquire_compatible_hold,
    complete_migration,
)
from sparehash.state import BlockerState
from sparehash.xmrig_paths import PathTrust

REPO_ROOT = Path(__file__).resolve().parent.parent
MANUAL_STATE = json.dumps({"manual": True, "reason": "operator", "source": "cli"}).encode("utf-8")


def _state_dir(tmp_path: Path) -> Path:
    """Create a protected state directory that locked_store pins and flocks."""
    state = tmp_path / "var/lib/sparehash"
    state.mkdir(parents=True)
    for directory in (tmp_path / "var", tmp_path / "var/lib", state):
        directory.chmod(0o755)
    tmp_path.chmod(0o755)
    return state


def _context(tmp_path: Path) -> StoreContext:
    return StoreContext(PathTrust(tmp_path, os.getuid()), timeout=0)


def _write_hold(state: Path, *, token: str = "ab" * 16, request_id: str = "op-19",
                purpose: str = "update") -> ActiveLease:
    lease = ActiveLease(token, request_id, purpose)
    LeaseStore(state / "lifecycle-lease.json").save(lease)
    return lease


def _journal(hold: ActiveLease, *, phase: MigrationPhase = MigrationPhase.PREPARED,
             manual_state_preserved: bool = True) -> MigrationJournal:
    return MigrationJournal(
        operation_id=hold.request_id,
        payload_identity="payload-abc123",
        previous_layout={"opt_root": "/opt/sparehash", "local_root": "/usr/local"},
        new_layout={"opt_root": "/opt/sparehash/dist", "local_root": "/usr/local"},
        phase=phase,
        phase_changed_at=1.0,
        manual_state_preserved=manual_state_preserved,
        expected_completion_action="complete-upgrade",
        hold_request_id=hold.request_id,
        hold_token=hold.token,
    )


def _evidence(journal: MigrationJournal, **overrides: object) -> CompletionEvidence:
    base = CompletionEvidence(
        payload_identity=journal.payload_identity,
        empty_cgroup=True,
        guards_clear=True,
        manual_state_preserved=True,
    )
    return replace(base, **overrides)


def _force_bytes(state: Path, name: str, data: bytes) -> None:
    path = state / name
    path.unlink(missing_ok=True)
    path.write_bytes(data)


def test_accepts_old_readable_hold_and_completion(tmp_path: Path) -> None:
    state = _state_dir(tmp_path)
    hold = _write_hold(state)
    # The frozen v0.1.1 parser reads the exact three-field sidecar we adopt.
    assert LeaseStore(state / "lifecycle-lease.json").load() == hold

    (state / "state.json").write_bytes(MANUAL_STATE)

    with locked_store(_context(tmp_path)) as store:
        adopted = acquire_compatible_hold(store, request_id=hold.request_id)
        assert adopted == hold

        journal_store = MigrationStore(store)
        journal = _journal(hold)
        journal_store.write(journal)
        info = (state / "migration.json").stat()
        assert (info.st_mode & 0o777, info.st_uid) == (0o640, os.getuid())

        for phase in (MigrationPhase.HELD, MigrationPhase.STOPPED_EMPTY, MigrationPhase.STAGED,
                      MigrationPhase.PUBLISHED, MigrationPhase.VERIFIED_AWAITING_OPERATOR):
            journal = journal_store.advance(phase)
            assert MigrationStore(store).read() == journal
        assert journal.phase is MigrationPhase.VERIFIED_AWAITING_OPERATOR

        invalidated: list[str] = []
        result = complete_migration(hold.request_id, store, evidence=_evidence(journal),
                                    invalidate_authorization=lambda: invalidated.append("done"))
        assert result is MigrationPhase.COMPLETE
        assert invalidated == ["done"]
        assert journal_store.read() is None

    # Completion removed only the journal and the hold; the pause is byte-identical.
    assert not (state / "migration.json").exists()
    assert not (state / "lifecycle-lease.json").exists()
    assert (state / "state.json").read_bytes() == MANUAL_STATE
    # Restart-equivalent checks: old parser sees no hold and the manual pause survives.
    assert LeaseStore(state / "lifecycle-lease.json").load() is None
    assert "manual" in BlockerState(state_file=state / "state.json").active()


@pytest.mark.parametrize("case", [
    "missing_field",
    "corrupt_journal",
    "symlink_journal",
    "hardlink_journal",
    "wrong_phase",
    "skipped_phase",
    "early_complete",
    "no_compatible_hold",
    "replay_other_request",
    "wrong_token",
    "wrong_purpose",
    "absent_hold_during_completion",
    "operation_id_mismatch",
    "payload_identity_mismatch",
    "manual_state_drift",
])
def test_rejects_corruption_replay_and_early_release(tmp_path: Path, case: str) -> None:
    state = _state_dir(tmp_path)
    hold = _write_hold(state)
    phase = MigrationPhase.VERIFIED_AWAITING_OPERATOR
    if case == "wrong_phase":
        phase = MigrationPhase.PUBLISHED
    elif case in ("skipped_phase", "early_complete"):
        phase = MigrationPhase.PREPARED

    with locked_store(_context(tmp_path)) as store:
        journal_store = MigrationStore(store)
        journal = _journal(hold, phase=phase)
        journal_store.write(journal)
        evidence = _evidence(journal)

        if case == "missing_field":
            raw = json.loads((state / "migration.json").read_text(encoding="utf-8"))
            del raw["payload_identity"]
            _force_bytes(state, "migration.json", json.dumps(raw).encode("utf-8"))
            with pytest.raises(MigrationError):
                _ = journal_store.read()
        elif case == "corrupt_journal":
            _force_bytes(state, "migration.json", b"{not a journal")
            with pytest.raises(MigrationError):
                _ = journal_store.read()
        elif case == "symlink_journal":
            (state / "migration.json").unlink()
            (state / "migration.json").symlink_to(state / "install-manifest.json")
            with pytest.raises(MigrationError):
                _ = journal_store.read()
        elif case == "hardlink_journal":
            os.link(state / "migration.json", state / "linked.json")
            with pytest.raises(MigrationError):
                _ = journal_store.read()
        elif case == "wrong_phase":
            with pytest.raises(MigrationError):
                _ = complete_migration(hold.request_id, store, evidence=evidence)
        elif case == "skipped_phase":
            with pytest.raises(MigrationError):
                _ = journal_store.advance(MigrationPhase.STAGED)
        elif case == "early_complete":
            with pytest.raises(MigrationError):
                _ = journal_store.advance(MigrationPhase.COMPLETE)
        elif case == "no_compatible_hold":
            (state / "lifecycle-lease.json").unlink()
            with pytest.raises(MigrationError):
                _ = acquire_compatible_hold(store, request_id=hold.request_id)
        elif case == "replay_other_request":
            with pytest.raises(MigrationError):
                _ = acquire_compatible_hold(store, request_id="some-other-operation")
        elif case == "wrong_token":
            _ = _write_hold(state, token="cd" * 16, request_id=hold.request_id)
            with pytest.raises(MigrationError):
                _ = complete_migration(hold.request_id, store, evidence=evidence)
        elif case == "wrong_purpose":
            _ = _write_hold(state, purpose="uninstall")
            with pytest.raises(MigrationError):
                _ = complete_migration(hold.request_id, store, evidence=evidence)
        elif case == "absent_hold_during_completion":
            (state / "lifecycle-lease.json").unlink()
            with pytest.raises(MigrationError):
                _ = complete_migration(hold.request_id, store, evidence=evidence)
        elif case == "operation_id_mismatch":
            with pytest.raises(MigrationError):
                _ = complete_migration("other-operation", store, evidence=evidence)
        elif case == "payload_identity_mismatch":
            with pytest.raises(MigrationError):
                _ = complete_migration(hold.request_id, store,
                                       evidence=_evidence(journal, payload_identity="payload-other"))
        elif case == "manual_state_drift":
            with pytest.raises(MigrationError):
                _ = complete_migration(hold.request_id, store,
                                       evidence=_evidence(journal, manual_state_preserved=False))
        else:  # pragma: no cover - parametrization is exhaustive
            raise AssertionError(case)


def test_module_carries_no_controller_or_state_authority() -> None:
    source = (REPO_ROOT / "sparehash" / "migration.py").read_text(encoding="utf-8")
    for forbidden in ("sparehash.controller", "sparehash.state", "from .controller",
                      "from .state", "abort_update(", "end_external("):
        assert forbidden not in source, forbidden
