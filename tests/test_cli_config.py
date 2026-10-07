"""Offline config publication and same-unit operation boundaries."""

import contextlib
import grp
import io
import json
import os
import tempfile
import unittest
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from typing import final

from sparehash import cli_config as cli, ipc
from sparehash.__main__ import main
from sparehash.config import Api, Config, Controller, Cpu, Gaming, Hugepages, Pool, Thermal, Xmrig, load, save
from sparehash.wizard import run_interactive
from sparehash.xmrig_config import read_token, write_xmrig_json
from tests.test_wizard import ACCOUNT, CAPS, SENSOR, WALLET
from tests.test_xmrig_config import RecordingRunner


class Client:
    def __init__(self) -> None:
        self.requests: list[ipc.Document] = []
        self.reply: ipc.Document = {"ok": True}
        self.reload: ipc.Document = {"ok": True}
        self.blockers: list[ipc.Json] = []

    def __call__(self, command: ipc.Document, /, *, timeout: float) -> ipc.Document:
        self.requests.append(command)
        assert timeout == 1
        match command.get("cmd"):
            case "status":
                return {"ok": True, "blockers": self.blockers}
            case "reload":
                return self.reload
            case _:
                return self.reply


@final
class ConfigTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = cli.ConfigFiles(self.root / "config.toml", self.root / "xmrig.json", self.root, os.getuid())
        self.token = "a" * 64
        self.current = Config(wallet=WALLET, pool=Pool("pool.example", 4444, True, "before"),
                              gaming=Gaming(False), xmrig=Xmrig(str(self.root / "missing")),
                              api=Api(39001), controller=Controller(19, 7))
        self.client = Client()
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.public_before = b""
        self.miner_before = b""

    def _setup(self) -> None:
        _ = self.enterContext(contextlib.redirect_stdout(self.stdout))
        _ = self.enterContext(contextlib.redirect_stderr(self.stderr))
        _ = self.enterContext(patch("sparehash.xmrig_config.grp.getgrnam", return_value=grp.struct_group(("sparehash", "x", os.getgid(), []))))
        _ = self.enterContext(patch("sparehash.xmrig_config.os.fchown"))
        save(self.current, self.files.public, warn_overwrite=False)
        write_xmrig_json(self.current, self.token, self.files.miner)
        self.public_before = self.files.public.read_bytes()
        self.miner_before = self.files.miner.read_bytes()

    setUp = _setup

    def edit(self, *, confirm: str = "yes", runner: RecordingRunner | None = None) -> int:
        return cli.edit(self.files, lambda cfg: replace(cfg, pool=replace(cfg.pool, worker="after")),
                        geteuid=lambda: 0, confirm=lambda _: confirm,
                        client=self.client, runner=runner, capabilities=lambda: CAPS)

    def assert_unchanged(self) -> None:
        self.assertEqual(self.files.public.read_bytes(), self.public_before)
        self.assertEqual(self.files.miner.read_bytes(), self.miner_before)

    def test_show_when_generated_token_exists(self) -> None:
        # Given / When
        code = cli.show(self.files.public)
        # Then
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.stdout.getvalue()), self.current.to_dict())
        self.assertNotIn(self.token, self.stdout.getvalue() + self.stderr.getvalue())

    def test_seeded_wizard_when_answers_are_empty(self) -> None:
        # Given
        current = replace(self.current, donate_level=7, cpu=Cpu("custom", 31, '{"rx":[1]}'),
                          thermal=Thermal(True, 88, 72), gaming=Gaming(True, (ACCOUNT,)),
                          hugepages=Hugepages(True, 700))
        # When
        updated = run_interactive(CAPS, lambda _: "", lambda _: None, current=current, sensor_probe=lambda: SENSOR)
        # Then
        self.assertEqual(updated, current)

    def test_declined_when_overwrite_is_not_explicit(self) -> None:
        # Given / When
        code = self.edit(confirm="y")
        # Then
        self.assertEqual(code, 0)
        self.assert_unchanged()
        self.assertEqual(self.client.requests, [])
        self.assertEqual(len(list(self.root.iterdir())), 2)

    def test_published_when_confirmed_preserves_token_and_backup(self) -> None:
        # Given
        before_inode = self.files.public.stat().st_ino
        # When
        code = self.edit()
        # Then
        self.assertEqual(code, 0)
        self.assertEqual(load(self.files.public).pool.worker, "after")
        self.assertNotEqual(self.files.public.stat().st_ino, before_inode)
        self.assertEqual(read_token(self.files.miner), self.token)
        self.assertEqual(self.files.miner.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.client.requests, [{"cmd": "status"}, {"cmd": "reload"}])
        self.assertEqual([p.read_bytes() for p in self.root.glob(".sparehash-backup-*")], [self.public_before])

    def test_rollback_when_reload_is_rejected(self) -> None:
        # Given
        self.client.reload = {"ok": False, "error": self.token}
        # When
        code = self.edit()
        # Then
        self.assertEqual(code, 1)
        self.assert_unchanged()
        self.assertNotIn(self.token, self.stderr.getvalue())

    def test_rollback_when_protected_publication_denied(self) -> None:
        # Given
        with patch("sparehash.cli_config.write_xmrig_json", side_effect=PermissionError(self.token)):
            # When
            code = self.edit()
        # Then
        self.assertEqual(code, 1)
        self.assert_unchanged()
        self.assertEqual(self.client.requests, [{"cmd": "status"}])

    def test_denied_when_nonroot_before_editor_or_ipc(self) -> None:
        # Given / When
        code = cli.edit(self.files, geteuid=lambda: 1000, client=self.client)
        # Then
        self.assertEqual(code, 2)
        self.assert_unchanged()
        self.assertEqual(self.client.requests, [])

    def test_missing_token_when_edit_never_rotates_credentials(self) -> None:
        # Given
        self.files.miner.unlink()
        # When
        code = self.edit()
        # Then
        self.assertEqual(code, 1)
        self.assertFalse(self.files.miner.exists())
        self.assertEqual(self.files.public.read_bytes(), self.public_before)

    def test_invalid_when_config_is_malformed_or_custom_json_invalid(self) -> None:
        # Given
        for raw in ("[pool", 'wallet="bad"', self.public_before.decode().replace('mode = "spare"', 'mode = "custom"')):
            with self.subTest(raw=raw):
                _ = self.files.public.write_text(raw)
                # When
                code = self.edit()
                # Then
                self.assertEqual(code, 1)
                self.assertEqual(self.files.public.read_text(), raw)
                self.assertEqual(self.client.requests, [])

    def test_denied_when_parent_or_source_is_unsafe(self) -> None:
        # Given
        self.files.public.unlink()
        self.files.public.symlink_to(self.files.miner)
        # When
        code = self.edit()
        # Then
        self.assertEqual(code, 1)
        self.assertEqual(self.files.miner.read_bytes(), self.miner_before)

    def test_denied_when_present_binary_has_no_isolated_adapter(self) -> None:
        # Given
        binary = self.root / "candidate"
        _ = binary.write_bytes(b"never executed")
        cfg = replace(self.current, xmrig=Xmrig(str(binary)))
        save(cfg, self.files.public, warn_overwrite=False)
        self.public_before = self.files.public.read_bytes()
        # When
        code = self.edit()
        # Then
        self.assertEqual(code, 1)
        self.assert_unchanged()
        self.assertEqual(self.client.requests, [])

    def test_probe_when_fake_runner_receives_private_redacted_snapshot(self) -> None:
        # Given
        binary = self.root / "candidate"
        _ = binary.write_bytes(b"never executed")
        save(replace(self.current, xmrig=Xmrig(str(binary))), self.files.public, warn_overwrite=False)
        runner = RecordingRunner()
        # When
        code = self.edit(runner=runner)
        # Then
        self.assertEqual(code, 0)
        self.assertEqual(runner.bytes, b"never executed")
        self.assertEqual(runner.requests[0].uid, 65534)
        self.assertNotIn(self.token, json.dumps(runner.config))
        self.assertEqual(read_token(self.files.miner), self.token)

    def test_denied_when_controller_socket_is_missing(self) -> None:
        # Given
        with patch("sparehash.ipc.socket_path", return_value=self.root / "missing.sock"):
            # When
            code = cli.edit(self.files, lambda cfg: cfg, geteuid=lambda: 0,
                            confirm=lambda _: "yes", capabilities=lambda: CAPS)
        # Then
        self.assertEqual(code, 1)
        self.assert_unchanged()
        self.assertEqual(len(list(self.root.iterdir())), 2)

    def test_denied_when_injected_editor_returns_invalid_wallet(self) -> None:
        # Given / When
        code = cli.edit(self.files, lambda cfg: replace(cfg, wallet="invalid"),
                        geteuid=lambda: 0, confirm=lambda _: "yes", client=self.client,
                        capabilities=lambda: CAPS)
        # Then
        self.assertEqual(code, 1)
        self.assert_unchanged()

    def test_denied_when_parent_is_writable(self) -> None:
        # Given
        self.root.chmod(0o777)
        # When
        code = self.edit()
        # Then
        self.assertEqual(code, 1)
        self.assert_unchanged()


@final
class BenchmarkTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.client = Client()
        self.stdout, self.stderr = io.StringIO(), io.StringIO()

    def _setup(self) -> None:
        _ = self.enterContext(contextlib.redirect_stdout(self.stdout))
        _ = self.enterContext(contextlib.redirect_stderr(self.stderr))

    setUp = _setup

    def test_root_denied_when_cli_invoked_by_nonroot(self) -> None:
        # Given / When
        code = cli.benchmark(self.client, geteuid=lambda: 1000)
        # Then
        self.assertEqual(code, 2)
        self.assertEqual(self.client.requests, [])

    def test_denied_when_controller_reports_blocker(self) -> None:
        # Given
        self.client.reply = {"ok": False}
        for name in ("battery", "gaming", "manual", "thermal", "unknown"):
            self.client.blockers = [name]
            # When
            code = cli.benchmark(self.client, geteuid=lambda: 0)
            # Then
            self.assertEqual(code, 1)
            self.assertIn(name, self.stderr.getvalue())

    def test_error_when_controller_unavailable(self) -> None:
        # Given
        def missing(_command: ipc.Document, /, *, timeout: float) -> ipc.Document:
            assert timeout == 1
            raise ipc.IPCError("unavailable")
        # When
        code = cli.benchmark(missing, geteuid=lambda: 0)
        # Then
        self.assertEqual(code, 1)

    def test_fixed_units_when_operation_output_is_injected(self) -> None:
        for outcome in cli.BenchmarkOutcome:
            # Given
            def follow(units: tuple[str, str], emit: Callable[[str], None]) -> cli.BenchmarkOutcome:
                self.assertEqual(units, ("sparehash.service", "sparehash-xmrig.service"))
                emit("a" * 64)
                return outcome
            # When
            code = cli.benchmark(self.client, follow, lambda: 0)
            # Then
            self.assertEqual(code, 0 if outcome is cli.BenchmarkOutcome.COMPLETED else 1)
            self.assertEqual(self.client.requests[-1], {"cmd": "benchmark"})
            self.assertNotIn("a" * 64, self.stdout.getvalue())

    def test_pending_when_completion_adapter_absent(self) -> None:
        # Given / When
        code = cli.benchmark(self.client, geteuid=lambda: 0)
        # Then
        self.assertEqual(code, 3)
        self.assertEqual(self.client.requests, [{"cmd": "benchmark"}])

    def test_grammar_when_config_action_missing_or_nonroot_edit(self) -> None:
        # Given / When / Then
        with self.assertRaises(SystemExit) as caught:
            _ = main(["config"])
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(main(["config", "edit"], geteuid=lambda: 1000), 2)
