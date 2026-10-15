# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Cross-consumer threading: one pinned ``Layout`` honored by every consumer.

Each case builds a synthetic anchor owned by the current user, so no test
resolves or touches a real ``/opt``, ``/usr/local`` or mount.
``test_accepts_consistent_consumers`` runs a conventional and an Atomic redirect
tree through the same deployment/trust/detection/config consumers.
``test_rejects_consumer_drift`` swaps the pinned physical target and asserts
every consumer refuses or detects the substitution rather than following it.

Identity hashing does not yet cover redirect inode substitution; that remains an
open follow-up and is exercised here only as consumer-level drift refusal.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from sparehash import deployment, layout, xmrig_config, xmrig_detect, xmrig_install
from sparehash.layout import LayoutKind
from sparehash.paths import physical_path
from sparehash.xmrig_paths import MANIFEST_RELPATH, MARKER_NAME, PathTrust

VERSION = "xmrig-6.26.0"
BINARY_BYTES = b"#!/bin/sh\nexit 0\n"
LOGICAL_ROOT = "/opt/xmrig"
MANIFEST_TEXT = (
    '{"root":"' + LOGICAL_ROOT + '","versions":[{"version":"' + VERSION + '",'
    '"installed_at":"2026-10-07T00:00:00Z","source":"github:xmrig/xmrig:v6.26.0"}]}'
)


def _mkdir(path: Path, mode: int = 0o755) -> Path:
    """Create a directory and chmod every new ancestor, beating the process umask."""
    created: list[Path] = []
    current = path
    while not current.exists():
        created.append(current)
        current = current.parent
    for directory in reversed(created):
        directory.mkdir()
    for directory in [*reversed(created), path]:
        directory.chmod(mode)
    return path


def _conventional(root: Path) -> None:
    _mkdir(root / "opt" / "xmrig")
    _mkdir(root / "usr" / "local")


def _atomic(root: Path) -> None:
    _mkdir(root / "var" / "opt" / "xmrig")
    _mkdir(root / "var" / "usrlocal")
    _mkdir(root / "usr")
    (root / "opt").symlink_to("var/opt")
    (root / "usr" / "local").symlink_to("../var/usrlocal")


class _Settings:
    """A probed layout plus the protected managed tree it anchors."""

    def __init__(self, root: Path, pinned: layout.Layout, trust: PathTrust) -> None:
        self.root = root
        self.pinned = pinned
        self.trust = trust
        self.managed = pinned.opt_physical / "xmrig"
        self.version_dir = self.managed / VERSION
        self.binary = self.version_dir / "xmrig"
        self.marker = self.managed / MARKER_NAME
        self.manifest = root / MANIFEST_RELPATH


def _build(root: Path, kind: str) -> _Settings:
    if kind == "atomic":
        _atomic(root)
    else:
        _conventional(root)
    pinned = layout.probe(root, uid=os.getuid())
    settings = _Settings(root, pinned, PathTrust(root, os.getuid(), pinned))
    _mkdir(settings.version_dir)
    _ = settings.binary.write_bytes(BINARY_BYTES)
    settings.binary.chmod(0o755)
    (settings.managed / "current").symlink_to(VERSION)
    _ = settings.marker.write_text(LOGICAL_ROOT, encoding="utf-8")
    settings.marker.chmod(0o600)
    _mkdir(settings.manifest.parent)
    _ = settings.manifest.write_text(MANIFEST_TEXT, encoding="utf-8")
    settings.manifest.chmod(0o600)
    return settings


def _detect_probe(snapshot: Path, *, timeout: int, uid: int,
                  required_properties: tuple[str, ...]) -> str:
    _ = (timeout, uid, required_properties)
    assert snapshot.read_bytes() == BINARY_BYTES
    return "XMRig 6.26.0"


class _Runner:
    def __call__(self, request: xmrig_config.DryRunRequest) -> xmrig_config.DryRunResult:
        _ = request
        return xmrig_config.DryRunResult(0, 65534, xmrig_config.PROPERTIES, True)


@pytest.fixture(params=["conventional", "atomic"])
def settings(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[_Settings]:
    built = _build(tmp_path, request.param)
    try:
        yield built
    finally:
        built.pinned.close()


def test_accepts_consistent_consumers(settings: _Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    trust, pinned = settings.trust, settings.pinned
    logical_current = trust.logical_managed_root / "current/xmrig"

    # paths maps the logical artifact onto the pinned physical target.
    assert physical_path(trust.logical_managed_root, settings.root, pinned) == settings.managed
    assert trust.managed_root == settings.managed
    if pinned.kind is LayoutKind.CONVENTIONAL:
        assert settings.managed == settings.root / "opt" / "xmrig"
    else:
        assert settings.managed == settings.root / "var" / "opt" / "xmrig"

    # PathTrust authorizes and snapshots the manifest-recorded current binary.
    assert trust.authorized_current(settings.marker, settings.manifest) == settings.binary
    private = settings.root / "private"
    private.mkdir(mode=0o755)
    snapshot = trust.current_snapshot(private, settings.marker, settings.manifest)
    assert snapshot is not None
    assert snapshot[0].read_bytes() == BINARY_BYTES
    snapshot[0].unlink()

    # The deployment Tree reads the same physical bytes through the logical name.
    tree = deployment.Tree(settings.root, os.getuid(), pinned)
    assert tree.read(f"{LOGICAL_ROOT}/{VERSION}/xmrig") == BINARY_BYTES

    # The installer reports the layout-aware current binary.
    assert xmrig_install.current_binary(trust) == settings.binary

    # Detection snapshots and verifies the authorized current candidate.
    monkeypatch.setattr(xmrig_detect, "candidate_paths", lambda caps=None: (logical_current,))
    installs = xmrig_detect.detect_installations(
        trust=trust, layout=pinned, probe=_detect_probe)
    assert installs == [xmrig_detect.Install(logical_current, (6, 26, 0),
                                             xmrig_detect.ManagedBy.SPAREHASH, True, False)]

    # The config dry-run hook snapshots the same authorized current binary.
    xmrig_config.validate_rendered(
        {"http": {"enabled": True}}, Path(xmrig_config.XMRIG_CURRENT), _Runner(),
        layout=pinned, trust=trust)


def test_rejects_consumer_drift(settings: _Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    trust, pinned = settings.trust, settings.pinned
    marker, manifest = settings.marker, settings.manifest
    logical = trust.logical_managed_root / VERSION / "xmrig"
    private = settings.root / "private"
    private.mkdir(mode=0o755)

    # Swap the pinned physical target: the renamed original is replaced by an
    # empty directory, so any consumer that follows the path reads nothing.
    target = pinned.opt_physical
    os.rename(target, target.with_name(target.name + ".original"))
    target.mkdir(mode=0o755)

    # Every consumer refuses or detects the swap rather than using the substitute.
    with pytest.raises(layout.LayoutError):
        _ = physical_path(logical, settings.root, pinned)
    assert trust.authorized_current(marker, manifest) is None
    assert trust.current_snapshot(private, marker, manifest) is None
    assert xmrig_install.current_binary(trust) is None
    with pytest.raises(layout.LayoutError):
        _ = deployment.Tree(settings.root, os.getuid(), pinned).read(f"{LOGICAL_ROOT}/{VERSION}/xmrig")
    monkeypatch.setattr(xmrig_detect, "candidate_paths",
                        lambda caps=None: (trust.logical_managed_root / "current/xmrig",))
    installs = xmrig_detect.detect_installations(trust=trust, layout=pinned, probe=_detect_probe)
    assert not any(item.verified for item in installs)
