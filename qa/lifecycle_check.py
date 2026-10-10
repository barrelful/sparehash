#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# Run via qa_controller_lifecycle.sh in the parent's disposable systemd VM.
"""Assertions for lifecycle acceptance; never treat unavailable IPC as approval."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from sparehash import config, ipc

CONTROLLER: Final = "sparehash.service"
MINER: Final = "sparehash-xmrig.service"
CGROUP: Final = Path("/sys/fs/cgroup")
SPEC: Final = Path("/run/sparehash-miner/operation.json")


def load_json(source: str, decoder: Callable[[str], ipc.Json] = json.loads) -> ipc.Json:
    return decoder(source)


LOAD_JSON: Final = load_json


@dataclass(frozen=True, slots=True)
class LifecycleError(Exception):
    detail: str


@dataclass(frozen=True, slots=True)
class Status:
    generation: int
    blockers: tuple[str, ...]
    debug: tuple[str, ...]
    running: bool


def strings(value: ipc.Json) -> tuple[str, ...]:
    match value:
        case list() as items if all(isinstance(item, str) for item in items):
            return tuple(str(item) for item in items)
        case _:
            raise LifecycleError("invalid controller blocker list")


def exchange(command: str, blocker: str | None = None) -> ipc.Document:
    request: ipc.Document = {"cmd": command}
    if blocker is not None:
        request["blocker"] = blocker
    response = ipc.call(request, timeout=1)
    if response.get("ok") is not True:
        raise LifecycleError(f"IPC rejected {command}: {response}")
    return response


def status() -> Status:
    match exchange("status"):
        case {"ready": True, "generation": int() as generation,
              "blockers": blockers, "debug": debug, "running": bool() as running}:
            return Status(generation, strings(blockers), strings(debug), running)
        case _:
            raise LifecycleError("controller not ready or malformed status")


def prop(unit: str, name: str) -> str:
    return subprocess.run(
        ["systemctl", "show", "-p", name, "--value", unit],
        check=True, capture_output=True, text=True, timeout=2,
    ).stdout.strip()


def wait_until(predicate: Callable[[], bool], seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while True:
        success = predicate()
        now = time.monotonic()
        if success and now <= deadline:
            return
        if now >= deadline:
            raise LifecycleError(f"condition not observed within {seconds}s")
        time.sleep(min(0.1, deadline - now))


def miner_off() -> bool:
    return prop(MINER, "ActiveState") in ("inactive", "failed") and prop(MINER, "MainPID") == "0"


def baseline() -> bool:
    current = status()
    return (current.running and not current.blockers and not current.debug
            and prop(MINER, "ActiveState") == "active" and int(prop(MINER, "MainPID")) > 0)


def blocked(name: str) -> bool:
    current = status()
    return name in current.blockers and not current.running and miner_off()


def tasks(group: Path) -> tuple[int, ...]:
    """Absent recorded cgroups are empty; inspect every surviving descendant."""
    result: list[int] = []
    for path in (group / "cgroup.procs", *group.glob("**/cgroup.procs")):
        try:
            result.extend(int(value) for value in path.read_text().split())
        except FileNotFoundError:
            continue  # systemd may remove the captured group during this walk.
    return tuple(result)


def process_birth(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except FileNotFoundError:
        return None


def loss() -> None:
    """Observe cleanup before RestartSec, then a new systemd invocation's recovery."""
    if not baseline():
        raise LifecycleError("SIGKILL requires active mining baseline")
    pid, miner_pid = int(prop(CONTROLLER, "MainPID")), int(prop(MINER, "MainPID"))
    invocation, generation = prop(CONTROLLER, "InvocationID"), status().generation
    control_group = prop(MINER, "ControlGroup")
    if pid <= 0 or not invocation or not control_group.startswith("/") or control_group == "/":
        raise LifecycleError("missing pre-death controller identity/miner ControlGroup")
    group = CGROUP / control_group.lstrip("/")
    old_tasks = tuple((task, process_birth(task)) for task in tasks(group))
    if not (group / "cgroup.procs").is_file() or miner_pid not in dict(old_tasks):
        raise LifecycleError("active miner has no captured cgroup membership")
    stamp = SPEC.stat().st_mtime_ns
    print(f"before death: controller={pid} invocation={invocation} generation={generation} " +
          f"miner={miner_pid} ControlGroup={control_group} tasks={old_tasks}", flush=True)
    started = time.monotonic()
    os.kill(pid, signal.SIGKILL)
    wait_until(lambda: not tasks(group) and all(process_birth(task) != birth
               for task, birth in old_tasks), max(0, 5 - (time.monotonic() - started)))
    print(f"old miner empty after {time.monotonic() - started:.3f}s", flush=True)
    wait_until(lambda: prop(CONTROLLER, "ActiveState") == "active"
               and int(prop(CONTROLLER, "MainPID")) not in (0, pid)
               and prop(CONTROLLER, "InvocationID") not in ("", invocation), 40)
    wait_until(baseline, 90)
    current = status()
    operation = LOAD_JSON(SPEC.read_text())
    match operation:
        case {"generation": int() as published, "mode": "mine"}:
            current_operation = published == current.generation
        case _:
            current_operation = False
    if (int(prop(MINER, "MainPID")) == miner_pid or SPEC.stat().st_mtime_ns <= stamp
            or not current_operation):
        raise LifecycleError("miner recovery lacks new PID/current freshly published generation")
    # Numeric generation is process-local and can repeat across invocations.
    print(f"recovery: controller={prop(CONTROLLER, 'MainPID')} " +
          f"invocation={prop(CONTROLLER, 'InvocationID')} generation={current.generation} " +
          f"miner={prop(MINER, 'MainPID')}")


def hold(name: str) -> None:
    deadline = time.monotonic() + config.load().controller.min_dwell_seconds + 5
    while True:
        if not blocked(name):
            raise LifecycleError(f"{name} failed to inhibit mining beyond dwell")
        if time.monotonic() >= deadline:
            return
        time.sleep(0.25)


def main(args: list[str]) -> None:
    match args:
        case ["ipc", command, *names] if len(names) <= 1:
            print(exchange(command, names[0] if names else None))
        case ["baseline"]:
            wait_until(baseline, 90)
        case ["blocked", name, *rest]:
            wait_until(lambda: blocked(name), float(rest[0]) if rest else 15)
        case ["debug", name]:
            wait_until(lambda: name in status().debug and miner_off(), 5)
            if name != "manual" and name not in status().blockers:
                raise LifecycleError("debug safety blocker missing from status registry")
        case ["hold", name]:
            hold(name)
        case ["off"]:
            wait_until(lambda: prop(CONTROLLER, "ActiveState") == "inactive" and miner_off(), 5)
        case ["denied"]:
            if not miner_off() or not re.search(r"status=1(?:\D|$)", prop(MINER, "ExecCondition")):
                raise LifecycleError("direct miner start was not denied by ExecCondition")
        case ["loss"]:
            loss()
        case ["unready"]:
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                if prop(CONTROLLER, "ActiveState") == "active" or not miner_off():
                    raise LifecycleError("failed initial readiness allowed controller/miner activation")
                time.sleep(0.1)
            if prop(CONTROLLER, "ExecMainStatus") != "1":
                raise LifecycleError("readiness failure did not reach controller config loading")
        case _:
            raise LifecycleError(f"unknown lifecycle check: {args}")


if __name__ == "__main__":
    main(sys.argv[1:])
