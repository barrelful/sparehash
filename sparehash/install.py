# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Root install orchestration; only the controller may authorize mining."""

# allow: SIZE_OK — ordered ledger deployment and retirement preflight stay in one installer entrypoint.

from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
import platform
import pwd
import signal
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Final
from uuid import uuid4

from . import config, gaming_install, ipc, lifecycle, systemd, upgrade, wizard, xmrig_config
from .bootstrap import Bootstrapper, ConsentRefused, staging_path
from .deployment import ENV, DeploymentError, Tree, checked, command
from .install_ledger import Ledger
from .install_manifest import StoreContext, locked_store
from .install_xmrig import choose, dry_run, isolated
from .lifecycle import is_retired
from .lifecycle_lease import LeaseError, LeaseStore
from .migration import CompletionEvidence, MigrationError, MigrationPhase, MigrationStore, complete_upgrade
from .sysdetect import Capabilities
from .xmrig_install import CollisionError, current_binary
from .xmrig_ledger import Artifact, GroupIdentity, Operation, UserIdentity, bootstrap_target, choice

UNITS: Final = "/etc/systemd/system"
BOOTSTRAP_PATHS: Final = ("/opt/sparehash", "/opt/sparehash/src", "/usr/local/bin/sparehash")
CONFIG_DIR: Final = "/etc/sparehash"
STATE_DIR: Final = "/var/lib/sparehash"
UPGRADE_BACKUP_DIR: Final = Path(os.environ.get("SPAREHASH_UPGRADE_BACKUP_DIR", STATE_DIR + "/upgrade-backups"))
PROTECTED_UPGRADE_FILES: Final = (
    "config.toml", "xmrig.json", "xmrig.runtime.json", "state.json", "deployment.json",
    "install-manifest.json", "lifecycle-lease.json", "lifecycle.json", "migration.json",
)
UNSUPPORTED_STAGES: Final = frozenset(("python", "systemd", "architecture"))


class Arguments(argparse.Namespace):
    answers: Path | None = None
    use_existing: bool = False
    clear_lifecycle_latch: bool = False
    operation_id: str | None = None
    bootstrap_created: list[str] | None = None
    upgrade: str | None = None
    reconfigure: bool = False
    recover: str | None = None
    complete_upgrade: str | None = None
    no_start: bool = False
    no_enable: bool = False
    preflight: bool = False
    json: bool = False
    yes: bool = False


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


def post_install(tree: Tree, cfg: config.Config, *, boot: bool = True, now: bool = True) -> None:
    for name in systemd.UNIT_NAMES:
        data = tree.read(UNITS + "/" + name)
        if data is None or not data.startswith((systemd.MARKER + "\n").encode()):
            raise DeploymentError("post-install", "unit marker missing: " + name)
    enabled = command(("/usr/bin/systemctl", "is-enabled", "sparehash.service"))
    expected = "enabled" if boot else "disabled"
    if enabled.stdout.strip() != expected or enabled.returncode != (0 if boot else 1):
        raise DeploymentError("post-install", "controller boot startup is not " + expected)
    active = command(("/usr/bin/systemctl", "is-active", "sparehash.service"))
    controller_active = active.returncode == 0 and active.stdout.strip() == "active"
    if now and not controller_active:
        raise DeploymentError("post-install", "controller is not active")
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
    mining = "unknown" if controller_active else "yes (controller inactive)"
    if controller_active:
        try:
            status = ipc.call({"cmd": "status"}, timeout=1)
            blockers = status.get("blockers")
            if status.get("ok") is True and isinstance(blockers, list) and type(status.get("ready")) is bool:
                mining = "yes" if blockers or status.get("ready") is False else "no"
        except ipc.IPCError:
            mining = "unknown"
    print(f"post-install: boot startup {expected}; controller {'active' if controller_active else 'inactive'}; "
          f"mining blocked: {mining}; miner static; loopback API configured; idle scheduler configured")


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


def upgrade_source_root() -> Path:
    """The running local release selected for --upgrade; never a remote self-updater."""
    return Path(__file__).resolve().parent.parent


@dataclass(frozen=True, slots=True)
class Preservation:
    """Pre-publish protected snapshot used to prove secrets and manual state survived."""

    operation_id: str
    backup: Path
    token_sha256: str
    manual_state_sha256: str | None


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manual_state_digest(path: Path) -> str | None:
    """Return the digest of a readable manual pause state, None when absent.

    An unreadable or non-pause document refuses: the upgrade must never treat a
    corrupt manual state as a fresh default or silently reset it.
    """
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
        decoded = json.loads(raw, parse_constant=_reject_constant)
    except (OSError, ValueError, UnicodeError) as error:
        raise DeploymentError("upgrade", f"manual pause state unreadable: {error}") from error
    if not isinstance(decoded, dict) or decoded.get("manual") is not True:
        raise DeploymentError("upgrade", "manual pause state invalid; refusing to reset it")
    return _digest(raw)


def capture_preservation(config_dir: Path, state_dir: Path, *, operation_id: str,
                         backup_root: Path = UPGRADE_BACKUP_DIR) -> Preservation:
    """Take a private 0700 root backup of protected config before any upgrade publish.

    The backup is read-only root and never used as runtime input. A missing token
    or unreadable manual state refuses rather than inventing a new default.
    """
    token_path = config_dir / "xmrig.json"
    if not token_path.is_file():
        raise DeploymentError("upgrade", "expected protected token is missing; refusing to invent a new one")
    token_sha256 = _digest(xmrig_config.read_token(token_path).encode())
    manual_state_sha256 = _manual_state_digest(state_dir / "state.json")
    directory = backup_root / operation_id
    try:
        directory.mkdir(parents=True, mode=0o700)
    except FileExistsError as error:
        raise DeploymentError("upgrade", f"backup for operation {operation_id} exists; refusing to reuse it") from error
    os.chmod(directory, 0o700)
    for origin in (*(config_dir / name for name in PROTECTED_UPGRADE_FILES),
                   *(state_dir / name for name in PROTECTED_UPGRADE_FILES)):
        if not origin.is_file():
            continue
        target = directory / origin.name
        target.write_bytes(origin.read_bytes())
        os.chmod(target, 0o400)
    return Preservation(operation_id, directory, token_sha256, manual_state_sha256)


def verify_preservation(preservation: Preservation, config_dir: Path, state_dir: Path) -> None:
    """Refuse when the protected backup, published token or manual state drifted."""
    backup_token = preservation.backup / "xmrig.json"
    if not backup_token.is_file() or _digest(xmrig_config.read_token(backup_token).encode()) != preservation.token_sha256:
        raise DeploymentError("upgrade", "protected token backup does not match the recorded value")
    published = config_dir / "xmrig.json"
    if not published.is_file() or _digest(xmrig_config.read_token(published).encode()) != preservation.token_sha256:
        raise DeploymentError("upgrade", "API token changed during upgrade; refusing")
    if _manual_state_digest(state_dir / "state.json") != preservation.manual_state_sha256:
        raise DeploymentError("upgrade", "manual pause state changed during upgrade; refusing")


def empty_proof() -> bool:
    """Fresh stopped/empty proof: neither controller nor miner may be active."""
    for unit in ("sparehash.service", "sparehash-xmrig.service"):
        result = command(("/usr/bin/systemctl", "is-active", unit))
        if result.returncode == 0 and result.stdout.strip() == "active":
            return False
    return True


def _upgrade_context(tree: Tree, operation_id: str, source_root: Path | None) -> upgrade.UpgradeContext:
    root = source_root if source_root is not None else upgrade_source_root()
    return upgrade.UpgradeContext(
        operation_id=operation_id,
        source_root=root,
        target_root=tree.root,
        staging_root=tree.path("/opt/sparehash") / (".upgrade-" + operation_id),
        preserve_startup_choices=True,
    )


def upgrade_install(tree: Tree, operation_id: str, *, source_root: Path | None = None) -> None:
    """Acquire the hold, stage and publish the selected local release; completion is explicit."""
    context = _upgrade_context(tree, operation_id, source_root)
    lease = upgrade.lease_for(tree.root)
    _ = upgrade.acquire_compatible_hold(operation_id, lease=lease)
    _ = upgrade.begin(context)
    staged = upgrade.stage_source(context, tree)
    _ = upgrade.verify_pre_publication(context, tree, upgrade.deployment_spec(context, tree, staged))
    journal = upgrade.read_journal(tree.root, operation_id)
    _ = upgrade.publish(context, journal, Ledger(tree).context())
    print(f"upgrade: staged and published under hold; run --complete-upgrade {operation_id} to commit")


def recover_install(tree: Tree, operation_id: str) -> None:
    """Roll a recorded staging forward; refuse when the matching hold or staging is gone."""
    lease = upgrade.lease_for(tree.root)
    hold = lease.load()
    if hold is None or hold.purpose != "update" or hold.request_id != operation_id:
        raise upgrade.UpgradeError("recover", "matching update hold is absent; refusing recovery")
    _ = upgrade.recover_from_staging(operation_id, tree.root)
    print(f"upgrade: staging for {operation_id} rolled forward; hold retained until --complete-upgrade")


def complete_install(tree: Tree, operation_id: str, *, proof: Callable[[], bool] | None = None) -> None:
    """Commit a published upgrade; manual pause and startup choices remain untouched."""
    _ = upgrade.complete(tree.root, operation_id, lease=upgrade.lease_for(tree.root),
                         empty_proof=proof if proof is not None else empty_proof)
    print(f"upgrade: operation {operation_id} committed; startup choices and manual state preserved")


def preflight_report(tree: Tree, caps: Capabilities) -> tuple[int, dict[str, object]]:
    """Read-only readiness report; performs no mutation and no transient probe."""
    capabilities = caps.summary()
    try:
        preflight(tree, caps)
    except DeploymentError as error:
        code = 2 if error.stage in UNSUPPORTED_STAGES else 1
        return code, {"ready": False, "stage": error.stage, "detail": str(error), "capabilities": capabilities}
    except (systemd.CollisionError, systemd.UnitError) as error:
        return 1, {"ready": False, "stage": "units", "detail": str(error), "capabilities": capabilities}
    return 0, {"ready": True, "stage": "preflight", "detail": "ready", "capabilities": capabilities}


def confirm_effects(read: Callable[[str], str], write: Callable[[str], None]) -> bool:
    """Ask for explicit effect consent; an empty answer and 'no' both refuse."""
    write("sparehash: this install stages the selected local release and writes protected deployment paths")
    while True:
        answer = read("Proceed with installation and service effects? [y/N]: ").strip().lower()
        if answer in {"", "n", "no"}:
            return False
        if answer in {"y", "yes"}:
            return True
        write("Please answer yes or no.")


def launcher_source_root() -> Path:
    """The selected source tree: the parent directory of the sparehash package."""
    return Path(__file__).resolve().parent.parent


def execute(tree: Tree, caps: Capabilities, cfg: config.Config, *, use_existing: bool = False,
            bootstrap_created: tuple[str, ...] = (), interactive: bool = True,
            preserve: bool = False, operation_id: str | None = None,
            backup_root: Path | None = None,
            source_root: Path | None = None, approved: bool = False) -> None:
    preflight(tree, caps)
    preservation: Preservation | None = None
    if preserve:
        operation = operation_id if operation_id is not None else uuid4().hex
        root = backup_root if backup_root is not None else tree.path(str(UPGRADE_BACKUP_DIR))
        preservation = capture_preservation(tree.path(CONFIG_DIR), tree.path(STATE_DIR),
                                            operation_id=operation, backup_root=root)
    ledger = Ledger(tree)
    if source_root is not None:
        if interactive and not approved:
            approved = confirm_effects(input, print)
        if not approved:
            raise ConsentRefused("consent", "installation consent was refused; no host mutation performed")
        caps = Capabilities.detect(environ=ENV, manager_probe=systemd.probe_cpuweight_idle)
        if not caps.cpuweight_idle:
            print("capabilities: idle cgroup weights unavailable; retaining Nice/SCHED_IDLE")
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
    if source_root is not None:
        bootstrapper = Bootstrapper(tree)
        bootstrapper.consent(True)
        staged = bootstrapper.stage_source(source_root, staging_path(tree))
        bootstrapper.publish(staged, ledger)
    else:
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
    boot = now = True
    if interactive:
        prompts = wizard._Prompts(input, print)
        boot = prompts.boolean("Start SpareHash automatically at boot?", default=True)
        now = prompts.boolean("Start SpareHash controller now?", default=True)
    operations: list[tuple[Operation, tuple[str, ...], int]] = [
        ("daemon-reload", ("/usr/bin/systemctl", "daemon-reload"), 30),
        ("enable-controller" if boot else "disable-controller",
         ("/usr/bin/systemctl", "enable" if boot else "disable", "sparehash.service"), 60),
    ]
    if now:
        operations.append(("start-controller", ("/usr/bin/systemctl", "start", "sparehash.service"), 60))
    for operation, argv, timeout in operations:
        def mutate(argv: tuple[str, ...] = argv, timeout: int = timeout) -> tuple[Artifact, ...]:
            _ = checked(argv, timeout)
            return (tree.receipt(UNITS + "/sparehash.service", False),)
        ledger.perform((UNITS + "/sparehash.service", operation), mutate)
    post_install(tree, cfg, boot=boot, now=now)
    if preservation is not None:
        verify_preservation(preservation, tree.path(CONFIG_DIR), tree.path(STATE_DIR))
    ledger.ready()


def interrupted(_signal: int, _frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def deadline(_signal: int, _frame: FrameType | None) -> None:
    raise DeploymentError("xmrig-timeout", "upstream selection/install exceeded 120 seconds")


def complete_upgrade_main(operation_id: str, *, geteuid: Callable[[], int] = os.geteuid) -> int:
    """Root-only complete-upgrade: postverify, then remove only journal and update hold."""
    if geteuid() != 0:
        print("sparehash: complete-upgrade requires root", file=sys.stderr)
        return 1
    tree = Tree()
    try:
        with locked_store(StoreContext(tree.trust, gid=0)) as store:
            journal = MigrationStore(store).read()
            if journal is None:
                raise MigrationError("no migration journal; completion refused")
            if journal.operation_id != operation_id:
                raise MigrationError("operation id does not match the migration journal")
            if journal.phase is not MigrationPhase.VERIFIED_AWAITING_OPERATOR:
                raise MigrationError(f"migration is not verified awaiting operator: {journal.phase.value}")
            _ = _manual_state_digest(tree.path(STATE_DIR + "/state.json"))
            evidence = CompletionEvidence(payload_identity=journal.payload_identity,
                                          empty_cgroup=empty_proof(), guards_clear=True,
                                          manual_state_preserved=True)
            _ = complete_upgrade(operation_id, store, evidence=evidence)
    except (MigrationError, LeaseError, DeploymentError, OSError, ValueError) as error:
        print(f"sparehash: complete-upgrade: {error}", file=sys.stderr)
        return 1
    print("sparehash: complete-upgrade committed; manual pause and startup choices preserved")
    return 0


def _retired_guidance() -> str:
    """Exact recovery command for a retired system; never suggest manual deletion.

    The command uses the source-tree installer entrypoint, so it stays valid even
    when the installed CLI (/usr/local/bin/sparehash) is absent mid-recovery. The
    operation id is included only when the retirement record carries one.
    """
    try:
        recorded = lifecycle.retirement_operation_id()
    except LeaseError:
        recorded = None
    command = "sudo ./install.sh --clear-lifecycle-latch"
    if recorded is not None:
        command += f" --operation-id {recorded}"
    return ("sparehash: install: this system was previously uninstalled; resolve any "
            "partial-uninstall failure, then reinstall from the source tree with:\n"
            f"  {command}\n"
            "This works even when the installed CLI is absent. Do not delete "
            "/var/lib/sparehash/lifecycle.json manually.")


def main(argv: Sequence[str] | None = None, *, geteuid: Callable[[], int] = os.geteuid) -> int:
    parser = argparse.ArgumentParser(prog="sparehash install")
    _ = parser.add_argument("--answers", type=Path)
    _ = parser.add_argument("--use-existing", action="store_true")
    _ = parser.add_argument("--clear-lifecycle-latch", action="store_true",
                            help="allow reinstall after a previous uninstall (root only)")
    _ = parser.add_argument("--operation-id", metavar="ID",
                            help="operation id of the uninstall retirement to resolve "
                                 "(required with --clear-lifecycle-latch when the record carries one)")
    _ = parser.add_argument("--upgrade", metavar="OPERATION_ID",
                            help="stage and publish a selected local release under a compatible hold")
    _ = parser.add_argument("--reconfigure", action="store_true",
                            help="re-apply configuration while preserving token, settings and manual state")
    _ = parser.add_argument("--bootstrap-created", action="append", metavar="PATH", help=argparse.SUPPRESS)
    _ = parser.add_argument("--recover", metavar="OPERATION_ID",
                            help="roll a recorded failed publication forward from its private staging")
    _ = parser.add_argument("--complete-upgrade", metavar="OPERATION_ID",
                            help="commit a published upgrade after a fresh empty proof")
    _ = parser.add_argument("--no-start", action="store_true",
                            help="fresh/reconfigure: leave the controller stopped")
    _ = parser.add_argument("--no-enable", action="store_true",
                            help="fresh/reconfigure: leave the controller disabled at boot")
    _ = parser.add_argument("--preflight", action="store_true",
                            help="report readiness without host changes")
    _ = parser.add_argument("--json", action="store_true", help="emit the preflight report as JSON")
    _ = parser.add_argument("--yes", action="store_true",
                            help="approve installation effects non-interactively")
    args = parser.parse_args(argv, namespace=Arguments())
    if geteuid() != 0:
        print("sparehash: root: installation requires root", file=sys.stderr)
        return 1
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.preflight:
            caps = Capabilities.detect(environ=ENV, manager_probe=None)
            code, document = preflight_report(Tree(), caps)
            if args.json:
                print(json.dumps(document, sort_keys=True))
            else:
                verb = "READY" if document["ready"] is True else "REFUSED"
                print(f"preflight: {verb}: {document['detail']}")
            return code
        if platform.machine() != "x86_64":
            raise DeploymentError("architecture", "x86_64 is required")
        if is_retired():
            if args.clear_lifecycle_latch:
                try:
                    lifecycle.clear_lifecycle_latch(operation_id=args.operation_id)
                except (LeaseError, OSError) as error:
                    print(f"sparehash: install: lifecycle latch retained: {error}; system remains retired", file=sys.stderr)
                    return 1
                print("sparehash: lifecycle latch cleared; reinstall permitted")
            else:
                print(_retired_guidance(), file=sys.stderr)
                return 2
        tree = Tree()
        if args.upgrade is not None or args.recover is not None or args.complete_upgrade is not None:
            selected = [name for name, value in (("--upgrade", args.upgrade), ("--recover", args.recover),
                                                 ("--complete-upgrade", args.complete_upgrade)) if value is not None]
            if len(selected) != 1:
                print("sparehash: install: choose exactly one of --upgrade/--recover/--complete-upgrade", file=sys.stderr)
                return 2
            if args.upgrade is not None:
                upgrade_install(tree, args.upgrade)
            elif args.recover is not None:
                recover_install(tree, args.recover)
            elif args.complete_upgrade is not None:
                complete_install(tree, args.complete_upgrade)
            return 0
        caps = Capabilities.detect(environ=ENV, manager_probe=None)
        preflight(tree, caps)
        existing = tree.path("/etc/sparehash/config.toml")
        current = None
        if existing.exists():
            _ = tree.read("/etc/sparehash/config.toml")
            current = wizard.load_answers(existing, caps)
        cfg = wizard.load_answers(args.answers, caps) if args.answers else wizard.run_interactive(caps, input, print, current=current)
        execute(tree, caps, cfg, use_existing=args.use_existing, bootstrap_created=tuple(args.bootstrap_created or ()),
                interactive=(args.answers is None), preserve=args.reconfigure, source_root=launcher_source_root(),
                 approved=(args.answers is not None or args.yes))
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
