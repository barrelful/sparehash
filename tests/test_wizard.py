"""Scripted setup and file-boundary behavior, without stdin or host mutation."""

import json
import pwd
import tempfile
import unittest
import warnings
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from sparehash.config import Config, Cpu, Gaming, Hugepages, Pool, Thermal, validate
from sparehash.sysdetect import Capabilities
from sparehash.thermal import SensorPath
from sparehash.wizard import WizardError, _Prompts, load_answers, run_interactive

WALLET = "4" + "A" * 94
ACCOUNT = pwd.getpwuid(0).pw_name
SENSOR = SensorPath(Path("/fixture/temp1_input"), "coretemp", "Package id 0")
CAPS = Capabilities("x86_64", True, 255, True, "cgroup2", True, True, False,
                    (0,), (), 8, 8 * 1024**3, None, None, True)


def _serialize(cfg: Config) -> str:
    raw = cfg.to_dict()
    lines = [f"version = {cfg.version}", f"wallet = {json.dumps(cfg.wallet)}",
             f"donate_level = {cfg.donate_level}"]
    sections = (("pool", raw.get("pool", {})), ("cpu", raw.get("cpu", {})), ("thermal", raw.get("thermal", {})),
                ("gaming", raw.get("gaming", {})), ("hugepages", raw.get("hugepages", {})), ("api", raw.get("api", {})),
                ("controller", raw.get("controller", {})), ("xmrig", raw.get("xmrig", {})))
    for key, table in sections:
        lines.append(f"[{key}]")
        lines.extend(f"{field} = {json.dumps(item)}" for field, item in table.items())
    return "\n".join(lines) + "\n"


class WizardTests(unittest.TestCase):
    def test_boolean_when_enter_pressed_uses_displayed_default(self) -> None:
        for default, suffix in ((True, "[Y/n]: "), (False, "[y/N]: ")):
            # Given
            input_fn = Mock(return_value="")
            prompts = _Prompts(input_fn, lambda _: None)
            # When
            result = prompts.boolean("Choice", default)
            # Then
            self.assertEqual(result, default)
            self.assertTrue(input_fn.call_args.args[0].endswith(suffix))

    def scripted(self, replies: list[str], *, caps: Capabilities = CAPS,
                 sensor: SensorPath | None = SENSOR, username: str | None = None) -> Config:
        pending = iter(replies)
        with patch("builtins.input", side_effect=AssertionError("uncontrolled stdin")):
            result = run_interactive(caps, lambda _: next(pending), lambda _: None,
                                     username, sensor_probe=lambda: sensor)
        self.assertEqual(list(pending), [])
        return result

    def answers(self, text: str, caps: Capabilities = CAPS,
                sensor: SensorPath | None = SENSOR) -> Config:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "answers.toml"
            _ = path.write_text(text, encoding="utf-8")
            with patch("builtins.input", side_effect=AssertionError("uncontrolled stdin")):
                return load_answers(path, caps, sensor_probe=lambda: sensor)

    def baseline(self) -> Config:
        return Config(wallet=WALLET, pool=Pool("pool.example", 4444, True, "rig-test"),
                      gaming=Gaming(users=(ACCOUNT,)))

    def test_full_config_when_every_option_is_seeded(self) -> None:
        # Given
        replies = [WALLET, "custom.example", "5555", "yes", "other-rig", "7",
                   "custom", '{"rx":[0,2]}', "y", "y", ACCOUNT, "y", "640"]
        expected = Config(wallet=WALLET, donate_level=7, pool=Pool("custom.example", 5555, True, "other-rig"),
                          cpu=Cpu("custom", 75, '{"rx":[0,2]}'), thermal=Thermal(True),
                          gaming=Gaming(users=(ACCOUNT,)), hugepages=Hugepages(True, 640))
        # When
        result = self.scripted(replies)
        # Then
        self.assertEqual(result, expected)
        self.assertEqual(validate(result), [])
        self.assertNotIn("msr", result.to_dict())

    def test_wallet_reprompts_when_format_is_invalid(self) -> None:
        # Given
        replies = ["bad", "9" + "A" * 94, WALLET, "pool.example", "", "", "rig", "", "", "n", "n", "n"]
        # When
        result = self.scripted(replies)
        # Then
        self.assertEqual(result.wallet, WALLET)

    def test_retries_when_field_input_is_invalid(self) -> None:
        # Given
        replies = [WALLET, "https://bad", "pool.example", "abc", "0", "65536", "4444",
                   "perhaps", "y", "\x01", "rig", "0", "100", "8", "unknown", "spare-capped",
                   "0", "101", "50", "n", "y", "missing/account", ACCOUNT, "y", "0", "64"]
        # When
        result = self.scripted(replies)
        # Then
        self.assertEqual((result.pool.port, result.cpu.max_threads_percent,
                          result.donate_level, result.hugepages.pages), (4444, 50, 8, 64))

    def test_json_retries_when_not_an_object(self) -> None:
        # Given
        replies = [WALLET, "pool.example", "", "", "rig", "", "custom", "{", "[]",
                   '{"rx":[1]}', "n", "n", "n"]
        # When
        result = self.scripted(replies)
        # Then
        self.assertEqual(json.loads(result.cpu.custom_json), {"rx": [1]})

    def test_default_account_when_sudo_user_is_verified(self) -> None:
        # Given
        replies = [WALLET, "pool.example", "", "", "", "", "", "", "", "", ""]
        # When
        result = self.scripted(replies, username=ACCOUNT)
        # Then
        self.assertEqual(result.gaming, Gaming(users=(ACCOUNT,)))
        self.assertEqual(result.hugepages, Hugepages())

    def test_no_default_account_when_candidate_is_invalid(self) -> None:
        for candidate in ("missing/account", "sparehash_nonexistent_93eac"):
            with self.subTest(candidate=candidate):
                # Given
                replies = [WALLET, "pool.example", "", "", "rig", "", "", "n", "y", "", ACCOUNT, "n"]
                # When
                result = self.scripted(replies, username=candidate)
                # Then
                self.assertEqual(result.gaming.users, (ACCOUNT,))

    def test_optional_prompts_absent_when_capabilities_are_unavailable(self) -> None:
        for nodes in (None, (), (0, 1)):
            with self.subTest(nodes=nodes):
                # Given
                replies = [WALLET, "pool.example", "", "", "rig", "", "", "n"]
                # When
                result = self.scripted(replies, caps=replace(CAPS, numa_nodes=nodes), sensor=None)
                # Then
                self.assertEqual((result.thermal.enabled, result.hugepages.enabled), (False, False))

    def test_default_page_count_when_one_memory_node_is_detected(self) -> None:
        # Given
        replies = [WALLET, "pool.example", "", "", "rig", "", "", "n", "n", "y", ""]
        # When
        result = self.scripted(replies, caps=replace(CAPS, numa_nodes=(3,)))
        # Then
        self.assertEqual(result.hugepages, Hugepages(True, 1280))

    def test_roundtrip_when_answers_use_complete_schema(self) -> None:
        # Given
        config = replace(self.baseline(), donate_level=9, cpu=Cpu("custom", 33, '{"rx":[1]}'),
                         thermal=Thermal(True), hugepages=Hugepages(True, 512))
        # When
        result = self.answers(_serialize(config))
        # Then
        self.assertEqual(result, config)

    def test_error_when_explicit_hugepages_have_unsupported_topology(self) -> None:
        for nodes in (None, (), (0, 1)):
            with self.subTest(nodes=nodes):
                # Given
                text = _serialize(replace(self.baseline(), hugepages=Hugepages(True)))
                # When / Then
                with self.assertRaises(WizardError) as caught:
                    _ = self.answers(text, replace(CAPS, numa_nodes=nodes))
                self.assertEqual(caught.exception.field, "hugepages.enabled")

    def test_error_when_explicit_thermal_has_no_credible_sensor(self) -> None:
        # Given
        text = _serialize(replace(self.baseline(), thermal=Thermal(True)))
        # When / Then
        with self.assertRaises(WizardError) as caught:
            _ = self.answers(text, sensor=None)
        self.assertEqual(caught.exception.field, "thermal.enabled")

    def test_errors_when_schema_types_ranges_or_fields_are_invalid(self) -> None:
        # Given: independent file mutations covering all scalar/table/list boundaries.
        text = _serialize(self.baseline())
        mutations = [("version = 1", "version = true"), ("version = 1", "version = 2"),
                     (f'wallet = "{WALLET}"', 'wallet = 12'), (f'wallet = "{WALLET}"', 'wallet = "bad"'),
                     (f'wallet = "{WALLET}"', ""), ('host = "pool.example"', 'host = ""'),
                     ('host = "pool.example"', 'host = "bad/path"'), ('host = "pool.example"', 'host = false'),
                     ('worker = "rig-test"', 'worker = ""'), ('worker = "rig-test"', 'worker = 1'),
                     ("port = 4444", "port = true"), ("port = 4444", "port = 0"),
                     ("port = 4444", "port = 65536"), ("tls = true", 'tls = "true"'),
                     ("donate_level = 1", "donate_level = false"), ("donate_level = 1", "donate_level = 0"),
                     ("donate_level = 1", "donate_level = 100"), ('mode = "spare"', 'mode = 2'),
                     ('mode = "spare"', 'mode = "other"'), ('mode = "spare"', 'mode = "custom"'),
                     ("max_threads_percent = 75", "max_threads_percent = false"),
                     ("max_threads_percent = 75", "max_threads_percent = 0"),
                     ("max_threads_percent = 75", "max_threads_percent = 101"),
                     ('custom_json = ""', 'custom_json = 1'), ('custom_json = ""', 'custom_json = "[]"'),
                     ("pause_c = 85", "pause_c = true"), ("resume_c = 75", "resume_c = 85"),
                     ("resume_c = 75", "resume_c = 1.5"), ("enabled = false", 'enabled = "false"'),
                     ("[gaming]\nenabled = true", '[gaming]\nenabled = 1'),
                     (f'users = ["{ACCOUNT}"]', "users = [1]"), (f'users = ["{ACCOUNT}"]', 'users = "root"'),
                     (f'users = ["{ACCOUNT}"]', "users = []"),
                     (f'users = ["{ACCOUNT}"]', 'users = ["missing/account"]'),
                     ("pages = 1280", "pages = true"), ("pages = 1280", "pages = 0"),
                     ("pages = 1280", "pages = 1280\npages_1gb = true"),
                     ("port = 38087", "port = false"), ("port = 38087", "port = 0"),
                     ("port = 38087", "port = 65536"),
                     ("min_dwell_seconds = 60", "min_dwell_seconds = -1"),
                     ("min_dwell_seconds = 60", "min_dwell_seconds = true"),
                     ("reconcile_seconds = 30", "reconcile_seconds = 0"),
                     ("reconcile_seconds = 30", "reconcile_seconds = false"),
                     ('binary = "/opt/xmrig/current/xmrig"', "binary = 1"),
                     ("version = 1", "version = 1\nunknown = true"), ("[pool]", "pool = 3\n[other]")]
        for old, new in mutations:
            with self.subTest(old=old, new=new):
                self.assertIn(old, text)
                # When / Then
                with self.assertRaises(WizardError):
                    _ = self.answers(text.replace(old, new))

    def test_error_when_any_section_is_not_a_table(self) -> None:
        # Given
        for section in ("pool", "cpu", "thermal", "gaming", "hugepages", "api", "controller", "xmrig", "msr"):
            # When / Then
            with self.subTest(section=section), self.assertRaises(WizardError):
                _ = self.answers(f'{section} = 3\nwallet = "{WALLET}"\n')

    def test_error_when_answers_are_malformed_or_missing(self) -> None:
        for text in ("[pool", ""):
            # Given / When / Then
            with self.subTest(text=text), self.assertRaises(WizardError):
                _ = self.answers(text)
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(WizardError):
            _ = load_answers(Path(directory) / "absent", CAPS, sensor_probe=lambda: None)

    def test_error_when_answers_are_not_utf8(self) -> None:
        # Given
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "answers.toml"
            _ = path.write_bytes(b"wallet = '\xff'")
            # When / Then
            with self.assertRaises(WizardError) as caught:
                _ = load_answers(path, CAPS, sensor_probe=lambda: None)
            self.assertEqual(caught.exception.field, "answers")

    def test_msr_legacy_when_enabled_or_malformed_is_rejected(self) -> None:
        for value in ("true", "0", '"false"'):
            # Given / When / Then
            with self.subTest(value=value), self.assertRaises(WizardError) as caught:
                _ = self.answers(_serialize(self.baseline()) + f"\n[msr]\nenabled = {value}\n")
            self.assertEqual(caught.exception.field, "msr")

    def test_msr_legacy_when_disabled_is_ignored_without_output_setting(self) -> None:
        # Given
        text = _serialize(self.baseline()) + "\n[msr]\nenabled = false\n"
        # When
        with warnings.catch_warnings(record=True):
            result = self.answers(text)
        # Then
        self.assertEqual(result, self.baseline())
        self.assertNotIn("msr", result.to_dict())
