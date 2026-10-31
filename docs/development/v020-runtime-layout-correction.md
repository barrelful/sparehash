# Protected runtime layout correction (proposal before implementation)

Base: `9eec7b46013579ceb64440a515a8f25300ed4650`. The owner approved only the
necessary structural changes below, within the two existing runtime hierarchies.
No scheduling change, host update, policy module or new privileged location is
included. See the [root-cause investigation](v020-priority-cleanup-investigation.md).

## Exact layout

| Path | Owner:group | Mode | Manager / purpose |
|---|---|---|---|
| `/run/sparehash` | root:root | 0711 | systemd RuntimeDirectory; stable parent |
| `/run/sparehash/ipc` | root:sparehash | 0750 | controller-owned protected IPC child |
| `/run/sparehash/ipc/control.sock` | root:sparehash | 0660 | existing group-gated socket protocol, relocated |
| `/run/sparehash-miner` | root:root | 0711 | systemd RuntimeDirectory; stable parent |
| `/run/sparehash-miner/private` | root:xmrig | 0750 | controller-owned protected operation child |
| `/run/sparehash-miner/private/operation.json` | root:xmrig | 0640 | exact existing operation schema |
| `/run/sparehash-miner/private/benchmark.json` | root:xmrig | 0640 | existing internal benchmark configuration |

Parent mode 0711 permits traversal to the protected children, **not directory
listing or file reads** by group/other. No directory or regular artifact is
group-writable; no artifact gains broader read access. The existing socket's
0660 access is retained so authorised IPC clients can communicate; peer-credential
checks and root-only operations remain unchanged. IPC and miner groups do not
gain access to each other's protected child. Consumers use only the new exact
paths; no permissive legacy path or symlink fallback is added.

Systemd continues to run the controller as root:root and manage both top-level
parents. Neither publisher nor IPC server changes those parents' ownership.
Their matching UID/GID prevents systemd's recursive ownership reconciliation
from descending into mixed-group children before ExecStopPost.

`RuntimeDirectoryPreserve=yes` retains the runtime tree across service stop so
systemd does not recursively erase foreign files. Trusted cleanup removes only
the fixed, validated operation/benchmark files and stale owned socket, then
removes protected child directories **only if empty**. Foreign entries and
unrelated persistent state are retained. Empty root-owned 0711 parents may remain
after uninstall; they are ephemeral `/run` scaffolding, not active authority.

## Creation, validation and SELinux expectations

Systemd creates parents before ExecStart. The controller creates protected children
through no-follow pinned paths, initially private, then assigns the intended
root/group/mode. A pre-existing operation child must already have the exact
protected metadata; unsafe, non-directory or symlink paths fail closed rather
than being adopted. Existing descriptor-anchored ancestor checks, exact file
ownership/mode/schema and executable/config binding remain in force.

Descriptor traversal uses Linux `O_PATH|O_DIRECTORY|O_NOFOLLOW` for intermediate
ancestors and a readable directory descriptor only at the requested leaf. This
lets xmrig traverse the 0711 scaffolding without adding directory-read permission;
every ancestor still undergoes the same root-ownership/no-write checks.

Use the distro's existing inherited runtime labels (expected `var_run_t` in
Bazzite), with **no chcon, restorecon exception, custom type or policy change**.
Mixed-group child files may keep their existing labels because systemd no longer
needs to setattr them during directory setup. Validate this under Enforcing
using audit transport/file evidence as well as kernel/service journals.

## Cleanup and lifecycle

- Normal stop: controller revokes authority, independently stops the miner,
  verifies empty, drains workers, and runs the existing cleanup logic. The
  original trusted `ExecStopPost=/usr/local/bin/sparehash __cleanup__` remains.
- SIGKILL or bounded worker nonzero exit: ExecStopPost must actually execute
  successfully, prove the miner cgroup empty, restore the hugepage baseline and
  remove owned runtime artifacts. Empty cgroup alone is not a success criterion.
- Missing/unknown/nonempty cgroup proof: return failure **before** page or runtime
  mutation; keep evidence/state for recovery. Keep existing hugepage-ledger
  ownership, boot-ID and external-page-change protections.
- Restart: cleanup of the stale owned socket allows a clean bind; operation
  publication recreates the protected child and retains exact validation.
- Uninstall: stop both units, verify empty, invoke the existing trusted cleanup
  helper before removing the CLI/accounts, then perform existing receipt-based
  removal. Do not recursively remove runtime children or unrelated state.
- No in-place upgrade/migration is introduced. VM tests cover fresh installation
  and the existing explicit non-purge removal/reinstall path. The physical
  workstation and its current installed version remain untouched.

## Acceptance plan

Fresh Bazzite 44 and Ubuntu Noble clones, sequentially, no reboot: normal
start/stop/restart; 32-page active leases under SIGKILL and stuck-worker bounded
exit; actual hook exit status, page baseline, ledger/operation cleanup; foreign
runtime/config/state preservation; nonempty/unknown proof refusal; successful
recovery/reinstall/purge. Capture exact unit/drop-ins, path metadata/labels,
manager/service/audit evidence and source pins. Preserve SELinux and sandboxing.
Run focused/full regressions, typing, scoped lint, builds and final-head CI.
Evidence stays gitignored under `.omo/evidence/v020/task-2/runtime-layout/`.

This document records the proposal before production implementation. Results and
any evidence-justified adjustments will be appended rather than inferred.

## Local regression evidence

The proposal was committed before implementation in `bf73b58`; the implementation
candidate is `7c23abd`. Focused coverage checks new exact paths, unchanged manager
parent metadata, restrictive child/file modes, stale-socket cleanup, foreign
entry preservation, unsafe artifact/child refusal, no-follow O_PATH traversal
with unsafe-ancestor rejection, non-root/nonempty/unknown cleanup refusal, and
runtime-state preservation when hugepage cleanup warns. Uninstall tests require
cleanup before deleting payload/accounts and refuse an altered cleanup CLI or
failed helper. Existing operation/benchmark validation, IPC and signal/worker
tests also run unchanged apart from relocated fixtures.

Full local suite: **1,193 passed + 619 subtests**. Mypy is clean on 56 production
modules; scoped Ruff, README inventory, offline wheel/sdist, artifact attribution/
runtime-dependency and whitespace checks passed. No runtime dependency was added.

One later local full-suite run reported missing pre-created temporary files in
existing XMRig current/manifest test classes (31 failures, including duplicated
class/subtest reports). Its complete failure log is retained as
`local/tests-delivery.log`. The affected cluster passed **53 tests + 64 subtests**
in isolation, and an immediate unchanged full rerun passed **1,193 + 619** in
24.46 seconds. No assertion, safety gate or skip was relaxed to obtain that
result. The intermittent fixture-loss cause is **not diagnosed**; retain it as
test-harness stability debt rather than claiming every local invocation passed.

VM results below must establish actual hook execution, zero restored pages,
removed lease/operation/socket state, preserved foreign files and clean relevant
audit evidence independently; local mocks alone do not establish those outcomes.

## Preliminary VM attempt (retained QA failure)

At candidate `7c23abdc521c977b6b552e7cc6b85060d61433a4`, both distributions
installed successfully with the relocated layout and unprivileged idle miner.
The initial normal-stop snapshot had an executed, successful cleanup hook and
removed owned runtime artifacts. The driver then failed while checking a
not-yet-created foreign-sentinel manifest (`KeyError: foreign`). This is a
**QA harness FAIL**, not a product failure or full acceptance pass. The check now
treats the pre-sentinel phase as an empty inventory, with a regression also
ensuring later changed sentinels still fail. Both guest archives were collected
and both clones discarded; later lifecycle/abnormal tests were not reached in
that attempt. Records remain under `round1/` and are not overwritten.

## Second attempt: Ubuntu full PASS, Bazzite fault-injector limitation

Source archive `d0957168d47d31cf75a48077781eea204df3c5cd`, production inventory
`cb20973431ccdc7937c0c2a731458e152b85e2cf0e86dfd156bbf75d87b84f5a`, archive hash
`c4010a7c0272fe8ab67cf0cdc023062083c0f1fc9e3e82fe4df62c992c4d01df`:

- Ubuntu completed the full driver with guest exit 0, `complete=true`, and no
  relevant audit denial. Original trusted hook exit 0 restored 32 → 0 pages and
  removed lease/operation/socket artifacts after both SIGKILL and blocked-worker
  nonzero exit. The latter took **12.2674 seconds**. Normal lifecycle, exact
  layout/permissions, nonempty and genuinely unverifiable cgroup refusal,
  foreign-state retention, non-purge reinstall and purge all passed.
- Bazzite passed installation, normal start/restart/stop and nonempty-cgroup
  refusal. The temporary unknown-proof injector tried masking individual manager
  socket inodes with InaccessiblePaths; its setup exited **226/NAMESPACE** before
  the cleanup helper ran. That is **BLOCKED/QA fixture failure**, not a successful
  unknown-proof test or evidence of an application cleanup failure. Later checks
  were not reached. Both clones were collected and discarded.
- The revised injector uses empty read-only tmpfs mounts over manager directories
  in only its own transient helper namespace, preserving NoNewPrivileges and
  ProtectSystem. The real trusted helper and real systemctl remain unchanged.
  Its actual ExecMainCode/ExecMainStatus and journal are captured; status 226
  cannot be mistaken for the helper's expected refusal status 1. Fixture checks
  cover its command contract. Audit evidence is now captured in finalization
  even if an earlier scenario fails. This modifies only QA, not production code
  or the controller sandbox.

## Final scoped VM acceptance — PASS on both distributions

Bazzite's full rerun used archive
`f02c76745b8e4788324a552cbb2a08a227d9780d`, archive SHA-256
`c2f61dfbd6f7c003d1989a31f116387c15dfc5a401fab959153a6c4696a3347c`.
Ubuntu's full run used the `d095716` archive pinned above. Both share the exact
same production source/unit/installer inventory digest
`cb20973431ccdc7937c0c2a731458e152b85e2cf0e86dfd156bbf75d87b84f5a`.
Only the QA fault injector/ordering and documentation differ between archives.
Both returned guest exit **0**, **14 recorded checks**, `complete=true`, and
`relevant_audit_denials=[]`. Complete controller journals contain no repeated
`233/RUNTIME_DIRECTORY` failure.

| Required property | Bazzite 44 | Ubuntu Noble |
|---|---|---|
| Install, exact payload, actual unprivileged miner execution | PASS | PASS |
| Exact parent/child ownership and modes; no legacy path fallback | PASS | PASS |
| Normal start, stop, restart and successful original trusted hook | PASS | PASS |
| SIGKILL with live miner, 32 pages, ledger/operation/socket present | PASS | PASS |
| SIGKILL hook actually executes and exits 0 | PASS | PASS |
| After SIGKILL: pages 0; lease, operation, socket absent; miner empty | PASS | PASS |
| Clean manual restart after SIGKILL | PASS | PASS |
| Blocked scanner worker with live miner and active 32-page lease | PASS | PASS |
| Bounded main-process exit status 1 | PASS, 12.2563 s | PASS, 12.2674 s |
| Blocked-worker original hook actually executes and exits 0 | PASS | PASS |
| After worker exit: pages 0; lease, operation, socket absent; miner empty | PASS | PASS |
| Nonempty-cgroup cleanup refusal before page/runtime mutations | PASS | PASS |
| Genuinely unverifiable manager with actual empty miner: helper exit 1, state retained | PASS | PASS |
| Foreign runtime-child/top-level files preserved across lifecycle | PASS | PASS |
| Foreign persistent state and top-level runtime files preserved through uninstall/purge | PASS | PASS |
| Foreign source-content refusal; non-purge config/manual-state retention | PASS | PASS |
| Explicit reinstall preserves pause until resume; subsequent purge | PASS | PASS |
| Relevant audit transport denials | None | None |
| SELinux | Enforcing throughout | Not applicable to this baseline |

For each abnormal termination, the **before** snapshot independently records
`pages=32`, `lease=true`, `operation=true`, `socket=true`, `empty=false`. The
**after** snapshot records `pages=0`, `lease=false`, `operation=false`,
`socket=false`, `empty=true`, plus the trusted hook's executed PID, timestamps,
`code=exited` and `status=0`. This is not an inference from cgroup emptiness.
Bazzite kernel and full audit transport were both checked; no relabel or policy
exception was used. Root-owned 0711 parents retained their expected group while
mixed-group children were published/read by the real unprivileged miner.

Foreign child sentinels are retained through abnormal/normal cleanup and restart.
Before account removal/recreation, the driver withdraws **only its own exact
nested fixture sentinels**, then removes their empty child directories; this
avoids conflating recycled test account GIDs with an in-place ownership migration.
Top-level foreign runtime files and an unrelated persistent-state sentinel remain
through non-purge removal, reinstall and purge. No runtime directory is recursively
deleted. A pre-existing protected child with mismatched metadata is refused,
not silently re-owned; unknown entries, including interrupted temporary files,
are not adopted as runtime authority or broadly removed.

### Reproduction

Read `docs/development/vm-testing.md`. On one fresh, isolated, networkless clone
at a time, stage a Git archive of the identified source under
`/var/tmp/investigation-source` using the established external launcher's `put`.
Record the full archive hash and production digest, then inside the guest run:

```sh
SPAREHASH_QA_DISPOSABLE_VM=YES python3 \
  /var/tmp/investigation-source/qa/runtime_layout_acceptance.py \
  --source-sha256 SOURCE_DIGEST
```

The driver requires guest root, explicit consent, virtualisation, no guest NIC,
a matching source digest and a fresh installation/runtime hierarchy. It uses
only the owned non-hashing loopback fixture and disposable config. Bundle
`/var/tmp/runtime-layout-evidence`, collect it within the lab's 8 MiB limit, then
force off/discard only the owned clone. Repeat against the other baseline. No
reboot, host share, passthrough, external pool or production credential is needed.
Detailed evidence and hash manifest remain private/gitignored in
`.omo/evidence/v020/task-2/runtime-layout/`.

### Disposition and remaining limits

- **PASS:** the scoped runtime-layout correction and required two-image checks
  above. The previously diagnosed mixed-ownership cleanup failure/32-page leak
  is corrected in these tested cases.
- **FAIL, retained historical QA evidence:** missing initial sentinel inventory
  and the first Bazzite namespace injector; neither is promoted to acceptance.
  The corrected driver completed on a new clone without changing product code.
- **BLOCKED, separate prior work:** the dual-image reboot/transport gate remains
  unresolved; no guest reboot was attempted here. Desktop cgroup hierarchy /
  foreground contention remains a separate investigation, not tuned or cleared.
- **NOT TESTED:** physical-workstation deployment/upgrade, physical faults,
  arbitrary partial-publication crash windows, or in-place runtime migration from
  the prior layout. Unsafe partial/pre-existing metadata fails closed; there is
  no new migration or legacy-path adoption mechanism.

All five owned clones across the three attempts were collected where available,
forced off and discarded. Final lab status had no running domains. Both physical
services were independently verified inactive/dead at the beginning and end;
no physical service, installation, configuration, permission or security change
was performed. No VM attempts remain pending. No merge, release tag or RC.
