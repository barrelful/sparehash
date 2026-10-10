# SpareHash

> **Experimental v0.1.1 software.** SpareHash is a young system-level project.
> It has been tested on a limited set of Linux distributions. Review this
> README, `SECURITY.md`, and the v1 limitations section before installing on a
> machine you care about.

SpareHash is a CPU-only Monero (RandomX) miner manager for Linux x86_64. It runs
[XMRig](https://xmrig.com/) in the background using spare CPU cycles, and
pauses automatically when games, thermal limits, or power conditions demand the
hardware.

SpareHash is CPU-only. There is no GPU mining, no MSR helper, no kernel module,
and no network or firewall policy change. The miner runs as a dedicated
unprivileged `xmrig` user, never as root.

Licensed under the GNU General Public License v3.0 (GPLv3). See `LICENSE`.

## What SpareHash does and does not do

- **Does:** install and manage an unprivileged XMRig service that mines only
  when every safety blocker is clear.
- **Does:** pause mining on battery, during GameMode sessions, above a thermal
  threshold, or when you run `sparehash pause`.
- **Does:** use kernel idle scheduling (`Nice=19`, `SCHED_IDLE`, cgroup idle
  weights where supported) so normal foreground work always wins without
  frequent stop/start cycles.
- **Does:** verify XMRig downloads against a pinned GPG fingerprint and
  SHA-256 checksum.
- **Does:** keep the XMRig HTTP API on `127.0.0.1` only.
- **Does not:** mine as root.
- **Does not:** change DNS, firewall, SELinux, AppArmor, sysctl, or bootloader
  settings.
- **Does not:** enable MSR optimisation, GPU mining, or 1GiB hugepages in v1.
- **Does not:** provide public pool presets or calculate mining profitability.

## Supported platforms

- Linux x86_64
- systemd as PID 1 (systemd 248+ recommended for `LoadCredential` support)
- Python 3.11 or newer
- No additional Python packages required at runtime

Primary testing target: recent Debian, Ubuntu, and Linux Mint. Other systemd
distributions may work but are not guaranteed in v1.

## Quick start

One command installs everything:

```
sudo ./install.sh
```

The installer walks you through an interactive setup: wallet address, pool
host/port, CPU mode, thermal guard, hugepages, and gaming users. It creates the
`xmrig` and `sparehash` system accounts, selects the latest supported XMRig
release from GitHub and verifies it (pinned GPG fingerprint, SHA-256 checksum),
or reuses an already verified managed installation, writes configuration to
`/etc/sparehash/`, and installs two systemd units. It then asks:

- `Start SpareHash automatically at boot? [Y/n]:`
- `Start SpareHash controller now? [Y/n]:`

Enter accepts Yes for each. No to boot startup disables the controller unit;
No to starting now leaves a fresh installation inactive (it does not stop an
already-running controller on reinstall). The installer never enables or starts
the miner directly. Its final summary reports boot startup, controller activity,
and whether mining is blocked (or unknown if controller status is unavailable).

For unattended installs, supply a TOML answers file:

```
sudo ./install.sh --answers answers.toml
```

In `--answers` mode, both startup choices default to Yes without reading stdin:
the controller is enabled at boot and started immediately. If you declined
startup interactively, you can enable and start it later with:

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
| `install` | `--answers FILE`, `--use-existing`, `--clear-lifecycle-latch` | yes | Run the installer |
| `uninstall` | `--purge` | yes | Remove SpareHash; configuration retained unless `--purge` |
| `status` | `--json` | yes | Show controller, miner, blockers, power, thermal, hugepages, gaming, API |
| `start` | | yes | Enable and start the controller (`systemctl enable --now`) |
| `stop` | | yes | Stop controller and miner (does not disable; reboot can resume) |
| `pause` | | no | Manually pause mining via controller IPC |
| `resume` | | no | Clear manual pause (mining resumes only when all blockers are clear) |
| `config show` | | no | Print current public configuration as JSON |
| `config edit` | | yes | Unsupported in experimental v0.1.1; exits 2 without effects |
| `logs` | `-f`, `--follow` | no | Stream the last 100 lines from both unit journals |
| `benchmark` | | yes | Unsupported in experimental v0.1.1; exits 2 without effects |
| `update` | | yes | Unsupported in experimental v0.1.1; exits 2 without effects |
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
`config edit` is unsupported in v0.1.1. All keys and their defaults:

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
users = []                   # list of Unix account names to monitor for GameMode

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

MSR (model-specific register) access is unavailable in v1. The rendered XMRig
configuration sets `rdmsr: false` and `wrmsr: false`. No kernel module, no
sysctl drop-in, and no MSR helper is installed or required. The `[msr]` config
section accepts only `enabled = false`; any other value is rejected.

## Safety model

SpareHash uses a blocker-based readiness system. The controller evaluates all
blockers on every reconciliation cycle. Mining starts only when every blocker is
clear. The four registry blockers are:

- **Battery**: credible battery inventory with on-battery/Discharging status, or
  unknown/stale power evidence (fail-closed)
- **Gaming**: a configured user has an active GameMode session
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

## GameMode and gaming hooks

When `gaming.enabled = true` and `gaming.users` lists one or more accounts,
SpareHash monitors those users for active GameMode sessions. The controller
queries `gamemoded -s` per user via `runuser` and the user's D-Bus session bus.

To activate GameMode for a Steam game, add this to the game's launch options:

```
gamemoderun %command%
```

SpareHash does not perform blanket process detection. It checks only the
configured accounts through the official GameMode socket.

### Active session-bus requirement

The gaming check runs `runuser -u <name> -- env DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/<uid>/bus gamemoded -s`.
If the configured user is not logged in (no `/run/user/<uid>/bus` socket), the
query is inconclusive and SpareHash inhibits mining. Add gaming users only if
they will have an active graphical or session bus while playing.

### Group membership and `sg`

The installer adds configured gaming users to the `sparehash` group so they can
access the control socket at `/run/sparehash/control.sock`. Group membership
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
  incremental 1-3% gain from 1GiB pages is distinct and not pursued in v1

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

If you later want to reinstall after uninstall, opt in explicitly:

```
sudo ./install.sh --clear-lifecycle-latch
```

This clears the retained uninstall lease before removing the latch. An active
update hold or an unreadable lease isn't cleared; a failure leaves the system
retired. Never delete lifecycle records manually to bypass retirement.
Inspect and resolve any partial-uninstall failure before opting into reinstall.

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

## Update and uninstall

`update`, `config edit`, and `benchmark` are unsupported in experimental v0.1.1.
They refuse with exit code 2 before workflow effects. The underlying update
sidecar lease still gates internal update operations and survives controller
restart; it is not a supported public update workflow in this release.

Remove SpareHash:

```
sudo sparehash uninstall
```

Configuration is retained by default. Remove everything including configuration
with:

```
sudo sparehash uninstall --purge
```

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

- `config edit`, `update`, and `benchmark` are unsupported in v0.1.1.
- The `systemd-run` isolated version probe has been observed to fail in certain
  VM environments where transient unit creation is restricted. The rest of the
  post-install verification still passes.
- Multi-NUMA hugepage allocation is not automatic.
- Only Linux x86_64 is supported.
- GameMode detection requires the configured user to have an active D-Bus
  session bus.

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
installer adds configured gaming users; other users can be added manually:

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
v0.1.1. Let an active operation finish, then retry. If it failed or is stuck,
inspect `sparehash logs` and the lifecycle records before recovery. A controller
restart doesn't clear an external hold, and the reinstall flag doesn't erase an
update hold. Don't delete lifecycle files to bypass the safety barrier.

### Reinstall says "this system was previously uninstalled"

After resolving any partial-uninstall failure, explicitly opt into reinstall:

```
sudo ./install.sh --clear-lifecycle-latch
```

This clears both the retained uninstall lease and the retirement latch. Removing
`lifecycle.json` alone isn't equivalent and may leave mining inhibited.

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

`qa/qa_release_smoke.sh` is the required disposable-VM acceptance procedure for
a frozen candidate SHA. It checks eight checkpoints through offline uninstall,
using only synthetic local hashing on `127.0.0.1:3333` with zero donation.
Real-pool mining is forbidden during QA. Passing unit tests does not establish
VM acceptance; see `qa/README.md` for invocation and evidence requirements.

## Contributing

SpareHash is licensed under the GPLv3. By contributing, you agree to license
your work under the same license. See `CONTRIBUTING.md` and `docs/architecture.md`
for development setup and design invariants.

## Support

SpareHash is experimental software. File issues against the repository. Do not
contact upstream XMRig for SpareHash-specific problems.

For security issues, see `SECURITY.md`.
