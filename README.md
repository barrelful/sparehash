# SpareHash

> **Experimental v0.2.1 software.** SpareHash is a young system-level project.
> It has been tested on a limited set of Linux distributions. Review this
> README, `SECURITY.md`, `docs/platforms.md`, and the limitations section
> before installing on a machine you care about.

Release scope and limitations are in the
[v0.2.0 release notes](docs/release-0.2.0.md); the
[candidate report](docs/release-0.2-candidate.md) preserves historical evidence.
For a supervised installation
and real-game tests, use the [owner checklist](docs/testing-0.2-owner.md).

SpareHash is a CPU-only Monero (RandomX) miner manager for Linux x86_64. It runs
[XMRig](https://xmrig.com/) in the background using spare CPU cycles, and
uses best-effort Steam detection and strict thermal/power/manual blockers to
pause mining. Unrecognised games can be missed.

SpareHash is CPU-only. There is no GPU mining, no MSR helper, no kernel module,
and no network or firewall policy change. The miner runs as a dedicated
unprivileged `xmrig` user, never as root.

Licensed under GPL-3.0-or-later. See `LICENSE` and `THIRD_PARTY_NOTICES.md`.

## What SpareHash does and does not do

- **Does:** install and manage an unprivileged XMRig service that mines only
  when every safety blocker is clear.
- **Does:** pause mining on battery, on best-effort Steam game detection, above a thermal
  threshold, or when you run `sparehash pause`.
- **Does:** use low-priority kernel scheduling (`Nice=19`, `SCHED_IDLE`, cgroup
  idle weights where supported). This is not a promise of zero contention.
- **Does:** verify XMRig downloads against a pinned GPG fingerprint and
  SHA-256 checksum.
- **Does:** keep the XMRig HTTP API on `127.0.0.1` only.
- **Does not:** mine as root.
- **Does not:** change DNS, firewall, SELinux, AppArmor, sysctl, or bootloader
  settings.
- **Does not:** enable MSR optimisation, GPU mining, or 1GiB hugepages in v0.2.
- **Does not:** provide public pool presets or calculate mining profitability.

## Supported platforms

SpareHash classifies every host into one of three support tiers. Run `sudo ./install.sh --preflight` to see the tier before any changes are made. See `docs/platforms.md` for the full support matrix, preflight behavior, and platform-specific guidance.

| Tier | Meaning |
|---|---|
| **VALIDATED** | Matches a tested release line; evidence is limited to the named VM images |
| **UNVERIFIED** | Known distribution family but no VM evidence for this version |
| **UNSUPPORTED** | Known incompatible; installation refused (NixOS, Proxmox VE) |

**Synthetic lifecycle evidence:** Ubuntu 24.04 on `ubuntu-noble-20260926-v1`
and Bazzite 44 on `bazzite-kde-44-20261012-v1`. Install/removal/reinstall/reboot,
permissions and synthetic safety callbacks passed; see the
[acceptance record](docs/release-0.2-candidate.md#fresh-owner-requested-acceptance-at-b18e1c8). Real native/Proton
gameplay, KDE/Gaming Mode and hardware validation remain pending owner testing.

**UNVERIFIED:** Debian, Mint, Ubuntu 22.04 and other releases, Fedora Workstation,
Silverblue/Kinoite, Bazzite-Deck and other Atomic images, openSUSE and Arch.
Preflight's VALIDATED label means the release ID matches a tested release line,
not that it authenticated this exact image or certified all derivatives/hardware.

**UNSUPPORTED:** NixOS (imperative installer cannot operate within the Nix store model), Proxmox VE (hypervisor host; scheduling and thermal risks not mitigated).

Requirements:

- Linux x86_64
- systemd as PID 1 (tested: Ubuntu systemd 255; Bazzite systemd 259)
- Python 3.11 or newer
- No additional Python packages required at runtime

## Preflight check

Before installing, run a read-only preflight to see compatibility, support tier, gaming coverage, and planned effects:

```
sudo ./install.sh --preflight
```

The report shows support (VALIDATED/UNVERIFIED/UNSUPPORTED), readiness (READY/ACTION_REQUIRED/REFUSED_UNSUPPORTED/REFUSED_UNSAFE/INCONCLUSIVE), gaming detection (BEST_EFFORT/DISABLED), effects (paths, units, accounts, protected directories), missing evidence, reasons, unsafe conditions, and remediation guidance. Gaming readiness is not proof of gameplay coverage. Add `--json` for machine-readable output:

```
sudo ./install.sh --preflight --json
```

Preflight makes no persistent host changes. It does not bootstrap a CLI, create
users, start services or download XMRig. Exit codes: **0 READY, 2 ACTION_REQUIRED,
1 REFUSED_UNSUPPORTED/REFUSED_UNSAFE/INCONCLUSIVE**. Syntax/consent/deferred-mode
refusals return 2. See `docs/platforms.md` for the full installer exit contract.

## Quick start

### Linux Mint and other UNVERIFIED platforms (v0.2.1)

v0.2.0 cannot complete installation on UNVERIFIED platforms; v0.2.1 fixes the
compatibility-acknowledgment path. Mint remains **UNVERIFIED**, not Ubuntu-validated.
Review `docs/platforms.md`, then explicitly acknowledge that experimental status:

```sh
sudo ./install.sh --preflight --acknowledge-unverified
sudo ./install.sh --acknowledge-unverified --no-start --no-enable
```

Without `--acknowledge-unverified`, preflight/install still returns 2. This flag
does not override unsupported platforms, unsafe/missing capabilities, ownership
checks or existing-install refusal. Unattended installs still require separate
`--answers FILE --yes` consent. There is no supported in-place upgrade; complete
receipt-aware removal before reinstalling, even if old configuration is unwanted.

Select the release source first (use a separate checkout if you have local work):

```sh
git clone --branch v0.2.1 --depth 1 https://github.com/barrelful/sparehash.git
cd sparehash
sudo ./install.sh --preflight
```

For a fresh installation, the interactive installer installs the required files:

```
sudo ./install.sh
```

The installer walks you through an interactive setup: wallet address, pool
host/port, CPU mode, thermal guard, hugepages, and gaming users. It creates the
`xmrig` system user and the `xmrig`/`sparehash` groups (no `sparehash` user), selects the latest supported XMRig
release from GitHub and verifies it (pinned GPG fingerprint, SHA-256 checksum),
or reuses an already verified managed installation, writes configuration to
`/etc/sparehash/`, and installs two systemd units. It then asks:

- `Start SpareHash automatically at boot? [Y/n]:`
- `Start SpareHash controller now? [Y/n]:`

Enter accepts Yes for each. No to boot startup disables the controller unit;
No to starting now leaves a fresh installation inactive. An existing installation
is refused; reinstall is explicit after completed removal, not an in-place update.
The installer never enables or starts
the miner directly. Its final summary reports boot startup, controller activity,
and whether mining is blocked (or unknown if controller status is unavailable).

For unattended installs, supply a TOML answers file and explicit `--yes`
consent for the disclosed effects:

```
sudo ./install.sh --answers answers.toml --yes
```

`--answers` without `--yes` refuses before any effect is applied. `--no-start`
and `--no-enable` skip the two startup prompts (defaulting each to No) so QA or
an operator can stage a service without starting the controller.

For a cautious first experiment, use the same installer with
`sudo ./install.sh --no-start --no-enable`. Starting without enabling future boot
startup is then an explicit `sudo systemctl start sparehash.service` choice;
`sparehash start` opts into both.

A dry, read-only preflight reports support, readiness, and gaming coverage
without changing the host, in human or `--json` form:

```
sudo ./install.sh --preflight
sudo ./install.sh --preflight --json
```

A `READY` report exits 0; `ACTION_REQUIRED` exits 2; refused or inconclusive
outcomes exit 1. In `--answers` mode, both startup choices default to Yes
without reading stdin: the controller is enabled at boot and started
immediately. If you declined startup interactively, you can enable and start it
later with:

```
sudo sparehash start
```

Check status:

```
sudo sparehash status
sudo sparehash status --json
```

Mining does not begin instantly. The controller evaluates every blocker, waits
for the configured minimum dwell (default 60 seconds), and only then requests a
freshly authorized miner start.

## CLI reference

Every public command:

| Command | Flags | Requires root | Description |
|---|---|---|---|
| `install` | `--answers FILE`, `--use-existing`, `--clear-lifecycle-latch`, `--operation-id ID`, `--no-start`, `--no-enable`, `--preflight`, `--json`, `--yes` | yes | Fresh install or explicit reinstall after completed removal; not an in-place upgrade |
| `complete-upgrade` | `OPERATION_ID` | no effects | Deferred; exits 2 and retains journals/holds |
| `uninstall` | `--purge` | yes | Remove SpareHash; configuration retained unless `--purge` |
| `status` | `--json` | yes | Show controller, miner, blockers, power, thermal, hugepages, gaming, API |
| `start` | | yes | Enable and start the controller (`systemctl enable --now`) |
| `stop` | | yes | Stop controller and miner (does not disable; reboot can resume) |
| `pause` | | no | Manually pause mining via controller IPC |
| `resume` | | no | Clear manual pause (mining resumes only when all blockers are clear) |
| `config show` | | no | Print current public configuration as JSON |
| `config edit` | | yes | Unsupported in experimental v0.2.0; exits 2 without effects |
| `logs` | `-f`, `--follow` | no | Stream the last 100 lines from both unit journals |
| `benchmark` | | yes | Unsupported in experimental v0.2.0; exits 2 without effects |
| `update` | | no effects | Deferred in experimental v0.2.0; exits 2 without effects; no alternate upgrade path |
| `gaming-start` | | no | Signal that a configured user started gaming |
| `gaming-end` | | no | Signal that a configured user stopped gaming |
| `debug set-blocker` | `NAME` | yes | Force a named blocker active (debugging only) |
| `debug clear-blocker` | `NAME` | yes | Clear a named blocker (debugging only) |

Internal commands (`__controller__`, `__cleanup__`, `__authorize_miner__`,
`__miner_exec__`, `authorize-start`) are invoked by systemd units only. They are
not user-facing.

## Configuration

The public configuration lives at `/etc/sparehash/config.toml`. Use the installer
wizard or an answers file to configure installation; `config show` is read-only.
`config edit` is unsupported in v0.2.0. All keys and their defaults:

```toml
version = 1
wallet = ""
donate_level = 1

[pool]
host = ""
port = 3333
tls = false
worker = "sparehash"

[cpu]
mode = "spare"               # spare | spare-capped | custom
max_threads_percent = 75     # 1..100
custom_json = ""             # required when mode = "custom"; must be a JSON object

[thermal]
enabled = false
pause_c = 85                 # pause mining at or above this temperature
resume_c = 75                # resume when cooled below this (must be < pause_c)

[power]
maintenance_mode = false     # opt-in AC maintenance discharge exception, at most 1W

[gaming]
enabled = true
users = []                   # optional verified hook accounts; scanner needs none

[hugepages]
enabled = false
pages = 1280                 # 1280 x 2MiB = 2560 MiB reserved

[api]
port = 38087                 # XMRig HTTP API, bound to 127.0.0.1 only

[controller]
min_dwell_seconds = 60       # minimum time between state transitions
reconcile_seconds = 30       # how often the controller re-evaluates blockers

[xmrig]
binary = "/opt/xmrig/current/xmrig"
```

The generated `/etc/sparehash/xmrig.json` (mode 0640, root:sparehash) contains
the full XMRig configuration including a localhost-only API access token. This
token is generated once at install time and preserved across reconfiguration.
Never share it.

### MSR

MSR (model-specific register) access is unavailable in v0.2. The rendered XMRig
configuration sets `rdmsr: false` and `wrmsr: false`. No kernel module, no
sysctl drop-in, and no MSR helper is installed or required. The `[msr]` config
section accepts only `enabled = false`; any other value is rejected.

## Safety model

SpareHash uses a blocker-based readiness system. The controller evaluates all
blockers on every reconciliation cycle. Mining starts only when every blocker is
clear. The four registry blockers are:

- **Battery**: credible battery inventory with on-battery/Discharging status, or
  unknown/stale power evidence (fail-closed)
- **Gaming**: a Steam reaper is recognised, scanning fails/is stale, or an authenticated hook holds inhibition
- **Manual**: operator issued `sparehash pause`
- **Thermal**: CPU temperature at or above `thermal.pause_c`, or sensor unreadable
  (fail-safe)

The controller uses `BindsTo=` and `After=` dependencies with `sd_notify`
readiness. The miner unit (`sparehash-xmrig.service`) is `static`; it is started
only by the controller wrapper, never directly. If the controller dies, the
miner stops with it.

### Why the miner unit is static

`sparehash-xmrig.service` is `static` (not `enabled`) and has an
`ExecCondition=+/usr/local/bin/sparehash __authorize_miner__` that runs as root.
Before systemd ever starts XMRig, this condition asks the controller for a
fresh, generation-scoped authorization. A direct `systemctl start
sparehash-xmrig.service` therefore succeeds only when the controller would have
started mining anyway. If the controller is down or any blocker is active, the
condition fails and XMRig does not run.

### Controller loss

If the controller process crashes, is killed, or exits, systemd's `BindsTo=`
relationship stops the miner unit. When the controller restarts, it re-evaluates
every blocker before requesting any mining start. An active external lifecycle
lease survives restart and keeps launches inhibited; readiness requires a fresh
STOP completion with proof that the miner cgroup is empty.

Soft workload contention is handled by kernel idle scheduling (`Nice=19`,
`SCHED_IDLE`, conditional cgroup idle weights); the miner remains active but
yields to foreground work. DNS resolution failure is a diagnostic hint shown in
status, not a blocker. Hugepage reservation status is reported as a capability
and degraded warning, not a central blocker.

## Power and UPS guard

SpareHash reads power state from two sources: UPower (D-Bus) and `/sys/class/power_supply`.
Both are consulted independently. The policy is conservative:

- **Battery present**: if either credible source reports on-battery/Discharging,
  mining is inhibited. A FALSE from the other source cannot override it.
- **Desktop/UPS-only**: a successfully inventoried machine with no battery
  (including desktop systems with UPS) is allowed to mine. The status output
  reports `ups_guard: desktop/UPS-only` when this condition is detected.
- **Unknown inventory**: if the power inventory is incomplete or stale, mining
  is inhibited (fail-closed). Stale laptop evidence (older than 10 seconds) is
  treated as unknown.

The status output reports the current power state, including battery presence,
UPower and sysfs evidence with freshness indicators, `ac_online`,
`discharge_rate_w`, and the UPS guard status.

Optional `[power] maintenance_mode = true` allows tiny firmware maintenance
discharge only when fresh UPower reports **not on battery**, sysfs reports
Discharging, an external AC supply is online, and the measured total battery
rate is **at most 1W**. Typical laptop idle draw is far higher; firmware trickle
is usually well under 1W. Unplugged AC, missing rates, significant discharge,
UPower on-battery, and unknown/stale inventory still block mining. The default
is `false`, preserving the conservative policy. This section is also accepted
in TOML answers files.

## Best-effort Steam detection and gaming hooks

`gaming.enabled = true` uses one Lutris-derived Steam reaper detector for any
numeric App ID. It requires no Lutris, GameMode package, per-game options,
running Steam client, session bus or user logs. `gaming.users` may be empty;
those accounts retain hook permissions, not process-scanner scope.

Healthy scans with no recognised game may release gaming inhibition. Recognised
games, genuine scanner faults and stale results inhibit. Scans run once per
second, with bounded work and controller-owned two-second freshness. Recovery
respects the existing restart dwell; battery, thermal, manual and lifecycle
blockers remain independent.

**Best effort is not exhaustive coverage.** Non-Steam games, changed/unrecognised
markers and games surviving their marker may be missed. Low priority does not
promise zero contention. Actual native/Proton gameplay and KDE/Gaming Mode
validation of the final release remain pending owner testing. Historical owner
observations are not final-release validation. See
[`docs/gaming-integration-contract.md`](docs/gaming-integration-contract.md).

### Latency objectives (not guarantees)

The system targets ≤1 second callback latency and ≤5 second empty proof from
trigger, including detection and queued job time. These are initial engineering
objectives subject to feasibility testing, not hard real-time guarantees and not
zero game-start overlap. Actual values depend on system load and scheduling.

### Status and migration

Status reports `gaming_detection` as Steam reaper / best effort with enabled,
detected/not detected/unknown, scan age/duration and fixed failure reasons.
`coverage_tier` is `BEST_EFFORT`, `DISABLED`, or `UNVERIFIED` when unavailable,
never FULL coverage. No-match status is not independent mining permission.

New configuration writes only `gaming.enabled` and `gaming.users`. Legacy
`integration="auto"` and `steam_log=false` are deprecated readable no-ops.
Conservative/GameMode modes or `steam_log=true` are refused with migration
guidance: review the best-effort limits, remove those keys, explicitly choose
enabled or disabled. No automatic fallback or log reader remains.
The installer no longer installs GameMode, edits its INI or adds hook memberships;
ownership-safe removal handling for previously installed hooks remains.

### Manual pause interaction

A manual `sparehash pause` is independent of gaming blockers. Both must clear
for mining to start. `sparehash resume` clears only the manual pause and cannot
bypass an active gaming blocker.

### Group membership and `sg`

Existing hook users need authorized access to the control socket at
`/run/sparehash/control.sock`; new setup does not add group memberships.
Only grant `sparehash` group access to trusted users. Group membership
takes effect at next login. To apply it to the current session without logging
out:

```
sg sparehash -c "sparehash gaming-start"
```

## Hugepages

Regular 2MiB hugepages typically improve RandomX performance by 20-30%, and
possibly up to 50% depending on hardware. The default configuration reserves
1280 pages (2560 MiB).

Key constraints:

- **Default off**: `hugepages.enabled = false` in the default config
- **Single NUMA node**: automatic reservation works only when exactly one
  memory-bearing NUMA node is detected. Multi-NUMA systems require manual tuning
- **Best-effort release**: pages are returned to the kernel when the miner
  cgroup is empty. If the kernel does not honor the reduction, the lease is
  marked degraded
- **1GiB pages remain disabled**: `randomx.1gb-pages` is set to `false`. The
  incremental 1-3% gain from 1GiB pages is distinct and not pursued in v0.2

## Thermal guard

Thermal protection is off by default. When `thermal.enabled = true`, SpareHash discovers the CPU temperature sensor
automatically. Supported drivers: `coretemp` (Intel), `k10temp` and `zenpower`
(AMD). The guard uses hysteresis: mining pauses at `pause_c` (default 85 C) and
resumes at `resume_c` (default 75 C).

If no credible sensor exists or the sensor becomes unreadable, the enabled guard
fails safe and blocks mining. The
status output reports `thermal_status: UNAVAILABLE` when no credible sensor is
found.

## Lifecycle latch (reinstall behavior)

Root uninstall works independently of controller IPC. It durably writes the
retirement latch at `/var/lib/sparehash/lifecycle.json`, then stops/disables the
controller, stops the miner, and requires empty-cgroup proof before receipt-owned
removal. A partial failure after retirement leaves the system retired too.
An existing uninstall sidecar lease is retained; no lease is required or created
for offline uninstall. Configuration is retained by default.

If you later want to reinstall after uninstall, opt in explicitly. When the
retirement record carries an operation id, pass the same id:

```
sudo ./install.sh --clear-lifecycle-latch --operation-id <ID>
```

The installer prints the exact command, including the recorded id, when it
refuses a retired system. This clears the retained uninstall lease before
removing the latch. An active update hold, a mismatched operation id, or an
unreadable lease isn't cleared; a failure leaves the system retired. Never
delete lifecycle records manually to bypass retirement. Inspect and resolve any
partial-uninstall failure before opting into reinstall.

## DNS filtering

SpareHash resolves pool hostnames through the system resolver. If DNS resolution
fails and a filtered DNS path is available, the status output includes a hint:

```
dns_hint: Pool resolution failed; filtered DNS is possible; check resolver/network policy.
```

No DNS policy is changed by SpareHash itself. If your DNS setup blocks the pool,
you must add the pool domain to your resolver's allowlist yourself.

## Logs

Stream recent logs from both units:

```
sparehash logs
sparehash logs --follow
```

This streams the last 100 lines (or follows live) from `sparehash.service` and
`sparehash-xmrig.service` via `journalctl`. No pager, no caller-controlled
query.

## Deferred workflows and uninstall

**Upgrades, rollback, native packaging, benchmark and in-place reconfiguration
are deferred.** There is no supported v0.1.1 → v0.2 upgrade or automatic updater.
`update`, `complete-upgrade`, `config edit`, `benchmark` and installer flags
`--upgrade`, `--recover`, `--complete-upgrade`, `--reconfigure` refuse with exit 2
before host inspection/effects. There is no public rollback command. Internal
research implementations/tests remain, not a supported bypass.

Preserve any existing migration journal, staging and safety hold; do not delete
them or clear an update lease to force a new install. Seek maintainer assistance
for a partial old migration. See [upgrade status](docs/upgrade-v020.md).
Fresh installation and explicit reinstall after completed removal are the
experimental path. Native RPM/DEB/Nix/Flatpak channels are not delivered by v0.2.
Ordinary installation also refuses an existing protected CLI after a read-only
check, before clearing retirement or collecting capabilities. Omitting deferred
flags is not an alternate in-place reconfiguration route.

Remove SpareHash:

```
sudo sparehash uninstall
```

Configuration and manual state are retained by default. To also remove unchanged,
receipt-owned configuration:

```
sudo sparehash uninstall --purge
```

`--purge` does **not** erase all `/var/lib/sparehash` state, retirement/receipt
records, foreign files or an externally owned XMRig. Changed owned files cause
safe refusal instead of deletion. Removal needs no running controller or network;
resolve a partial removal before explicitly clearing retirement for reinstall.

Uninstall does not require a running controller or a successful IPC reply.
After protected receipt/sidecar checks, it durably latches retirement, stops and
disables the controller, stops the miner, and requires fresh empty-cgroup proof.
It restores any owned hugepage lease,
and removes only receipt-owned files, units, group memberships, and identities.
The retained latch (and any existing uninstall lease) keeps the system retired
if removal partially fails. An update hold or unreadable sidecar refuses removal
before retirement or service changes.

## File layout

| Path | Purpose |
|---|---|---|
| `/opt/sparehash/src/` | Installed Python package |
| `/opt/xmrig/current/` | Symlink to the active verified XMRig binary |
| `/etc/sparehash/config.toml` | Public configuration (mode 0644) |
| `/etc/sparehash/xmrig.json` | Generated XMRig config with API token (mode 0640, root:sparehash) |
| `/etc/sparehash/xmrig.runtime.json` | Runtime copy for the miner wrapper (mode 0640, root:xmrig) |
| `/var/lib/sparehash/` | State directory (manifest, hugepage lease, controller state) |
| `/run/sparehash/control.sock` | Controller IPC socket (mode 0660, root:sparehash) |
| `/etc/systemd/system/sparehash.service` | Controller unit |
| `/etc/systemd/system/sparehash-xmrig.service` | Miner unit (static) |
| `/usr/local/bin/sparehash` | CLI entry point |

### Atomic/redirect paths

On Atomic distributions (Fedora Silverblue, Bazzite, Kinoite), the filesystem
redirects `/opt` to `/var/opt` and `/usr/local` to `/var/usrlocal`. The
installer recognizes these redirects and operates on the physical paths while
receipting the logical identities. Configuration at `/etc/sparehash/` is not
redirected. The installer never creates, modifies, or removes OS-level redirect
links. See `docs/platforms.md` for details.

## What SpareHash does not do

- No GPU mining
- No MSR reads or writes, no kernel modules, no sysctl drop-ins
- No network or firewall policy changes
- No root miner process
- No public pool presets (you supply wallet and pool)
- No `--pause-on-battery` or `--pause-on-active` control path in the rendered
  XMRig config (both are hardcoded to disabled/zero)
- No multi-NUMA automatic hugepage reservation
- No 1GiB hugepage support
- No profitability or electricity-cost calculation

## Known limitations

- `config edit`, `update`, and `benchmark` are unsupported in v0.2.0.
- The `systemd-run` isolated version probe has been observed to fail in certain
  VM environments where transient unit creation is restricted. The rest of the
  post-install verification still passes.
- Multi-NUMA hugepage allocation is not automatic.
- Only Linux x86_64 is supported.
- Best-effort Steam detection can miss games; see the gaming contract above.

## Troubleshooting

### `sudo sparehash status` shows `controller: unavailable`

Status requires root; use `sudo sparehash status` (or add `--json`). An
unavailable controller means its socket is missing or refuses connections,
usually because the controller unit is not running. Other IPC failures report
`controller: ipc-error` with exit code 1, not a stopped controller. Check:

```
systemctl status sparehash.service
sparehash logs
```

### `sparehash pause` or `sparehash gaming-start` says "socket access denied"

Status requires root even if your user belongs to `sparehash`; run
`sudo sparehash status`. For pause/resume and gaming hooks, your user must be
root or in the `sparehash` group to use the control socket. The
installer does not add new hook memberships; trusted users can be added manually:

```
sudo usermod -aG sparehash $USER
```

Then log out and back in, or use `sg sparehash -c "sparehash ..."`.

### Mining never starts after `sparehash start`

This is usually expected behavior. Check `sudo sparehash status` for active blockers.
A fresh install needs:

1. controller to finish startup observations;
2. the minimum dwell interval (default 60 s) after the last automatic stop;
3. every blocker to be clear.

A manual `sparehash pause` persists until `sparehash resume`.

### `thermal_status: UNAVAILABLE`

No supported CPU thermal sensor was found. SpareHash supports `coretemp`,
`k10temp`, and `zenpower`. If thermal guard is disabled, this is informational.
If enabled, mining is blocked because the sensor is unavailable.

### `dns_hint` appears

The pool hostname could not be resolved. Check connectivity and your DNS
resolver. SpareHash does not change DNS settings.

### `sparehash uninstall` fails with "lifecycle busy"

An internal or older update operation may be holding
the lease; the public update/config-edit/benchmark commands are unsupported in
v0.2.0. Let an active operation finish, then retry. If it failed or is stuck,
inspect `sparehash logs` and the lifecycle records before recovery. A controller
restart doesn't clear an external hold, and the reinstall flag doesn't erase an
update hold. Don't delete lifecycle files to bypass the safety barrier.

### Reinstall says "this system was previously uninstalled"

After resolving any partial-uninstall failure, explicitly opt into reinstall.
The installer prints the exact command, including the recorded operation id:

```
sudo ./install.sh --clear-lifecycle-latch --operation-id <ID>
```

This clears both the retained uninstall lease and the retirement latch. A
mismatched or missing operation id, an active update hold, or a corrupt record
is refused and leaves the system retired. Removing `lifecycle.json` alone isn't
equivalent and may leave mining inhibited.

## Profitability and electricity

SpareHash does not calculate profitability, electricity cost, or expected
revenue. CPU mining on many machines costs more in electricity than it earns in
Monero. Before leaving SpareHash running continuously, estimate your power draw
and local electricity rate. SpareHash's battery guard helps prevent laptop
drain, but it does not make mining profitable.

## QA

The `qa/` directory contains integration test suites and a README explaining
which tests are destructive and must run only in a disposable VM. See
`qa/README.md` for details. **Do not run destructive QA scripts on a production
machine.**

`qa/qa_release_smoke.sh` is the required disposable-VM acceptance entrypoint.
It requires `--yes`, `SPAREHASH_QA_DISPOSABLE_VM=YES` and a verified source SHA-256,
exercises actual install/operation/removal paths, and ends purged/retired. Its
attributed synthetic miner never hashes or contacts a pool; no real-pool mining
is permitted. S5 separately freezes candidate commit/artifact identity. Passing
unit tests does not establish VM acceptance; see `qa/README.md` for invocation
and evidence requirements.

## Contributing

SpareHash is licensed under the GPLv3. By contributing, you agree to license
your work under the same license. See `CONTRIBUTING.md` and `docs/architecture.md`
for development setup and design invariants.

## Support

SpareHash is experimental software. File issues against the repository. Do not
contact upstream XMRig for SpareHash-specific problems.

For security issues, see `SECURITY.md`.
