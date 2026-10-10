# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Root install orchestration; never start mining during deployment."""

# allow: SIZE_OK — ordered ledger deployment and retirement preflight stay in one installer entrypoint.

from __future__ import annotations

import argparse
import grp
import os
import platform
import pwd
import signal
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from types import FrameType
from typing import Final

from . import config, gaming_install, lifecycle, systemd, wizard, xmrig_config
from .deployment import ENV, DeploymentError, Tree, checked
from .install_ledger import Ledger
from .install_manifest import StoreContext, locked_store
from .install_xmrig import choose, dry_run, isolated
from .lifecycle import is_retired
from .lifecycle_lease import LeaseError
from .sysdetect import Capabilities
from .xmrig_install import CollisionError, current_binary
from .xmrig_ledger import Artifact, GroupIdentity, Operation, UserIdentity, bootstrap_target, choice

UNITS: Final = "/etc/systemd/system"
BOOTSTRAP_PATHS: Final = ("/opt/sparehash", "/opt/sparehash/src", "/usr/local/bin/sparehash")


class Arguments(argparse.Namespace):
    answers: Path | None = None
    use_existing: bool = False
    clear_lifecycle_latch: bool = False
    bootstrap_created: list[str] | None = None


def preflight(tree: Tree, caps: Capabilities) -> None:
    if not caps.python_supported or sys.version_info < (3, 11):
        raise DeploymentError("python", "Python 3.11 or newer is required")
    if caps.systemd_pid1 is not True:
        raise DeploymentError("systemd", "systemd must be PID 1")
    if caps.architecture not in {"x86_64", "amd64"}:
        raise DeploymentError("architecture", "x86_64 is required")
    systemd.check_collisions(tree.path(UNITS))
    for name in systemd.UNIT_NAMES:
        _ = tree.read(UNITS + "/" + name)
    if tree.root == Path("/"):
        for directory in ("/usr/lib/systemd/system", "/run/systemd/system"):
            if Path(directory).is_dir():
                systemd.check_collisions(Path(directory))
    if os.path.lexists(tree.path(UNITS + "/xmrig.service")):
        raise DeploymentError("units", "refusing foreign " + UNITS + "/xmrig.service")
    if os.path.lexists(tree.trust.managed_root) and current_binary(tree.trust) is None:
        raise DeploymentError("xmrig-collision", "foreign or corrupt /opt/xmrig; leave it untouched")
    if tree.trust.manifest_path.exists():
        with locked_store(StoreContext(tree.trust, gid=0)) as store:
            prior = store.read()
            if prior is not None and prior.manifest.versions and not tree.trust.managed_root.exists():
                raise DeploymentError("manifest", "ghost inventory references a missing /opt/xmrig")
    try:
        account = pwd.getpwnam("xmrig")
    except KeyError:
        account = None
    if account is not None and (not 0 < account.pw_uid < 1000 or account.pw_shell not in {"/usr/sbin/nologin", "/sbin/nologin", "/bin/false"}):
        raise DeploymentError("identity", "xmrig is an existing login; it will not be adopted")
    for name in ("xmrig", "sparehash"):
        try:
            group = grp.getgrnam(name)
        except KeyError:
            continue
        if group.gr_gid == 0:
            raise DeploymentError("identity", "dedicated groups cannot alias the root group")


def identities(ledger: Ledger) -> None:
    for name in ("sparehash", "xmrig"):
        try:
            _ = grp.getgrnam(name)
        except KeyError:
            def create_group(name: str = name) -> tuple[GroupIdentity, ...]:
                _ = checked(("/usr/sbin/groupadd", "--system", name))
                return (GroupIdentity("group", choice(name, ("xmrig", "sparehash")), grp.getgrnam(name).gr_gid, True),)
            ledger.perform(("group:" + name, "create-group"), create_group)
    try:
        _ = pwd.getpwnam("xmrig")
    except KeyError:
        def create_user() -> tuple[UserIdentity, ...]:
            _ = checked(("/usr/sbin/useradd", "--system", "--gid", "xmrig", "--no-create-home", "--home-dir", "/nonexistent", "--shell", "/usr/sbin/nologin", "xmrig"))
            account = pwd.getpwnam("xmrig")
            return (UserIdentity("user", "xmrig", account.pw_uid, account.pw_gid, True),)
        ledger.perform(("user:xmrig", "create-user"), create_user)
    account = pwd.getpwnam("xmrig")
    ledger.applied(0, (UserIdentity("user", "xmrig", account.pw_uid, account.pw_gid, False),
                       GroupIdentity("group", "xmrig", grp.getgrnam("xmrig").gr_gid, False),
                       GroupIdentity("group", "sparehash", grp.getgrnam("sparehash").gr_gid, False)))


def file(ledger: Ledger, name: str, payload: tuple[bytes, int, int]) -> None:
    data, mode, gid = payload
    old = ledger.tree.read(name)
    if old == data:
        info = ledger.tree.path(name).lstat()
        if info.st_uid == ledger.tree.uid and info.st_gid == gid and info.st_mode & 0o777 == mode:
            return
    def mutate() -> tuple[Artifact, ...]:
        ledger.tree.write(name, payload)
        return (ledger.tree.receipt(name, old is None),)
    ledger.perform((name, "write-file"), mutate)


def install_config(ledger: Ledger, cfg: config.Config, token: str) -> None:
    spare_gid, miner_gid = grp.getgrnam("sparehash").gr_gid, grp.getgrnam("xmrig").gr_gid
    with tempfile.TemporaryDirectory(prefix=".sparehash-config-", dir=ledger.tree.path("/etc/sparehash")) as directory:
        stage = Path(directory)
        config.save(cfg, stage / "config.toml", warn_overwrite=False)
        xmrig_config.write_xmrig_json(cfg, token, stage / "xmrig.json", runner=dry_run)
        rendered = xmrig_config.render_config(cfg, token, Path(cfg.xmrig.binary), runner=dry_run)
        xmrig_config.validate_rendered(rendered, Path(cfg.xmrig.binary), dry_run)
        file(ledger, "/etc/sparehash/config.toml", ((stage / "config.toml").read_bytes(), 0o644, spare_gid))
        data = (stage / "xmrig.json").read_bytes()
        file(ledger, "/etc/sparehash/xmrig.json", (data, 0o640, spare_gid))
        file(ledger, "/etc/sparehash/xmrig.runtime.json", (data, 0o640, miner_gid))


def install_units(ledger: Ledger, caps: Capabilities) -> None:
    units = systemd.render(caps)
    if any(not text.startswith(systemd.MARKER + "\n") for text in (units["sparehash.service"], units["sparehash-xmrig.service"])):
        raise DeploymentError("units", "missing managed unit marker")
    with tempfile.TemporaryDirectory(prefix=".sparehash-units-", dir=ledger.tree.path(UNITS)) as directory:
        stage = Path(directory)
        systemd.install_units(units, stage)
        warning = systemd.verify_units(stage)
        if warning:
            print("units: " + warning.strip())
        for name in systemd.UNIT_NAMES:
            file(ledger, UNITS + "/" + name, ((stage / name).read_bytes(), 0o644, 0))


def post_install(tree: Tree, cfg: config.Config) -> None:
    for name in systemd.UNIT_NAMES:
        data = tree.read(UNITS + "/" + name)
        if data is None or not data.startswith((systemd.MARKER + "\n").encode()):
            raise DeploymentError("post-install", "unit marker missing: " + name)
    if checked(("/usr/bin/systemctl", "is-enabled", "sparehash.service")) != "enabled":
        raise DeploymentError("post-install", "controller is not enabled")
    if checked(("/usr/bin/systemctl", "is-enabled", "sparehash-xmrig.service")) != "static":
        raise DeploymentError("post-install", "miner must remain static")
    if tree.path(UNITS + "/xmrig.service").exists():
        raise DeploymentError("post-install", "foreign xmrig.service")
    code, _ = isolated(Path(cfg.xmrig.binary), ("--version",))
    if code:
        raise DeploymentError("post-install", "isolated XMRig version check failed")
    document = xmrig_config.render_config(cfg, xmrig_config.read_token(tree.path("/etc/sparehash/xmrig.json")), Path(cfg.xmrig.binary), runner=dry_run)
    if document["http"] is None:
        raise DeploymentError("post-install", "missing loopback API")
    print("post-install: controller enabled (not started); miner static; loopback API configured; idle scheduler configured")


def bootstrap(ledger: Ledger, created: tuple[str, ...]) -> None:
    """Record only paths the shell bootstrap created, restricted to its exact grammar."""
    tree = ledger.tree
    declared = set(created)
    for name in BOOTSTRAP_PATHS:
        if name not in declared and tree.path(name).exists():
            ledger.applied(0, (tree.receipt(name, False),))
    for name in created:
        target = bootstrap_target(name)
        if target is None:
            raise DeploymentError("bootstrap", "unsupported bootstrap target: " + name)
        kind, identity, mode = target
        receipt = tree.receipt(name, True, identity=identity)
        if receipt.kind != kind or receipt.uid != tree.uid or receipt.mode != mode:
            raise DeploymentError("bootstrap", "unprotected bootstrap target: " + name)
        ledger.applied(0, (receipt,))


def execute(tree: Tree, caps: Capabilities, cfg: config.Config, *, use_existing: bool = False,
            bootstrap_created: tuple[str, ...] = (), interactive: bool = True) -> None:
    preflight(tree, caps)
    ledger = Ledger(tree)
    established = tree.path("/etc/sparehash/config.toml").exists()
    existed_dirs = {name: tree.path(name).exists() for name in ("/etc/sparehash", "/var/lib/sparehash")}
    token_path = tree.path("/etc/sparehash/xmrig.json")
    if established or token_path.exists():
        try:
            _ = tree.read("/etc/sparehash/xmrig.json")
            token = xmrig_config.read_token(token_path)
        except (OSError, config.ConfigError) as error:
            raise DeploymentError("token", "restore the protected xmrig.json token; automatic rotation is refused") from error
    else:
        token = xmrig_config.new_token()
    ledger.initialize()
    identities(ledger)
    bootstrap(ledger, bootstrap_created)
    gid = grp.getgrnam("sparehash").gr_gid
    for name in ("/etc/sparehash", "/var/lib/sparehash"):
        existed = existed_dirs[name]
        def create_directory(name: str = name, existed: bool = existed) -> tuple[Artifact, ...]:
            tree.directory(name, gid)
            return (tree.receipt(name, not existed),)
        ledger.perform((name, "create-directory"), create_directory)
    alarm = signal.signal(signal.SIGALRM, deadline)
    timer = signal.setitimer(signal.ITIMER_REAL, 120)
    try:
        cfg = choose(cfg, caps, use_existing)
    finally:
        _ = signal.setitimer(signal.ITIMER_REAL, *timer)
        _ = signal.signal(signal.SIGALRM, alarm)
    def consent(prompt: str) -> bool:
        choices = {"": True, "y": True, "yes": True, "n": False, "no": False}
        while True:
            answer = input(prompt).strip().lower()
            if answer in choices:
                return choices[answer]
            print("Please answer yes or no.")
    result = gaming_install.configure(cfg, caps, consent if interactive else None, ledger=ledger)
    cfg = result.config
    install_config(ledger, cfg, token)
    install_units(ledger, caps)
    operations: tuple[tuple[Operation, tuple[str, ...], int], ...] = (("daemon-reload", ("/usr/bin/systemctl", "daemon-reload"), 30),
                                     ("enable-controller", ("/usr/bin/systemctl", "enable", "sparehash.service"), 60))
    for operation, argv, timeout in operations:
        def mutate(argv: tuple[str, ...] = argv, timeout: int = timeout) -> tuple[Artifact, ...]:
            _ = checked(argv, timeout)
            return (tree.receipt(UNITS + "/sparehash.service", False),)
        ledger.perform((UNITS + "/sparehash.service", operation), mutate)
    post_install(tree, cfg)
    ledger.ready()


def interrupted(_signal: int, _frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def deadline(_signal: int, _frame: FrameType | None) -> None:
    raise DeploymentError("xmrig-timeout", "upstream selection/install exceeded 120 seconds")


def main(argv: Sequence[str] | None = None, *, geteuid: Callable[[], int] = os.geteuid) -> int:
    parser = argparse.ArgumentParser(prog="sparehash install")
    _ = parser.add_argument("--answers", type=Path)
    _ = parser.add_argument("--use-existing", action="store_true")
    _ = parser.add_argument("--clear-lifecycle-latch", action="store_true",
                            help="allow reinstall after a previous uninstall (root only)")
    _ = parser.add_argument("--bootstrap-created", action="append", metavar="PATH", help=argparse.SUPPRESS)
    args = parser.parse_args(argv, namespace=Arguments())
    if geteuid() != 0:
        print("sparehash: root: installation requires root", file=sys.stderr)
        return 1
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        if platform.machine() != "x86_64":
            raise DeploymentError("architecture", "x86_64 is required")
        if is_retired():
            if args.clear_lifecycle_latch:
                try:
                    lifecycle.clear_lifecycle_latch()
                except (LeaseError, OSError) as error:
                    print(f"sparehash: install: lifecycle latch retained: {error}; system remains retired", file=sys.stderr)
                    return 1
                print("sparehash: lifecycle latch cleared; reinstall permitted")
            else:
                print("sparehash: install: this system was previously uninstalled; "
                      "run with --clear-lifecycle-latch to reinstall, or remove /var/lib/sparehash/lifecycle.json manually",
                      file=sys.stderr)
                return 2
        caps = Capabilities.detect(environ=ENV, manager_probe=systemd.probe_cpuweight_idle)
        tree = Tree()
        preflight(tree, caps)
        if not caps.cpuweight_idle:
            print("capabilities: idle cgroup weights unavailable; retaining Nice/SCHED_IDLE")
        existing = tree.path("/etc/sparehash/config.toml")
        current = None
        if existing.exists():
            _ = tree.read("/etc/sparehash/config.toml")
            current = wizard.load_answers(existing, caps)
        cfg = wizard.load_answers(args.answers, caps) if args.answers else wizard.run_interactive(caps, input, print, current=current)
        execute(tree, caps, cfg, use_existing=args.use_existing, bootstrap_created=tuple(args.bootstrap_created or ()),
                interactive=(args.answers is None))
        return 0
    except KeyboardInterrupt:
        print("sparehash: interrupted; pending ledger retained for inspection", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, systemd.CollisionError, systemd.UnitError, CollisionError) as error:
        print(f"sparehash: install: {error}", file=sys.stderr)
        return 1
    finally:
        _ = signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
