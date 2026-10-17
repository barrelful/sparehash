"""Deployment description tests use synthetic trees and never the real repository."""

from __future__ import annotations

import ast
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from sparehash.deployment_spec import (
    DeploymentSpec,
    DeploymentSpecError,
    inventory_digest,
    load,
    parse,
    path_for,
    render,
    validate,
)

MODULE = Path(__file__).resolve().parent.parent / "sparehash" / "deployment_spec.py"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _tree(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    _write(root / "sparehash" / "__init__.py", "VERSION = '0.2.0'\n")
    _write(root / "sparehash" / "controller.py", "def main() -> None:\n    pass\n")
    _write(root / "systemd" / "sparehash.service.in", "[Service]\nExecStart=@BIN@\n")
    return root


def _spec(root: Path) -> DeploymentSpec:
    return DeploymentSpec(
        owner="root",
        kind="manual",
        layout={"opt_root": "/opt", "local_root": "/usr/local"},
        payload_version="0.2.0",
        inventory_digest=inventory_digest(root),
        runtime_paths={
            "source": Path("/opt/sparehash/src"),
            "binary": Path("/opt/xmrig/current/xmrig"),
            "public_config": Path("/etc/sparehash/config.toml"),
            "deployment": Path("/etc/sparehash/deployment.json"),
        },
        integrity_refs={"source": "a" * 64, "binary": "b" * 64},
        capabilities={"load_credential": True, "cgroup2": True},
    )


@pytest.mark.parametrize("kind", ["manual", "external_provision"])
def test_accepts_manual_and_external_provisioning(tmp_path: Path, kind: str) -> None:
    # Given a synthetic payload tree and a root-controlled description
    root = _tree(tmp_path, kind)
    spec = replace(_spec(root), kind=kind)  # type: ignore[arg-type]
    # When rendered, parsed and validated
    validate(spec, root)
    data = render(spec)
    # Then the document is deterministic, bounded and schema 1
    assert data == render(spec)
    assert data.endswith(b"\n")
    assert json.loads(data)["schema"] == 1
    assert not path_for(root).exists()
    assert parse(data) == spec
    # And the protected file is accepted only no-follow, root-owned, mode 0644
    destination = path_for(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    os.chmod(destination, 0o644)
    assert load(root, uid=os.getuid()) == spec


def test_rejects_foreign_or_unsafe_description(tmp_path: Path) -> None:
    # extra fields and unknown schema
    root = _tree(tmp_path, "extra")
    spec = _spec(root)
    document = json.loads(render(spec))
    document["extra"] = 1
    with pytest.raises(DeploymentSpecError):
        _ = parse(json.dumps(document).encode())
    document.pop("extra")
    document["schema"] = 2
    with pytest.raises(DeploymentSpecError):
        _ = parse(json.dumps(document).encode())

    # owner missing, empty and oversized
    for mutation in ("empty", "oversized"):
        with pytest.raises(DeploymentSpecError):
            validate(replace(spec, owner="" if mutation == "empty" else "a" * 257), root)

    # payload version and version-like literals
    with pytest.raises(DeploymentSpecError):
        validate(replace(spec, payload_version="1.2"), root)

    # non-Path and arbitrary destination
    with pytest.raises(DeploymentSpecError):
        validate(replace(spec, runtime_paths={"binary": "/opt/x/xmrig"}), root)  # type: ignore[dict-item]
    with pytest.raises(DeploymentSpecError):
        validate(replace(spec, runtime_paths={"binary": Path("/tmp/evil/xmrig")}), root)

    # capabilities must be booleans
    with pytest.raises(DeploymentSpecError):
        validate(replace(spec, capabilities={"load_credential": 1}), root)  # type: ignore[dict-item]

    # integrity references must name a runtime path and be a sha256 digest
    for refs in ({"nope": "a" * 64}, {"binary": "nothex"}):
        with pytest.raises(DeploymentSpecError):
            validate(replace(spec, integrity_refs=refs), root)

    # untrusted symlink component under root
    symlink_root = _tree(tmp_path, "symlink")
    symlink_spec = _spec(symlink_root)
    os.symlink(symlink_root / "elsewhere", symlink_root / "opt")
    with pytest.raises(DeploymentSpecError):
        validate(symlink_spec, symlink_root)

    # inventory digest drift after a payload byte changes
    drift_root = _tree(tmp_path, "drift")
    drift_spec = _spec(drift_root)
    _write(drift_root / "sparehash" / "controller.py", "def main() -> None:\n    return None\n")
    with pytest.raises(DeploymentSpecError):
        validate(drift_spec, drift_root)

    # protected file mode and no-follow enforcement
    file_root = _tree(tmp_path, "file")
    file_spec = _spec(file_root)
    destination = path_for(file_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(render(file_spec))
    os.chmod(destination, 0o600)
    with pytest.raises(DeploymentSpecError):
        _ = load(file_root, uid=os.getuid())
    destination.unlink()
    os.symlink(destination.parent / "elsewhere", destination)
    with pytest.raises(DeploymentSpecError):
        _ = load(file_root, uid=os.getuid())


def test_does_not_import_wizard_or_install() -> None:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    modules: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                modules.add(node.module)
            names.update(alias.name for alias in node.names)
    forbidden = {"install", "wizard", "gaming_install"}
    tops = {module.split(".")[0] for module in modules}
    tails = {module.split(".")[-1] for module in modules}
    assert not forbidden & (tops | tails | names)
    assert not any("install" in module or "wizard" in module for module in modules)
