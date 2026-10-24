# Gaming Integration Contract

This document describes what SpareHash actually does and does not do for gaming
workloads. It distinguishes FULL coverage, conservative fallback, and refusal
states with measured limits.

## Admission status

**Task 2 admission decision: `NO_DEPENDABLE_ROUTE`**

SpareHash v0.2.0 has NOT been demonstrated for FULL gaming coverage. The
bounded admission research pass (Task 2) returned `NO_DEPENDABLE_ROUTE`, meaning
no validated producer contract or demonstrated lifecycle invariants exist for
full-adapter work. FULL coverage requires validated idle/game distinction in
both Steam Gaming Mode and conventional GameMode sessions, with complete
native/Proton lifetimes, missed-event reconciliation, and recursive empty
proof. This has NOT been demonstrated.

The system implements conservative session inhibition as a fallback mechanism,
not full acceptance. This fallback inhibits mining during covered graphical
sessions but does not constitute full gaming coverage.

## What is implemented

### Optional Steam process-log evidence

`sparehash.gaming_steam.SteamLogProducer` is a bounded, standard-library-only,
same-UID producer. The owner explicitly authorized this runtime implementation
after observing paired additions and removals in a real Steam log. That
authorization is not FULL platform acceptance, and the earlier admission result
above is not rewritten as a successful cross-mode certification.

The caller supplies an absolute log path and a live Steam-client anchor PID,
opens the producer as a context manager under that unprivileged UID, and passes
it in `GamingSessionGuard(..., steam_producers=(producer,))`. The default is
still no producer. The owner closes the producer only after worker evaluation
has stopped. Evaluations perform real source reconciliation rather than return
a re-timestamped cache. The guard runs them on the existing dedicated gaming
worker and reduces the resulting `GamingEvidence` using the caller's `now`.
For a real producer, `now` is the conservative start time of this fresh
evaluation; it must also fall within the producer's own monotonic budget.

The provider component is `steam-log`, UID is a separate evidence field, and
the required coverage/completeness key is exactly `steam-log:<uid>`, following
`GamingReducer`'s existing contract. The version fingerprint includes the parser
version, pinned source/client identity, and a fresh consumer incarnation.
Replacing a consumer therefore revokes the reducer epoch even if its sequence
restarts at one. This is a protocol/incarnation fingerprint, not authentication
of a Steam build. AppID is only the storefront's association; title knowledge
never grants clearance.

#### Exact grammar and clean-start rule

Only these complete LF-terminated tracking records are accepted:

```text
[YYYY-MM-DD HH:MM:SS] AppID <1-20 ASCII digits> adding PID <1-10 ASCII digits> as a tracked process
[YYYY-MM-DD HH:MM:SS] AppID <ASCII digits> no longer tracking PID <ASCII digits>, exit code -?<ASCII digits>
```

An addition may have a space followed by a private suffix and may end in one
CR before LF. The suffix is discarded without decoding or reporting it. A
removal permits no suffix or CR. Tracking markers with invalid syntax are
MALFORMED, not ignored diagnostics.

Clean start means replay **from byte zero**, never a tail window: the entire
file fits the configured history budget, every complete tracking record parses,
at least one addition exists, and every removal matches an earlier addition
with the same PID/AppID. Empty or diagnostic-only files do not establish
clearance. A partial terminal line inhibits until completed. A removal at the
left edge, duplicate addition, or repeated numeric PID within retained history
invalidates continuity. A nonempty final set must also have live, same-UID
pidfds whose start times do not postdate their log additions; an unresolvable
addition at the right edge is UNKNOWN, never inferred dead. Process exit alone
does not erase a tracked entry; a paired removal is required. Once attached,
a removal while its pidfd is still alive is contradictory and inhibits.

This definition assumes that the supplied file really begins the retained
producer history. A balanced file cannot prove that Steam or another process
did not discard an earlier *balanced* segment before this consumer opened it,
nor reveal a completely omitted game. There is no invented startup sentinel.
The measured pairing supports reconciliation of this format, not an
authentication or completeness proof for arbitrary Steam versions.

#### Bounds and loss handling

Constructor parameters, with module-constant defaults:

| Parameter | Default | Exhaustion |
| --- | ---: | --- |
| `max_history_bytes` | 1,048,576 bytes | OVERFLOW |
| `max_line_length` | 4,096 bytes excluding LF | OVERFLOW |
| `max_tracked_processes` | 64 simultaneous records | OVERFLOW |
| `max_events` | 4,096 log lines plus inotify records per evaluation | OVERFLOW |
| `freshness_seconds` | 5 seconds | STALE |

Each evaluation replays the bounded complete file and hashes the previous
prefix; runtime and retained memory are bounded by these limits. History is
never silently trimmed. Prefix edits, same-size rewrites, truncation, rotation,
source identity/ownership changes, symlinks at any path component, nonregular
files, failed reads, client death, pidfd identity loss, consumer close and
inotify loss cannot clear. An inotify subscription is installed before the
initial read. No root execution, broad `/proc` traversal, command-line or
environment reads, source writes, credential access, or save-file access occurs.
Only explicit client/tracked PIDs' bounded `stat` metadata is read.

Health is `OK`, `STALE`, `DISCONNECTED`, `MALFORMED`, or `OVERFLOW`, using the
existing Literal. Inhibitory samples carry no completeness token. A partial
line or not-yet-established empty history can recover through a healthy append;
grammar, resource, source replacement, and rewritten-history failures remain
inhibitory. `SteamLogProducer` is the worker-side supervisor: on the next
evaluation after STALE or transport loss it opens one fresh reader incarnation,
requires the same pinned live client and source inode, carries the retained
prefix/hash and all game pidfds forward, and replays the entire bounded file.
It closes the old reader only when the replacement can retain those continuity
anchors. Source replacement, truncation, or changed retained bytes cannot be
repaired by this automatic path.

The new fingerprint makes `GamingReducer` revoke the old epoch; that recovery
evaluation remains inhibited, and only another fresh evaluation in the new
epoch can clear. At the existing one-second worker cadence the deterministic
append-race and transient-read-loss tests recover in two seconds (within the
five-second objective), without authorizing the stale or old-epoch samples.
Persistent faults stay inhibited. Closing the consumer disables recreation.
External selector users must re-register `fileno()` after recreation; the
production gaming worker uses bounded evaluations, not this descriptor.

Silence is not activity: an
unchanged healthy file can be freshly reconciled. An unserviced consumer past
its freshness budget cannot resume issuing clearance in the old incarnation;
its supervisor must pass the fresh-incarnation checks above.
Wall-clock discontinuities and legitimate PID reuse may conservatively inhibit.

#### Wiring boundary and remaining blocker

All pre-existing no-producer behavior remains unchanged: no sessions plus a
fresh empty cgroup proof can clear; any session present still blocks. With an
explicit producer, every tracked UID must have current-epoch, fresh, healthy,
complete INACTIVE evidence. Logind's idle hint is diagnostic only and cannot
prevent clearance on its own. ACTIVE,
UNKNOWN, missing, incomplete, stale, or mismatched evidence cannot clear, even
after logind session disappearance. Session topology changes revoke the epoch.

The recursive whole `user.slice` emptiness proof now applies **only when no
producer is configured**. Owner decision 2026-10-24: with a producer present,
a complete fresh INACTIVE sample is the idle authority, because a normal
logged-in desktop always populates `user.slice`, so requiring an empty slice
made desktop clearance unreachable. The cgroup snapshot still gates probe
health and remains diagnostic evidence; it is no longer permission.

Consequence, stated plainly: a game that Steam does not track in this log - a
standalone binary, Lutris, a manual Wine run - is not seen by this producer,
so SpareHash can mine while such a game runs. Non-Steam launchers are
unsupported for 0.2 and a second producer is required before any claim of
complete coverage.

The root controller does not automatically discover Steam paths/PIDs or open
user logs. Rootless producer supervision and transport to that controller also
need a separately defined deployment boundary. No config switch, service,
privileged helper, or FULL gaming coverage claim is introduced by this optional seam.
Tests exercise real temporary files, inotify and child lifetimes; they do not
certify real native/Proton gameplay or either desktop mode.

### Conservative session inhibition (fallback)

When `gaming.enabled = true` and `gaming.users` lists configured accounts,
SpareHash monitors those users for active GameMode sessions via `gamemoded -s`
queries over the user's D-Bus session bus. The controller also accepts explicit
`gaming-start` and `gaming-end` hooks from users in the `sparehash` group.

The gaming blocker activates when:
- Any configured user has an active GameMode session (queried independently)
- Any hooked user has sent `gaming-start` without a matching `gaming-end`

The blocker clears only when:
- All configured users report inactive GameMode status
- All hooked users have sent `gaming-end` or disconnected
- Fresh complete evidence arrives (no stale observations)

This is conservative fallback coverage, not FULL acceptance. It inhibits mining
during covered graphical sessions but does not prove exhaustive negative
coverage of all gaming workloads.

### Engineering objectives (not hard real-time guarantees)

The system targets the following latency objectives under declared loads:

- **Callback latency**: ≤1 second from covered trigger to observer callback on
  the same monotonic host clock
- **Empty proof**: ≤5 seconds from trigger to proven recursive miner-cgroup
  empty, including detection and queued job time

These are initial engineering objectives subject to feasibility testing. They
are not hard real-time guarantees and not zero game-start overlap. Actual
measured values depend on system load, detection queues, and job scheduling.
Maximum objective breach fails acceptance.

Stops ignore the 60-second automatic start dwell. Recovery requires fresh
complete evidence plus existing dwell. Manual resume behavior is unchanged but
cannot bypass any real gaming hold.

### Manual pause interaction

A manual `sparehash pause` persists across controller restarts via state file.
It is independent of gaming blockers. A manual pause does not clear gaming
holds, and gaming holds do not clear manual pauses. Both must be clear for
mining to start.

`sparehash resume` clears only the manual pause. It cannot bypass an active
gaming blocker. If gaming is active, mining remains inhibited until gaming
clears.

## What is NOT implemented

### FULL coverage

FULL coverage requires:
- Validated producer/version/capability fingerprints for Steam Gaming Mode and
  conventional GameMode
- Complete native and Proton game lifetimes with missed-event reconciliation
- Initial/absent/unknown distinction with session identity persistence
- Game-end/child emptiness with recursive cgroup proof
- Steam and monitor restart handling
- Session mode switch detection
- New UID and upgrade invalidation

The optional Steam producer implements bounded log reconciliation and
incarnation/freshness handling, but not the full coverage contract above.
The system does not claim full gaming coverage.

### Per-game configuration

SpareHash does not perform per-game setup, game-specific configuration, or
arbitrary game detection. It monitors configured users through the official
GameMode socket and accepts explicit hooks. This is not equivalent to FULL
coverage of arbitrary games.

### Blanket noninterference guarantee

The system does not guarantee noninterference with all gaming workloads. It
inhibits mining during covered sessions but cannot prove absence of interference
for workloads outside validated containment.

## Fallback-only status

**Fallback-only remains reduced-function NO-GO pending owner decision.**

Conditional fallback approval is not reduced-release approval. The system
operates in a reduced-function state when only conservative session inhibition
is available. This is not equivalent to full gaming coverage and requires explicit
owner decision before release.

## Unknowns and limitations

### Session bus requirement

The gaming check requires an active D-Bus session bus at
`/run/user/<uid>/bus`. If the configured user is not logged in or the bus is
missing, the query is inconclusive and SpareHash inhibits mining (fail-closed).

### Lingering teardown

After a game session ends, the system remains in UNKNOWN state until fresh
complete logind/systemd inventory and recursively empty/absent covered
user-manager/session cgroups prove the lifetime ended. A lingering user manager,
SSH/background process, or unconfigured graphical UID can conservatively prevent
clearance.

Missing pathname, empty parent cgroup.procs, Steam crash, or elapsed dwell
alone is not proof of session end.

### Monitor loss

Loss of monitor connection, timeout, parse error, disconnect, suspend/resume,
queue overflow, or epoch/producer changes invalidates clearance immediately.
The system does not silently persist enabled=false when FULL coverage is lost.

### Uncovered workloads

Workloads outside validated containment (arbitrary games, non-GameMode
graphical sessions, unsupported Steam configurations) are not covered. The
system refuses unattended mining for UNVERIFIED/UNSUPPORTED configurations.

### Steam Gaming Mode vs GameMode

Steam Gaming Mode and conventional GameMode are distinct mechanisms. The system
monitors GameMode via `gamemoded -s`. The optional log producer is not a validated
Steam Gaming Mode integration. The two are not interchangeable for coverage purposes.

## Configuration

The `[gaming]` configuration section accepts:

```toml
[gaming]
enabled = true
users = []  # list of Unix account names to monitor
```

`enabled = false` or empty `users` does not make the system unattended mining
ready. It disables gaming monitoring entirely. Runtime loss of coverage
automatically inhibits mining; it never silently persists as enabled.

## Bazzite and integration modes

For Bazzite or similar distributions, `gaming.integration="auto"` requests FULL
coverage and reports action-required if unadmitted. Explicit `"conservative"`
selects limited fallback. `"gamemode"` is conventional legacy coverage only.

The system retains `enabled` and `users` keys for backward compatibility. These
do not imply FULL coverage.

## Safety properties

Despite the admission gap, the system maintains these safety properties:

- **Fail-closed**: Unknown or stale gaming observations inhibit mining
- **Fresh evidence required**: Authorization requires observation ≤1 second old
  with matching generation
- **No silent downgrade**: Loss of coverage inhibits rather than silently
  persisting
- **Manual pause independence**: Manual pause and gaming blockers are separate;
  both must clear for mining
- **Controller loss stops mining**: `BindsTo=` relationship ensures miner stops
  with controller
- **Generation revocation**: Blocker changes increment generation and revoke
  launch permission

## Testing and verification

The system includes tests for:
- Gaming blocker activation and clearance
- Hook authentication via SO_PEERCRED
- Fresh evidence requirements
- Manual pause independence
- Fail-closed behavior on missing/inconclusive observations

Tests do not establish FULL coverage. They verify the implemented conservative
fallback mechanism.

## References

- Task 2 admission decision: `NO_DEPENDABLE_ROUTE`
- Task 30 actual results: conservative session inhibition implemented
- Plan sections C, D, G2
- `sparehash/gaming.py`: GameMode observation module
- `sparehash/controller.py`: Blocker registry and state machine
