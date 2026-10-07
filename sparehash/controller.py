"""Single-owner safety state machine; all external waits belong to injected ports."""

from __future__ import annotations

import logging
from typing import Final, assert_never, final

from .controller_io import Action, Completion, Generation, Job, LifecycleBusy, Mode, Observation, Operation, Ports, SampleId, SafetyBlocker, dispatch
from .ipc import Document, Peer, Request, Uid
from .state import BlockerState

LOGGER: Final = logging.getLogger(__name__)


@final
class Controller:
    """Mutable safety accumulator; only the selector owner may call these methods."""

    def __init__(self, state: BlockerState, ports: Ports) -> None:
        self.state, self.ports = state, ports
        self.generation = Generation(0)
        self.mode = Mode.MINE
        self.sample = SampleId(0)
        self.observation: Observation | None = None
        self.requested_at = 0.0
        self.collecting = False
        self.next_refresh = 0.0
        self.ready = False
        self.stopping = False
        self.excluded = False
        self.lease: Generation | None = None
        self.empty = False
        self.running = False
        self.pending: Job | None = None
        self.failures = 0
        self.retry_at = 0.0
        self.real: dict[str, bool] = {name: True for name in ("battery", "gaming", "thermal")}
        self.debug: set[str] = set()
        self.hooks: set[Uid] = set()
        self.benchmark_authorized = False
        self.next_mode: Mode | None = None
        self.observed_generation: Generation | None = None
        self.launch_permitted = False

    @property
    def operation(self) -> Operation:
        return Operation(self.generation, self.mode)

    def refresh(self) -> None:
        """Request concurrent observations, preserving current hard blockers."""
        if not self.collecting:
            self.sample = SampleId(self.sample + 1)
            self.requested_at = self.ports.clock()
            self.collecting = True
            self.ports.providers.request(self.sample, self.requested_at + 10)

    def start(self) -> None:
        self.sync()
        self.refresh()
        self.apply()

    def observe(self, observation: Observation) -> None:
        if (observation.sample != self.sample or not self.collecting
                or not self.requested_at <= observation.at <= self.ports.clock()
                or self.ports.clock() - observation.at > 10):
            return
        self.collecting = False
        self.observation = observation
        self.next_refresh = self.ports.clock() + 30
        self.real = dict(observation.blockers())
        self.sync()
        self.observed_generation = self.generation
        if not self.ready and not self.stopping:
            try:
                self.ports.lifecycle.bind()
                self.ports.lifecycle.ready()
            except OSError:
                LOGGER.error("controller readiness failed", exc_info=True)
                self.shutdown()
                return
            self.ready = True
        self.apply()

    def invalidate(self) -> None:
        self.generation = Generation(self.generation + 1)
        self.ports.jobs.cancel()
        self.pending = None
        self.empty = False
        self.running = False
        self.benchmark_authorized = False
        self.launch_permitted = False
        self.failures = 0
        self.retry_at = 0.0

    def sync(self) -> None:
        changed = False
        for name in ("battery", "gaming", "thermal"):
            blocked = self.real[name] or name in self.debug or (name == "gaming" and bool(self.hooks))
            transition = (self.state.set(name, "unsafe or unknown observation", "controller")
                          if blocked else self.state.clear(name))
            if transition.changed:
                changed = True
                LOGGER.info("blocker changed", extra={"blocker": name, "active": blocked, "reason": transition.message})
        if changed:
            self.invalidate()
            if self.ready and not self.state.mining_allowed():
                self.state.automatic_stop()

    def safety(self, name: SafetyBlocker, blocked: bool | None) -> None:
        """Immediate authoritative provider event; invalidate outstanding queries."""
        self.real[name] = blocked is not False
        self.collecting = False
        self.observation = None
        self.sync()
        self.apply()
        self.refresh()

    def apply(self) -> None:
        """Queue at most one ordered job; never wait for stop/start completion."""
        allowed = self.ready and not self.stopping and not self.excluded and self.state.mining_allowed() and not self.debug
        if not allowed:
            if self.mode is Mode.BENCHMARK and not self.excluded:
                self.excluded, self.next_mode = True, Mode.MINE
                self.lease = self.generation
            if not self.empty and self.pending is None:
                self._submit(Action.STOP)
            return
        if self.pending is not None or self.running or self.failures >= 3 or self.ports.clock() < self.retry_at:
            return
        if not self.empty:
            self._submit(Action.STOP)
            return
        if (self.collecting or self.observed_generation != self.generation
                or self.observation is None or self.ports.clock() - self.observation.at > 1):
            self.refresh()
            return
        if self.state.auto_start_allowed():
            self._submit(Action.START)

    def _submit(self, action: Action) -> None:
        if self.failures >= 3 or self.ports.clock() < self.retry_at:
            return
        job = Job(action, self.operation, self.ports.clock() + 5)
        if action is Action.START:
            self.launch_permitted = True
        self.pending = job
        self.ports.jobs.submit(job)

    def complete(self, result: Completion) -> None:
        if result.job != self.pending:
            return
        self.pending = None
        match result.job.action:
            case Action.STOP:
                success = result.success and result.cgroup_empty
                if success:
                    self.empty, self.running = True, False
                    if self.next_mode is not None and self.lease is not None:
                        self.mode, self.next_mode = self.next_mode, None
                        _ = self.release_update(self.lease)
                        self.empty = True
            case Action.START:
                success = result.success
                self.running = success
                self.empty = False
            case _:
                assert_never(result.job.action)
        if not success:
            self.launch_permitted = False
            self.ports.jobs.cancel()
            self.failures += 1
            self.retry_at = self.ports.clock() + 1
            LOGGER.warning("miner job failed; bounded retry", extra={"action": result.job.action.value, "attempt": self.failures})
        elif result.job.action is Action.START or not self.state.mining_allowed():
            self.failures = 0
        self.apply()

    def authorize(self, consume: bool = True) -> bool:
        """B1: only just-refreshed current generation; unavailable/unknown denies.

        Wrapper must compare this generation again before exec (Todo24). No token
        survives invalidation. Benchmark permission is one-use, no auto-loop.
        """
        fresh = self.observation is not None and 0 <= self.ports.clock() - self.observation.at <= 1
        allowed = (fresh and self.observed_generation == self.generation and self.launch_permitted
                   and not self.collecting and self.ready and not self.stopping and not self.excluded
                   and self.state.mining_allowed() and not self.debug
                   and self.ports.jobs.published() == self.operation)
        if not fresh or self.observed_generation != self.generation:
            self.refresh()
        if self.mode is Mode.BENCHMARK:
            allowed = allowed and not self.benchmark_authorized
            self.benchmark_authorized = self.benchmark_authorized or (allowed and consume)
        return allowed

    def acquire_update(self) -> Generation:
        """Exclusive launch inhibition; caller MUST wait for update_ready(token)."""
        if self.excluded:
            raise LifecycleBusy()
        self.excluded = True
        self.state.automatic_stop()
        self.invalidate()
        self.lease = self.generation
        self.apply()
        return self.generation

    def update_ready(self, token: Generation) -> bool:
        return self.excluded and token == self.lease and self.empty and self.pending is None

    def release_update(self, token: Generation) -> bool:
        if not self.update_ready(token):
            return False
        self.excluded = False
        self.lease = None
        self.invalidate()
        self.refresh()
        return True

    def benchmark_finished(self, operation: Operation) -> None:
        if operation == self.operation and self.mode is Mode.BENCHMARK:
            _ = self.acquire_update()
            self.next_mode = Mode.MINE

    def shutdown(self) -> None:
        self.stopping = True
        self.invalidate()
        self.apply()

    def tick(self) -> None:
        now = self.ports.clock()
        if self.collecting and now - self.requested_at >= 10:
            self.observe(Observation(self.sample, now, None, None, None, ((Uid(0), None),), None))
        if self.pending is not None and now >= self.pending.deadline:
            self.complete(Completion(self.pending, False))
        if not self.stopping and now >= self.next_refresh:
            self.refresh()
        if self.observation is not None and now - self.observation.at > 10:
            self.real = {name: True for name in self.real}
            self.sync()
        self.apply()

    def handle(self, request: Request, peer: Peer) -> Document:
        return dispatch(self, request, peer)
