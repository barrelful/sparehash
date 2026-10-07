"""Strict Option A boundary tests and real fixture-tree publication coverage."""

from __future__ import annotations

import json
import os
import unittest
from dataclasses import replace
from typing import Callable, Final, final
from unittest import mock

from sparehash import xmrig_install as install
from sparehash.xmrig_ledger import Action, Artifact, GroupIdentity, Installer, Json, Membership, UserIdentity, items, mapping
from sparehash.xmrig_manifest import Manifest, ManifestError, VersionRecord, format_manifest, parse_manifest
from tests.xmrig_layout_fixtures import DATE, FakeGate, Layout, canonical_manifest, release_members, request_for, source_for, write_tar

VERSION = "xmrig-6.26.0"
FILE = "/etc/sparehash/config.toml"
LOAD_JSON: Final[Callable[..., Json]] = json.loads


def document_fixture() -> dict[str, Json]:
    return mapping(LOAD_JSON(format_manifest(manifest_fixture())))


def receipt_at(document: dict[str, Json], section: str) -> dict[str, Json]:
    return mapping(items(mapping(document["installer"])[section])[0])


def installer_fixture() -> Installer:
    return Installer(1, "installing", (UserIdentity("user", "xmrig", 996, 996, False),
                                     GroupIdentity("group", "sparehash", 983, True)),
                     (Membership("osint", 1000, "sparehash", 983, True),),
                     (Artifact(FILE, FILE, "file", True, 0, 0, 0o644, 1, 20, "a" * 64),),
                     (Action(1, "b" * 32, DATE, "write-file", FILE, "pending"),))


def manifest_fixture() -> Manifest:
    return Manifest("/opt/xmrig", (VersionRecord(VERSION, DATE, source_for(VERSION)),), installer_fixture())


@final
class ManifestSchemaTests(unittest.TestCase):
    def test_canonical_manifest_parses_and_drift_is_rejected(self) -> None:
        # Given
        valid = canonical_manifest([VERSION])
        # When
        parsed = parse_manifest(valid)
        # Then
        assert parsed is not None
        self.assertEqual(parsed.versions, (VersionRecord(VERSION, DATE, source_for(VERSION)),))
        self.assertEqual(parse_manifest(format_manifest(manifest_fixture()).decode()), manifest_fixture())
        drift = [valid.replace("/opt/xmrig", "/opt/other", 1),
                 json.dumps({"root": "/opt/xmrig", "versions": [], "extra": 1}),
                 '{"root":"/opt/xmrig","root":"/opt/xmrig","versions":[]}',
                 canonical_manifest([VERSION, VERSION]), canonical_manifest(["xmrig-6.26"]),
                 json.dumps({"root": "/opt/xmrig", "versions": {}}), "{"]
        for contents in drift:
            with self.subTest(contents=contents):
                self.assertIsNone(parse_manifest(contents))

    def test_extended_receipts_when_document_is_valid(self) -> None:
        # Given
        original = manifest_fixture()
        # When
        parsed = parse_manifest(format_manifest(original).decode())
        # Then
        self.assertEqual(parsed, original)

    def test_parse_manifest_rejects_arbitrary_top_level_keys_even_with_installer(self) -> None:
        for key in ("token", "api_token", "artifacts", "artifacts_list", "metadata"):
            with self.subTest(key=key):
                # Given
                document = document_fixture()
                document[key] = []
                # When
                parsed = parse_manifest(json.dumps(document))
                # Then
                self.assertIsNone(parsed)

    def test_rejection_when_nested_keys_are_unknown_missing_or_duplicate(self) -> None:
        for section in ("identities", "memberships", "artifacts", "actions"):
            # Given
            document = document_fixture()
            receipt = receipt_at(document, section)
            first = next(iter(receipt))
            fragment = json.dumps(first) + ": " + json.dumps(receipt[first])
            duplicate = json.dumps(document).replace(fragment, fragment + ", " + fragment, 1)
            receipt["unexpected"] = 1
            unknown = json.dumps(document)
            del receipt["unexpected"]
            del receipt[first]
            missing = json.dumps(document)
            for contents in (unknown, missing, duplicate):
                with self.subTest(section=section, contents=contents):
                    # When
                    parsed = parse_manifest(contents)
                    # Then
                    self.assertIsNone(parsed)

    def test_rejection_when_integer_boolean_mode_hash_or_enum_is_invalid(self) -> None:
        cases = (("identities", "uid", True), ("memberships", "gid", False),
                 ("artifacts", "mode", 0o1000), ("artifacts", "device", -1),
                 ("artifacts", "inode", 1.5), ("artifacts", "created", 1),
                 ("artifacts", "sha256", "A" * 64), ("artifacts", "sha256", None),
                 ("actions", "sequence", True), ("actions", "sequence", 0),
                 ("actions", "install_id", "B" * 32), ("actions", "state", "done"),
                 ("actions", "at", "2026-02-30T00:00:00Z"), ("actions", "operation", "exec"))
        for section, field, value in cases:
            with self.subTest(section=section, field=field, value=value):
                # Given
                document = document_fixture()
                receipt_at(document, section)[field] = value
                # When
                parsed = parse_manifest(json.dumps(document))
                # Then
                self.assertIsNone(parsed)

    def test_rejection_when_receipt_keys_or_paths_are_duplicated(self) -> None:
        for section in ("identities", "memberships", "artifacts"):
            with self.subTest(section=section):
                # Given
                document = document_fixture()
                values = items(mapping(document["installer"])[section])
                values.append(values[0])
                # When
                parsed = parse_manifest(json.dumps(document))
                # Then
                self.assertIsNone(parsed)

    def test_rejection_when_action_reference_sequence_or_ready_phase_is_invalid(self) -> None:
        for mutation in ("target", "type", "sequence", "ready"):
            with self.subTest(mutation=mutation):
                # Given
                document = document_fixture()
                ledger = mapping(document["installer"])
                if mutation == "target":
                    receipt_at(document, "actions")["target"] = "/etc/shadow"
                if mutation == "type":
                    receipt_at(document, "actions")["operation"] = "create-directory"
                if mutation == "sequence":
                    items(ledger["actions"]).append(receipt_at(document, "actions"))
                if mutation == "ready":
                    ledger["phase"] = "ready"
                # When
                parsed = parse_manifest(json.dumps(document))
                # Then
                self.assertIsNone(parsed)

    def test_rejection_when_shapes_dates_or_size_drift(self) -> None:
        for mutation in ("versions", "null", "schema", "date", "size"):
            with self.subTest(mutation=mutation):
                # Given
                document = document_fixture()
                if mutation == "versions":
                    del document["versions"]
                if mutation == "null":
                    document["installer"] = None
                if mutation == "schema":
                    mapping(document["installer"])["schema"] = True
                if mutation == "date":
                    mapping(items(document["versions"])[0])["installed_at"] = "2026-13-01T00:00:00Z"
                contents = json.dumps(document)
                if mutation == "size":
                    contents += " " * (64 * 1024)
                # When
                parsed = parse_manifest(contents)
                # Then
                self.assertIsNone(parsed)

    def test_publication_refused_when_required_ledger_exceeds_cap(self) -> None:
        # Given
        ledger = installer_fixture()
        actions = tuple(replace(ledger.actions[0], sequence=index) for index in range(1, 1000))
        manifest = replace(manifest_fixture(), installer=replace(ledger, actions=actions))
        # When / Then
        with self.assertRaises(ManifestError):
            _ = format_manifest(manifest)

    def test_pending_creation_when_inode_receipt_does_not_exist_yet(self) -> None:
        # Given
        document = document_fixture()
        mapping(document["installer"])["artifacts"] = []
        # When
        parsed = parse_manifest(json.dumps(document))
        # Then
        self.assertIsNotNone(parsed)

    def test_applied_creation_rejected_when_inode_receipt_is_missing(self) -> None:
        # Given
        document = document_fixture()
        mapping(document["installer"])["artifacts"] = []
        receipt_at(document, "actions")["state"] = "applied"
        # When
        parsed = parse_manifest(json.dumps(document))
        # Then
        self.assertIsNone(parsed)

    def test_legacy_shape_when_extension_is_absent(self) -> None:
        # Given
        manifest = replace(manifest_fixture(), installer=None)
        # When
        contents = format_manifest(manifest)
        # Then
        self.assertEqual(set(mapping(LOAD_JSON(contents))), {"root", "versions"})
        self.assertEqual(parse_manifest(contents.decode()), manifest)


@final
class ManifestMutationTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.layout = Layout()
        self.addCleanup(self.layout.cleanup)
        self.layout.build([VERSION], current=VERSION)
        self.prior = format_manifest(manifest_fixture())
        _ = self.layout.manifest.write_bytes(self.prior)

    def test_extension_preserved_when_updating_and_rolling_back(self) -> None:
        # Given
        target = "xmrig-6.26.1"
        archive = write_tar(self.layout.root / "release.tar.gz", release_members(target))
        context = install.InstallContext(trust=self.layout.trust, gate=FakeGate())
        with mock.patch.object(install, "verify_release"):
            _ = install.update_flow(request_for(archive, (6, 26, 1)), context)
        # When
        result = install.rollback(self.layout.trust, gate=FakeGate())
        # Then
        parsed = parse_manifest(self.layout.manifest.read_text())
        self.assertEqual(result.manifest.installer, installer_fixture())
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.installer, installer_fixture())
        self.assertEqual(self.layout.manifest.stat().st_mode & 0o777, 0o640)

    def test_prior_document_restored_when_current_publication_fails(self) -> None:
        # Given
        target = "xmrig-6.26.1"
        archive = write_tar(self.layout.root / "release.tar.gz", release_members(target))
        context = install.InstallContext(trust=self.layout.trust, gate=FakeGate())
        rename = os.rename

        def reject_link(src: str, dst: str, *, src_dir_fd: int, dst_dir_fd: int) -> None:
            if dst == "current":
                raise OSError("fixture link failure")
            rename(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

        # When / Then
        with mock.patch.object(install, "verify_release"), mock.patch("sparehash.xmrig_layout.os.rename", side_effect=reject_link):
            with self.assertRaises(install.InstallError):
                _ = install.update_flow(request_for(archive, (6, 26, 1)), context)
        self.assertEqual(self.layout.manifest.read_bytes(), self.prior)

    def test_extension_cannot_authorize_unrecorded_current_or_missing_marker(self) -> None:
        for mutation in ("unrecorded", "marker"):
            with self.subTest(mutation=mutation):
                # Given
                if mutation == "unrecorded":
                    _ = self.layout.manifest.write_bytes(format_manifest(replace(manifest_fixture(), versions=())))
                else:
                    self.layout.marker.unlink()
                # When
                binary = install.current_binary(self.layout.trust)
                # Then
                self.assertIsNone(binary)
