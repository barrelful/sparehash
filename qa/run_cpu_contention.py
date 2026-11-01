#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Interactive owner-supervised stopped/mining/stopped CPU measurement. No service control."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing
import os
import re
import select
import signal
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from multiprocessing.connection import wait
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import cpu_contention as benchmark
from contention_safety import (
    UNITS,
    Row,
    SafetyAbort,
    ThermalMonitor,
    Thermometer,
    cpu_information,
    foreground_evidence,
    installed_identity,
    services,
    validate_services,
)

SCHEMA = "sparehash-supervised-cpu-v1"
METHOD = "sha256-input-mib-v1/owned-spawn-synchronised-v1"
REPO = Path(__file__).resolve().parents[1]
MANUAL_STOP = ("This program did NOT stop SpareHash. In your own terminal, verify both services; "
               "if necessary run: sudo systemctl stop sparehash.service\n"
               "Then: systemctl is-active sparehash.service; systemctl is-active sparehash-xmrig.service\n"
               "Both must be inactive. Do not automatically restart mining.")


def interrupted(signum: int, _frame: Any) -> None:
    raise SafetyAbort(f"Benchmark interrupted by signal {signum}")


def worker(connection: Any, gate: Any) -> None:
    """Only this runner's owned children compute; parent alone handles Ctrl+C."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        connection.send({"kind": "boot"})
        while True:
            duration = connection.recv()
            connection.send({"kind": "ready"})
            if not gate.wait(15):
                return
            result = benchmark.work(duration)
            result["affinity"] = sorted(os.sched_getaffinity(0))
            connection.send({"kind": "result", "result": result})
    except (EOFError, BrokenPipeError):
        pass
    except Exception:
        connection.send({"kind": "error"})
    finally:
        connection.close()


class OwnedWorkers:
    def __init__(self, count: int, safety: Callable[[], None], context: Any = None) -> None:
        self.count, self.safety = count, safety
        self.context = context or multiprocessing.get_context("spawn")
        self.gate = self.context.Event()
        self.processes: list[Any] = []
        self.connections: list[Any] = []

    def __enter__(self) -> OwnedWorkers:
        try:
            for index in range(self.count):
                self.safety()
                parent, child = self.context.Pipe()
                self.connections.append(parent)
                process = self.context.Process(target=worker, args=(child, self.gate), name=f"benchmark-cpu-{index}")
                self.processes.append(process)
                try:
                    process.start()
                finally:
                    child.close()
            self.gather("boot", 15)
            return self
        except BaseException:
            self.close()
            raise

    def gather(self, kind: str, seconds: float) -> list[Row]:
        pending = list(self.connections)
        results = []
        deadline = time.monotonic() + seconds
        while pending:
            self.safety()
            if time.monotonic() >= deadline:
                raise SafetyAbort("Benchmark worker deadline exceeded")
            for connection in cast(list[Any], wait(pending, timeout=.05)):
                try:
                    message = connection.recv()
                except (EOFError, OSError) as error:
                    raise SafetyAbort("A benchmark-owned worker disappeared") from error
                if not isinstance(message, dict) or message.get("kind") != kind:
                    raise SafetyAbort("Unexpected benchmark worker response")
                results.append(message)
                pending.remove(connection)
        self.safety()
        return results

    def sample(self, seconds: float) -> Row:
        for connection in self.connections:
            connection.send(seconds)
        self.gather("ready", 10)
        self.safety()
        started = time.monotonic()
        self.gate.set()
        messages = self.gather("result", seconds + 5)
        elapsed = time.monotonic() - started
        self.gate.clear()
        results = [message["result"] for message in messages]
        for row in results:
            if (row.get("policy") != os.SCHED_OTHER or row.get("nice") != 0
                    or not math.isfinite(row["mib"]) or row["mib"] <= 0
                    or row.get("affinity") != sorted(os.sched_getaffinity(0))
                    or not math.isfinite(row["cpu_seconds"]) or row["cpu_seconds"] < 0
                    or not math.isfinite(row["elapsed"])
                    or row["elapsed"] < seconds - .05):
                raise SafetyAbort("Worker scheduling or measurement was unexpected")
        return {"elapsed": elapsed, "mib_per_second": sum(row["mib"] for row in results) / elapsed,
                "workers": results, "started_at": started, "ended_at": time.monotonic()}

    def close(self) -> None:
        # Process objects are exclusively those created above. No PID scans,
        # process-group signals, os.kill, pkill, or service modifications.
        started = [process for process in self.processes if process.pid is not None]
        for process in started:
            if process.is_alive():
                process.terminate()
        deadline = time.monotonic() + 1
        for process in started:
            process.join(timeout=max(0, deadline - time.monotonic()))
        for process in started:
            if process.is_alive():
                process.kill()
        deadline = time.monotonic() + 2
        for process in started:
            process.join(timeout=max(0, deadline - time.monotonic()))
        for connection in self.connections:
            connection.close()
        if any(process.is_alive() for process in started):
            raise SafetyAbort("A benchmark-owned worker could not be reaped; retain this report")

    def __exit__(self, *_args: Any) -> None:
        self.close()


class PhaseSafety:
    def __init__(self, monitor: ThermalMonitor, condition: str, initial: Row,
                 reader: Callable[[], Row] = services) -> None:
        self.monitor, self.condition, self.initial, self.reader = monitor, condition, initial, reader
        self.next_query = 0.0
        self.observations: list[Row] = []
        self.foreground = foreground_evidence()
        self.affinity = sorted(os.sched_getaffinity(0))

    def __call__(self) -> None:
        self.monitor.check()
        if time.monotonic() >= self.next_query:
            current = foreground_evidence()
            for evidence in (current, self.foreground):
                for ancestor in evidence["ancestry"]:
                    ancestor.pop("cpu.stat", None)
            if current != self.foreground or sorted(os.sched_getaffinity(0)) != self.affinity:
                raise SafetyAbort("Foreground scheduling or affinity changed during measurement")
            row = self.reader()
            validate_services(row, self.condition, self.initial)
            self.observations.append({"at": time.monotonic(), "services": row})
            self.next_query = time.monotonic() + .5
        self.monitor.check()


def prompt(message: str, token: str, monitor: ThermalMonitor, timeout: float = 300) -> None:
    instruction = f"Type {token}" if token else "Press Enter"
    print(message + f"\n{instruction} to continue, or anything else to cancel: ", end="", flush=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        monitor.check()
        if select.select([sys.stdin], [], [], .1)[0]:
            if sys.stdin.readline().strip() != token:
                raise SafetyAbort("Owner cancelled or input closed")
            monitor.check()
            return
    raise SafetyAbort("Owner confirmation deadline exceeded")


def run_phase(label: str, parameters: Row, monitor: ThermalMonitor,
              reader: Callable[[], Row] = services, engine: Any = OwnedWorkers) -> Row:
    condition = "mining" if label == "mining" else "stopped"
    initial = reader()
    validate_services(initial, condition)
    safety = PhaseSafety(monitor, condition, initial, reader)
    rows = []
    with engine(parameters["workers"], safety) as pool:
        for index, seconds in enumerate([parameters["warmup"]] + [parameters["seconds"]] * parameters["repeats"]):
            safety()
            offset = max(0, len(monitor.trace()) - 1)
            row = pool.sample(seconds)
            safety()
            trace = monitor.trace()[offset:]
            if not trace:
                raise SafetyAbort("Missing thermal measurement trace")
            row.update(warmup=index == 0, temperature_current_c=trace[-1]["max_c"],
                       temperature_max_observed_c=max(point["max_c"] for point in trace),
                       temperature_observations=len(trace))
            rows.append(row)
            progress = "warm-up" if index == 0 else f"sample {index}/{parameters['repeats']}"
            print(f"{label}: {progress}: "
                  f"{row['mib_per_second']:.2f} MiB/s; CPU {row['temperature_current_c']:.1f} C, "
                  f"max observed {row['temperature_max_observed_c']:.1f} C", flush=True)
    final = reader()
    validate_services(final, condition, initial)
    return {**parameters, "workload": METHOD, "label": label, "samples": rows,
            "median_mib_per_second": statistics.median(row["mib_per_second"] for row in rows[1:]),
            "temperature_max_observed_c": max(row["temperature_max_observed_c"] for row in rows),
            "services_before": initial, "services_after": final, "service_observations": safety.observations}


def session_comparison(session: Row) -> Row:
    phases = session["phases"]
    first = benchmark.compare(phases["baseline_a"], phases["mining"])
    second = benchmark.compare(phases["baseline_b"], phases["mining"])
    drift = 100 * (phases["baseline_b"]["median_mib_per_second"] / phases["baseline_a"]["median_mib_per_second"] - 1)
    warnings = []
    if abs(drift) > 5:
        warnings.append("Stopped baselines differ by more than 5%; background/thermal drift confounds attribution")
    for name, phase in phases.items():
        values = [row["mib_per_second"] for row in phase["samples"][1:]]
        if (max(values) - min(values)) / statistics.median(values) > .1:
            warnings.append(name + " sample spread exceeds 10%; repeatability is poor")
    return {"relative_to_baseline_a": first, "relative_to_baseline_b": second,
            "baseline_b_drift_percent_from_a": drift, "warnings": warnings,
            "verdict": "MEASUREMENT ONLY — no performance acceptance threshold is imposed"}


def compare_sessions(old: Row, new: Row) -> Row:
    for session in (old, new):
        if session.get("schema") != SCHEMA or session.get("status") != "completed":
            raise ValueError("Only completed supervised sessions can be compared")
        for label in ("baseline_a", "mining", "baseline_b"):
            phase = session["phases"][label]
            values = [phase["median_mib_per_second"], phase["temperature_max_observed_c"]]
            values.extend(sample["mib_per_second"] for sample in phase["samples"])
            if not all(type(value) in (int, float) and math.isfinite(value) and value > 0 for value in values):
                raise ValueError("Invalid saved measurements")
        session["comparison"] = session_comparison(session)
    for key in ("parameters", "tool_sha256", "sensor_signature"):
        if old[key] != new[key]:
            raise ValueError("Not like-for-like: " + key)
    for key in ("models", "affinity", "logical_cpus", "physical_cores", "python", "openssl", "architecture"):
        if old["cpu"][key] != new["cpu"][key]:
            raise ValueError("Not like-for-like CPU/software environment: " + key)
    # Session IDs legitimately change on reconnect, but never conflate user.slice
    # with system.slice or different app/service hierarchy placements.
    def normalise(value: str) -> str:
        return re.sub(r"session-[0-9]+\.scope", "session-*.scope", value)
    if normalise(old["foreground"]["membership"]) != normalise(new["foreground"]["membership"]):
        raise ValueError("Foreground cgroup placement differs; do not attribute it to the installed implementation")
    def controls(session: Row) -> list[Row]:
        return [{key: normalise(value) if key == "path" else value for key, value in ancestor.items()
                 if key != "cpu.stat"} for ancestor in session["foreground"]["ancestry"]]
    if controls(old) != controls(new):
        raise ValueError("Foreground ancestor scheduling controls differ")
    changes = {}
    for label in ("baseline_a", "mining", "baseline_b"):
        before, after = old["phases"][label], new["phases"][label]
        changes[label] = {"old_mib_per_second": before["median_mib_per_second"],
                          "new_mib_per_second": after["median_mib_per_second"],
                          "change_percent": 100 * (after["median_mib_per_second"] / before["median_mib_per_second"] - 1),
                          "old_max_observed_c": before["temperature_max_observed_c"],
                          "new_max_observed_c": after["temperature_max_observed_c"]}
    warnings = list(old["comparison"]["warnings"]) + list(new["comparison"]["warnings"])
    if old["cpu"]["kernel"] != new["cpu"]["kernel"]:
        warnings.append("Kernel changed: version attribution is confounded")
    if old["installed"] == new["installed"]:
        warnings.append("No installed implementation change was established")
    if not all(session["installed"].get("available") for session in (old, new)):
        warnings.append("Installed identity unavailable; no version attribution is possible")
    if any(abs(changes[label]["change_percent"]) > 5 for label in ("baseline_a", "baseline_b")):
        warnings.append("Stopped throughput changed by more than 5% between sessions; background drift confounds attribution")
    deltas = {name: new["comparison"][name]["degradation_percent"] - old["comparison"][name]["degradation_percent"]
              for name in ("relative_to_baseline_a", "relative_to_baseline_b")}
    return {"schema": "sparehash-cpu-comparison-v1", "old_session": old["id"], "new_session": new["id"],
            "throughput_changes": changes, "old_comparison": old["comparison"], "new_comparison": new["comparison"],
            "degradation_change_percentage_points": deltas,
            "old_installed": old["installed"], "new_installed": new["installed"],
            "old_miner": old["phases"]["mining"]["services_before"][UNITS[1]],
            "new_miner": new["phases"]["mining"]["services_before"][UNITS[1]],
            "warnings": warnings, "verdict": "MEASUREMENT ONLY; not a causal attribution or acceptance pass"}


def readable_report(session: Row) -> str:
    lines = ["SpareHash supervised CPU measurement — NOT a performance acceptance pass",
             f"Session: {session['id']}   Status: {session['status']}"]
    for label, phase in session.get("phases", {}).items():
        lines.append(f"{label}: {phase['median_mib_per_second']:.2f} MiB/s; "
                     f"maximum observed CPU {phase['temperature_max_observed_c']:.1f} C")
    if "comparison" in session:
        data = session["comparison"]
        for label in ("a", "b"):
            lines.append(f"Degradation relative to baseline {label.upper()}: "
                         f"{data['relative_to_baseline_' + label]['degradation_percent']:.2f}%")
        lines.append(f"Stopped baseline B drift from A: {data['baseline_b_drift_percent_from_a']:+.2f}%")
        lines.extend("WARNING: " + warning for warning in data["warnings"])
    if "mining" in session.get("phases", {}):
        miner = session["phases"]["mining"]["services_before"][UNITS[1]]
        lines.append(f"Miner policy/nice: {miner.get('policy')}/{miner.get('nice')}; "
                     f"cpu.idle={miner['cgroup'].get('cpu.idle')}; cpu.weight={miner['cgroup'].get('cpu.weight')}")
    identity = session.get("installed", {})
    lines.append("Installed public-source fingerprint: " + str(identity.get("fingerprint", "unavailable")))
    lines.append("Matching source commits (file equality, not installation attestation): "
                 + ", ".join(identity.get("matching_source_commits", [])))
    if session.get("error"):
        lines.append("ABORTED: " + session["error"])
    lines.append("Sampled temperatures are not guaranteed hardware peaks; safety monitoring is not hard real-time.")
    lines.append(MANUAL_STOP if session["status"] != "completed" else "Both services were verified inactive at completion; no restart was requested.")
    return "\n".join(lines) + "\n"


def write_json(path: Path, data: Row) -> None:
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as stream:
        temporary = Path(stream.name)
        try:
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def new_directory(base: Path, prefix: str = "session") -> Path:
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    if base.is_symlink() or base.stat().st_uid != os.getuid() or base.stat().st_mode & 0o022:
        raise SafetyAbort("Output directory must be owned by you and not writable by other users")
    name = prefix + "-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12]
    path = base / name
    path.mkdir(mode=0o700)
    return path


def run_session(directory: Path, parameters: Row, *, confirm: Callable[..., None] = prompt,
                phase_runner: Callable[..., Row] = run_phase, reader: Callable[[], Row] = services,
                thermometer: Thermometer | None = None, identity_reader: Callable[[], Row] | None = None) -> Row:
    identity_reader = identity_reader or (lambda: installed_identity(REPO))
    thermal = None
    session: Row = {"schema": SCHEMA, "id": directory.name, "status": "preflight", "phases": {},
                    "started_utc": datetime.now(UTC).isoformat(), "parameters": parameters,
                    "tool_sha256": hashlib.sha256(b"".join((REPO / "qa" / name).read_bytes() for name in
                                                          ("cpu_contention.py", "contention_safety.py", "run_cpu_contention.py"))).hexdigest(),
                    }
    def save() -> None:
        session["thermal_trace"] = thermal.trace() if thermal else []
        write_json(directory / "session.json", session)
        (directory / "report.txt").write_text(readable_report(session), encoding="utf-8")

    try:
        thermal = ThermalMonitor(thermometer or Thermometer(), parameters["abort_c"])
        session.update(cpu=cpu_information(), foreground=foreground_evidence(), installed=identity_reader(),
                       sensors=thermal.thermometer.sensors,
                       sensor_signature=sorted((sensor["driver"], sensor["label"]) for sensor in thermal.thermometer.sensors))
        if parameters["workers"] > len(session["cpu"]["affinity"]):
            raise SafetyAbort("Worker count exceeds current CPU affinity")
        if any(ancestor.get("cpu.idle") == "1" for ancestor in session["foreground"].get("ancestry", [])):
            raise SafetyAbort("Foreground cgroup ancestry is idle scheduled; use a normal-priority session")
        thermal.start()
        initial = reader()
        session["initial_services"] = initial
        print("CPU:", ", ".join(session["cpu"]["models"]))
        print("Physical cores:", session["cpu"]["physical_cores"], "available logical CPUs:", len(session["cpu"]["affinity"]))
        print("Parameters:", json.dumps(parameters))
        print("Temperature sensors:", ", ".join(sensor["driver"] + ":" + sensor["label"] for sensor in session["sensors"]))
        print("Current maximum CPU temperature:", thermal.trace()[-1]["max_c"], "C")
        print("Installed implementation evidence:", json.dumps(session["installed"]))
        print("Service states:", ", ".join(name + "=" + initial[name]["ActiveState"] for name in UNITS))
        instructions = {
            "baseline_a": ("Confirm the plan and that BOTH services are stopped. If needed, manually run "
                           "sudo systemctl stop sparehash.service in your other terminal.", "STOPPED"),
            "mining": ("In your OTHER terminal, manually run sudo systemctl start sparehash.service. "
                       "Press Enter here when ready for an activity check.", ""),
            "baseline_b": ("In your OTHER terminal, manually run sudo systemctl stop sparehash.service. "
                           "Confirm BOTH services are inactive before the second baseline.", "STOPPED"),
        }
        for label in ("baseline_a", "mining", "baseline_b"):
            session["status"] = "awaiting_owner"
            save()
            message, token = instructions[label]
            confirm(message, token, thermal)
            current = reader()
            validate_services(current, "mining" if label == "mining" else "stopped")
            if label == "mining":
                print("Miner active; observed scheduling:", json.dumps(current[UNITS[1]]))
                confirm("Using your existing private monitor, confirm ACTUAL HASHING has stabilised. "
                        "The runner does not query hash rate, wallets or pool APIs.", "HASHING", thermal)
                session["hashing_confirmed_by_owner"] = True
            if identity_reader() != session["installed"]:
                raise SafetyAbort("Installed public-source identity changed during the session")
            session["status"] = "measuring"
            save()
            session["phases"][label] = phase_runner(label, parameters, thermal, reader=reader)
            if identity_reader() != session["installed"]:
                raise SafetyAbort("Installed public-source identity changed during the session")
            save()
        validate_services(reader(), "stopped")
        thermal.check()
        session["comparison"] = session_comparison(session)
        session["status"] = "completed"
    except BaseException as error:
        session["status"] = "aborted"
        session["error"] = ("Owner interrupted the benchmark" if isinstance(error, KeyboardInterrupt) else
                            str(error) if isinstance(error, SafetyAbort) else
                            "Unexpected benchmark failure (" + type(error).__name__ + ")")
    finally:
        if thermal:
            thermal.close()
        save()
    print(readable_report(session))
    print("Saved JSON and report:", directory)
    return session


def compare_saved(base: Path) -> None:
    sessions = []
    for directory in sorted(base.glob("session-*")):
        path = directory / "session.json"
        if directory.is_symlink() or path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 2**20:
            continue
        try:
            data = json.loads(path.read_text())
            if data.get("schema") == SCHEMA and data.get("status") == "completed":
                compare_sessions(data, data)  # Validate saved structure/numbers before presenting it.
                sessions.append(data)
        except (OSError, ValueError, AttributeError, KeyError, TypeError, ZeroDivisionError):
            continue
    if len(sessions) < 2:
        raise SafetyAbort("Two completed sessions are required; interrupted sessions cannot be compared")
    for index, session in enumerate(sessions, 1):
        print(index, session["id"], "installed fingerprint", session["installed"].get("fingerprint", "unknown"))
    try:
        first = int(input("Older session number: ")) - 1
        second = int(input("Newer session number: ")) - 1
        if first == second or min(first, second) < 0:
            raise ValueError
        result = compare_sessions(sessions[first], sessions[second])
    except (ValueError, IndexError, KeyError, TypeError, ZeroDivisionError) as error:
        raise SafetyAbort("Comparison refused: selections or like-for-like parameters differ") from error
    directory = new_directory(base, "comparison")
    write_json(directory / "comparison.json", result)
    lines = ["SpareHash session comparison — MEASUREMENT ONLY", result["old_session"] + " → " + result["new_session"]]
    for label, row in result["throughput_changes"].items():
        lines.append(f"{label}: {row['old_mib_per_second']:.2f} → {row['new_mib_per_second']:.2f} MiB/s "
                     f"({row['change_percent']:+.2f}%); max CPU {row['old_max_observed_c']:.1f} → {row['new_max_observed_c']:.1f} C")
    for name in ("old_comparison", "new_comparison"):
        row = result[name]
        lines.append(f"{name}: degradation vs A {row['relative_to_baseline_a']['degradation_percent']:.2f}%, "
                     f"vs B {row['relative_to_baseline_b']['degradation_percent']:.2f}%; "
                     f"baseline drift {row['baseline_b_drift_percent_from_a']:+.2f}%")
    for name in ("old_miner", "new_miner"):
        row = result[name]
        lines.append(f"{name}: policy/nice {row.get('policy')}/{row.get('nice')}; "
                     f"cpu.idle={row['cgroup'].get('cpu.idle')}; cpu.weight={row['cgroup'].get('cpu.weight')}")
    for name in ("old_installed", "new_installed"):
        identity = result[name]
        lines.append(f"{name}: public-source fingerprint {identity.get('fingerprint', 'unavailable')}; "
                     f"matching source commits {', '.join(identity.get('matching_source_commits', [])) or 'unknown'} "
                     "(file equality only, not installation attestation)")
    for name, delta in result["degradation_change_percentage_points"].items():
        lines.append(f"Degradation change {name}: {delta:+.2f} percentage points (negative is improvement)")
    lines.extend("WARNING: " + warning for warning in result["warnings"])
    lines.append(result["verdict"])
    text = "\n".join(lines) + "\n"
    (directory / "report.txt").write_text(text)
    print(text, "Saved comparison:", directory)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, help="default: min(24, available CPU affinity)")
    parser.add_argument("--seconds", type=float, default=4)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmup", type=float, default=1)
    parser.add_argument("--abort-c", type=float, default=80, help="thermal abort threshold, 40..80 C")
    parser.add_argument("--output-dir", type=Path, default=Path.home() / "sparehash-contention-results")
    parser.add_argument("--compare", action="store_true", help="select two completed sessions; no workload or host probes")
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error("run as an ordinary user, never sudo")
    try:
        if args.compare:
            compare_saved(args.output_dir)
            return 0
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise SafetyAbort("An interactive terminal is required (use an SSH PTY)")
        action = input("Enter to begin a NEW supervised session, or type COMPARE to compare saved sessions: ").strip()
        if action == "COMPARE":
            compare_saved(args.output_dir)
            return 0
        if action:
            raise SafetyAbort("Owner cancelled")
        if os.sched_getscheduler(0) != os.SCHED_OTHER or os.getpriority(os.PRIO_PROCESS, 0) != 0:
            raise SafetyAbort("Foreground runner must use SCHED_OTHER/nice 0")
        available = len(os.sched_getaffinity(0))
        workers = args.workers if args.workers is not None else min(24, available)
        if not (1 <= workers <= min(24, available) and .5 <= args.seconds <= 10 and 2 <= args.repeats <= 7
                and .5 <= args.warmup <= 3 and 40 <= args.abort_c <= 80):
            raise SafetyAbort("Invalid bounded parameters: workers within affinity/24, seconds .5..10, repeats 2..7, warmup .5..3, abort 40..80 C")
        parameters = {"workers": workers, "seconds": args.seconds, "repeats": args.repeats,
                      "warmup": args.warmup, "abort_c": args.abort_c,
                      "thermal_interval": ThermalMonitor.interval, "service_interval": .5}
        print("MEASUREMENT ONLY. This runner never starts/stops/pauses/reconfigures mining. Ctrl+C aborts its workers only.")
        directory = new_directory(args.output_dir)
        previous = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGHUP)}
        try:
            session = run_session(directory, parameters)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        if session["status"] != "completed":
            print(MANUAL_STOP, file=sys.stderr)
            return 1
        if input("Compare two completed saved sessions now? Type COMPARE, or Enter to finish: ").strip() == "COMPARE":
            compare_saved(args.output_dir)
        return 0
    except (SafetyAbort, OSError, KeyboardInterrupt, EOFError) as error:
        print("Benchmark not completed:", str(error) if isinstance(error, SafetyAbort) else type(error).__name__, file=sys.stderr)
        print(MANUAL_STOP, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
