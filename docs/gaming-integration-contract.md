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

None of these are implemented. The system does not claim full gaming coverage.

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
monitors GameMode via `gamemoded -s`. Steam Gaming Mode integration is not
implemented. The two are not interchangeable for coverage purposes.

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
