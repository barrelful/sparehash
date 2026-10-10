# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""XMRig configuration boundary and loopback-only summary client."""

from __future__ import annotations

import grp
import json
import math
import os
import re
import secrets
import subprocess
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from http.client import HTTPResponse
from typing import Final, Protocol, TypeAlias, assert_never
from urllib.error import HTTPError, URLError
from urllib.request import HTTPHandler, OpenerDirector, ProxyHandler, Request

from .config import Config, ConfigError, validate
from .paths import XMRIG_CURRENT, XMRIG_JSON
from .xmrig_paths import PathTrust, snapshot

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
PROPERTIES: Final = ("ProtectSystem=strict", "ProtectHome=yes", "PrivateNetwork=yes",
                     "NoNewPrivileges=yes", "PrivateTmp=yes")
MAX_RESPONSE: Final = 1024 * 1024


@dataclass(frozen=True, slots=True)
class RenderError(ConfigError):
    reason: str

    def __post_init__(self) -> None:
        ConfigError.__init__(self, self.reason)


@dataclass(frozen=True, slots=True)
class ApiError(Exception):
    port: int
    reason: str

    def __post_init__(self) -> None:
        Exception.__init__(self, f"XMRig API at 127.0.0.1:{self.port}: {self.reason}")


class CpuMode(StrEnum):
    SPARE = "spare"
    CAPPED = "spare-capped"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class DryRunRequest:
    snapshot: Path
    config: Path
    uid: int = 65534
    timeout: int = 30
    required_properties: tuple[str, ...] = PROPERTIES


@dataclass(frozen=True, slots=True)
class DryRunResult:
    returncode: int
    effective_uid: int
    properties: tuple[str, ...]
    inputs_readable: bool
    stderr: str = ""


class IsolatedRunner(Protocol):
    """Todo11/18 adapter seam, not a production implementation.

    Execute ONLY request.snapshot with --config request.config --dry-run in the
    required transient sandbox. Stage protected inputs readable by request.uid
    (detector snapshots may be root:0500), preserving private snapshot bytes.
    Attest actual effective UID, applied properties and input readability;
    bound output/time, destroy the unit on every outcome. Never execute the
    original candidate or retry as root. Raise OSError/TimeoutError on failure.
    """

    def __call__(self, request: DryRunRequest) -> DryRunResult: ...


class _HttpOpen(Protocol):
    def __call__(self, request: Request, /, *, timeout: float) -> HTTPResponse: ...


class _JsonDecoder(Protocol):
    def __call__(self, raw: str | bytes, /) -> JsonValue: ...


def _json_object(raw: str | bytes, decoder: _JsonDecoder = json.loads) -> JsonObject:
    """Parse an object at the JSON boundary; reject nonstandard numeric values."""
    try:
        value = decoder(raw)
        # dumps rejects nested NaN/Infinity accepted by the stdlib decoder.
        _ = json.dumps(value, allow_nan=False)
    except (ValueError, UnicodeError, RecursionError):
        raise RenderError("expected valid JSON object") from None
    if not isinstance(value, dict):
        raise RenderError("expected JSON object")
    return value


def _token(token: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", token) is None:
        raise RenderError("access token must be 32-byte lowercase hexadecimal")
    return token


def new_token() -> str:
    return secrets.token_hex(32)


def render_config(cfg: Config, token: str, binary: Path, *,
                  runner: IsolatedRunner | None = None) -> JsonObject:
    """Render approved keys; custom mode validates existing binaries in isolation.

    The keyword-only runner extends the plan's three-argument interface: a
    present binary without that adapter must fail closed, not run directly.
    """
    errors = validate(cfg)
    if errors:
        raise RenderError("; ".join(errors))
    cpu: JsonObject = {"enabled": True}
    mode = CpuMode(cfg.cpu.mode)
    match mode:
        case CpuMode.SPARE:
            pass
        case CpuMode.CAPPED:
            cpu["max-threads-hint"] = cfg.cpu.max_threads_percent
        case CpuMode.CUSTOM:
            cpu.update(_json_object(cfg.cpu.custom_json))
        case _:
            assert_never(mode)
    rendered: JsonObject = {
        "autosave": False, "background": False, "colors": False, "syslog": False,
        "print-time": 60, "donate-level": cfg.donate_level,
        "pause-on-battery": False, "pause-on-active": 0,
        "randomx": {"1gb-pages": False, "rdmsr": False, "wrmsr": False, "numa": True},
        "cpu": cpu,
        "pools": [{"url": f"{cfg.pool.host}:{cfg.pool.port}", "user": cfg.wallet,
                   "pass": cfg.pool.worker, "tls": cfg.pool.tls, "keepalive": True, "nicehash": False}],
        "http": {"enabled": True, "host": "127.0.0.1", "port": cfg.api.port,
                 "access-token": _token(token), "restricted": True},
    }
    if mode is CpuMode.CUSTOM and os.path.lexists(binary):
        validate_rendered(rendered, binary, runner)
    return rendered


def validate_rendered(rendered: JsonObject, binary: Path, runner: IsolatedRunner | None) -> None:
    """Explicit later-install hook for validating every mode; no direct execution."""
    if runner is None:
        raise RenderError("isolated unprivileged dry-run runner is required")
    with tempfile.TemporaryDirectory(prefix="sparehash-dry-run-") as directory:
        private = Path(directory)
        if binary == XMRIG_CURRENT:
            trust = PathTrust()
            copied = trust.current_snapshot(private, Path("/opt/xmrig/.sparehash-managed"),
                                             Path("/var/lib/sparehash/install-manifest.json"))
        else:
            copied = snapshot(binary, private_dir=private)
        if copied is None:
            raise RenderError("binary could not be safely snapshotted")
        # No real API credential is handed to potentially hostile candidate code.
        safe = _json_object(json.dumps(rendered, allow_nan=False))
        http = safe.get("http")
        if not isinstance(http, dict):
            raise RenderError("rendered config requires an HTTP object")
        http["access-token"] = "0" * 64
        config = private / "config.json"
        with config.open("x", encoding="utf-8") as output:
            os.fchmod(output.fileno(), 0o600)
            _ = output.write(json.dumps(safe, indent=2, allow_nan=False))
        try:
            result = runner(DryRunRequest(copied[0], config))
        except (OSError, subprocess.SubprocessError, TimeoutError):
            raise RenderError("isolated dry-run failed or timed out; no root fallback") from None
        if (result.effective_uid != 65534 or not result.inputs_readable
                or not set(PROPERTIES).issubset(result.properties)):
            raise RenderError("dry-run did not attest required nonroot isolation/readable inputs")
        if result.returncode != 0:
            # Suppress runner-supplied raw exceptions; sanitize bounded stderr too.
            detail = re.sub(r"[0-9a-fA-F]{64}", "[redacted]", result.stderr[:4096])
            raise RenderError(f"isolated XMRig dry-run rejected config (exit {result.returncode}): {detail}")


def write_xmrig_json(cfg: Config, token: str, dest: Path = XMRIG_JSON, *,
                     runner: IsolatedRunner | None = None) -> None:
    """Atomically publish root:sparehash 0640 or surface permission errors.

    No ownership downgrade on unprivileged hosts; injected tests use their own
    identity for fchown only, never a production fallback.
    """
    rendered = render_config(cfg, token, Path(cfg.xmrig.binary), runner=runner)
    gid = grp.getgrnam("sparehash").gr_gid
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=dest.parent, delete=False) as output:
            temporary = Path(output.name)
            os.fchown(output.fileno(), 0, gid)
            os.fchmod(output.fileno(), 0o640)
            _ = output.write(json.dumps(rendered, indent=2, allow_nan=False) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, dest)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_token(path: Path = XMRIG_JSON) -> str:
    with path.open("rb") as source:
        raw = source.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise RenderError("XMRig config exceeds size limit")
    http = _json_object(raw).get("http")
    if not isinstance(http, dict):
        raise RenderError("XMRig config is missing its access token")
    token = http.get("access-token")
    if not isinstance(token, str):
        raise RenderError("invalid access token")
    return _token(token)


def _fetch(open_response: _HttpOpen, request: Request, timeout: float) -> tuple[int, bytes]:
    with open_response(request, timeout=timeout) as response:
        return response.status, response.read(MAX_RESPONSE + 1)


def summary(host: str, port: int, token: str, timeout: float = 5) -> JsonObject:
    """Use direct bounded urllib HTTP; disallow proxies and redirect handlers.

    Four arguments preserve the explicitly approved API client interface.
    """
    if host != "127.0.0.1" or not 1 <= port <= 65535:
        raise ApiError(port, "only 127.0.0.1 and a valid port are permitted")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ApiError(port, "timeout must be finite and positive")
    credential = _token(token)
    request = Request(f"http://127.0.0.1:{port}/1/summary",
                      headers={"Authorization": f"Bearer {credential}"})
    direct = OpenerDirector()
    for handler in (ProxyHandler({}), HTTPHandler()):
        direct.add_handler(handler)
    try:
        status, raw = _fetch(direct.open, request, timeout)
        if status != 200:
            raise ApiError(port, f"HTTP status {status}")
        if len(raw) > MAX_RESPONSE:
            raise ApiError(port, "summary exceeds size limit")
        return _json_object(raw)
    except HTTPError as exc:
        raise ApiError(port, f"HTTP status {exc.code}") from None
    except (URLError, OSError) as exc:
        reason = str(exc.reason) if isinstance(exc, URLError) else str(exc)
        raise ApiError(port, reason.replace(credential, "[redacted]")[:512]) from None
    except RenderError:
        raise ApiError(port, "invalid JSON summary object") from None
