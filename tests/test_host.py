from pathlib import Path

import pytest

from sparehash.host import HostFacts


@pytest.mark.parametrize(
    ("release", "expected"),
    [
        ('ID=debian\nID_LIKE=""\nNAME="Debian GNU/Linux"\n', "debian"),
        ('ID=ubuntu\nNAME="Ubuntu"\n', "ubuntu"),
        ('ID=linuxmint\nID_LIKE="ubuntu debian"\nNAME="Linux Mint"\n', "linuxmint"),
        ('ID=bazzite\nID_LIKE="fedora"\nNAME="Bazzite"\n', "bazzite"),
        ('ID=fedora\nNAME="Fedora Linux"\n', "fedora"),
        ('ID=nixos\nNAME="NixOS"\n', "nixos"),
        ('ID=debian\nNAME="Debian"\nVERSION_ID="12"\n', "debian"),
        ('ID=ubuntu\nID_LIKE="debian"\nNAME="Ubuntu"\n', "ubuntu"),
    ],
)
def test_accepts_host_inventory(tmp_path: Path, release: str, expected: str) -> None:
    etc = tmp_path / "etc"
    etc.mkdir()
    _ = (etc / "os-release").write_text(release, encoding="utf-8")
    for path in ("etc/pve", "run/pve-cluster", "sysroot/ostree", "run/ostree", "usr/lib/ostree-boot"):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
    facts = HostFacts.detect(root=tmp_path)
    assert facts.os_release.identifier == expected
    assert facts.inconclusive is False


@pytest.mark.parametrize(
    "release",
    [
        'ID=debian\nID=ubuntu\n',
        'ID=debian; touch /tmp/nope\n',
        'ID="unterminated\n',
        'ID=debian\nNAME="control\x01"\n',
        "ID=debian\n" + "X=" + "a" * 17_000,
        "ID=debian\nVERSION_ID=1\nVERSION_ID=2\n",
        "ID=debian\n" + "\xff",
        "ID=\nNAME=unknown\n",
    ],
)
def test_rejects_unsafe_or_inconclusive_inventory(tmp_path: Path, release: str) -> None:
    etc = tmp_path / "etc"
    etc.mkdir()
    _ = (etc / "os-release").write_bytes(release.encode("utf-8", errors="replace"))
    facts = HostFacts.detect(root=tmp_path)
    assert facts.reason
    assert facts.inconclusive is True


def test_accepts_vendor_os_release_link(tmp_path: Path) -> None:
    (tmp_path / "etc").mkdir()
    (tmp_path / "usr/lib").mkdir(parents=True)
    _ = (tmp_path / "usr/lib/os-release").write_text("ID=ubuntu\n", encoding="utf-8")
    (tmp_path / "etc/os-release").symlink_to("../usr/lib/os-release")
    facts = HostFacts.detect(root=tmp_path)
    assert facts.os_release.identifier == "ubuntu"


def test_rejects_deep_os_release_link_chain(tmp_path: Path) -> None:
    (tmp_path / "etc").mkdir()
    (tmp_path / "usr/lib").mkdir(parents=True)
    (tmp_path / "etc/os-release").symlink_to("../usr/lib/os-release")
    (tmp_path / "usr/lib/os-release").symlink_to("os-release.image")
    _ = (tmp_path / "usr/lib/os-release.image").write_text("ID=debian\n", encoding="utf-8")
    facts = HostFacts.detect(root=tmp_path)
    assert facts.reason
    assert facts.inconclusive is True


def test_refuses_debian_identity_with_proxmox_evidence(tmp_path: Path) -> None:
    (tmp_path / "etc").mkdir()
    _ = (tmp_path / "etc/os-release").write_text("ID=debian\n", encoding="utf-8")
    (tmp_path / "etc/pve").mkdir()
    facts = HostFacts.detect(root=tmp_path)
    assert facts.proxmox.detected is True
    assert facts.deployment.certified_desktop is False
    assert facts.reason
    assert facts.inconclusive is True


def test_inventory_records_container_and_proxmox_helper_independently(tmp_path: Path) -> None:
    (tmp_path / "etc").mkdir()
    _ = (tmp_path / "etc/os-release").write_text("ID=debian\n", encoding="utf-8")
    (tmp_path / "run").mkdir()
    _ = (tmp_path / "run/.containerenv").write_text("", encoding="utf-8")
    (tmp_path / "run/pve-cluster").mkdir()
    facts = HostFacts.detect(root=tmp_path)
    assert facts.container.kind == "container"
    assert "pveversion" in facts.helpers_missing
