# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Consented preflight CLI: report parity, unattended consent, and refusal gates."""

from __future__ import annotations

import io
import json
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import pytest

from sparehash import config, install
from sparehash.preflight import HostFacts as PreflightFacts
from sparehash.sysdetect import Capabilities
from tests.install_fixture import capabilities

WALLET = "4" + "1" * 94


def _cfg(*, gaming_enabled: bool = True) -> config.Config:
    return config.Config(
        wallet=WALLET,
        pool=config.Pool(host="127.0.0.1"),
        gaming=config.Gaming(enabled=gaming_enabled, users=("gamer",) if gaming_enabled else ()),
    )


def _debian() -> PreflightFacts:
    return PreflightFacts("ubuntu", "24.04")


def _bazzite() -> PreflightFacts:
    return PreflightFacts("bazzite", "44", atomic=True, dnf_available=True)


def _proxmox() -> PreflightFacts:
    return PreflightFacts("debian", "12", proxmox_evidence=True)


@contextmanager
def _patches(facts: PreflightFacts, cfg: config.Config):
    """Wire every host boundary so install.main stays hermetic and root-only."""
    tree = mock.Mock()
    tree.read.return_value = None
    tree.path.return_value = mock.Mock(exists=mock.Mock(return_value=False))
    with (
        mock.patch.object(install, "is_retired", return_value=False),
        mock.patch.object(install.platform, "machine", return_value="x86_64"),
        mock.patch.object(Capabilities, "detect", return_value=capabilities()),
        mock.patch.object(install, "collect_facts", return_value=facts),
        mock.patch.object(install, "Tree", return_value=tree),
        mock.patch.object(install, "preflight"),
        mock.patch.object(install.systemd, "probe_cpuweight_idle", return_value=False),
        mock.patch.object(install.wizard, "load_answers", return_value=cfg),
        mock.patch.object(install.wizard, "run_interactive", return_value=cfg),
        mock.patch.object(install, "execute"),
    ):
        yield


@pytest.mark.parametrize("enabled", (True, False))
def test_preflight_answers_are_used_for_readiness(enabled: bool, tmp_path: Path) -> None:
    """A dry preflight must report the posture the operator actually supplied."""
    # Given a Bazzite host and answers naming an explicit reduced coverage
    cfg = config.Config(
        wallet=WALLET,
        pool=config.Pool(host="127.0.0.1"),
        gaming=config.Gaming(enabled=enabled),
    )
    answers = tmp_path / "answers.toml"
    _ = answers.write_text("wallet = 'unused'\n", encoding="utf-8")
    # When preflight runs with those answers
    machine = io.StringIO()
    with _patches(_bazzite(), cfg), redirect_stdout(machine):
        code = install.main(["--preflight", "--json", "--answers", str(answers)], geteuid=lambda: 0)
    # Then the report reflects the explicit coverage instead of the default auto posture
    report = json.loads(machine.getvalue())
    assert code == 0
    assert (report["gaming"], report["readiness"]) == ("BEST_EFFORT" if enabled else "DISABLED", "READY")


@pytest.mark.parametrize("mode", ["interactive", "unattended"])
def test_accepts_interactive_and_unattended_consent(mode: str, tmp_path: Path) -> None:
    # Given a READY Debian host with verified gaming integration
    cfg = _cfg(gaming_enabled=True)
    answers = tmp_path / "answers.toml"
    _ = answers.write_text("wallet = 'unused'\n", encoding="utf-8")
    argv = ["--answers", str(answers), "--yes"] if mode == "unattended" else []
    stdin = (
        mock.patch("builtins.input", return_value="")
        if mode == "interactive"
        else mock.patch("builtins.input", side_effect=AssertionError("uncontrolled stdin"))
    )
    # When consent is granted through the appropriate channel
    with _patches(_debian(), cfg), stdin:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = install.main(argv, geteuid=lambda: 0)
    # Then the install proceeds without effects being declined
    assert code == 0, err.getvalue()
    assert "preflight:" in out.getvalue()
    assert "readiness: READY" in out.getvalue()
    assert "SELinux compatible" not in out.getvalue()

    # And the dry --preflight report agrees between human and JSON forms
    human, machine = io.StringIO(), io.StringIO()
    with _patches(_debian(), cfg):
        with redirect_stdout(human):
            assert install.main(["--preflight"], geteuid=lambda: 0) == 0
        with redirect_stdout(machine):
            assert install.main(["--preflight", "--json"], geteuid=lambda: 0) == 0
    report = json.loads(machine.getvalue())
    assert report["readiness"] == "READY"
    assert report["support"] == "VALIDATED"
    assert report["gaming"] == "BEST_EFFORT"
    for field in ("support: VALIDATED", "readiness: READY", "gaming: BEST_EFFORT"):
        assert field in human.getvalue()


def test_refuses_unsafe_or_unapproved_install(tmp_path: Path) -> None:
    answers = tmp_path / "answers.toml"
    _ = answers.write_text("wallet = 'unused'\n", encoding="utf-8")

    # Option validation refuses before any host boundary is touched.
    for argv, needle in (
        (["--answers", str(answers)], "requires explicit --yes"),
        (["--yes"], "requires an answers file"),
        (["--json"], "only meaningful with --preflight"),
        (["--preflight", "--no-start"], "cannot be combined with --preflight"),
        (["--answers", str(answers), "--yes", "--clear-lifecycle-latch"], "explicit interactive consent"),
    ):
        err = io.StringIO()
        with redirect_stderr(err):
            code = install.main(argv, geteuid=lambda: 0)
        assert code == 2, argv
        assert needle in err.getvalue(), argv

    # No stdin: interactive consent cannot be obtained.
    with _patches(_debian(), _cfg()), mock.patch("builtins.input", side_effect=EOFError):
        err = io.StringIO()
        with redirect_stderr(err):
            code = install.main([], geteuid=lambda: 0)
    assert code == 2
    assert "no input available" in err.getvalue()

    # Explicitly disabled detection is reported, not silently re-enabled.
    with _patches(_debian(), _cfg(gaming_enabled=False)):
        err = io.StringIO()
        with redirect_stderr(err):
            code = install.main(["--answers", str(answers), "--yes"], geteuid=lambda: 0)
    assert code == 0

    # Unsupported Proxmox host is refused with a REFUSED_UNSUPPORTED report.
    with _patches(_proxmox(), _cfg()):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = install.main(["--preflight"], geteuid=lambda: 0)
    assert code == 1
    assert "readiness: REFUSED_UNSUPPORTED" in out.getvalue()


def test_merge_forwards_bootstrap_preservation_and_startup_options(tmp_path: Path) -> None:
    with _patches(_debian(), _cfg()):
        assert install.main(["--answers", str(tmp_path / "answers.toml"), "--yes",
                             "--no-start", "--no-enable"],
                            geteuid=lambda: 0) == 0
        kwargs = install.execute.call_args.kwargs
        assert kwargs["source_root"] == install.launcher_source_root()
        assert kwargs["preserve"] is False
        assert kwargs["approved"] is True
        assert kwargs["boot"] is False
        assert kwargs["now"] is False


def test_merge_preflight_never_clears_retirement() -> None:
    with _patches(_debian(), _cfg()), mock.patch.object(install.lifecycle, "clear_lifecycle_latch") as clear:
        assert install.main(["--preflight", "--json"], geteuid=lambda: 0) == 0
        clear.assert_not_called()
        install.is_retired.assert_not_called()
        install.execute.assert_not_called()


def test_merge_declined_bootstrap_never_captures_preservation(tmp_path: Path) -> None:
    with (mock.patch.object(install, "preflight"),
          mock.patch.object(install, "confirm_effects", return_value=False),
          mock.patch.object(install, "capture_preservation") as capture,
          mock.patch.object(install, "Ledger") as ledger):
        with pytest.raises(install.ConsentRefused):
            install.execute(mock.Mock(), capabilities(), _cfg(), preserve=True,
                            source_root=tmp_path, interactive=True)
        capture.assert_not_called()
        ledger.assert_not_called()
