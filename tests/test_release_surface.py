"""Deferred workflows refuse before host inspection or privileged mutations."""

import io
from contextlib import redirect_stderr
from unittest.mock import patch

import pytest

from sparehash import install
from sparehash.__main__ import main
from sparehash.config import Config
from sparehash.preflight import HostFacts, evaluate
from tests.install_fixture import capabilities


@pytest.mark.parametrize("arguments", [
    ["benchmark"], ["config", "edit"], ["install", "--reconfigure"],
])
@pytest.mark.parametrize("uid", [0, 1000])
def test_deferred_workflows_have_no_effects(arguments: list[str], uid: int) -> None:
    output = io.StringIO()
    with redirect_stderr(output), patch.object(install, "Tree") as tree, patch.object(
        install, "is_retired",
    ) as retired, patch.object(install.Capabilities, "detect") as detect, patch.object(
        install.lifecycle, "clear_lifecycle_latch",
    ) as clear:
        assert main(arguments, geteuid=lambda: uid) == 2
    for boundary in (tree, retired, detect, clear):
        boundary.assert_not_called()
    assert "unsupported" in output.getvalue()
    assert "preserve" in output.getvalue().lower()


@pytest.mark.parametrize("arguments", [
    ["complete-upgrade", "old-operation"], ["install", "--upgrade", "operation"],
    ["install", "--recover", "operation"], ["install", "--complete-upgrade", "operation"],
])
def test_obsolete_upgrade_interfaces_are_removed(arguments) -> None:
    with patch.object(install, "Tree") as tree, pytest.raises(SystemExit) as ended:
        main(arguments, geteuid=lambda: 0)
    assert ended.value.code == 2
    tree.assert_not_called()


@pytest.mark.parametrize("distro,version", [
    ("debian", "12"), ("ubuntu", "22.04"), ("linuxmint", "22"),
    ("bazzite", "43"), ("bazzite", "45"), ("ubuntu", None),
])
def test_untested_releases_are_not_validated(distro: str, version: str | None) -> None:
    report = evaluate(HostFacts(distro, version), capabilities(), Config(), True)
    assert report.support == "UNVERIFIED"
    assert report.readiness == "ACTION_REQUIRED"


@pytest.mark.parametrize("distro,version", [("ubuntu", "24.04"), ("bazzite", "44")])
def test_preflight_matches_only_tested_release_lines(distro: str, version: str) -> None:
    report = evaluate(HostFacts(distro, version), capabilities(), Config(), True)
    assert report.support == "VALIDATED"
    assert report.readiness == "READY"


@pytest.mark.parametrize("arguments", [["--help"], ["install", "--help"], ["config", "--help"]])
def test_help_labels_deferred_workflows(arguments: list[str], capsys) -> None:
    with pytest.raises(SystemExit) as ended:
        main(arguments)
    assert ended.value.code == 0
    text = capsys.readouterr().out
    assert "deferred" in text
    assert "stage and publish a selected local release" not in text


def test_unsupported_host_remediation_does_not_invent_packaging_channel() -> None:
    report = evaluate(HostFacts("nixos", "25", nixos=True), capabilities(), Config(), True)
    text = " ".join(install.remediate(report))
    assert "Native packaging is deferred" in text
    assert "or a native packaging channel" not in text


def test_default_install_cannot_bypass_deferral_or_clear_retirement() -> None:
    from unittest.mock import Mock

    tree = Mock()
    tree.read.return_value = b"protected existing CLI"
    output = io.StringIO()
    with patch.object(install, "Tree", return_value=tree), patch.object(
        install.platform, "machine", return_value="x86_64",
    ), patch.object(install.Capabilities, "detect") as detect, patch.object(
        install.lifecycle, "clear_lifecycle_latch",
    ) as clear, redirect_stderr(output):
        assert main(["install", "--clear-lifecycle-latch"], geteuid=lambda: 0) == 2
    tree.read.assert_called_once_with("/usr/local/bin/sparehash")
    detect.assert_not_called()
    clear.assert_not_called()
    assert "existing installation refused" in output.getvalue()
