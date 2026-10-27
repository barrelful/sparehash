# Experimental v0.2 platforms, installation and removal

## Evidence and support labels

Linux x86_64, Python >=3.11 and systemd PID 1 are required. Runtime Python is
standard-library-only: no `python3-systemd`, Lutris or GameMode dependency.

| Evidence | Tested image | Scope |
| --- | --- | --- |
| Ubuntu 24.04 | `ubuntu-noble-20260926-v1`, systemd 255 / Python 3.12.3 | Synthetic install/operation/removal/purge/reinstall/reboot |
| Bazzite 44 | `bazzite-kde-44-20261012-v1`, systemd 259 / Python 3.14.8 | Same, with SELinux Enforcing and captured kernel audits |

See [S3 results](development/s3-lifecycle-reliability.md). These checks used a
non-hashing, root-trusted external fixture and file-backed sensor values. They
do not certify actual upstream mining performance, arbitrary hardware, real
native/Proton gameplay or KDE/Gaming Mode behavior. Those remain owner testing
after an automated/VM-gated experimental RC. No RC is declared here.

Preflight `VALIDATED` means os-release ID/version matches Ubuntu `24.04` or
Bazzite `44`; it does not authenticate the exact baseline image or all hardware
with that label. `UNVERIFIED` covers every other release (including Debian,
Mint, Ubuntu 22.04, other Fedora/Atomic systems, Deck variants, Arch/openSUSE).
Unknown versions do not inherit validation from a derivative or family.
`UNSUPPORTED` covers detected NixOS and Proxmox. There is no force bypass.

## Read-only preflight and exact installer exits

```sh
sudo ./install.sh --preflight
sudo ./install.sh --preflight --json
```

Preflight reads host facts and reports support, readiness, gaming posture,
planned effects, missing evidence, reason codes and remediation. It does not
install anything, download XMRig, create identities or transient units, or start
services. Human and JSON forms describe the same decision.

| Exit | Meaning |
| --- | --- |
| 0 | Preflight READY, or successful installation |
| 2 | Preflight ACTION_REQUIRED; syntax/option/consent refusal; deferred workflow; retired system without explicit reinstall consent; unavailable interactive input |
| 1 | Preflight REFUSED_UNSUPPORTED, REFUSED_UNSAFE or INCONCLUSIVE; non-root install; host/probe/receipt/publication failure or declined installation |
| 130 | Installer interrupted by the user/signal handler |

A failed installation can leave receipted partial state. Exit 1 is not a promise
that no earlier approved effects happened; preserve the ledger and logs. No
preflight report substitutes for actual sandbox/runtime safety checks.

## Consent, accounts and permissions

```sh
sudo ./install.sh                         # interactive consent and startup choices
sudo ./install.sh --answers answers.toml --yes
sudo ./install.sh --answers answers.toml --yes --no-start --no-enable
```

`--answers` without `--yes` refuses before effects, except read-only preflight.
Interactive setup asks about wallet/pool, CPU, optional thermal/hugepages,
best-effort detection and optional hook accounts. Boot enablement and starting
now are separate choices, both default Yes; unattended installs use those defaults
unless `--no-start` / `--no-enable` are supplied. The miner unit remains static,
never directly enabled by the installer. `sparehash start` enables/starts the
controller; `stop` stops both services but does not disable future boot startup.

The installer creates/reuses **groups `sparehash` and `xmrig` and a system user
`xmrig`**, not a `sparehash` user. `xmrig` has no home creation and a nologin shell;
the controller runs as root, the miner only as unprivileged `xmrig`.

| Object | Ownership / mode |
| --- | --- |
| Public `config.toml` | root:`sparehash`, 0644 |
| API-bearing `xmrig.json` | root:`sparehash`, 0640 |
| Miner fallback `xmrig.runtime.json` | root:`xmrig`, 0640 |
| Controller socket | root:`sparehash`, 0660 in protected `/run/sparehash` |
| Installed source/CLI/units | Root-owned; unprivileged callers cannot alter them |

Group access is powerful: trusted peers may pause/resume and send gaming hooks.
Public status requires root. New gaming setup does not add user memberships,
install packages or edit GameMode INIs. Grant access only deliberately. Required
system tools are distribution prerequisites; there is no automatic package layer,
MSR helper, DNS/firewall change or SELinux policy modification.

## Gaming policy

`gaming.enabled=true` selects the sole Lutris-derived best-effort Steam reaper
scanner. `gaming.users` controls verified hook accounts, not scanner coverage;
an empty list is valid. No Steam client, log, session bus, Lutris or GameMode is
required at setup. Successful no-match scans may release gaming inhibition;
genuine faults, exhausted bounds and stale scans inhibit. Polling is one second,
freshness two seconds, restart dwell defaults to 60 seconds; other blockers remain
independent. Non-Steam/unrecognised games or surviving children may be missed.
Low priority is not a zero-contention guarantee.

Old `integration="auto"` and `steam_log=false` are deprecated readable no-ops,
omitted on save. Other modes or `steam_log=true` refuse with migration guidance:
review the limits, remove those keys and explicitly choose enabled or disabled.
See [the gaming contract](gaming-integration-contract.md) for fixed failure
reasons, timing objectives, status and attribution.

## Paths and removal

Logical roots are `/opt/sparehash`, `/opt/xmrig`, `/usr/local/bin`,
`/etc/sparehash` and `/var/lib/sparehash`. Atomic OS redirects are pinned through
the existing trusted layout grammar (`/opt` → `/var/opt`, `/usr/local` →
`/var/usrlocal` on the tested Bazzite image); the installer never changes them.
Source is under `/opt/sparehash/src`; managed XMRig, when installed, is under
`/opt/xmrig/current`. `--use-existing` explicitly permits a root-protected,
unprivileged-probed external artifact; it is not adopted as owned for removal.

```sh
sudo sparehash uninstall
sudo sparehash uninstall --purge
```

Removal is network-free and needs no controller IPC. It retires first, stops and
disables the controller, requires empty miner-cgroup proof and removes only
receipt-owned matching artifacts/identities. Changed/unrecorded content refuses;
foreign files and externally owned XMRig survive. Owned legacy GameMode removal
retains account/path and receipt safeguards.

Default removal retains configuration/manual state. `--purge` additionally
removes unchanged receipt-owned configuration, **not all state**, journals,
retirement/receipt records or foreign files. Removal returns 0 on success, 1 on
safe failure/partial removal, 2 when not root. Preserve records after failure.

After completed removal, explicit **interactive** reinstall consent uses
`sudo ./install.sh --clear-lifecycle-latch`, plus `--operation-id ID` only if the
printed retirement guidance requires it. An active update hold, mismatched ID
or corrupt record is not cleared. Never delete lifecycle files to bypass this.

## Deferred workflows

No supported upgrade/rollback, in-place reconfiguration, benchmark or native
RPM/DEB/Nix/Flatpak channel is delivered. `update`, `complete-upgrade`, `config edit`,
`benchmark` and installer `--upgrade`, `--recover`, `--complete-upgrade`,
`--reconfigure` refuse with exit 2 before inspection/effects. Preserved internals
are research, not another route. Keep any old migration hold/journal and contact
the maintainer; see [upgrade status](upgrade-v020.md).
Ordinary installation also checks the protected CLI read-only and refuses an
existing installation before retirement clearing or capability detection.
Completed removal deletes that CLI, preserving the explicit reinstall path.
