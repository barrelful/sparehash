"""Explicit compatibility consent never overrides an independent refusal."""

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from sparehash import install
from sparehash.config import Config
from sparehash.preflight import HostFacts, evaluate
from tests.install_fixture import capabilities
from tests.test_preflight_cli import _cfg, _patches


@pytest.mark.parametrize("acknowledged", [False, True])
def test_mint_requires_explicit_acknowledgment(acknowledged: bool) -> None:
    report = evaluate(HostFacts("linuxmint", "22.3"), capabilities(), Config(), False,
                      unverified_acknowledged=acknowledged)
    assert report.support == "UNVERIFIED"
    assert report.readiness == ("READY" if acknowledged else "ACTION_REQUIRED")
    assert ("UnverifiedCompatibilityAcknowledgmentRequired" in report.reason_codes) != acknowledged


@pytest.mark.parametrize("facts,expected", [
    (HostFacts("nixos", "25", nixos=True), "REFUSED_UNSUPPORTED"),
    (HostFacts("debian", "13", proxmox_evidence=True), "REFUSED_UNSUPPORTED"),
    (HostFacts("linuxmint", "22.3", noexec_paths=("/opt",)), "REFUSED_UNSAFE"),
    (HostFacts("linuxmint", "22.3", security_policy_unsafe=True), "REFUSED_UNSAFE"),
    (HostFacts("linuxmint", "22.3", cgroup_shutdown_proven=False), "REFUSED_UNSAFE"),
    (HostFacts("linuxmint", "22.3", os_release_readable=False), "INCONCLUSIVE"),
    (HostFacts("linuxmint", "22.3", foreign_host=True), "INCONCLUSIVE"),
    (HostFacts("fedora", "44", atomic=True, dnf_available=False), "REFUSED_UNSAFE"),
    (None, "INCONCLUSIVE"),
])
def test_acknowledgment_does_not_override_refusals(facts: HostFacts | None, expected: str) -> None:
    assert evaluate(facts, capabilities(), Config(), False,
                    unverified_acknowledged=True).readiness == expected


def test_acknowledgment_does_not_supply_missing_systemd_evidence() -> None:
    caps = replace(capabilities(), systemd_version=None)
    assert evaluate(HostFacts("linuxmint", "22.3"), caps, Config(), False,
                    unverified_acknowledged=True).readiness == "INCONCLUSIVE"


@pytest.mark.parametrize("acknowledged", [False, True])
def test_cli_preflight_remains_read_only_and_unverified(acknowledged: bool, capsys) -> None:
    argv = ["--preflight", "--json"]
    if acknowledged:
        argv.append("--acknowledge-unverified")
    with _patches(HostFacts("linuxmint", "22.3"), _cfg()):
        assert install.main(argv, geteuid=lambda: 0) == (0 if acknowledged else 2)
        install.Tree.assert_not_called()
        install.is_retired.assert_not_called()
        install.execute.assert_not_called()
        install.wizard.run_interactive.assert_not_called()
    report = json.loads(capsys.readouterr().out)
    assert report["support"] == "UNVERIFIED"
    assert report["readiness"] == ("READY" if acknowledged else "ACTION_REQUIRED")


@pytest.mark.parametrize("acknowledged", [False, True])
@pytest.mark.parametrize("unattended", [False, True])
def test_install_requires_both_compatibility_and_installation_consent(
    acknowledged: bool, unattended: bool, tmp_path: Path,
) -> None:
    argv = ["--no-start", "--no-enable"]
    if acknowledged:
        argv.append("--acknowledge-unverified")
    if unattended:
        argv += ["--answers", str(tmp_path / "answers.toml"), "--yes"]
    with _patches(HostFacts("linuxmint", "22.3"), _cfg()), patch("builtins.input", return_value=""):
        assert install.main(argv, geteuid=lambda: 0) == (0 if acknowledged else 2)
        if acknowledged:
            assert install.execute.call_args.kwargs["boot"] is False
            assert install.execute.call_args.kwargs["now"] is False
        else:
            install.execute.assert_not_called()


def test_acknowledgment_does_not_supply_unattended_effect_consent() -> None:
    with patch.object(install.Capabilities, "detect") as detect, patch.object(install, "Tree") as tree:
        assert install.main(["--answers", "answers.toml", "--acknowledge-unverified"],
                            geteuid=lambda: 0) == 2
        detect.assert_not_called()
        tree.assert_not_called()


def test_declined_interactive_install_has_no_effects() -> None:
    with _patches(HostFacts("linuxmint", "22.3"), _cfg()), patch("builtins.input", return_value="n"):
        assert install.main(["--acknowledge-unverified"], geteuid=lambda: 0) == 1
        install.execute.assert_not_called()


def test_acknowledgment_does_not_bypass_existing_install_refusal() -> None:
    with _patches(HostFacts("linuxmint", "22.3"), _cfg()):
        install.Tree.return_value.read.return_value = b"existing protected CLI"
        assert install.main(["--acknowledge-unverified"], geteuid=lambda: 0) == 2
        install.Capabilities.detect.assert_not_called()
        install.is_retired.assert_not_called()
        install.execute.assert_not_called()
