# SpareHash

SpareHash is a CPU-only Monero (RandomX) miner manager for Linux x86_64. It runs
XMRig v1 in the background using spare CPU cycles, pausing automatically when
games, thermal limits, or power conditions demand the hardware.

SpareHash is CPU-only. There is no GPU mining, no MSR helper, no kernel module,
and no network or firewall policy change. The miner runs as a dedicated
unprivileged `xmrig` user, never as root.

Licensed under the GNU General Public License v3.0 (GPLv3). See `LICENSE`.

## Requirements

- Linux x86_64
- systemd as PID 1
- Python 3.11 or newer
- No additional packages beyond what the target distribution provides

## Quick start

One command installs everything:

```
sudo ./install.sh
```

The installer walks you through an interactive setup: wallet address, pool
host/port, CPU mode, thermal guard, hugepages, and gaming users. It creates the
`xmrig` and `sparehash` system accounts, downloads and verifies XMRig v6.26.0
from GitHub (pinned GPG fingerprint, SHA-256 checksum), writes configuration to
`/etc/sparehash/`, installs two systemd units, and enables the controller.

For unattended installs, supply a TOML answers file:

```
sudo ./install.sh --answers answers.toml
```

After install, the controller is enabled but not started. Start it with:

```
sudo sparehash start
```

Check status:

```
sparehash status
sparehash status --json
```

## CLI reference

Every command the CLI exposes:

| Command | Flags | Requires root | Description |
|---|---|---|---|
| `install` | `--answers FILE`, `--use-existing` | yes | Run the installer |
| `uninstall` | `--purge` | yes | Remove SpareHash (not yet implemented in v1) |
| `status` | `--json` | no | Show controller, miner, blockers, power, thermal, hugepages, gaming, API |
| `start` | | yes | Enable and start the controller (`systemctl enable --now`) |
| `stop` | | yes | Stop controller and miner (does not disable; reboot can resume) |
| `pause` | | no | Manually pause mining via controller IPC |
| `resume` | | no | Clear manual pause (mining resumes only when all blockers are clear) |
| `config show` | | no | Print current public configuration as JSON |
| `config edit` | | yes | Re-run the wizard to update configuration; backs up and validates |
| `logs` | `-f`, `--follow` | no | Stream the last 100 lines from both unit journals |
| `benchmark` | | yes | Request a fixed 1M-mode benchmark from the controller |
| `update` | | yes | Update XMRig to a newer verified release (not yet implemented in v1) |
| `gaming-start` | | no | Signal that a configured user started gaming |
| `gaming-end` | | no | Signal that a configured user stopped gaming |
| `debug set-blocker` | `NAME` | yes | Force a named blocker active (debugging only) |
| `debug clear-blocker` | `NAME` | yes | Clear a named blocker (debugging only) |

Internal commands (`__controller__`, `__cleanup__`, `__authorize_miner__`,
`__miner_exec__`, `authorize-start`) are invoked by systemd units only. They are
not user-facing.

## Configuration

The public configuration lives at `/etc/sparehash/config.toml`. Edit it through
`sparehash config edit` (recommended) or by hand. All keys and their defaults:

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
UPower and sysfs evidence with freshness indicators, and the UPS guard status.

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

When `thermal.enabled = true`, SpareHash discovers the CPU temperature sensor
automatically. Supported drivers: `coretemp` (Intel), `k10temp` and `zenpower`
(AMD). The guard uses hysteresis: mining pauses at `pause_c` (default 85 C) and
resumes at `resume_c` (default 75 C).

If the sensor becomes unreadable, the guard fails safe and blocks mining. The
status output reports `thermal_status: UNAVAILABLE` when no credible sensor is
found.

## DNS filtering

SpareHash resolves pool hostnames through the system resolver. If DNS resolution
fails and a filtered DNS path is available, the status output includes a hint:

```
dns_hint: Pool resolution failed; filtered DNS is possible; check resolver/network policy.
```

No DNS policy is changed by SpareHash itself.

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

Update and uninstall commands are defined in the CLI but not yet implemented in
v1. Running them returns exit code 2 with an "unavailable" message.

When implemented, `sparehash uninstall --purge` will remove all SpareHash files,
units, and identities. Without `--purge`, configuration is retained.

## File layout

| Path | Purpose |
|---|---|
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

## Known limitations

- The `systemd-run` isolated version probe has been observed to fail in certain
  VM environments where transient unit creation is restricted. The rest of the
  post-install verification (marker, units, controller enabled, miner static,
  loopback API, idle scheduler) still passes. Probe reliability under restricted
  systemd managers remains an open TODO.
- `sparehash update` and `sparehash uninstall` are not yet implemented.

## QA

The `qa/` directory contains integration test suites and a README explaining
which tests are destructive and must run only in a disposable VM. See
`qa/README.md` for details.

## Contributing

SpareHash is licensed under the GPLv3. By contributing, you agree to license
your work under the same license.

## Support

SpareHash is experimental software. File issues against the repository. Do not
contact upstream XMRig for SpareHash-specific problems.
