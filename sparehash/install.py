# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Root install orchestration; only the controller may authorize mining."""

# allow: SIZE_OK — ordered ledger deployment and retirement preflight stay in one installer entrypoint.

from __future__ import annotations

import argparse
import grp
import json
import os
import platform
import pwd
import shutil
import signal
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from types import FrameType
from typing import Final

from . import config, gaming_install, host, ipc, lifecycle, systemd, wizard, xmrig_config
from .deployment import ENV, DeploymentError, Tree, checked, command
from .install_ledger import Ledger
from .install_manifest import StoreContext, locked_store
from .install_xmrig import choose, dry_run, isolated
from .lifecycle import is_retired
from .lifecycle_lease import LeaseError
from .preflight import HostFacts as PreflightFacts
from .preflight import Preflight
from .preflight import evaluate as evaluate_preflight
from .sysdetect import FIXED_PATH, Capabilities
from .xmrig_install import CollisionError, current_binary
from .xmrig_ledger import Artifact, GroupIdentity, Operation, UserIdentity, bootstrap_target, choice

UNITS: Final = "/etc/systemd/system"
BOOTSTRAP_PATHS: Final = ("/opt/sparehash", "/opt/sparehash/src", "/usr/local/bin/sparehash")

PREFLIGHT_EXIT: Final[dict[str, int]] = {
    "READY": 0,
    "ACTION_REQUIRED": 2,
    "REFUSED_UNSUPPORTED": 1,
    "REFUSED_UNSAFE": 1,
    "INCONCLUSIVE": 1,
}


class Arguments(argparse.Namespace):
    answers: Path | None = None
    use_existing: bool = False
    clear_lifecycle_latch: bool = False
    preflight: bool = False
    json: bool = False
    yes: bool = False
    no_start: bool = False
    no_enable: bool = False
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


def execute(tree: Tree, caps: Capabilities, cfg: config.Config, *, use_existing: bool = False,
            bootstrap_created: tuple[str, ...] = (), interactive: bool = True,
            boot: bool | None = None, now: bool | None = None) -> None:
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
    if interactive:
        prompts = wizard._Prompts(input, print)
        if boot is None:
            boot = prompts.boolean("Start SpareHash automatically at boot?", default=True)
        if now is None:
            now = prompts.boolean("Start SpareHash controller now?", default=True)
    boot = True if boot is None else boot
    now = True if now is None else now
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
    ledger.ready()


def interrupted(_signal: int, _frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def deadline(_signal: int, _frame: FrameType | None) -> None:
    raise DeploymentError("xmrig-timeout", "upstream selection/install exceeded 120 seconds")


def _validate_options(args: Arguments) -> str | None:
    """Return a refusal reason for contradictory options, or None when valid."""
    if args.yes and args.answers is None:
        return "--yes requires an answers file via --answers FILE"
    if args.answers is not None and not args.yes:
        return "--answers requires explicit --yes consent before any effects are applied"
    if args.json and not args.preflight:
        return "--json is only meaningful with --preflight"
    if args.preflight:
        for flag in ("yes", "no_start", "no_enable", "clear_lifecycle_latch", "use_existing"):
            if getattr(args, flag):
                return f"--{flag.replace('_', '-')} cannot be combined with --preflight"
    if args.clear_lifecycle_latch and args.answers is not None and args.yes:
        return "--clear-lifecycle-latch requires explicit interactive consent; refusing to erase retirement state in unattended mode"
    return None


def translate_facts(raw: host.HostFacts, caps: Capabilities) -> PreflightFacts:
    """Map bounded raw host inventory onto the pure decision inputs.

    Checks not observable before installation keep their conservative defaults;
    nothing here claims runtime policy compatibility.
    """
    return PreflightFacts(
        distro_id=raw.os_release.identifier or "",
        distro_version=raw.os_release.version_id,
        os_release_readable=raw.os_release.identifier is not None,
        foreign_host=raw.container.kind is not None,
        nixos=raw.nixos_detected is True,
        proxmox_evidence=raw.proxmox.detected is True,
        atomic=raw.mounts.atomic is True,
        dnf_available=shutil.which("dnf", path=FIXED_PATH) is not None,
        cgroup_shutdown_proven=True,
        noexec_paths=(),
        security_policy_unsafe=False,
        identity_available=True,
        python_available=caps.python_supported,
        pid1_systemd=caps.systemd_pid1 is True,
        linux_x86_64=caps.architecture in {"x86_64", "amd64"},
        supporting_facts=raw.proxmox.evidence + raw.container.evidence,
    )


def collect_facts(caps: Capabilities) -> PreflightFacts | None:
    """Collect decision-level host facts without running mutating probes."""
    try:
        raw = host.HostFacts.detect()
    except OSError:
        return None
    return translate_facts(raw, caps)


def evaluate_report(caps: Capabilities, cfg: config.Config) -> Preflight:
    """Run the pure preflight decision; no I/O, subprocesses, or effect runners."""
    return evaluate_preflight(
        collect_facts(caps), caps, cfg, has_no_new_install_action=False,
        gaming_admission_decision=None, both_mode_coverage_complete=False,
        unverified_acknowledged=False, proxmox_pvecluster_present=False,
    )


def report_to_dict(report: Preflight) -> dict[str, object]:
    """Stable JSON-shaped report; the human form renders the same fields."""
    return {
        "support": report.support,
        "readiness": report.readiness,
        "gaming": report.gaming,
        "proxmox_or_nixos_hypervisor": report.proxmox_or_nixos_hypervisor,
        "proxmox_pvecluster_present": report.proxmox_pvecluster_present,
        "gaming_integration_attempted": report.gaming_integration_attempted,
        "gaming_admission_decision": report.gaming_admission_decision,
        "required_evidence_missing": list(report.required_evidence_missing),
        "reason_codes": list(report.reason_codes),
        "unsafe_reasons": list(report.unsafe_reasons),
        "supporting_facts_hash": report.supporting_facts_hash,
        "effects": [
            {
                "paths": list(effect.paths),
                "units": list(effect.units),
                "accounts": list(effect.accounts),
                "protected_directories": list(effect.protected_directories),
                "requires_reboot": effect.requires_reboot,
            }
            for effect in report.effects
        ],
    }


def remediate(report: Preflight) -> tuple[str, ...]:
    """One-line remediations per non-ready outcome; never a bypass instruction."""
    items: list[str] = []
    if report.readiness == "REFUSED_UNSUPPORTED":
        items.append("host is unsupported by this installer; use a supported distribution or a native packaging channel")
    if report.readiness == "REFUSED_UNSAFE":
        items.append("unsafe host conditions must be resolved before install: " + ", ".join(report.unsafe_reasons))
    if report.readiness == "INCONCLUSIVE":
        items.append("required host evidence is missing or inconclusive; inspect os-release and systemd and retry")
    if report.readiness == "ACTION_REQUIRED":
        items.append("operator action is required; resolve compatibility acknowledgment or gaming coverage first")
    if report.gaming == "UNVERIFIED":
        items.append("gaming protection is not verified; unattended installation is refused")
    if report.gaming == "UNSUPPORTED":
        items.append("gaming integration has no dependable route on this host")
    if report.gaming == "CONSERVATIVE":
        items.append("gaming coverage is reduced-function: conservative fallback, not full idle-versus-gameplay coverage")
    return tuple(items)


def render_human(report: Preflight) -> str:
    """Human-readable report matching the JSON form field for field."""
    lines = ["preflight:",
             f"  support: {report.support}",
             f"  readiness: {report.readiness}",
             f"  gaming: {report.gaming}"]
    for effect in report.effects:
        lines.append("  effects:")
        if effect.paths:
            lines.append("    - paths: " + ", ".join(effect.paths))
        if effect.units:
            lines.append("    - units: " + ", ".join(effect.units))
        if effect.accounts:
            lines.append("    - accounts: " + ", ".join(effect.accounts))
        if effect.protected_directories:
            lines.append("    - protected directories: " + ", ".join(effect.protected_directories))
        if effect.requires_reboot:
            lines.append("    - requires reboot: yes")
    if report.required_evidence_missing:
        lines.append("  missing evidence: " + ", ".join(report.required_evidence_missing))
    if report.reason_codes:
        lines.append("  reasons: " + ", ".join(report.reason_codes))
    if report.unsafe_reasons:
        lines.append("  unsafe: " + ", ".join(report.unsafe_reasons))
    remediation = remediate(report)
    if remediation:
        lines.append("  remediation: " + "; ".join(remediation))
    return "\n".join(lines)


def render_report(report: Preflight, *, as_json: bool = False) -> str:
    """Render the preflight report; human and JSON forms share one source."""
    if as_json:
        return json.dumps(report_to_dict(report), sort_keys=True)
    return render_human(report)


def run_preflight(args: Arguments, caps: Capabilities) -> int:
    """Dry preflight: report only, no host mutation and no manager probe."""
    report = evaluate_report(caps, config.Config())
    print(render_report(report, as_json=args.json))
    return PREFLIGHT_EXIT[report.readiness]


def main(argv: Sequence[str] | None = None, *, geteuid: Callable[[], int] = os.geteuid) -> int:
    parser = argparse.ArgumentParser(prog="sparehash install")
    _ = parser.add_argument("--answers", type=Path)
    _ = parser.add_argument("--use-existing", action="store_true")
    _ = parser.add_argument("--clear-lifecycle-latch", action="store_true",
                            help="allow reinstall after a previous uninstall (root only)")
    _ = parser.add_argument("--preflight", action="store_true",
                            help="run read-only preflight and print a readiness report")
    _ = parser.add_argument("--json", action="store_true",
                            help="print the preflight report as JSON")
    _ = parser.add_argument("--yes", action="store_true",
                            help="unattended consent for the disclosed effects; requires --answers")
    _ = parser.add_argument("--no-start", action="store_true",
                            help="do not start the controller now; skip the interactive prompt")
    _ = parser.add_argument("--no-enable", action="store_true",
                            help="do not enable the controller at boot; skip the interactive prompt")
    _ = parser.add_argument("--bootstrap-created", action="append", metavar="PATH", help=argparse.SUPPRESS)
    args = parser.parse_args(argv, namespace=Arguments())
    option_error = _validate_options(args)
    if option_error is not None:
        print("sparehash: install: " + option_error, file=sys.stderr)
        return 2
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
        caps = Capabilities.detect(environ=ENV)
        if args.preflight:
            return run_preflight(args, caps)
        tree = Tree()
        preflight(tree, caps)
        existing = tree.path("/etc/sparehash/config.toml")
        current = None
        if existing.exists():
            _ = tree.read("/etc/sparehash/config.toml")
            current = wizard.load_answers(existing, caps)
        cfg = wizard.load_answers(args.answers, caps) if args.answers else wizard.run_interactive(caps, input, print, current=current)
        report = evaluate_report(caps, cfg)
        print(render_report(report))
        if args.answers is None:
            prompts = wizard._Prompts(input, print)
            if not prompts.boolean("Proceed with installation?", default=True):
                print("sparehash: install: declined", file=sys.stderr)
                return 1
        if report.readiness != "READY":
            print(f"sparehash: install: readiness is {report.readiness}; no effects applied", file=sys.stderr)
            return PREFLIGHT_EXIT[report.readiness]
        if args.answers is not None and report.gaming in {"UNVERIFIED", "UNSUPPORTED"}:
            print("sparehash: install: unattended installation requires verified gaming integration; "
                  "refusing to install without gaming protection", file=sys.stderr)
            return 2
        if not caps.cpuweight_idle:
            if systemd.probe_cpuweight_idle(ENV):
                caps = replace(caps, cpuweight_idle=True)
            else:
                print("capabilities: idle cgroup weights unavailable; retaining Nice/SCHED_IDLE")
        execute(tree, caps, cfg, use_existing=args.use_existing, bootstrap_created=tuple(args.bootstrap_created or ()),
                interactive=(args.answers is None),
                boot=(False if args.no_enable else None),
                now=(False if args.no_start else None))
        return 0
    except KeyboardInterrupt:
        print("sparehash: interrupted; pending ledger retained for inspection", file=sys.stderr)
        return 130
    except EOFError:
        print("sparehash: install: no input available; use --answers FILE --yes for unattended installation", file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError, systemd.CollisionError, systemd.UnitError, CollisionError) as error:
        print(f"sparehash: install: {error}", file=sys.stderr)
        return 1
    finally:
        _ = signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
