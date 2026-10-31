# v0.2 idle-cgroup and abnormal-cleanup investigation

Base: `83cf1e8092348729ec923f43127a72f5a5b3fd9f`, PR #1. This follows the
[previous acceptance report](v020-current-acceptance.md); it does not clear the
blocked dual-image reboot gate. No disposable guest reboot is required here.

## Root-cgroup capability bug

The [kernel cgroup-v2 documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html#cpu-interface-files)
defines `cpu.idle` as a **non-root cgroup** control. Its absence at the hierarchy
root is not evidence that child cgroups cannot use idle scheduling.

There were two independent false-negative gates:

1. `Capabilities.detect()` required a readable root `cpu.idle` before even calling
   the explicit installer-supplied manager probe.
2. `systemd.probe_cpuweight_idle()` required that same root file before creating
   the transient unit.

Both gates now require only root identity and cgroup v2 before the explicit
bounded probe. A positive capability still requires successful transient-unit
exec/start, a live MainPID and running substate, expected manager properties, a safe non-root ControlGroup path,
effective **child** `cpu.idle=1`, and successful transient-unit cleanup. No
probe/default, unprivileged invocation, v1, rejected manager properties, missing
or non-idle child control, read failure, timeout and cleanup failure retain the
fallback. `render()` and ordinary status still do not invoke manager probes.

A hermetic failing-before/passing-after reproduction retains a supported child
without creating root `cpu.idle`: the base probe returned false without a manager
call; the corrected probe returned true after its three recorded manager calls.
Focused regressions test both gates and unsupported/uncertain cases. These are
unit-test results, not substitutes for the VM evidence below.

The first VM run exposed a third false negative: an active/exited oneshot with
`RemainAfterExit=yes` does **not** retain a ControlGroup on these systemd builds.
Both returned `ControlGroup=` after `/usr/bin/true` exited. The probe now uses
`Type=exec` with `/usr/bin/sleep 30`, `RuntimeMaxSec=30s`, the existing bounded
start/stop/subprocess deadlines and no `--wait`. It checks the live child's
effective control and then stops its own uniquely named unit. If management is
interrupted, the sleeper and systemd runtime limit independently bound lifetime.
Missing/dead process, exited substate and empty cgroup all retain fallback;
regressions cover these even with a fixture child reporting `cpu.idle=1`.

## Reproduction and controls

Read [VM safety and transfer instructions](vm-testing.md) first. Archive the
identified source commit, record archive SHA-256 plus
`python3 qa/release_smoke.py --print-source-sha256`, and transfer through the
existing external launcher's `put` operation. In a fresh networkless disposable
systemd VM, run:

```sh
SPAREHASH_QA_DISPOSABLE_VM=YES python3 \
  /var/tmp/investigation-source/qa/investigate_priority_cleanup.py \
  --source-sha256 SOURCE_DIGEST
```

The driver requires explicit VM consent, guest root, detected virtualisation,
disabled networking and matching production source inventory. It refuses an
existing installation. It records the actual kernel/systemd, root/system-slice
controllers, effective child controls, safe probe commands/results, installation,
and full bounded command evidence. Its only miner is the existing non-hashing
authenticated loopback fixture. No production pool or credentials are used.

### Contention attribution

The foreground SHA-256 computation, worker count (four), one-second warm-up,
three three-second measurements, and four synthetic idle/nice19 CPU workers are
unchanged from the previous measurement. Added metadata records foreground
worker cgroups/PIDs and ancestor CPU controls; it does not change computation.

The order is **baseline → process-idle only → group-idle → group-idle →
process-idle only → baseline**. The same live contender cgroup and task policies
are retained across the four contended conditions. Only its owned transient
unit's `CPUWeight` is switched between 100 and idle, with kernel `cpu.idle`,
`cpu.weight`, process policy/nice and CPU accounting recorded. No root controller
enablement, quotas, governors, global scheduler settings or physical-host service
changes are performed. Symmetric conditions bracket drift; the original absolute
throughputs are historical comparisons, not controlled same-run baselines.

### Cleanup attribution

The first crash scenario uses the installed controller's original ExecStart and
ExecStopPost, no blocked-worker shim, and a guest-only config bind enabling a
small 32-page lease. `Restart=no` in a temporary guest drop-in prevents restart
from masking the immediate outcome; it does not change the sandbox or ownership.
Exact unit/drop-in properties, runtime ownership/mode/context, controller mount
namespace, safe systemd properties, manager/service journals and audit evidence
are retained. SIGKILL is sent only to the disposable controller. Empty miner
cgroup, page count, ledger and operation-state presence are recorded after exit.

Machine-specific records remain gitignored under
`.omo/evidence/v020/task-2/idle-cleanup-investigation/`. Collect the guest evidence
bundle before forcing off and discarding each owned clone. A timed-out launch is
unknown, not a pass; discard instead of retrying an uncertain guest command. Do
not copy the lab controller or private host manifests into this repository.

## Outcomes

PASS/FAIL/BLOCKED/NOT TESTED outcomes and measured comparisons are recorded here
after collection. Physical workstation services must remain unchanged and stopped.

Local final verification: **1,170 tests + 619 subtests passed**, mypy clean on
56 production modules, scoped Ruff, README inventory, offline wheel/sdist and
artifact/GPL/dependency checks passed. CI lint now includes the changed probe,
detector, focused tests and investigation script.

### First instrumented round: causal evidence, not a completed fix

Archive `6bcb4458c27cda0534a2bc336eafc43cffe98f35` (root-gates removed, original
oneshot still present), SHA-256
`4db17d0c7a5455d0698a898061d08d91e3c58d124f74a6647f235a7f2ef64e81`:

- Bazzite: kernel `7.2.9-ogc3.1.fc44.x86_64`, systemd `259.9-1.fc44`.
- Ubuntu: kernel `6.8.0-142-generic`, systemd `255.4-1ubuntu8.17`.
- Both root `cgroup.controllers` include `cpu`; both lack root `cpu.idle`.
  CPU control is enabled down the manager's system-slice hierarchy. An actual
  transient child accepts CPUWeight=idle and exposes `cpu.idle=1`. No manual
  delegation/controller activation was needed; these are root-managed system
  services, not evidence of unprivileged delegation rights.
- Both original oneshot probes returned false because `ControlGroup` was empty,
  not because CPUWeight=idle was rejected. The installer correctly retained its
  fallback for that inconclusive probe. This exposed the live-probe requirement
  above; the root-only correction by itself was insufficient.

Median foreground throughput (MiB SHA-256 input/s), in execution order:

| Condition | Bazzite | Ubuntu |
|---|---:|---:|
| Baseline before | 9035.86 | 8835.23 |
| Process idle only | 4810.12 | 4167.03 |
| Group idle, first | 4792.02 | 8904.43 |
| Group idle, second | 4642.31 | 8755.94 |
| Process idle only, repeated | 4626.92 | 4297.64 |
| Baseline after | 10050.70 | 8696.92 |

All foreground workers were SCHED_OTHER/nice0; all contender tasks were
SCHED_IDLE/nice19. Contender kernel idle was verified 0 → 1 → 1 → 0, with
weight 100 → 1 → 1 → 100 on Bazzite and 100 → 0 → 0 → 100 on Ubuntu. Effective
kernel `cpu.idle`, not an assumed numeric weight encoding, is the decisive fact.
Both VM ancestor limits recorded `cpu.max=max 100000`; the lab's external
aggregate four-CPU cap remains in force and was not changed.

The foreground topology differs by management transport: Bazzite's serial login
and `runuser` retain `/user.slice/user-0.slice/session-*.scope`; its contender is
under `/system.slice`. Ubuntu's guest-agent foreground and contender are peers
under `/system.slice`. Ubuntu's idle-group improvement is a within-run
priority-only result. Bazzite's cross-slice result does **not** improve; group
idle is relative to peer groups, not an unconditional low-priority guarantee
across active ancestor slices. The second round explicitly places foreground
and contender as system-slice peers to separate topology/methodology from the
priority effect. It cannot be substituted for the cross-slice/desktop result.

### Cleanup root cause and real hugepage leak

**FAIL reproduced on Bazzite without a QA ExecStart/blocked-worker override.**
SIGKILL with 32 pages leased empties the miner but leaves all **32 pages reserved**
and the lease ledger present. `ExecStopPost` has actual status 233 and never
executes the trusted CLI. Ubuntu's same scenario executes the hook with status
zero, releases pages to zero and removes the ledger.

The runtime publishes `/run/sparehash-miner` as root:xmrig, mode 0750, while the
controller unit declares `Group=root` and manages that directory using
`RuntimeDirectory=`. On the next command spawn, systemd attempts to reconcile
directory ownership recursively to root:root. Bazzite's audit journal and audit
file record the exact denial:

```text
comm="(sparehash)" name="operation.json"
scontext=system_u:system_r:init_t:s0
tcontext=system_u:object_r:var_run_t:s0 tclass=file
avc: denied { setattr } ... permissive=0
```

This occurs during execution-directory setup, before the cleanup executable or
its mount namespace can run. The file exists after abrupt termination but is
normally removed by graceful controller cleanup, explaining the abnormal-exit
association. It is **not** evidence that SELinux should be weakened or that the
root cleanup helper needs more privileges.

The previous acceptance report's empty **kernel-journal** AVC filter did not
establish absence of SELinux denials: the decisive record is in the audit
transport/file. The expanded diagnostics retain both sources and supersede any
interpretation of that limited filter as a full SELinux clearance.

Smallest justified correction direction: avoid systemd recursively re-owning
mixed-group runtime artifacts. Keep systemd-managed top-level ownership aligned
with its declared identity, and put differently group-readable protected
artifacts in controller-owned children so manager ownership reconciliation does
not descend into them. Preserve root ownership, no group write, distinct IPC vs
miner groups, exact operation validation, the sandbox and empty-cgroup cleanup
gate. A concrete protected-runtime layout change requires **owner approval**
under AGENTS.md before introducing new privileged paths. No arbitrary relabel,
policy module, broad permission change, primary-group change, ignored hook
failure or privileged prefix is proposed as a workaround. Required regression:
SIGKILL and blocked-worker nonzero exit with an active lease must execute the
unchanged trusted cleanup hook successfully under Enforcing, return pages to
baseline, remove owned operation/lease state, preserve foreign state, and deny
cleanup when the miner cgroup is populated or unknown.

### Second round: verified live probe and peer-group scheduling

Archive `0c18d4e6deb857e461a99ca4bf677c61a98aa1cb`, SHA-256
`ec3a35ae89c7569ff0042714f008d256c2f4e1548f926a93060641c27d61bb66`, production
source digest `7d00fdb46beb6ca04537103382f2565621b48c15ebe2ef578f043013befaa930`.
Run the driver with `--sibling-foreground` to reproduce this explicitly different
placement. Both guest commands completed with exit zero; that means evidence
collection completed, **not** that the intentionally failing cleanup passed.

**PASS on both distributions:** the bounded live probe returns true despite
absent root `cpu.idle`; `Capabilities.detect()` returns true; the actual installer
renders `CPUWeight=idle`; installed miner processes retain SCHED_IDLE/nice19,
`Delegate=no`, and effective miner `cpu.idle=1` (kernel weight 1 on Bazzite, 0 on
Ubuntu). Managers provide the necessary CPU controller path without manually
changing delegation or root controls. Probe unit stop succeeds. The final source
also tightens PID validation to bounded ASCII decimal; focused tests cover that
fail-closed parser change, which does not alter acceptance of the recorded PIDs.

In this round the ordinary foreground work is unprivileged, SCHED_OTHER/nice0,
CPUWeight=100, in `/system.slice/qa-priority-foreground.service`; the synthetic
contender is its peer. Neither unit is a production mining workload.

| Condition, median MiB/s | Bazzite | Ubuntu |
|---|---:|---:|
| Baseline before | 10094.25 | 8625.50 |
| Process idle only | 4257.35 | 4295.50 |
| Group idle, first | 8836.77 | 8665.66 |
| Group idle, second | 8710.30 | 8597.85 |
| Process idle only, repeated | 3882.32 | 4360.91 |
| Baseline after | 10052.28 | 8602.71 |

Taking the median of the two run medians for each condition (six timed samples
per condition, excluding warm-ups) yields:

| Aggregate comparison | Bazzite | Ubuntu |
|---|---:|---:|
| Baseline | 10073.27 | 8614.10 |
| Process idle only | 4069.84 | 4328.21 |
| Group idle | 8773.53 | 8631.76 |
| Process-only degradation | **59.60%** | **49.75%** |
| Group-idle degradation | **12.90%** | **−0.20%** |

Ubuntu's slightly faster group-idle sample is measurement noise, not a benefit
claim. The within-round priority-only contrasts show substantial benefit from
verified group idle with matching topology; the change from Bazzite's first
round to this round also changes foreground placement and **must not** be
attributed solely to the probe fix. The historical 47.48% / 52.68% numbers and
this round's process-only numbers are from different guest boots/runs, so their
absolute differences are not isolated causal effects. Bazzite's cross-slice
failure to improve and remaining peer-group 12.90% loss remain performance
limitations. No global policy, parent weight, quota or governor was tuned.
The contribution of kernel/virtual scheduling to the remaining loss is not
isolated, and no physical-workstation performance claim is made.

### Second-round cleanup controls and recovery

With **only `Restart=no`** as a controller drop-in (ordinary guest config enabled
32 pages; no config mount and no ExecStart override), Bazzite again leaked
32 reserved pages and kept its ledger after SIGKILL; the cleanup hook again
failed with status 233. This rules out both the blocked-worker shim and the QA
config mount as necessary causes. Ubuntu again executed cleanup with status
zero and removed the reservation/ledger.

A separate minimal transient service created a normal var_run_t operation file
and used `/usr/bin/true` as ExecStopPost. The same sandbox, runtime path, file
ownership and mode were used for both cases; only **directory GID drift from
systemd's declared root:root ownership** differed:

| Minimal control | Bazzite hook | Ubuntu hook |
|---|---|---|
| Directory ownership unchanged | PASS, status 0 | PASS, status 0 |
| Directory changed to root:xmrig | FAIL, status 233 + audit setattr denial | PASS, status 0 |

This is real-distro regression evidence for the ownership-reconciliation cause,
not a mocked cleanup result. Exact unit/property, journal, ownership/context and
namespace records are in each collected archive. The
[systemd.exec documentation](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#RuntimeDirectory=)
describes recursive re-ownership when directory ownership differs, and skipping
child ownership changes when the top-level ownership already matches.

**PASS, recovery mechanism in the VM:** invoking the already-existing trusted
`sparehash __cleanup__` after empty-cgroup proof restored Bazzite's pages from
32 to 0 and removed its lease ledger. It also succeeded on Ubuntu. This is an
internal helper exercised as guest root, **not** a new public recovery command,
an automatic guarantee, or an instruction to run it on the physical workstation.
Systemd had removed the runtime directories by then, so owned operation state
was absent; that removal alone had not released the hugepages. Automatic
abnormal-exit recovery therefore remains **FAIL / unresolved** on Bazzite until
the structural correction is approved and validated.

## Delivery disposition

- **PASS:** corrected positive capability detection/installed idle unit on both
  images; recorded child controls, CPU accounting and task priorities; controlled
  peer-group contention comparisons; ownership controls; trusted guest recovery.
- **FAIL:** automatic Bazzite abnormal-exit cleanup, with a demonstrated 32-page
  reservation leak. The root cause is diagnosed, but no speculative policy,
  permission or runtime-layout change is included in this fix.
- **BLOCKED:** implementing the proposed protected-runtime layout correction
  pending explicit owner approval for any new privileged paths; the pre-existing
  dual-image reboot gate remains blocked by transport diagnosis. No guest reboot
  was attempted in this investigation.
- **NOT TESTED:** a corrected cleanup layout under SIGKILL/blocked-worker exit,
  physical cgroup-idle effects, physical contention, other foreground cgroup
  hierarchies, or certification that group idle alone guarantees negligible
  desktop impact. The original blocked-worker nonzero-exit failure remains
  relevant; this investigation's active-lease termination case uses SIGKILL.

Both physical services were confirmed inactive/dead at the beginning and end.
No physical service stop/start, installation/configuration/security change or
synthetic workload was performed. All four investigation guests were forced off
and discarded; final lab status listed no running domains. No attempts remain
pending. PR #1 remains open/unmerged; no release tag, RC or readiness claim.
