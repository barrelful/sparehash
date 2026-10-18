"""Consent-gated protected bootstrap: staging safety, module cap and zero-mutation refusal."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from unittest import mock

import pytest

from sparehash import bootstrap, install
from sparehash.bootstrap import Bootstrapper, ConsentRefused
from sparehash.deployment import DeploymentError, Tree
from sparehash.install_ledger import Ledger
from sparehash.sysdetect import Capabilities
from sparehash.xmrig_ledger import PACKAGE_DIR, TEMPLATE_DIR


def capabilities() -> Capabilities:
    return Capabilities("x86_64", True, 257, True, "cgroup2", True, False, False,
                        (0,), (), 2, 8 * 1024**3, None, None, False)


def make_source(root: Path, modules: tuple[str, ...] = ("__init__.py", "__main__.py", "alpha.py")) -> Path:
    package = root / "sparehash"
    package.mkdir(parents=True)
    for name in modules:
        _ = (package / name).write_text(f"# {name}\nvalue = {len(name)}\n", encoding="utf-8")
    templates = root / "systemd"
    templates.mkdir()
    _ = (templates / "sparehash.service.in").write_text("[Unit]\nDescription=x\n", encoding="utf-8")
    _ = (templates / "sparehash-xmrig.service.in").write_text("[Unit]\nDescription=y\n", encoding="utf-8")
    return root


def make_tree(root: Path) -> Tree:
    for name in ("opt", "usr/local/bin", "etc/systemd/system", "var/lib"):
        (root / name).mkdir(parents=True, exist_ok=True)
    root.chmod(0o755)
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts)):
        if path.is_dir():
            path.chmod(0o755)
    return Tree(root, os.getuid())


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes()
            for path in sorted(root.rglob("*")) if path.is_file()}


def test_accepts_consented_protected_bootstrap(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source")
    tree = make_tree(tmp_path / "host")
    foreign_dir = tree.path("/opt/sparehash")
    foreign_dir.mkdir(mode=0o755)
    foreign = foreign_dir / "keep.txt"
    _ = foreign.write_bytes(b"foreign sentinel")
    ledger = Ledger(tree)
    ledger.initialize()

    boot = Bootstrapper(tree)
    boot.consent(True)
    staged = boot.stage_source(source, tmp_path / "staging")
    boot.publish(staged, ledger)

    for relative in ("sparehash/alpha.py", "sparehash/__main__.py", "systemd/sparehash.service.in"):
        assert (staged / relative).read_bytes() == (source / relative).read_bytes()
    for name in ("__init__.py", "__main__.py", "alpha.py"):
        assert tree.path(PACKAGE_DIR + "/" + name).read_bytes() == (source / "sparehash" / name).read_bytes()
    assert tree.path(TEMPLATE_DIR + "/sparehash.service.in").read_bytes() == (source / "systemd/sparehash.service.in").read_bytes()

    shim = tree.path("/usr/local/bin/sparehash")
    expected_shim = bootstrap.SHIM_TEMPLATE.format(source=str(tree.path("/opt/sparehash/src"))).encode("utf-8")
    assert shim.read_bytes() == expected_shim
    assert shim.stat().st_mode & 0o777 == 0o755
    assert foreign.read_bytes() == b"foreign sentinel"

    oversized = make_source(tmp_path / "big",
                            modules=tuple(f"m{index}.py" for index in range(128)) + ("__main__.py",))
    capped = Bootstrapper(tree)
    capped.consent(True)
    with pytest.raises(DeploymentError):
        capped.stage_source(oversized, tmp_path / "big-staging")
    assert not (tmp_path / "big-staging").exists()
    assert not tree.path(PACKAGE_DIR + "/m0.py").exists()


def test_refuses_without_mutation(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source")
    tree = make_tree(tmp_path / "host")
    before = snapshot(tree.root)

    with pytest.raises(ConsentRefused):
        Bootstrapper(tree).stage_source(source, tmp_path / "no-consent")
    assert not (tmp_path / "no-consent").exists()
    assert snapshot(tree.root) == before

    boot = Bootstrapper(tree)
    boot.consent(True)
    original = Bootstrapper._copy_file
    calls = {"count": 0}

    def copy_then_mutate(self: Bootstrapper, src: Path, destination: Path) -> None:
        original(self, src, destination)
        calls["count"] += 1
        if calls["count"] == 1:
            _ = src.write_bytes(src.read_bytes() + b"# mutated\n")

    with mock.patch.object(Bootstrapper, "_copy_file", copy_then_mutate):
        with pytest.raises(DeploymentError):
            boot.stage_source(source, tmp_path / "mutated")
    assert not (tmp_path / "mutated").exists()
    assert not tree.path(PACKAGE_DIR + "/alpha.py").exists()
    assert snapshot(tree.root) == before

    with (mock.patch.object(install, "Tree", return_value=tree),
          mock.patch.object(install.Capabilities, "detect", return_value=capabilities()),
          mock.patch.object(install.pwd, "getpwnam", side_effect=KeyError),
          mock.patch.object(install.grp, "getgrnam", side_effect=KeyError),
          mock.patch("sparehash.systemd.probe_cpuweight_idle") as probe):
        code = install.main(["--preflight", "--json"], geteuid=lambda: 0)
    assert code == 0
    probe.assert_not_called()
    assert snapshot(tree.root) == before
    assert not tree.path(PACKAGE_DIR).exists()

    concurrent_source = make_source(tmp_path / "concurrent-source")
    ledger = Ledger(tree)
    ledger.initialize()
    shared = tmp_path / "shared"
    results: list[str] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        attempt = Bootstrapper(tree)
        attempt.consent(True)
        barrier.wait()
        try:
            published = attempt.stage_source(concurrent_source, shared)
            attempt.publish(published, ledger)
        except DeploymentError:
            results.append("refused")
        else:
            results.append("ok")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["ok", "refused"]
