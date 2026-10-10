#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# python3 qa/local_pool.py 600 (seconds; binds ONLY 127.0.0.1:3333)
"""Bounded Monero JSON-RPC fixture: synthetic rx/0 work, never external mining.

Configure QA miner JSONs with ONLY 127.0.0.1:3333, tls=false, donate-level=0.
Start before the real controller. One connection at a time; reconnect supported.
The fixed job has no chain value. Shares are rejected, never submitted elsewhere.
"""
from __future__ import annotations

import json
import math
import socket
import sys
import time
from typing import Final, TypedDict


class Job(TypedDict):
    job_id: str
    blob: str
    target: str
    algo: str
    seed_hash: str
    height: int


JOB: Final[Job] = {
    "job_id": "local-qa-only",
    "blob": "0e0e" + "00" * 74,  # valid hashing blob length, nonce offset 39
    "target": "01000000",  # high difficulty: bound share traffic
    "algo": "rx/0",
    "seed_hash": "00" * 32,
    "height": 1,
}


class LoginResult(TypedDict):
    id: str
    job: Job
    status: str


class StatusResult(TypedDict):
    status: str


class RpcError(TypedDict):
    code: int
    message: str


class Reply(TypedDict):
    id: int
    jsonrpc: str
    error: RpcError | None
    result: LoginResult | StatusResult | None


def response(line: bytes) -> bytes:
    """Parse bounded JSON-RPC requests; Monero uses login, not mining.subscribe."""
    raw = json.loads(line)
    match raw:
        case {"id": int(request_id), "method": str(method)}:
            pass
        case _:
            raise SystemExit("invalid fixture JSON-RPC request")
    result: Reply = {"id": request_id, "jsonrpc": "2.0", "error": None,
                     "result": {"status": "OK"}}
    if method == "login":
        result["result"] = {"id": "local-qa", "job": JOB, "status": "OK"}
    elif method == "submit":
        result["error"] = {"code": -1, "message": "synthetic QA share rejected"}
        result["result"] = None
    return (json.dumps(result) + "\n").encode()


def serve(server: socket.socket, deadline: float) -> None:
    """Single-client synchronous service with bounded lifetime and input buffer."""
    while time.monotonic() < deadline:
        server.settimeout(min(1, deadline - time.monotonic()))
        try:
            connection, _ = server.accept()
        except TimeoutError:
            continue
        with connection:
            buffer = b""
            while time.monotonic() < deadline:
                connection.settimeout(min(1, deadline - time.monotonic()))
                try:
                    data = connection.recv(4096)
                    if not data:
                        break
                    buffer += data
                    if len(buffer) > 16384:
                        break
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        connection.sendall(response(line))
                except TimeoutError:
                    continue
                except (ConnectionError, json.JSONDecodeError) as error:
                    print(f"fixture client disconnected: {error}", flush=True)
                    break


def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 600.0
    if not math.isfinite(seconds) or not 0 < seconds <= 1800:
        raise SystemExit("fixture duration must be between 0 and 1800 seconds")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 3333))
        server.listen(1)
        print("LOCAL ONLY fixture ready on 127.0.0.1:3333", flush=True)
        serve(server, time.monotonic() + seconds)


if __name__ == "__main__":
    main()
