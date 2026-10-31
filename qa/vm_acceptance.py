#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Two-phase disposable-VM acceptance, extending the existing release smoke.

Run install, reboot the guest through its lab transport, then run verify.
Never invoke on a physical workstation. No lab dependency is imported.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from release_smoke import SOURCE, Smoke, source_sha256


def blocked_override(source: Path) -> str:
    return ("[Service]\nEnvironment=SPAREHASH_QA_DISPOSABLE_VM=YES\n"
            + f"BindReadOnlyPaths={source / 'qa/fixtures/blocked_controller.py'}:/run/sparehash/qa-controller.py\n"
            + "ExecStart=\nExecStart=/usr/bin/python3 /run/sparehash/qa-controller.py\n")


class Acceptance(Smoke):
    def hugepages(self) -> None:
        from dataclasses import replace

        from sparehash import config

        self.run(["systemctl", "stop", "sparehash.service"])
        assert self.empty()
        self.run(["systemctl", "reset-failed", "sparehash.service"])
        pages = Path("/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages")
        baseline = int(pages.read_text())
        assert baseline == 0, "fresh guest hugepage ownership must be unambiguous"
        overlay = self.evidence / "hugepages.toml"
        config.save(replace(config.load(), hugepages=config.Hugepages(enabled=True, pages=32)), overlay)
        self.drop.parent.mkdir()
        self.drop.write_text(f"[Service]\nBindReadOnlyPaths={overlay}:/etc/sparehash/config.toml\n")
        self.run(["systemctl", "daemon-reload"])
        self.run(["systemctl", "start", "sparehash.service"])
        self.wait(lambda r: r.get("running"))
        assert int(pages.read_text()) == 32
        self.call({"cmd": "pause"})
        self.wait(self.stopped)
        assert int(pages.read_text()) == baseline
        self.call({"cmd": "resume"})
        self.wait(lambda r: r.get("running"))
        assert int(pages.read_text()) == 32
        self.remove_overlay()
        assert int(pages.read_text()) == baseline
        assert not Path("/var/lib/sparehash/hugepages.json").exists()
        self.check("hugepage_allocate_pause_release_resume_stop_cleanup", {"baseline": baseline, "allocated": 32, "final": 0})
        self.run(["systemctl", "start", "sparehash.service"])
        self.wait(lambda r: r.get("running"))

    def blocked_worker(self) -> None:
        self.run(["systemctl", "stop", "sparehash.service"])
        assert self.empty()
        self.run(["systemctl", "reset-failed", "sparehash.service"])
        drop = Path("/run/systemd/system/sparehash.service.d/qa-blocked.conf")
        drop.parent.mkdir()
        drop.write_text(blocked_override(SOURCE))
        block = Path("/var/lib/sparehash/qa-block")
        entered = Path("/var/lib/sparehash/qa-entered")
        self.run(["systemctl", "daemon-reload"])
        try:
            self.run(["systemctl", "start", "sparehash.service"])
            self.wait(lambda r: r.get("running"))
            block.touch()
            self.wait(lambda r: r["gaming_detection"]["state"] == "unknown" and self.stopped(r), 15)
            assert entered.exists()
            self.check("blocked_scanner_stale_result_inhibits")
            block.unlink()
            self.run(["systemctl", "stop", "sparehash.service"])
            assert self.empty()
            entered.unlink()
            self.run(["systemctl", "reset-failed", "sparehash.service"])
            self.run(["systemctl", "start", "sparehash.service"])
            self.wait(lambda r: r.get("running"))
            self.check("stale_worker_service_restart_recovers")
            block.touch()
            deadline = time.monotonic() + 3
            while not entered.exists() and time.monotonic() < deadline:
                time.sleep(.05)
            assert entered.exists()
            assert not self.empty(), "blocked-worker SIGTERM test requires a live miner"
            started = time.monotonic()
            self.run(["systemctl", "stop", "--no-block", "sparehash.service"])
            while not self.empty() and time.monotonic() - started < 5:
                time.sleep(.05)
            assert self.empty()
            empty_seconds = time.monotonic() - started
            while self.prop("sparehash.service", "ActiveState") in ("active", "deactivating") and time.monotonic() - started < 22:
                time.sleep(.1)
            assert self.prop("sparehash.service", "ExecMainStatus") == "1"
            elapsed = time.monotonic() - started
            assert 11 <= elapsed < 22
            self.check("blocked_worker_sigterm_empty_and_nonzero_bounded_exit",
                       {"empty_seconds": empty_seconds, "exit_seconds": elapsed, "exit_status": 1})
        finally:
            block.unlink(missing_ok=True)
            entered.unlink(missing_ok=True)
            self.run(["systemctl", "stop", "sparehash.service"])
            drop.unlink()
            drop.parent.rmdir()
            self.run(["systemctl", "daemon-reload"])
            self.run(["systemctl", "reset-failed", "sparehash.service"])
        self.run(["systemctl", "start", "sparehash.service"])
        self.wait(lambda r: r.get("running"))

    def contention(self) -> None:
        from cpu_contention import compare

        self.run(["systemctl", "stop", "sparehash.service"])
        assert self.empty()
        rows = {}
        contender_started = False
        try:
            for label in ("stopped", "mining"):
                if label == "mining":
                    self.run(["systemd-run", "--unit=sparehash-qa-contention",
                              "--property=User=nobody", "--property=Nice=19",
                              "--property=WorkingDirectory=/var/tmp",
                              "--property=CPUSchedulingPolicy=idle", "--property=RuntimeMaxSec=120",
                              "--property=KillMode=control-group", "/usr/bin/python3", "-c",
                              "import subprocess,time; "
                              "p=[subprocess.Popen(['/usr/bin/python3','-c',"
                              "'while True: sum(range(10000))']) for _ in range(4)]; time.sleep(110)"])
                    contender_started = True
                target = Path(f"/var/tmp/sparehash-contention-{label}.json")
                self.run(["runuser", "-u", "nobody", "--", "/usr/bin/env", "--chdir=/var/tmp", "/usr/bin/python3",
                          str(SOURCE / "qa/cpu_contention.py"), "--label", label,
                          "--output", str(target), "--workers", "4", "--seconds", "3"])
                rows[label] = json.loads(target.read_text())
                (self.evidence / target.name).write_bytes(target.read_bytes())
            pid = int(self.prop("sparehash-qa-contention.service", "MainPID"))
            assert os.sched_getscheduler(pid) == os.SCHED_IDLE
            assert os.getpriority(os.PRIO_PROCESS, pid) == 19
            group = Path("/sys/fs/cgroup") / self.prop("sparehash-qa-contention.service", "ControlGroup").lstrip("/")
            pids = [int(value) for value in (group / "cgroup.procs").read_text().split()]
            assert len(pids) == 5  # One sleeping parent and four actual contenders.
            scheduling = [{"pid": child, "policy": os.sched_getscheduler(child),
                           "nice": os.getpriority(os.PRIO_PROCESS, child)} for child in pids]
            assert all(row["policy"] == os.SCHED_IDLE and row["nice"] == 19 for row in scheduling)
            result = compare(rows["stopped"], rows["mining"])
            result["contender_scheduling"] = scheduling
            result["contender_cpu_stat"] = (group / "cpu.stat").read_text()
            result["scope"] = "4-vCPU VM; independent non-hashing idle/nice19 synthetic contender, not RandomX"
            self.check("quantitative_cpu_contention", result)
        finally:
            if contender_started:
                self.run(["systemctl", "stop", "sparehash-qa-contention.service"])
        self.run(["systemctl", "start", "sparehash.service"])
        self.wait(lambda r: r.get("running"))

    def extra_sensors(self) -> None:
        # operate() has removed its overlays. Re-use only our fixture files.
        battery = self.evidence / "power/BAT0"
        sensor = self.evidence / "hwmon/hwmon0/temp1_input"
        self.run(["systemctl", "stop", "sparehash.service"])
        assert self.empty()
        self.run(["systemctl", "reset-failed", "sparehash.service"])
        self.drop.parent.mkdir()
        self.drop.write_text("[Service]\n" + "".join(f"BindReadOnlyPaths={src}:{dst}\n" for src, dst in (
            (battery.parent, "/sys/class/power_supply"),
            (sensor.parent.parent, "/sys/class/hwmon"),
            (self.evidence / "config.toml", "/etc/sparehash/config.toml"))))
        (battery / "scope").write_text("Device\n")
        (battery / "present").unlink()
        (battery / "status").write_text("Unknown\n")
        self.run(["systemctl", "daemon-reload"])
        self.run(["systemctl", "start", "sparehash.service"])
        self.wait(lambda r: r.get("running"))
        self.check("peripheral_only_missing_present_unknown_status_allows")
        (battery / "scope").write_text("Unknown\n")
        self.wait(lambda r: "battery" in r["blockers"] and self.stopped(r), 12)
        (battery / "scope").write_text("Device\n")
        self.wait(lambda r: r.get("running"))
        self.check("ambiguous_scope_inhibits_then_recovers")
        sensor.write_text("not-a-temperature\n")
        self.wait(lambda r: "thermal" in r["blockers"] and self.stopped(r), 8)
        sensor.write_text("70000\n")
        self.wait(lambda r: r.get("running"))
        self.check("malformed_sensor_inhibits_then_recovers")
        sensor.unlink()
        self.wait(lambda r: "thermal" in r["blockers"] and self.stopped(r), 8)
        self.run(["systemctl", "restart", "sparehash.service"])
        self.wait(lambda r: "thermal" in r["blockers"] and self.stopped(r), 8)
        self.check("missing_sensor_at_runtime_and_restart_inhibits")
        sensor.write_text("70000\n")
        self.run(["systemctl", "restart", "sparehash.service"])
        self.wait(lambda r: r.get("running"))
        self.remove_overlay()
        self.run(["systemctl", "start", "sparehash.service"])
        self.wait(lambda r: r.get("running"))


def guard(digest: str) -> None:
    if os.environ.get("SPAREHASH_QA_DISPOSABLE_VM") != "YES" or os.geteuid() != 0:
        raise ValueError("explicit disposable VM consent and guest root required")
    if subprocess.run(["systemd-detect-virt", "--vm", "--quiet"], check=False).returncode:
        raise ValueError("refusing non-VM environment")
    if any(path.name != "lo" for path in Path("/sys/class/net").iterdir()):
        raise ValueError("guest networking must be disabled")
    if source_sha256() != digest:
        raise ValueError("source digest mismatch")
    os.environ.clear()
    os.environ.update({"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C",
                       "PYTHONDONTWRITEBYTECODE": "1"})
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(SOURCE))


def checked_reboot(phase: str, before: str, current: str) -> bool:
    rebooted = current != before
    if phase == "verify":
        assert rebooted, "verify requires an actual reboot"
    else:
        assert phase in ("exercise", "tail") and not rebooted, "exercise/tail are explicitly non-reboot subsets"
    return rebooted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("install", "verify", "exercise", "tail"))
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--evidence", type=Path, default=Path("/var/tmp/sparehash-acceptance"))
    args = parser.parse_args()
    guard(args.source_sha256)
    if args.evidence.parent != Path("/var/tmp") or args.evidence.is_symlink():
        parser.error("evidence must be a direct non-symlink child of /var/tmp")
    if args.phase == "install":
        for name in ("/usr/local/bin/xmrig", "/usr/local/bin/sparehash", "/opt/sparehash",
                     "/etc/sparehash", "/var/lib/sparehash", "/run/systemd/system/sparehash.service.d"):
            if os.path.lexists(name):
                parser.error("fresh VM required: " + name)
        smoke = Acceptance(args.evidence, args.source_sha256)
    else:
        smoke = Acceptance.__new__(Acceptance)
        smoke.evidence = args.evidence
        smoke.result = json.loads((args.evidence / "summary.json").read_text())
        assert smoke.result["source_sha256"] == args.source_sha256
        smoke.children = []
        smoke.sequence = 100
        smoke.services_owned = True
        smoke.drop = Path("/run/systemd/system/sparehash.service.d/s4-smoke.conf")
    try:
        if args.phase == "install":
            smoke.result["boot_before"] = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            smoke.prepare()
            assert smoke.prop("sparehash.service", "UnitFileState") == "enabled"
            smoke.check("enabled_for_disposable_reboot")
        else:
            rebooted = checked_reboot(args.phase, smoke.result["boot_before"],
                                     Path("/proc/sys/kernel/random/boot_id").read_text().strip())
            smoke.wait(lambda r: r.get("running"), 40)
            smoke.verify_payload()
            smoke.check("actual_reboot_autostart_and_exact_payload" if rebooted else "non_reboot_subset_exact_payload")
            if args.phase != "tail":
                smoke.contention()
                smoke.operate()
                smoke.extra_sensors()
            smoke.blocked_worker()
            smoke.hugepages()
            smoke.remove()
            smoke.result["tail_complete" if args.phase == "tail" else "non_reboot_complete"] = True
            smoke.result["complete"] = rebooted
    except Exception as error:
        smoke.result["failure"] = type(error).__name__ + ": " + str(error)
        smoke.result["complete"] = False
        raise
    finally:
        smoke.save()
        if args.phase != "install" or "failure" in smoke.result:
            try:
                smoke.close()
            except Exception as error:
                smoke.result["complete"] = False
                smoke.result["cleanup_failure"] = type(error).__name__ + ": " + str(error)
                smoke.save()
                raise
        assert source_sha256() == args.source_sha256


if __name__ == "__main__":
    main()
