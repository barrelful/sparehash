#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Owner-invoked, unprivileged foreground CPU comparison. Never controls mining."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import statistics
import subprocess
import time
from pathlib import Path


def work(seconds: float) -> dict:
    """Fixed-size standard-library computation; units are MiB SHA-256 input/s."""
    started = time.monotonic()
    cpu = time.process_time()
    count = 0
    data = b"sparehash foreground benchmark\0" * 32768
    deadline = started + seconds
    while time.monotonic() < deadline:
        hashlib.sha256(data).digest()
        count += 1
    return {"mib": count * len(data) / 2**20, "elapsed": time.monotonic() - started,
            "cpu_seconds": time.process_time() - cpu, "policy": os.sched_getscheduler(0),
            "nice": os.getpriority(os.PRIO_PROCESS, 0), "pid": os.getpid(),
            "cgroup": Path("/proc/self/cgroup").read_text()}


def temperatures() -> list[dict]:
    rows = []
    for name in sorted(Path("/sys/class/hwmon").glob("hwmon*/name")):
        try:
            driver = name.read_text().strip()
            if driver not in ("k10temp", "zenpower", "coretemp"):
                continue
            for sensor in sorted(name.parent.glob("temp*_input")):
                rows.append({"driver": driver, "sensor": sensor.name,
                             "celsius": int(sensor.read_text()) / 1000})
        except (OSError, ValueError):
            continue
    return rows


def service_evidence() -> dict:
    # Only safe service properties; never configuration, command lines or logs.
    proc = subprocess.run(["systemctl", "show", "sparehash.service", "sparehash-xmrig.service",
                           "--property=Id,ActiveState,SubState,MainPID"],
                          capture_output=True, text=True, timeout=5)
    scheduling = []
    for line in proc.stdout.splitlines():
        if line.startswith("MainPID=") and line[8:].isdigit() and int(line[8:]) > 0:
            pid = int(line[8:])
            try:
                scheduling.append({"pid": pid, "policy": os.sched_getscheduler(pid),
                                   "nice": os.getpriority(os.PRIO_PROCESS, pid)})
            except OSError:
                scheduling.append({"pid": pid, "unavailable": True})
    return {"properties": proc.stdout, "exit": proc.returncode, "scheduling": scheduling}


def measure(workers: int, seconds: float, repeats: int, warmup: float) -> dict:
    if os.geteuid() == 0 or os.sched_getscheduler(0) != os.SCHED_OTHER or os.getpriority(os.PRIO_PROCESS, 0) != 0:
        raise ValueError("run as an ordinary user at SCHED_OTHER/nice 0, not sudo")
    rows = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
        for index, duration in enumerate([warmup] + [seconds] * repeats):
            before = temperatures()
            start = time.monotonic()
            results = list(pool.map(work, [duration] * workers))
            elapsed = time.monotonic() - start
            rows.append({"warmup": index == 0, "elapsed": elapsed,
                         "mib_per_second": sum(row["mib"] for row in results) / elapsed,
                         "workers": results, "temperatures_before": before,
                         "temperatures_after": temperatures()})
    return {"workload": "sha256-input-mib-v1", "workers": workers, "seconds": seconds,
            "repeats": repeats, "warmup": warmup, "samples": rows,
            "median_mib_per_second": statistics.median(row["mib_per_second"] for row in rows[1:])}


def compare(baseline: dict, mining: dict) -> dict:
    for key in ("workload", "workers", "seconds", "repeats", "warmup"):
        if baseline[key] != mining[key]:
            raise ValueError("comparison parameters differ: " + key)
    base, active = baseline["median_mib_per_second"], mining["median_mib_per_second"]
    return {"baseline_mib_per_second": base, "mining_mib_per_second": active,
            "degradation_percent": 100 * (base - active) / base}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", choices=("stopped", "mining", "paused"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workers", type=int, default=min(len(os.sched_getaffinity(0)), 32))
    parser.add_argument("--seconds", type=float, default=4)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmup", type=float, default=1)
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("BASELINE", "MINING"))
    args = parser.parse_args()
    if args.compare:
        print(json.dumps(compare(*(json.loads(path.read_text()) for path in args.compare)), indent=2))
        return
    if not args.label or not args.output:
        parser.error("--label and --output are required (or use --compare)")
    if not (1 <= args.workers <= 32 and 0.5 <= args.seconds <= 10 and 2 <= args.repeats <= 7 and 0.5 <= args.warmup <= 3):
        parser.error("bounded limits: workers 1..32, seconds .5..10, repeats 2..7, warmup .5..3")
    # Reserve the filename before doing any work; never overwrite earlier evidence.
    with args.output.open("x") as output:
        before = service_evidence()
        result = measure(args.workers, args.seconds, args.repeats, args.warmup)
        result.update({"label": args.label, "services_before": before,
                       "services_after": service_evidence()})
        json.dump(result, output, indent=2)
    print(json.dumps({"output": str(args.output), "median_mib_per_second": result["median_mib_per_second"]}))


if __name__ == "__main__":
    main()
