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
