# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Nonblocking controller ports; adapters own bounded external work, never policy."""

from __future__ import annotations

import logging
import selectors
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, NewType, Protocol, assert_never

from .ipc import (Command, DebugCommand, Document, IPCError, LifecycleBegin, LifecycleEnd,
                    LifecycleStatus, Outcome, Peer, ROOT_ONLY, Request, Uid, Verb, call)
from .lifecycle_lease import LeaseError, Purpose, is_purpose
from .power import Evidence, PowerSample, blocked
from .state import BLOCKERS, BlockerState, StatePersistenceError

Generation = NewType("Generation", int)
SampleId = NewType("SampleId", int)
SafetyBlocker = Literal["battery", "gaming", "thermal"]


class Mode(StrEnum):
    MINE = "mine"
    BENCHMARK = "benchmark"


class Action(StrEnum):
    START = "start"
    STOP = "stop"


class LifecycleBusy(RuntimeError):
    """An update/operation lease already excludes all competing lifecycle callers."""


class StaleToken(RuntimeError):
    """The presented token names no active external lease."""


@dataclass(frozen=True, slots=True)
class Operation:
    generation: Generation
    mode: Mode


@dataclass(frozen=True, slots=True)
class Observation:
    """One concurrent collection, including ALL configured live gaming users.

    None is unknown. Battery inventory False means confirmed desktop/UPS-only.
    Thermal False also represents a disabled or positively unavailable guard.
    Provider adapters must invalidate dead monitors and parse input at the edge.
    """

    sample: SampleId
    at: float
    battery_present: bool | None
    upower: bool | None
    sysfs: bool | None
    gaming: tuple[tuple[Uid, bool | None], ...]
    thermal: bool | None
    ac_online: bool | None = None
    discharge_rate_w: float | None = None
    maintenance_mode: bool = False

    def blockers(self) -> tuple[tuple[str, bool], ...]:
        battery = blocked(PowerSample(self.at, Evidence(self.battery_present, self.at),
                                      Evidence(self.upower, self.at), Evidence(self.sysfs, self.at),
                                      self.ac_online, self.discharge_rate_w),
                          maintenance_mode=self.maintenance_mode)
        return (("battery", battery), ("gaming", any(value is not False for _, value in self.gaming)),
                ("thermal", self.thermal is not False))


@dataclass(frozen=True, slots=True)
class Job:
    action: Action
    operation: Operation
    deadline: float


@dataclass(frozen=True, slots=True)
class Completion:
    job: Job
    success: bool
    cgroup_empty: bool = False


class Providers(Protocol):
    def request(self, sample: SampleId, deadline: float) -> None:
        """Enqueue concurrent fresh queries; never wait; deadline yields unknown."""
        ...


class Jobs(Protocol):
    """One bounded serialized worker for the SAME UNIT_MINER, no subprocess miner.

    submit START reserves pages and atomically publishes protected operation before
    systemctl start. STOP cancels activation/restart and confirms entire cgroup
    empty BEFORE releasing pages or acknowledging success. cancel revokes queued
    STARTs immediately; a dispatched start still meets ExecCondition. Workers
    enforce deadline and deliver Completion on the selector owner thread.
    All methods here are enqueue/local-state operations, never helper waits.
    """

    def submit(self, job: Job) -> None: ...
    def cancel(self) -> None: ...
    def published(self) -> Operation | None:
        """Return locally tracked successfully published specification, no I/O."""
        ...


class Lifecycle(Protocol):
    def bind(self) -> None:
        """Bind protected IPC before READY; dispatch on controller owner thread."""
        ...

    def ready(self) -> None:
        """Deliver notify_ready outside dispatch; failure raises OSError."""
        ...


@dataclass(frozen=True, slots=True)
class Ports:
    providers: Providers
    jobs: Jobs
    lifecycle: Lifecycle
    clock: Callable[[], float]


class LoopOwner(Protocol):
    stopping: bool
    def tick(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Source:
    """Readable pipe/socket; drain bounded events without waiting or IPC framing."""

    fd: int
    dispatch: Callable[[], None]


def run_loop(owner: LoopOwner, sources: tuple[Source, ...]) -> None:
    """Owner-thread serialization: safety sources precede job/IPC completions.

    Pass sources in safety-first order. IPC transport must live off-loop because
    ipc.serve has bounded but blocking per-frame reads; marshal requests through
    a Source and return responses to its worker, never mutate state there.
    """
    with selectors.DefaultSelector() as selector:
        for source in sources:
            _ = selector.register(source.fd, selectors.EVENT_READ)
        while not owner.stopping:
            readable = {key.fd for key, _ in selector.select(0.05)}
            for source in sources:
                if source.fd in readable:
                    source.dispatch()
            owner.tick()


class CommandOwner(Protocol):
    running: bool
    observation: Observation | None
    ports: Ports
    state: BlockerState
    debug: set[str]
    hooks: set[Uid]
    ready: bool
    generation: Generation
    mode: Mode
    next_mode: Mode | None
    pending: Job | None
    excluded: bool
    def authorize(self) -> bool: ...
    def invalidate(self) -> None: ...
    def sync(self) -> None: ...
    def apply(self) -> None: ...
    def refresh(self) -> None: ...
    def acquire_update(self) -> Generation: ...
    def update_ready(self, token: Generation) -> bool: ...
    def release_update(self, token: Generation) -> bool: ...
    def begin_external(self, purpose: Purpose, request_id: str) -> str: ...
    def status_external(self, token: str) -> bool: ...
    def end_external(self, token: str, outcome: Outcome) -> bool: ...
    def shutdown(self) -> None: ...


def dispatch(owner: CommandOwner, request: Request, peer: Peer) -> Document:
    """Typed IPC routing only; every mutation goes through the controller owner."""
    if request.verb in ROOT_ONLY and peer.uid != 0:
        return {"ok": False, "error": "permission denied"}
    match request:
        case DebugCommand(verb=verb, blocker=name):
            if name not in BLOCKERS or verb not in (Verb.DEBUG_SET, Verb.DEBUG_CLEAR):
                return {"ok": False}
            changed = (name in owner.debug) != (verb is Verb.DEBUG_SET)
            if verb is Verb.DEBUG_SET:
                owner.debug.add(name)
            else:
                owner.debug.discard(name)
            if changed:
                owner.invalidate()
            owner.sync()
        case LifecycleBegin(purpose=purpose, request_id=request_id):
            if not is_purpose(purpose):
                return {"ok": False, "error": "invalid purpose"}
            try:
                return {"ok": True, "token": owner.begin_external(purpose, request_id)}
            except LifecycleBusy:
                return {"ok": False, "error": "lifecycle busy"}
            except LeaseError:
                logging.getLogger(__name__).error("lease persistence failed", exc_info=True)
                return {"ok": False, "error": "lease record unavailable"}
        case LifecycleStatus(token=token):
            try:
                ready = owner.status_external(token)
            except StaleToken:
                return {"ok": False, "error": "invalid or stale token"}
            except LeaseError:
                logging.getLogger(__name__).error("lease record unreadable", exc_info=True)
                return {"ok": False, "error": "lease record unavailable"}
            return {"ok": True, "status": "ready" if ready else "pending"}
        case LifecycleEnd(token=token, outcome=outcome):
            try:
                if owner.end_external(token, outcome):
                    return {"ok": True}
                return {"ok": False, "error": "empty barrier not satisfied"}
            except StaleToken:
                return {"ok": False, "error": "invalid or stale token"}
            except LeaseError:
                logging.getLogger(__name__).error("lease persistence failed", exc_info=True)
                return {"ok": False, "error": "lease record unavailable"}
        case Command(verb=verb):
            try:
                match verb:
                    case Verb.STATUS:
                        observation = owner.observation
                        age = None if observation is None else owner.ports.clock() - observation.at
                        return {"ok": True, "ready": owner.ready, "blockers": list(owner.state.active()),
                                "debug": list(owner.debug), "generation": owner.generation,
                                "running": owner.running,
                                "reasons": {name: value[0] for name, value in owner.state.active().items()},
                                "observation_age": age,
                                "gaming": [] if observation is None else
                                [[uid, active] for uid, active in observation.gaming]}
                    case Verb.AUTHORIZE_START:
                        return {"ok": owner.authorize(), "generation": owner.generation, "mode": owner.mode.value}
                    case Verb.PAUSE:
                        if owner.state.set("manual", "manual pause", "ipc").changed:
                            owner.invalidate()
                    case Verb.RESUME:
                        if owner.state.resume().changed:
                            owner.invalidate()
                    case Verb.GAMING_START | Verb.GAMING_END:
                        if verb is Verb.GAMING_START:
                            owner.hooks.add(peer.uid)
                        else:
                            owner.hooks.discard(peer.uid)
                        owner.sync()
                    case Verb.RELOAD:
                        if owner.excluded:
                            return {"ok": False, "error": "lifecycle busy"}
                        _ = owner.acquire_update()
                        owner.next_mode = Mode.MINE
                        owner.refresh()
                    case Verb.BENCHMARK:
                        if not owner.authorize() or owner.pending is not None or owner.excluded:
                            return {"ok": False}
                        _ = owner.acquire_update()
                        owner.next_mode = Mode.BENCHMARK
                    case _:
                        assert_never(verb)
            except StatePersistenceError:
                logging.getLogger(__name__).error("manual persistence failed", exc_info=True)
                owner.shutdown()
                return {"ok": False}
        case _:
            assert_never(request)
    owner.apply()
    return {"ok": True}


def authorize_miner() -> int:
    """Fixed ExecCondition client: root checked by CLI AND kernel-peer server."""
    try:
        return 0 if call({"cmd": "authorize-start"}, timeout=1).get("ok") is True else 1
    except IPCError:
        return 1
