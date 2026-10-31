# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Publication contract on private roots; never execute candidate code."""

from __future__ import annotations

import json
import os
import pwd
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from sparehash import config, runtime_spec
from sparehash.deployment import DeploymentError, Tree
from sparehash.layout import probe
from sparehash.xmrig_config import (
    PROPERTIES,
    DryRunRequest,
    DryRunResult,
    RenderError,
)


@dataclass(frozen=True, slots=True)
class Publication:
    cfg: config.Config
    tree: Tree
    candidate: Path
    marker: Path


@pytest.fixture
def publication(tmp_path: Path) -> Iterator[Publication]:
    for directory in ("opt", "usr", "usr/local", "run", "run/sparehash-miner", "run/sparehash-miner/private"):
        (tmp_path / directory).mkdir(mode=0o755)
    (tmp_path / "run/sparehash-miner/private").chmod(0o750)
    candidate = tmp_path / "opt/xmrig"
    marker = tmp_path / "executed"
    _ = candidate.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 0\n', encoding="utf-8")
    candidate.chmod(0o755)
    cfg = config.Config(wallet="fixture", pool=config.Pool("pool.invalid", 3333),
                        cpu=config.Cpu(mode="custom", custom_json='{"rx":[1]}'),
                        xmrig=config.Xmrig(str(candidate)))
    layout = probe(tmp_path, uid=os.getuid())
    try:
        with (
            patch.object(runtime_spec.pwd, "getpwnam", return_value=pwd.getpwuid(os.getuid())),
            patch.object(runtime_spec.os, "fchown"),
            patch.object(runtime_spec, "binary", return_value=candidate),
            patch("sparehash.xmrig_config.read_token", return_value="a" * 64),
        ):
            yield Publication(cfg, Tree(tmp_path, os.getuid(), layout), candidate, marker)
    finally:
        layout.close()


@pytest.mark.parametrize("stale", [False, True])
def test_benchmark_refuses_when_runner_missing(publication: Publication, stale: bool) -> None:
    # Given: a stale operation must not be replaced with a successful publication.
    tree = publication.tree
    previous = b'{"generation":7,"mode":"mine"}\n'
    if stale:
        tree.write(runtime_spec.SPEC, (previous, 0o640, os.getgid()))
    # When / Then: the renderer owns the controlled refusal, not a NameError.
    with pytest.raises(RenderError, match="runner is required"):
        runtime_spec.publish(publication.cfg, 8, "benchmark", tree=tree, layout=tree.layout)
    assert tree.read(runtime_spec.SPEC) == (previous if stale else None)
    assert tree.read(runtime_spec.BENCHMARK) is None
    assert not publication.marker.exists()


def test_publication_creates_child_without_changing_manager_parent(publication: Publication) -> None:
    tree = publication.tree
    child = tree.path(runtime_spec.DIRECTORY)
    child.rmdir()
    child.parent.chmod(0o711)
    parent = child.parent.stat()
    runtime_spec.publish(publication.cfg, 4, "mine", tree=tree, layout=tree.layout)
    assert child.parent.stat().st_uid == parent.st_uid
    assert child.parent.stat().st_gid == parent.st_gid
    assert child.parent.stat().st_mode == parent.st_mode
    assert child.stat().st_mode & 0o777 == 0o750
    assert tree.path(runtime_spec.SPEC).stat().st_mode & 0o777 == 0o640
    assert not (child.parent / "operation.json").exists()


@pytest.mark.parametrize("fault", ["mode", "symlink"])
def test_publication_refuses_unsafe_existing_child(publication: Publication, fault: str) -> None:
    tree = publication.tree
    child = tree.path(runtime_spec.DIRECTORY)
    if fault == "mode":
        child.chmod(0o755)
    else:
        child.rmdir()
        child.symlink_to(child.parent)
    with pytest.raises((OSError, DeploymentError)):
        runtime_spec.publish(publication.cfg, 4, "mine", tree=tree, layout=tree.layout)
    assert not (child.parent / "operation.json").exists()


def test_benchmark_uses_supplied_runner_when_publishing(publication: Publication) -> None:
    # Given: a runner consumes only a private snapshot and sanitized config.
    tree = publication.tree
    payload = publication.candidate.read_bytes()
    requests: list[DryRunRequest] = []

    def runner(request: DryRunRequest) -> DryRunResult:
        requests.append(request)
        assert request.snapshot != publication.candidate
        assert request.snapshot.read_bytes() == payload
        assert "a" * 64 not in request.config.read_text(encoding="utf-8")
        return DryRunResult(0, request.uid, request.required_properties, True)

    # When
    runtime_spec.publish(publication.cfg, 9, "benchmark", tree=tree,
                         layout=tree.layout, runner=runner)
    # Then: both documents were published through the supplied private Tree.
    operation = json.loads(tree.path(runtime_spec.SPEC).read_bytes())
    benchmark = json.loads(tree.path(runtime_spec.BENCHMARK).read_bytes())
    assert operation == {"generation": 9, "mode": "benchmark",
                         "binary": str(publication.candidate), "config": runtime_spec.BENCHMARK}
    assert benchmark["pools"] == []
    assert benchmark["http"] == {"enabled": False}
    assert len(requests) == 1
    assert requests[0].uid != 0
    assert not requests[0].snapshot.exists()
    assert not requests[0].config.exists()
    assert not publication.marker.exists()


@pytest.mark.parametrize("result", [
    DryRunResult(0, 0, PROPERTIES, True),
    DryRunResult(0, 65534, (), True),
    DryRunResult(0, 65534, PROPERTIES, False),
    DryRunResult(2, 65534, PROPERTIES, True, "success"),
])
def test_benchmark_refuses_when_runner_attestation_fails(
    publication: Publication, result: DryRunResult,
) -> None:
    # Given: a zero exit alone or success text is not proof of safe validation.
    tree = publication.tree

    def runner(request: DryRunRequest) -> DryRunResult:
        _ = request
        return result

    # When / Then
    with pytest.raises(RenderError):
        runtime_spec.publish(publication.cfg, 10, "benchmark", tree=tree,
                             layout=tree.layout, runner=runner)
    assert tree.read(runtime_spec.SPEC) is None
    assert tree.read(runtime_spec.BENCHMARK) is None
    assert not publication.marker.exists()
