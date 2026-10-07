"""Serialized bounded systemd jobs; controller policy stays on its owner thread."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Thread
from typing import assert_never, final

from .config import Config
from .controller_io import Action, Completion, Job, Mode, Operation
from .deployment import ENV, checked
from .sysdetect import Capabilities
from .hugepages import LeaseManager
from .runtime_spec import publish


def empty() -> bool:
    try:
        shown = checked(("/usr/bin/systemctl", "show", "sparehash-xmrig.service", "--property=ActiveState,SubState,ControlGroup"), 1)
        fields = dict(line.split("=", 1) for line in shown.splitlines() if "=" in line)
        group = fields.get("ControlGroup")
        if fields.get("ActiveState") in {"inactive", "failed"} and fields.get("SubState") in {"dead", "failed"} and group == "":
            return True
        if group is None or not group.startswith("/") or group == "/" or ".." in Path(group).parts:
            return False
        path = Path("/sys/fs/cgroup") / group.removeprefix("/")
        return (path / "cgroup.events").read_text().splitlines().count("populated 0") == 1 and not (path / "cgroup.procs").read_text().strip()
    except (OSError, RuntimeError, ValueError):
        return False


@final
class Jobs:
    def __init__(self, cfg: Config, enqueue: Callable[[Callable[[], None]], None]) -> None:
        self.cfg, self.enqueue = cfg, enqueue
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="miner-job")
        self.epoch = 0
        self.operation: Operation | None = None
        self.watched: Operation | None = None
        self.finish: Callable[[Completion], None] = lambda _: None
        self.authorize: Callable[[], bool] = lambda: False
        self.pages = LeaseManager(empty_proof=empty)
        self.numa = Capabilities.detect(environ=ENV).numa_nodes if cfg.hugepages.enabled else None
        self.finished: Callable[[Operation], None] = lambda _: None
        self.stop = Event()
        self.watcher = Thread(target=self.watch, daemon=True)
        self.watcher.start()

    def watch(self) -> None:
        while not self.stop.wait(0.25):
            operation = self.watched
            if operation is not None and operation.mode is Mode.BENCHMARK and empty():
                self.enqueue(lambda operation=operation: self.finished(operation))

    def cancel(self) -> None:
        self.epoch += 1
        self.operation = None
        self.watched = None

    def published(self) -> Operation | None:
        return self.operation

    def submit(self, job: Job) -> None:
        _ = self.worker.submit(self.perform, job, self.epoch)

    def ask(self, callback: Callable[[], bool]) -> bool:
        ready = Event()
        result: list[bool] = []
        def deliver() -> None:
            result.append(callback())
            ready.set()
        self.enqueue(deliver)
        return ready.wait(0.5) and result == [True]

    def perform(self, job: Job, epoch: int) -> None:
        success, vacant = False, False
        try:
            match job.action:
                case Action.STOP:
                    _ = checked(("/usr/bin/systemctl", "stop", "sparehash-xmrig.service"), 3)
                    vacant = empty()
                    if vacant:
                        _ = self.pages.cleanup()
                    success = vacant
                case Action.START:
                    if epoch != self.epoch or time.monotonic() >= job.deadline:
                        return
                    publish(self.cfg, job.operation.generation, job.operation.mode.value)
                    def published() -> bool:
                        if epoch != self.epoch:
                            return False
                        self.operation = job.operation
                        return self.authorize()
                    if not self.ask(published):
                        return
                    _ = self.pages.reserve_after_authorization(self.cfg.hugepages.enabled, self.numa,
                                                              self.cfg.hugepages.pages, authorized=True)
                    _ = checked(("/usr/bin/systemctl", "start", "sparehash-xmrig.service"), 3)
                    success = checked(("/usr/bin/systemctl", "show", "sparehash-xmrig.service", "--property=ActiveState", "--value"), 1) == "active"
                    self.watched = job.operation if success and epoch == self.epoch else None
                case _:
                    assert_never(job.action)
        except (OSError, RuntimeError, ValueError):
            success = False
        finally:
            completion = Completion(job, success, vacant)
            self.enqueue(lambda: self.finish(completion))

    def close(self) -> None:
        self.cancel()
        self.stop.set()
        self.watcher.join(timeout=2)
        self.worker.shutdown(wait=True, cancel_futures=True)
