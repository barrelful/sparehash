# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors
"""Destructive disposable-VM smoke, adapted from the passing S3 fixture driver.

No real mining, public pool, lab controller, GameMode protocol or installed QA
dependency. The fixture is our own non-hashing stand-in, not upstream XMRig.
The exercised Steam detector is Lutris-derived: see THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = Path("/var/tmp/sparehash-v020-evidence")


def source_sha256(source: Path = SOURCE) -> str:
    """Hash the exact source/units/fixture inventory, not an unchecked Git label."""
    paths = [*sorted((source / "sparehash").glob("*.py")),
             *sorted((source / "systemd").glob("*.in"))]
    paths += [source / name for name in (
        "install.sh", "LICENSE", "THIRD_PARTY_NOTICES.md", "pyproject.toml",
        "qa/qa_release_smoke.sh", "qa/release_smoke.py", "qa/fixtures/synthetic_xmrig.py",
    )]
    if not (source / "sparehash/__main__.py").is_file():
        raise ValueError("missing source package")
    manifest = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Smoke:
    def __init__(self, evidence: Path, digest: str) -> None:
        evidence.mkdir(mode=0o700)  # Refuse existing evidence, never overwrite it.
        self.evidence = evidence
        self.result: dict = {"source_sha256": digest, "fixture": "SpareHash-owned synthetic non-hashing miner",
                             "detector_attribution": "Lutris; THIRD_PARTY_NOTICES.md",
                             "checks": {}, "complete": False}
        self.children: list[subprocess.Popen] = []
        self.sequence = 0
        self.services_owned = False
        self.drop = Path("/run/systemd/system/sparehash.service.d/s4-smoke.conf")

    def save(self) -> None:
        (self.evidence / "summary.json").write_text(json.dumps(self.result, indent=2) + "\n")

    def check(self, name: str, detail: object = True) -> None:
        self.result["checks"][name] = detail
        self.save()
        print("PASS", name, json.dumps(detail), flush=True)

    def run(self, argv: list[str], *, expected: int = 0, replies: str | None = None) -> str:
        self.sequence += 1
        result = subprocess.run(argv, input=replies, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=180,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        (self.evidence / f"command-{self.sequence:02d}.log").write_text(json.dumps(argv) + "\n" + result.stdout)
        if result.returncode != expected:
            raise RuntimeError(f"{argv[0]} exited {result.returncode}; see command-{self.sequence:02d}.log")
        return result.stdout

    @staticmethod
    def call(command: dict) -> dict:
        from sparehash.ipc import call

        result = call(command, timeout=1)
        if result.get("ok") is not True:
            raise RuntimeError("controller rejected fixture request")
        return result

    @staticmethod
    def empty() -> bool:
        from sparehash.runtime_jobs import empty

        return empty()

    def wait(self, predicate, seconds: float = 30) -> dict:
        from sparehash.ipc import IPCError

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                row = self.call({"cmd": "status"})
                if predicate(row):
                    return row
            except (IPCError, OSError):
                pass
            time.sleep(0.25)  # Headroom for authorization under the existing IPC limit.
        raise RuntimeError("controller state deadline exceeded")

    def stopped(self, row: dict) -> bool:
        return not row.get("running") and self.empty()

    @staticmethod
    def prop(unit: str, name: str) -> str:
        return subprocess.check_output(["systemctl", "show", unit, f"--property={name}", "--value"], text=True).strip()

    def verify_payload(self) -> None:
        for directory, pattern in (("sparehash", "*.py"), ("systemd", "*.in")):
            expected = {p.name: p.read_bytes() for p in (SOURCE / directory).glob(pattern)}
            actual = {p.name: p.read_bytes() for p in (Path("/opt/sparehash/src") / directory).glob(pattern)}
            assert expected and actual == expected, "installed source differs"
        assert not Path("/opt/sparehash-probe").exists()
        assert not list(Path("/opt").glob(".sparehash-probe-*"))
        assert not list(Path("/opt/sparehash").glob(".staging-*"))

    def sentinels(self) -> None:
        for name, digest in self.result["sentinels"].items():
            assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest, name

    def prepare(self) -> None:
        from sparehash import config

        binary = Path("/usr/local/bin/xmrig")
        with binary.open("xb") as stream:
            stream.write((SOURCE / "qa/fixtures/synthetic_xmrig.py").read_bytes())
        binary.chmod(0o755)
        Path("/etc/sparehash").mkdir(mode=0o755)
        sentinels = (Path("/opt/smoke-foreign-sentinel"), Path("/etc/sparehash/smoke-foreign-sentinel"))
        for path in sentinels:
            with path.open("x") as stream:
                stream.write("foreign sentinel; not a product-owned artifact\n")
        self.result["sentinels"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (*sentinels, binary)}
        cfg = config.Config(wallet="4" + "1" * 94, pool=config.Pool(host="127.0.0.1"),
                            controller=config.Controller(min_dwell_seconds=3))
        answers = self.evidence / "answers.toml"
        config.save(cfg, answers)
        self.services_owned = True
        self.run(["/bin/sh", str(SOURCE / "install.sh"), "--answers", str(answers), "--yes", "--use-existing"])
        row = self.wait(lambda r: r.get("running"))
        assert row["gaming_detection"]["state"] == "not_detected"
        self.verify_payload()
        self.check("install_payload_receipts_and_default_best_effort_scan")
        pid = int(self.prop("sparehash-xmrig.service", "MainPID"))
        assert Path(f"/proc/{pid}").stat().st_uid == pwd.getpwnam("xmrig").pw_uid > 0
        assert os.sched_getscheduler(pid) == os.SCHED_IDLE and os.getpriority(os.PRIO_PROCESS, pid) == 19
        self.check("unprivileged_idle_priority")

    def operate(self) -> None:
        from sparehash import config

        self.run(["systemctl", "stop", "sparehash.service"])
        assert self.empty()
        battery = self.evidence / "power/BAT0"
        ac = self.evidence / "power/AC"
        sensor = self.evidence / "hwmon/hwmon0"
        for directory in (battery, ac, sensor):
            directory.mkdir(parents=True)
        self.evidence.chmod(0o755)
        for name, text in (("type", "Battery"), ("present", "1"), ("status", "Full")):
            (battery / name).write_text(text + "\n")
        (ac / "type").write_text("Mains\n")
        (ac / "online").write_text("1\n")
        (sensor / "name").write_text("coretemp\n")
        (sensor / "temp1_label").write_text("Package id 0\n")
        (sensor / "temp1_input").write_text("45000\n")
        overlay = self.evidence / "config.toml"
        config.save(replace(config.load(), thermal=config.Thermal(enabled=True)), overlay)
        self.drop.parent.mkdir()
        self.drop.write_text("[Service]\n" + "".join(f"BindReadOnlyPaths={src}:{dst}\n" for src, dst in (
            (battery.parent, "/sys/class/power_supply"), (sensor.parent, "/sys/class/hwmon"),
            (overlay, "/etc/sparehash/config.toml"))))
        self.run(["systemctl", "daemon-reload"])
        self.run(["systemctl", "start", "sparehash.service"])
        self.wait(lambda r: r.get("running"))
        (battery / "status").write_text("Discharging\n")
        (ac / "online").write_text("0\n")
        self.wait(lambda r: "battery" in r["blockers"] and self.stopped(r), 12)
        self.call({"cmd": "pause"})
        (battery / "status").write_text("Full\n")
        (ac / "online").write_text("1\n")
        self.wait(lambda r: "battery" not in r["blockers"] and "manual" in r["blockers"] and self.stopped(r), 12)
        self.check("battery_callback_and_independent_manual_hold")
        self.call({"cmd": "resume"})
        self.wait(lambda r: r.get("running"))
        (sensor / "temp1_input").write_text("95000\n")
        self.wait(lambda r: "thermal" in r["blockers"] and self.stopped(r), 6)
        (sensor / "temp1_input").write_text("80000\n")
        time.sleep(1.2)
        assert "thermal" in self.call({"cmd": "status"})["blockers"]
        (sensor / "temp1_input").write_text("70000\n")
        self.wait(lambda r: r.get("running"))
        self.check("thermal_callback_hysteresis_recovery")
        loads = [self.spawn(["/usr/bin/python3", "-c", "while True: sum(range(10000))"]) for _ in range(2)]
        started = time.monotonic()
        game = self.spawn(["reaper", "-c", "import time;time.sleep(120)", "SteamLaunch", "AppId=987654321", "--", "synthetic-game"], "/usr/bin/python3")
        self.wait(lambda r: r["gaming_detection"]["state"] == "detected" and self.stopped(r), 8)
        elapsed = time.monotonic() - started
        assert elapsed <= 5, elapsed
        self.check("recognised_game_to_empty_under_foreground_load_seconds", elapsed)
        game.terminate()
        game.wait(timeout=5)
        self.wait(lambda r: r.get("running"))
        for child in loads:
            child.terminate()
            child.wait(timeout=5)
        fault = self.spawn(["/usr/bin/python3", "-c", "import time;time.sleep(120)", "x" * 70000])
        row = self.wait(lambda r: r["gaming_detection"]["state"] == "unknown" and self.stopped(r), 8)
        assert row["gaming_detection"]["reason"] == "cmdline_limit"
        fault.terminate()
        fault.wait(timeout=5)
        self.wait(lambda r: r.get("running"))
        self.check("game_exit_and_scan_failure_recovery_via_dwell")
        lease = self.call({"cmd": "begin-update", "purpose": "update", "request_id": "release-smoke"})
        self.wait(self.stopped)
        time.sleep(1.1)
        assert self.stopped(self.call({"cmd": "status"}))
        self.call({"cmd": "end-update", "token": lease["token"], "outcome": "abort"})
        self.wait(lambda r: r.get("running"))
        self.check("independent_lifecycle_hold")
        started = time.monotonic()
        self.run(["systemctl", "kill", "--kill-whom=main", "--signal=SIGKILL", "sparehash.service"])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not self.empty():
            time.sleep(0.1)
        assert self.empty()
        self.check("controller_loss_to_empty_seconds", time.monotonic() - started)
        self.wait(lambda r: r.get("running"), 25)
        self.remove_overlay()
        self.run(["systemctl", "start", "sparehash.service"])
        self.wait(lambda r: r.get("running"))

    def spawn(self, argv: list[str], executable: str | None = None) -> subprocess.Popen:
        user = pwd.getpwnam("nobody")
        child = subprocess.Popen(argv, executable=executable, user=user.pw_uid, group=user.pw_gid,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.children.append(child)
        return child

    def remove_overlay(self) -> None:
        self.run(["systemctl", "stop", "sparehash.service"])
        assert self.empty(), "keep fixtures if stop proof fails"
        if self.drop.exists():
            self.drop.unlink()
            self.drop.parent.rmdir()
            self.run(["systemctl", "daemon-reload"])

    def remove(self) -> None:
        self.call({"cmd": "pause"})
        self.wait(self.stopped)
        cfg = Path("/etc/sparehash/config.toml").read_bytes()
        state = Path("/var/lib/sparehash/state.json").read_bytes()
        foreign = Path("/opt/sparehash/src/sparehash/smoke-foreign-sentinel")
        foreign.write_text("foreign test content\n")
        text = self.run(["/usr/local/bin/sparehash", "uninstall"], expected=1)
        assert "unrecorded content" in text and foreign.read_text() == "foreign test content\n"
        foreign.unlink()  # Withdraw only this test's sentinel; never bypass receipts.
        self.run(["/usr/local/bin/sparehash", "uninstall"])
        assert not Path("/usr/local/bin/sparehash").exists()
        assert Path("/etc/sparehash/config.toml").read_bytes() == cfg
        assert Path("/var/lib/sparehash/state.json").read_bytes() == state
        self.sentinels()
        self.check("foreign_content_refusal_nonpurge_state_and_config_retention")
        replies = "4" + "1" * 94 + "\n127.0.0.1\n" + "\n" * 40
        self.run(["/bin/sh", str(SOURCE / "install.sh"), "--clear-lifecycle-latch", "--use-existing"], replies=replies)
        self.verify_payload()
        self.wait(lambda r: "manual" in r["blockers"] and self.stopped(r))
        self.call({"cmd": "resume"})
        self.wait(lambda r: r.get("running"))
        self.check("explicit_reinstall_preserves_pause_until_resume")
        self.run(["/usr/local/bin/sparehash", "uninstall", "--purge"])
        for name in ("/usr/local/bin/sparehash", "/etc/systemd/system/sparehash.service",
                     "/etc/systemd/system/sparehash-xmrig.service", "/etc/sparehash/config.toml",
                     "/etc/sparehash/xmrig.json", "/etc/sparehash/xmrig.runtime.json"):
            assert not Path(name).exists(), name
        assert self.empty()
        self.sentinels()
        self.check("purge_owned_files_only_and_no_running_miner")

    def close(self) -> None:
        for child in self.children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)
        if self.services_owned and (self.drop.exists() or Path("/etc/systemd/system/sparehash.service").exists()):
            self.remove_overlay()
        if self.services_owned:
            assert self.empty(), "cleanup lacks empty miner proof"
        for label, argv in (
            ("controller-journal", ["journalctl", "-b", "-u", "sparehash.service", "--no-pager"]),
            ("miner-journal", ["journalctl", "-b", "-u", "sparehash-xmrig.service", "--no-pager"]),
            ("kernel-journal", ["journalctl", "-b", "-k", "--no-pager"]),
        ):
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
            (self.evidence / (label + ".log")).write_text(proc.stdout + proc.stderr)
            assert proc.returncode == 0, "journal capture failed"
        if shutil.which("getenforce"):
            assert self.run(["getenforce"]).strip() == "Enforcing"
            audit = (self.evidence / "kernel-journal.log").read_text()
            self.result["avc_lines"] = [line for line in audit.splitlines() if "avc:" in line.lower()]
            assert not self.result["avc_lines"], "AVCs recorded; inspect rather than suppress them"
            self.result["selinux"] = "Enforcing"
        self.save()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="approve destructive disposable-VM actions")
    parser.add_argument("--source-sha256", help="independently verified source inventory digest")
    parser.add_argument("--print-source-sha256", action="store_true", help="read-only source digest; no root/VM required")
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    args = parser.parse_args(argv)
    if args.print_source_sha256:
        print(source_sha256())
        return 0
    if not args.yes:
        parser.error("--yes is required for destructive VM acceptance")
    if os.environ.get("SPAREHASH_QA_DISPOSABLE_VM") != "YES":
        parser.error("SPAREHASH_QA_DISPOSABLE_VM=YES is required")
    if not args.source_sha256 or re.fullmatch(r"[0-9a-f]{64}", args.source_sha256) is None:
        parser.error("--source-sha256 must name the verified 64-character source digest")
    if source_sha256() != args.source_sha256:
        parser.error("source SHA-256 mismatch; no effects applied")
    if args.evidence.parent != Path("/var/tmp") or args.evidence.parent.resolve() != args.evidence.parent:
        parser.error("evidence must be a fresh direct child of /var/tmp")
    if os.geteuid() != 0 or Path("/proc/1/comm").read_text().strip() != "systemd":
        parser.error("root inside a disposable systemd VM is required")
    if any(p.name != "lo" for p in Path("/sys/class/net").iterdir()):
        parser.error("guest networking must be disabled")
    for name in ("/usr/local/bin/sparehash", "/usr/local/bin/xmrig", "/opt/sparehash", "/opt/xmrig",
                 "/etc/sparehash", "/var/lib/sparehash", "/etc/systemd/system/sparehash.service",
                 "/etc/systemd/system/sparehash-xmrig.service", "/run/systemd/system/sparehash.service.d"):
        if os.path.lexists(name):
            parser.error("fresh VM required; refusing pre-existing " + name)
    # No inherited import roots, helper PATH, socket/config QA overrides or
    # proxies may redirect privileged fixture actions after explicit consent.
    os.environ.clear()
    os.environ.update({"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C",
                       "PYTHONDONTWRITEBYTECODE": "1"})
    if shutil.which("getenforce") and subprocess.check_output(["getenforce"], text=True).strip() != "Enforcing":
        parser.error("SELinux must remain Enforcing")
    for helper in ("git", "curl", "systemctl"):
        if shutil.which(helper) is None:
            parser.error("missing fixture prerequisite: " + helper)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(SOURCE))
    smoke = Smoke(args.evidence, args.source_sha256)
    try:
        smoke.prepare()
        smoke.operate()
        smoke.remove()
        assert source_sha256() == args.source_sha256, "source changed during acceptance"
        smoke.result["complete"] = True
    except Exception as error:
        smoke.result["failure"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        try:
            smoke.close()
        except Exception as error:
            smoke.result["complete"] = False
            smoke.result["cleanup_failure"] = type(error).__name__ + ": " + str(error)
            raise
        finally:
            smoke.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
