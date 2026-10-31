#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded disposable-VM-only cgroup and abnormal-exit investigation. No reboot."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import replace
from pathlib import Path

from release_smoke import SOURCE, Smoke
from vm_acceptance import guard


class Investigation(Smoke):
    def read(self, path: Path) -> str | None:
        try:
            return path.read_text()
        except OSError:
            return None

    def snapshot(self, label: str) -> dict:
        row = {}
        for unit in ("sparehash.service", "sparehash-xmrig.service", "qa-priority-contender.service"):
            output = self.run(["systemctl", "show", unit])
            fields = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
            row[unit] = fields
            group = fields.get("ControlGroup", "")
            if group and group != "/":
                fields["kernel"] = self.cgroup(Path("/sys/fs/cgroup") / group.lstrip("/"))
        for name in ("sparehash", "sparehash-miner"):
            directory = Path("/run") / name
            if directory.exists():
                info = directory.stat()
                row[str(directory)] = {"uid": info.st_uid, "gid": info.st_gid, "mode": oct(info.st_mode & 0o777)}
                self.run(["ls", "-aldZ", str(directory)])
                self.run(["ls", "-alZ", str(directory)])
        row["hugepages"] = self.read(Path("/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages"))
        row["lease_exists"] = Path("/var/lib/sparehash/hugepages.json").exists()
        row["operation_exists"] = Path("/run/sparehash-miner/operation.json").exists()
        (self.evidence / (label + ".json")).write_text(json.dumps(row, indent=2))
        return row

    def cgroup(self, group: Path) -> dict:
        row = {name: self.read(group / name) for name in (
            "cgroup.controllers", "cgroup.subtree_control", "cpu.idle", "cpu.weight", "cpu.max", "cpu.stat",
            "cpuset.cpus.effective", "cgroup.procs")}
        pids = (row["cgroup.procs"] or "").split()
        row["processes"] = [{"pid": int(pid), "policy": os.sched_getscheduler(int(pid)),
                             "nice": os.getpriority(os.PRIO_PROCESS, int(pid)),
                             "cgroup": self.read(Path("/proc") / pid / "cgroup")} for pid in pids]
        return row

    def capabilities(self) -> None:
        from sparehash import systemd
        from sparehash.sysdetect import Capabilities

        root = Path("/sys/fs/cgroup")
        self.result["kernel"] = self.run(["uname", "-r"]).strip()
        self.result["systemd"] = self.run(["systemctl", "--version"]).splitlines()[0]
        self.result["root_cgroup"] = self.cgroup(root)
        self.result["system_slice"] = self.cgroup(root / "system.slice")
        calls = []

        def runner(argv, **kwargs):
            proc = systemd._run(argv, **kwargs)
            calls.append({"argv": list(argv), "exit": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr})
            return proc

        self.result["direct_probe"] = systemd.probe_cpuweight_idle({}, runner=runner)
        self.result["probe_calls"] = calls
        self.result["detected_idle"] = Capabilities.detect(environ={}, manager_probe=systemd.probe_cpuweight_idle).cpuweight_idle
        self.save()
        self.check("capability_evidence_recorded", {"root_idle": self.read(root / "cpu.idle"),
                                                   "probe": self.result["direct_probe"],
                                                   "detected": self.result["detected_idle"]})

    def contention(self) -> None:
        self.run(["systemctl", "stop", "sparehash.service"])
        assert self.empty()
        self.result["contention"] = []
        unit = "qa-priority-contender.service"
        active = False
        try:
            for index, mode in enumerate(("baseline", "process", "group", "group", "process", "baseline")):
                if mode == "baseline":
                    if active:
                        self.run(["systemctl", "stop", unit])
                        active = False
                elif not active:
                    self.run(["systemd-run", "--unit=" + unit, "--property=User=nobody",
                              "--property=WorkingDirectory=/var/tmp", "--property=Nice=19",
                              "--property=CPUSchedulingPolicy=idle", "--property=CPUWeight=100",
                              "--property=RuntimeMaxSec=240", "/usr/bin/python3", "-c",
                              "import subprocess,time; p=[subprocess.Popen(['/usr/bin/python3','-c',"
                              "'while True: sum(range(10000))']) for _ in range(4)]; time.sleep(230)"])
                    active = True
                if active:
                    self.run(["systemctl", "set-property", "--runtime", unit, "CPUWeight=" + ("idle" if mode == "group" else "100")])
                group = Path("/sys/fs/cgroup") / self.prop(unit, "ControlGroup").lstrip("/") if active else None
                before = self.cgroup(group) if group else None
                if before:
                    deadline = time.monotonic() + 3
                    while len(before["processes"]) < 5 and time.monotonic() < deadline:
                        time.sleep(.05)
                        before = self.cgroup(group)
                    assert before["cpu.idle"].strip() == ("1" if mode == "group" else "0")
                    assert len(before["processes"]) == 5
                    assert all(p["policy"] == os.SCHED_IDLE and p["nice"] == 19 for p in before["processes"])
                target = Path(f"/var/tmp/priority-{index}.json")
                self.run(["runuser", "-u", "nobody", "--", "env", "--chdir=/var/tmp", "python3",
                          str(SOURCE / "qa/cpu_contention.py"), "--label", "stopped" if mode == "baseline" else "mining",
                          "--workers", "4", "--seconds", "3", "--output", str(target)])
                data = json.loads(target.read_text())
                data.update(mode=mode, contender_before=before, contender_after=self.cgroup(group) if group else None)
                # Observe foreground ancestry without moving tasks or altering limits.
                ancestry = {}
                for sample in data["samples"]:
                    for worker in sample["workers"]:
                        part = worker["cgroup"].strip().split("::", 1)[1]
                        parent = (Path("/sys/fs/cgroup") / part.lstrip("/")).parent
                        while parent.is_relative_to(Path("/sys/fs/cgroup")):
                            ancestry[str(parent)] = {name: self.read(parent / name) for name in
                                                     ("cpu.idle", "cpu.weight", "cpu.max", "cpu.stat", "cgroup.subtree_control")}
                            if parent == Path("/sys/fs/cgroup"):
                                break
                            parent = parent.parent
                data["foreground_ancestry"] = ancestry
                self.result["contention"].append(data)
                self.check("contention_" + str(index) + "_" + mode, data["median_mib_per_second"])
        finally:
            if active:
                self.run(["systemctl", "stop", unit])

    def cleanup_diagnostics(self) -> None:
        from sparehash import config

        self.run(["systemctl", "stop", "sparehash.service"])
        self.run(["systemctl", "reset-failed", "sparehash.service"])
        overlay = self.evidence / "hugepages.toml"
        config.save(replace(config.load(), hugepages=config.Hugepages(enabled=True, pages=32)), overlay)
        self.drop.parent.mkdir()
        self.drop.write_text(f"[Service]\nBindReadOnlyPaths={overlay}:/etc/sparehash/config.toml\nRestart=no\n")
        self.run(["systemctl", "daemon-reload"])
        self.run(["systemctl", "cat", "sparehash.service"])
        self.run(["systemctl", "start", "sparehash.service"])
        self.wait(lambda r: r.get("running"))
        before = self.snapshot("before-kill")
        assert int(before["hugepages"]) == 32
        pid = int(self.prop("sparehash.service", "MainPID"))
        self.run(["cat", f"/proc/{pid}/mountinfo"])
        self.run(["cat", f"/proc/{pid}/attr/current"])
        self.run(["systemctl", "kill", "--kill-whom=main", "--signal=SIGKILL", "sparehash.service"])
        deadline = time.monotonic() + 15
        while self.prop("sparehash.service", "ActiveState") in ("active", "deactivating") and time.monotonic() < deadline:
            time.sleep(.1)
        assert self.empty()
        self.snapshot("after-kill")
        self.run(["journalctl", "-b", "-u", "sparehash.service", "--no-pager"])
        self.run(["journalctl", "-b", "_TRANSPORT=audit", "--no-pager", "-n", "100"])
        self.run(["journalctl", "-b", "_PID=1", "--no-pager", "-n", "150"])
        if Path("/var/log/audit/audit.log").exists():
            self.run(["tail", "-n", "100", "/var/log/audit/audit.log"])
        self.check("abnormal_exit_diagnostics_collected")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--evidence", type=Path, default=Path("/var/tmp/priority-cleanup"))
    args = parser.parse_args()
    guard(args.source_sha256)
    if args.evidence.parent != Path("/var/tmp") or args.evidence.exists():
        parser.error("fresh evidence directly under /var/tmp required")
    for path in ("/usr/local/bin/xmrig", "/usr/local/bin/sparehash", "/etc/sparehash", "/opt/sparehash"):
        if os.path.lexists(path):
            parser.error("fresh disposable required")
    probe = Investigation(args.evidence, args.source_sha256)
    try:
        probe.capabilities()
        probe.prepare()
        probe.contention()
        probe.cleanup_diagnostics()
    except Exception as error:
        probe.result["failure"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        probe.save()
        probe.close()


if __name__ == "__main__":
    main()
