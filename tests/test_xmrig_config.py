"""Renderer, protected publication and loopback wire behavior."""

from __future__ import annotations

import json
import grp
import os
import socket
import tempfile
import threading
import unittest
from collections.abc import Callable
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from sparehash.config import Api, Config, Cpu, Pool, Xmrig
from sparehash.xmrig_config import (
    PROPERTIES, ApiError, DryRunRequest, DryRunResult, JsonObject, RenderError,
    new_token, read_token, render_config, summary, validate_rendered, write_xmrig_json,
)


class RecordingRunner:
    """Mutable injected recorder; deliberately never executes candidate code."""

    def __init__(self, result: DryRunResult | None = None, failure: OSError | None = None) -> None:
        self.result: DryRunResult = result or DryRunResult(0, 65534, PROPERTIES, True)
        self.failure: OSError | None = failure
        self.requests: list[DryRunRequest] = []
        self.bytes: bytes = b""
        self.config: JsonObject = {}

    def __call__(self, request: DryRunRequest) -> DryRunResult:
        self.requests.append(request)
        self.bytes = request.snapshot.read_bytes()
        decode: Callable[[str], JsonObject] = json.loads
        self.config = decode(request.config.read_text(encoding="utf-8"))
        if self.failure is not None:
            raise self.failure
        return self.result


class ConfigTests(unittest.TestCase):
    token: str = new_token()
    missing: Path = Path("/nonexistent-sparehash-fixture/xmrig")
    cfg: Config = Config(wallet="wallet", donate_level=3,
                         pool=Pool("pool.example", 443, True, "worker"), api=Api(39001))

    def test_golden_when_each_cpu_mode_selected(self) -> None:
        # Given
        expected: JsonObject = {
            "autosave": False, "background": False, "colors": False, "syslog": False,
            "print-time": 60, "donate-level": 3, "pause-on-battery": False, "pause-on-active": 0,
            "randomx": {"1gb-pages": False, "rdmsr": False, "wrmsr": False, "numa": True},
            "pools": [{"url": "pool.example:443", "user": "wallet", "pass": "worker",
                       "tls": True, "keepalive": True, "nicehash": False}],
            "http": {"enabled": True, "host": "127.0.0.1", "port": 39001,
                     "access-token": self.token, "restricted": True},
        }
        modes: tuple[tuple[Cpu, JsonObject], ...] = (
            (Cpu(), {"enabled": True}),
            (Cpu(mode="spare-capped", max_threads_percent=42), {"enabled": True, "max-threads-hint": 42}),
            (Cpu(mode="custom", custom_json='{"enabled":false,"rx":[0,2]}'), {"enabled": False, "rx": [0, 2]}),
        )
        for cpu, fragment in modes:
            with self.subTest(mode=cpu.mode):
                # When
                actual = render_config(replace(self.cfg, cpu=cpu), self.token, self.missing)
                # Then
                self.assertEqual(actual, {**expected, "cpu": fragment})

    def test_rejects_when_custom_json_is_invalid(self) -> None:
        # Given
        for raw in ("not json", "[]", "null", '"text"', "", '{"rx":NaN}'):
            with self.subTest(raw=raw):
                cfg = replace(self.cfg, cpu=Cpu(mode="custom", custom_json=raw))
                # When / Then
                with self.assertRaises(RenderError):
                    _ = render_config(cfg, self.token, self.missing)

    def test_token_round_trip_when_config_published(self) -> None:
        # Given: inject only privileged ownership syscall on this unprivileged host.
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / "xmrig.json"
            cfg = replace(self.cfg, xmrig=Xmrig(str(self.missing)))
            with patch("sparehash.xmrig_config.grp.getgrnam", return_value=grp.struct_group(("sparehash", "x", os.getgid(), []))), patch("sparehash.xmrig_config.os.fchown") as owner:
                # When
                write_xmrig_json(cfg, self.token, dest)
            # Then
            self.assertEqual(read_token(dest), self.token)
            self.assertEqual(dest.stat().st_mode & 0o777, 0o640)
            owner.assert_called_once()
            self.assertEqual(owner.call_args.args[1:], (0, os.getgid()))
            self.assertRegex(self.token, r"^[0-9a-f]{64}$")

    def test_publish_fails_closed_when_chown_denied(self) -> None:
        # Given
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / "xmrig.json"
            with patch("sparehash.xmrig_config.grp.getgrnam", return_value=grp.struct_group(("sparehash", "x", os.getgid(), []))), patch("sparehash.xmrig_config.os.fchown", side_effect=PermissionError):
                # When / Then
                with self.assertRaises(PermissionError):
                    write_xmrig_json(self.cfg, self.token, dest)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_rejects_when_token_is_malformed(self) -> None:
        # Given
        for token in ("", "short", "g" * 64, "a" * 64 + "\n"):
            # When / Then
            with self.assertRaises(RenderError) as caught:
                _ = render_config(self.cfg, token, self.missing)
            self.assertNotIn("short", str(caught.exception))

    def test_dry_run_contract_when_malicious_candidate_present(self) -> None:
        # Given: candidate would leave a marker if directly executed.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker, candidate = root / "root-side-effect", root / "xmrig"
            payload = f'#!/bin/sh\necho "XMRig 6.26.0"\ncase "$*" in *--dry-run*) touch "{marker}";; esac\n'
            _ = candidate.write_text(payload, encoding="utf-8")
            candidate.chmod(0o755)
            runner = RecordingRunner()
            cfg = replace(self.cfg, cpu=Cpu(mode="custom", custom_json='{"rx":[1]}'))
            # When
            rendered = render_config(cfg, self.token, candidate, runner=runner)
            # Then: injected contract proof only, not real sandbox execution.
            self.assertFalse(marker.exists())
            self.assertEqual(runner.bytes, payload.encode())
            self.assertEqual(len(runner.requests), 1)
            request = runner.requests[0]
            self.assertNotEqual(request.snapshot, candidate)
            self.assertEqual((request.uid, request.timeout, request.required_properties), (65534, 30, PROPERTIES))
            self.assertNotIn(self.token, json.dumps(runner.config))
            self.assertEqual(runner.config["cpu"], rendered["cpu"])
            self.assertFalse(request.snapshot.exists())
            self.assertFalse(request.config.exists())

    def test_dry_run_fails_closed_when_runner_missing_or_invalid(self) -> None:
        # Given
        results = (DryRunResult(0, 0, PROPERTIES, True), DryRunResult(0, 65534, (), True),
                   DryRunResult(0, 65534, PROPERTIES, False), DryRunResult(2, 65534, PROPERTIES, True, self.token))
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "xmrig"
            _ = candidate.write_bytes(b"candidate is never executed")
            rendered = render_config(self.cfg, self.token, self.missing)
            for runner in (None, *(RecordingRunner(result) for result in results),
                           RecordingRunner(failure=TimeoutError(self.token)), RecordingRunner(failure=PermissionError(self.token))):
                # When / Then
                with self.assertRaises(RenderError) as caught:
                    validate_rendered(rendered, candidate, runner)
                self.assertNotIn(self.token, str(caught.exception))
                if runner is not None:
                    self.assertFalse(runner.requests[0].config.exists())

    def test_custom_rejects_when_present_binary_has_no_runner(self) -> None:
        # Given
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "xmrig"
            _ = candidate.write_bytes(b"not executable")
            cfg = replace(self.cfg, cpu=Cpu(mode="custom", custom_json="{}"))
            # When / Then
            with self.assertRaisesRegex(RenderError, "runner is required"):
                _ = render_config(cfg, self.token, candidate)


class ApiTests(unittest.TestCase):
    def test_summary_fails_closed_when_response_is_invalid_or_redirected(self) -> None:
        # Given
        for status, payload in ((302, b""), (200, b"[]"), (200, b"not JSON"), (200, b" " * (1024 * 1024 + 1))):
            with self.subTest(status=status, size=len(payload)):
                class Handler(BaseHTTPRequestHandler):
                    def do_GET(self) -> None:
                        self.send_response(status)
                        self.send_header("Location", "http://127.0.0.1:1/redirect-target")
                        self.end_headers()
                        _ = self.wfile.write(payload)

                with patch.object(Handler, "log_message"), HTTPServer(("127.0.0.1", 0), Handler) as server:
                    thread = threading.Thread(target=server.handle_request)
                    thread.start()
                    try:
                        # When / Then
                        with self.assertRaises(ApiError) as caught:
                            _ = summary("127.0.0.1", server.server_port, new_token())
                        if status == 302:
                            self.assertEqual(caught.exception.reason, "HTTP status 302")
                    finally:
                        thread.join(timeout=5)

    def test_summary_when_local_server_receives_bearer(self) -> None:
        # Given
        token = new_token()
        received: list[tuple[str, str | None]] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                received.append((self.path, self.headers.get("Authorization")))
                self.send_response(200)
                self.end_headers()
                _ = self.wfile.write(b'{"hashrate":{"total":[123.5]},"pool":"local"}')

        with patch.object(Handler, "log_message"), HTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.handle_request)
            thread.start()
            try:
                # When
                result = summary("127.0.0.1", server.server_port, token)
            finally:
                thread.join(timeout=5)
            # Then
            self.assertEqual(result, {"hashrate": {"total": [123.5]}, "pool": "local"})
            self.assertEqual(received, [("/1/summary", f"Bearer {token}")])

    def test_summary_rejects_when_host_is_not_literal_loopback(self) -> None:
        # Given
        for host in ("localhost", "::1", "0.0.0.0", "127.0.0.2", "127.0.0.1@remote", "remote"):
            # When / Then
            with self.assertRaises(ApiError):
                _ = summary(host, 38087, new_token())

    def test_summary_reports_connection_refused_when_port_closed(self) -> None:
        # Given: bound but non-listening port cannot be claimed by another test.
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            address: Callable[[], tuple[str, int]] = reserved.getsockname
            port, token = address()[1], new_token()
            # When / Then
            with self.assertRaises(ApiError) as caught:
                _ = summary("127.0.0.1", port, token)
            self.assertIn("Connection refused", str(caught.exception))
            self.assertIn(f"127.0.0.1:{port}", str(caught.exception))
            self.assertNotIn(token, str(caught.exception))


if __name__ == "__main__":
    _ = unittest.main()
