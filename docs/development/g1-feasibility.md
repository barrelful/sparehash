# G1 feasibility result — 2026-10-25

> **Historical research, not the v0.2 runtime contract.** Revision 3 selected
> best-effort Steam reaper detection. S2 retired the log/session experiments
> after verified external preservation; the findings below remain unchanged.
> See [the current gaming contract](../gaming-integration-contract.md).

**Gate not passed. Do not deploy this experiment or enable `gaming.steam_log`
based on these results. No production architecture or release acceptance.**

## Scope and preservation

Only preservation and the Steam/root-controller feasibility experiment were
attempted. No cleanup, installation, release work, commits or pushes occurred.
The pre-existing `docs/release-0.2-status.md` change was not edited.

A private external `t0-g1-20261025-215949` archive under `~/sparehash-archive/`
preserves HEAD `f5b100f6717da6aa980ff533065fdef48b60d54e`, all eight original
refs, seven stash commits, raw Git data/reflogs, the dirty document and binary
diff, source files, ignored evidence, observer artifacts and plans. Existing
backups, refs and stashes were retained. Only reproducible environments and
caches were excluded from the workspace archive.

Verification included cloning the new bundle into an independent bare
repository, checking every original ref, and reconstructing each stash's
worktree/index/untracked tree from exported binary patches. Untracked trees
also have tar exports. All reconstructed trees matched. The workspace archive
was checked against 748 source-file/symlink manifest entries; 1,367 archive
files were checksummed and rechecked. Original status/refs/stashes and the dirty
document were unchanged after preservation. Recovery instructions and checksum
manifests accompany the private archive; it must not be published.

## Experimental changes

- `sparehash/gaming_steam.py`: explicit root opt-in, positive exact-integer
  non-root owner UID, both real/effective UID zero required for root reading;
  default root refusal retained. Successor readers preserve the opt-in. Existing
  bounded parsing, no-follow traversal, ownership/link checks, pidfds and
  continuity-loss checks remain.
- `sparehash/gaming_producer.py`: `SteamCoverage` owns entry/evaluation/close
  on the gaming worker and repeats bounded client discovery. Initial attachment
  returns UNKNOWN. Missing/ambiguous client never means idle. After attachment,
  client loss/ambiguity/replacement permanently inhibits this manager and closes
  its reader. A new client cannot erase possible orphan games. Legacy constructor
  now passes the target UID explicitly and rejects root/invalid owner UIDs.
- `sparehash/runtime.py`: the existing default-off Steam flag selects one owned
  manager per configured user, including unavailable clients. Shutdown joins
  gaming work before closing managers. Controller authorization and miner job
  semantics were not changed.
- `tests/test_gaming_g1.py`: fixture lifecycle/UID/cleanup tests and two strict
  expected-failure tests identifying unresolved clearance requirements.

No new configuration mode, service, privileged helper or policy was added.
The root manager does not canonicalize user-controlled symlinks before opening.
This is conservative, but also refuses Bazzite's `/home` redirect: portability
is unresolved, not justification to allow arbitrary symlink traversal.

## Tests and evidence limits

Local evidence: `.omo/evidence/v020/task-2/g1-feasibility/` (gitignored).

| Check | Result |
| --- | --- |
| Six focused gaming modules | 251 passed, 2 expected failures |
| Full unit suite | 1,340 passed, 2 expected failures, 624 subtests passed |
| mypy | Clean, 59 production files |
| Ruff on changed Python files | Two pre-existing SIM105 findings in `runtime.py`; no new findings |
| `git diff --check` | Clean |
| Independent read-only security and implementation reviews | G1 unaccepted; no new fail-open regression identified |
| Disposable Ubuntu VM | Created; startup exceeded 120-second tool timeout; forced off, discarded; final lab status empty |
| Root production runtime, real logind/cgroups and miner stop timing | **Not validated** |
| Actual Steam/native/Proton gameplay, KDE/Gaming Mode, SELinux | **Not validated** |

Root identity unit tests simulate IDs and stop at mocked process pinning. The
manager lifecycle test uses real same-UID fixture files/PIDs but substitutes
client discovery. Neither is evidence that privileged reading works. Earlier
same-user `CLEAR → BLOCKED → CLEAR` results used synthetic Steam-format logs,
an injected guard/cgroup snapshot and patched monitor construction; they are
not production-runtime or actual-gameplay acceptance.

The VM timeout yielded no guest test result. No host installation or systemd
units were changed. The failed disposable was cleaned up; no other VM was
modified. A successful privileged run and owner-supervised real gameplay
remain mandatory, not replaceable with these fixtures.

## Independent security findings and decision

1. **Unsupported lifecycle formats can be ignored.** A tracking-like line such
   as `AppID 2 adding process 456` is treated as diagnostic input. After balanced
   history, replay can still claim completeness. This is a synthetic
   counterexample, not an observed Steam format. The strict expected-failure
   test makes the unsatisfied fail-closed requirement visible.
2. **Balanced history is not cold-start completeness.** An add/remove pair
   encountered at attachment does not establish the absence of omitted older
   games or games surviving a previous client/controller. The loss latch helps
   only after this manager has observed the client. Restarting the controller
   cannot be accepted as a repair for unknown lifetimes.
3. **Root parsing adds exposure.** Bounded bytes/records/PIDs and descriptor
   checks are useful, but root filesystem operations may block on user-backed
   filesystems. In-process freshness checks do not interrupt those operations.
   `/proc/*/comm` identifies a mutable name, not an authenticated Steam binary.
   Same-user log/client forgery remains possible. Fixed production parser
   budgets must not become user-controlled.
4. **Portability and end-to-end behavior are unproven.** Bazzite home redirects,
   protected-home systemd operation, enforcing SELinux, real session inventory,
   cgroup health and miner stop/empty-proof deadlines require real evidence.

**Recommendation: further investigation; do not accept G1.** The existing
reader can be lifecycle-managed with a small adapter, but the source's negative
proof is not yet adequate. Evaluate G2 as privilege/process isolation rather
than weakening root checks: a supervised rootless reader could limit the root
parser and blocked-filesystem failure surface, but requires authenticated,
bounded, freshness-aware transport and child cleanup. G2 does **not** repair
malformed-format blindness or initial lifetime completeness. Establish a
defensible source-completeness/restart contract before selecting either route.

Next owner decision: authorize a bounded source-completeness/G2 investigation
and restore a functioning disposable VM run. Real Steam/native/Proton validation
needs separate owner-supervised resources. No broader work is authorized here.

Proposed minimum coverage remains Steam-launched native Linux and Proton
games, not all games. **Non-Steam games are not detected. Low-priority/idle
scheduling is not equivalent to stopping mining during gaming.** This report
establishes no desktop-support or release-readiness claim.

## Continuation: evidence correctness investigation

The normative findings are now in
[`gaming-evidence-model.md`](gaming-evidence-model.md). They supersede the
initial plan's unproven absence/clean-start implications, not the preservation
record above. T0 was not repeated; no cleanup or G2 implementation occurred.

- Every nonblank unrecognized record now inhibits parsing. Both original xfail
  regressions remain as public-producer behavioral tests; they pass without
  xfail because the unsafe effects have been removed.
- The producer never issues a completeness token. Balanced replay can report
  observed INACTIVE, but cannot grant CLEAR. Initial *functional* clearance
  remains unresolved. Log-only mining on an ordinary desktop is not viable.
- Additional tests cover unknown grammar, omitted additions, surviving games
  across Steam/controller restarts, paired removals at initial attachment,
  truncation/rotation, event loss, PID identity changes, and path refusal.
- Ubuntu disposable capability is restored: startup completed with a larger
  outer tool budget; guest root execution, Python 3.12.3/systemd 255 and healthy
  real logind/cgroup-v2 probes were observed. No lab configuration was changed.
- A source-manifest-verified VM fixture exercised root opt-in reading of a
  non-root-owned log, default root refusal and real PIDs. Diagnostic states
  were INACTIVE → ACTIVE → INACTIVE, without completeness. Unmocked Providers
  returned BLOCKED throughout. The populated session was a synthetic PAM
  background session, not a graphical desktop or actual Steam client.
- This did not install SpareHash, start a miner, validate its stop deadline,
  test systemd protected-home confinement or SELinux, or run Steam/gameplay.
  The guest was stopped/discarded; final lab status was empty.

Evidence is under `.omo/evidence/v020/task-2/g1-evidence-correctness/` and is
gitignored. Recommendation: investigate an authoritative source contract before
choosing G1/G2. Root isolation cannot cure incomplete gaming evidence.

Continuation verification: **272 focused gaming tests passed; full suite 1,361
tests and 624 subtests passed, with no remaining xfails.** Mypy is clean across
59 source files. Ruff passes on the Python files changed in this continuation;
`git diff --check` passes. The earlier report's test counts and VM failure remain
historical results, superseded by these new runs rather than reinterpreted.

Files changed in this continuation: `sparehash/gaming_steam.py`,
`tests/test_gaming_steam.py`, `tests/test_gaming_g1.py`, new
`tests/test_gaming_evidence.py`, this report, new `gaming-evidence-model.md`, and
the correction banner in `docs/gaming-integration-contract.md`. A correction was
also added to §2.6 of the external architecture plan. The previous manager and
runtime changes were retained without further changes; the user's dirty release
status document remains untouched. Work stays uncommitted on
`atlas/v020-task-2-g1-feasibility`; no pushes or host installation changes.
