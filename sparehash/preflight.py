"""Pure host compatibility and readiness decisions."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from .config import Config, GamingIntegration
from .sysdetect import Capabilities

Support = Literal["VALIDATED", "UNVERIFIED", "UNSUPPORTED"]
Readiness = Literal["READY", "ACTION_REQUIRED", "REFUSED_UNSUPPORTED", "REFUSED_UNSAFE", "INCONCLUSIVE"]
Gaming = Literal["FULL", "CONSERVATIVE", "UNVERIFIED", "UNSUPPORTED"]
GamingDecision = Literal["ADMISSIBLE_CANDIDATE", "NO_DEPENDABLE_ROUTE", "BLOCKED_ENVIRONMENT"]


@dataclass(frozen=True, slots=True)
class HostFacts:
    """Read-only host evidence already collected by a host detector."""

    distro_id: str
    distro_version: str | None
    os_release_readable: bool = True
    foreign_host: bool = False
    nixos: bool = False
    proxmox_evidence: bool = False
    atomic: bool = False
    dnf_available: bool = False
    cgroup_shutdown_proven: bool = True
    noexec_paths: tuple[str, ...] = ()
    security_policy_unsafe: bool = False
    identity_available: bool = True
    python_available: bool = True
    pid1_systemd: bool = True
    linux_x86_64: bool = True
    supporting_facts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PreflightEffect:
    """Description of a possible effect; this value never performs it."""

    paths: tuple[str, ...] = ()
    units: tuple[str, ...] = ()
    accounts: tuple[str, ...] = ()
    protected_directories: tuple[str, ...] = ()
    requires_reboot: bool = False


@dataclass(frozen=True, slots=True)
class Preflight:
    support: Support
    readiness: Readiness
    gaming: Gaming
    proxmox_or_nixos_hypervisor: bool
    proxmox_pvecluster_present: bool
    gaming_integration_attempted: bool
    gaming_admission_decision: GamingDecision | None
    required_evidence_missing: tuple[str, ...]
    reason_codes: tuple[str, ...]
    unsafe_reasons: tuple[str, ...]
    supporting_facts_hash: str | None
    effects: tuple[PreflightEffect, ...]


def evaluate(
    facts: HostFacts | None,
    caps: Capabilities,
    config: Config,
    has_no_new_install_action: bool,
    *,
    gaming_admission_decision: GamingDecision | None = None,
    both_mode_coverage_complete: bool = False,
    unverified_acknowledged: bool = False,
    proxmox_pvecluster_present: bool = False,
) -> Preflight:
    """Evaluate compatibility without I/O, subprocesses or effect runners."""
    if facts is None:
        return Preflight(
            "UNVERIFIED", "INCONCLUSIVE", "UNVERIFIED", False,
            proxmox_pvecluster_present, False, gaming_admission_decision,
            ("host_facts",), ("HostFactsMissing",), (), None, (),
        )

    digest = sha256("\0".join(facts.supporting_facts).encode()).hexdigest()
    missing = tuple(name for name, present in (
        ("os_release", facts.os_release_readable), ("python", facts.python_available),
        ("systemd_pid1", facts.pid1_systemd), ("cgroup_shutdown_proof", facts.cgroup_shutdown_proven),
        ("identity", facts.identity_available), ("architecture", facts.linux_x86_64),
        ("systemd", caps.systemd_version is not None),
    ) if not present)
    unsafe = tuple(
        (["CgroupShutdownUnproven"] if not facts.cgroup_shutdown_proven else [])
        + (["NoExecPath"] if facts.noexec_paths else [])
        + (["SecurityPolicyUnsafe"] if facts.security_policy_unsafe else [])
        + (["PythonUnavailable"] if not facts.python_available else [])
        + (["SystemdNotPid1"] if not facts.pid1_systemd else [])
        + (["IdentityUnavailable"] if not facts.identity_available else [])
        + (["AtomicDnfNotAvailable"] if facts.atomic and not facts.dnf_available and not has_no_new_install_action else [])
    )
    recognized_unsupported = facts.nixos or facts.proxmox_evidence
    proxmox_nixos = facts.nixos or facts.proxmox_evidence
    support: Support = "UNSUPPORTED" if recognized_unsupported else (
        "UNVERIFIED" if facts.distro_id.casefold() not in {"bazzite", "debian", "ubuntu", "linuxmint"} else "VALIDATED"
    )
    readiness: Readiness
    if recognized_unsupported:
        readiness = "REFUSED_UNSUPPORTED"
    elif unsafe:
        readiness = "REFUSED_UNSAFE"
    elif not facts.os_release_readable or facts.foreign_host or missing:
        readiness = "INCONCLUSIVE"
    else:
        readiness = "ACTION_REQUIRED"

    integration = config.gaming.enabled
    if not integration:
        gaming: Gaming = "UNVERIFIED"
    elif gaming_admission_decision == "ADMISSIBLE_CANDIDATE" and both_mode_coverage_complete:
        gaming = "FULL"
    elif gaming_admission_decision in {"NO_DEPENDABLE_ROUTE", "BLOCKED_ENVIRONMENT"}:
        gaming = "UNSUPPORTED"
    elif gaming_admission_decision is None and facts.distro_id.casefold() == "bazzite":
        # An explicit non-auto integration is the operator's acknowledgement of reduced
        # coverage. Only the default auto posture on Bazzite has to wait for an admission
        # decision, which the environment currently cannot produce.
        gaming = (
            "UNVERIFIED" if config.gaming.integration is GamingIntegration.AUTO
            else "CONSERVATIVE"
        )
    else:
        gaming = "CONSERVATIVE"

    if readiness == "ACTION_REQUIRED":
        if support == "UNVERIFIED" and not unverified_acknowledged:
            readiness = "ACTION_REQUIRED"
        elif facts.atomic and not facts.dnf_available and has_no_new_install_action:
            readiness = "REFUSED_UNSAFE"
        elif facts.distro_id.casefold() == "bazzite" and gaming == "UNVERIFIED":
            readiness = "ACTION_REQUIRED"
        elif support == "VALIDATED":
            readiness = "READY"

    reasons = tuple(unsafe) + tuple(missing) + (("UnverifiedCompatibilityAcknowledgmentRequired",)
        if support == "UNVERIFIED" and not unverified_acknowledged else ())
    if facts.atomic and not facts.dnf_available:
        reasons += ("AtomicDnfNotAvailable",)
    effects = () if has_no_new_install_action else (PreflightEffect(
        paths=("/opt/sparehash", "/etc/sparehash", "/var/lib/sparehash"),
        units=("sparehash.service", "sparehash-xmrig.service"),
        accounts=("sparehash", "xmrig"), protected_directories=("/etc/sparehash",),
    ),)
    return Preflight(
        support, readiness, gaming, proxmox_nixos, proxmox_pvecluster_present,
        integration, gaming_admission_decision, missing, reasons, unsafe, digest, effects,
    )
