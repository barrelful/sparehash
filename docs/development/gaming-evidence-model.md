# Gaming evidence correctness gate

> **Historical completeness research.** Revision 3 rejected authoritative game
> absence as a v0.2 requirement. S2 retired these experimental implementations;
> this document is not an active readiness gate or fallback specification.
> See [the current gaming contract](../gaming-integration-contract.md).

This investigation supersedes the architecture plan's proposed implications
“Steam absent ⇒ INACTIVE” and “balanced replay ⇒ CLEAR”. Neither is established.
It does not select G1/G2, authorize a new observer or constitute a release design.

## 1. Scope and decision semantics

The proposed minimum is Steam-launched native Linux/Proton games for every
configured user and claimed desktop mode. It requires a demonstrated coverage
contract for those launches; merely calling a log “Steam-only” does not supply
one. Non-Steam games remain undetected. Idle/low-priority scheduling does not
substitute for stopping mining during gaming.

| Decision | Required evidence | Mining |
| --- | --- | --- |
| BLOCKED | A supported game/candidate process is positively observed live with checked UID, pinned lifetime and current source association; or an unresolved positive/manual inhibitor remains | Stop/inhibit |
| UNKNOWN | Required inventory, lifecycle coverage, source identity, freshness or continuity cannot be established; no sufficient current negative proof exists | Stop/inhibit |
| CLEAR | Every required coverage domain supplies a fresh, current-epoch, healthy, authoritative inventory with no supported game alive, no unresolved earlier lifetime and continuous launch/exit observation tied to that inventory | May proceed through existing controller safety, dwell and authorization checks |

Positive evidence is asymmetric: a correctly pinned candidate may inhibit
without proving that *every other* game is observed. Stale positive evidence
may be retained conservatively as inhibition; it cannot authenticate current
activity or turn into clearance by aging out. Empty coverage, a timeout, parser
failure, a client name, a pidfd closing or a missing producer never grants CLEAR.

Freshness and epoch checks are necessary, not sufficient. Re-reading an unchanged
file creates a fresh observation of those bytes, not proof of fresh absence.
An epoch bump invalidates old samples; it cannot repair an unknown inventory.
Unknown has a different meaning from confirmed gaming even though both stop
mining. The existing guard coalesces UNKNOWN to operational BLOCKED when a
session exists; reducer UNKNOWN and the reason for inhibition must not be
described as a detected game. No controller authorization rule is relaxed here.

### What an authoritative absence observation must establish

1. **Closed coverage:** enumerate all supported existing lifetimes, including
   games predating controller attachment and games surviving Steam exit. Define
   how relevant descendants/Proton workloads remain covered after launcher exit.
2. **Inventory/stream handoff:** subscribe before inventory, with an atomic
   snapshot/barrier or a documented reconciliation protocol. A launch between
   enumeration and subscription must not disappear.
3. **Loss detection:** a source generation and monotonic sequence/barrier, or
   equivalent authoritative resnapshot, must expose dropped application events,
   truncation and restart. Kernel notification delivery alone is insufficient.
4. **Lifetime identity:** PID plus UID/start identity and pidfd; PID numbers and
   paired textual records alone are not lifetimes. No unresolved earlier game
   is erased by an exit/removal belonging to another incarnation.
5. **Fresh delivery:** inventory and events meet the observation budget, with
   inhibition on timeout/disconnection and controller stop/empty-proof evidence
   within the approved deadline. A snapshot is absence *at its observation time*,
   not a promise that no game can launch later.
6. **Recovery:** controller, Steam and observer restart require a new complete
   inventory or demonstrably preserved lifetime coverage, not replay optimism.

No configurable `initially_complete=true`, manual “trust this log” acknowledgement
or synthetic startup marker is proposed. A forced clean session alone does not
establish loss-free observation of future launches.

## 2. What the current Steam process log can and cannot prove

The reader can bound its input, reject unexpected bytes, reconcile exact paired
records, pin the processes that those records mention and detect changes to a
prefix it previously read. These remain useful **additive inhibition** and QA
capabilities. None authenticates a complete inventory of supported games.

Consider two inputs at attachment: identical balanced log bytes and a live
same-UID process. The process may be an ordinary desktop application, or a game
omitted from retained history/surviving an earlier client. The log provides no
way to distinguish these worlds. Scanning generic process names or interpreting
user.slice as “populated but probably idle” does not supply game classification.
Reading more history or waiting longer does not resolve this ambiguity.

No authoritative Steam inventory, snapshot barrier, guaranteed event sequence,
or coverage of surviving children has been demonstrated by the supplied evidence.
Historical native/Proton pairing measurements cannot prove completeness across
initial attachment, format changes or omitted application events. This is a
limit of the demonstrated source contract, not a claim that every possible
Steam interface has been exhaustively investigated.

**Conclusion:** trustworthy CLEAR is not achievable from this log alone under
the required failure model. It needs additional authoritative observations or
a different, explicitly enforced launch/containment boundary. Such a boundary
would change support semantics and requires owner approval before implementation.

### Current implementation semantics

`SteamLogProducer` now emits **no completeness tokens**, including on successful
replay. INACTIVE/OK describes an empty observed tracking set and healthy local
I/O, not verified game absence. ACTIVE can still inhibit; INACTIVE without
completeness reduces to UNKNOWN. A configured ordinary desktop therefore stays
inhibited with log-only observation. This is an explicit experiment blocker,
**not an acceptable functional v0.2 release or an always-blocked fallback plan**.

The parser accepts the existing exact add/remove grammar and blank whitespace
lines. Every other nonblank record is MALFORMED and latches inhibition. There is
no speculative diagnostic allowlist or broader guessed lifecycle grammar. This
may reject legitimate Steam diagnostics: compatibility requires real examples,
not silently ignoring unknown bytes. Addition suffixes are still discarded as
the existing grammar permits; no source bytes/titles are logged.

The original two expected-failure regressions are retained as behavioral tests.
Their xfail markers are removed only because their unsafe effects are now fixed:
unknown lines inhibit, and balanced history cannot produce clearance. Removing
the unsafe completeness token does **not** solve useful initial clearance.

## 3. Failure matrix

| Case | Required response / evidence gap |
| --- | --- |
| Missing/ambiguous client | UNKNOWN; no absence inference |
| Client exit, game survives | Inhibit; manager latches loss, no automatic clear on replacement |
| Controller restart with orphan game | New log reader still lacks authoritative inventory; never CLEAR |
| Balanced history containing an already-removed but live PID | Does not prove that PID/lifetime exited; no completeness |
| Truncation/rotation/rewrite | Terminal continuity loss for that reader, even if old prefix returns |
| Inotify overflow/missed kernel records | UNKNOWN; notification-loss latch, not a fresh empty inventory |
| Application never writes an addition | Cannot be detected by inotify/hash; missing negative authority prevents CLEAR |
| Partial/unknown record | Inhibit; a completed blank append can restore diagnostics, not clearance |
| Game exits without removal | UNKNOWN, not inferred idle |
| Removal while pinned process lives | MALFORMED; inhibit |
| PID number reused / start identity changes | Inhibit; old association must not adopt a new lifetime |
| Stale observation / transient read failure | Inhibit; prefix-preserving recovery may restore diagnostics only |

Real same-UID files/PIDs and adversarial wire/identity injections test these
behaviors. They are explicitly not actual Steam/native/Proton evidence.

## 4. Separate G1 root/path assessment

Root opt-in is explicit and default refusal remains. Descriptor-relative
no-follow opens, exact owner/regular-file/link checks and fixed byte/line/PID
budgets meaningfully reduce attacks. They do not bound wall time for user-backed
filesystem lookup/read: a blocked FUSE/network filesystem operation can outlive
the single gaming worker's deadline. O_NONBLOCK and a later freshness check do
not interrupt an already blocked VFS operation. Root opens objects before
validating their final type; no claim of fully safe hostile-filesystem handling
has been established. No existing safety/ownership checks should be removed.
`Providers` also joins its gaming worker on close and has no interruptible I/O
deadline. A future observer must not allow a hung query to preserve an earlier
clearance indefinitely; a post-read clock check alone is not that guarantee.

Bazzite's logical `/home` redirect is currently refused. A finite, protected OS
redirect could in principle be handled with root-owned, non-writable ancestry,
an exact approved `/home`→`/var/home` mapping, descriptor pinning, substitution
checks and no extra hops. Alternatively an authoritative account record may
already name the physical home. The existing layout module approves only
`/opt`/`/usr/local`, not `/home`; do not silently extend its privileged grammar
or use unrestricted root `realpath()` on user-controlled paths. No redirect
exception is implemented by this investigation.

The redirect itself appears bounded in complexity, but does not fix source
correctness or blocking I/O. Investing in root path policy before identifying
an authoritative observer is not justified. G2 could isolate parsing and
filesystem stalls from the privileged controller, at the cost of child/transport
supervision. It does not create an absence proof. G2 is not implemented here.

## 5. Decision and smallest next step

**Recommend a different authoritative observation contract before choosing
G1/G2.** Keep the log only as an additive inhibitor/diagnostic. Do not expand the
root parser, guess a process-name classifier, or build a helper merely to carry
incomplete evidence.

The smallest next step is a bounded feasibility probe of **one** candidate with
an explicit inventory/launch-continuity contract. First establish whether a
supported Steam interface actually offers complete running-game inventory,
restart/orphan semantics and a race-free event handoff. If no such contract can
be demonstrated, present an enforced launch/containment approach and its support
limitations to the owner before coding it. Mere opt-in launch hooks are not
complete coverage if ordinary Steam launches can bypass them.

Only after a source passes idle/start/exit, pre-existing game, client/controller
restart, surviving descendant, loss and identity tests should its privilege
placement be selected. Actual Steam/native/Proton and claimed Bazzite modes
remain mandatory owner-supervised validation. Neither G1 nor G2 is accepted.
