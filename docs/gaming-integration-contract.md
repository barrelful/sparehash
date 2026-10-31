# Gaming integration contract — experimental v0.2

## Steam reaper / best-effort detection

With `gaming.enabled=true` (the default), SpareHash scans `/proc` for Steam
reaper launch arguments containing `SteamLaunch` and any numeric `AppId=`.
`sparehash/gaming_scan.py` adapts Lutris's pinned implementation under
GPL-3.0-or-later; see [third-party notices](../THIRD_PARTY_NOTICES.md).
There is no Lutris runtime dependency, App ID list or per-game configuration.
No Steam client, GameMode package, session bus or user log is needed at setup.
Logged-in desktops and populated user slices do not themselves block mining.

This is **best-effort**, not proof that no games are running. Non-Steam games,
unrecognised launchers, changed reaper markers and games that outlive their
marker may be missed. Process arguments can be spoofed. Low miner priority
does not promise zero contention when detection misses a game.

Real native/Proton gameplay, KDE/Gaming Mode and hardware behavior remain pending
owner testing after RC. Automated synthetic recognition is not gameplay evidence.

## Runtime policy

- Poll every **1 second**, with one scan in flight and no queued scan backlog.
- Recognised game: inhibit mining promptly, independently of restart dwell.
- Successful scan without a match: release only the automatic gaming hold.
- Genuine scan failures, unavailable procfs, exhausted bounds and stale results:
  inhibit mining. Ordinary process disappearance is normal churn.
- Bound each scan to 16,384 numeric processes, 65,536 cmdline bytes per process
  and a 500 ms cooperative work budget. Reads cannot be forcibly interrupted;
  controller-owned two-second freshness expiry also applies to a hung worker.
- The controller rejects out-of-generation negative results, checks freshness
  independently at START authorization and retains miner-cgroup stop/empty proof.
- Recovery uses existing restart dwell (default 60 seconds), never a new timer.

Latency targets are **engineering objectives**, not hard real-time guarantees:
1 second detection/callback and 5 second recognised-trigger to miner cgroup empty,
including queued stop work. This is **not zero game-start overlap**. See the
[S1 report](development/s1-steam-detection.md) for measured synthetic VM results
and their limits, not a guarantee on arbitrary workloads.

Battery, thermal, manual pause and lifecycle protections remain independent.
A clear gaming scan cannot bypass any of them. `sparehash resume` clears only
manual pause, not gaming or another safety hold.

## Configuration and migration

New configuration contains only:

```toml
[gaming]
enabled = true
users = []
```

`gaming.users` retains verified account permissions for existing authenticated
`gaming-start` / `gaming-end` hooks; it is not a scanner scope or prerequisite.
No automatic package installation, group additions or GameMode INI merging is
performed. Existing socket/group authorization is not widened. A peer's end
hook removes only that peer's hold, never an automatic detection hold. A trusted
peer can inhibit indefinitely by not sending its matching end hook.

- Old `integration="auto"` selects this single detector; it is accepted with a
  deprecation warning and omitted on save.
- Old `steam_log=false` is accepted only as a deprecated no-op, omitted on save.
- Explicit `integration="conservative"`, `integration="gamemode"`, unknown
  modes or `steam_log=true` fail with actionable migration guidance, including
  when detection was disabled. No selected policy is silently replaced.
- To migrate, review the best-effort limitations, remove `integration` and
  `steam_log`, and explicitly choose `gaming.enabled=true` or `false`.
  Preserve/review an old disabled value: an earlier installer may have disabled
  protection after a setup failure. Nothing automatically re-enables it.
- Explicit `enabled=false` disables automatic detection and is reported as such;
  it never disables non-gaming safety protections.

Legacy GameMode unmerge and membership/artifact receipt handling remain for safe
removal of previously owned setup. Foreign configuration and memberships must
not be removed; new installations do not read/write user homes for detection.

## Status

`gaming_detection` identifies `steam-reaper`, `best_effort=true`, enabled/disabled,
`detected` / `not_detected` / `unknown` / `disabled`, last successful scan age,
scan age, duration, match count and a bounded fixed reason. No raw command lines,
environment values, titles or PID lists are exposed. Public `coverage_tier` is
`BEST_EFFORT`, `DISABLED` or `UNVERIFIED` when no detector status is available.
“Not detected” is not mining permission while another blocker remains active.

Fixed reasons are diagnostic, not bypass instructions:

| Reason | Meaning |
| --- | --- |
| `ok` | Fresh scan completed; no-match is best-effort only |
| `disabled` / `not_scanned` | Explicitly disabled, or no accepted scan yet |
| `stale_scan` | Controller freshness budget expired or result timing is invalid |
| `scanner_failed` | Unexpected scanner exception; no raw exception/argv exposed |
| `proc_unavailable` / `proc_malformed` | Required procfs data unavailable or invalid |
| `process_unreadable` / `process_malformed` | Genuine process read/parse failure, not ordinary exit churn |
| `identity_changed` | Candidate identity/arguments changed during inspection |
| `cmdline_limit` / `process_limit` | Bounded read/process work limit exhausted |
| `scan_timeout` | Cooperative scan work/timing budget exhausted |

Failures inhibit; a recognised positive also inhibits if another read failed.
Fix visibility/resource/environment issues and wait for fresh successful scans;
never weaken another blocker to make a diagnostic disappear.

## Preserved research, not live fallbacks

The former Steam-log replay manager, session-completeness guard and evidence
reducer were retired in S2 after caller audit and verified preservation. Their
research is preserved outside the shipped project and in
[the pre-consolidation Git history](https://github.com/barrelful/sparehash/tree/af7f2cf/docs/development).
The current [consolidation report](development/v020-review-consolidation.md)
records its disposition; the experiments are not alternative production paths.
Historical FULL/admission requirements and `NO_DEPENDABLE_ROUTE` conclusions
describe that rejected completeness objective, not a current readiness gate.
There is one live detector, no automatic fallback chain or root-log activation.
