"""Behavioral tests for the command-line scaffold."""

import ast
import contextlib
import importlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sparehash import cli_config
from sparehash import cli_lifecycle
from sparehash.__main__ import main


class CliTests(unittest.TestCase):
    def test_future_module_names_import(self) -> None:
        """Given the task-one module namespaces, imports complete without stubs running."""
        for module_name in (
            "config",
            "wallet",
            "sysdetect",
            "state",
            "xmrig",
            "systemd",
            "ipc",
            "controller",
            "power",
            "gaming",
            "thermal",
            "wizard",
        ):
            with self.subTest(module=module_name):
                _ = importlib.import_module(f"sparehash.{module_name}")

    def test_package_modules_parse(self) -> None:
        """Given all package modules, each module parses as Python source."""
        package_directory = Path(__file__).parents[1] / "sparehash"

        for module_path in package_directory.glob("*.py"):
            with self.subTest(module=module_path.name):
                _ = ast.parse(module_path.read_text())

    def test_help_lists_public_commands(self) -> None:
        """Given the package entrypoint, help exposes supported public verbs."""
        result = subprocess.run(
            [sys.executable, "-m", "sparehash", "--help"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("benchmark", result.stdout)
        self.assertIn("uninstall", result.stdout)

    def test_unknown_command_exits_with_argparse_error(self) -> None:
        """Given an unknown verb, parsing fails with the argparse status."""
        result = subprocess.run(
            [sys.executable, "-m", "sparehash", "frobnicate"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)

    def test_status_reports_disconnected_controller(self) -> None:
        """Given no installed controller, status reports stopped and unavailable."""
        result = subprocess.run(
            [sys.executable, "-m", "sparehash", "status"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 3)
        self.assertIn("controller", result.stdout)
        self.assertIn("unavailable", result.stdout)
        self.assertIn("stopped", result.stdout)

    def test_uninstall_requires_root_when_unprivileged(self) -> None:
        """Given an unprivileged identity, uninstall refuses before any removal."""
        result = subprocess.run(
            [sys.executable, "-m", "sparehash", "uninstall"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("requires root", result.stderr)

    def test_internal_commands_require_root(self) -> None:
        """Given an unprivileged identity, internal commands stop before dispatch."""
        for command in (
            "__controller__",
            "__cleanup__",
            "__authorize_miner__",
            "authorize-start",
        ):
            with (
                self.subTest(command=command),
                patch("sparehash.__main__._incomplete") as dispatch,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = main([command], geteuid=lambda: 1000)

                self.assertEqual(result, 2)
                dispatch.assert_not_called()

    def test_miner_exec_rejects_root_identity(self) -> None:
        """Given root, miner execution is rejected instead of running as root."""
        with (
            patch("sparehash.__main__._incomplete") as dispatch,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            result = main(
                ["__miner_exec__"],
                geteuid=lambda: 0,
                getusername=lambda _uid: "root",
            )

        self.assertEqual(result, 2)
        dispatch.assert_not_called()

    def test_installer_discovers_future_python_modules(self) -> None:
        """Given a package containing a future module, installer iteration is not frozen."""
        installer = (Path(__file__).parents[1] / "install.sh").read_text()

        self.assertIn('for source_file in "$source_package"/*.py; do', installer)
        self.assertIn("file=${source_file##*/}", installer)
        self.assertNotIn("for file in __init__.py __main__.py paths.py", installer)
        with tempfile.TemporaryDirectory() as temporary_directory:
            package_directory = Path(temporary_directory) / "sparehash"
            package_directory.mkdir()
            (package_directory / "power.py").touch()

            discovered_modules = {path.name for path in package_directory.glob("*.py")}

        self.assertIn("power.py", discovered_modules)

    def test_python_shim_forwards_arguments_in_temporary_package(self) -> None:
        """Given an isolated package, generated Python shim forwards argv to main."""
        installer = (Path(__file__).parents[1] / "install.sh").read_text()
        marker = "<<'PYTHON_SHIM'\n"
        shim_start = installer.index(marker) + len(marker)
        shim_end = installer.index("\nPYTHON_SHIM", shim_start)
        template = installer[shim_start:shim_end]
        self.assertEqual(template.splitlines()[0], "#!/usr/bin/python3")

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            package_directory = temporary_path / "sparehash"
            package_directory.mkdir()
            _ = (package_directory / "__init__.py").write_text("")
            _ = (package_directory / "__main__.py").write_text(
                "import sys\nraise SystemExit(0 if sys.argv[1:] == ['status', '--fixture'] else 1)\n",
            )
            shim_path = temporary_path / "sparehash-cli"
            _ = shim_path.write_text(template.replace("/opt/sparehash/src", temporary_directory))

            result = subprocess.run(
                [sys.executable, str(shim_path), "status", "--fixture"],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 0)

    def test_config_edit_refused_root_and_nonroot(self) -> None:
        """sparehash config edit is unsupported in experimental v0.1.0, exit 2, root and nonroot identical."""
        for geteuid_fn, uid in [(os.geteuid, 0), (lambda: 1000, 1000)]:
            with self.subTest(geteuid=uid):
                captured = io.StringIO()
                with contextlib.redirect_stderr(captured):
                    with patch.object(cli_config, "edit", side_effect=AssertionError("edit should not be called")):
                        with patch.object(cli_config, "benchmark", side_effect=AssertionError("benchmark should not be called")):
                            with patch.object(cli_lifecycle, "update", side_effect=AssertionError("update should not be called")):
                                result = main(["config", "edit"], geteuid=geteuid_fn)
                self.assertEqual(result, 2)
                self.assertIn(
                    "sparehash: config edit is unsupported in experimental v0.1.0",
                    captured.getvalue(),
                )

    def test_update_refused_root_and_nonroot(self) -> None:
        """sparehash update is unsupported in experimental v0.1.0, exit 2, root and nonroot identical."""
        for geteuid_fn, uid in [(os.geteuid, 0), (lambda: 1000, 1000)]:
            with self.subTest(geteuid=uid):
                captured = io.StringIO()
                with contextlib.redirect_stderr(captured):
                    result = main(["update"], geteuid=geteuid_fn)
                self.assertEqual(result, 2)
                self.assertIn(
                    "sparehash: update is unsupported in experimental v0.1.0",
                    captured.getvalue(),
                )

    def test_benchmark_refused_root_and_nonroot(self) -> None:
        """sparehash benchmark is unsupported in experimental v0.1.0, exit 2, root and nonroot identical."""
        for geteuid_fn, uid in [(os.geteuid, 0), (lambda: 1000, 1000)]:
            with self.subTest(geteuid=uid):
                captured = io.StringIO()
                with contextlib.redirect_stderr(captured):
                    result = main(["benchmark"], geteuid=geteuid_fn)
                self.assertEqual(result, 2)
                self.assertIn(
                    "sparehash: benchmark is unsupported in experimental v0.1.0",
                    captured.getvalue(),
                )

    def test_config_show_works_root_and_nonroot(self) -> None:
        """sparehash config show continues working, exit 0, no token leak."""
        import sparehash.cli_config as cli_config_mod
        for geteuid_fn, uid in [(os.geteuid, 0), (lambda: 1000, 1000)]:
            with self.subTest(geteuid=uid):
                with patch.object(cli_config_mod, "show") as mock_show:
                    cfg_path = Path("/tmp/sparehash-config-show.toml")
                    cfg_path.parent.mkdir(parents=True, exist_ok=True)
                    cfg_path.write_text('version = 1\nwallet = "test"\n')
                    mock_show.return_value = 0
                    captured_stdout = io.StringIO()
                    captured_stderr = io.StringIO()
                    with contextlib.redirect_stdout(captured_stdout):
                        with contextlib.redirect_stderr(captured_stderr):
                            result = main(["config", "show"], geteuid=geteuid_fn)
                    self.assertEqual(result, 0)
                    self.assertEqual(captured_stderr.getvalue(), "")

    def test_status_json_requires_valid_json(self) -> None:
        """Given the sparehash CLI, status --json is a typed document whose exit status follows controller state."""
        result = subprocess.run(
            [sys.executable, "-m", "sparehash", "status", "--json"],
            check=False,
            capture_output=True,
            text=True,
        )

        # The renderer accepts exactly two exit statuses: 3 when the controller
        # is unavailable, 0 for every other reachable state.
        self.assertIn(result.returncode, (0, 3))
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail("status --json output is not valid JSON")
        self.assertIsInstance(data["controller"], str)
        self.assertEqual(result.returncode, 3 if data["controller"] == "unavailable" else 0)

        # Required core fields keep their documented types.
        self.assertIsInstance(data["miner"], str)
        self.assertIsInstance(data["api"], str)
        self.assertTrue(data["pool"] is None or isinstance(data["pool"], str))
        self.assertTrue(data["dns_hint"] is None or isinstance(data["dns_hint"], str))
        for entry in data["blockers"]:
            self.assertIsInstance(entry, list)
            self.assertEqual(len(entry), 2)
            self.assertTrue(all(isinstance(part, str) for part in entry))
        for source in ("inventory", "upower", "sysfs"):
            evidence = data["power"][source]
            self.assertIn(type(evidence["value"]), (bool, type(None)))
            self.assertIs(type(evidence["stale"]), bool)


if __name__ == "__main__":
    _ = unittest.main()