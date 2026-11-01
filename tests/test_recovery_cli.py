"""Recovery CLI: clear only the explicitly resolved uninstall retirement.

The install --clear-lifecycle-latch boundary and lifecycle.clear_retired must
match the operation id recorded in the retirement record. A resolved uninstall
retirement is cleared; unrelated, unresolved, concurrent or corrupt records are
refused and left byte-identical. The retired diagnostic never suggests deleting
the latch by hand.
"""

from __future__ import annotations

import io
import tempfile
from collections.abc import Callable
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

import pytest

from sparehash import install, lifecycle
from sparehash.lifecycle_lease import ActiveLease, LeaseStore

OP = "op-1234"
OTHER = "op-9999"
UNINSTALL = ActiveLease("a" * 32, OP, "uninstall")
UPDATE = ActiveLease("b" * 32, OP, "update")


def _run_install(latch: Path, *flags: str) -> tuple[int, str]:
    """Run install.main on the fixture latch, stopping at the first capability probe."""
    captured = io.StringIO()
    with (mock.patch("sparehash.lifecycle.LIFECYCLE_PATH", latch),
          mock.patch("sparehash.install.Tree.read", return_value=None),
          mock.patch("sparehash.install.platform.machine", return_value="x86_64"),
          mock.patch("sparehash.install.Capabilities.detect", side_effect=OSError("injected stop")),
          redirect_stderr(captured)):
        code = install.main([*flags], geteuid=lambda: 0)
    return code, captured.getvalue()


def _refused(latch: Path, *flags: str) -> None:
    """Assert the installer refused the clear and left the system retired."""
    code, errors = _run_install(latch, *flags)
    assert code == 1, errors
    assert "lifecycle latch retained" in errors, errors
    assert "Traceback" not in errors, errors


def test_accepts_resolved_retired_reinstall() -> None:
    """A matching operation id clears the retirement record and the uninstall hold."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        latch = root / "lifecycle.json"
        store = LeaseStore(root / "lifecycle-lease.json")
        # Given a completed uninstall: retirement record plus matching uninstall hold
        lifecycle.retire(latch, operation_id=OP)
        store.save(UNINSTALL)
        # When the operator supplies the matching operation id
        code, errors = _run_install(latch, "--clear-lifecycle-latch", "--operation-id", OP)
        # Then the clear happened before any install work and both records are gone
        assert code == 1, errors
        assert "injected stop" in errors
        assert "lifecycle latch retained" not in errors
        assert not lifecycle.is_retired(latch)
        assert store.load() is None


def test_retired_guidance_names_the_exact_recovery_command() -> None:
    """The retired diagnostic prints the exact recovery command, never manual deletion."""
    with tempfile.TemporaryDirectory() as directory:
        latch = Path(directory) / "lifecycle.json"
        # Given a retired system with a recorded operation id
        lifecycle.retire(latch, operation_id=OP)
        # When the installer runs without the explicit clear
        code, errors = _run_install(latch)
        # Then it refuses and prints the exact recovery command
        assert code == 2, errors
        assert f"--clear-lifecycle-latch --operation-id {OP}" in errors
        assert "remove /var/lib/sparehash/lifecycle.json" not in errors
        assert "Do not delete" in errors


def test_legacy_latch_without_an_id_stays_clearable() -> None:
    """A v0.1.1 retirement record with no operation id remains clearable."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        latch = root / "lifecycle.json"
        store = LeaseStore(root / "lifecycle-lease.json")
        # Given a legacy retirement record with no operation id
        lifecycle.retire(latch)
        store.save(ActiveLease("c" * 32, "request-1", "uninstall"))
        # When the explicit clear runs without an operation id
        code, errors = _run_install(latch, "--clear-lifecycle-latch")
        # Then the legacy record is still cleared for backward compatibility
        assert code == 1, errors
        assert "injected stop" in errors
        assert not lifecycle.is_retired(latch)
        assert store.load() is None


def _scenario_wrong_operation_id(latch: Path, lease_path: Path) -> None:
    lifecycle.retire(latch, operation_id=OP)
    store = LeaseStore(lease_path)
    store.save(UNINSTALL)
    before = latch.read_bytes()
    _refused(latch, "--clear-lifecycle-latch", "--operation-id", OTHER)
    assert latch.read_bytes() == before
    assert store.load() == UNINSTALL


def _scenario_missing_operation_id(latch: Path, lease_path: Path) -> None:
    lifecycle.retire(latch, operation_id=OP)
    store = LeaseStore(lease_path)
    store.save(UNINSTALL)
    before = latch.read_bytes()
    _refused(latch, "--clear-lifecycle-latch")
    assert latch.read_bytes() == before
    assert store.load() == UNINSTALL


def _scenario_mismatched_lease_request_id(latch: Path, lease_path: Path) -> None:
    lifecycle.retire(latch, operation_id=OP)
    store = LeaseStore(lease_path)
    foreign = ActiveLease("c" * 32, OTHER, "uninstall")
    store.save(foreign)
    before = latch.read_bytes()
    _refused(latch, "--clear-lifecycle-latch", "--operation-id", OP)
    assert latch.read_bytes() == before
    assert store.load() == foreign


def _scenario_active_update_hold(latch: Path, lease_path: Path) -> None:
    lifecycle.retire(latch, operation_id=OP)
    store = LeaseStore(lease_path)
    store.save(UPDATE)
    before = latch.read_bytes()
    _refused(latch, "--clear-lifecycle-latch", "--operation-id", OP)
    assert latch.read_bytes() == before
    assert store.load() == UPDATE


def _scenario_corrupt_lease_record(latch: Path, lease_path: Path) -> None:
    lifecycle.retire(latch, operation_id=OP)
    _ = lease_path.write_text("{", encoding="utf-8")
    before = latch.read_bytes()
    _refused(latch, "--clear-lifecycle-latch", "--operation-id", OP)
    assert latch.read_bytes() == before
    assert lease_path.read_text(encoding="utf-8") == "{"


def _scenario_concurrent_reinstall(latch: Path, lease_path: Path) -> None:
    # A concurrent uninstall holds a different operation id than the retirement record
    lifecycle.retire(latch, operation_id=OP)
    store = LeaseStore(lease_path)
    foreign = ActiveLease("d" * 32, OTHER, "uninstall")
    store.save(foreign)
    before = latch.read_bytes()
    _refused(latch, "--clear-lifecycle-latch", "--operation-id", OTHER)
    assert latch.read_bytes() == before
    assert store.load() == foreign


def _scenario_unrelated_hold_without_latch(latch: Path, lease_path: Path) -> None:
    # No retirement to resolve: the explicit clear must not adopt or erase the hold
    store = LeaseStore(lease_path)
    store.save(UNINSTALL)
    code, errors = _run_install(latch, "--clear-lifecycle-latch", "--operation-id", OP)
    assert code == 1, errors
    assert "lifecycle latch cleared" not in errors
    assert store.load() == UNINSTALL


def _scenario_corrupt_retirement_record(latch: Path, lease_path: Path) -> None:
    # A corrupt retirement record is never cleared
    _ = latch.write_text("{", encoding="utf-8")
    store = LeaseStore(lease_path)
    store.save(UNINSTALL)
    code, errors = _run_install(latch, "--clear-lifecycle-latch", "--operation-id", OP)
    assert code == 1, errors
    assert "lifecycle latch cleared" not in errors
    assert latch.read_text(encoding="utf-8") == "{"
    assert store.load() == UNINSTALL


_SCENARIOS: tuple[tuple[str, Callable[[Path, Path], None]], ...] = (
    ("wrong-operation-id", _scenario_wrong_operation_id),
    ("missing-operation-id", _scenario_missing_operation_id),
    ("mismatched-lease-request-id", _scenario_mismatched_lease_request_id),
    ("active-update-hold", _scenario_active_update_hold),
    ("corrupt-lease-record", _scenario_corrupt_lease_record),
    ("concurrent-reinstall", _scenario_concurrent_reinstall),
    ("unrelated-hold-without-latch", _scenario_unrelated_hold_without_latch),
    ("corrupt-retirement-record", _scenario_corrupt_retirement_record),
)


@pytest.mark.parametrize("scenario", [case for _, case in _SCENARIOS], ids=[name for name, _ in _SCENARIOS])
def test_refuses_unresolved_or_unrelated_holds(scenario: Callable[[Path, Path], None]) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        scenario(root / "lifecycle.json", root / "lifecycle-lease.json")
