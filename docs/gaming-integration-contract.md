# Gaming integration contract (v0.2.0 C1 admission)

## Status: NOT ADMITTED — full cross-mode coverage is blocked (NO-GO for G0)

| Field | Recorded value |
|---|---|
| Admission decision (this Task-2 run) | `BLOCKED_ENVIRONMENT` (`qa/probe_gaming.py --read-only`, no authorized capture surface) |
| Underlying C1 route conclusion | `NO_DEPENDABLE_ROUTE` for a complete maintained GLOBAL both-mode producer |
| Full cross-mode support (FULL) | **Not admitted.** Wave 2 / full-support expansion is blocked at gate G0. |
| Conservative session fallback | A separate, reduced-function fallback; it is **not** full acceptance (see below). |
| Mechanism shipped by this task | None. This task ships research, an evaluation runner and this contract only. |

This document is the record required by plan task 2 ("establish an admissible
full cross-mode lifecycle contract or stop with NO-GO"). It is self-contained
and cites every primary source. It does **not** authorise an adapter: no
unadmitted production provider is written, and no heuristic is presented as
FULL integration.

The admission machine-readable record for this run lives in
`qa/probe_gaming.py` → `admission.json` (strict schema validated by
`validate_admission`). Re-running the probe rootless with `--read-only` and no
`--capture-source` reproducibly emits `BLOCKED_ENVIRONMENT`, because there is no
authorized disposable Bazzite/Steam surface attached to this workspace. A
captured `contract.json` can be replayed with `--capture-source DIR` to evaluate
a candidate; that path never starts Steam.

## Why the decision is non-admissible

Admission (`ADMISSIBLE_CANDIDATE`) requires **one** complete, maintained,
GLOBAL producer/integration that, while Steam is idle, distinguishes idle from
gameplay in **both** Steam Gaming Mode **and** KDE, with:

1. a nameable, versioned producer whose behaviour is supported upstream;
2. complete **initial inventory** at producer startup (not user testimony);
3. a **lifetime owner** that accounts for every native and Proton lifetime,
   including crash, launcher-survival and detached-child cases;
4. **health signals** that detect monitor loss/EOF/malformed output and reconnect;
5. a **negative-proof invariant** — clearance only from a complete current-epoch
   inventory, never from scope/reaper silence or a finite set of positive trials;
6. demonstrated **budgets**: incremental idle CPU ≤ 1 % of one logical CPU over
   10 minutes, incremental RSS ≤ 32 MiB, and a recorded query/wakeup frequency
   with session scaling.

No such contract was found. The evidence below shows each candidate is partial,
mode-specific, private-format, or unable to prove complete negative coverage. A
finite number of successful trials cannot prove exhaustive absence, so no
adapter is shipped for an unproven signal.

## Candidate producer inventory (evaluated)

| Candidate | Both-mode? | Initial inventory | Lifetime owner | Health | Negative proof | Verdict |
|---|---|---|---|---|---|---|
| Maintained GLOBAL Steam/Bazzite lifecycle API | No complete public contract exists | unavailable | unavailable | unavailable | unavailable | **NO ROUTE** |
| Per-game systemd scopes + recursive cgroup population | Partial; desktop Steam does not consistently create scopes | per-launch only | scope, not global | cgroup watch only | scope/reaper silence only | **REJECTED for clearance** |
| Reaper-parent fallback (Steam reaper PID) | Partial; private format | no | reaper parent, crash/detach gaps | no | silence only | **REJECTED for clearance** |
| `STEAM_GAMES_RUNNING` / window focus / Gamescope atoms | Gaming Mode only | no | no | no | absence misses live games | **Diagnostic only** |
| Gamescope-session unit + logind graphical session | Yes (both modes) but cannot tell idle from gameplay | session inventory available | session lifetime | supported signals | cannot prove "no gameplay" | **CONSERVATIVE fallback only** |
| GameMode (`gamemoded`) | Where installed; **removed** on the target image | per-registration | application-registered | socket | requires app opt-in | **Legacy coverage only** |

## Primary evidence (all URLs and paths cited)

### S2 — Bazzite target image (pinned)

- Bazzite pinned `Containerfile` (commit `9258b2c43ff163c9ac024627a1ae76b7d78605fa`)
  removes GameMode from the image with `dnf5 -y remove gamemode`:
  <https://github.com/ublue-os/bazzite/blob/9258b2c43ff163c9ac024627a1ae76b7d78605fa/Containerfile>
  → GameMode cannot be the both-mode mechanism on the target.
- Deck SDDM session config `holo.conf` autologins to
  `gamescope-session-ogui-steam.desktop`:
  <https://github.com/ublue-os/bazzite/blob/9258b2c43ff163c9ac024627a1ae76b7d78605fa/system_files/deck/shared/usr/lib/sddm/sddm.conf.d/holo.conf>
  → Gaming Mode is the default session; KDE must be covered separately.

### S3 — Valve Gamescope AppID scopes (consumer, not a producer API)

- Valve Gamescope commit `6191bc8b12825280a21b7802458f3c2a06d945e6` parses
  `app-steam-app${appid}-${reaperpid}.scope` from `/proc/PID/cgroup` and retains
  a **reaper-parent fallback because desktop Steam does NOT consistently create
  per-game scopes**:
  <https://github.com/ValveSoftware/gamescope/commit/6191bc8b12825280a21b7802458f3c2a06d945e6>
  → Scope/reaper observation is a positive-inhibit or corroboration signal with
  known coverage gaps, not an exhaustive producer contract.

### S4 — OpenGamingCollective gamescope session unit

- OGC `gamescope-session-plus@.service` (commit
  `08e65a62ed3d7acc6ae17db637091d402a55ae2a`) is a user service bound to the
  graphical session with `KillMode=mixed`:
  <https://github.com/OpenGamingCollective/gamescope-session/blob/08e65a62ed3d7acc6ae17db637091d402a55ae2a/usr/lib/systemd/user/gamescope-session-plus@.service>
  → Supported liveness source, but sibling scopes are not proven dead when the
  unit stops and the unit cannot distinguish idle Steam from gameplay.

### S5 — systemd supported APIs

- logind D-Bus interface (`ListSessions`, `SessionNew`, `SessionRemoved`):
  <https://github.com/systemd/systemd/blob/main/man/org.freedesktop.login1.xml>
- systemd unit dependency semantics (`BindsTo=`, `PartOf=`, `KillMode=`):
  <https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html>
  → These are supported session/identity/liveness APIs, **not** gameplay APIs.
  They can drive a CONSERVATIVE fallback, never a FULL idle/game distinction.

### In-repository consumers

- `sparehash/gaming.py:107-148` — current GameMode probe: per configured UID,
  `gamemoded -s` over the user session bus; unknown/inconclusive inhibits.
- `sparehash/runtime.py:55-82` — bounded per-UID gaming futures folded into a
  single observation; unknown becomes an inhibitor.
  → The existing runtime already implements fail-closed reduction; what is
  missing is a producer that can *clear* it from complete evidence.

## Lifetime argument (why no route is admitted)

For a FULL claim, every native and Proton lifetime must be attributable to the
producer: launch → gameplay (including overlay/focus loss/paused) → exit, plus
game crash, launcher survival, detached children, Steam crash/restart, session
mode switch, logout/relogin, new UID, controller restart, and reboot with prior
inhibition. The available sources cover only fragments:

- Steam per-game scopes are **not consistently created** (S3), so their absence
  proves nothing.
- The reaper-parent fallback is a **private format** with documented crash and
  detached-child gaps (S3).
- Gamescope session/logind signals prove a session exists, not that gameplay is
  absent (S5).
- GameMode is **removed on the target** (S2).

Therefore clearance cannot be derived from a complete current-epoch inventory in
both modes, and the negative-proof invariant fails. This is why the route is
`NO_DEPENDABLE_ROUTE` even where a positive inhibitor can be built.

## Exact declared launch coverage

- **FULL (required for IS-3):** none declared. No producer can inhibit native
  and Proton gameplay in both Gaming Mode and KDE without per-game setup while
  still proving idle clearance.
- **CONSERVATIVE (fallback only, not admitted here):** all covered graphical
  sessions in Gaming Mode and KDE inhibit; after any signal disappears mining
  stays UNKNOWN until a fresh, complete logind/systemd inventory and recursively
  empty covered cgroups prove the lifetime ended. It inhibits idle Steam too and
  therefore does **not** satisfy "useful idle mining + gameplay protection".
- **Legacy GameMode:** preserved where installed; limited to applications that
  opt in; absent on the target image.
- **Rejected:** per-game launch options, Steam patches, private injected hooks.

## Negative-proof invariant (stated explicitly)

> Mining may be cleared **only** when a fresh, complete, current-epoch inventory
> proves no covered game lifetime is active. Absence of a positive signal, a
> finite sample of successful trials, scope/reaper silence, an empty parent
> `cgroup.procs`, a missing pathname, a Steam crash, or elapsed dwell are **not**
> proof of absence.

## Budgets (declared; not measured — environment blocked)

| Budget | Limit | Measured this run |
|---|---|---|
| Incremental idle CPU | ≤ 1 % of one logical CPU over 10 min | no (BLOCKED) |
| Incremental RSS | ≤ 32 MiB | no (BLOCKED) |
| Query/wakeup frequency | recorded, with session scaling | no (BLOCKED) |

`overhead.json` from the probe records `measured: false` with the reason.
Any `/proc` snapshot is bounded opt-in probe data; it is not authorization to
ship a recurring scanner.

## What would change this decision

Admission would require an independent, upstream-maintained, versioned GLOBAL
producer that covers both modes with complete initial inventory and a
complete-inventory negative-proof invariant, plus a captured trace on an
authorized disposable Bazzite/Steam surface proving all G2 lifetimes, within
budget. Until then:

1. Wave 2 / full integration (plan tasks 13–18, 30, 36) stays blocked at G0.
2. The outcome (`BLOCKED_ENVIRONMENT` and/or `NO_DEPENDABLE_ROUTE`) must be
   presented to the owner before any reduced-function or fallback scope decision.
3. This plan does **not** authorise and does **not** relabel a CONSERVATIVE-only
   release as full support.

## Reproduce

```bash
# Rootless, read-only, no capture surface -> BLOCKED_ENVIRONMENT
python qa/probe_gaming.py \
  --catalog /var/tmp/sparehash-v020-game-catalog.json \
  --output .omo/evidence/v020/task-2/admission.json \
  --read-only

# Replay a separately captured candidate contract (never starts Steam)
python qa/probe_gaming.py \
  --output /tmp/admission.json \
  --capture-source /path/to/authorized-capture \
  --read-only

pytest tests/test_gaming_admission.py::test_accepts_complete_contract -q
pytest tests/test_gaming_admission.py::test_rejects_incomplete_contract -q
```
