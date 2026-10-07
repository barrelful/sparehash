"""Configuration behavior tests."""

import tempfile
import unittest
import warnings
from pathlib import Path

from sparehash.config import Config, ConfigError, Cpu, Thermal, load, save, validate


class ConfigTests(unittest.TestCase):
    def test_empty_file_uses_valid_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            _ = path.write_text("", encoding="utf-8")
            self.assertEqual(validate(load(path)), [])

    def test_round_trip_and_secret_free_fixed_schema(self) -> None:
        config = Config(wallet='wallet "quoted"\\line', cpu=Cpu(mode="custom", custom_json='{"threads":4}'))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            save(config, path, warn_overwrite=False)
            self.assertEqual(load(path), config)
            self.assertNotIn("token", path.read_text(encoding="utf-8").lower())
            self.assertNotIn("[msr]", path.read_text(encoding="utf-8"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            self.assertEqual(Config.from_dict(config.to_dict()), config)

    def test_custom_mode_rejects_invalid_and_non_object_json(self) -> None:
        for custom_json in ("{", "[]", "null", '"quoted"'):
            with self.subTest(custom_json=custom_json), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.toml"
                _ = path.write_text(f'[cpu]\nmode = "custom"\ncustom_json = {custom_json!r}\n', encoding="utf-8")
                with self.assertRaises(ConfigError):
                    _ = load(path)

    def test_custom_mode_accepts_raw_object_fragment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            _ = path.write_text('[cpu]\nmode = "custom"\ncustom_json = \'{"threads":4}\'\n', encoding="utf-8")
            self.assertEqual(load(path).cpu.custom_json, '{"threads":4}')

    def test_custom_mode_with_empty_fragment_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            _ = path.write_text('[cpu]\nmode = "custom"\n', encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "required"):
                _ = load(path)

    def test_custom_mode_accepts_valid_object_fragment(self) -> None:
        config = Config.from_dict({"cpu": {"mode": "custom", "custom_json": '{"threads":4}'}})
        self.assertEqual(config.cpu.custom_json, '{"threads":4}')
        self.assertEqual(validate(config), [])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            save(config, path, warn_overwrite=False)
            self.assertEqual(load(path), config)

    def test_validate_rejects_custom_empty_malformed_and_non_object_fragments(self) -> None:
        for custom_json in ("", "{", "[]"):
            with self.subTest(custom_json=custom_json):
                errors = validate(Config(cpu=Cpu(mode="custom", custom_json=custom_json)))
                self.assertTrue(any("cpu.custom_json" in error for error in errors))

    def test_save_rejects_invalid_custom_json_before_creating_artifacts(self) -> None:
        for custom_json in ("", "{", "[]"):
            with self.subTest(custom_json=custom_json), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.toml"
                with self.assertRaises(ConfigError):
                    save(Config(cpu=Cpu(mode="custom", custom_json=custom_json)), path, warn_overwrite=False)
                self.assertFalse(path.exists())
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_empty_custom_json_remains_valid_outside_custom_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            _ = path.write_text('[cpu]\nmode = "spare"\n', encoding="utf-8")
            self.assertEqual(load(path).cpu.custom_json, "")

    def test_scalar_types_reject_bool_as_integer_and_integer_as_string(self) -> None:
        for key, value in (("version", "true"), ("wallet", "123"), ("donate_level", "true")):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.toml"
                _ = path.write_text(f"{key} = {value}\n", encoding="utf-8")
                with self.assertRaises(ConfigError):
                    _ = load(path)

    def test_scalar_types_reject_toml_boolean_and_integer_mismatches(self) -> None:
        for key, value in (("version", "version = true"), ("wallet", "wallet = 123"), ("donate_level", "donate_level = true")):
            with self.subTest(key=key):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "config.toml"
                    _ = path.write_text(value + "\n", encoding="utf-8")
                    with self.assertRaises(ConfigError):
                        _ = load(path)
        with self.assertRaises(ConfigError):
            _ = Config.from_dict({"version": True})

    def test_mixed_gaming_users_rejects_before_config_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            _ = path.write_text('[gaming]\nusers = [1, "alice"]\n', encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "gaming.users"):
                _ = load(path)

    def test_toml_string_control_characters_round_trip(self) -> None:
        config = Config(wallet="wallet\x01\x08\x0b\x1f\x7f", cpu=Cpu(custom_json=""))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            save(config, path, warn_overwrite=False)
            self.assertEqual(load(path).wallet, config.wallet)

    def test_validation_reports_hysteresis_and_boundaries(self) -> None:
        errors = validate(Config(thermal=Thermal(pause_c=80, resume_c=85), donate_level=100))
        self.assertTrue(any("hysteresis" in error for error in errors))
        self.assertTrue(any("donate_level" in error for error in errors))

    def test_malformed_toml_has_line_information(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            _ = path.write_text("[pool\nhost =", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "line"):
                _ = load(path)

    def test_enabled_legacy_msr_is_rejected_and_disabled_warns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            _ = path.write_text("[msr]\nenabled = true\n", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "msr"):
                _ = load(path)
            _ = path.write_text("[msr]\nenabled = false\n", encoding="utf-8")
            with warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always")
                self.assertEqual(validate(load(path)), [])
            self.assertTrue(any("msr" in str(item.message) for item in captured))

    def test_unknown_key_warns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            _ = path.write_text("surprise = true\n", encoding="utf-8")
            with self.assertWarnsRegex(UserWarning, "surprise"):
                _ = load(path)


if __name__ == "__main__":
    _ = unittest.main()
