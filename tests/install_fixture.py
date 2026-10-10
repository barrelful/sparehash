"""Private-tree installer fixture; all privileged boundaries are injected."""

from __future__ import annotations

import grp
import os
import pwd
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sparehash import config, install, ipc, systemd, xmrig_config
from sparehash.deployment import Tree
from sparehash.sysdetect import Capabilities


def capabilities() -> Capabilities:
    return Capabilities("x86_64", True, 257, True, "cgroup2", True, False, False,
                        (0,), (), 2, 8 * 1024**3, None, None, False)


class InstallFixture(unittest.TestCase):
    def __init__(self, method_name: str = "runTest") -> None:
        super().__init__(method_name)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tree = Tree(self.root, os.getuid())
        for name in ("etc/systemd/system", "var/lib", "opt/sparehash/src", "usr/local/bin"):
            (self.root / name).mkdir(parents=True, mode=0o755)
        for path in self.root.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)
        self.calls: list[tuple[str, ...]] = []
        self.enabled = True
        self.active = False
        self.cfg = config.Config(wallet="4" + "1" * 94, pool=config.Pool(host="127.0.0.1"),
                                 gaming=config.Gaming(enabled=False))

    def checked(self, argv: tuple[str, ...], timeout: float = 10) -> str:
        self.assertTrue(0 < timeout <= 60)
        self.calls.append(argv)
        if argv[1] in ("enable", "disable"):
            self.enabled = argv[1] == "enable"
        if argv[1] == "start":
            self.active = True
        if argv[1] == "is-enabled":
            return "static" if argv[2] == "sparehash-xmrig.service" else "enabled" if self.enabled else "disabled"
        return ""

    def command(self, argv: tuple[str, ...], timeout: float = 10) -> subprocess.CompletedProcess[str]:
        self.assertTrue(0 < timeout <= 60)
        self.calls.append(argv)
        if argv[1] == "is-enabled":
            return subprocess.CompletedProcess(argv, 0 if self.enabled else 1,
                                               "enabled" if self.enabled else "disabled", "")
        return subprocess.CompletedProcess(argv, 0 if self.active else 3,
                                           "active" if self.active else "inactive", "")

    def execute(self, cfg: config.Config | None = None, verify_error: systemd.UnitError | None = None,
                *, interactive: bool = False) -> None:
        original = os.fchown
        def chown(fd: int, uid: int, gid: int) -> None:
            _ = uid, gid
            original(fd, os.getuid(), os.getgid())
        account = pwd.struct_passwd(("xmrig", "x", 996, os.getgid(), "", "/nonexistent", "/usr/sbin/nologin"))
        def group(name: str) -> grp.struct_group:
            return grp.struct_group((name, "x", os.getgid(), []))
        def choose(cfg: config.Config, caps: Capabilities, consent: bool) -> config.Config:
            _ = caps, consent
            return cfg
        with (mock.patch("sparehash.install.pwd.getpwnam", return_value=account),
              mock.patch("sparehash.install.grp.getgrnam", side_effect=group),
              mock.patch("sparehash.install.os.fchown", side_effect=chown),
              mock.patch.object(install, "choose", side_effect=choose),
              mock.patch.object(install, "isolated", return_value=(0, "XMRig 6.26.0")),
              mock.patch.object(systemd, "verify_units", return_value="", side_effect=verify_error),
              mock.patch.object(xmrig_config, "validate_rendered"),
              mock.patch.object(install, "checked", side_effect=self.checked),
              mock.patch.object(install, "command", side_effect=self.command),
              mock.patch.object(ipc, "call", return_value={"ok": True, "ready": True, "blockers": ["battery"]})):
            install.execute(self.tree, capabilities(), cfg or self.cfg, interactive=interactive)
