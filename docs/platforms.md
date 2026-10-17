# Platform support and preflight

SpareHash targets Linux x86_64 with systemd as PID 1. Not every Linux
distribution is tested or supported. This page explains what the installer
checks, what each support tier means, and how to read the preflight report
before attempting installation.

## Support tiers

The installer classifies every host into one of three support levels. The
preflight report (`sudo ./install.sh --preflight`) shows the tier before any
changes are made.

| Tier | Meaning |
|---|---|
| **VALIDATED** | The exact image and version has passed disposable-VM acceptance with real systemd, cgroups, and synthetic loopback mining. Configuration, ownership, and lifecycle behave as documented. |
| **UNVERIFIED** | The host matches a known distribution family but no disposable-VM evidence exists for this specific image or version. The installer may work, but compatibility is not certified. Technical compatibility is not the same as certified support. |
| **UNSUPPORTED** | The host is known to be incompatible with this imperative installer. Installation is refused. |

### VALIDATED platforms

These distributions and versions have passed disposable-VM acceptance:

| Distribution | Versions | Notes |
|---|---|---|
| Debian | 12 (bookworm) | systemd 252+, Python 3.11 |
| Ubuntu | 22.04 LTS, 24.04 LTS | systemd 249+, Python 3.11+ |
| Linux Mint | 21.x, 22.x | Based on Ubuntu LTS; same systemd/Python |

Each VALIDATED entry means: install, reinstall, start/stop, ownership, idle
scheduling, controller loss, and retired uninstall all passed on that exact
image. Passes are not copied between versions or derivatives.

### UNVERIFIED platforms

These distributions are technically similar but have not completed disposable-VM
acceptance. The installer may refuse or may work; either outcome is honest.

| Distribution | Status | Notes |
|---|---|---|
| Fedora Workstation | UNVERIFIED | systemd and Python present; no VM evidence |
| Fedora Silverblue / Kinoite | UNVERIFIED | Atomic layout with `/opt` and `/usr/local` redirects; SELinux enforcing; no VM evidence |
| Bazzite-Deck | UNVERIFIED | Atomic layout with enforcing SELinux; gaming integration not yet proven |
| Other Atomic/bootc images | UNVERIFIED | Redirect grammar is recognized; no VM evidence |
| openSUSE Tumbleweed / Leap | UNVERIFIED | systemd present; no VM evidence |
| Arch Linux | UNVERIFIED | Rolling release; no fixed version to certify |

An UNVERIFIED host is not a promise that installation will succeed. The preflight
report explains what is missing. If you choose to proceed on an UNVERIFIED host,
you accept that behavior is not guaranteed and recovery may require manual
intervention.

### UNSUPPORTED platforms

These hosts are refused by the installer. The refusal is deliberate and not a
bug.

| Platform | Reason |
|---|---|
| **NixOS** | The imperative installer cannot operate within the Nix store model. Users, secrets, and activation are managed by the Nix evaluator, not by shell scripts. A future native NixOS module may deploy the runtime directly; this installer is not that path. |
| **Proxmox VE** | Proxmox is a hypervisor management platform, not a general-purpose Linux host. The installer detects Proxmox evidence (`pve-manager` package or `pveversion`/`pvesh` helpers plus `pmxcfs` or node evidence) and refuses. Running a mining manager on a hypervisor host introduces scheduling and thermal risks that this installer does not mitigate. |

The installer does not offer a `--force` flag to bypass these refusals. If your
host is refused, do not attempt to work around the check by editing os-release,
masking packages, or modifying system identity. Use a supported distribution or
wait for a native packaging channel.

## Preflight report

The preflight report is a read-only compatibility check. It makes no persistent
host changes, does not bootstrap an installed CLI, and does not run mutating
probes. Run it before any installation attempt:

```
sudo ./install.sh --preflight
```

The report shows:

- **support**: VALIDATED, UNVERIFIED, or UNSUPPORTED
- **readiness**: READY, ACTION_REQUIRED, REFUSED_UNSUPPORTED, REFUSED_UNSAFE, or INCONCLUSIVE
- **gaming**: FULL, CONSERVATIVE, UNVERIFIED, or UNSUPPORTED
- **effects**: paths, units, accounts, and protected directories the installer would create or modify
- **missing evidence**: what host information is incomplete or unavailable
- **reasons**: why the installer reached this decision
- **unsafe**: specific unsafe conditions that must be resolved
- **remediation**: one-line guidance per non-ready outcome

Add `--json` for machine-readable output:

```
sudo ./install.sh --preflight --json
```

The JSON form contains the same fields as the human-readable form. Exit codes:

| Exit code | Meaning |
|---|---|
| 0 | READY; installation may proceed |
| 1 | ACTION_REQUIRED; operator must resolve compatibility acknowledgment or gaming coverage |
| 2 | REFUSED_UNSUPPORTED or REFUSED_UNSAFE; installation is refused |
| 3 | INCONCLUSIVE; required host evidence is missing |

A non-zero exit does not mean the host is broken. It means the installer cannot
proceed safely without more information or operator action.

### What preflight does not do

Preflight does not:

- Install or bootstrap any CLI
- Create users, groups, or directories
- Start or enable services
- Download or verify XMRig
- Modify configuration or SELinux labels
- Run mutating probes or transient systemd units

Preflight is a dry run. It collects read-only host facts and evaluates them
against the installer's compatibility rules. The report is the same whether you
run it once or ten times.

## Consent and unattended installation

The installer requires explicit operator consent before making any persistent
changes. Interactive installation walks through a wizard that asks for wallet,
pool, CPU mode, thermal guard, hugepages, and gaming users, then asks:

- `Start SpareHash automatically at boot? [Y/n]:`
- `Start SpareHash controller now? [Y/n]:`

Enter accepts Yes for each. No to boot startup disables the controller unit; No
to starting now leaves a fresh installation inactive.

For unattended installation, supply a TOML answers file and explicit consent:

```
sudo ./install.sh --answers answers.toml --yes
```

The `--yes` flag is required with `--answers`. It confirms that you have reviewed
the answers file and approve the effects. Without `--yes`, the installer refuses
to proceed. This is not a bug; it is a safety barrier.

The answers file uses the same TOML format as `/etc/sparehash/config.toml`. See
the README for all keys and defaults. The `[power]` section is also accepted in
answers files.

### Testing and admin choices

For testing or staged deployment, you can suppress automatic startup:

```
sudo ./install.sh --answers answers.toml --yes --no-start --no-enable
```

- `--no-start`: install but do not start the controller now
- `--no-enable`: install but do not enable the controller at boot

These flags are useful for QA harnesses that need to stage service-only
loopback inputs before any controller start, or for administrators who want to
review configuration before enabling the service. The installer completes
successfully; the controller remains stopped and disabled until you run
`sudo sparehash start`.

## Dependency remediation

The installer does not invoke package managers automatically. If required system
tools are missing, the preflight report lists them in the `remediation` field.
You must install them yourself using your distribution's package manager.

For example, if the report says:

```
remediation: required host evidence is missing; install systemd-python or equivalent
```

You would run:

```
sudo apt install python3-systemd    # Debian/Ubuntu
sudo dnf install python3-systemd    # Fedora
```

The installer does not know your package manager preferences or repository
configuration. It reports what is missing; you decide how to provide it.

## Canonical and visible paths

SpareHash uses two path prefixes:

- **Logical (canonical)**: `/opt/sparehash`, `/opt/xmrig`, `/usr/local/bin`
- **Physical (visible)**: the actual on-disk location after OS redirects

On conventional distributions, logical and physical paths are identical. On
Atomic distributions (Fedora Silverblue, Bazzite, Kinoite), the filesystem
redirects `/opt` to `/var/opt` and `/usr/local` to `/var/usrlocal`. The
installer recognizes these redirects and operates on the physical paths while
receipting the logical identities.

This means:

- Configuration files are at `/etc/sparehash/config.toml` (no redirect)
- The installed Python package is at `/opt/sparehash/src/sparehash/` (physical: `/var/opt/sparehash/src/sparehash/` on Atomic)
- The XMRig binary is at `/opt/xmrig/current/xmrig` (physical: `/var/opt/xmrig/current/xmrig` on Atomic)
- The CLI entry point is at `/usr/local/bin/sparehash` (physical: `/var/usrlocal/bin/sparehash` on Atomic)

The installer never creates, modifies, or removes OS-level redirect links. It
follows the documented redirect grammar and operates within the redirected
trees. Receipts record the logical identity; removal operates on the physical
path.

Do not manually edit or relink these paths. Do not attempt to "fix" Atomic
redirects by creating symlinks or bind mounts. The installer's path validation
will refuse unsafe targets.

## What the installer does not do

The installer does not:

- Change DNS, firewall, SELinux mode, AppArmor, sysctl, or bootloader settings
- Enable MSR optimization, GPU mining, or 1GiB hugepages
- Install or configure a network or firewall policy
- Mine as root
- Provide public pool presets or calculate mining profitability
- Automatically layer packages on Atomic distributions
- Disable or bypass SELinux policy
- Remove foreign files, units, or identities that it did not create
- Clear a manual pause or independent safety blocker during upgrade

If your environment requires DNS allowlisting, SELinux policy adjustments, or
firewall changes, you must make those changes yourself. The installer reports
what it would do; it does not make system policy decisions for you.

## Recovery and reinstallation

If installation fails partway through, the installer leaves a durable ledger at
`/var/lib/sparehash/` recording what was created. Do not manually delete files
from `/opt/sparehash`, `/etc/sparehash`, or `/var/lib/sparehash` to "clean up."
Inspect the ledger and use the recovery command shown in the error message.

After a successful uninstall, the system is retired. A lifecycle latch at
`/var/lib/sparehash/lifecycle.json` prevents accidental reinstallation. To
reinstall after uninstall, explicitly opt in:

```
sudo ./install.sh --clear-lifecycle-latch
```

This clears the retained uninstall lease and retirement latch. An active update
hold or an unreadable lease is not cleared; a failure leaves the system retired.
Never delete lifecycle records manually to bypass retirement. Inspect and
resolve any partial-uninstall failure before opting into reinstall.

If the installed CLI (`/usr/local/bin/sparehash`) is absent mid-recovery, the
error message shows a stable source recovery entrypoint. Use that command; do
not attempt to reconstruct the CLI path manually.

## Gaming integration tiers

The preflight report shows one of four gaming coverage levels:

| Tier | Meaning |
|---|---|
| **FULL** | Validated idle-versus-gameplay distinction in both Steam Gaming Mode and KDE. Mining proceeds while Steam is idle; mining stops when supported native or Proton games are running. No per-game setup required. |
| **CONSERVATIVE** | Session-wide fallback. Mining is inhibited during any covered graphical session, including Steam UI idle and KDE idle. This is reduced-function coverage, not full idle-versus-gameplay distinction. |
| **UNVERIFIED** | Gaming protection is not verified on this host. Unattended installation is refused. |
| **UNSUPPORTED** | No dependable gaming integration route exists on this host. Installation is refused or requires explicit acknowledgment of reduced coverage. |

The `gaming.integration` configuration key controls the requested coverage:

- `auto` (default): request FULL coverage; report ACTION_REQUIRED if unadmitted
- `conservative`: select limited fallback explicitly
- `gamemode`: conventional legacy GameMode coverage only

FULL coverage requires a validated producer contract and demonstrated lifecycle
invariants on the target host. CONSERVATIVE coverage inhibits all covered
graphical sessions but does not distinguish idle Steam from active gameplay.

See `docs/gaming-integration-contract.md` for the full admission contract and
latency objectives.

## Further reading

- `README.md`: quick start, CLI reference, configuration, safety model
- `docs/architecture.md`: design invariants and module boundaries
- `docs/gaming-integration-contract.md`: gaming admission and coverage tiers
- `qa/README.md`: disposable-VM acceptance procedures
- `SECURITY.md`: security policy and vulnerability reporting
