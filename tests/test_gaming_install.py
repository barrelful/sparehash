import configparser
import os
import tempfile
import unittest
from pathlib import Path
from typing import final
from unittest.mock import patch

from sparehash import gaming_ini as ini
from tests.gaming_install_consent import ConsentTests as ConsentTests


@final
class IniTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.directory = self.home / ".config"
        self.directory.mkdir(parents=True)
        self.file = self.directory / ini.NAME
        self.target = ini.Home(self.home, os.getuid(), os.getgid())
        self.original = b"# keep\r\n[general]\r\nrenice=5\r\n[custom]\r\nstart=notify-send X\r\n    echo X=1\r\nend=echo done\r\nscript_timeout=12\r\n[other]\r\nx=y\r\n"

    def test_merge_when_file_absent_creates_user_owned_hooks(self) -> None:
        # Given / When
        changed = ini.edit(self.target, True)
        # Then
        parser = configparser.RawConfigParser()
        _ = parser.read(self.file)
        self.assertTrue(changed)
        for key, hook in ini.HOOKS.items():
            self.assertEqual(parser["custom"][key].strip(), hook)
        info = self.file.stat()
        self.assertEqual((info.st_mode & 0o777, info.st_uid, info.st_gid), (0o644, os.getuid(), os.getgid()))
        self.assertFalse((self.directory / ini.BACKUP).exists())

    def test_merge_when_custom_commands_exist_preserves_text_and_metadata(self) -> None:
        # Given
        _ = self.file.write_bytes(self.original)
        self.file.chmod(0o640)
        before = self.file.stat()
        # When
        _ = ini.edit(self.target, True)
        # Then
        actual = self.file.read_bytes()
        for hook in ini.HOOKS.values():
            actual = actual.replace(b"    " + hook.encode() + b"\r\n", b"")
        self.assertEqual(actual, self.original)
        after = self.file.stat()
        self.assertEqual((after.st_mode, after.st_uid, after.st_gid), (before.st_mode, before.st_uid, before.st_gid))
        self.assertEqual((self.directory / ini.BACKUP).read_bytes(), self.original)

    def test_merge_when_repeated_is_noop_and_keeps_backup(self) -> None:
        # Given
        _ = self.file.write_bytes(self.original)
        _ = ini.edit(self.target, True)
        before = self.file.stat()
        backup = (self.directory / ini.BACKUP).stat()
        # When
        changed = ini.edit(self.target, True)
        # Then
        self.assertFalse(changed)
        self.assertEqual(self.file.stat(), before)
        self.assertEqual((self.directory / ini.BACKUP).stat(), backup)

    def test_merge_when_backup_exists_never_overwrites_it(self) -> None:
        # Given
        _ = self.file.write_bytes(self.original)
        _ = (self.directory / ini.BACKUP).write_bytes(b"first backup")
        # When
        _ = ini.edit(self.target, True)
        # Then
        self.assertEqual((self.directory / ini.BACKUP).read_bytes(), b"first backup")

    def test_unmerge_when_existing_file_preserves_all_foreign_commands(self) -> None:
        # Given
        _ = self.file.write_bytes(self.original)
        _ = ini.edit(self.target, True)
        # When
        _ = ini.edit(self.target, False)
        # Then
        self.assertEqual(self.file.read_bytes(), self.original)

    def test_unmerge_when_created_file_is_untouched_removes_owned_leftovers(self) -> None:
        # Given
        _ = ini.edit(self.target, True)
        # When
        _ = ini.edit(self.target, False)
        # Then
        self.assertFalse(self.file.exists())

    def test_unmerge_when_foreign_file_contains_similar_commands_removes_only_exact_hooks(self) -> None:
        # Given
        data = ("[custom]\nstart=" + ini.HOOKS["start"] + "\n    echo keep\nend=echo end\n" +
                "    " + ini.HOOKS["end"] + "\n    " + ini.HOOKS["end"] + " extra\n[other]\nstart=" + ini.HOOKS["start"] + "\n").encode()
        _ = self.file.write_bytes(data)
        # When
        _ = ini.edit(self.target, False)
        # Then
        expected = data.replace(("start=" + ini.HOOKS["start"]).encode(), b"start=", 1)
        expected = expected.replace(("    " + ini.HOOKS["end"] + "\n").encode(), b"", 1)
        self.assertEqual(self.file.read_bytes(), expected)

    def test_merge_when_hooks_duplicated_normalizes_to_one_per_key(self) -> None:
        # Given
        _ = self.file.write_text("[custom]\nstart=" + ini.HOOKS["start"] + "\n    " + ini.HOOKS["start"] + "\nend=echo end\n")
        # When
        _ = ini.edit(self.target, True)
        # Then
        parser = configparser.RawConfigParser()
        _ = parser.read(self.file)
        self.assertEqual(parser["custom"]["start"].split().count("gaming-start'"), 1)
        self.assertEqual(parser["custom"]["end"].split().count("gaming-end'"), 1)

    def test_edit_when_unsafe_leaf_or_backup_rejects_without_changes(self) -> None:
        # Given / When / Then
        sentinel = self.root / "sentinel"
        _ = sentinel.write_bytes(b"unrelated")
        for enabled in (True, False):
            self.file.symlink_to(sentinel)
            with self.assertRaises(ini.UnsafePathError):
                _ = ini.edit(self.target, enabled)
            self.file.unlink()
        _ = self.file.write_bytes(self.original)
        (self.directory / ini.BACKUP).symlink_to(sentinel)
        with self.assertRaises(ini.UnsafePathError):
            _ = ini.edit(self.target, True)
        self.assertEqual(sentinel.read_bytes(), b"unrelated")
        self.assertEqual(self.file.read_bytes(), self.original)

    def test_edit_when_parent_symlink_rejects_both_operations(self) -> None:
        # Given
        self.directory.rmdir()
        foreign = self.root / "etc"
        foreign.mkdir()
        sentinel = foreign / ini.NAME
        _ = sentinel.write_bytes(b"root sentinel")
        self.directory.symlink_to(foreign)
        # When / Then
        for enabled in (True, False):
            with self.assertRaises(ini.UnsafePathError):
                _ = ini.edit(self.target, enabled)
            self.assertEqual(sentinel.read_bytes(), b"root sentinel")

    def test_edit_when_parent_replacement_observed_aborts_both_operations(self) -> None:
        # Given / When / Then
        check = ini.check_identity
        for enabled in (True, False):
            with tempfile.TemporaryDirectory(dir=self.root) as private:
                home = Path(private)
                directory = home / ".config"
                directory.mkdir()
                original = ini.transform(self.original, True)
                starting = self.original if enabled else original
                _ = (directory / ini.NAME).write_bytes(starting)
                calls = 0
                def swap(chain: list[tuple[int, str, int]], prior: tuple[bytes, os.stat_result] | None) -> None:
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        _ = directory.rename(home / "moved")
                        directory.mkdir()
                        _ = (directory / ini.NAME).write_bytes(b"replacement sentinel")
                    check(chain, prior)
                with patch.object(ini, "check_identity", side_effect=swap), self.assertRaises(ini.UnsafePathError):
                    _ = ini.edit(ini.Home(home, os.getuid(), os.getgid()), enabled)
                self.assertEqual((directory / ini.NAME).read_bytes(), b"replacement sentinel")
                self.assertEqual((home / "moved" / ini.NAME).read_bytes(), starting)
                self.assertFalse(any(p.name.startswith(".gamemode.ini.sparehash-") for p in (home / "moved").iterdir()))

    def test_edit_when_parent_swap_after_final_check_writes_only_pinned_inode(self) -> None:
        # Given / When / Then
        check = ini.check_identity
        for enabled in (True, False):
            with tempfile.TemporaryDirectory(dir=self.root) as private:
                home = Path(private)
                directory = home / ".config"
                directory.mkdir()
                original = ini.transform(self.original, True)
                _ = (directory / ini.NAME).write_bytes(original if not enabled else self.original)
                sentinel = home / "etc-sentinel"
                _ = sentinel.write_bytes(b"root sentinel")
                calls = 0
                def swap(chain: list[tuple[int, str, int]], prior: tuple[bytes, os.stat_result] | None) -> None:
                    nonlocal calls
                    check(chain, prior)
                    calls += 1
                    if calls == 2:
                        _ = directory.rename(home / "moved")
                        directory.mkdir()
                        _ = (directory / ini.NAME).write_bytes(b"replacement sentinel")
                with patch.object(ini, "check_identity", side_effect=swap):
                    _ = ini.edit(ini.Home(home, os.getuid(), os.getgid()), enabled)
                self.assertEqual((directory / ini.NAME).read_bytes(), b"replacement sentinel")
                self.assertEqual(sentinel.read_bytes(), b"root sentinel")
                self.assertEqual((home / "moved" / ini.NAME).read_bytes(), original if enabled else self.original)

    def test_edit_when_malformed_special_or_invalid_paths_rejects(self) -> None:
        # Given / When / Then
        for path in (Path("relative"), Path("/"), self.home / ".." / "escape"):
            with self.assertRaises(ini.UnsafePathError):
                _ = ini.Home(path, os.getuid(), os.getgid())
        for data in (b"malformed", b"[custom]\nstart=x\nstart=y\n", b"\xff"):
            _ = self.file.write_bytes(data)
            with self.assertRaises(ini.UnsafePathError):
                _ = ini.edit(self.target, True)
            self.assertEqual(self.file.read_bytes(), data)
        self.file.unlink()
        os.mkfifo(self.file)
        with self.assertRaises(ini.UnsafePathError):
            _ = ini.edit(self.target, True)

    def test_edit_when_replace_fails_cleans_temp_without_rollback_clobber(self) -> None:
        # Given
        _ = self.file.write_bytes(self.original)
        with patch.object(os, "replace", side_effect=PermissionError(13, "denied")):
            # When / Then
            with self.assertRaises(ini.UnsafePathError):
                _ = ini.edit(self.target, True)
        self.assertEqual(self.file.read_bytes(), self.original)
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()), [ini.NAME, ini.BACKUP])

    def test_edit_when_symlink_observed_during_recheck_never_writes_target(self) -> None:
        # Given
        _ = self.file.write_bytes(self.original)
        foreign = self.root / "etc"
        foreign.mkdir()
        _ = (foreign / ini.NAME).write_bytes(b"root sentinel")
        check = ini.check_identity
        def swap(chain: list[tuple[int, str, int]], prior: tuple[bytes, os.stat_result] | None) -> None:
            _ = self.directory.rename(self.home / "moved")
            self.directory.symlink_to(foreign)
            check(chain, prior)
        # When / Then
        with patch.object(ini, "check_identity", side_effect=swap), self.assertRaises(ini.UnsafePathError):
            _ = ini.edit(self.target, True)
        self.assertEqual((foreign / ini.NAME).read_bytes(), b"root sentinel")
        self.assertEqual((self.home / "moved" / ini.NAME).read_bytes(), self.original)

    def test_edit_when_final_file_replaced_at_recheck_preserves_concurrent_update(self) -> None:
        # Given
        _ = self.file.write_bytes(self.original)
        check = ini.check_identity
        def swap(chain: list[tuple[int, str, int]], prior: tuple[bytes, os.stat_result] | None) -> None:
            _ = self.file.rename(self.directory / "old")
            _ = self.file.write_bytes(b"concurrent update")
            check(chain, prior)
        # When / Then
        with patch.object(ini, "check_identity", side_effect=swap), self.assertRaises(ini.UnsafePathError):
            _ = ini.edit(self.target, True)
        self.assertEqual(self.file.read_bytes(), b"concurrent update")

    def test_edit_when_metadata_write_fails_removes_partial_backup(self) -> None:
        # Given
        _ = self.file.write_bytes(self.original)
        with patch.object(os, "fchown", side_effect=PermissionError(13, "denied")):
            # When / Then
            with self.assertRaises(ini.UnsafePathError):
                _ = ini.edit(self.target, True)
        self.assertEqual(self.file.read_bytes(), self.original)
        self.assertEqual(list(self.directory.iterdir()), [self.file])
