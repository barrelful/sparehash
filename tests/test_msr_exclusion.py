from __future__ import annotations

import tempfile
import unittest
import warnings
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

from sparehash.config import Config, ConfigError, Cpu, load
from sparehash.__main__ import PUBLIC_COMMANDS, main
from sparehash.paths import UNIT_CONTROLLER, UNIT_MINER
from sparehash.sysdetect import Capabilities
from sparehash.systemd import render
from sparehash.wizard import run_interactive
from sparehash.xmrig_config import new_token, render_config


ROOT = Path(__file__).resolve().parents[1]


class MsrExclusionTests(unittest.TestCase):
    def test_enabled_legacy_config_fails_before_destination_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "config.toml"
            destination = root / "generated.toml"
            _ = source.write_text("[msr]\nenabled = true\n", encoding="utf-8")

            with self.assertRaises(ConfigError):
                _ = load(source)
            self.assertFalse(destination.exists())
            self.assertEqual(list(root.iterdir()), [source])

    def test_disabled_legacy_config_warns_and_never_serializes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            _ = path.write_text("[msr]\nenabled = false\n", encoding="utf-8")
            with warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always")
                config = load(path)
            self.assertTrue(any("msr" in str(item.message).lower() for item in captured))
            self.assertNotIn("msr", config.to_dict())

    def test_legacy_msr_unknown_fields_and_true_like_values_fail(self) -> None:
        for value in ("0", '"false"', "no", "true"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.toml"
                _ = path.write_text(f"[msr]\nenabled = {value}\n", encoding="utf-8")
                with self.assertRaises(ConfigError):
                    _ = load(path)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            _ = path.write_text("[msr]\nenabled = false\nboost = true\n", encoding="utf-8")
            with warnings.catch_warnings():
                warnings.simplefilter("error", UserWarning)
                with self.assertRaises((ConfigError, UserWarning)):
                    _ = load(path)

    def test_custom_cpu_settings_cannot_enable_randomx_msr(self) -> None:
        for fragment in ('{"rdmsr":true,"wrmsr":true}', '{"randomx":{"rdmsr":true,"wrmsr":true}}'):
            with self.subTest(fragment=fragment):
                rendered = render_config(Config(cpu=Cpu(mode="custom", custom_json=fragment)),
                                         new_token(), Path("/missing-fixture-xmrig"))
                self.assertEqual(rendered["randomx"], {
                    "1gb-pages": False, "rdmsr": False, "wrmsr": False, "numa": True,
                })

    def test_rendered_units_are_only_controller_and_unprivileged_miner(self) -> None:
        caps = Capabilities("x86_64", True, 255, True, "cgroup2", True, True, False,
                            (0,), (), 8, 8 * 1024**3, None, None, True)
        units = render(caps)
        self.assertEqual(set(units), {UNIT_CONTROLLER, UNIT_MINER})
        miner = units[UNIT_MINER]
        self.assertIn("User=xmrig", miner)
        self.assertNotIn("CAP_SYS_RAWIO", miner)
        self.assertNotIn("modprobe", miner)
        self.assertNotIn("insmod", miner)
        self.assertNotIn("setcap", miner)

    def test_interactive_setup_has_no_msr_question(self) -> None:
        replies = iter(("4" + "A" * 94, "pool.example", "", "", "worker", "", "", "n", "n"))
        prompts: list[str] = []
        caps = Capabilities("x86_64", True, 255, True, "cgroup2", True, True, False,
                            (0,), (), 8, 8 * 1024**3, None, None, True)
        _ = run_interactive(caps, lambda prompt: (prompts.append(prompt), next(replies))[1], lambda _: None,
                            sensor_probe=lambda: None)
        self.assertEqual(len(prompts), 9)

    def test_public_cli_has_no_msr_switch(self) -> None:
        self.assertEqual(PUBLIC_COMMANDS, (
            "install", "status", "start", "stop", "pause", "resume", "config", "logs",
            "benchmark", "update", "uninstall",
        ))
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            _ = main(("install", "--msr"))

    def test_installer_and_units_have_no_msr_privilege_path(self) -> None:
        paths = (ROOT / "install.sh", *(ROOT / "systemd").glob("*"))
        forbidden = ("CAP_SYS_RAWIO", "modprobe", "insmod", "setcap", "modules-load",
                     "sparehash-msr.service", "UNIT_MSR")
        matches = [f"{path.relative_to(ROOT)}:{term}" for path in paths
                   for term in forbidden if term.lower() in path.read_text(encoding="utf-8").lower()]
        self.assertEqual(matches, [])
        self.assertEqual(set(render(Capabilities(
            "x86_64", True, 255, True, "cgroup2", True, True, False,
            (0,), (), 8, 8 * 1024**3, None, None, True))), {UNIT_CONTROLLER, UNIT_MINER})


if __name__ == "__main__":
    _ = unittest.main()
