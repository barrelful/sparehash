#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# sudo qa/qa_contention.sh (disposable systemd VM only)
"""Fail-closed contention acceptance using real controller and live workloads."""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from sparehash.xmrig_config import ApiError, read_token, summary

MINER: Final = "sparehash-xmrig.service"
CLI: Final = "/usr/local/bin/sparehash"
CPUS: Final = len(os.sched_getaffinity(0))
HZ: Final = int(os.sysconf("SC_CLK_TCK"))


class CheckFailure(Exception):
    """Acceptance or evidence failure with a diagnostic reason."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise CheckFailure(reason)


@dataclass(frozen=True, slots=True)
class Sample:
    pid: int
    start_ticks: int
    cpu_ticks: int
    at: float


@dataclass(frozen=True, slots=True)
class CpuScale:
    cpus: int
    hz: int


def parse_stat(raw: str, at: float) -> Sample:
    """Parse after FINAL ')' (comm can contain spaces and parentheses)."""
    head, separator, tail = raw.rpartition(")")
    require(bool(separator) and "(" in head, "malformed proc stat")
    fields = tail.split()
    require(len(fields) >= 20, "missing proc stat fields")
    try:
        pid = int(head.split("(", 1)[0].strip())
        ticks = int(fields[11]) + int(fields[12])  # fields 14/15, not cmajflt
        start = int(fields[19])  # field 22 detects PID reuse
    except ValueError as error:
        raise CheckFailure("nonnumeric proc stat fields") from error
    require(pid > 0 and ticks >= 0 and start >= 0, "invalid proc counters")
    require(fields[0] not in {"Z", "X", "x"}, "miner process exited")
    return Sample(pid, start, ticks, at)


def cpu_percent(before: Sample, after: Sample, scale: CpuScale) -> float:
    require((before.pid, before.start_ticks) == (after.pid, after.start_ticks),
            "miner PID changed or was reused")
    elapsed = after.at - before.at
    delta = after.cpu_ticks - before.cpu_ticks
    require(math.isfinite(elapsed) and elapsed > 0 and delta >= 0,
            "invalid CPU sampling interval or counter regression")
    require(scale.cpus > 0 and scale.hz > 0, "invalid CPU normalization")
    return delta * 100 / (elapsed * scale.hz * scale.cpus)


def command(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True,
                          timeout=10).stdout.strip()


def snapshot() -> Sample:
    require(command("systemctl", "is-active", MINER) == "active", "miner inactive")
    pid = int(command("systemctl", "show", MINER, "-p", "MainPID", "--value"))
    require(pid > 0, "missing miner MainPID")
    result = parse_stat(Path(f"/proc/{pid}/stat").read_text(), time.monotonic())
    require(result.pid == pid, "proc PID mismatch")
    return result


def check_yield(percent: float) -> None:
    require(math.isfinite(percent) and 0 <= percent <= 10,
            f"miner CPU {percent:.3f}% exceeds 10% during load")


def check_throughput(baseline: float, under: float) -> float:
    require(math.isfinite(baseline) and baseline > 0 and math.isfinite(under) and under > 0,
            "missing or invalid foreground throughput")
    degradation = (baseline - under) / baseline * 100
    require(degradation <= 25, f"foreground degradation {degradation:.3f}% exceeds 25%")
    return degradation


def require_overlap(stress: subprocess.Popen[str], foreground: subprocess.Popen[str]) -> None:
    """A completed foreground result is valid only while stress is still alive."""
    require(stress.poll() is None, "stress ended before foreground measurement completed")
    require(foreground.poll() in {None, 0}, "foreground worker failed")


@dataclass(frozen=True, slots=True)
class Workload:
    port: int
    token: str

    def hashes(self) -> int:
        result = summary("127.0.0.1", self.port, self.token, timeout=1)
        match result.get("results"):
            case {"hashes_total": int(total)} if total >= 0:
                return total
            case _:
                raise CheckFailure("API missing results.hashes_total workload evidence")


def safe_workload() -> Workload:
    """Reject external pools/donation BEFORE invoking the controller."""
    raw = json.loads(Path("/etc/sparehash/xmrig.json").read_text())
    fallback = json.loads(Path("/etc/sparehash/xmrig.runtime.json").read_text())
    for document in (raw, fallback):
        require(document.get("donate-level") == 0, "QA requires donate-level=0 in both miner JSON files")
        pools = document.get("pools")
        require(bool(pools) and all(pool.get("url") == "127.0.0.1:3333"
                                  and pool.get("tls") is False for pool in pools),
                "QA requires exclusively 127.0.0.1:3333 non-TLS pools")
    match raw.get("http"):
        case {"enabled": True, "host": "127.0.0.1", "port": int(port)} if 1 <= port <= 65535:
            return Workload(port, read_token())
        case _:
            raise CheckFailure("QA requires loopback miner API")


def wait_active() -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        result = subprocess.run(("systemctl", "is-active", "--quiet", MINER), timeout=10)
        if result.returncode == 0:
            return
        time.sleep(1)
    raise CheckFailure("miner did not become active through controller within 120s")


def recover(work: Workload, deadline: float, identity: Sample) -> None:
    """Require both real hash progress and >=20% CPU before a monotonic deadline."""
    before = snapshot()
    _ = cpu_percent(identity, before, CpuScale(CPUS, HZ))
    hashes = work.hashes()
    while time.monotonic() + 1 < deadline:
        time.sleep(1)
        after = snapshot()
        percent = cpu_percent(before, after, CpuScale(CPUS, HZ))
        progress = work.hashes()
        now = time.monotonic()
        print(f"recovery CPU={percent:.3f}% hashes={progress}", flush=True)
        if percent >= 20 and progress > hashes and now <= deadline:
            return
        before, hashes = after, progress
    raise CheckFailure("actual hashing did not recover to >=20% CPU by deadline")


def load_phase(miner: Sample | None) -> tuple[float, float]:
    """Matched 60s full-CPU runs; same 40s foreground probe overlaps each."""
    with subprocess.Popen(("stress-ng", "--cpu", str(CPUS), "--cpu-method", "loop",
                           "--timeout", "60s"), text=True, start_new_session=True) as stress:
        try:
            started = time.monotonic()
            time.sleep(3)  # identical startup settling in both arms
            with subprocess.Popen((sys.executable, str(Path(__file__).with_name("spin.py")), "40"),
                                  stdout=subprocess.PIPE, text=True) as foreground:
                try:
                    require_overlap(stress, foreground)
                    before = snapshot() if miner is not None else None
                    if before is not None and miner is not None:
                        _ = cpu_percent(miner, before, CpuScale(CPUS, HZ))
                    while time.monotonic() - started < 57:
                        require_overlap(stress, foreground)
                        time.sleep(1)
                        require_overlap(stress, foreground)
                        if before is not None:
                            after = snapshot()
                            percent = cpu_percent(before, after, CpuScale(CPUS, HZ))
                            check_yield(percent)
                            print(f"load CPU={percent:.3f}%", flush=True)
                            before = after
                        else:
                            require(command("systemctl", "show", MINER, "-p", "MainPID", "--value") == "0",
                                    "miner running in stress-only baseline")
                    output, _ = foreground.communicate(timeout=2)
                    require_overlap(stress, foreground)
                    throughput = float(output.strip())
                finally:
                    if foreground.poll() is None:
                        foreground.terminate()
                        foreground.wait(timeout=5)
            require(stress.wait(timeout=10) == 0, "stress-ng failed")
            ended = time.monotonic()
            require(ended - started >= 60, "stress-ng exited before full 60s load")
            # Conservative deadline: delayed observation of exit grants no extra recovery time.
            return throughput, started + 60
        finally:
            if stress.poll() is None:
                os.killpg(stress.pid, 15)
                stress.wait(timeout=5)


def main() -> int:
    try:
        work = safe_workload()
        command(CLI, "start")
        command(CLI, "pause")
        try:
            deadline = time.monotonic() + 30
            while command("systemctl", "show", MINER, "-p", "MainPID", "--value") != "0":
                require(time.monotonic() < deadline, "controller did not stop miner for baseline")
                time.sleep(1)
            baseline, _ = load_phase(None)
        finally:
            command(CLI, "resume")
        wait_active()
        identity = snapshot()
        recover(work, time.monotonic() + 120, identity)  # exclude RandomX initialization
        under, ended = load_phase(identity)
        recover(work, ended + 30, identity)
        degradation = check_throughput(baseline, under)
        print(f"PASS: stress-only={baseline:.3f}/s stress+miner={under:.3f}/s degradation={degradation:.3f}%")
        return 0
    except (CheckFailure, ApiError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
