# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Controller assembly consumes a protected deployment description, not receipts.

An externally provisioned deployment supplies a root-controlled description and
runs the same controller contract with no installer receipts and no mutation
authority.  The manual-pause path is passed explicitly so a hostile environment
cannot redirect it through the import-time default.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest import mock

import pytest

from sparehash import config, deployment_spec, runtime
from sparehash.controller import Controller
from sparehash.controller_io import Job, Ports, SampleId
from sparehash.runtime import Lifecycle
from sparehash.state import BlockerState

REPOSITORY = Path(__file__).resolve().parents[1]


class _Providers:
    def request(self, sample: SampleId, deadline: float) -> None:
        _ = sample, deadline


class _Jobs:
    def submit(self, job: Job) -> None:
        _ = job

    def cancel(self) -> None:
        pass

    def published(self) -> None:
        return None


def _ports(root: Path) -> Ports:
    bound = threading.Event()
    bound.set()
    return Ports(_Providers(), _Jobs(), Lifecycle(bound, str(root / "notify.sock")), lambda: 0.0)


def _provision(root: Path) -> Path:
    (root / "sparehash").mkdir(parents=True, exist_ok=True)
    (root / "systemd").mkdir(parents=True, exist_ok=True)
    _ = (root / "sparehash" / "runtime.py").write_text("# payload\n", encoding="utf-8")
    _ = (root / "systemd" / "sparehash.service").write_text("[Service]\n", encoding="utf-8")
    return root


def _spec(root: Path, *, digest: str | None = None,
          binary: str = "/opt/xmrig/current/xmrig") -> deployment_spec.DeploymentSpec:
    return deployment_spec.DeploymentSpec(
        owner="task18-fixture",
        kind="external_provision",
        layout={"opt_root": "/opt", "local_root": "/usr/local"},
        payload_version="0.2.0",
        inventory_digest=digest if digest is not None else deployment_spec.inventory_digest(root),
        runtime_paths={"xmrig_binary": Path(binary)},
        integrity_refs={"xmrig_binary": "a" * 64},
        capabilities={"load_credential": True},
    )


def test_accepts_noninteractive_provisioning(tmp_path: Path) -> None:
    cfg = config.DEFAULTS
    root = _provision(tmp_path)
    spec = _spec(root)
    state = BlockerState(state_path_override=tmp_path / "state.json")
    with mock.patch("sparehash.install_manifest.locked_store",
                    side_effect=AssertionError("installer mutation attempted")) as locked:
        owner = runtime.initialize(state, _ports(tmp_path), deployment_spec=spec, cfg=cfg, root=root)
    locked.assert_not_called()
    assert isinstance(owner, Controller)
    assert owner.stopping is False
    assert not (root / "install-manifest.json").exists()
    assert not (tmp_path / "state.json").exists()


def test_refuses_hostile_paths_or_deployment_drift(tmp_path: Path) -> None:
    cfg = config.DEFAULTS
    root = _provision(tmp_path)

    with pytest.raises(deployment_spec.DeploymentSpecError):
        runtime.validate_deployment(_spec(root, digest="0" * 64), cfg, root)
    with pytest.raises(deployment_spec.DeploymentSpecError):
        runtime.validate_deployment(_spec(root, binary="/opt/other/current/xmrig"), cfg, root)
    with pytest.raises(deployment_spec.DeploymentSpecError):
        runtime.initialize(BlockerState(state_path_override=tmp_path / "state.json"), _ports(tmp_path),
                           deployment_spec=_spec(root, digest="0" * 64), cfg=cfg, root=root)

    hostile = tmp_path / "hostile"
    hostile.mkdir()
    override = tmp_path / "explicit-state.json"
    script = (
        "import os, sys\n"
        "import sparehash.runtime\n"
        "from pathlib import Path\n"
        "from sparehash.state import BlockerState\n"
        "assert 'sparehash.install_manifest' not in sys.modules\n"
        "assert 'sparehash.install_xmrig' not in sys.modules\n"
        "assert 'sparehash.wizard' not in sys.modules\n"
        "assert 'sparehash.install' not in sys.modules\n"
        "state = BlockerState(state_path_override=Path(os.environ['TASK18_OVERRIDE']))\n"
        "state.set('manual', 'hostile', 'hostile')\n"
        "assert Path(os.environ['TASK18_OVERRIDE']).exists()\n"
        "assert not Path(os.environ['SPAREHASH_STATE_FILE']).exists()\n"
    )
    environment = dict(os.environ)
    environment.update(SPAREHASH_STATE_FILE=str(hostile / "state.json"),
                       TASK18_OVERRIDE=str(override),
                       PYTHONPATH=str(hostile))
    result = subprocess.run([sys.executable, "-c", script], cwd=str(REPOSITORY), env=environment,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert override.exists()
    assert not (hostile / "state.json").exists()


def test_state_path_override_used(tmp_path: Path) -> None:
    override = tmp_path / "state.json"
    hostile = tmp_path / "hostile-state.json"
    with mock.patch.dict(os.environ, {"SPAREHASH_STATE_FILE": str(hostile)}, clear=False):
        state = BlockerState(state_path_override=override)
        _ = runtime.initialize(state, _ports(tmp_path))
        _ = state.set("manual", "operator pause", "test")
    assert override.exists()
    assert "manual" in state.active()
    assert not hostile.exists()


def test_runtime_entry_scrubs_hostile_environment() -> None:
    """Runtime refuses hostile environment variables and path overrides (task-16 contract)."""
    import inspect
    from sparehash import config, runtime

    config_source = inspect.getsource(config.load)
    assert "SPAREHASH_CONFIG" not in config_source

    run_source = inspect.getsource(runtime.run)
    assert "SPAREHASH_RUN_DIR" in run_source
    assert "SPAREHASH_HWMON_ROOT" in run_source
    assert "SPAREHASH_STATE_FILE" in run_source
    assert "os.environ.pop" in run_source
