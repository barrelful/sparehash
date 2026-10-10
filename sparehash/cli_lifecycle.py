# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Root-only XMRig update over the controller lifecycle lease; no try-restart bypass."""

from __future__ import annotations

import os
import secrets
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from . import ipc
from .cli_ops import Client, OperationError
from .controller_io import Generation
from .deployment import DeploymentError
from .install_xmrig import download_release
from .xmrig_install import InstallContext, InstallError, InstallRequest, Installed, current_binary, update_flow
from .xmrig_paths import PathTrust

LEASE_TIMEOUT: Final = 30.0
REQUEST_TIMEOUT: Final = 5.0
POLL_INTERVAL: Final = 0.2
_DEFAULT_TRUST: Final = PathTrust()


class LifecycleError(OperationError):
    """A lifecycle lease stage failed; an unverified hold is retained by the controller."""


@dataclass(frozen=True, slots=True)
class HeldLease:
    """Installer gate for a lease this process already acquired over IPC."""

    token: Generation

    def acquire_update(self) -> Generation:
        return self.token

    def update_ready(self, token: Generation) -> bool:
        return token == self.token

    def release_update(self, token: Generation) -> bool:
        return token == self.token


def _exchange(client: Client, command: ipc.Document) -> ipc.Document:
    try:
        return client(command, timeout=REQUEST_TIMEOUT)
    except (ipc.IPCError, OSError) as error:
        raise LifecycleError("controller", "lifecycle IPC unavailable") from error


def _detail(response: ipc.Document) -> str:
    error = response.get("error")
    return error if isinstance(error, str) else "controller rejected request"


def acquire(client: Client, request_id: str, *, purpose: str = "update") -> str:
    """Begin or idempotently re-obtain the opaque lease token for this request id."""
    response = _exchange(client, {"cmd": "begin-update", "purpose": purpose, "request_id": request_id})
    token = response.get("token")
    if response.get("ok") is not True or not isinstance(token, str):
        raise LifecycleError("begin-update", _detail(response))
    return token


def await_ready(client: Client, token: str, *, clock: Callable[[], float] = time.monotonic,
                pause: Callable[[float], None] = time.sleep, timeout: float = LEASE_TIMEOUT) -> None:
    """Poll until the matching stop proof is fresh; schedule is controller-owned."""
    deadline = clock() + timeout
    while True:
        status = _exchange(client, {"cmd": "update-status", "token": token})
        if status.get("ok") is not True:
            raise LifecycleError("update-status", _detail(status))
        if status.get("status") == "ready":
            return
        if clock() >= deadline:
            raise LifecycleError("update-status", "miner cgroup empty proof not ready")
        pause(POLL_INTERVAL)


def end_update(client: Client, token: str, outcome: Literal["commit", "abort"]) -> bool:
    """Commit or abort; an abort failure leaves the durable hold for the operator."""
    response = _exchange(client, {"cmd": "end-update", "token": token, "outcome": outcome})
    return response.get("ok") is True


def update(*, geteuid: Callable[[], int] = os.geteuid, trust: PathTrust = _DEFAULT_TRUST,
           client: Client = ipc.call, prepare: Callable[[Path], InstallRequest] = download_release,
           install: Callable[[InstallRequest, InstallContext], Installed] = update_flow,
           clock: Callable[[], float] = time.monotonic, pause: Callable[[float], None] = time.sleep,
           request_id: str | None = None) -> int:
    """Verify and publish a newer signed release while the owner thread holds the lease."""
    if geteuid() != 0:
        print("sparehash: update requires root", file=sys.stderr)
        return 2
    if current_binary(trust) is None:
        print("sparehash: update requires a SpareHash-managed XMRig install; run install first", file=sys.stderr)
        return 1
    token: str | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="sparehash-update-") as directory:
            request = prepare(Path(directory))
            token = acquire(client, request_id or secrets.token_hex(16))
            await_ready(client, token, clock=clock, pause=pause)
            installed = install(request, InstallContext(trust=trust, gate=HeldLease(Generation(int(token, 16)))))
        if not end_update(client, token, "commit"):
            raise LifecycleError("end-update", "controller could not commit the lifecycle lease")
    except (OperationError, InstallError, DeploymentError, OSError, ValueError) as error:
        print(f"sparehash: update: {error}", file=sys.stderr)
        if token is not None:
            try:
                _ = end_update(client, token, "abort")
            except LifecycleError as abort_error:
                print(f"sparehash: update: abort unreachable; the controller retains the hold: {abort_error}",
                      file=sys.stderr)
        return 1
    print(f"xmrig: updated to {installed.version}; controller re-evaluates before any restart.")
    return 0
