"""Local contention regressions: parser, identity, thresholds, pool contracts.

No installed CLI, host service, or /etc file is touched. Every case drives the
verified helpers with artificial /proc/<pid>/stat strings, a bounded fake
clock, a temporary directory, or a stubbed interpreter.
"""

# allow: SIZE_OK — this file is the sole owned home for the mandated
# parser/identity/threshold/local-pool/recovery/wrapper contract matrix, so it
# is intentionally kept whole instead of split.

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import pytest

import qa.contention as contention
import qa.local_pool as pool
import qa.spin as spin

REPO: Final = Path(__file__).resolve().parents[1]
WRAPPER: Final = REPO / "qa" / "qa_contention.sh"
HELPER: Final = REPO / "qa" / "contention.py"
INSTALLED_SRC: Final = "/opt/sparehash/src"


def stat_line(pid: int, comm: str, *, state: str = "S", length: int = 52,
              overrides: dict[int, int | str] | None = None) -> str:
    """Artificial /proc/<pid>/stat whose tail starts at the state field.

    Tail indexes: 0 state, 10 cmajflt, 11 utime, 12 stime, 19 starttime.
    """
    fields = ["0"] * length
    if length > 0:
        fields[0] = state
    for index, value in (overrides or {}).items():
        if index < length:
            fields[index] = str(value)
    return f"{pid} ({comm}) {' '.join(fields)}".rstrip()


class TestParseStat:
    """Zero-based 11/12/19 after the FINAL ')' keeps comm from stealing fields."""

    def test_comm_with_spaces_and_parentheses_keeps_field_alignment(self) -> None:
        # Given: cmajflt carries a sentinel so a shifted parser cannot pass.
        raw = stat_line(4242, "xmrig (worker 1)",
                        overrides={10: 9999, 11: 120, 12: 30, 19: 200})
        # When
        sample = contention.parse_stat(raw, 7.5)
        # Then: utime+stime only, starttime preserved, at timestamp untouched.
        assert sample == contention.Sample(4242, 200, 150, 7.5)

    def test_comm_with_closing_parenthesis_splits_on_final_separator(self) -> None:
        # Given
        raw = stat_line(77, "a)b (c)", overrides={11: 1, 12: 2, 19: 99})
        # When
        sample = contention.parse_stat(raw, 1.0)
        # Then
        assert sample == contention.Sample(77, 99, 3, 1.0)

    @pytest.mark.parametrize(("raw", "reason"), [
        ("", "malformed proc stat"),
        ("42 (xmrig S 0 0 0", "malformed proc stat"),
        ("42 xmrig) S 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0", "malformed proc stat"),
        (stat_line(5, "x", length=19), "missing proc stat fields"),
        (stat_line(5, "x", length=0), "missing proc stat fields"),
        (stat_line(5, "x", overrides={11: "abc"}), "nonnumeric proc stat fields"),
        ("abc (x) " + " ".join(["0"] * 52), "nonnumeric proc stat fields"),
        (stat_line(0, "x"), "invalid proc counters"),
        (stat_line(5, "x", overrides={11: -5}), "invalid proc counters"),
        (stat_line(5, "x", overrides={19: -1}), "invalid proc counters"),
        (stat_line(5, "x", state="Z"), "miner process exited"),
        (stat_line(5, "x", state="X"), "miner process exited"),
        (stat_line(5, "x", state="x"), "miner process exited"),
    ])
    def test_rejects_malformed_missing_nonnumeric_or_dead_input(
        self, raw: str, reason: str,
    ) -> None:
        # Given / When / Then
        with pytest.raises(contention.CheckFailure, match=reason):
            _ = contention.parse_stat(raw, 0.0)


class TestCpuPercent:
    """Identity proof plus normalization by the ACTUAL monotonic interval."""

    def test_normalizes_by_actual_elapsed_interval(self) -> None:
        # Given
        before = contention.Sample(9, 100, 1000, 10.0)
        scale = contention.CpuScale(4, 100)
        # When: identical tick delta over 2s and over 4s
        fast = contention.cpu_percent(before, contention.Sample(9, 100, 1100, 12.0), scale)
        slow = contention.cpu_percent(before, contention.Sample(9, 100, 1100, 14.0), scale)
        # Then: 100 ticks / (interval * 100Hz * 4 cpus)
        assert fast == pytest.approx(12.5)
        assert slow == pytest.approx(6.25)
        assert slow * 2 == pytest.approx(fast)

    @pytest.mark.parametrize(("pid", "start"), [(10, 100), (9, 101)])
    def test_rejects_pid_or_start_tick_identity_change(
        self, pid: int, start: int,
    ) -> None:
        # Given / When / Then: a restarted or reused PID is never compared.
        with pytest.raises(contention.CheckFailure, match="PID changed"):
            _ = contention.cpu_percent(
                contention.Sample(9, 100, 1000, 1.0),
                contention.Sample(pid, start, 1100, 2.0),
                contention.CpuScale(1, 100),
            )

    @pytest.mark.parametrize(("before_at", "after_at", "delta"), [
        (1.0, 1.0, 100),             # zero interval
        (2.0, 1.0, 100),             # clock ran backwards
        (1.0, float("nan"), 100),    # non-finite interval
        (1.0, 2.0, -1),              # counter regression
    ])
    def test_rejects_invalid_interval_or_counter(
        self, before_at: float, after_at: float, delta: int,
    ) -> None:
        # Given / When / Then
        with pytest.raises(contention.CheckFailure, match="invalid CPU sampling"):
            _ = contention.cpu_percent(
                contention.Sample(9, 100, 1000, before_at),
                contention.Sample(9, 100, 1000 + delta, after_at),
                contention.CpuScale(1, 100),
            )

    @pytest.mark.parametrize("scale", [contention.CpuScale(0, 100), contention.CpuScale(1, 0)])
    def test_rejects_invalid_normalization_scale(self, scale: contention.CpuScale) -> None:
        # Given / When / Then
        with pytest.raises(contention.CheckFailure, match="invalid CPU normalization"):
            _ = contention.cpu_percent(
                contention.Sample(9, 100, 1000, 1.0),
                contention.Sample(9, 100, 1100, 2.0),
                scale,
            )


class TestThresholds:
    """Strict acceptance bounds: yield 10%, degradation 25%, recovery 20%."""

    @pytest.mark.parametrize("percent", [0.0, 5.0, 10.0])
    def test_yield_accepts_up_to_ten_percent(self, percent: float) -> None:
        # Given / When / Then
        contention.check_yield(percent)

    @pytest.mark.parametrize("percent", [10.0001, 11.0, 50.0, float("nan"), -0.001, -25.0])
    def test_yield_rejects_exceed_nan_and_negative(self, percent: float) -> None:
        # Given / When / Then
        with pytest.raises(contention.CheckFailure, match="exceeds 10%"):
            contention.check_yield(percent)

    @pytest.mark.parametrize(("baseline", "under", "degradation"), [
        (400.0, 400.0, 0.0),
        (400.0, 300.0, 25.0),   # exact bound stays acceptable
        (400.0, 440.0, -10.0),  # an improvement is not a failure
    ])
    def test_throughput_accepts_within_bound(
        self, baseline: float, under: float, degradation: float,
    ) -> None:
        # Given / When / Then
        assert contention.check_throughput(baseline, under) == pytest.approx(degradation)

    def test_throughput_rejects_when_degradation_exceeds_bound(self) -> None:
        # Given / When / Then: 299/400 is 25.25% degraded.
        with pytest.raises(contention.CheckFailure, match="exceeds 25%"):
            _ = contention.check_throughput(400.0, 299.0)

    @pytest.mark.parametrize(("baseline", "under"), [
        (0.0, 100.0),
        (-1.0, 100.0),
        (float("nan"), 100.0),
        (100.0, 0.0),
        (100.0, -1.0),
        (100.0, float("nan")),
    ])
    def test_throughput_rejects_missing_or_invalid(
        self, baseline: float, under: float,
    ) -> None:
        # Given / When / Then
        with pytest.raises(contention.CheckFailure, match="missing or invalid"):
            _ = contention.check_throughput(baseline, under)


class _BoundedClock:
    """Deterministic monotonic clock; every sleep advances and is counted."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now: float = start
        self.sleeps: int = 0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        if self.sleeps > 50:
            raise AssertionError("recovery loop exceeded 50 bounded clock sleeps")
        self.now += seconds


class _Samples:
    """Replay recorded cpu tick totals against the fake clock."""

    def __init__(self, clock: _BoundedClock, ticks: tuple[int, ...]) -> None:
        self._clock: _BoundedClock = clock
        self._ticks: tuple[int, ...] = ticks
        self.calls: int = 0

    def __call__(self) -> contention.Sample:
        ticks = self._ticks[min(self.calls, len(self._ticks) - 1)]
        self.calls += 1
        return contention.Sample(7, 500, ticks, self._clock.now)


@dataclass(frozen=True, slots=True)
class _Progress(contention.Workload):
    """Workload seam: hashes() replays a bounded series, repeating its last value."""

    port: int = 0
    token: str = ""
    totals: tuple[int, ...] = ()
    seen: list[int] = field(default_factory=lambda: [0])

    @property
    def calls(self) -> int:
        return self.seen[0]

    def hashes(self) -> int:
        if not self.totals:
            raise ValueError("progress seam needs at least one hash total")
        index = self.seen[0]
        value = self.totals[min(index, len(self.totals) - 1)]
        self.seen[0] = index + 1
        return value


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _BoundedClock:
    bounded = _BoundedClock()
    monkeypatch.setattr(time, "monotonic", bounded.monotonic)
    monkeypatch.setattr(time, "sleep", bounded.sleep)
    monkeypatch.setattr(contention, "CPUS", 4)
    monkeypatch.setattr(contention, "HZ", 100)
    return bounded


class TestRecover:
    """Recovery requires BOTH >=20% CPU and real hash progress before deadline."""

    def _start(
        self, clock: _BoundedClock, monkeypatch: pytest.MonkeyPatch, ticks: tuple[int, ...],
        hashes: tuple[int, ...] = (10,),
    ) -> tuple[_Samples, _Progress]:
        samples = _Samples(clock, ticks)
        progress = _Progress(totals=hashes)
        monkeypatch.setattr(contention, "snapshot", samples)
        return samples, progress

    @staticmethod
    def _identity() -> contention.Sample:
        return contention.Sample(7, 500, 1000, 0.0)

    def test_returns_when_cpu_and_progress_arrive_before_deadline(
        self, clock: _BoundedClock, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Given: 1000 ticks at baseline, then +200 ticks one fake second later.
        samples, progress = self._start(clock, monkeypatch, (1100, 1300), (10, 20))
        # When
        _ = contention.recover(progress, clock.monotonic() + 300, self._identity())
        # Then
        assert samples.calls == 2
        assert progress.calls == 2
        assert "CPU=50.000" in capsys.readouterr().out

    def test_rejects_when_cpu_never_reaches_threshold(
        self, clock: _BoundedClock, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Given: tick totals never move, so CPU stays at zero percent.
        _, progress = self._start(clock, monkeypatch, (1100,))
        # When / Then
        with pytest.raises(contention.CheckFailure, match="did not recover to"):
            _ = contention.recover(progress, clock.monotonic() + 5, self._identity())
        assert clock.sleeps <= 5
        assert "CPU=0.000" in capsys.readouterr().out

    def test_rejects_when_hash_progress_stalls(
        self, clock: _BoundedClock, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Given: CPU clears 50% every interval but hashes never increase.
        _, progress = self._start(clock, monkeypatch, (1100, 1300, 1500, 1700, 1900, 2100))
        # When / Then
        with pytest.raises(contention.CheckFailure, match="did not recover to"):
            _ = contention.recover(progress, clock.monotonic() + 5, self._identity())
        assert progress.calls >= 2
        assert "CPU=50.000" in capsys.readouterr().out

    def test_rejects_expired_deadline_without_sleeping(
        self, clock: _BoundedClock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given
        _, progress = self._start(clock, monkeypatch, (1100,))
        # When / Then
        with pytest.raises(contention.CheckFailure, match="did not recover to"):
            _ = contention.recover(progress, clock.monotonic(), self._identity())
        assert clock.sleeps == 0
        assert progress.calls == 1

    def test_rejects_restarted_miner_identity(
        self, clock: _BoundedClock, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given: start_ticks moved, so this is a new process behind the same PID.
        _, progress = self._start(clock, monkeypatch, (1100,))
        identity = contention.Sample(7, 501, 1000, 0.0)
        # When / Then
        with pytest.raises(contention.CheckFailure, match="PID changed"):
            _ = contention.recover(progress, clock.monotonic() + 5, identity)
        assert clock.sleeps == 0


class TestLocalPoolContract:
    """The bounded fixture answers Monero JSON-RPC; no external network."""

    @staticmethod
    def _call(method: str, request_id: int = 1) -> dict[str, Any]:
        line = json.dumps({"id": request_id, "method": method, "params": []}).encode()
        return json.loads(pool.response(line))

    def test_login_emits_the_fixed_local_job(self) -> None:
        # Given / When
        reply = self._call("login")
        # Then
        assert reply["id"] == 1
        assert reply["error"] is None
        result = reply["result"]
        assert result["id"] == "local-qa"
        assert result["status"] == "OK"
        assert result["job"]["job_id"] == "local-qa-only"
        assert result["job"]["algo"] == "rx/0"
        assert result["job"]["seed_hash"] == "00" * 32
        assert result["job"]["height"] == 1

    def test_submit_is_rejected_not_forwarded(self) -> None:
        # Given / When
        reply = self._call("submit", request_id=9)
        # Then
        assert reply["id"] == 9
        assert reply["result"] is None
        assert reply["error"] == {"code": -1, "message": "synthetic QA share rejected"}

    def test_subscribe_does_not_emit_a_job(self) -> None:
        # Given: Monero miners log in; subscribe is not a job-bearing method here.
        # When
        reply = self._call("mining.subscribe")
        # Then
        assert reply["result"] == {"status": "OK"}
        assert "job" not in reply["result"]

    @pytest.mark.parametrize("line", [
        b"",
        b"not json",
        b'{"id": 1}',
        b'{"method": "login"}',
        b'{"id": "x", "method": "login"}',
        b'{"id": 1, "method": 7}',
        b"[]",
    ])
    def test_rejects_invalid_request(self, line: bytes) -> None:
        # Given / When / Then: decode failures and contract failures both stop cold.
        with pytest.raises((SystemExit, json.JSONDecodeError)):
            _ = pool.response(line)


class TestForegroundProbe:
    """spin.py reports batches per ACTUAL monotonic second."""

    @pytest.mark.parametrize("seconds", [0.0, -1.0, float("nan"), float("inf")])
    def test_rejects_nonpositive_or_nonfinite_duration(self, seconds: float) -> None:
        # Given / When / Then
        with pytest.raises(SystemExit):
            _ = spin.spin(seconds)

    def test_reports_positive_batches_per_actual_second(self) -> None:
        # Given / When
        rate = spin.spin(0.05)
        # Then
        assert rate > 0


class TestWrapper:
    """qa_contention.sh only gates and execs the helper; it never acts itself."""

    @staticmethod
    def _stub(tmp_path: Path) -> dict[str, str]:
        stub = tmp_path / "python3"
        _ = stub.write_text("""#!/usr/bin/env bash
printf 'STUB args=%s\\n' "$*"
printf 'STUB PYTHONPATH=%s\\n' "${PYTHONPATH:-}"
""")
        stub.chmod(0o755)
        environment = dict(os.environ)
        environment["PATH"] = f"{tmp_path}{os.pathsep}{environment.get('PATH', '')}"
        return environment

    @staticmethod
    def _run(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(WRAPPER)], env=environment, timeout=30,
                              capture_output=True, text=True, check=False)

    def test_refuses_before_helper_when_vm_consent_absent(self, tmp_path: Path) -> None:
        # Given
        environment = self._stub(tmp_path)
        _ = environment.pop("SPAREHASH_QA_DISPOSABLE_VM", None)
        # When
        result = self._run(environment)
        # Then: gate closes before any helper launch, whichever gate fires first.
        assert result.returncode != 0
        assert "STUB" not in result.stdout
        expected = ("SPAREHASH_QA_DISPOSABLE_VM=YES" if os.geteuid() == 0 else "root")
        assert expected in result.stderr

    def test_execs_helper_with_repo_and_installed_pythonpath(self, tmp_path: Path) -> None:
        # Given
        environment = self._stub(tmp_path)
        environment["SPAREHASH_QA_DISPOSABLE_VM"] = "YES"
        # When
        result = self._run(environment)
        # Then
        if os.geteuid() != 0:
            assert result.returncode != 0
            assert "root" in result.stderr
            assert "STUB" not in result.stdout
            return
        assert result.returncode == 0
        assert f"STUB args={HELPER}" in result.stdout
        pythonpath = result.stdout.split("STUB PYTHONPATH=", 1)[1].splitlines()[0]
        assert pythonpath.split(":")[:2] == [str(REPO), INSTALLED_SRC]
