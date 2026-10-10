# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Bounded local control transport; the controller owns all state decisions."""

from __future__ import annotations

import grp
import json
import logging
import math
import os
import selectors
import socket
import stat
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from threading import Event
from typing import Final, Literal, NewType, TypeAlias, TypeGuard

from .paths import RUN_DIR
from .state import BLOCKERS

Json: TypeAlias = None | bool | int | float | str | list["Json"] | dict[str, "Json"]
Document: TypeAlias = dict[str, Json]
Uid = NewType("Uid", int)
Gid = NewType("Gid", int)
MAX_FRAME: Final = 4096
CONNECTION_TIMEOUT: Final = 1.0
RATE_WINDOW_SECONDS: Final = 1.0
RATE_MAX_PER_WINDOW: Final = 20
LOGGER: Final = logging.getLogger(__name__)
_LOAD_JSON: Final[Callable[..., Json]] = json.loads
_connection_log: dict[int, deque[float]] = {}
_connection_lock = threading.Lock()
# allow: SIZE_OK — trusted framing, credentials, rate limit, and typed boundary parsing are one transport unit.
_TOKEN_MAX: Final = 64
_PURPOSE_MAX: Final = 32
_REQUEST_MAX: Final = 128


class Verb(StrEnum):
    STATUS = "status"
    PAUSE = "pause"
    RESUME = "resume"
    GAMING_START = "gaming-start"
    GAMING_END = "gaming-end"
    RELOAD = "reload"
    DEBUG_SET = "debug-set"
    DEBUG_CLEAR = "debug-clear"
    AUTHORIZE_START = "authorize-start"
    BENCHMARK = "benchmark"
    BEGIN_UPDATE = "begin-update"
    UPDATE_STATUS = "update-status"
    END_UPDATE = "end-update"


VERBS: Final = frozenset(Verb)
ROOT_ONLY: Final = frozenset((Verb.RELOAD, Verb.DEBUG_SET, Verb.DEBUG_CLEAR,
                            Verb.AUTHORIZE_START, Verb.BENCHMARK,
                            Verb.BEGIN_UPDATE, Verb.UPDATE_STATUS, Verb.END_UPDATE))
DEBUG: Final = frozenset((Verb.DEBUG_SET, Verb.DEBUG_CLEAR))
LIFECYCLE: Final = frozenset((Verb.BEGIN_UPDATE, Verb.UPDATE_STATUS, Verb.END_UPDATE))
ORDINARY: Final = VERBS - DEBUG - LIFECYCLE

OrdinaryVerb = Literal[Verb.STATUS, Verb.PAUSE, Verb.RESUME, Verb.GAMING_START, Verb.GAMING_END,
                        Verb.RELOAD, Verb.AUTHORIZE_START, Verb.BENCHMARK]
DebugVerb = Literal[Verb.DEBUG_SET, Verb.DEBUG_CLEAR]
LifecycleVerb = Literal[Verb.BEGIN_UPDATE, Verb.UPDATE_STATUS, Verb.END_UPDATE]
Outcome = Literal["commit", "abort"]
OUTCOMES: Final[tuple[Outcome, ...]] = ("commit", "abort")


@dataclass(frozen=True, slots=True)
class Peer:
    uid: Uid
    gid: Gid


@dataclass(frozen=True, slots=True)
class Command:
    verb: OrdinaryVerb


@dataclass(frozen=True, slots=True)
class DebugCommand:
    """Only modifies injected observations; never a real blocker-clear instruction."""

    verb: DebugVerb
    blocker: str


@dataclass(frozen=True, slots=True)
class LifecycleBegin:
    verb: Literal[Verb.BEGIN_UPDATE]
    purpose: str
    request_id: str


@dataclass(frozen=True, slots=True)
class LifecycleStatus:
    verb: Literal[Verb.UPDATE_STATUS]
    token: str


@dataclass(frozen=True, slots=True)
class LifecycleEnd:
    verb: Literal[Verb.END_UPDATE]
    token: str
    outcome: Outcome


Request: TypeAlias = Command | DebugCommand | LifecycleBegin | LifecycleStatus | LifecycleEnd
Handler: TypeAlias = Callable[[Request, Peer], Document]


class IPCError(Exception):
    def __init__(self, code: str, cause: OSError | None = None) -> None:
        super().__init__(code)
        self.code: str = code
        self.cause: OSError | None = cause


def _pairs(pairs: list[tuple[str, Json]]) -> Document:
    result: Document = {}
    for key, value in pairs:
        if key in result:
            raise IPCError("duplicate field")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise IPCError("invalid JSON")


def _document(frame: bytes) -> Document:
    try:
        raw = _LOAD_JSON(frame, object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise IPCError("invalid JSON") from exc
    if not isinstance(raw, dict):
        raise IPCError("expected object")
    return raw


def _is_debug(verb: Verb) -> TypeGuard[DebugVerb]:
    return verb in DEBUG


def _is_lifecycle(verb: Verb) -> TypeGuard[LifecycleVerb]:
    return verb in LIFECYCLE


def _is_ordinary(verb: Verb) -> TypeGuard[OrdinaryVerb]:
    return verb in ORDINARY


def _is_outcome(value: str) -> TypeGuard[Outcome]:
    return value in OUTCOMES


def _bounded_token(value: Json | None) -> TypeGuard[str]:
    if not isinstance(value, str) or not 0 < len(value) <= _TOKEN_MAX:
        return False
    try:
        _ = int(value, 16)
    except ValueError:
        return False
    return True


def parse_request(frame: bytes) -> Request:
    """Consume client input once, rejecting all non-protocol fields."""
    raw = _document(frame)
    name = raw.get("cmd")
    if not isinstance(name, str) or name not in VERBS:
        raise IPCError("unknown verb")
    verb = Verb(name)
    if _is_debug(verb):
        blocker = raw.get("blocker")
        if set(raw) != {"cmd", "blocker"} or not isinstance(blocker, str) or blocker not in BLOCKERS:
            raise IPCError("invalid blocker")
        return DebugCommand(verb, blocker)
    if _is_lifecycle(verb):
        return _parse_lifecycle(verb, raw)
    if not _is_ordinary(verb):
        raise IPCError("unknown verb")
    if set(raw) != {"cmd"}:
        raise IPCError("unexpected field")
    return Command(verb)


def _parse_lifecycle(verb: LifecycleVerb, raw: Document) -> LifecycleBegin | LifecycleStatus | LifecycleEnd:
    if verb is Verb.BEGIN_UPDATE:
        purpose = raw.get("purpose")
        request_id = raw.get("request_id")
        if (set(raw) != {"cmd", "purpose", "request_id"} or not isinstance(purpose, str)
                or not 0 < len(purpose) <= _PURPOSE_MAX
                or not isinstance(request_id, str) or not 0 < len(request_id) <= _REQUEST_MAX):
            raise IPCError("invalid lifecycle begin")
        return LifecycleBegin(verb, purpose, request_id)
    if verb is Verb.UPDATE_STATUS:
        token = raw.get("token")
        if set(raw) != {"cmd", "token"} or not _bounded_token(token):
            raise IPCError("invalid lifecycle status")
        return LifecycleStatus(verb, token)
    token = raw.get("token")
    outcome = raw.get("outcome")
    if (set(raw) != {"cmd", "token", "outcome"} or not _bounded_token(token)
            or not isinstance(outcome, str) or not _is_outcome(outcome)):
        raise IPCError("invalid lifecycle end")
    return LifecycleEnd(verb, token, outcome)


def peer_credentials(connection: socket.socket) -> Peer:
    credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    uid = int.from_bytes(credentials[4:8], byteorder=sys.byteorder, signed=True)
    gid = int.from_bytes(credentials[8:12], byteorder=sys.byteorder, signed=True)
    return Peer(Uid(uid), Gid(gid))


def socket_path() -> Path:
    """Environment override is an explicit QA seam, confined to private temp storage."""
    override = os.environ.get("SPAREHASH_RUN_DIR")
    if override is None:
        return RUN_DIR / "control.sock"
    directory = Path(override)
    resolved = directory.resolve(strict=True)
    info = directory.lstat()
    parent = directory.parent.stat()
    if (directory != resolved or not resolved.is_relative_to(Path(tempfile.gettempdir()).resolve())
            or resolved == Path(tempfile.gettempdir()).resolve()
            or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022
            or parent.st_uid != os.geteuid() or parent.st_mode & 0o077):
        raise IPCError("unsafe QA directory")
    return directory / "control.sock"


def _directory_owner(fd: int) -> None:
    os.fchown(fd, 0, grp.getgrnam("sparehash").gr_gid)


def _socket_owner(fd: int) -> None:
    os.chown("control.sock", 0, grp.getgrnam("sparehash").gr_gid, dir_fd=fd, follow_symlinks=False)


def _frame(connection: socket.socket, deadline: float) -> bytes:
    data = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IPCError("timeout")
        connection.settimeout(remaining)
        chunk = connection.recv(MAX_FRAME + 1 - len(data))
        if not chunk:
            raise IPCError("incomplete frame")
        data.extend(chunk)
        if len(data) > MAX_FRAME:
            raise IPCError("oversized frame")
        if b"\n" in data:
            if data.count(b"\n") != 1 or not data.endswith(b"\n"):
                raise IPCError("multiple commands")
            return bytes(data[:-1])


def _encode(document: Document) -> bytes:
    frame = json.dumps(document, allow_nan=False, separators=(",", ":")).encode() + b"\n"
    if len(frame) > MAX_FRAME:
        raise IPCError("oversized frame")
    return frame


def _rate_limited(uid: int) -> bool:
    now = time.monotonic()
    with _connection_lock:
        log = _connection_log.setdefault(uid, deque(maxlen=RATE_MAX_PER_WINDOW))
        while log and log[0] < now - RATE_WINDOW_SECONDS:
            log.popleft()
        if len(log) >= RATE_MAX_PER_WINDOW:
            return True
        log.append(now)
    return False


def _dispatch(connection: socket.socket, handler: Handler) -> None:
    deadline = time.monotonic() + CONNECTION_TIMEOUT
    try:
        peer = peer_credentials(connection)
        if _rate_limited(peer.uid):
            raise IPCError("rate limited")
        request = parse_request(_frame(connection, deadline))
        LOGGER.info("control command", extra={"uid": peer.uid, "gid": peer.gid, "verb": request.verb.value})
        if peer.uid < 0 or peer.gid < 0 or (request.verb in ROOT_ONLY and peer.uid != 0):
            raise IPCError("permission denied")
        response = handler(request, peer)
        encoded = _encode(response)
    except (IPCError, OSError) as exc:
        encoded = _encode({"ok": False, "error": exc.code if isinstance(exc, IPCError) else "connection error"})
    try:
        connection.settimeout(max(0.001, deadline - time.monotonic()))
        connection.sendall(encoded)
    except OSError as exc:
        raise IPCError("connection error") from exc


def serve(sock_path: str | Path, handler: Handler, stop_event: Event, *, bound: Event | None = None) -> None:
    """Accept nonblocking; each connection has a total bounded receive/send budget.

    Handler must be nonblocking: controller lifecycle/observation work belongs outside
    this transport loop. Socket filesystem access is group-gated by the kernel.
    """
    expected = socket_path()
    if Path(sock_path) != expected:
        raise IPCError("unexpected socket path")
    identity: tuple[int, int] | None = None
    try:
        expected.parent.mkdir(mode=0o750, exist_ok=True)
        fd = os.open(expected.parent, os.O_DIRECTORY | os.O_NOFOLLOW | os.O_RDONLY)
        try:
            info = os.fstat(fd)
            trusted_uid = os.geteuid() if os.environ.get("SPAREHASH_RUN_DIR") else 0
            if info.st_uid != trusted_uid or info.st_mode & 0o022:
                raise IPCError("unsafe run directory")
            _directory_owner(fd)
            os.fchmod(fd, 0o750)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener, selectors.DefaultSelector() as selector:
                # Bind relative to the pinned directory; never unlink preexisting entries.
                listener.bind(f"/proc/self/fd/{fd}/control.sock")
                info = os.stat("control.sock", dir_fd=fd, follow_symlinks=False)
                identity = (info.st_dev, info.st_ino)
                _socket_owner(fd)
                os.chmod("control.sock", 0o660, dir_fd=fd)
                listener.listen(16)
                listener.setblocking(False)
                if bound is not None:
                    bound.set()
                _ = selector.register(listener, selectors.EVENT_READ)
                while not stop_event.is_set():
                    for _key, _mask in selector.select(0.05):
                        connection = listener.accept()[0]
                        with connection:
                            try:
                                _dispatch(connection, handler)
                            except IPCError:
                                LOGGER.warning("control connection closed")
        finally:
            try:
                if identity is not None:
                    info = os.stat("control.sock", dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == identity:
                        os.unlink("control.sock", dir_fd=fd)
            except FileNotFoundError:
                identity = None
            finally:
                os.close(fd)
    except (OSError, KeyError) as exc:
        raise IPCError("socket setup or connection error") from exc


def call(cmd: Document, timeout: float = 5) -> Document:
    """One command per connection; a single deadline covers connect/send/receive."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise IPCError("invalid timeout")
    frame = _encode(cmd)
    _ = parse_request(frame[:-1])
    deadline = time.monotonic() + timeout
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout)
            connection.connect(str(socket_path()))
            connection.sendall(frame)
            return _document(_frame(connection, deadline))
    except TimeoutError as exc:
        raise IPCError("timeout") from exc
    except PermissionError as exc:
        raise IPCError("permission denied", exc) from exc
    except FileNotFoundError as exc:
        raise IPCError("socket missing", exc) from exc
    except ConnectionRefusedError as exc:
        raise IPCError("connection refused", exc) from exc
    except OSError as exc:
        raise IPCError("connection error", exc) from exc
