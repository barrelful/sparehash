# S1: best-effort Steam detector

> **S1 stage record.** S2 subsequently retired the old modules and configuration
> fields described as temporarily retained below. For current behavior and
> migration see [the gaming contract](../gaming-integration-contract.md).

## Result and scope

**S1's automated/synthetic runtime acceptance gate passed.** No S2–S5 work was
performed. This is not RC readiness, real native/Proton validation or a claim of
complete gaming coverage. The owner will test real games after the RC gates.

The previous experiments remain intact. Before production edits, a targeted,
recoverable snapshot was verified under
`~/sparehash-archive/s1-gaming-experiments-20261025-225924/`. Its archive, binary
patch, original status and checksums preserve 28 source/document files and the
existing task-2 evidence. The full T0 preservation was not repeated.

## Implementation

- Added `sparehash/gaming_scan.py`, adapting Lutris's pinned
  `get_steam_game_pids()` implementation. Source/licence and modification details
  are in [`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md) and its source
  header. Upstream revision: `1f32fe0e46e2a5d92fad9db2846c7cb7becd20c3`,
  **GPL-3.0-or-later**. No Lutris installation/import or runtime dependency.
- The adaptation scans numeric proc entries, reads bounded NUL-separated
  command lines and matches a reaper launch prefix containing `SteamLaunch`
  followed by any numeric `AppId=`. No predetermined App ID, per-game options,
  Steam client prerequisite, environment scan or user-home access.
- Relative to Lutris: argument-boundary matching instead of incidental
  substrings; process/read/time limits; candidate UID/start identity checks;
  explicit error results instead of silently skipping every `OSError`.
- Kept the scanner in one small new module so S1 does **not** delete or rewrite
  the previous gaming modules. The production runtime no longer imports their
  log manager or session-completeness guard. Their retirement belongs to S2.
- Reused the existing gaming worker, one-second poll, safety queue, controller
  and Jobs. No new process, service, IPC protocol or provider framework.
- CLEAR means a successful scan found no recognised game, not proof of absence.
  Recognised candidates inhibit; genuine failures/unknown results inhibit.
  Definite process disappearance is normal churn. A known positive can inhibit
  with a degraded error reason if another process inspection fails.
- Limits: 16,384 numeric processes, 65,536 bytes per cmdline, 500 ms cooperative
  scan budget, two-second accepted-result freshness. A blocked read cannot be
  interrupted by the cooperative clock check; controller-owned expiry stops
  renewing clearance independently of worker completion.
- The controller checks accepted scan freshness in observation reduction,
  application/owner-loop ticks and synchronous miner authorization. Slow
  aggregates cannot re-stamp a negative result; late out-of-generation CLEAR
  cannot erase a newer inhibition. A blocked worker cannot queue more scans.
- Existing restart dwell is unchanged (default 60 seconds); no second cooldown
  state machine. Scanner CLEAR releases only gaming, never other protections.

### Configuration and status

Default `gaming.enabled=true`, `integration="auto"`, `steam_log=false` now uses
the scanner even when `gaming.users=[]`. Existing hook permissions/account
checks are not widened. Disabled detection is explicitly reported as disabled.
For S1, legacy `steam_log=true` or non-auto integration requests inhibit with
`legacy_gaming_configuration`; they do not activate the old root log path or
silently switch an explicitly selected policy. S2 can retire those fields with
proper migration guidance.

Controller/public JSON status adds `gaming_detection`:

```json
{"detector":"steam-reaper","best_effort":true,"enabled":true,
 "state":"not_detected","reason":"ok","scan_age":0.1,
 "last_successful_scan_age":0.1,"duration":0.002,"matches":0}
```

States are `detected`, `not_detected`, `unknown` or `disabled`. Public coverage
is `BEST_EFFORT`; other blockers/running status remain separate. No raw command
lines, game titles, PID list or environment values are exposed.

## Validation

| Check | Result |
| --- | --- |
| Focused scanner/runtime/controller, lifecycle, power/thermal, miner specification, units and status tests | 203 passed; 130 subtests passed |
| Complete local unit suite | 1,394 passed; 625 subtests passed |
| Mypy | Clean, 60 production source files |
| Ruff: new scanner and changed gaming tests | Passed |
| Ruff: other touched production files | Eight pre-existing findings; per-file/rule counts unchanged from snapshot/HEAD baseline |
| `git diff --check` | Passed |
| Build and attribution packaging | Wheel/source builds passed; both carry notices, wheel also carries GPL licence |

The full local suite ran with Python 3.11. The VM runtime fixtures ran with
Ubuntu Python 3.12.3 and Bazzite Python 3.14.8; this is not the full CI matrix.
Tests cover normal churn, all numeric IDs, multiple candidates, malformed input,
access/I/O/bounds failures, identity changes, startup inhibition, stale result
authorization, hung-worker expiry/no backlog, existing dwell, and independent
battery/thermal/manual/lifecycle blockers. Historical gaming tests remain present
and passing, not silently removed or marked expected-failure.

### Disposable VM production path

Both fresh baselines ran **unmocked `runtime.run`, actual protected controller
IPC, Jobs, operation publication, ExecCondition authorization, miner exec and
systemd stop/empty proof**, using the existing unit templates and sandbox.
Source manifests were verified before staging; no host installation was touched.

The fixtures used a populated PAM background login/user.slice, a synthetic
reaper command line and an unprivileged idle dummy miner that performs no
hashing, pool traffic or network operations. No claim of an actual graphical
desktop/game session is made. Three-second fixture dwell compressed independent
scenarios; the production 60-second default is unchanged. The fixture resets
the miner's burst counter between scenarios, not its shipped limits or auth.

Both guests demonstrated:

- Successful no-match scanning allowed the dummy miner to start despite the
  populated login/session context.
- A recognised synthetic reaper stopped it and emptied its cgroup within five
  seconds; disappearance recovered via the existing dwell path.
- A real proc cmdline exceeding the scan byte bound produced UNKNOWN and stopped
  mining; removal of that fixture recovered through successful scans/dwell.
- Manual pause remained active across successful negative scans. Independent
  battery/thermal controller debug blockers inhibited despite no game detected.
  Physical sensor behavior is unit tested, not certified by this VM fixture;
  the VM retained the existing default with physical thermal monitoring disabled.
- The miner ran as non-root `xmrig`, with actual `SCHED_IDLE` and nice 19.
  Stopping the controller also stopped the bound miner.

| Measurement | Ubuntu Noble | Bazzite 44 |
| --- | ---: | ---: |
| Synthetic reaper trigger → miner cgroup empty | 0.834 s | 0.812 s |
| Baseline process count | 171 | 240 |
| Mean / maximum baseline scan | 1.215 / 1.822 ms | 1.556 / 4.180 ms |
| Estimated scanner CPU at 1 Hz, one CPU | 0.122% | 0.155% |
| Mean / maximum scan with 200 extra idle processes | 2.441 / 3.471 ms | 2.630 / 3.611 ms |
| Estimated scanner CPU at 1 Hz with extra processes | 0.244% | 0.262% |

CPU estimates use process CPU time over 100 direct scans, extrapolated to the
one-second cadence; they are not whole-controller CPU, gaming-load or hardware
power measurements. Bazzite remained SELinux Enforcing without policy changes.
All disposable instances were stopped/discarded; final lab status was empty.

## Failures, remaining risks and next stage

- Initial fixture status polling exhausted root's existing IPC rate limit and
  caused safe authorization refusal. Slower fixture polling fixed it; production
  rate limits were not weakened. An optional reset of unloaded fixture units
  initially returned an error; this was corrected in the test driver.
- One guest-agent transfer timed out; that disposable was forced/discarded and
  the final Ubuntu run used a fresh guest. Both final runtime assertion sets passed.
- Final journals contained recovered bounded miner-job retries (two on Ubuntu,
  one on Bazzite). No unsafe authorization or failed final assertion was observed;
  the existing strict authorization/retry machinery remains unchanged.
- The post-run Bazzite AVC journal filter returned exit 1 and was not captured
  successfully. Enforcing was confirmed before/after, but **no clean-AVC-audit
  claim** is made. Complete SELinux/lifecycle acceptance remains S3 work.
- An initial offline build lacked cached Hatchling. A normal build fetched the
  already-declared build tool and succeeded; the final offline rebuild also
  passed. Runtime dependencies remain empty.
- Best-effort false negatives/positives remain possible: missing/changed reaper
  markers, unmarked surviving games, spoofed argv and restricted process
  visibility. Non-Steam/unrecognised games are not covered. Low priority is not
  equivalent to stopping during a missed game. No native/Proton/Flatpak or
  KDE/Gaming Mode hardware validation is claimed before owner testing.
- Install/uninstall/reinstall/reboot, the full Python CI matrix and RC readiness
  were not attempted. VM staging was a runtime fixture, not installer evidence.

The only temporary complexity is retaining the old experimental modules/tests
and legacy configuration fields as explicitly required for S1. The new path has
one scanner/result type and local controller freshness/status handling; no extra
observer architecture. **Recommend S2 after owner approval**, with focused
retirement/migration of inactive gaming code, not general repository cleanup.

Evidence, source/attribution snapshots, fixture driver, manifests and failed-run
logs are private and gitignored under `.omo/evidence/v020/task-2/s1-steam/`.
