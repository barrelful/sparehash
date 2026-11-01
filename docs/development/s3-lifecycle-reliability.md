# S3: lifecycle reliability

**S3's automated/synthetic lifecycle acceptance passed on both named VM images.**
This stage is limited to lifecycle/cross-distribution reliability; no S4
release-surface work, RC or real gameplay acceptance is implied.

## Narrow production fixes

1. `install_xmrig.py`: use exclusive temporary probe directories directly under
   the existing executable `/opt`, not a permanent unreceipted
   `/opt/sparehash-probe` root. Context-managed snapshots are removed on success,
   rejection and exceptions. `/tmp` and `/var/tmp` are hidden by the existing
   `PrivateTmp` probe sandbox; its isolation is unchanged. No new permanent
   privileged path, service or SELinux policy was introduced.
2. `bootstrap.py`: complete source publication consumes its verified private
   staging copy. Previously successful installs left `.staging-*` under
   `/opt/sparehash`, causing strict uninstallation to refuse unrecorded content.
   Cleanup checks the exact staging inventory and digests, then unlinks only
   known files and removes empty directories. Unexpected/changed content refuses
   cleanup. Interrupted publication retains staging for investigation/recovery.
   The uninstaller's receipt, foreign-content and miner-empty checks are unchanged.

Regressions cover probe cleanup across success/rejection/exception, successful
bootstrap cleanup, interrupted publication and preservation of unexpected or
changed staging content. Existing controller/scanner safety code is unchanged.

Pre-edit source/test preservation is verified under
`~/sparehash-archive/s3-probe-lifecycle-20261026-202623/`, with an additional
bootstrap-test snapshot. T0 and earlier archives/evidence are retained.

## Test boundaries

All privileged operations use disposable lab guests, one at a time, with no
network interface and no host filesystem sharing. The host SpareHash installation
is untouched. The existing v0.1.1 Ubuntu/Bazzite baseline evidence was reviewed,
not repeated. Bazzite must stay SELinux Enforcing with AVC text recorded.

The private VM driver uses the actual source installer, installed launcher,
protected runtime/IPC, systemd units and removal code, without mocking their
behavior. Its external synthetic miner is transferred with pinned SHA-256 bytes,
then checked/executed through the actual unprivileged version/dry-run probes.
It reports zero hashrate, never hashes or contacts a pool, and serves only an
authenticated loopback fixture API. This is not a signed upstream XMRig release
or a test of upstream signature acceptance/real mining performance.

Power and thermal callbacks use explicit file-backed sensor fixtures bound only
into the controller's mount namespace; the original configuration/units are
restored before removal. These are actual monitor/controller callbacks, not
physical sensor validation. Three-second dwell compresses independent fixture
scenarios; production defaults are not weakened. Synthetic Steam argv and two
foreground CPU workers test recognised-game stop timing under concurrent load,
not arbitrary foreground workload latency or native/Proton gameplay.

## Local checks

- Complete suite: **1,117 passed; 608 subtests passed**.
- Focused installer/bootstrap/removal/probe/controller/scanner tests:
  **169 passed; 43 subtests passed**.
- Mypy: clean across 56 production modules.
- Ruff: no added findings against pre-S3 snapshots; three pre-existing production
  findings and one older bootstrap-test finding remain in the touched files.
- README inventory and `git diff --check`: passed.
- Offline wheel/sdist build: passed.

An earlier full-suite attempt timed out after reporting a failure without its
traceback. Subsequent complete reruns passed (including the recorded final run); no
existing safety assertion was removed or skipped to obtain that result.

## VM evidence so far

The initial Ubuntu 24.04 disposable passed source installation, real unprivileged
idle scheduling, successful procfs scans, battery/manual independence, thermal
hysteresis/recovery, scanner-bound failure/recovery, lifecycle hold independence
and controller-loss stopping. Recognised synthetic game → cgroup empty under
foreground load: **1.006 s**; controller loss → empty: **0.030 s**.

It then reproduced the bootstrap staging removal defect. Those timings precede
the bootstrap fix and are not final-source lifecycle acceptance. A corrected
fresh Ubuntu retry failed through the guest-agent transport; its lifecycle
outcome is unknown. Both owned Ubuntu guests were forced off/discarded after
guest-agent timeouts. No agent confinement, baseline, policy or host controller
was altered to bypass the failures.

The corrected Bazzite 44 image passed all 18 fixture checks, including:

- Real source install, probe execution, installed-byte/receipt checks and operation.
- Battery/manual independence, thermal hysteresis, scan failure/recovery and
  lifecycle inhibition through the actual runtime path.
- Recognised synthetic game → empty under foreground load: **1.010 s**;
  controller loss → empty: **0.031 s**.
- Foreign content inside the managed source tree causes safe removal refusal;
  withdrawing only that owned test sentinel permits removal without weakening
  receipt checks. External/configuration sentinels and the external miner survive.
- Non-purge removal preserves configuration and manual pause. Explicit retirement
  clearing/reinstall retains pause until resume. Purge removes owned configuration,
  units and CLI. Fresh post-purge reinstall works.
- An actual reboot changes the boot ID and autostarts the controller/miner;
  installed source remains byte-equal with no probe or bootstrap staging residue.
- SELinux stays Enforcing. Both pre-/post-reboot kernel journal audit queries
  succeeded and contained no AVC lines. This is the observed fixture run, not a
  claim about every workload or physical mode.

The final fresh Ubuntu retry passed the same complete sequence and actual reboot
check (18 checks), using the same source manifest as Bazzite. Its recognised
game → empty time was **0.757 s**, controller loss → empty **0.116 s**.
After reboot, its agent was temporarily disconnected; bounded boot-connectivity
checks waited only for reconnection, without retrying a hung command or failed
guest assertion. It reconnected and the boot-ID/autostart/source/residue checks
passed. Earlier timed-out agent runs remain failed evidence, not acceptance.

| Final source / synthetic VM check | Ubuntu Noble baseline | Bazzite 44 baseline |
| --- | --- | --- |
| Install → operation → non-purge removal → reinstall → purge → reinstall | Passed | Passed |
| Foreign-content refusal and foreign sentinel preservation | Passed | Passed |
| Manual state/config retention and owned config/unit/CLI purge | Passed | Passed |
| Actual reboot, changed boot ID, controller/miner autostart | Passed | Passed |
| Unprivileged miner, SCHED_IDLE, nice 19 | Passed | Passed |
| Real monitor callbacks from file-backed sensors, independent blockers | Passed | Passed |
| Recognised synthetic game → empty under foreground CPU load | 0.757 s | 1.010 s |
| Controller loss → miner cgroup empty | 0.116 s | 0.031 s |
| No probe/bootstrap staging residue after successful lifecycle | Passed | Passed |
| SELinux | Not applicable to this Ubuntu image | Enforcing; no AVC lines in captured pre/post kernel journals |

All owned guests, including failed runs, were stopped/forced and discarded;
final lab status is empty. Scope is **Ubuntu 24.04 on the named Noble image and
Bazzite 44 on the named image**, not Debian, Mint, all Atomic systems, Steam Deck
or validated KDE/Gaming Mode gameplay. The loopback/non-hashing external fixture
does not certify upstream XMRig mining performance or signed-release installation.

No scanner, controller, runtime, stop-proof or uninstaller policy code changed in
S3. The user's dirty release-status document remains untouched. Work remains
uncommitted on `atlas/v020-task-2-g1-feasibility`; no push was performed and the
host installation was never modified. **Stopped before S4; recommend S4 only
after explicit owner approval.**

Private source manifests, fixture code, failed-run logs, checksum records and test output live under
`.omo/evidence/v020/task-2/s3-lifecycle/` (gitignored).
