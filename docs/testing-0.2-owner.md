# Experimental v0.2: owner installation and gameplay testing

Read the [current review report](development/v020-review-consolidation.md) and
[PR #1](https://github.com/barrelful/sparehash/pull/1) first. Automated fixtures
cannot certify real native/Proton gameplay, Bazzite KDE/Gaming Mode, physical
sensors, upstream mining performance or zero foreground impact. This is an
owner-supervised experiment, not a production-readiness claim.

For the latest source-pinned VM outcomes and unresolved transport/performance
findings, see [current acceptance](development/v020-current-acceptance.md).
The separate [owner-run CPU comparison](development/cpu-contention.md) needs no
reinstallation or configuration changes; it must not be run by the assistant on
the physical workstation.
From a reviewed checkout, run `python3 qa/run_cpu_contention.py` as your ordinary
user. It guides stopped/mining/stopped measurements and saved-session comparison;
only you manually start/stop mining. It never deploys the checkout or updates an
installation. See the linked guide for thermal aborts and interpretation limits.

## Before installation

**During the stability sprint:** do not replace an installation merely to test
the separate stability branch. Finish the already-approved recovery/reinstall
with its exact pinned guide, then provide the second physical CPU comparison
using the unchanged methodology. Record the installed source identity, comparison
results, temperatures and any abort; keep credentials private. Further owner
installation/testing instructions await review of the sprint and that benchmark.

- Test the **latest PR #1 head on `review/v0.2-pre-rc`**, identified by its full
  Git commit SHA as described below, on a machine you deliberately choose.
  The historical `f79718d` candidate and its old wheel/sdist hashes in
  `release-0.2-candidate.md` are **not the current owner-testing target**.
  Review [platforms](platforms.md) and [security](../SECURITY.md).
  Back up your configuration and keep a recovery terminal available.
- Do not upgrade an existing installation in place. Upgrades, rollback, config
  editing and reconfiguration are deferred. Resolve any old migration or partial
  removal with the maintainer; never delete receipts or safety holds.
- Do not copy VM sensor overrides, synthetic XMRig, throwaway wallets or QA
  consent variables onto your workstation. Destructive automated QA belongs
  only in the disposable VM lab.
- Choose your own wallet and pool privately. The VM evidence uses no real mining
  or public pools; it is not evidence of pool connectivity or mining profitability.

### Identify the current review commit

In a clean source checkout (do not discard local changes), fetch and select the
current review head explicitly:

```sh
git fetch origin review/v0.2-pre-rc
git switch --detach FETCH_HEAD
git rev-parse HEAD
git status --short
```

The printed full SHA must match the **current head commit in PR #1**, and that
head's GitHub checks must be reviewed before testing. Record this SHA with every
hardware/gameplay result. The branch can advance: re-check the PR head rather
than trusting an old downloaded archive, historical artifact digest or copied
short SHA. A clean checkout does not authorise an in-place installation over an
existing deployment.

From that identified checkout, review read-only preflight:

```sh
sudo ./install.sh --preflight
```

If you accept the disclosed effects, stage an interactive installation without
starting the controller or enabling boot startup:

```sh
sudo ./install.sh --no-start --no-enable
sudo sparehash config show
```

During setup, keep best-effort gaming detection enabled. Enable the thermal guard
only after checking the available sensor and choosing appropriate thresholds:
**thermal monitoring defaults to off**, and VM sensor values do not validate your
hardware. Leave optional hugepages off initially. No Lutris/GameMode package,
gaming user list or per-game launch option is needed for scanning.

When ready for a supervised run, start the controller without opting into boot
startup yet:

```sh
sudo systemctl start sparehash.service
sudo sparehash status --json
```

The default restart dwell is 60 seconds, and other blockers must also clear.
`gaming_detection.state=not_detected` is **not** proof of game absence or permission
to mine. Inspect all blockers and miner state. `unknown` means scan failure/staleness,
not a recognised game and not a successful negative.

## Gameplay matrix — owner results required

Repeat the relevant rows on each desktop/mode you intend to claim: Ubuntu desktop,
Bazzite KDE and Bazzite Gaming Mode. Include at least one native and one Proton
title where available; record title/version, launcher and session mode locally.
Do not fill an unavailable row with a synthetic result.

| Scenario | Record |
| --- | --- |
| Idle desktop and Steam library/menu | Detection state, blockers, miner state and foreground responsiveness |
| Launch a native game | Recognised or missed; detection → stop timing; foreground impact |
| Launch a Proton game | Same, separately from native results |
| Start controller with a game already running | First scan result; whether mining remains inhibited |
| Exit each recognised game | Scan recovery, all blockers and restart after configured dwell |
| Restart Steam while a game remains active | Detector/reaper survival, misses or failures; miner behavior |
| Game/reaper survives launcher or game exit | Which process survives and whether inhibition persists |
| Recovery after a genuine scan fault | `unknown` inhibits; fresh success and dwell required to resume |
| Manual pause while game state changes | Pause remains until an explicit resume |
| Normal foreground workload alongside mining | Responsiveness/contention; low priority is not zero impact |

Use `sudo sparehash status --json` at a modest rate (about once per second),
not a tight loop that exhausts IPC limits. Record `gaming_detection` reason/ages,
blockers, miner status and timestamps separately. Known unrecognised games are
best-effort limitations: pause manually rather than weakening the scanner or
another protection. Do not simulate sensor failures on your workstation to
repeat destructive QA; physical sensor checks require separate supervised care.

## Stop and recovery

Keep these controls available:

```sh
sudo sparehash pause                 # persistent manual hold
sudo systemctl stop sparehash.service # stops controller and its bound miner
sudo systemctl is-active sparehash-xmrig.service
```

Termination revokes controller launch authority and attempts the explicit miner
stop **before** waiting for observation workers. A worker that cannot drain within
the fixed 12-second teardown grace causes a logged, nonzero controller exit,
rather than an unbounded Python executor join. Existing systemd `BindsTo` and
`ExecStopPost` remain; protected cleanup still requires empty-cgroup proof. A
nonzero exit is a failure diagnostic, not a clean-shutdown/empty-proof claim.
Kernel or systemd failures are not covered by a hard real-time exit guarantee.

If a recognised game does not stop mining within the five-second test objective,
or a genuine scanner failure permits mining, stop the test and report the safety
regression. Never disable SELinux, edit service containment or erase lifecycle
records to force recovery. A missed unrecognised game is recorded separately.

Resume only after inspecting the cause: `sudo sparehash resume` clears the manual
hold, not battery/thermal/gaming/lifecycle blockers. If you intentionally disabled
boot startup above, a reboot will not auto-start the controller. Enable it later
only if you deliberately accept that behavior.

Removal is `sudo sparehash uninstall`; `--purge` additionally removes unchanged
receipt-owned configuration, not all state or foreign files. Preserve receipts
and diagnostics after a refusal. Reinstall after completed removal needs the
explicit retirement-clear consent printed by the installer; it is not an upgrade.

## Reporting results

Share the commit, OS/image, session mode, native/Proton result, recognised/missed/
unknown outcome, stop/resume timing, blockers and foreground impact. Redact wallet,
pool credentials, tokens and private paths from logs. Never post raw process argv
or personal credentials. Update validation labels only from actual observations;
an untested title or mode remains pending.
