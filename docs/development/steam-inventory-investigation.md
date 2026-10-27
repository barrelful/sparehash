# Steam inventory investigation — 2026-10-25

> **Historical completeness investigation.** Its negative conclusions concern
> authoritative absence, not the approved best-effort v0.2 scope. S2 preserves
> this research without adding another observer or completeness gate.
> See [the current gaming contract](../gaming-integration-contract.md).

**Decision: no candidate passes the evidence gate. Do not implement another
observer or select G1/G2 on these results.** Passive metadata identifies useful
positive candidates, but complete, recoverable absence evidence is unproven.
This is not proof that every possible Steam integration is impossible.

## Evidence actually obtained

- Read-only local `/proc` census: no Steam process or Steam directory observed
  for the current user. This is an availability check, not verified game absence.
- Read-only Bazzite census through the permitted management account: Steam,
  one `reaper` carrying `SteamLaunch`/`AppId` annotations, and 11 Wine/`.exe`-named
  candidates whose sampled ancestry reached that reaper. This demonstrates
  attachment to an existing Steam-launched Wine workload, **not confirmed
  gameplay, Proton version or a completed native/Proton test matrix**.
- The recorded Bazzite census enumerated 515 processes but read only six
  environments; 509 process inspections encountered permission denial. No Steam
  markers appeared in the readable subset. Treating this as idle would be wrong.
  No privilege escalation, credential copying or session access bypass was used.
- Cgroup membership was readable. No observed process belonged to an
  `app-steam-app<appid>-<pid>.scope`. Steam, reaper and wineserver instead had
  Gamescope-named ancestry in their cgroup paths. A Gamescope/session-wide
  population test would also inhibit ordinary Steam desktop/menu activity.
- Two disposable Ubuntu kernel probe scripts passed their assertions: exec
  removed environment markers without changing PID/start ticks or closing the
  pidfd; a child survived launcher/subreaper death with lost ancestry and no
  markers; it remained in the same generic cgroup as the ordinary observer;
  non-dumpability denied a same-UID environment read; and a process born after
  enumeration was absent from that inventory. These used synthetic processes,
  **not installed Steam, native games or Proton**. The guest was stopped and
  discarded; final lab status was empty.
- Public source/documentation review: Linux proc/cgroup semantics, Valve Proton
  and Gamescope code, public SteamUtils API and a reference subreaper. No supported
  complete running-workload inventory with restart/orphan and handoff guarantees
  was established. API declarations alone would not establish those guarantees.

**Not tested:** controlled native/Proton startup and exit; actual Steam restart
with gameplay surviving; actual controller restart during confirmed gameplay;
Ubuntu Steam desktop; Bazzite KDE versus Gaming Mode lifecycle; miner-stop latency
and load overhead. These require an owner-supervised game session and appropriate
read access. The VM lab cannot certify them. No Steam/game/client/unit or host
SpareHash installation was modified, and no live game/client was terminated.

## What the sources prove

| Source | Useful positive evidence | Why its absence cannot presently authorize CLEAR |
| --- | --- | --- |
| `/proc` PID/UID/start ticks + pidfds | A particular observed process remains alive; identity changes can be detected | A live process is not intrinsically classified as a game. A pidfd survives exec; PID/start identity does not preserve application semantics |
| `SteamAppId`, `SteamGameId`, compatibility variables | Steam/compatibility association when readable; potentially recognizes an already-running process independently of the client | No demonstrated invariant covers every supported live workload/descendant. Exec can supply a new environment. Permissions, namespaces and budgets can leave unknown processes. Positive markers also occur in launch/setup/helper contexts |
| `reaper SteamLaunch AppId=…` ancestry | Useful cold-attachment association, including the live Bazzite Wine workload | Reparenting destroys ancestry after an ancestor dies. A surviving subreaper helps while alive, but subreaper status alone does not prevent children surviving its death. Universal launch coverage and deployed failure semantics remain unverified |
| Per-game cgroup | Kernel-backed subtree membership/population can survive parent exit and environment changes; potentially the simplest strong lifetime source | Every supported launch must be placed in a known scope before execution, with no silent fallback/escape. Scope naming alone does not prove that coverage. No matching scopes were observed in the live workload |
| Generic user/Steam/Gamescope cgroup or logind session | Workload/session presence and identity | Contains ordinary desktop/client activity. Empty whole-user containment can be conservative evidence, but requiring it precludes useful logged-in desktop mining |
| Gamescope window/AppID/focus/running-count metadata | A compositor-visible application or Steam-reported count can inhibit | Startup without a window, background/minimized workloads, other sessions and stale client properties are not a complete process inventory. The reviewed Gamescope code reads `STEAM_GAMES_RUNNING` from a root-window property, not an independent process census |
| Steam process log / installed-app metadata / current-process API | Positive launch association, installed applications, or the caller's AppID respectively | None supplies the missing complete running-lifetime inventory. The existing log remains additive inhibition only |

`/proc/pid/environ` describes the environment at the current exec; ordinary
in-process `setenv()` changes are not reliably reflected there. The VM
counterexample therefore used **exec with a replacement environment**, not a
claim that `unsetenv()` always removes a proc-visible marker. Host PID namespace
visibility can include container descendants; a container-local observer may
miss processes outside it. Root access could repair some read failures, but
would not supply a mandatory game-classification invariant.

## Initial inventory and ongoing observation

Sticky knowledge of positively identified PID lifetimes is useful: loss of a
marker or the Steam client must not erase a known surviving game. It cannot
reconstruct a vanished ancestor before controller startup, nor a previously
unknown child created between observations. PID reuse needs start/pidfd checks;
exec needs reassessment without treating it as a new PID lifetime.

A fork/exec/exit stream subscribed before inventory can close some handoff races,
but it neither labels an initially unmarked orphan nor repairs an interrupted
history without an authoritative resnapshot. Adding a process connector/eBPF
pipeline would add privileged complexity without solving that semantic gap.

Conversely, **a complicated event stream is not inherently required** by the
five-second objective. Bounded full rescans could suffice if a persistent,
complete classification contract existed, races/read failures inhibited, and
the scan interval + worst-case scan/delivery + miner stop stayed within five
seconds. Two matching scans do not prove such a contract. The partial snapshots
took roughly 13–47 ms, but lacked most Bazzite environment reads and were not
under gaming load; they establish neither a worst-case budget nor stop latency.

## Minimal approaches versus original requirements

1. **Passive marker/reaper inventory:** little code for detection, but substantial
   uncertainty/recovery bookkeeping if promoted to absence authority. Useful
   positive observations; fails the demonstrated completeness/recovery gate.
2. **Mandatory automatic per-game scopes supplied by the launch platform:** the
   smallest promising observation contract, **conditional only**, not a newly
   implemented mechanism. Enumerate all covered scope subtrees on cold start,
   retain identity across client exit, and require fresh empty population before
   CLEAR. It could avoid per-game configuration and process-environment reads.
   Need proof of mandatory creation, no migration/fallback gaps, restart behavior
   and both-platform availability. The current Bazzite snapshot does not support
   assuming that contract. Defining coverage as merely “whatever has a marker”
   would silently weaken the requirement, not solve it.
3. **Launch-time wrapping/registration:** considered only after the passive gap.
   A gate plus per-game scope could establish a defined boundary, but ordinary
   launches must not bypass it. Per-game `%command%`/GameMode configuration fails
   the no-per-game-configuration requirement. Wrapping Steam itself catches its
   always-running UI too, preventing idle mining. A new global game-launch hook
   would require demonstrated platform support and introduces launch-path
   interference/failure handling. None is approved or implemented here.

**Recommendation: pause observer implementation and reconsider the release
scope/design with the owner.** No current method passes all original criteria.
An Ubuntu-only claim would not by itself repair marker/ancestry semantics.
Do not ship “no markers found ⇒ CLEAR”, an always-blocked desktop release, or
another helper that transports incomplete evidence. If the owner wants one
remaining bounded validation, obtain a supervised native/Proton session and
verify whether an existing platform actually guarantees automatic per-game
scopes. Otherwise stop this route rather than inventing a subsystem to continue.
G1 versus G2 remains secondary: privilege isolation does not create completeness.

## References and reproducibility

- [Linux process environments](https://man7.org/linux/man-pages/man5/proc_pid_environ.5.html),
  [PID namespaces/reparenting](https://man7.org/linux/man-pages/man7/pid_namespaces.7.html),
  [kernel cgroup v2 membership/population](https://docs.kernel.org/admin-guide/cgroup-v2.html).
- Gamescope at `e6674a98118ca7b098b98a3874b8f195ae1bf750`:
  [cgroup and reaper AppID lookup](https://github.com/ValveSoftware/gamescope/blob/e6674a98118ca7b098b98a3874b8f195ae1bf750/src/Utils/Process.cpp#L666),
  [`STEAM_GAMES_RUNNING` property consumption](https://github.com/ValveSoftware/gamescope/blob/e6674a98118ca7b098b98a3874b8f195ae1bf750/src/steamcompmgr.cpp#L7123).
- [Valve Proton source](https://github.com/ValveSoftware/Proton/blob/master/proton),
  [SteamUtils GetAppID](https://partner.steamgames.com/doc/api/ISteamUtils#GetAppID),
  [reference subreaper](https://github.com/Plagman/reaper). The reference source
  was not authenticated as the installed Steam reaper binary.

Private, gitignored evidence: `.omo/evidence/v020/task-2/steam-inventory-investigation/`.
It contains anonymized snapshots, disposable-VM results, public source snapshots
and diagnostic scripts/checksums; no environment values, game titles, personal
paths or credentials were retained by the live process probes. No production
code or regression tests were changed in this investigation. All earlier branch
work is retained; no commits, pushes, cleanup or G2 implementation occurred.
