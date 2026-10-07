"""Behavioral tests for the command-line scaffold."""

import ast
import contextlib
import importlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_unfinished_public_command_remains_unavailable(self) -> None:
        """Given an unfinished public command, dispatch still exits nonzero."""
        result = subprocess.run(
            [sys.executable, "-m", "sparehash", "uninstall"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("unavailable", result.stderr)

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


if __name__ == "__main__":
    _ = unittest.main()
