#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Disposable-VM-only runtime layout/abnormal-exit acceptance. No reboot or tuning."""

from __future__ import annotations

import argparse
import grp
import json
import os
import pwd
import re
import subprocess
import time
from dataclasses import replace
from pathlib import Path

from release_smoke import SOURCE, Smoke
from vm_acceptance import blocked_override, guard


def unknown_proof_command() -> list[str]:
    # Hide manager directories in this transient helper's namespace, not socket
    # inodes (mounting over individual sockets is not portable across policies).
    return ["systemd-run", "--unit=qa-layout-unknown", "--wait", "--pipe",
            "--property=RuntimeMaxSec=15", "--property=NoNewPrivileges=yes",
            "--property=ProtectSystem=strict",
            "--property=TemporaryFileSystem=/run/systemd:ro /run/dbus:ro",
            "/usr/local/bin/sparehash", "__cleanup__"]


class LayoutAcceptance(Smoke):
    pages = Path("/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages")
    lease = Path("/var/lib/sparehash/hugepages.json")
    operation = Path("/run/sparehash-miner/private/operation.json")
    benchmark = operation.with_name("benchmark.json")
    control = Path("/run/sparehash/ipc/control.sock")

    def running(self) -> None:
        self.wait(lambda r: r.get("running") and not self.empty())
        assert int(self.pages.read_text()) == 32 and self.lease.exists() and self.operation.exists()

    def snapshot(self, label: str) -> dict:
        output = self.run(["systemctl", "show", "sparehash.service",
                           "--property=ExecStart,ExecStopPost,ExecMainCode,ExecMainStatus,Result,ActiveState,SubState,"
                           "RuntimeDirectory,RuntimeDirectoryMode,RuntimeDirectoryPreserve,User,Group,"
                           "NoNewPrivileges,ProtectSystem,ProtectHome,PrivateTmp,RestrictAddressFamilies,DropInPaths"])
        row = {"properties": output, "pages": int(self.pages.read_text()), "lease": self.lease.exists(),
               "operation": self.operation.exists(), "benchmark": self.benchmark.exists(),
               "socket": self.control.exists(), "empty": self.empty()}
        paths = (self.control.parent.parent, self.control.parent, self.operation.parent.parent,
                 self.operation.parent, self.control, self.operation)
        row["paths"] = {}
        for path in paths:
            if path.exists():
                info = path.stat()
                row["paths"][str(path)] = [info.st_uid, info.st_gid, oct(info.st_mode & 0o777)]
                self.run(["ls", "-aldZ", str(path)])
        (self.evidence / (label + ".json")).write_text(json.dumps(row, indent=2))
        return row

    def layout(self) -> None:
        for parent in (self.control.parent.parent, self.operation.parent.parent):
            info = parent.stat()
            assert (info.st_uid, info.st_gid, info.st_mode & 0o777) == (0, 0, 0o711)
        for child, group in ((self.control.parent, "sparehash"), (self.operation.parent, "xmrig")):
            info = child.stat()
            assert (info.st_uid, info.st_gid, info.st_mode & 0o777) == (0, grp.getgrnam(group).gr_gid, 0o750)
        info = self.operation.stat()
        assert (info.st_uid, info.st_gid, info.st_mode & 0o777) == (0, pwd.getpwnam("xmrig").pw_gid, 0o640)
        assert not Path("/run/sparehash/control.sock").exists()
        assert not Path("/run/sparehash-miner/operation.json").exists()

    def cleaned(self, label: str) -> None:
        row = self.snapshot(label)
        assert row["empty"] and row["pages"] == 0
        assert not row["lease"] and not row["operation"] and not row["benchmark"] and not row["socket"]
        hook = self.prop("sparehash.service", "ExecStopPost")
        assert "argv[]=/usr/local/bin/sparehash __cleanup__" in hook
        assert re.search(r"pid=[1-9][0-9]* ; code=exited ; status=0(?: |/|})", hook), hook
        self.foreign_preserved()
        self.check(label, {"pages": 0, "hook": hook, "owned_state_removed": True})

    def foreign_preserved(self) -> None:
        for name in self.result.get("foreign", ()):
            assert Path(name).read_text() == "foreign runtime-layout fixture\n", name

    def exercises(self) -> None:
        from sparehash import config

        self.run(["systemctl", "stop", "sparehash.service"])
        self.cleaned("initial_normal_stop")
        assert int(self.pages.read_text()) == 0
        config.save(replace(config.load(), hugepages=config.Hugepages(enabled=True, pages=32)))
        self.drop.parent.mkdir()
        self.drop.write_text("[Service]\nRestart=no\n")
        self.run(["systemctl", "daemon-reload"])
        self.run(["systemctl", "start", "sparehash.service"])
        self.running()
        self.layout()
        foreign = (self.control.parent.parent / "foreign", self.operation.parent.parent / "foreign",
                   self.control.parent / "foreign", self.operation.parent / "foreign",
                   Path("/var/lib/sparehash/runtime-layout-foreign"))
        for path in foreign:
            with path.open("x") as stream:
                stream.write("foreign runtime-layout fixture\n")
            path.chmod(0o600)
        self.result["foreign"] = [str(path) for path in foreign]
        self.snapshot("normal_running")
        self.run(["systemctl", "restart", "sparehash.service"])
        self.running()
        self.layout()
        self.foreign_preserved()
        self.run(["systemctl", "stop", "sparehash.service"])
        self.cleaned("normal_restart_and_stop")
        self.run(["systemctl", "start", "sparehash.service"])
        self.running()

        lease, operation = self.lease.read_bytes(), self.operation.read_bytes()
        self.run(["/usr/local/bin/sparehash", "__cleanup__"], expected=1)
        assert self.lease.read_bytes() == lease and self.operation.read_bytes() == operation
        assert int(self.pages.read_text()) == 32 and self.control.exists()
        self.check("nonempty_cgroup_refuses_before_state_mutation")

        self.snapshot("before_sigkill_active_lease")
        self.run(["systemctl", "kill", "--kill-whom=main", "--signal=SIGKILL", "sparehash.service"])
        self.wait_inactive(12)
        self.cleaned("sigkill_original_hook_restores_active_lease")
        self.run(["systemctl", "reset-failed", "sparehash.service"])
        self.run(["systemctl", "start", "sparehash.service"])
        self.running()
        self.layout()
        self.check("restart_after_sigkill")
        self.run(["systemctl", "stop", "sparehash.service"])

        drop = self.drop.with_name("qa-blocked.conf")
        drop.write_text(blocked_override(SOURCE))
        self.run(["systemctl", "daemon-reload"])
        block, entered = Path("/var/lib/sparehash/qa-block"), Path("/var/lib/sparehash/qa-entered")
        try:
            self.run(["systemctl", "start", "sparehash.service"])
            self.running()
            self.snapshot("before_blocked_worker_active_lease")
            block.touch()
            deadline = time.monotonic() + 3
            while not entered.exists() and time.monotonic() < deadline:
                time.sleep(.025)
            assert entered.exists() and not self.empty() and int(self.pages.read_text()) == 32
            started = time.monotonic()
            self.run(["systemctl", "stop", "--no-block", "sparehash.service"])
            self.wait_inactive(22)
            elapsed = time.monotonic() - started
            assert self.prop("sparehash.service", "ExecMainStatus") == "1" and 11 <= elapsed < 22
            self.cleaned("blocked_worker_hook_restores_active_lease")
            self.check("blocked_worker_bounded_nonzero_exit", elapsed)
        finally:
            block.unlink(missing_ok=True)
            entered.unlink(missing_ok=True)
            self.run(["systemctl", "stop", "sparehash.service"])
            drop.unlink()
            self.run(["systemctl", "daemon-reload"])
            self.run(["systemctl", "reset-failed", "sparehash.service"])
        self.run(["systemctl", "start", "sparehash.service"])
        self.running()
        self.foreign_preserved()
        self.unverifiable()
        self.remove_overlay()
        assert int(self.pages.read_text()) == 0
        # Withdraw only these exact nested fixture sentinels before account
        # deletion/recreation; top-level foreign files remain through uninstall.
        for path in foreign[2:4]:
            assert path.read_text() == "foreign runtime-layout fixture\n"
            path.unlink()
            path.parent.rmdir()
            self.result["foreign"].remove(str(path))
        self.run(["systemctl", "start", "sparehash.service"])
        self.running()
        self.remove()
        self.foreign_preserved()
        assert int(self.pages.read_text()) == 0 and not self.lease.exists()
        assert not self.operation.exists() and not self.control.exists()
        self.check("uninstall_reinstall_purge_preserve_foreign_state")

    def unverifiable(self) -> None:
        lease, operation = self.lease.read_bytes(), self.operation.read_bytes()
        self.run(["systemctl", "kill", "--kill-whom=main", "--signal=SIGSTOP", "sparehash.service"])
        try:
            self.run(["systemctl", "stop", "sparehash-xmrig.service"])
            assert self.empty()
            proc = subprocess.run(unknown_proof_command(), capture_output=True, text=True, timeout=30)
            (self.evidence / "unknown-proof-command.log").write_text(proc.stdout + proc.stderr)
            shown = self.run(["systemctl", "show", "qa-layout-unknown.service",
                              "--property=ExecMainCode,ExecMainStatus,ExecStart,Result"])
            self.run(["journalctl", "-b", "-u", "qa-layout-unknown.service", "--no-pager"])
            assert proc.returncode == 1 and "ExecMainCode=1\n" in shown and "ExecMainStatus=1\n" in shown, shown
            self.run(["systemctl", "stop", "qa-layout-unknown.service"])
            self.run(["systemctl", "reset-failed", "qa-layout-unknown.service"])
            assert self.lease.read_bytes() == lease and self.operation.read_bytes() == operation
            assert int(self.pages.read_text()) == 32 and self.control.exists()
            self.check("unverifiable_cgroup_refuses_with_actual_empty_miner")
        finally:
            self.run(["systemctl", "kill", "--kill-whom=main", "--signal=SIGCONT", "sparehash.service"])
        self.call({"cmd": "pause"})
        self.wait(self.stopped)
        self.call({"cmd": "resume"})
        self.running()

    def wait_inactive(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.prop("sparehash.service", "ActiveState") in ("inactive", "failed"):
                return
            time.sleep(.05)
        raise RuntimeError("controller termination deadline exceeded")

    def audit(self) -> None:
        proc = subprocess.run(["journalctl", "-b", "_TRANSPORT=audit", "--no-pager"],
                              capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0
        (self.evidence / "audit-journal.log").write_text(proc.stdout)
        denied = [line for line in proc.stdout.splitlines() if "avc:" in line.lower() and "denied" in line.lower()
                  and any(name in line for name in ("sparehash", "operation.json", "benchmark.json", "control.sock"))]
        self.result["relevant_audit_denials"] = denied
        self.save()
        assert not denied, "relevant SELinux denial recorded"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha256", required=True)
    args = parser.parse_args()
    guard(args.source_sha256)
    for name in ("/usr/local/bin/sparehash", "/usr/local/bin/xmrig", "/etc/sparehash", "/var/lib/sparehash",
                 "/run/sparehash", "/run/sparehash-miner"):
        if os.path.lexists(name):
            parser.error("fresh disposable VM required: " + name)
    run = LayoutAcceptance(Path("/var/tmp/runtime-layout-evidence"), args.source_sha256)
    try:
        run.prepare()
        run.exercises()
        run.result["complete"] = True
    except Exception as error:
        run.result["failure"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        try:
            run.close()
            run.audit()
        except Exception as error:
            run.result["complete"] = False
            run.result["cleanup_failure"] = type(error).__name__ + ": " + str(error)
            raise
        finally:
            run.save()


if __name__ == "__main__":
    main()
