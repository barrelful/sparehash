# SpareHash Architecture

This document explains the design invariants and major components of SpareHash.
It is intended for contributors and reviewers who need to understand the system
without access to the original planning conversations.

## Overview

SpareHash is a fail-closed safety controller around an unprivileged XMRig
process. The controller (`sparehash.service`) runs as root, observes the host,
and decides whether mining is allowed. The miner (`sparehash-xmrig.service`)
runs as the dedicated `xmrig` user and is started only after a fresh
authorization from the controller.

```
┌──────────────────────┐     IPC      ┌──────────────────────┐
│  sparehash.service   │◄────────────►│ sparehash-xmrig.svc  │
│  (root controller)   │  authorize   │ (xmrig user miner)   │
└──────────────────────┘              └──────────────────────┘
         │
         │ observes
         ▼
   battery / gaming / thermal / manual blockers
```

## Core invariants

1. **Fail-closed.** Any unknown, stale, or unreadable safety observation is
   treated as a blocker. Mining starts only when the controller has a fresh,
   complete observation and every blocker is clear.

2. **Controller is the authority.** No process other than the controller can
   authorize a miner start. systemd's `ExecCondition` delegates this decision
   to the controller on every start attempt.

3. **Miner is unprivileged.** The `xmrig` user has no special capabilities.
   The binary is root-owned and read-only.

4. **Controller loss stops mining.** The miner unit `BindsTo=` the controller
   unit. A controller stop or crash propagates to the miner.

5. **Every start is freshly authorized.** Authorization is tied to a monotonic
   generation counter. A stale authorization from a previous generation cannot
   start a new miner process.

## Controller state machine

The controller is a single-threaded owner loop (`runtime.py`) with off-loop
workers for blocking observations (power, thermal, gaming) and serialized
systemd jobs (start/stop).

Key state:

- `generation`: incremented whenever a blocker changes or a reload occurs.
- `mode`: `mine` or `benchmark`.
- `observation`: the most recent concurrent sample of all blockers.
- `observed_generation`: the generation for which the current observation was
  taken.
- `launch_permitted`: set when a START job is submitted; consumed by
  authorization.
- `external`: the active durable update/uninstall lease, separate from generation.
- `excluded`: inhibits launches while a lifecycle operation holds the stack.
- `permanent_hold`: retains launch inhibition after uninstall commit.

### Lifecycle of a mining start

1. A fresh observation arrives with every blocker clear.
2. `apply()` decides mining is allowed and no job is pending.
3. A STOP job is submitted first if the previous miner state is not known to be
   empty.
4. Once empty, a START job is submitted; `launch_permitted` is set.
5. systemd starts the miner unit.
6. The `ExecCondition` (`__authorize_miner__`) calls the controller over IPC.
7. `authorize()` checks:
   - observation is fresh (<= 1 second old);
   - `observed_generation == generation`;
   - `launch_permitted` is true;
   - no blockers are active;
   - the published operation matches the controller's current operation.
8. If all checks pass, the condition returns success and systemd runs
   `__miner_exec__` as the `xmrig` user.
9. If any check fails, the condition returns failure and XMRig does not start.

### Why a generation counter?

A safety event can arrive while a START job is in flight. The generation counter
lets the controller distinguish an in-flight job that was authorized for an old
state from one that is still valid. When a blocker changes, the generation is
incremented, `launch_permitted` is revoked, and any late authorization request
is denied.

## Blockers

The blocker registry (`state.py`) is the single source of truth for whether
mining is allowed. There are four blockers:

| Blocker | Source | Fail-closed behavior |
|---|---|---|
| battery | UPower + `/sys/class/power_supply` | Unknown or stale state inhibits mining |
| gaming | `gamemoded -s` per configured user | Unknown user/session state inhibits mining |
| thermal | CPU sensor (`coretemp`, `k10temp`, `zenpower`) | Default off; when enabled, missing/unreadable sensor inhibits mining |
| manual | `sparehash pause` / `sparehash resume` | Persists across controller restarts via state file |

Debug blockers (`sparehash debug set-blocker`) are synthetic and only add to
the real blockers. Clearing a debug blocker never clears a real blocker. This
prevents debug tooling from accidentally masking a real safety condition.

## Observed generation vs authorization

`observed_generation` records the generation for which the most recent
observation was collected. `authorize()` requires `observed_generation ==
generation`. This means:

- A blocker change invalidates the current observation.
- A reload invalidates the current observation.
- An authorization cannot be reused after any state change.

## Controller → miner lifecycle

The miner unit is `static`, not `enabled`. It is started only by the controller
through `systemctl start sparehash-xmrig.service`. The unit has:

```ini
BindsTo=sparehash.service
ExecCondition=+/usr/local/bin/sparehash __authorize_miner__
ExecStart=/usr/local/bin/sparehash __miner_exec__
User=xmrig
Group=xmrig
```

`BindsTo=` ensures the miner stops if the controller stops. The `+` prefix on
`ExecCondition=` runs the condition as root so it can open the control socket
(which is `root:sparehash 0660`). The actual miner process runs as `xmrig`.

## IPC trust and authorization

The control socket is a Unix domain socket at `/run/sparehash/control.sock`,
owned `root:sparehash` with mode `0660`. The server uses `SO_PEERCRED` to learn
the caller's UID/GID.

- Root-only verbs: `reload`, `debug-set`, `debug-clear`, `authorize-start`,
  `benchmark`, `begin-update`, `update-status`, `end-update`.
- Group-gated verbs: `status`, `pause`, `resume`, `gaming-start`, `gaming-end`.

Per-UID rate limiting prevents a local user from flooding the socket.

## Durable external lifecycle lease

`lifecycle_lease.py` stores one active record at
`/var/lib/sparehash/lifecycle-lease.json`: `request_id`, `purpose` (`update` or
`uninstall`), and a unique token unrelated to the controller generation.
`begin-update` persists the hold before acknowledgment. Replaying the same
active request ID and purpose returns the same token without invalidating state
again; a competing request is rejected as lifecycle busy. Corrupt or unreadable
records inhibit launches rather than being treated as absent.

On controller restart, the record restores launch inhibition. No empty-cgroup
proof survives restart: `update-status` reports ready only after a fresh STOP
completion proves the miner cgroup empty, with no pending job. Stale tokens are
rejected. An update commit durably removes the lease before releasing the hold,
invalidates authorization, and requests fresh observations. An abort can release
an uncommitted hold, but cannot lift committed retirement.

Public root uninstall is controller-independent, not an IPC commit. It checks
protected receipts and the sidecar first: absent holds are allowed, existing
uninstall holds are retained, and update/corrupt/unreadable holds refuse before
effects. It durably writes `/var/lib/sparehash/lifecycle.json`, disables/stops
the controller, stops the miner, and requires fresh empty-cgroup proof before
hugepage restoration and receipt-owned file/version/account removal. It creates
no lease and makes no controller request. Configuration is retained by default.
A partial removal failure leaves the system retired. Explicit reinstall with
`--clear-lifecycle-latch` clears any
retained uninstall lease before removing the retirement latch; it refuses to
erase an update hold or an unreadable record. Deleting the latch alone doesn't
clear the sidecar lease.

## GameMode flow

A gaming user calls `sparehash gaming-start`. The controller records the peer
UID in a `hooks` set and activates the gaming blocker. The user must later call
`sparehash gaming-end` to remove their UID from the set. The controller also
queries `gamemoded -s` independently; if any configured user reports active,
the gaming blocker stays active regardless of hook state.

This design trusts gaming users: a member of the `sparehash` group can block
mining indefinitely by never sending `gaming-end`. Only add users you trust.

## Battery evidence

Power state is observed concurrently from two independent sources:

- UPower D-Bus `OnBattery` property.
- `/sys/class/power_supply` inventory and `online`/`status` files.

The controller blocks mining if either credible source reports on-battery, or
if the inventory is unknown/stale. A desktop/UPS-only machine (confirmed no
battery) is allowed to mine.

## Deferred optional workflows

Public `config edit`, `update`, and `benchmark` are unsupported in experimental
v0.1.0: dispatch exits 2 before workflow imports or effects. `config show` remains
supported. The internal benchmark mechanism remains, but is not public release
acceptance. Its controller flow is:

1. Calls `authorize()` to verify all blockers are clear.
2. Acquires the lifecycle update lease (which stops any current mining).
3. Sets `next_mode = benchmark`.
4. Submits a START job for `Mode.BENCHMARK`.
5. `__miner_exec__` receives a special benchmark config with no pools and no
   API, and runs XMRig with `--bench=1M`.

The benchmark authorization is single-use. A second authorization for the same
benchmark is denied.

## Hugepage lease and restore

Hugepages are optional and default to off. When enabled and a single NUMA node
is detected, the controller reserves 2MiB pages before starting the miner and
records a lease in `/var/lib/sparehash/hugepages.json`.

The lease contains:

- boot ID (so leases do not survive reboot);
- sysfs path;
- baseline count (before reservation);
- target count;
- applied count (what the kernel actually granted).

When the miner cgroup is confirmed empty, cleanup restores the baseline page
count. If the kernel does not honor the reduction, the lease remains and the
status output reports a degraded state.

## Install receipts and ledger

The installer uses a write-ahead ledger stored in
`/var/lib/sparehash/install-manifest.json`. Every created file, directory,
identity, group membership, and action is recorded. Uninstall removes only
recorded artifacts that still match their recorded SHA-256, ownership, and
mode.

This prevents uninstall from touching foreign files, pre-existing user-managed
XMRig installations, or user GameMode configuration that was merged rather than
created.

## XMRig manifest and path trust

`/opt/xmrig` is a managed tree. The manifest records every installed XMRig
version. The `current` symlink is always a relative link to one of the recorded
versions. `PathTrust` validates every path component with descriptors and
rejects symlinks, foreign ownership, or unrecorded directories.

Internal updates add a new version, repoint `current`, and optionally prune old versions.
Rollback points `current` back to the previous version.

## Why no MSR in v1

MSR access would require either a kernel module, a setuid helper, or privileged
instructions. All of these increase the attack surface and could affect other
workloads. SpareHash v1 deliberately excludes MSR optimisation. The rendered
XMRig config sets `rdmsr: false` and `wrmsr: false`, and the config schema
rejects any enabled MSR section.

## Reading list

- `sparehash/controller.py` — state machine
- `sparehash/controller_io.py` — IPC verbs and authorization
- `sparehash/runtime.py` — controller assembly and owner loop
- `sparehash/runtime_jobs.py` — serialized systemd jobs
- `sparehash/runtime_spec.py` — miner exec authorization
- `sparehash/lifecycle_lease.py`, `sparehash/lifecycle.py`: durable hold and retirement
- `sparehash/cli_uninstall.py`: retirement before receipt-owned removal
- `sparehash/state.py` — blocker registry
- `sparehash/ipc.py` — Unix socket transport
- `sparehash/power.py`, `sparehash/thermal.py`, `sparehash/gaming.py` — blockers
- `sparehash/install_ledger.py`, `sparehash/xmrig_manifest.py` — receipts
- `sparehash/xmrig_paths.py`, `sparehash/xmrig_layout.py` — path trust
- `systemd/sparehash*.service.in` — unit templates
