# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Production controller assembly: safety callbacks outrank IPC/job completions."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from functools import partial
from pathlib import Path
from queue import Empty, Queue
from types import FrameType
from typing import Final, final

from . import config, deployment_spec, gaming, ipc, power, systemd, thermal
from .controller import Controller
from .controller_io import Generation, Observation, Ports, SampleId
from .deployment import Tree, checked
from .deployment_spec import DeploymentSpec, DeploymentSpecError
from .gaming_scan import ScanResult, scan_steam_games
from .hugepages import LeaseManager
from .paths import STATE_FILE
from .runtime_jobs import Jobs, empty
from .runtime_spec import DIRECTORY, SPEC
from .state import BlockerState

LOGGER: Final = logging.getLogger(__name__)
GAMING_POLL_SECONDS: Final = 1.0
SHUTDOWN_WORKER_GRACE_SECONDS: Final = 12.0
PRODUCTION_ROOT: Final = Path("/")


def _discard_gaming(_result: ScanResult, _epoch: int) -> None:
    return


@final
class Providers:
    def __init__(self, cfg: config.Config, enqueue: Callable[[Callable[[], None]], None], *,
                 gaming_scan: Callable[[], ScanResult] = scan_steam_games,
                 gaming_sink: Callable[[ScanResult, int], None] | None = None) -> None:
        self.cfg, self.enqueue = cfg, enqueue
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="observation")
        self.gaming_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gaming-guard")
        self.queries = ThreadPoolExecutor(max_workers=8, thread_name_prefix="guard-query")
        self.monitor = power.PowerMonitor()
        self.guard = thermal.ThermalGuard(cfg.thermal.enabled, cfg.thermal.pause_c, cfg.thermal.resume_c,
                                          thermal.find_sensor(Path("/sys/class/hwmon")))
        self.guard_lock = threading.Lock()
        self.users = tuple(gaming.User.from_name(name) for name in cfg.gaming.users) if cfg.gaming.enabled else ()
        self.finish: Callable[[Observation], None] = lambda _: None
        self.gaming_enabled = cfg.gaming.enabled
        self.gaming_result: ScanResult | None = None
        self.gaming_scan = gaming_scan
        self.gaming_busy = False
        self.gaming_sink = gaming_sink if gaming_sink is not None else _discard_gaming

    @property
    def gaming_blocked(self) -> bool:
        result = self.gaming_result
        return self.gaming_enabled and (result is None or result.decision != "CLEAR"
                                       or not result.fresh(time.monotonic()))

    def temperature(self) -> bool:
        with self.guard_lock:
            return self.guard.poll().active

    def request(self, sample: SampleId, deadline: float) -> None:
        _ = self.worker.submit(self.collect, sample, deadline)

    def request_gaming(self, epoch: int) -> None:
        """Dedicated bounded priority query; one in flight, never on the owner loop."""
        if self.gaming_busy:
            return
        self.gaming_busy = True
        _ = self.gaming_worker.submit(self.collect_gaming, epoch)

    def collect_gaming(self, epoch: int) -> None:
        """One bounded best-effort scan, never on the controller owner loop."""
        try:
            started = time.monotonic()
            if not self.gaming_enabled:
                result = ScanResult("CLEAR", started, reason="disabled")
            else:
                try:
                    result = self.gaming_scan()
                except Exception:
                    # Do not log exception bytes from process-controlled input.
                    result = ScanResult("UNKNOWN", started, reason="scanner_failed")
            self.gaming_result = result
            self.gaming_sink(result, epoch)
        finally:
            self.gaming_busy = False

    def collect(self, sample: SampleId, deadline: float) -> None:
        at = time.monotonic()
        inventory, sysfs, ac_online, discharge_rate_w = power.read_sysfs(Path("/sys"), at)
        upower_future = self.monitor.backend.query()
        thermal_future = self.queries.submit(self.temperature)
        try:
            on_battery = upower_future.result(timeout=max(0.001, deadline - time.monotonic()))
        except TimeoutError:
            on_battery = None
        try:
            hot = thermal_future.result(timeout=max(0.001, deadline - time.monotonic()))
        except TimeoutError:
            hot = None
        now = time.monotonic()
        fresh = 0 <= now - at <= 10
        observation = Observation(sample, now, inventory.credible(now), on_battery if fresh else None,
                                  sysfs.credible(now), (), hot,
                                  ac_online if fresh else None, discharge_rate_w if fresh else None,
                                  self.cfg.power.maintenance_mode, self.gaming_blocked)
        self.enqueue(lambda: self.finish(observation))

    def close(self) -> None:
        self.worker.shutdown(wait=True, cancel_futures=True)
        self.gaming_worker.shutdown(wait=True, cancel_futures=True)
        self.queries.shutdown(wait=True, cancel_futures=True)


@final
class Lifecycle:
    def __init__(self, bound: threading.Event, notify: str) -> None:
        self.bound, self.notify = bound, notify

    def bind(self) -> None:
        if not self.bound.is_set():
            raise OSError("protected IPC has not bound")

    def ready(self) -> None:
        systemd.notify_ready(self.notify)


def validate_deployment(spec: DeploymentSpec, cfg: config.Config, root: Path = PRODUCTION_ROOT) -> None:
    """Refuse a description whose payload drifted or names another miner binary.

    The inventory digest must match the deployed payload and every declared
    runtime path must agree with the configured miner, so an externally
    provisioned fixture runs the same controller contract without installer
    receipts or mutation authority.
    """
    deployment_spec.validate(spec, root)
    declared = spec.runtime_paths.get("xmrig_binary") or spec.runtime_paths.get("binary")
    if declared is not None and str(declared) != cfg.xmrig.binary:
        raise DeploymentSpecError("runtime_paths", "configured miner binary differs from the deployment description")


def initialize(state: BlockerState, ports: Ports, *,
               deployment_spec: DeploymentSpec | None = None,
               cfg: config.Config | None = None,
               root: Path = PRODUCTION_ROOT) -> Controller:
    if deployment_spec is not None:
        if cfg is None:
            raise DeploymentSpecError("config", "deployment validation requires the loaded configuration")
        validate_deployment(deployment_spec, cfg, root)
    owner = Controller(state, ports)
    owner.start()
    return owner


def cleanup() -> int:
    if os.geteuid() != 0 or not empty():
        return 1
    result = LeaseManager(empty_proof=empty).cleanup()
    try:
        with ExitStack() as stack:
            parent = Tree().pin(stack, DIRECTORY)
            try:
                os.unlink(Path(SPEC).name, dir_fd=parent)
            except FileNotFoundError:
                pass
            os.fsync(parent)
    except FileNotFoundError:
        pass
    return 1 if result.warning else 0


def _close_workers(jobs: Jobs, providers: Providers, threads: tuple[threading.Thread, ...]) -> None:
    """Bound teardown without trusting observation reads to return.

    Executor shutdown(wait=False) still joins running threads at interpreter
    exit. A stuck read therefore needs a process-exit fallback, not just a
    nonblocking shutdown call. The owner has already revoked launch authority
    and the caller has attempted the independent miner stop. On failure leave
    protected cleanup to the existing systemd ExecStopPost, which rechecks empty
    after this process (and any in-flight Jobs) is gone.
    """
    closed, failed = threading.Event(), threading.Event()

    def close() -> None:
        try:
            jobs.close()
            providers.close()
            for thread in threads:
                if thread.ident is not None:
                    thread.join()
        except BaseException:
            failed.set()
        finally:
            closed.set()

    threading.Thread(target=close, name="controller-teardown", daemon=True).start()
    if not closed.wait(SHUTDOWN_WORKER_GRACE_SECONDS) or failed.is_set():
        LOGGER.error("controller worker shutdown incomplete; exiting for systemd cleanup")
        os._exit(1)  # Do not enter ThreadPoolExecutor's unbounded interpreter join.


def run(state_path: Path = STATE_FILE) -> int:
    if os.geteuid() != 0:
        return 1
    _ = os.environ.pop("SPAREHASH_RUN_DIR", None)
    _ = os.environ.pop("SPAREHASH_HWMON_ROOT", None)
    _ = os.environ.pop("SPAREHASH_STATE_FILE", None)
    notify_socket = os.environ.get("NOTIFY_SOCKET")
    if not notify_socket:
        print("sparehash: controller must be invoked by systemd (NOTIFY_SOCKET missing)", file=sys.stderr)
        return 1
    cfg = config.load()
    safety: Queue[Callable[[], None]] = Queue()
    messages: Queue[Callable[[], None]] = Queue()
    stop, bound = threading.Event(), threading.Event()
    providers = Providers(cfg, messages.put)
    jobs = Jobs(cfg, messages.put)
    state = BlockerState(Path(os.environ.get("SPAREHASH_STATE_FILE", str(state_path))),
                         min_dwell_seconds=cfg.controller.min_dwell_seconds)
    owner = Controller(state, Ports(providers, jobs, Lifecycle(bound, notify_socket), time.monotonic),
                       gaming_enabled=cfg.gaming.enabled)
    providers.finish, jobs.finish = owner.observe, owner.complete
    jobs.finished = owner.benchmark_finished
    jobs.authorize = lambda: owner.authorize(consume=False)
    providers.gaming_sink = (lambda result, epoch: safety.put(
        partial(owner.gaming_sample, result, Generation(epoch))))
    failures: list[str] = []

    def handle(request: ipc.Request, peer: ipc.Peer) -> ipc.Document:
        done = threading.Event()
        result: list[ipc.Document] = []
        def deliver() -> None:
            result.append(owner.handle(request, peer))
            done.set()
        messages.put(deliver)
        return result[0] if done.wait(0.75) else {"ok": False, "error": "owner unavailable"}

    def serve() -> None:
        try:
            ipc.serve("/run/sparehash/control.sock", handle, stop, bound=bound)
        except ipc.IPCError as error:
            failures.append(str(error))
            stop.set()

    def emit(sample: power.PowerSample) -> None:
        safety.put(lambda: owner.safety("battery", power.blocked(sample, maintenance_mode=cfg.power.maintenance_mode)))

    def temperatures() -> None:
        prior: bool | None = None
        while not stop.is_set():
            hot = providers.temperature()
            if hot != prior:
                safety.put(partial(owner.safety, "thermal", hot))
                prior = hot
            messages.put(owner.refresh)
            _ = stop.wait(5)

    def gaming() -> None:
        while not stop.is_set():
            providers.request_gaming(int(owner.generation))
            _ = stop.wait(GAMING_POLL_SECONDS)

    def terminate(_number: int, _frame: FrameType | None) -> None:
        stop.set()

    threads = (threading.Thread(target=serve, daemon=True),
               threading.Thread(target=providers.monitor.run, args=(stop, emit), daemon=True),
               threading.Thread(target=temperatures, daemon=True),
               threading.Thread(target=gaming, daemon=True))
    old_term = signal.signal(signal.SIGTERM, terminate)
    old_int = signal.signal(signal.SIGINT, terminate)
    try:
        threads[0].start()
        if not bound.wait(2):
            raise OSError("IPC failed to bind")
        threads[1].start()
        threads[2].start()
        threads[3].start()
        owner.start()
        while not stop.is_set() and not owner.stopping:
            while not safety.empty():
                safety.get_nowait()()
            try:
                messages.get(timeout=0.05)()
            except Empty:
                pass
            owner.tick()
        return 1 if failures else 0
    finally:
        stop.set()
        try:
            owner.shutdown()
        finally:
            try:
                # Never put observation/executor joins ahead of this fallback.
                _ = checked(("/usr/bin/systemctl", "stop", "sparehash-xmrig.service"), 5)
            finally:
                _close_workers(jobs, providers, threads)
                try:
                    # Reaffirm stop after any in-flight Jobs have drained.
                    _ = checked(("/usr/bin/systemctl", "stop", "sparehash-xmrig.service"), 5)
                finally:
                    try:
                        _ = cleanup()  # Still requires the existing empty-cgroup proof.
                    finally:
                        _ = signal.signal(signal.SIGTERM, old_term)
                        _ = signal.signal(signal.SIGINT, old_int)
