# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors
"""Seven signed-update scenarios, only in the approved networkless disposable lab.

The bench supplies isolated test-key source/archive fixtures. Production has no
test trust or fault switch. The fixture-only updater stops itself after its first
publication write when the driver requests interruption; the lab then reboots.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
EVIDENCE = Path("/var/tmp/sparehash-v030-evidence")
ARTIFACTS = Path("/var/tmp/sparehash-v030-artifacts")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--phase", choices=("before-reboot", "after-reboot"), required=True)
    parser.add_argument("--source-sha256", required=True)
    args = parser.parse_args()
    if not args.yes or os.environ.get("SPAREHASH_QA_DISPOSABLE_VM") != "YES":
        parser.error("explicit --yes and SPAREHASH_QA_DISPOSABLE_VM=YES required")
    if os.geteuid() != 0 or Path("/proc/1/comm").read_text().strip() != "systemd":
        parser.error("root in a disposable systemd VM required")
    if any(path.name != "lo" for path in Path("/sys/class/net").iterdir()):
        parser.error("guest networking must remain disabled")
    if shutil.which("getenforce") and subprocess.check_output(["getenforce"], text=True).strip() != "Enforcing":
        parser.error("SELinux must remain Enforcing")
    os.environ.clear()
    os.environ.update({"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C",
                       "PYTHONDONTWRITEBYTECODE": "1"})
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(SOURCE))
    from qa.release_smoke import Smoke, source_sha256
    from sparehash import ipc, upgrade
    from sparehash.deployment import Tree

    if source_sha256(SOURCE) != args.source_sha256:
        parser.error("independent source digest mismatch")
    if args.phase == "before-reboot":
        for path in ("/opt/sparehash", "/etc/sparehash", "/var/lib/sparehash", "/usr/local/bin/sparehash",
                     "/usr/local/bin/xmrig", "/etc/systemd/system/sparehash.service"):
            if os.path.lexists(path):
                parser.error("fresh VM required: " + path)
        smoke = Smoke(EVIDENCE, args.source_sha256)
        smoke.prepare()
        smoke.run(["systemctl", "disable", "sparehash.service"])
        smoke.call({"cmd": "pause"})
        smoke.wait(smoke.stopped)
        saved = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in upgrade.PRESERVED}
        miner = hashlib.sha256(Path("/usr/local/bin/xmrig").read_bytes()).hexdigest()
        smoke.result["preserved"] = saved
        smoke.result["miner_sha256"] = miner
        smoke.save()
    else:
        if not (EVIDENCE / "summary.json").is_file():
            parser.error("missing pre-reboot evidence")
        # Reuse the existing fixture's evidence/command capture without creating
        # a second evidence tree or repeating the fresh installation.
        smoke = object.__new__(Smoke)
        smoke.evidence = EVIDENCE
        smoke.result = json.loads((EVIDENCE / "summary.json").read_text())
        smoke.sequence = 50
        smoke.children = []
        smoke.services_owned = True
        smoke.drop = Path("/run/systemd/system/sparehash.service.d/s4-smoke.conf")
        if smoke.result["source_sha256"] != args.source_sha256:
            parser.error("evidence identity mismatch")

    def preserved() -> None:
        for name, expected in smoke.result["preserved"].items():
            assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == expected, name
        assert hashlib.sha256(Path("/usr/local/bin/xmrig").read_bytes()).hexdigest() == smoke.result["miner_sha256"]
        assert smoke.run(["systemctl", "is-enabled", "sparehash.service"], expected=1).strip() == "disabled"

    def installed(version: str, active: bool) -> None:
        tree = Tree()
        upgrade.validate_live(tree, upgrade._manifest(tree))
        assert upgrade.identity(tree.path(upgrade.SOURCE))[0] == version
        assert smoke.prop("sparehash.service", "ActiveState") == ("active" if active else "inactive")
        assert smoke.empty()
        if active:
            row = ipc.call({"cmd": "status"}, timeout=1)
            assert row.get("ready") and not row.get("running") and "manual" in row["blockers"]
        preserved()

    def update(version: str, expected: int = 0) -> None:
        smoke.run(["/usr/local/bin/sparehash", "update", "--from", str(ARTIFACTS / f"sparehash-{version}-source.tar.gz")],
                  expected=expected)

    try:
        if args.phase == "before-reboot":
            update("0.3.1")
            installed("0.3.1", True)
            assert "v030 fixture 0.3.1" in Path("/etc/systemd/system/sparehash.service").read_text()
            assert Path("/opt/sparehash/src/sparehash/v030_added.py").is_file()
            assert not Path("/opt/sparehash/src/sparehash/v030_obsolete.py").exists()
            smoke.check("1_signed_running_source_units_receipts")
            smoke.check("3_config_credentials_manual_active_boot_disabled_xmrig_preserved")
            smoke.run(["systemctl", "stop", "sparehash.service"])
            update("0.3.2")
            installed("0.3.2", False)
            smoke.check("2_signed_stopped_remains_stopped")
            original = ARTIFACTS / "sparehash-0.3.3-source.tar.gz"
            tampered = ARTIFACTS / "tampered.tar.gz"
            tampered.write_bytes(original.read_bytes() + b"tampered")
            Path(str(tampered) + ".asc").write_bytes(Path(str(original) + ".asc").read_bytes())
            manifest = Path("/var/lib/sparehash/install-manifest.json").read_bytes()
            smoke.run(["/usr/local/bin/sparehash", "update", "--from", str(tampered)], expected=1)
            assert Path("/var/lib/sparehash/install-manifest.json").read_bytes() == manifest
            assert tampered.read_bytes().endswith(b"tampered")
            installed("0.3.2", False)
            smoke.check("4_invalid_signature_before_live_or_service_changes")
            smoke.run(["systemctl", "start", "sparehash.service"])
            smoke.wait(lambda row: row.get("ready") and smoke.stopped(row))
            Path("/var/tmp/v030-interrupt-request").write_text("fixture-only fault\n")
            with (EVIDENCE / "interrupted-update.log").open("w") as output:
                process = subprocess.Popen(["/usr/local/bin/sparehash", "update", "--from", str(original)],
                                           stdout=output, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + 120
                marker = Path("/var/tmp/v030-worker-pid")
                while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.1)
                assert marker.is_file(), "fixture did not reach first publication write"
                os.kill(int(marker.read_text()), signal.SIGKILL)
                assert process.wait(timeout=30) != 0
            Path("/var/tmp/v030-interrupt-request").unlink()
            record = upgrade.load(Tree())
            assert record is not None and record.phase == "installing"
            assert smoke.empty()
            smoke.check("5a_interrupted_publication_pending_reboot", record.operation_id)
            smoke.save()
            print("READY FOR LAB POWER-CYCLE", flush=True)
            return 0
        assert smoke.empty()
        Path("/usr/local/bin/sparehash").unlink()
        Path("/opt/sparehash/src/sparehash/__main__.py").unlink()
        smoke.run(["/usr/bin/python3", "-I", "-B", "/opt/sparehash/.update-backup/src/sparehash/__main__.py",
                   "update", "--recover"])
        installed("0.3.2", True)
        smoke.run(["/usr/local/bin/sparehash", "update", "--recover"])
        installed("0.3.2", True)
        smoke.check("5b_reboot_broken_cli_backup_recovery_repeat_no_rollback")
        update("0.3.4", expected=1)
        installed("0.3.2", True)
        assert not Path("/var/lib/sparehash/update.json").exists()
        smoke.check("6_signed_activation_failure_automatic_restore_prior_activity")
        smoke.run(["/usr/local/bin/sparehash", "uninstall"])
        assert not Path("/usr/local/bin/sparehash").exists()
        assert not Path("/opt/sparehash/.update-backup").exists()
        assert not list(Path("/opt").glob("sparehash/.update-stage-*"))
        assert smoke.empty()
        for name, expected in smoke.result["preserved"].items():
            assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == expected
        smoke.sentinels()
        smoke.check("7_receipt_aware_uninstall_owned_update_leftovers")
        smoke.result["complete"] = True
    except Exception as error:
        smoke.result["failure"] = type(error).__name__ + ": " + str(error)
        smoke.save()
        raise
    finally:
        # Keep services running under their durable holds between the two phases.
        # The controlling lab ALWAYS discards the guest, including failures.
        if args.phase == "after-reboot":
            smoke.close()
        smoke.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
