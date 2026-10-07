"""Shared deterministic ports and temporary persistent controller fixtures."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import final

from sparehash.controller import Controller
from sparehash.controller_io import Action, Completion, Job, Observation, Operation, Ports, SampleId
from sparehash.ipc import Command, Gid, Peer, Uid, Verb
from sparehash.state import BlockerState

ROOT = Peer(Uid(0), Gid(0))


@final
class FakePorts:
    """Mutable recording ports; only tests can explicitly finish a job."""

    def __init__(self) -> None:
        self.now = 0.0
        self.trace: list[str] = []
        self.jobs: list[Job] = []
        self.requests: list[tuple[SampleId, float]] = []
        self.operation: Operation | None = None
        self.notify_fails = False

    def clock(self) -> float:
        return self.now

    def request(self, sample: SampleId, deadline: float) -> None:
        self.requests.append((sample, deadline))

    def submit(self, job: Job) -> None:
        self.jobs.append(job)
        self.trace.append(job.action.value)
        if job.action is Action.START:
            self.operation = job.operation

    def cancel(self) -> None:
        self.trace.append("cancel")
        self.operation = None

    def published(self) -> Operation | None:
        return self.operation

    def bind(self) -> None:
        self.trace.append("bind")

    def ready(self) -> None:
        self.trace.append("ready")
        if self.notify_fails:
            raise OSError("injected notify failure")


class ControllerFixture(unittest.TestCase):
    """Fixture lifetime is per test instance, including isolated persistent state."""

    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.temp: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path: Path = Path(self.temp.name) / "state.json"
        self.io: FakePorts = FakePorts()
        self.state: BlockerState = BlockerState(self.path, self.io.clock, 0)
        self.c: Controller = Controller(self.state, Ports(self.io, self.io, self.io, self.io.clock))

    def snapshot(self) -> Observation:
        return Observation(self.c.sample, self.io.now, True, False, False, (), False)

    def fresh(self, observation: Observation | None = None) -> None:
        self.c.refresh()
        self.c.observe(self.snapshot() if observation is None else replace(observation, sample=self.c.sample, at=self.io.now))

    def finish(self, success: bool = True, empty: bool = True) -> None:
        job = self.c.pending
        assert job is not None
        self.c.complete(Completion(job, success, empty))

    def running(self) -> None:
        self.c.start()
        self.fresh()
        self.finish()
        self.finish()

    def command(self, verb: Verb, uid: int = 0) -> None:
        _ = self.c.handle(Command(verb), Peer(Uid(uid), Gid(0)))
