"""Lease fault injection uses only private fake sysfs/proc/state/cgroup roots."""

import json
import os
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import final
from unittest.mock import patch

from sparehash import hugepages as hp


@final
class HugepageTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.roots = hp.Roots(*(self.base / name for name in ("sys", "state", "proc", "cgroup")))
        for directory in (self.roots.state, self.roots.cgroup):
            assert directory is not None
            directory.mkdir(mode=0o750)
            directory.chmod(0o750)
        self.page = self.roots.sys / hp.PAGE_PATH
        self.page.parent.mkdir(parents=True)
        self.large = self.page.parent.parent / "hugepages-1048576kB/nr_hugepages"
        self.large.parent.mkdir()
        _ = self.large.write_text("9\n", encoding="ascii")
        _ = self.page.write_text("17\n", encoding="ascii")
        self.boot = "11111111-1111-1111-1111-111111111111"
        boot_path = self.roots.proc / "sys/kernel/random/boot_id"
        boot_path.parent.mkdir(parents=True)
        _ = boot_path.write_text(self.boot + "\n", encoding="ascii")
        assert self.roots.cgroup is not None
        self.events = self.roots.cgroup / "cgroup.events"
        self.procs = self.roots.cgroup / "cgroup.procs"
        _ = self.events.write_text("populated 0\nfrozen 0\n", encoding="ascii")
        _ = self.procs.write_text("", encoding="ascii")
        self.chowns: list[tuple[int, int]] = []
        self.owner = hp.Ownership(os.getuid(), os.getgid(), self.chown)
        self.manager = hp.LeaseManager(self.roots, None, self.owner)

    def chown(self, fd: int, uid: int, gid: int) -> None:
        self.assertEqual(os.fstat(fd).st_uid, os.getuid())
        self.chowns.append((uid, gid))

    def ledger(self, applied: int | None = 1280) -> str:
        return json.dumps(asdict(hp.Ledger(self.boot, 17, 1280, str(self.page), applied)), sort_keys=True) + "\n"

    def assert_files(self, pages: str, ledger: str | None) -> None:
        self.assertEqual(self.page.read_text(encoding="ascii"), pages)
        self.assertEqual(self.large.read_text(encoding="ascii"), "9\n")
        if ledger is None:
            self.assertFalse(self.manager.ledger_path.exists())
        else:
            self.assertEqual(self.manager.ledger_path.read_text(encoding="ascii"), ledger)
        self.assertEqual(tuple(self.roots.state.glob("tmp*")), ())

    def reserve(self) -> hp.Result:
        return self.manager.reserve_after_authorization(True, (0,), authorized=True)

    def test_reserves_when_authorized_and_ledger_precedes_kernel_write(self) -> None:
        # Given
        def observe(path: Path, count: int) -> None:
            self.assert_files("17\n", self.ledger(None))
            _ = path.write_text(f"{count}\n", encoding="ascii")
        # When
        with patch.object(hp, "_write", observe):
            result = self.reserve()
        # Then
        self.assertEqual(result, hp.Result("reserved", 1280))
        self.assert_files("1280\n", self.ledger())
        self.assertEqual(self.manager.ledger_path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.chowns, [(os.getuid(), os.getgid())] * 2)

    def test_restores_when_empty_and_ownership_matches(self) -> None:
        # Given
        _ = self.reserve()
        # When
        result = self.manager.restore_after_cgroup_empty()
        # Then
        self.assertEqual(result, hp.Result("restored", 17))
        self.assert_files("17\n", None)

    def test_preserves_baseline_when_larger_than_target(self) -> None:
        # Given
        _ = self.page.write_text("2000\n", encoding="ascii")
        # When
        result = self.reserve()
        # Then
        self.assertEqual(result, hp.Result("satisfied", 2000))
        self.assert_files("2000\n", None)
        self.assertEqual(self.chowns, [])

    def test_reports_partial_when_kernel_allocates_less(self) -> None:
        # Given
        def partial(path: Path, count: int) -> None:
            self.assertEqual(count, 1280)
            _ = path.write_text("500\n", encoding="ascii")
        # When
        with patch.object(hp, "_write", partial), self.assertLogs(hp.LOGGER, "WARNING"):
            result = self.reserve()
        # Then
        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.actual, 500)
        self.assertTrue(result.warning)
        self.assert_files("500\n", self.ledger(500))

    def test_skips_when_disabled_or_unauthorized(self) -> None:
        for enabled, authorized in ((False, True), (True, False)):
            # Given / When
            result = self.manager.reserve_after_authorization(enabled, (0,), authorized=authorized)
            # Then
            self.assertEqual(result.status, "skipped")
            self.assert_files("17\n", None)
            self.assertEqual(tuple(self.roots.state.iterdir()), ())

    def test_degrades_when_topology_or_target_invalid(self) -> None:
        for nodes, target in ((None, 1280), ((), 1280), ((0, 1), 1280), ((-1,), 1280), ((0,), 0)):
            # Given / When
            with self.assertLogs(hp.LOGGER, "WARNING"):
                result = self.manager.reserve_after_authorization(True, nodes, target, authorized=True)
            # Then
            self.assertEqual(result.status, "degraded")
            self.assertTrue(result.warning)
            self.assert_files("17\n", None)
            self.assertEqual(tuple(self.roots.state.iterdir()), ())

    def test_retains_ledger_when_external_count_changes(self) -> None:
        # Given
        _ = self.reserve()
        _ = self.page.write_text("1300\n", encoding="ascii")
        # When
        with self.assertLogs(hp.LOGGER, "WARNING"):
            result = self.manager.cleanup()
        # Then
        self.assertEqual(result.status, "degraded")
        self.assert_files("1300\n", self.ledger())

    def test_retains_ledger_when_boot_changes(self) -> None:
        # Given
        _ = self.reserve()
        manager = replace(self.manager, boot_id="22222222-2222-2222-2222-222222222222")
        # When
        with self.assertLogs(hp.LOGGER, "WARNING"):
            result = manager.cleanup()
        # Then
        self.assertEqual(result.status, "degraded")
        self.assert_files("1280\n", self.ledger())

    def test_refuses_new_lease_when_crash_left_ambiguous_ownership(self) -> None:
        for current in ("17\n", "1280\n"):
            # Given: death before or after mutation, before applied persistence.
            _ = self.manager.ledger_path.write_text(self.ledger(None), encoding="ascii")
            _ = self.page.write_text(current, encoding="ascii")
            # When
            with self.assertLogs(hp.LOGGER, "WARNING"):
                result = self.reserve()
            # Then
            self.assertEqual(result.status, "degraded")
            self.assert_files(current, self.ledger(None))

    def test_retains_pending_ledger_when_sysfs_write_fails(self) -> None:
        # Given / When
        with patch.object(hp, "_write", side_effect=OSError("fixture write failed")), self.assertLogs(hp.LOGGER, "WARNING"):
            result = self.reserve()
        # Then
        self.assertEqual(result.status, "degraded")
        self.assert_files("17\n", self.ledger(None))

    def test_retains_pending_ledger_when_applied_persistence_fails(self) -> None:
        # Given
        rename = os.replace
        def fail_applied(source: str, destination: Path) -> None:
            if len(self.chowns) == 2:
                raise OSError("fixture persistence failure")
            rename(source, destination)
        # When
        with patch("sparehash.hugepages.os.replace", fail_applied), self.assertLogs(hp.LOGGER, "WARNING"):
            result = self.reserve()
        # Then
        self.assertEqual(result.status, "degraded")
        self.assert_files("1280\n", self.ledger(None))

    def test_retains_malformed_or_wrong_path_ledger_when_cleanup_runs(self) -> None:
        for text in ("{", "{}", self.ledger().replace('"baseline": 17', '"baseline": true'),
                     self.ledger().replace(str(self.page), "/foreign"),
                     self.ledger().replace('"applied": 1280', '"applied": 9999'),
                     self.ledger().replace('"baseline": 17', '"baseline": 17, "baseline": 18')):
            # Given
            _ = self.manager.ledger_path.write_text(text, encoding="ascii")
            # When
            with self.assertLogs(hp.LOGGER, "WARNING"):
                result = self.manager.cleanup()
            # Then
            self.assertEqual(result.status, "degraded")
            self.assert_files("17\n", text)

    def test_retains_when_cgroup_populated_missing_or_unknown(self) -> None:
        # Given
        _ = self.reserve()
        for events, procs in (("populated 1\n", ""), ("populated 0\n", "42\n"),
                              ("", ""), ("populated 0\npopulated 0\n", "")):
            _ = self.events.write_text(events, encoding="ascii")
            _ = self.procs.write_text(procs, encoding="ascii")
            # When
            with self.assertLogs(hp.LOGGER, "WARNING"):
                result = self.manager.cleanup()
            # Then
            self.assertEqual(result.status, "degraded")
            self.assert_files("1280\n", self.ledger())
            self.assertEqual(self.events.read_text(encoding="ascii"), events)
            self.assertEqual(self.procs.read_text(encoding="ascii"), procs)
        self.events.unlink()
        for manager in (self.manager, replace(self.manager, roots=replace(self.roots, cgroup=None))):
            with self.assertLogs(hp.LOGGER, "WARNING"):
                self.assertEqual(manager.cleanup().status, "degraded")
            self.assert_files("1280\n", self.ledger())

    def test_cleanup_is_idempotent_when_already_restored(self) -> None:
        # Given
        _ = self.reserve()
        _ = self.manager.cleanup()
        # When
        result = self.manager.cleanup()
        # Then
        self.assertEqual(result, hp.Result("idle"))
        self.assert_files("17\n", None)

    def test_recovers_old_lease_when_next_authorized_start_has_empty_proof(self) -> None:
        # Given
        _ = self.reserve()
        # When
        result = self.reserve()
        # Then
        self.assertEqual(result, hp.Result("reserved", 1280))
        self.assert_files("1280\n", self.ledger())

    def test_keeps_lease_when_kernel_does_not_restore_baseline(self) -> None:
        # Given
        _ = self.reserve()
        # When
        with patch.object(hp, "_write", return_value=None), self.assertLogs(hp.LOGGER, "WARNING"):
            result = self.manager.cleanup()
        # Then
        self.assertEqual(result.status, "degraded")
        self.assert_files("1280\n", self.ledger())

    def test_refuses_new_lease_when_prior_cgroup_is_populated(self) -> None:
        # Given
        _ = self.reserve()
        _ = self.events.write_text("populated 1\n", encoding="ascii")
        # When
        with self.assertLogs(hp.LOGGER, "WARNING"):
            result = self.reserve()
        # Then
        self.assertEqual(result.status, "degraded")
        self.assert_files("1280\n", self.ledger())

    def test_retains_when_cgroup_evidence_is_symlinked(self) -> None:
        # Given
        _ = self.reserve()
        foreign = self.base / "foreign.events"
        _ = foreign.write_text("populated 0\n", encoding="ascii")
        self.events.unlink()
        self.events.symlink_to(foreign)
        # When
        with self.assertLogs(hp.LOGGER, "WARNING"):
            result = self.manager.cleanup()
        # Then
        self.assertEqual(result.status, "degraded")
        self.assert_files("1280\n", self.ledger())
        self.assertEqual(foreign.read_text(encoding="ascii"), "populated 0\n")

    def test_skips_mutation_when_boot_or_kernel_count_is_unknown(self) -> None:
        for boot, count in (("unknown", "17\n"), (self.boot, "17x\n")):
            # Given
            manager = replace(self.manager, boot_id=boot)
            _ = self.page.write_text(count, encoding="ascii")
            # When
            with self.assertLogs(hp.LOGGER, "WARNING"):
                result = manager.reserve_after_authorization(True, (0,), authorized=True)
            # Then
            self.assertEqual(result.status, "degraded")
            self.assert_files(count, None)

    def test_skips_mutation_when_state_protection_fails(self) -> None:
        # Given
        self.roots.state.chmod(0o777)
        # When
        with self.assertLogs(hp.LOGGER, "WARNING"):
            result = self.reserve()
        # Then
        self.assertEqual(result.status, "degraded")
        self.assert_files("17\n", None)

    def test_retains_ledger_when_restore_write_fails(self) -> None:
        # Given
        _ = self.reserve()
        # When
        with patch.object(hp, "_write", side_effect=OSError("fixture restore failure")), self.assertLogs(hp.LOGGER, "WARNING"):
            result = self.manager.cleanup()
        # Then
        self.assertEqual(result.status, "degraded")
        self.assert_files("1280\n", self.ledger())
