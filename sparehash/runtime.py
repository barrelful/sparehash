"""Production controller assembly: safety callbacks outrank IPC/job completions."""

from __future__ import annotations

import os
import grp
import signal
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from queue import Empty, Queue
from types import FrameType
from typing import final

from . import config, gaming, ipc, power, systemd, thermal
from .controller import Controller
from .controller_io import Observation, Ports, SampleId
from .deployment import Tree, checked
from .hugepages import LeaseManager
from .install_manifest import StoreContext, locked_store
from .xmrig_paths import PathTrust
from .runtime_jobs import Jobs, empty
from .runtime_spec import DIRECTORY, SPEC
from .state import BlockerState


@final
class Providers:
    def __init__(self, cfg: config.Config, enqueue: Callable[[Callable[[], None]], None]) -> None:
        self.cfg, self.enqueue = cfg, enqueue
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="observation")
        self.queries = ThreadPoolExecutor(max_workers=8, thread_name_prefix="guard-query")
        self.monitor = power.PowerMonitor()
        self.guard = thermal.ThermalGuard(cfg.thermal.enabled, cfg.thermal.pause_c, cfg.thermal.resume_c,
                                          thermal.find_sensor(Path("/sys/class/hwmon")))
        self.guard_lock = threading.Lock()
        self.users = tuple(gaming.User.from_name(name) for name in cfg.gaming.users) if cfg.gaming.enabled else ()
        self.finish: Callable[[Observation], None] = lambda _: None

    def temperature(self) -> bool:
        with self.guard_lock:
            return self.guard.poll().active

    def request(self, sample: SampleId, deadline: float) -> None:
        _ = self.worker.submit(self.collect, sample, deadline)

    def collect(self, sample: SampleId, deadline: float) -> None:
        inventory, sysfs = power.read_sysfs(Path("/sys"), time.monotonic())
        upower_future = self.monitor.backend.query()
        thermal_future = self.queries.submit(self.temperature)
        gaming_futures = tuple((user.uid, self.queries.submit(gaming.DEFAULT_PROBE.query, user)) for user in self.users)
        try:
            on_battery = upower_future.result(timeout=max(0.001, deadline - time.monotonic()))
        except TimeoutError:
            on_battery = None
        try:
            hot = thermal_future.result(timeout=max(0.001, deadline - time.monotonic()))
        except TimeoutError:
            hot = None
        observations: list[tuple[ipc.Uid, bool | None]] = []
        for uid, future in gaming_futures:
            try:
                value = future.result(timeout=max(0.001, deadline - time.monotonic()))
            except TimeoutError:
                value = None
            observations.append((uid, value))
        observation = Observation(sample, time.monotonic(), inventory.value, on_battery, sysfs.value, tuple(observations), hot)
        self.enqueue(lambda: self.finish(observation))

    def close(self) -> None:
        self.worker.shutdown(wait=True, cancel_futures=True)
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


def initialize(state: BlockerState, ports: Ports) -> Controller:
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


def run() -> int:
    if os.geteuid() != 0:
        return 1
    _ = os.environ.pop("SPAREHASH_RUN_DIR", None)
    _ = os.environ.pop("SPAREHASH_HWMON_ROOT", None)
    cfg = config.load()
    with locked_store(StoreContext(PathTrust())) as store:
        os.fchown(store.fd, 0, grp.getgrnam("sparehash").gr_gid)
        snapshot = store.read()
        if snapshot is not None:
            store.write(snapshot.manifest)
    safety: Queue[Callable[[], None]] = Queue()
    messages: Queue[Callable[[], None]] = Queue()
    stop, bound = threading.Event(), threading.Event()
    providers = Providers(cfg, messages.put)
    jobs = Jobs(cfg, messages.put)
    state = BlockerState(min_dwell_seconds=cfg.controller.min_dwell_seconds)
    owner = Controller(state, Ports(providers, jobs, Lifecycle(bound, os.environ["NOTIFY_SOCKET"]), time.monotonic))
    providers.finish, jobs.finish = owner.observe, owner.complete
    jobs.finished = owner.benchmark_finished
    jobs.authorize = lambda: owner.authorize(consume=False)
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
        safety.put(lambda: owner.safety("battery", sample.blocked))

    def temperatures() -> None:
        prior: bool | None = None
        while not stop.is_set():
            hot = providers.temperature()
            if hot != prior:
                safety.put(lambda hot=hot: owner.safety("thermal", hot))
                prior = hot
            messages.put(owner.refresh)
            _ = stop.wait(5)

    def terminate(_number: int, _frame: FrameType | None) -> None:
        stop.set()

    threads = (threading.Thread(target=serve, daemon=True),
               threading.Thread(target=providers.monitor.run, args=(stop, emit), daemon=True),
               threading.Thread(target=temperatures, daemon=True))
    old_term = signal.signal(signal.SIGTERM, terminate)
    old_int = signal.signal(signal.SIGINT, terminate)
    try:
        threads[0].start()
        if not bound.wait(2):
            raise OSError("IPC failed to bind")
        threads[1].start()
        threads[2].start()
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
        owner.shutdown()
        stop.set()
        jobs.close()
        providers.close()
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=12)
        _ = checked(("/usr/bin/systemctl", "stop", "sparehash-xmrig.service"), 5)
        _ = cleanup()
        _ = signal.signal(signal.SIGTERM, old_term)
        _ = signal.signal(signal.SIGINT, old_int)
