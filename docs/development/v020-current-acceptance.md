# Current-source v0.2 acceptance

> **Historical engineering record, not current installation guidance.** PRs #1
> and #2 are merged. Later dual-distro acceptance and the accepted physical CPU
> comparison supersede the earlier unresolved dispositions below. See
> [release notes](../release-0.2.0.md) and the candidate report for those updates.
> All original observations, failures and source pins are preserved below.

> Latest stability-sprint disposition is consolidated in the
> [candidate report](../release-0.2-candidate.md#stabilisation-sprint--automated-gates-passed).
> Corrected-source Bazzite at `b18e1c8` now passes the complete install/reboot/
> runtime/uninstall/reinstall/purge workflow with SELinux Enforcing and full audit
> captured. Ubuntu's fresh retry also passes all 22 checks after condition-based
> agent reconnection within the full readiness deadline. Both summaries have
> `complete=true`. Earlier failures below remain historical, not the current
> dual-distro outcome. See the linked report
> for exact pins and retained transport failures.

> Physical benchmark update (owner-reported, 2026-11-01): `f348ed1` foreground
> degradation 39.08%; `21d4960` 34.39%, with 7.53% higher foreground throughput
> while mining. SCHED_IDLE/nice19, `cpu.idle=1`, `cpu.weight=1`; both services
> inactive afterward. The owner accepts the current scheduling benchmark gate as
> satisfied. No physical machine/result-file access or scheduler change by the
> assistant; see the candidate report for the saved comparison reference.

This report distinguishes instrumented disposable-VM evidence, owner-reported
physical observations, and untested hardware claims. PR #1 remains experimental;
this is not an RC declaration or merge approval.

Follow-up root-cause evidence and corrections are tracked in the
[idle-cgroup/cleanup investigation](v020-priority-cleanup-investigation.md).
In particular, the older kernel-only AVC filters below were incomplete: the
follow-up found a decisive SELinux denial in the audit transport/file. Retain
these historical outcomes, but do not read those filters as full SELinux clearance.

## Reproduction

Read `docs/development/vm-testing.md` first. Use the established external lab
launcher and the existing Ubuntu Noble / Bazzite 44 immutable baselines. Never
run installation, fault injection, synthetic contenders or this acceptance driver
on the physical workstation. No guest NIC, shares, passthrough or production
credentials are needed. Keep SELinux enforcing.

1. Resolve the reviewed source commit, archive it with `git archive`, record the
   archive SHA-256 and the output of
   `python3 qa/release_smoke.py --print-source-sha256`. The latter pins production
   modules, templates, installer and existing smoke fixture; the full archive
   digest additionally pins the new acceptance/benchmark tools.
2. Check the physical controller and miner using only safe systemd state
   properties. If mining is active, the owner stops it through their authorised
   mechanism and verifies both services inactive. The restricted lab account has
   no assumed sudo access. Never restart physical mining after testing.
3. Create one fresh clone, transfer the archive through the documented `put`
   interface, and extract under `/var/tmp/sparehash-source`. Do not modify a
   baseline or run both guests concurrently.
4. Inside that guest, run:

   ```bash
   SPAREHASH_QA_DISPOSABLE_VM=YES python3 \
     /var/tmp/sparehash-source/qa/vm_acceptance.py install \
     --source-sha256 SOURCE_DIGEST
   ```

5. Reboot **the disposable guest only**, through the lab `exec RUN ... systemctl
   reboot` interface. After it returns, run the same command with phase `verify`.
   The driver requires a changed boot ID and operational autostart, runs the
   existing lifecycle smoke plus extended faults, and ends with owned-file purge.
6. Bundle `/var/tmp/sparehash-acceptance`, collect the bounded archive, and stop /
   force and discard only your owned clone. Repeat against the other baseline.
   A transport timeout is an unknown outcome, never a PASS: terminate/discard
   the owned guest and retain the failure record.

When reboot transport is blocked, a **different fresh clone** may run `install`
then `exercise` without a reboot to collect the non-reboot scenarios. This is
explicitly a subset, not a replacement gate: it requires an unchanged boot ID,
records `non_reboot_complete` separately, and leaves overall `complete=false`.
The `verify` phase still refuses an unchanged boot ID. Focused regressions cover
both directions so the reboot requirement cannot silently become optional.
For a targeted rerun of only blocked-worker shutdown, hugepages and removal /
reinstall, use `install` followed by `tail` on a fresh clone without reboot.
That phase records only `tail_complete`; it cannot satisfy the full non-reboot
suite or reboot gate.

The tests install an owned synthetic loopback fixture with no pool connection,
temporary guest-only sensor/config overlays and an explicitly labelled scanner
shim. The shim wraps the installed provider with a deliberately blocked worker;
it does not modify receipted product files. Its temporary systemd override is
withdrawn before normal service recovery/removal. The contention service is an
independent four-worker non-hashing idle/nice19 contender, not the production
miner and not a RandomX emulator. All such loads are bounded by the enclosing
lab timeout and disposal; the contender also has systemd RuntimeMaxSec=120.

Detailed machine-specific evidence is private and gitignored under
`.omo/evidence/v020/task-2/current-acceptance/`. The checked-in report publishes
sanitised outcomes rather than host controller files, guest credentials or
production configuration.

## Owner-reported physical evidence

The owner reports current `f348ed1` on Bazzite Deck 44 with SELinux enforcing:
actual RandomX approximately **15.8–16.6 kH/s**, CPU **62–65°C**, peripheral battery
correction working, 1,280 hugepages while mining and zero after game detection,
and successful automatic stop/resume for Portal 2 native and Cyberpunk 2077 Proton
in Steam Gaming Mode. Boot startup remained disabled. These observations are
attributed hardware evidence, not assistant-instrumented results. Stop latency
and foreground performance were not measured quantitatively there.

The separately owner-invoked physical benchmark and exact stopping instructions
are in [cpu-contention.md](cpu-contention.md). It has not been run on the physical
host by the assistant. VM throughput cannot substitute for that result or for
game frame-time evidence.

## Source identity

The deployed production source is
`f348ed165358668d8101cd121cf07ee2f001295f`. This acceptance work adds QA tools,
tests and documentation only; it does not change `sparehash/`, `systemd/`, the
installer or the existing synthetic miner/smoke. Their source-inventory digest is
`55ebb0637b9c9553f0fdbbe848f574e07e33fa8521c49c059ab6277b8f57f894`.
Each run additionally records its actual Git archive commit and archive hash.

## Results

PASS requires an actual successful guest exit and matching evidence; FAIL means
an observed application/assertion failure; BLOCKED means transport or environment
prevented a conclusion; NOT TESTED means no instrumented attempt. Historical
S1–S5 snapshots do not satisfy this run.

### Automated quality gates

- Full local suite: **1,167 passed + 605 subtests**.
- Mypy: **56 production files clean**. Scoped Ruff, README inventory, whitespace,
  offline wheel/sdist build, artifact attribution/dependency checks and an
  exported-tree redacted secrets scan passed.
- [Hosted run 41226969866](https://github.com/barrelful/sparehash/actions/runs/41226969866)
  on `a69bb506e427339dec62afde568df2323b99f5ac`: **all five jobs passed**, including
  Python 3.11–3.14 and packaging. Each interpreter reported **1,159 passed,
  one existing bubblewrap-prerequisite skip, and 605 subtests**. No new skip or
  relaxed safety requirement was introduced.

### Preserved preliminary attempts

- Archive `13df634fdf76933018d81def02c3aa5824c33a30`: Ubuntu installation,
  installed bytes/receipts, default negative scanner, unprivileged idle/nice19
  miner and enabled guest boot startup **PASS**. Post-reboot transport **BLOCKED**
  (`Guest agent ... not connected`). Bazzite startup **BLOCKED** (no guest agent
  after the lab's 300-second boot deadline; serial login also unavailable).
- Archive `bb82349520866a868ef0b6b9d6557cd5bd714f81`: Ubuntu's same installation
  checks **PASS**, but verification launch and subsequent collection **BLOCKED**
  by five-second guest-agent execution/synchronisation timeouts. A successful
  small Python-version command between failures did not establish reliable
  execution; no later safety or performance result is inferred.
- In that second attempt, Bazzite installation, unprivileged idle/nice19 miner,
  enabled startup and **actual changed-boot-ID reboot/autostart with exact
  installed payload PASS**. The first foreground measurement **FAIL (QA harness)**:
  Python 3.14 forkserver workers could not re-enter inherited `/var/roothome`
  after dropping to `nobody`. The fixture now explicitly enters `/var/tmp` and
  no longer masks the original failure by stopping a never-created contender.
  A focused regression covers both requirements. No product code changed.
- All four preliminary clones were forced off and discarded. Bazzite's collected
  second-attempt summary reports `complete=false`, SELinux `Enforcing`, and no
  kernel-journal AVC lines. The private archive and transport records remain
  under `round2/`; failed evidence is not overwritten by the fixture retry.
- The fixture retry at `a69bb506e427339dec62afde568df2323b99f5ac` reached Bazzite
  source extraction, then lost serial transport during installation. The physical
  host subsequently refused SSH, preventing immediate collection/cleanup. The
  owner rebooted the workstation. On reconnection both physical services were
  confirmed `inactive/dead`; the interrupted owned clone was found running and
  explicitly forced off and discarded before the next attempt. No completion or
  performance result is inferred from that interrupted installation.
- After the owner's host reboot, the same exact archive was retried sequentially
  on fresh Bazzite and Ubuntu clones. Both again passed installation/payload,
  default scanner and idle-priority checks. Bazzite reboot returned an uncertain
  serial-console outcome and never regained login; Ubuntu's guest agent remained
  disconnected. Both bounded post-reboot readiness windows expired. Collection
  was unavailable, and both clones were forced off and discarded. These
  `round4/` results are **BLOCKED**, not product passes or diagnosed product
  failures. No further reboot retry was substituted for a successful gate.

### Ubuntu blocker and proposed next step

The dual-image reboot gate remains **BLOCKED**. The owner/lab maintainer should
diagnose the existing offline Ubuntu
guest-agent launch/synchronisation failure using disposable clones and retained
dispatcher records, then rerun the unchanged source-pinned driver. Do not weaken
guest confinement, add networking, modify immutable baselines in place, or
substitute mocked unit tests for distro evidence.

### Separate non-reboot evidence (both distributions)

Archive `1972591fce8274d02df458851ac003d3b5863912`, SHA-256
`4f66053e04c49cbbf88eb5e5c23e731a58a98041a4248d1fd068ed24df589677`, ran
`install` then `exercise` on fresh clones. Collected summaries remain
`complete=false`: both stopped at a QA-only blocked-controller fixture startup
error. `PrivateTmp=yes` correctly hid the fixture's original `/var/tmp` path.
The repair uses an explicit read-only bind to `/run/sparehash/qa-controller.py`,
without disabling PrivateTmp, weakening SELinux or modifying receipted product
files. A regression verifies the generated bind and execution path. Both clones
were discarded after collecting their evidence.

| Instrumented check | Bazzite 44 | Ubuntu Noble |
|---|---|---|
| Exact installed payload, normal install/start, unprivileged idle/nice19 miner | PASS | PASS |
| Negative scan, recognised game, failed scan and exit/recovery via dwell | PASS | PASS |
| Game recognition to empty miner cgroup under two foreground loads | PASS, 1.0109 s | PASS, 1.0099 s |
| Controller SIGKILL to empty miner cgroup | PASS, 0.1216 s | PASS, 0.0243 s |
| Battery discharge/recovery and independent manual hold | PASS | PASS |
| Peripheral-only missing-present/unknown-status clearance | PASS | PASS |
| Ambiguous scope inhibits and recovers | PASS | PASS |
| Thermal trip, hysteresis, recovery | PASS | PASS |
| Malformed sensor read, missing sensor, missing sensor across restart | PASS | PASS |
| Independent lifecycle hold not cleared by negative scans | PASS | PASS |
| Blocked-worker fixture startup | FAIL (QA harness, repaired for targeted rerun) | FAIL (QA harness, repaired for targeted rerun) |

Bazzite remained **SELinux Enforcing**; the collected kernel journal filter had
no AVC lines. However, its controller journal recorded one forced-death
`ExecStopPost` startup failure (`233/RUNTIME_DIRECTORY`, permission denied),
followed by successful automatic service recovery. Empty-cgroup proof passed,
but **forced-death cleanup is not fully cleared** by that observation. This
requires diagnosis rather than an unproven permission/security change.

### Quantitative synthetic CPU contention

Four normal SCHED_OTHER/nice0 foreground workers competed against four synthetic
SCHED_IDLE/nice19 busy workers in an independent transient service. All five
contender processes (including their sleeping parent) were checked for the idle
policy/nice; cgroup CPU accounting confirmed actual CPU consumption. There was
one one-second warm-up followed by three three-second samples per condition.
The fixture never hashed RandomX or connected to a pool.

| Throughput, MiB SHA-256 input/s | Bazzite | Ubuntu |
|---|---:|---:|
| Baseline samples | 10107.93, 10106.05, 10092.28 | 8358.92, 8928.66, 8563.46 |
| Contended samples | 5307.80, 5327.36, 5090.60 | 4029.11, 4125.33, 4052.23 |
| Baseline median | 10106.05 | 8563.46 |
| Contended median | 5307.80 | 4052.23 |
| Degradation | **47.48%** | **52.68%** |

The measurement operation completed, but the large degradation is an **unresolved
performance concern**, not proof of foreground-priority success. A `PASS
quantitative_cpu_contention` line means the measurement and scheduling checks
completed; it does not impose or satisfy a responsiveness threshold. These are
bounded four-vCPU VMs under the lab's aggregate execution cap; CPU cgroup
hierarchy, virtual scheduling and workload differences need investigation before
attributing the cause. No policy, weights, governors or privileged paths were
changed to improve the numbers. Guest CPU hwmon temperatures were unavailable
(`[]`), not zero. Physical quantitative contention remains **NOT TESTED**; use
the separate owner-run procedure, not these figures as a hardware prediction.

### Final targeted attempt and shutdown-cleanup finding

Archive `310b5e2a351a42f294b644d36cfdf1a778c944cd`, SHA-256
`45189915264d7c21104acf530f5ebdc06a690c9a23ad73fc3ea17c835874412c`, ran
`install` then `tail` without reboot on fresh clones. Bazzite returned guest exit
zero and a collected summary with `tail_complete=true`, `complete=false`, SELinux
`Enforcing` and no kernel-journal AVC lines. Ubuntu's install-command launch
timed out in the guest agent, and collection also failed synchronization; its
remaining targeted checks are **BLOCKED/unknown**, not assumed executed or passed.

| Targeted property | Bazzite 44 | Ubuntu Noble |
|---|---|---|
| Stuck scanner becomes stale, inhibits, and empties miner cgroup | PASS | BLOCKED |
| Restart after stuck scanner recovers | PASS | BLOCKED |
| SIGTERM with live miner and blocked scanner: empty cgroup | PASS, 0.0216 s | BLOCKED |
| Worker teardown fallback exits nonzero within bounded grace | PASS, 12.1327 s, main exit 1 | BLOCKED |
| Hugepage allocation, pause release, resume allocation, normal-stop cleanup | PASS, 0 → 32 → 0 → 32 → 0 | BLOCKED |
| Hugepage ledger removed after normal stop | PASS | BLOCKED |
| Foreign payload refuses uninstall without deleting sentinel | PASS | BLOCKED |
| Non-purge preserves configuration and manual state | PASS | BLOCKED |
| Explicit reinstall preserves pause until manual resume | PASS | BLOCKED |
| Purge removes owned files, preserves foreign sentinels, leaves empty cgroup | PASS | BLOCKED |

**FAIL / unresolved shutdown-cleanup finding:** after each intentionally blocked
worker's nonzero exit, Bazzite's `ExecStopPost` process failed before executing the
trusted cleanup CLI, with `233/RUNTIME_DIRECTORY` and permission denied setting
up `/run`. This repeats the earlier forced-SIGKILL observation. The miner was
empty and the bounded process exit worked, but the cleanup hook itself did not
run successfully. A successful later normal-stop hugepage test does not prove
cleanup after a stuck-worker/crash with an active hugepage lease. That combined
case is **NOT TESTED**. The source of the directory failure (product/systemd/
fixture interaction) is not diagnosed; no permission, policy, security or trusted
path change was guessed to suppress it. Next work should isolate the failure
on a disposable Bazzite clone and obtain directory/context and systemd-execution
evidence before proposing a narrow fix and regression.

### Final disposition and physical host

- **PASS:** the individually listed, source-pinned synthetic properties only.
- **FAIL:** QA fixture failures retained above; observed cleanup-hook failures
  remain unresolved. Fixed harness errors are not attributed to product code.
- **BLOCKED:** full final dual-image lifecycle/reboot acceptance, final Ubuntu
  teardown/hugepage/removal checks, and root-cause diagnosis of the runtime-directory
  failure. Reboot was observed once on Bazzite in the preliminary run, but the
  final repeatable dual-image gate was not completed.
- **NOT TESTED:** assistant-instrumented physical contention, frame-time/latency
  measurements, real-hardware battery/thermal fault injection, and combined
  abnormal-exit hugepage cleanup. Owner-reported gameplay/hashing remains separately
  attributed evidence, not a substitute for these measurements.
- **Performance unresolved:** the 47.48% / 52.68% synthetic degradation needs
  investigation and the separately supervised physical comparison. No universal
  acceptance threshold was supplied, and no low-impact claim is justified.

At the beginning, both physical services were active/running. The restricted
account's noninteractive sudo check required a password; **the owner**, not the
assistant, stopped the controller. Both services were independently verified
inactive/dead before VM workloads, after the owner reboot, and at final cleanup.
The assistant issued no physical mining stop/start, ran no physical synthetic
workload, and changed no physical installation/configuration/security setting.
All owned test guests, including the interrupted clone after reconnection, were
forced off and discarded; the final lab status listed no running domains.

No more VM attempts are pending. PR #1 remains unmerged, no tag/RC was declared,
and v0.2 is **not release-ready** on this evidence. The next actions are the
owner/lab maintainer's transport diagnosis, cleanup-hook root-cause investigation,
and optional owner-run benchmark—not an automatic mining restart.
