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

## Known blocker

A successful install **cannot be uninstalled**:

```
sparehash: uninstall: receipt: unrecorded content under /opt/sparehash; nothing removed
```

The install probe stages its snapshot inside `/opt/sparehash`, which is the tree the
uninstaller receipt-checks. The two alternatives tried so far also fail to install:
`/var/lib/sparehash` fails the config attestation (exit 203) and
`/var/tmp/sparehash-probe` fails with exit 1.

Candidate fix (needs owner approval because it adds a path SpareHash owns):
stage at `/opt/sparehash-probe` — outside the receipt-managed tree, inheriting the
`usr_t` label that installs cleanly, with the receipt guard left intact.

Unproven alongside it: reinstall after uninstall, reboot persistence (guest reboots in
the lab repeatedly needed a manual start), and real mining (never exercised; all runs
used a loopback pool and never produced hashrate).

Mining is explicitly out of scope and unproven for 0.2. Real-pool mining has never
been exercised: every run to date used a loopback pool and produced no hashrate.
The disposable VM lab forbids real-pool mining, so no real-pool evidence exists or
will be gathered inside it. The release claim for 0.2 covers the install, uninstall,
reinstall and reboot lifecycle only; it does not cover mining, hashrate, or pool
connectivity.

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
workstation:

1. install with explicit conservative coverage: `install.sh --answers <file> --yes --use-existing`
2. `sparehash status --json`, `systemctl show sparehash.service`, `getenforce`
3. **`sparehash uninstall`** — must exit 0 and leave no unit, no `/etc/sparehash`, no `/usr/local/bin/sparehash`
4. reinstall (`--clear-lifecycle-latch` needs interactive consent)
5. reboot the guest, then confirm `sparehash.service` is active and enabled

Release-ready means steps 1–5 pass in both baselines.

## Measurement rules learned the hard way

- Never measure sandbox behaviour through a pipe: `head` masks the real exit status.
  Capture `PIPESTATUS[0]` or run without a pipe.
- Assert the payload actually extracted in the guest (`test -f …/install.sh`, and echo
  the resolved staging constant) before drawing conclusions.
- `/tmp` is tmpfs in these guests and is wiped by reboots; keep scripts in `/var/tmp`.
- Lab boots flake roughly one run in two; retry, and clean orphaned domains with
  `virsh -c qemu:///session destroy|undefine --nvram` plus removing the run directory.

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
