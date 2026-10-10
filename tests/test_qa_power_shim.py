"""Power injection acceptance regressions without mounts or system services."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Final

import pytest

QA: Final = Path(__file__).resolve().parents[1] / "qa"


def _write_receipts(etc: Path, host: str = "127.0.0.1") -> dict[str, bytes]:
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "config.toml").write_text(
        "donate_level = 1\n\n"
        "[pool]\n"
        f'host = "{host}"\n'
        "port = 3333\n"
        "tls = false\n\n"
        "[thermal]\n"
        "enabled = false\n\n"
        "[gaming]\n"
        "enabled = true\n"
        "users = []\n"
    )
    miner = json.dumps({
        "donate-level": 1,
        "pools": [{"url": f"{host}:3333", "tls": False}],
        "http": {"enabled": True, "host": "127.0.0.1", "port": 38087},
    })
    (etc / "xmrig.json").write_text(miner)
    (etc / "xmrig.runtime.json").write_text(miner)
    return {path.name: path.read_bytes() for path in etc.iterdir()}


@pytest.mark.parametrize("specification", [
    ("gdbus", ["call", "--system", "--method", "org.freedesktop.DBus.Properties.GetAll"],
     "({'OnBattery': <false>},)"),
    ("gdbus", ["call", "--method", "org.freedesktop.DBus.Properties.Get", "org.freedesktop.UPower", "OnBattery"],
     "(<false>,)"),
    ("busctl", ["--system", "--timeout=2", "get-property", "org.freedesktop.UPower",
                "/org/freedesktop/UPower", "org.freedesktop.UPower", "OnBattery"], "b false"),
])
def test_helper_reads_selected_fixture_when_called(
    tmp_path: Path, specification: tuple[str, list[str], str],
) -> None:
    # Given
    helper, arguments, expected = specification
    fixture = tmp_path / "upower.json"
    _ = fixture.write_text(json.dumps({"org.freedesktop.UPower": {"OnBattery": False}}))
    # When
    result = subprocess.run([sys.executable, str(QA / "shims" / helper), *arguments],
                            env={**os.environ, "SPAREHASH_UPOWER_FIXTURE": str(fixture)},
                            capture_output=True, text=True, check=True)
    # Then
    assert result.stdout.strip() == expected


@pytest.mark.parametrize("helper", ["gdbus", "busctl"])
def test_helper_fails_when_upower_unavailable(tmp_path: Path, helper: str) -> None:
    # Given
    fixture = tmp_path / "upower.json"
    _ = fixture.write_text('{"available": false}')
    arguments = (["call", "--method", "org.freedesktop.DBus.Properties.GetAll"] if helper == "gdbus" else
                 ["--system", "get-property", "org.freedesktop.UPower", "/org/freedesktop/UPower",
                  "org.freedesktop.UPower", "OnBattery"])
    # When
    result = subprocess.run([sys.executable, str(QA / "shims" / helper), *arguments],
                            env={**os.environ, "SPAREHASH_UPOWER_FIXTURE": str(fixture)},
                            capture_output=True, check=False)
    # Then
    assert result.returncode != 0


def test_monitor_signals_when_atomic_fixture_changes_with_closed_stdin(tmp_path: Path) -> None:
    # Given
    fixture = tmp_path / "upower.json"
    _ = fixture.write_text('{"org.freedesktop.UPower": {"OnBattery": false}}')
    with subprocess.Popen([sys.executable, str(QA / "shims/gdbus"), "monitor", "--system"],
                          env={**os.environ, "SPAREHASH_UPOWER_FIXTURE": str(fixture)},
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE) as process:
        assert process.stdout is not None
        try:
            with selectors.DefaultSelector() as selector:
                _ = selector.register(process.stdout, selectors.EVENT_READ)
                assert selector.select(timeout=2)
                _ = process.stdout.readline()
                replacement = tmp_path / "replacement"
                _ = replacement.write_text('{"org.freedesktop.UPower": {"OnBattery": true}}')
                # When
                replacement.replace(fixture)
                assert selector.select(timeout=2)
                signal = process.stdout.readline()
                # Then
                assert b"PropertiesChanged" in signal
                assert process.poll() is None
        finally:
            process.terminate()
            _ = process.wait(timeout=2)


def test_power_files_preserve_mounted_inode_when_state_changes(tmp_path: Path) -> None:
    # Given
    command = f"""
set -euo pipefail
source '{QA}/lib/runtime.sh'
QA_ROOT='{tmp_path}'
mkdir "$QA_ROOT/power"
qa_power_files 0 Full
before=$(stat -c %i "$QA_ROOT/power")
qa_power_files 1 Discharging
[[ $(stat -c %i "$QA_ROOT/power") == "$before" ]]
[[ $(cat "$QA_ROOT/power/BAT0/status") == Discharging ]]
qa_upower false false
"""
    # When
    result = subprocess.run(["bash", "-c", command], capture_output=True, check=False)
    # Then
    assert result.returncode == 0
    assert json.loads((tmp_path / "upower.json").read_text())["available"] is False


def test_dropin_keeps_private_tmp_when_setup_owns_runtime(tmp_path: Path) -> None:
    # Given
    root = tmp_path / "runtime"
    dropin = tmp_path / "unit.d/qa.conf"
    command = f"""
set -euo pipefail
source '{QA}/lib/runtime.sh'
SCRIPT_DIR='{QA}'
QA_ROOT='{root}'
QA_DROPIN='{dropin}'
QA_SHIMS_DIR='{tmp_path}/qa-shims'
qa_preflight() {{ :; }}
systemctl() {{ [[ $1 == daemon-reload ]]; }}
qa_setup
"""
    # When
    _ = subprocess.run(["bash", "-c", command], check=True, capture_output=True)
    # Then
    directives = dropin.read_text().splitlines()
    assert "PrivateTmp=yes" in directives
    assert f"BindReadOnlyPaths={root}/power:/sys/class/power_supply" in directives
    assert any(line.endswith(":/usr/bin/busctl") for line in directives)
    assert any(line.endswith(":/usr/bin/gdbus") for line in directives)


def test_stop_acceptance_rejects_when_deadline_already_elapsed() -> None:
    # Given / When
    result = subprocess.run(["bash", "-c", f"set -e; source '{QA}/lib/runtime.sh'; "
                             "export QA_STOP_DEADLINE=0; qa_wait_miner inactive 5"],
                            capture_output=True, text=True, check=False)
    # Then
    assert result.returncode != 0


def test_status_acceptance_rejects_when_battery_blocker_missing(tmp_path: Path) -> None:
    # Given
    document = {"power": {"inventory": {"value": True, "stale": False},
                          "upower": {"value": False}, "sysfs": {"value": True}},
                "blockers": [], "miner": "stopped"}
    fixture = tmp_path / "fixture.json"
    _ = fixture.write_text(json.dumps(document))
    command = f"set -euo pipefail; source '{QA}/lib/runtime.sh'; QA_ROOT='{tmp_path}'; " \
              f"qa_status() {{ cat '{fixture}'; }}; qa_assert_power true false true"
    # When
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True, check=False)
    # Then
    assert result.returncode != 0


def test_gamemoded_shim_reports_inactive_when_fixture_missing(tmp_path: Path) -> None:
    # Given
    fixture = tmp_path / "gamemoded.json"
    # When
    result = subprocess.run([sys.executable, str(QA / "shims" / "gamemoded"), "-s",
                             "--fixture", str(fixture)],
                            capture_output=True, text=True, check=False)
    # Then
    assert result.returncode == 0
    assert result.stdout.strip() == "gamemode is inactive"


@pytest.mark.parametrize("flag,expected", [
    ("--set-active", "gamemode is active"),
    ("--set-inactive", "gamemode is inactive"),
])
def test_gamemoded_shim_set_roundtrips_status(tmp_path: Path, flag: str, expected: str) -> None:
    # Given
    fixture = tmp_path / "gamemoded.json"
    # When
    _ = subprocess.run([sys.executable, str(QA / "shims" / "gamemoded"), flag,
                        "--fixture", str(fixture)], check=True, capture_output=True)
    status = subprocess.run([sys.executable, str(QA / "shims" / "gamemoded"), "-s",
                             "--fixture", str(fixture)], check=True, capture_output=True, text=True)
    # Then
    assert status.stdout.strip() == expected
    assert json.loads(fixture.read_text())["active"] is (flag == "--set-active")


def test_gamemoded_shim_env_override_wins_over_default(tmp_path: Path) -> None:
    # Given
    fixture = tmp_path / "custom.json"
    _ = fixture.write_text('{"active": true, "status": "gamemode is active"}\n')
    # When
    result = subprocess.run([sys.executable, str(QA / "shims" / "gamemoded"), "-s"],
                            env={**os.environ, "SPAREHASH_QA_GAMEMODE_FIXTURE": str(fixture)},
                            capture_output=True, text=True, check=True)
    # Then
    assert result.stdout.strip() == "gamemode is active"


def test_gamemoded_shim_rejects_mutual_exclusion_and_torn_fixture(tmp_path: Path) -> None:
    # Given
    fixture = tmp_path / "gamemoded.json"
    # When / Then: both flags at once is a usage error, not a silent winner
    both = subprocess.run([sys.executable, str(QA / "shims" / "gamemoded"),
                           "--set-active", "--set-inactive", "--fixture", str(fixture)],
                          capture_output=True, text=True, check=False)
    assert both.returncode == 2
    # Given: a torn (invalid JSON) fixture must never silently read as inactive
    _ = fixture.write_text("{not json")
    # When / Then
    torn = subprocess.run([sys.executable, str(QA / "shims" / "gamemoded"), "-s",
                           "--fixture", str(fixture)], capture_output=True, text=True, check=False)
    assert torn.returncode == 2


def test_gamemode_state_publishes_root_fixture_document(tmp_path: Path) -> None:
    # Given / When
    command = f"""
set -euo pipefail
source '{QA}/lib/runtime.sh'
QA_ROOT='{tmp_path}'
qa_gamemode_state active
qa_gamemode_state inactive
"""
    _ = subprocess.run(["bash", "-c", command], check=True, capture_output=True)
    # Then
    document = json.loads((tmp_path / "gamemoded.json").read_text())
    assert document == {"active": False, "status": "gamemode is inactive"}


def test_fixture_setup_derives_fixtures_and_preserves_receipts(tmp_path: Path) -> None:
    # Given
    etc = tmp_path / "etc"
    before = _write_receipts(etc)
    qa_root = tmp_path / "qa-root"
    # When
    result = subprocess.run(
        [sys.executable, str(QA / "fixture_setup.py"), "--qa-root", str(qa_root), "--etc", str(etc)],
        capture_output=True, text=True, check=False)
    # Then
    assert result.returncode == 0, result.stderr
    assert {path.name: path.read_bytes() for path in etc.iterdir()} == before
    config = tomllib.loads((qa_root / "config.test.toml").read_text())
    assert config["thermal"]["enabled"] is True
    assert config["gaming"]["enabled"] is True
    assert config["gaming"]["users"] == ["root"]
    assert config["donate_level"] == 1
    for name in ("xmrig.zero.json", "xmrig.zero.runtime.json"):
        assert json.loads((qa_root / name).read_text())["donate-level"] == 0


@pytest.mark.parametrize("mutation", [
    {"pool": {"host": "pool.example.com", "port": 3333, "tls": False}},
    {"donate_level": 0},
])
def test_fixture_setup_refuses_when_source_contract_violated(
    tmp_path: Path, mutation: dict,
) -> None:
    # Given
    etc = tmp_path / "etc"
    _ = _write_receipts(etc)
    config_path = etc / "config.toml"
    if "donate_level" in mutation:
        _ = config_path.write_text(config_path.read_text().replace("donate_level = 1", "donate_level = 0"))
    else:
        pool = mutation["pool"]
        miner_path = etc / "xmrig.json"
        _ = miner_path.write_text(json.dumps({
            "donate-level": 1,
            "pools": [{"url": f"{pool['host']}:{pool['port']}", "tls": pool["tls"]}],
            "http": {"enabled": True, "host": "127.0.0.1", "port": 38087},
        }))
    # When
    result = subprocess.run(
        [sys.executable, str(QA / "fixture_setup.py"), "--qa-root", str(tmp_path / "qa-root"),
         "--etc", str(etc)], capture_output=True, text=True, check=False)
    # Then
    assert result.returncode != 0
    assert "FAIL: fixture setup" in result.stderr


def test_fixture_overlays_write_exclusive_dropins_with_full_bind_set(tmp_path: Path) -> None:
    # Given
    etc = tmp_path / "etc"
    _ = _write_receipts(etc)
    root = tmp_path / "runtime"
    run_dir = root / "run-test"
    _ = run_dir.mkdir(parents=True)
    controller_dropin = tmp_path / "unit.d/qa-controller.conf"
    miner_dropin = tmp_path / "unit.d/qa-miner.conf"
    command = f"""
set -euo pipefail
source '{QA}/lib/runtime.sh'
SCRIPT_DIR='{QA}'
QA_ROOT='{root}'
QA_ETC_DIR='{etc}'
QA_DROPIN='{controller_dropin}'
QA_MINER_DROPIN='{miner_dropin}'
RUN_DIR='{run_dir}'
QA_OWNS_RUNTIME=1
systemctl() {{ [[ $1 == daemon-reload ]]; }}
qa_fixture_overlays
"""
    # When
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True, check=False)
    # Then
    assert result.returncode == 0, result.stderr
    directives = controller_dropin.read_text().splitlines()
    assert "PrivateTmp=yes" in directives
    assert f"BindReadOnlyPaths={root}/power:/sys/class/power_supply" in directives
    assert f"BindReadOnlyPaths={root}/hwmon:/sys/class/hwmon" in directives
    assert any(line.endswith(":/usr/bin/gamemoded") for line in directives)
    assert any(line.endswith(":/usr/bin/busctl") for line in directives)
    assert any(line.endswith(":/usr/bin/gdbus") for line in directives)
    assert f"BindReadOnlyPaths={root}/users:/run/user" in directives
    assert f"BindReadOnlyPaths={root}/config.test.toml:/etc/sparehash/config.toml" in directives
    assert f"BindReadOnlyPaths={root}/xmrig.zero.json:/etc/sparehash/xmrig.json" in directives
    assert f"BindReadOnlyPaths={root}/xmrig.zero.runtime.json:/etc/sparehash/xmrig.runtime.json" in directives
    miner = miner_dropin.read_text().splitlines()
    assert "LoadCredential=" in miner
    assert f"LoadCredential=xmrig.json:{root}/xmrig.zero.json" in miner
    assert f"BindReadOnlyPaths={root}/xmrig.zero.runtime.json:/etc/sparehash/xmrig.runtime.json" in miner
    assert (root / "config.test.toml").is_file()
    assert (root / "gamemoded.json").is_file()
    assert json.loads((root / "gamemoded.json").read_text())["active"] is False


def test_fixture_overlays_refuse_foreign_runtime_and_repeat(tmp_path: Path) -> None:
    # Given
    etc = tmp_path / "etc"
    _ = _write_receipts(etc)
    root = tmp_path / "runtime"
    run_dir = root / "run-test"
    _ = run_dir.mkdir(parents=True)
    base = f"""
set -euo pipefail
source '{QA}/lib/runtime.sh'
SCRIPT_DIR='{QA}'
QA_ROOT='{root}'
QA_ETC_DIR='{etc}'
QA_DROPIN='{tmp_path}/unit.d/qa-controller.conf'
QA_MINER_DROPIN='{tmp_path}/unit.d/qa-miner.conf'
RUN_DIR='{run_dir}'
systemctl() {{ [[ $1 == daemon-reload ]]; }}
"""
    # When / Then: without QA_OWNS_RUNTIME the overlay refuses
    refused = subprocess.run(["bash", "-c", base + "qa_fixture_overlays"],
                             capture_output=True, text=True, check=False)
    assert refused.returncode != 0
    assert "QA-owned runtime" in refused.stderr
    # When: with ownership the first call succeeds and the second is a no-op
    # (same shell: QA_OVERLAYS_DONE is exported for child suites, not persisted
    # across separate processes)
    both = subprocess.run(
        ["bash", "-c", base + "QA_OWNS_RUNTIME=1; qa_fixture_overlays; qa_fixture_overlays"],
        capture_output=True, text=True, check=False)
    # Then
    assert both.returncode == 0, both.stderr
    assert both.stdout.count("QA fixture overlays") == 1


def test_fixture_overlays_reject_foreign_miner_dropin(tmp_path: Path) -> None:
    # Given
    etc = tmp_path / "etc"
    _ = _write_receipts(etc)
    root = tmp_path / "runtime"
    run_dir = root / "run-test"
    _ = run_dir.mkdir(parents=True)
    miner_dropin = tmp_path / "unit.d/qa-miner.conf"
    _ = miner_dropin.parent.mkdir(parents=True, exist_ok=True)
    _ = miner_dropin.write_text("# foreign\n")
    command = f"""
set -euo pipefail
source '{QA}/lib/runtime.sh'
SCRIPT_DIR='{QA}'
QA_ROOT='{root}'
QA_ETC_DIR='{etc}'
QA_DROPIN='{tmp_path}/unit.d/qa-controller.conf'
QA_MINER_DROPIN='{miner_dropin}'
RUN_DIR='{run_dir}'
QA_OWNS_RUNTIME=1
systemctl() {{ [[ $1 == daemon-reload ]]; }}
qa_fixture_overlays
"""
    # When
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True, check=False)
    # Then
    assert result.returncode != 0
    assert miner_dropin.read_text() == "# foreign\n"
