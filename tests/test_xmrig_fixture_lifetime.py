"""Fixtures belong to execution, not unittest/pytest collection or case lifetime."""

import tempfile
import unittest
from unittest.mock import patch

from tests import test_xmrig_current, test_xmrig_layout, test_xmrig_manifest


def test_collection_constructs_no_temporary_filesystem():
    with patch.object(tempfile, "TemporaryDirectory", side_effect=AssertionError("collection performed I/O")):
        test_xmrig_current.XmrigCurrentTests("test_current_is_verified_when_relative_link_is_manifest_authorized")
        test_xmrig_manifest.ManifestMutationTests("test_extension_preserved_when_updating_and_rolling_back")
        test_xmrig_layout.InstallLayoutTests("test_first_install_creates_managed_tree")


def test_same_case_can_run_twice_with_fresh_fixture_and_cleanup():
    case = test_xmrig_current.XmrigCurrentTests("test_current_is_verified_when_relative_link_is_manifest_authorized")
    roots = []
    for _ in range(2):
        result = unittest.TestResult()
        case.run(result)
        assert result.wasSuccessful(), result.errors + result.failures
        roots.append(case.root)
        assert not case.root.exists()
    assert roots[0] != roots[1]
