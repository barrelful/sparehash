"""Conservative session lifetime proofs; no live workstation/session operations."""

import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import patch

import pytest

from sparehash import gaming_session
from sparehash.gaming_session import (
    CgroupEvidence,
    CgroupwalkProbe,
    GamingSessionGuard,
    InventoryResult,
    LogindSessionProbe,
    ProbeError,
    SessionInventory,
)
from sparehash.gaming_state import GamingEvidence, GamingHealth, GamingState


@dataclass(frozen=True, slots=True)
class Inventory:
    result: InventoryResult

    def inventory(self) -> InventoryResult:
        return self.result


@dataclass(frozen=True, slots=True)
class Groups:
    result: tuple[CgroupEvidence, ...]

    def snapshot(self, uids: tuple[int, ...]) -> tuple[CgroupEvidence, ...]:
        return self.result


FRESH = InventoryResult((), 100.0, "OK", frozenset({"logind.list_sessions", "logind.seat"}), None)
EMPTY = CgroupEvidence(None, "/user.slice", 100.0, True, False, True, None)
USER_EMPTY = replace(EMPTY, uid=1000, cgroup_path="/user.slice/user-1000.slice")
SESSION = SessionInventory(1000, "1", "sddm", "user", False, "seat0", True, None)


@pytest.mark.parametrize("age", [0.0, 1.0, 5.0])
def test_accepts_proven_empty_lifetimes(age: float) -> None:
    # Given
    guard = GamingSessionGuard(Inventory(FRESH), Groups((EMPTY, USER_EMPTY)), (1000,))
    # When
    decision = guard.evaluate(100.0 + age)
    # Then
    assert decision == "CLEAR"


@pytest.mark.parametrize(("inventory", "groups", "now", "expected"), [
    (FRESH, (), 100.0, "UNKNOWN"),
    (FRESH, (EMPTY,), 100.0, "UNKNOWN"),
    (FRESH, (replace(EMPTY, populated=True), USER_EMPTY), 100.0, "UNKNOWN"),
    (FRESH, (EMPTY, replace(USER_EMPTY, procs_empty=False)), 100.0, "UNKNOWN"),
    (FRESH, (EMPTY, replace(USER_EMPTY, complete=False)), 100.0, "UNKNOWN"),
    (FRESH, (EMPTY, replace(USER_EMPTY, error="permission denied")), 100.0, "UNKNOWN"),
    (FRESH, (EMPTY, USER_EMPTY), 106.0, "UNKNOWN"),
    (FRESH, (EMPTY, replace(USER_EMPTY, observed_at=101.0)), 100.0, "UNKNOWN"),
    (replace(FRESH, observed_at=60.0), (EMPTY, USER_EMPTY), 100.0, "UNKNOWN"),
    (replace(FRESH, health="DISCONNECTED"), (EMPTY, USER_EMPTY), 100.0, "UNKNOWN"),
    (replace(FRESH, health="STALE"), (EMPTY, USER_EMPTY), 100.0, "UNKNOWN"),
    (replace(FRESH, health="MALFORMED"), (EMPTY, USER_EMPTY), 100.0, "UNKNOWN"),
    (replace(FRESH, health="OVERFLOW"), (EMPTY, USER_EMPTY), 100.0, "UNKNOWN"),
    (replace(FRESH, completeness=frozenset({"logind.list_sessions"})), (EMPTY, USER_EMPTY), 100.0, "UNKNOWN"),
    (replace(FRESH, sessions=(SESSION,)), (EMPTY, USER_EMPTY), 100.0, "BLOCKED"),
    (replace(FRESH, sessions=(replace(SESSION, uid=2000),)), (EMPTY, USER_EMPTY, replace(USER_EMPTY, uid=2000, cgroup_path="/user.slice/user-2000.slice")), 100.0, "BLOCKED"),
    (replace(FRESH, sessions=(replace(SESSION, service="sshd", remote=True, seat_id=None),)), (EMPTY, USER_EMPTY), 100.0, "BLOCKED"),
    (FRESH, (EMPTY, USER_EMPTY), float("nan"), "UNKNOWN"),
    (replace(FRESH, observed_at=float("nan")), (EMPTY, USER_EMPTY), 100.0, "UNKNOWN"),
    (replace(FRESH, sessions=(SESSION,)), (EMPTY, USER_EMPTY), 106.0, "UNKNOWN"),
    (replace(FRESH, sessions=(SESSION,)), (), 100.0, "UNKNOWN"),
])
def test_inhibits_ambiguous_session_loss(
    inventory: InventoryResult, groups: tuple[CgroupEvidence, ...], now: float, expected: str,
) -> None:
    # Given
    guard = GamingSessionGuard(Inventory(inventory), Groups(groups), (1000,))
    # When
    decision = guard.evaluate(now)
    # Then
    assert decision == expected


@pytest.mark.parametrize("health", ["STALE", "DISCONNECTED", "MALFORMED", "OVERFLOW"])
def test_invalidates_prior_inventory(health: str) -> None:
    # Given
    probe = LogindSessionProbe()
    guard = GamingSessionGuard(probe, Groups((EMPTY, USER_EMPTY)), (1000,))
    with patch.object(probe, "inventory", side_effect=[FRESH, replace(FRESH, health=health)]):
        assert guard.evaluate(100.0) == "CLEAR"
        # When
        decision = guard.evaluate(100.0)
    # Then
    assert decision == "UNKNOWN"
    assert guard.cached_inventory is None


def test_coverage_removal_retains_lifetime() -> None:
    # Given
    guard = GamingSessionGuard(Inventory(FRESH), Groups((EMPTY, USER_EMPTY)), (1000,))
    # When
    guard.set_tracked_uids((2000,))
    # Then
    assert guard.tracked_uids == (1000, 2000)
    assert guard.evaluate(100.0) == "UNKNOWN"


def make_group(path: Path, procs: str = "", populated: int = 0) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "cgroup.procs").write_text(procs)
    (path / "cgroup.events").write_text(f"populated {populated}\nfrozen 0\n")


@pytest.mark.parametrize("kind", ["empty", "descendant", "populated", "missing", "symlink", "malformed"])
def test_recursive_cgroup_proof(tmp_path: Path, kind: str) -> None:
    # Given
    (tmp_path / "cgroup.controllers").write_text("cpu memory\n")
    root = tmp_path / "user.slice"
    make_group(root)
    user = root / "user-1000.slice"
    make_group(user)
    if kind == "descendant":
        make_group(user / "app.slice" / "game.scope", "321\n", 1)
    if kind == "populated":
        (user / "cgroup.events").write_text("populated 1\n")
    if kind == "missing":
        (user / "cgroup.events").unlink()
    if kind == "symlink":
        (user / "escaped").symlink_to(tmp_path, target_is_directory=True)
    if kind == "malformed":
        (user / "cgroup.events").write_text("populated 0\npopulated 0\n")
    probe = CgroupwalkProbe(tmp_path, clock=lambda: 100.0)
    # When
    decision = GamingSessionGuard(Inventory(FRESH), probe, (1000,)).evaluate(100.0)
    # Then
    assert decision == ("CLEAR" if kind == "empty" else "UNKNOWN")


def test_absent_user_slice_is_proven_from_parent(tmp_path: Path) -> None:
    # Given
    (tmp_path / "cgroup.controllers").write_text("cpu\n")
    probe = CgroupwalkProbe(tmp_path, clock=lambda: 100.0)
    # When
    decision = GamingSessionGuard(Inventory(FRESH), probe, (1000,)).evaluate(100.0)
    # Then
    assert decision == "CLEAR"


@pytest.mark.parametrize("response", ["", "array [ struct { } ]", "array [ ] garbage"])
def test_logind_parse_failure_is_not_empty_success(response: str) -> None:
    # Given
    with patch("sparehash.gaming_session.resolve_helper", return_value="/usr/bin/dbus-send"), patch(
        "sparehash.gaming_session.subprocess.run",
        return_value=subprocess.CompletedProcess((), 0, "method return sender=:1.1\n" + response, ""),
    ):
        # When
        result = LogindSessionProbe().inventory()
    # Then
    assert result.health == "MALFORMED"
    assert result.error


@pytest.mark.parametrize("failure", [OSError("refused"), subprocess.TimeoutExpired("dbus-send", 1)])
def test_logind_transport_failure_is_unknown(failure: OSError | subprocess.TimeoutExpired) -> None:
    # Given
    with patch("sparehash.gaming_session.resolve_helper", return_value="/usr/bin/dbus-send"), patch(
        "sparehash.gaming_session.subprocess.run", side_effect=failure,
    ):
        # When
        result = LogindSessionProbe().inventory()
    # Then
    assert result.health == "DISCONNECTED"
    assert result.error


@pytest.mark.parametrize("active", [False, True])
def test_logind_complete_snapshot(active: bool) -> None:
    # Given
    header = "method return sender=:1.1\n"
    row = ('array [ struct { string "1" uint32 1000 string "gamer" string "seat0" '
           'object path "/org/freedesktop/login1/session/_31" } ]')
    replies = [row if active else "array [ ]"]
    if active:
        replies += ['variant string "user"', 'variant string "wayland"',
                    'variant string "sddm"', 'variant boolean true', 'variant boolean false',
                    'variant struct { string "seat0" object path "/org/freedesktop/login1/seat/seat0" }']
    replies += replies[:1]
    with patch("sparehash.gaming_session.resolve_helper", return_value="/usr/bin/dbus-send"), patch(
        "sparehash.gaming_session.subprocess.run",
        side_effect=[subprocess.CompletedProcess((), 0, header + text, "") for text in replies],
    ) as runner:
        # When
        result = LogindSessionProbe().inventory()
    # Then
    assert result.health == "OK"
    assert result.sessions == ((SESSION,) if active else ())
    for call in runner.call_args_list:
        assert call.args[0][:3] == ("/usr/bin/dbus-send", "--system", "--print-reply")
        assert 0 < call.kwargs["timeout"] <= 0.75
        assert call.kwargs["env"]["LC_ALL"] == "C"


def test_session_disappearance_without_cgroup_proof_stays_unknown() -> None:
    # Given
    probe = LogindSessionProbe()
    groups = Groups((replace(EMPTY, populated=True), replace(USER_EMPTY, populated=True)))
    guard = GamingSessionGuard(probe, groups, (1000,))
    with patch.object(probe, "inventory", side_effect=[replace(FRESH, sessions=(SESSION,)), FRESH]):
        assert guard.evaluate(100.0) == "BLOCKED"
        # When
        decision = guard.evaluate(100.0)
    # Then
    assert decision == "UNKNOWN"


@pytest.mark.parametrize("kind", ["new-session", "refused", "oversized", "missing-helper"])
def test_failed_complete_resnapshot_never_clears(kind: str) -> None:
    # Given
    first = subprocess.CompletedProcess((), 0, "method return sender=:1.1\narray [ ]", "")
    second = subprocess.CompletedProcess((), 0, "method return sender=:1.1\narray [ ]", "")
    if kind == "new-session":
        second.stdout = ('method return sender=:1.1\narray [ struct { string "2" uint32 2000 '
                         'string "other" string "" object path "/org/freedesktop/login1/session/_32" } ]')
    if kind == "refused":
        second.returncode, second.stderr = 1, "connection refused"
    if kind == "oversized":
        second.stdout = "x" * 65537
    with patch("sparehash.gaming_session.resolve_helper", return_value=None if kind == "missing-helper" else "/usr/bin/dbus-send"), patch(
        "sparehash.gaming_session.subprocess.run", side_effect=[first, second],
    ):
        # When
        result = LogindSessionProbe().inventory()
    # Then
    assert result.health == {"new-session": "STALE", "refused": "DISCONNECTED",
                             "oversized": "OVERFLOW", "missing-helper": "DISCONNECTED"}[kind]
    assert result.error


def test_unconfigured_lingering_workload_inhibits(tmp_path: Path) -> None:
    # Given
    (tmp_path / "cgroup.controllers").write_text("cpu\n")
    make_group(tmp_path / "user.slice")
    make_group(tmp_path / "user.slice" / "user-2000.slice", "222\n", 1)
    guard = GamingSessionGuard(Inventory(FRESH), CgroupwalkProbe(tmp_path, clock=lambda: 100.0), (1000,))
    # When
    decision = guard.evaluate(100.0)
    # Then
    assert decision == "UNKNOWN"


def test_coverage_overflow_latches_unknown() -> None:
    # Given
    guard = GamingSessionGuard(Inventory(FRESH), Groups((EMPTY, USER_EMPTY)), tuple(range(128)))
    guard.set_tracked_uids((999,))
    # When
    decision = guard.evaluate(100.0)
    # Then
    assert decision == "UNKNOWN"
    assert len(guard.tracked_uids) == 128


def test_replaced_cgroup_namespace_is_not_empty_proof(tmp_path: Path) -> None:
    # Given
    (tmp_path / "cgroup.controllers").write_text("cpu\n")
    root = tmp_path / "user.slice"
    make_group(root)
    read = gaming_session._read
    replaced = False

    def replace_during_read(parent: int, name: str) -> str:
        nonlocal replaced
        text = read(parent, name)
        if name == "cgroup.events" and not replaced:
            replaced = True
            root.rename(tmp_path / "old.slice")
            make_group(root, "321\n", 1)
        return text

    with patch.object(gaming_session, "_read", side_effect=replace_during_read):
        # When
        evidence = CgroupwalkProbe(tmp_path).snapshot((1000,))
    # Then
    assert all(not group.complete for group in evidence)


@pytest.mark.parametrize("budget", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_freshness_budget_is_refused(budget: float) -> None:
    # Given / When / Then
    with pytest.raises(ProbeError):
        GamingSessionGuard(Inventory(FRESH), Groups((EMPTY, USER_EMPTY)), (1000,), budget)


def test_empty_coverage_is_not_permission() -> None:
    # Given
    guard = GamingSessionGuard(Inventory(FRESH), Groups((EMPTY,)), ())
    # When
    decision = guard.evaluate(100.0)
    # Then
    assert decision == "UNKNOWN"


@dataclass(frozen=True, slots=True)
class SteamEvidence:
    state: GamingState = "INACTIVE"
    uid: int = 1000
    health: GamingHealth = "OK"
    complete: bool = True
    observed_at: float = 100.0
    epoch_offset: int = 0

    def evaluate(self, epoch: int, *, now: float) -> GamingEvidence:
        return GamingEvidence("steam-log", self.uid, epoch + self.epoch_offset, 1, self.observed_at, self.state,
                              self.health, frozenset({f"steam-log:{self.uid}"})
                              if self.complete else frozenset(), "test-protocol")


@pytest.mark.parametrize("state,health,complete,idle,populated,expected", [
    ("INACTIVE", "OK", True, True, False, "CLEAR"),
    ("ACTIVE", "OK", True, True, False, "BLOCKED"),
    ("UNKNOWN", "OK", True, True, False, "BLOCKED"),
    ("INACTIVE", "STALE", True, True, False, "BLOCKED"),
    ("INACTIVE", "OK", False, True, False, "BLOCKED"),
    ("INACTIVE", "OK", True, False, False, "CLEAR"),
    ("INACTIVE", "OK", True, None, False, "CLEAR"),
    # Owner decision 2026-10-24: with producers configured, a complete INACTIVE
    # sample is the idle authority, so a populated user.slice no longer inhibits.
    ("INACTIVE", "OK", True, True, True, "CLEAR"),
    # Without that authority the populated slice still inhibits.
    ("ACTIVE", "OK", True, True, True, "BLOCKED"),
    ("INACTIVE", "STALE", True, True, True, "BLOCKED"),
])
def test_steam_clearance_retains_idle_and_cgroup_requirements(
    state: GamingState, health: GamingHealth, complete: bool, idle: bool | None, populated: bool, expected: str,
) -> None:
    # Given
    inventory = replace(FRESH, sessions=(replace(SESSION, idle_hint=idle),))
    groups = (replace(EMPTY, populated=populated), replace(USER_EMPTY, populated=populated))
    guard = GamingSessionGuard(Inventory(inventory), Groups(groups), (1000,),
                               steam_producers=(SteamEvidence(state, 1000, health, complete),))
    # When / Then
    assert guard.evaluate(100.0) == expected


@pytest.mark.parametrize("sessions", [(), (SESSION,)])
@pytest.mark.parametrize("state", ["ACTIVE", "UNKNOWN"])
def test_steam_inhibition_survives_session_disappearance(
    sessions: tuple[SessionInventory, ...], state: GamingState,
) -> None:
    # Given
    guard = GamingSessionGuard(Inventory(replace(FRESH, sessions=sessions)),
                               Groups((EMPTY, USER_EMPTY)), (1000,),
                               steam_producers=(SteamEvidence(state),))
    # When / Then
    assert guard.evaluate(100.0) != "CLEAR"


def test_steam_does_not_clear_uncovered_uid() -> None:
    # Given
    sessions = (SESSION, replace(SESSION, uid=2000, session_id="2"))
    groups = (EMPTY, USER_EMPTY, replace(USER_EMPTY, uid=2000,
                                        cgroup_path="/user.slice/user-2000.slice"))
    guard = GamingSessionGuard(Inventory(replace(FRESH, sessions=sessions)), Groups(groups),
                               (1000,), steam_producers=(SteamEvidence(),))
    # When / Then
    assert guard.evaluate(100.0) == "BLOCKED"


@pytest.mark.parametrize("sample", [
    SteamEvidence(observed_at=94.99), SteamEvidence(observed_at=100.1),
    SteamEvidence(observed_at=float("nan")), SteamEvidence(epoch_offset=-1),
    SteamEvidence(epoch_offset=1),
])
def test_steam_freshness_and_epoch_use_guard_now(sample: SteamEvidence) -> None:
    # Given
    guard = GamingSessionGuard(Inventory(replace(FRESH, sessions=(SESSION,))),
                               Groups((EMPTY, USER_EMPTY)), (1000,), steam_producers=(sample,))
    # When / Then: real monotonic time is intentionally unrelated to this clock.
    assert guard.evaluate(100.0) == "BLOCKED"


@pytest.mark.parametrize("changed", [
    SteamEvidence(observed_at=94.0), SteamEvidence(epoch_offset=-1),
    SteamEvidence(state="UNKNOWN"), SteamEvidence(health="STALE"),
    SteamEvidence(complete=False),
])
def test_prior_clear_does_not_hide_a_rejected_current_sample(changed: SteamEvidence) -> None:
    # Given: a replay/old epoch must not borrow prior reducer clearance.
    source = SteamEvidence()
    guard = GamingSessionGuard(Inventory(replace(FRESH, sessions=(SESSION,))),
                               Groups((EMPTY, USER_EMPTY)), (1000,), steam_producers=(source,))
    assert guard.evaluate(100.0) == "CLEAR"
    # When
    with patch.object(SteamEvidence, "evaluate", return_value=changed.evaluate(guard._steam.current_epoch, now=100.0)):
        decision = guard.evaluate(100.0)
    # Then
    assert decision == "BLOCKED"
