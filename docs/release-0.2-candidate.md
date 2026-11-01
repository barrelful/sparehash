# Experimental v0.2 candidate acceptance

> **Historical engineering record.** PRs #1 and #2 have been merged into `main`.
> Statements below about draft PRs, pending merges, RC proposals and selected
> candidate hashes describe their original snapshots, not current release status.
> Use [v0.2.0 release notes](release-0.2.0.md) and the published tag for release
> identity. The original evidence and failed attempts below are retained intact.

## Stabilisation sprint — automated gates passed

Work is isolated on `atlas/v020-task-2-stability`, based on reviewed recovery
delivery `41c0e788a84b97c1e947b5a3ad2074e95846123d`. It does not replace the owner's
tested recovery bundle `21d4960d99813f2da4d1d3c923f9d373e2973158`, access the physical
installation, change scheduling or change the CPU comparison methodology.

- **Lifecycle:** read-only removal preflight; durable version-removal intents;
  cleanup checkpoint; absent-account/membership retry; source retained through
  unit/account/reload failures and launcher removed last. Focused tests cover
  valid large manifests, four version-deletion interruption boundaries, early
  refusal, hugepage warnings and reload retry. Numeric manifest ownership is
  retained through owned-group deletion; checkpointed retries require the matching
  owned group receipt if its NSS identity is already absent. Final self-source interruption is
  still a documented late recovery limit, not an automatic-repair promise.
- **Test reliability:** affected XMRig fixtures are created in `setUp`, not during
  collection; duplicate testcase re-exports removed. Construction/no-I/O and
  repeated-case regressions pass. Ten repeated focused suites passed. This fixes
  demonstrated lifetime hazards; it does not prove the original intermittent
  file-loss cause. Test-count reduction reflects removed duplicate collection.
- **Local verification:** 1,245 tests + 565 subtests; production typing on 56
  modules and scoped changed-file lint passed. Corrected-source Bazzite and Ubuntu
  each passed all 22 recorded VM checks, including actual reboot. Do not
  treat private-tree tests as real-distro installation/reboot evidence.
- **Physical benchmark gate satisfied:** owner reports 39.08% foreground
  degradation on `f348ed1` versus 34.39% on `21d4960`, with 7.53% higher foreground
  throughput while mining on the new installation. SCHED_IDLE/nice19,
  `cpu.idle=1`, `cpu.weight=1` confirmed; both services inactive after testing.
  The owner accepts the current scheduler for experimental v0.2; no further
  optimisation is requested unless practical evidence warrants it. Native/
  Proton recognition, real sensors and foreground responsiveness remain physical
  owner checks; VM fixtures do not certify them.

The owner approved branch publication and draft [PR #2](https://github.com/barrelful/sparehash/pull/2)
for existing hosted CI; PR #1 and its branch are unchanged. All five CI jobs passed
on production-source snapshot `5de94c6` ([run](https://github.com/barrelful/sparehash/actions/runs/41299137285));
Python 3.11–3.14 each passed 1,243 tests, one existing prerequisite skip and 563
subtests. For the later negative-GID regression/reporting head's own CI status,
use draft PR #2's checks; these historical counts are not its final-head result.
No merge, release tag or RC declaration is implied. The older
selected-candidate report below is retained as history, not current hashes or CI.

### Retained Bazzite failure and correction

At `de92087c115bb90770368f34806efc1fb49517cc`, a fresh Bazzite clone installed,
completed a real guest reboot (changed boot ID), autostarted and verified exact
payload. Manual/battery/thermal/game guards, controller loss, blocked scanner,
bounded shutdown and hugepage allocate/restore **0 → 32 → 0** passed. Serial stock
commands worked after reboot: this attempt is not a transport failure.

Uninstall then failed at its final leaf-removal store open because the owned
`sparehash` group was already deleted, but the canonical store still resolved its
GID through NSS. Source/shim were retained; the clone and evidence were collected
and discarded. This is a **product FAIL**, not complete acceptance. The subsequent
`5de94c6` correction retains the validated numeric manifest GID and tests both
same-run deletion and retry after group disappearance/reload failure. It does not
recreate accounts or bypass manifest/path/ownership validation. Retained private
evidence: `.omo/evidence/v020/task-2/stability/bazzite/`.

The existing unchanged VM comparison measured 52.1% synthetic contention in this
4-vCPU guest despite SCHED_IDLE/nice19. That is VM evidence, not RandomX or a
physical benchmark. It motivates retaining the foreground-impact warning, not a
scheduler change. The completed owner comparison below now satisfies that gate;
the synthetic result is not substituted for it.

### Completed physical CPU comparison (owner-reported)

The owner supplied the stopped/mining comparison without assistant access to the
physical machine or result files. Saved comparison reference:
`~/sparehash-contention-results/comparison-20261101T104014Z-2b8ee82a6593`.
The results above describe this hardware/workload, not a zero-impact guarantee or
universal scheduling claim. The existing benchmark methodology and scheduling
implementation are unchanged. This result is accepted as sufficient for the
current experimental scheduler; no repeat benchmark is required by this sprint.

### Corrected-source VM attempts — blocked before product execution

Both used exact source `5de94c65513268accc4aeaff8743632cbf4769a0`, archive SHA-256
`c8fccb9f1d124af0325ac5ecffaf7d63f606e4520356fe630755e6a5392fdf33` and the source
inventory below. Neither reached source transfer or installation:

| Baseline | Actual outcome | Classification |
| --- | --- | --- |
| Bazzite 44 | `start`: guest agent unavailable after 300 seconds; stock diagnostic/collection calls could not reach serial login | Boot/management availability blocker; no product command ran |
| Ubuntu Noble | `start` returned; first stock `/usr/bin/true` lost the guest agent, subsequent calls found it disconnected | Guest-agent/management blocker before any product execution |

This does **not** identify whether the underlying cause is guest boot, controller
synchronization or another lab issue. The authorized interfaces returned no guest
diagnostic payload, so further attribution would be speculation. Failed command
outputs and pins are retained under `stability/bazzite-reboot-5de94c6/` and
`stability/ubuntu-reboot-5de94c6/`; no guest evidence archive could be collected.
All three sprint clones were forced off/discarded; final lab status had no running
domains. No baseline, transport confinement, networking or security setting changed.

**Disposition of those attempts:** retries stopped without host-infrastructure work.
The lab maintainer should restore stock boot/login/`true`/reboot command access on
fresh clones through the documented interfaces, preserving baseline integrity.
The next step was to rerun the unchanged source-pinned install → actual reboot →
verify workflow on both images, including normal removal/reinstall. At that stage
the corrected uninstall had focused/full/hosted tests, **not a real-distro pass**.
The later owner-requested runs below supersede this earlier blocked disposition,
without promoting its failed attempts to passes.

The owner's earlier Ubuntu waiver applied only to the separate one-off Bazzite
recovery. It does not waive this sprint's dual-distribution acceptance gate.

### Owner checklist and readiness disposition

1. Complete the independently approved recovery/reinstall only with its exact
   pinned procedure. Do not deploy this branch over that installation in place.
2. Physical stopped/mining/stopped comparison: **completed and accepted** as
   reported above. No assistant scheduler changes or physical installation
   access occurred; no additional benchmark is required absent new problems.
3. After VM gates and source review, separately authorize any new installation;
   check explicit no-start/no-enable behavior, status, pause/resume/stop, native/
   Proton recognition, real sensors and foreground responsiveness. Preserve
   failed-operation records; do not force or delete lifecycle evidence.

**Recommendation:** suitable to propose an **experimental v0.2 release candidate**
for owner review. Corrected-source Bazzite/Ubuntu lifecycle and reboot gates now
pass, and the physical benchmark prerequisite is satisfied. No known failing
functional release gate remains in this sprint. This is not release publication,
merge/tag approval, production readiness or a full-gaming-support claim.
No further scheduler optimisation is needed on the currently supplied evidence.
The existing final self-source crash window remains a practical recovery limit;
the fixture lifetime correction does not conclusively diagnose historical file
loss. These limits are disclosed rather than replaced by mocks or weaker checks.

### Fresh owner-requested acceptance at `b18e1c8`

Both images received the exact archive from
`b18e1c8ecb43392edb59691c4720b2476b1a2437`, archive SHA-256
`da50b250e835002360b0f983d07e8511da5e4dc5c0ce94f0b84d4ef10f99b66a`.
Runtime/units/QA inventory is unchanged from the selected production build below:
`9b5cda95415517415930f84741a163ae9afa81ee4f1f01344e6544e992563fc6`.

**Bazzite 44: PASS, 22 checks.** Install and verify exited 0; summary `complete=true`.
An actual reboot changed the boot ID and exact-payload autostart passed. The
reboot command's serial result had a UTF-8 decode failure, retained as a transport
error; independently successful post-reboot stock commands and changed boot ID
proved execution rather than treating the failed command response as acceptance.
Runtime guards, controller-loss/worker shutdown, hugepages **0 → 32 → 0**, foreign-
content refusal, non-purge config/state retention, explicit pause-preserving
reinstall and final purge all passed. This directly validates the corrected
owned-group/manifest-store ordering on a real distro. SELinux remained Enforcing;
full audit transport was collected in addition to the driver's kernel audit. It
contains only two stock `tuned_t` mac_admin denials, retained, not suppressed;
no product/recovery/Python denial was found. The VM-only synthetic comparison
reported 51.4% degradation; it does not supersede the accepted physical result.

**Ubuntu Noble: initial fresh attempt INCONCLUSIVE.** Preflight stock commands,
source transfer, installation and enabled idle/nice19 fixture passed. At reboot,
the guest agent disconnected and eight stock boot-ID probes failed. The outer
runner stopped after its probe count rather than using its intended 180-second
readiness deadline; this is insufficient to diagnose a persistent transport or
product failure. No post-reboot guest summary could be collected. That attempt
is retained rather than promoted to a pass.

**Ubuntu Noble fresh retry: PASS, 22 checks.** The unchanged source installed,
completed an actual reboot (changed boot ID), autostarted with exact payload,
passed runtime/safety/worker tests, restored hugepages **0 → 32 → 0**, refused
foreign content, removed non-purge while retaining config/state, reinstalled with
manual pause preserved until explicit resume, and purged only owned files. Install
and verify exited 0; summary `complete=true`; collected audit contains no denials.
Stock boot-ID probes tolerated transient agent disconnection until reconnection
within the actual 180-second readiness deadline. This corrects the outer runner's
synchronization limit, not SpareHash behavior or the sealed baseline. No host
controller was modified. It does not prove the cause of every older lab failure.
VM synthetic contention was 50.8%; the accepted physical comparison remains the
relevant owner scheduling decision.

All three fresh owner-requested clones were forced off and discarded (six sprint
clones total). Evidence/pins are
under `stability/bazzite-reboot-b18e1c8/` and
`stability/ubuntu-reboot-b18e1c8/`; Bazzite includes the verified summary and full
audit; the passing Ubuntu retry is `stability/ubuntu-reboot2-b18e1c8/` with its
verified summary and audit. Earlier failed attempts above are historical evidence,
not the current dual-distro outcome. No physical machine, scheduling or security-
policy changes. No owned VM is left running.

### Selected build identity (not a release)

Artifacts built offline from an exported exact Git tree, not dirty checkout bytes:

- Source: `5de94c65513268accc4aeaff8743632cbf4769a0`
- Source/units/QA inventory: `9b5cda95415517415930f84741a163ae9afa81ee4f1f01344e6544e992563fc6`
- Wheel: `273f55266c016ce9c6c45b48b2831cf0e341ae2ebf6edc309a89cad3f1ff6691`
- Sdist: `de9c437afacbe9b9b1a8a4e7698ea14341a9bf32fa6afdf2b59fa65689421ee1`

Artifact checks passed: GPL/third-party attribution shipped, no runtime
dependencies, source inventory included. README command/flag/config inventory,
changed-file lint, production typing and redacted exported-tree secrets scan also
passed. Later reporting commits are not substituted into these build identities.

## Historical candidate

> Historical candidate `f79718d`, not the identity of the later consolidated
> review branch. See the [consolidation report](development/v020-review-consolidation.md)
> for current changes and validation. Old source/artifact hashes below must not
> be used as hashes of the consolidated branch.

**Implementation complete; candidate-wide VM acceptance blocked by Ubuntu's
guest-agent transport. No RC or production-readiness declaration.**

Updated 2026-10-27. S1–S4 implementation and the S5 source/artifact selection and
owner-test handoff are complete. S5's final dual-image lifecycle/reboot acceptance
is not complete. Real-game testing also requires the owner; it cannot be completed
by this automated session.

This is the current revision-3 report. `release-0.2-status.md` is the preserved
owner-authored historical status for an older snapshot, not current installation
guidance. It remains intact in Git. The [S1](development/s1-steam-detection.md),
[S2](development/s2-gaming-retirement.md), [S3](development/s3-lifecycle-reliability.md)
and [S4](development/s4-release-alignment.md) reports retain their original scope.

## Selected implementation

- Commit: `f79718de43bed66fdbb77af202b6c57f27613a09`.
- Source/units/installer/fixture inventory SHA-256:
  `2f9e83812c8f3c7e0c8067854bd232448817b7917dcdd8251cda4f72c6535150`.
- Wheel SHA-256:
  `f314f2369a4b00d72d4020643cb0840b6382474781b57804991482b1a57c1468`.
- Source distribution SHA-256:
  `3bb74a34c83ca8532a2385cfe3407921642a9d1686149d19fab91e1f6b7e82c5`.

These are the selected snapshot's artifacts, not a promise that rebuilding a later
documentation commit produces identical archives. Follow-up reporting/checklist
commits must not change the tested runtime, installer, units or fixture inventory.
No release tag, package registry upload or remote push is part of this operation.

## Acceptance gates

| Gate | Result |
| --- | --- |
| Python 3.11.13 / 3.12.3 / 3.13.5 / 3.14.9 | 1,159 tests + 608 subtests passed on each |
| Mypy | Clean, 56 production modules under all four interpreters |
| Scoped release lint / README inventory / whitespace | Passed; older baseline lint findings are not claimed clean |
| Wheel/sdist | Clean builds, shipped GPL/attribution, no runtime dependencies |
| Hosted GitHub CI | Not run: no push; corresponding local locked-tool gates passed |
| Ubuntu Noble candidate-bound lifecycle/reboot | Blocked: guest-agent timeout; outcome unknown, not passed |
| Bazzite 44 candidate-bound lifecycle/reboot | Not rerun: stopped at the required Ubuntu baseline blocker |
| Real native/Proton gameplay and KDE/Gaming Mode | Pending owner testing |

The disposable acceptance driver replays S3's full install → safety callbacks →
strict foreign-content refusal → non-purge removal → pause-preserving reinstall →
purge → fresh reinstall → actual guest reboot sequence against this commit's
source inventory. Candidate identity, artifact digests, driver/payload bytes and
per-phase journals are recorded in private, gitignored
`.omo/evidence/v020/task-2/s5-candidate/`. Tests use the owned non-hashing fixture,
disabled guest networking, real systemd/IPC and file-backed sensor values. Bazzite
must remain SELinux Enforcing and an observed AVC fails acceptance.

S4's required release-smoke entrypoint passed its 11 checks and cleanup on the
same exact runtime/source digest on Bazzite, with a synthetic recognised-game
stop of 1.012 seconds. Older Ubuntu transport timeouts were unknown outcomes,
not passes. Final acceptance below must come from the selected source, not an
older development snapshot or relaxed lab security.

### Final Ubuntu attempt and stop condition

The fresh `ubuntu-noble-20260926-v1` disposable booted successfully, executed a
small Python/systemd transport check, and accepted the pinned payload transfer.
Launching the full candidate-bound driver then returned the lab error
`guest agent didn't respond to command within '5' seconds`. No guest assertion
result or final journals were returned. The guest was immediately forced off and
discarded; an unobserved run is not acceptance. No baseline, guest-agent
confinement, host controller, security policy or resource limit was altered.

The plan explicitly requires stopping when a required baseline is unavailable.
Accordingly no experimental RC is declared, no candidate-wide reboot pass is
inferred from S3, and no real-game validation is claimed. All owned VMs are
discarded. The working tree is intended for source review and deliberate testing,
not an automatic production installation or an in-place upgrade.

**Next acceptance step:** the owner/lab maintainer must restore reliable Noble
guest-agent command execution. Then replay the unchanged selected source/payload
in a fresh Noble guest and finish candidate-bound lifecycle/reboot acceptance on
both images. Preserve and inspect failed-run evidence; do not weaken containment
or replace the missing real-distro evidence with mocks. The private S5 payload
and driver are retained for this rerun; their SHA-256 identities are recorded with
the evidence. After both VM gates pass, issue the experimental RC report with
gameplay explicitly pending, then use the owner checklist.

## Repository handoff

Implementation was committed in logical groups: preservation of the owner's
status edit, retained feasibility research, the best-effort scanner/configuration
retirement, staging/lifecycle fixes, and release-surface/QA/CI alignment. This
report and the owner checklist are a separate documentation handoff commit.
Nothing was pushed, tagged or installed on the workstation. The owner's historical
`release-0.2-status.md` content was preserved without rewriting it to imply newer
acceptance. Full Git history and the private stage archives remain recoverable.

## Installation and owner testing

See the [owner checklist](testing-0.2-owner.md) for cautious staged installation,
manual stop controls, native/Proton and desktop/mode rows, timing and reporting.
There is no supported in-place upgrade/rollback/config-edit/benchmark route.
No per-game configuration or Lutris runtime dependency is introduced.

Automated success does not validate physical battery/thermal behavior, actual
upstream mining performance, real games, all launchers or zero foreground impact.
Final gameplay labels require owner results. Record recognised misses separately
from genuine scanner failures; strict-safety regressions remain release blockers.
