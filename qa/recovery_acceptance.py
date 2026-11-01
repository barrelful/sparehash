# SPDX-License-Identifier: GPL-3.0-or-later
"""Disposable-VM-only one-off recovery acceptance; no physical-host execution."""

from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
import shutil
import socket
import subprocess
from dataclasses import replace
from pathlib import Path

from release_smoke import SOURCE, Smoke
from vm_acceptance import guard


class RecoveryAcceptance(Smoke):
    def exercise(self, commit: str, digest: str) -> None:
        from sparehash import config
        from sparehash.deployment import Tree, checked
        from sparehash.hugepages import LeaseManager
        from sparehash.install_ledger import Ledger
        from sparehash.install_manifest import locked_store
        from sparehash.runtime_jobs import empty
        from sparehash.xmrig_ledger import Artifact
        from sparehash.xmrig_manifest import VersionRecord

        old = Path("/var/tmp/original-source")
        assert not Path("/usr/local/bin/sparehash").exists()
        synthetic = Path("/usr/local/bin/xmrig")
        synthetic.write_bytes((SOURCE / "qa/fixtures/synthetic_xmrig.py").read_bytes())
        synthetic.chmod(0o755)
        answers = self.evidence / "answers.toml"
        config.save(config.Config(wallet="4" + "1" * 94, pool=config.Pool(host="127.0.0.1")), answers)
        self.run(["/bin/sh", str(old / "install.sh"), "--answers", str(answers), "--yes", "--use-existing", "--no-start", "--no-enable"])
        self.services_owned = True
        assert self.prop("sparehash.service", "ActiveState") == "inactive" and empty()
        # Offline managed version fixture: no upstream download or actual hashing.
        managed = Path("/opt/xmrig")
        (managed / "xmrig-6.26.0").mkdir(parents=True, mode=0o755)
        shutil.copyfile(synthetic, managed / "xmrig-6.26.0/xmrig")
        (managed / "xmrig-6.26.0/xmrig").chmod(0o755)
        (managed / "current").symlink_to("xmrig-6.26.0")
        (managed / ".sparehash-managed").write_bytes(b"/opt/xmrig")
        (managed / ".sparehash-managed").chmod(0o640)
        with locked_store() as store:
            prior = store.read()
            assert prior is not None
            store.write(replace(prior.manifest, versions=(VersionRecord("xmrig-6.26.0", "2026-10-07T00:00:00Z", "github:xmrig/xmrig:v6.26.0"),)))
        manifest_path = Path("/var/lib/sparehash/install-manifest.json")
        initial_size = manifest_path.stat().st_size
        ledger = Ledger(Tree())
        recorded = 0
        while manifest_path.stat().st_size < 81416:
            def disabled() -> tuple[Artifact, ...]:
                checked(("/usr/bin/systemctl", "disable", "sparehash.service"))
                return (ledger.tree.receipt("/etc/systemd/system/sparehash.service", False),)
            ledger.perform(("/etc/systemd/system/sparehash.service", "disable-controller"), disabled)
            recorded += 1
        ledger.ready()
        size = manifest_path.stat().st_size
        assert 65536 < size < 262144
        self.check("original_install_large_canonical_manifest", {"bytes": size, "initial_bytes": initial_size,
                   "recorded_disable_operations": recorded, "original_commit": "f348ed165358668d8101cd121cf07ee2f001295f"})
        ipc, miner = Path("/run/sparehash"), Path("/run/sparehash-miner")
        for directory, group in ((ipc, "sparehash"), (miner, "xmrig")):
            directory.mkdir(mode=0o750)
            directory.chmod(0o750)
            os.chown(directory, 0, grp.getgrnam(group).gr_gid)
        # Use the original operation publisher, not the corrected runtime layout.
        script = ("import sys;sys.path.insert(0,'/opt/sparehash/src');"
                  "from sparehash import config,runtime_spec;runtime_spec.publish(config.load(),1,'mine')")
        self.run(["/usr/bin/python3", "-I", "-B", "-c", script])
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(ipc / "control.sock"))
        os.chown(ipc / "control.sock", 0, grp.getgrnam("sparehash").gr_gid)
        (ipc / "control.sock").chmod(0o660)
        benchmark = Path("/root/sparehash-contention-results/session-fixture/report.txt")
        benchmark.parent.mkdir(parents=True)
        benchmark.write_text("owner benchmark sentinel\n")
        foreign = [ipc / "foreign", miner / "foreign", Path("/etc/sparehash/recovery-foreign"), Path("/var/lib/sparehash/recovery-foreign"), benchmark]
        for path in foreign[:-1]:
            path.write_text("foreign recovery sentinel\n")
            path.chmod(0o600)
        preserved = [*foreign, *(Path("/etc/sparehash") / name for name in ("config.toml", "xmrig.json", "xmrig.runtime.json"))]
        before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in preserved}
        error = self.run(["/usr/local/bin/sparehash", "uninstall"], expected=1)
        assert "valid protected marker and manifest are required" in error
        assert not Path("/opt/sparehash/src/sparehash").exists() and Path("/usr/local/bin/sparehash").exists()
        journal = Path("/var/lib/sparehash/uninstall-progress.json")
        progress = json.loads(journal.read_text())
        assert progress["phases"] == ["leaf-receipts"] and len(progress["removed"]) == 58
        failed = self.run(["/usr/local/bin/sparehash", "__cleanup__"], expected=1)
        assert "ModuleNotFoundError" in failed
        self.check("real_old_uninstall_failure_and_missing_modules", {"removed": 58, "phases": progress["phases"], "launcher_survives": True})
        stage = Path("/run/sparehash/recovery") / commit
        bootstrap = self.run(["/bin/sh", str(SOURCE / "qa/recovery_bootstrap.sh"), "/var/tmp/recovery-bundle.tar", commit, digest])
        assert "non-purge recovery eligible" in bootstrap
        command = ["/usr/bin/python3", "-I", "-B", str(stage / "stage.py"), commit, digest]
        protected_state = [journal, manifest_path, Path("/var/lib/sparehash/lifecycle.json"), Path("/usr/local/bin/sparehash")]
        snapshot = {str(path): path.read_bytes() for path in protected_state}
        self.run([*command, "preflight"])
        assert all(path.read_bytes() == snapshot[str(path)] for path in protected_state)
        self.check("read_only_installed_state_preflight")
        # Changed shim and malformed journal must fail before any removal.
        for path, invalid in ((Path("/usr/local/bin/sparehash"), b"# foreign replacement"), (journal, b"{}"), (manifest_path, b"{}")):
            original = path.read_bytes()
            path.write_bytes(invalid)
            self.run([*command, "preflight"], expected=1)
            assert path.read_bytes() == invalid and managed.exists()
            path.write_bytes(original)  # Withdraw only this fixture's injected corruption.
        self.check("shim_manifest_journal_substitution_refusal")
        # Real nonempty miner cgroup, using only a sleep fixture with unit sandbox retained.
        drop = Path("/run/systemd/system/sparehash-xmrig.service.d/recovery-fixture.conf")
        drop.parent.mkdir()
        drop.write_text("[Unit]\nBindsTo=\n[Service]\nExecCondition=\nExecStart=\nExecStart=/usr/bin/sleep 120\nRestart=no\n")
        self.run(["systemctl", "daemon-reload"])
        self.run(["systemctl", "start", "sparehash-xmrig.service"])
        assert not empty()
        self.run([*command, "preflight"], expected=1)
        self.run(["systemctl", "stop", "sparehash-xmrig.service"])
        drop.unlink()
        drop.parent.rmdir()
        self.run(["systemctl", "daemon-reload"])
        self.run(["systemd-run", "--unit=qa-recovery-unknown", "--wait", "--pipe", "--collect",
                  "--property=NoNewPrivileges=yes", "--property=ProtectSystem=strict",
                  "--property=TemporaryFileSystem=/run/systemd:ro /run/dbus:ro", *command, "preflight"], expected=1)
        assert all(path.read_bytes() == snapshot[str(path)] for path in protected_state)
        self.check("real_nonempty_and_unknown_manager_refusal")
        pages = LeaseManager(empty_proof=empty)
        result = pages.reserve_after_authorization(True, (0,), 32, authorized=True)
        assert result.status == "reserved" and result.actual == 32
        page_count = Path("/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages")
        page_count.write_text("33\n")
        self.run([*command, "preflight"], expected=1)
        assert pages.ledger_path.exists() and managed.exists()
        page_count.write_text("32\n")
        self.run([*command, "preflight"])
        self.check("hugepage_external_change_refusal_and_active_32_page_lease")
        # SIGKILL only this owned helper after a real version-file unlink and fsync.
        fault = ("import sys,os,signal;sys.path[:0]=" + repr([str(stage / "source/qa"), str(stage / "source")]) + ";"
                 "import recover_uninstall as r,recovery_stage as s;"
                 f"d,f=s.verified_source(s.BASE/'{commit}','{digest}','{commit}');"
                 "original=os.unlink\n"
                 "def cut(path,*a,**kw):\n original(path,*a,**kw)\n"
                 " if path=='xmrig':\n  os.fsync(kw['dir_fd']);os.kill(os.getpid(),signal.SIGKILL)\n"
                 "os.unlink=cut\nr.recover(d,apply=True)\n")
        self.run(["/usr/bin/python3", "-I", "-B", "-c", fault], expected=-9)
        assert not (managed / "xmrig-6.26.0/xmrig").exists() and manifest_path.exists()
        assert int(page_count.read_text()) == 0 and not pages.ledger_path.exists()
        assert not (ipc / "control.sock").exists() and not (miner / "operation.json").exists()
        assert any(item.startswith("recovery-v1:") for item in json.loads(journal.read_text())["removed"])
        self.check("sigkill_after_actual_unlink_durable_plan_pages_and_old_runtime_cleanup")
        (managed / "foreign").write_text("unrelated new file")
        (managed / "foreign").chmod(0o600)
        self.run([*command, "apply"], expected=1)
        assert (managed / "foreign").read_text() == "unrelated new file"
        (managed / "foreign").unlink()  # Withdraw only the fixture's deliberate collision.
        self.run([*command, "apply"])
        self.run([*command, "apply"])
        assert not managed.exists() and not Path("/usr/local/bin/sparehash").exists()
        assert all(hashlib.sha256(Path(name).read_bytes()).hexdigest() == value for name, value in before.items())
        assert json.loads(Path("/var/lib/sparehash/lifecycle.json").read_text())["retired"] is True
        self.check("resumed_and_repeated_nonpurge_recovery_preserves_config_foreign_and_benchmark")
        assert not journal.exists() and (stage / "complete.json").exists()
        replies = "4" + "1" * 94 + "\n127.0.0.1\n" + "\n" * 100
        self.run(["/usr/bin/python3", "-I", "-B", str(stage / "source/sparehash/__main__.py"), "install",
                  "--clear-lifecycle-latch", "--use-existing", "--no-start", "--no-enable"], replies=replies)
        assert self.prop("sparehash.service", "ActiveState") == "inactive" and empty()
        assert self.run(["systemctl", "is-enabled", "sparehash.service"], expected=1).strip() == "disabled"
        assert hashlib.sha256(benchmark.read_bytes()).hexdigest() == before[str(benchmark)]
        self.run([*command, "apply"])  # Completed gate cannot operate on the new installation.
        self.check("explicit_reinstall_no_start_no_enable_benchmark_retained")
        self.run([*command, "remove-staging"])
        assert not stage.parent.exists() and not journal.exists()
        self.check("verified_staging_disposal_no_alternate_management_interface")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--archive-sha256", required=True)
    args = parser.parse_args()
    guard(args.source_sha256)
    run = RecoveryAcceptance(Path("/var/tmp/recovery-evidence"), args.source_sha256)
    try:
        run.exercise(args.commit, args.archive_sha256)
        run.result["complete"] = True
    except BaseException as error:
        run.result["failure"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        audit = subprocess.run(["journalctl", "-b", "_TRANSPORT=audit", "--no-pager"], capture_output=True, text=True, timeout=30)
        (run.evidence / "audit.log").write_text(audit.stdout)
        denied = [line for line in audit.stdout.splitlines() if "avc:" in line.lower() and "denied" in line.lower()]
        run.result["audit_denials"] = denied
        if denied:
            run.result["complete"] = False
        if Path("/sys/fs/selinux/enforce").exists():
            run.result["selinux"] = Path("/sys/fs/selinux/enforce").read_text().strip()
            assert run.result["selinux"] == "1"
        run.save()


if __name__ == "__main__":
    main()
