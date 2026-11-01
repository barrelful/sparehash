"""Read-only configuration and retained wizard seeding regressions."""

import json
from dataclasses import replace
from pathlib import Path

from sparehash import cli_config
from sparehash.config import Config, Cpu, Gaming, Hugepages, Pool, Thermal, save
from sparehash.wizard import run_interactive
from tests.test_wizard import ACCOUNT, CAPS, SENSOR, WALLET


def test_show_serializes_only_public_config(tmp_path: Path, capsys) -> None:
    cfg = Config(wallet=WALLET)
    public = tmp_path / "config.toml"
    save(cfg, public)
    secret = "a" * 64
    (tmp_path / "xmrig.json").write_text(secret)
    assert cli_config.show(public) == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == cfg.to_dict()
    assert secret not in output.out + output.err


def test_show_refuses_missing_or_invalid_config(tmp_path: Path, capsys) -> None:
    public = tmp_path / "config.toml"
    assert cli_config.show(public) == 1
    public.write_text("[invalid TOML")
    assert cli_config.show(public) == 1
    assert "invalid or unavailable public config" in capsys.readouterr().err


def test_seeded_wizard_when_answers_are_empty() -> None:
    current = replace(Config(wallet=WALLET, pool=Pool("pool.example", 4444, True, "before")), donate_level=7,
                      cpu=Cpu("custom", 31, '{"rx":[1]}'),
                      thermal=Thermal(True, 88, 72), gaming=Gaming(True, (ACCOUNT,)),
                      hugepages=Hugepages(True, 700))
    updated = run_interactive(CAPS, lambda _: "", lambda _: None,
                              current=current, sensor_probe=lambda: SENSOR)
    assert updated == current
