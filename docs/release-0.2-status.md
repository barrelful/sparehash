# SpareHash 0.2 — release status

Status: **NOT RELEASABLE YET.** Installation is proven on both target distributions;
removal is not. Do not publish a release or install on a production machine until the
blocker in "Known blocker" is closed and the lifecycle below passes.

## What is proven

Verified on disposable lab VMs (`bazzite-kde-44-20261012-v1`, `ubuntu-noble-20260926-v1`)
from source tree `a96ffb2` (branch `atlas/v020-task-15-gaming-producer`, tip `ce04d6b`):

| Requirement | Bazzite KDE | Ubuntu Noble |
| --- | --- | --- |
| Unattended install (`install.sh --answers … --yes`) | exit 0 | exit 0 |
| Controller | `ready`, active, `enabled` | `ready`, active, `enabled` |
| Miner unit | `inactive`/`static` (held by the gaming guard) | `active`/`static` |
| `sparehash status --json` | `coverage_tier: CONSERVATIVE` | `coverage_tier: CONSERVATIVE` |
| SELinux | `Enforcing` before and after, no SpareHash AVCs | n/a |

Supporting evidence lives in `.omo/evidence/v020/task-29-fix/` (gitignored):
`final-report.md`, `guest-provisioning.md`, `altfiles-realhost.md`,
`row29x-execpath.txt`, and the collected guest logs.

## Known blockers

- A successful install **cannot be uninstalled**: `sparehash: uninstall: receipt: unrecorded content under /opt/sparehash; nothing removed`. The install probe stages its snapshot inside `/opt/sparehash`, the tree the uninstaller receipt-checks; staging inside the receipt-managed tree trips the guard.
- The staging mechanism is unproven: candidate fix stages at `/opt/sparehash-probe` (outside the receipt-managed tree, inheriting the `usr_t` label that installs cleanly), but reinstall after uninstall, reboot persistence (guest reboots repeatedly needed a manual start), and real mining are all unproven.
- Mining is out of scope for 0.2: real-pool mining never exercised; every run used a loopback pool and produced no hashrate. The lab forbids real-pool mining, so no real-pool evidence exists or will be gathered. The 0.2 release claim covers the install, uninstall, reinstall and reboot lifecycle only.
- Candidate fix needs owner approval (it adds a path SpareHash owns): stage at `/opt/sparehash-probe`, receipt guard left intact. `/var/lib/sparehash` fails config attestation (exit 203); `/var/tmp/sparehash-probe` fails with exit 1.

## Prerequisites on a real machine

The installer requires a resolvable unprivileged probe identity and at least one real
gaming user. On Atomic desktops the account database lives in `/usr/lib/passwd`;
resolution there is verified on the owner's Bazzite host (`(99, 99)` resolved with no
`nobody` entry in `/etc/passwd`). The minimal lab baseline ships neither, so guest runs
add a `nobody` and a throwaway uid-1000 user before installing — see
`.omo/evidence/v020/task-29-fix/guest-provisioning.md`. Installation on a host that
lacks these is refused by design rather than guessed at.

## How to verify a release candidate

Run inside the disposable VM lab (`docs/development/vm-testing.md`), never on a
workstation. Each step lists the command, expected output, and what a failure means.

1. Prerequisites. Disposable VM only; network-free baseline; signed XMRig artifact staged offline; resolvable unprivileged identity (e.g. `nobody`, uid 99); real gaming user (uid 1000). On Atomic hosts the account database lives in `/usr/lib/passwd`.
   - Command: `id nobody` and `getent passwd <gaming-user>`.
   - Expected: both resolve to a uid/gid.
   - Failure means: installer refused by design; provision the identity first (see `.omo/evidence/v020/task-29-fix/guest-provisioning.md`).

2. Install with explicit conservative gaming coverage.
   - Command: `install.sh --answers <file> --yes --use-existing` (answers file sets `coverage_tier: CONSERVATIVE`).
   - Expected: exit 0; `sparehash status --json` shows `coverage_tier: CONSERVATIVE`; controller `ready`, active, `enabled`; miner unit `inactive`/`static` on Bazzite (gaming guard) or `active`/`static` on Ubuntu; SELinux `Enforcing` before and after, no SpareHash AVCs.
   - Failure means: check SELinux state (`getenforce`), probe identity resolution, and the extracted payload (`test -f …/install.sh`).

3. Status check.
   - Command: `sparehash status --json`, `systemctl show sparehash.service`, `getenforce`.
   - Expected: controller active and enabled.
   - Failure means: service did not come up; inspect `journalctl -u sparehash.service`.

4. Uninstall --purge.
   - Command: `sparehash uninstall --purge`.
   - Expected: exit 0; no unit, no `/etc/sparehash`, no `/usr/local/bin/sparehash`.
   - Failure means: receipt-managed tree contains unrecorded content (the staging probe). Known blocker; do not proceed.

5. Reinstall.
   - Command: `install.sh --answers <file> --yes --use-existing --clear-lifecycle-latch` (interactive consent required).
   - Expected: exit 0; same status as step 2.
   - Failure means: lifecycle latch not cleared or leftover state from step 4.

6. Real reboot and post-reboot service state.
   - Command: reboot the guest, then `systemctl is-active sparehash.service`, `systemctl is-enabled sparehash.service`.
   - Expected: service active and enabled after reboot.
   - Failure means: manual start needed; reboot persistence unproven.

Release-ready means steps 1-6 pass in both baselines.

## Measurement rules learned the hard way

- Never measure sandbox behaviour through a pipe: `head` masks the real exit status. Capture `PIPESTATUS[0]` or run without a pipe.
- Assert the payload actually extracted before installing: `test -f …/install.sh`, and echo the resolved staging constant.
- `/tmp` is tmpfs in these guests and is wiped by reboots; keep scripts in `/var/tmp`.
- Lab boots flake roughly one run in two; a retry is not a result. Clean orphaned domains with `virsh -c qemu:///session destroy|undefine --nvram` plus removing the run directory.
- A worktree holding uncommitted evidence must be committed before any worktree removal.

## Cross-references

- Lab procedure: `docs/development/vm-testing.md`.
- Fix list: `CHANGELOG.md`.

## Tagging

Do not run until lifecycle passes on both baselines. Exact command:

```
git tag -a v0.2 -m "SpareHash 0.2"
```

## Repository layout

Development now happens in `workspace/sparehash` only. Historical per-task worktrees
were archived to `~/sparehash-archive/` with a branch bundle, and
`atlas/v020-integration` was fast-forwarded to the 0.2 tip. Create a branch and
`git switch` it; only use a worktree when two branches must be touched simultaneously.
