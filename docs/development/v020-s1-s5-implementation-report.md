# SpareHash v0.2 — S1–S5 implementation and release-readiness report

> Historical implementation snapshot through `af7f2cf`. Subsequent cleanup and
> branch integration are recorded in the [review consolidation report](v020-review-consolidation.md).
> File-presence, test-count and publication statements below describe that snapshot.

Date: 2026-10-27. Purpose: independent code and release-readiness review.
This is a report of existing implementation and recorded tests, not a new
acceptance run, security audit, feature change or RC declaration.

## 1. Executive summary

**The intended, deliberately narrowed v0.2 implementation is present, but the
complete release-readiness plan has not passed. An experimental RC is not yet
justified by the recorded evidence.** S5 was entered and its source/artifact
selection and owner handoff were implemented; its final dual-image VM acceptance
was not completed. "Implementation through S5" must not be read as "all five
stages passed."

Since the original development baseline `f5b100f`, the principal changes are:

- Replaced the production gaming-completeness/log/session experiment with one
  small Lutris-derived, best-effort Steam reaper scanner. A healthy no-match scan
  may release gaming inhibition; faults, stale results and recognised games
  inhibit. No new supervisor, service or provider framework was introduced.
- Removed four obsolete gaming modules, their exclusive test modules and the
  old setup-consent fixture; simplified live configuration, status and setup.
  Retained ownership-safe legacy removal and recoverable historical research.
- Fixed probe placement and successful bootstrap-staging cleanup so strict
  receipt-based removal can succeed without adopting or deleting foreign files.
- Refused deferred public workflows, replaced the broken release-smoke entrypoint,
  aligned documentation/support labels, added Python 3.14 to CI and checked
  distribution artifacts for source, licensing and runtime dependencies.
- Selected an implementation commit and artifacts, attempted candidate-bound VM
  acceptance, and supplied an owner installation/gameplay checklist. Stopped at
  the required Ubuntu baseline's transport failure rather than declaring success.

**Simpler and more maintainable? Yes, particularly the live gaming path.** At the
review snapshot, the baseline-to-HEAD diff is 71 files, 3,343 insertions and 5,423
deletions, excluding this report. Under `sparehash/` it is 18 files, 348 insertions
and 1,663 deletions: a net reduction of 1,315 production lines. Four retired gaming
modules totalled 1,148 lines; the new scanner is 134 lines. These are textual diff
measurements, not a formal complexity or correctness metric. Retained migration,
benchmark and historical QA code still adds substantial review surface.

**Functionality implemented:** low-priority, unprivileged CPU-miner management;
controller-owned independent blockers and start authorization; best-effort Steam
recognition; source install/removal/explicit reinstall; status and operational
controls. This does not mean actual upstream XMRig hashing, real gameplay or all
desktop/distribution combinations have been demonstrated.

**What prevents an RC now:** final candidate-bound Ubuntu VM acceptance failed
through the guest-agent transport, with an unknown product/test outcome. The
candidate-wide Bazzite lifecycle/reboot replay was then not run, in accordance
with the plan's stop condition. Hosted GitHub CI also has not run. Prior scoped
VM passes and local CI-equivalent checks must not be substituted for these
missing results. Real owner gameplay is pending; the approved plan permits that
to follow a properly VM-gated experimental RC, but not a gameplay-validation claim.

## 2. Review scope, identity and provenance

| Item | Identity |
| --- | --- |
| Original development baseline | `f5b100f6717da6aa980ff533065fdef48b60d54e` |
| Selected implementation/candidate commit | `f79718de43bed66fdbb77af202b6c57f27613a09` |
| Reviewed handoff HEAD, before this report | `4d569149bf75c44a72afed862eb6c559f6656885` |
| Branch | `atlas/v020-task-2-g1-feasibility` |
| Source/units/installer/fixture inventory SHA-256 | `2f9e83812c8f3c7e0c8067854bd232448817b7917dcdd8251cda4f72c6535150` |
| Selected wheel SHA-256 | `f314f2369a4b00d72d4020643cb0840b6382474781b57804991482b1a57c1468` |
| Selected sdist SHA-256 | `3bb74a34c83ca8532a2385cfe3407921642a9d1686149d19fab91e1f6b7e82c5` |

The selected wheel and sdist are `sparehash-0.2.0-py3-none-any.whl` and
`sparehash-0.2.0.tar.gz`. Later documentation builds have separate hashes; the
table does not describe every archive with those filenames. The source digest
is the inventory defined in `qa/release_smoke.py`, not a Git commit hash, complete
repository hash or upstream XMRig identity. The handoff commit changed reporting
and the README, not the selected runtime/installer/unit/fixture inventory.

Six commits organise the changes after the baseline:

| Commit | Purpose |
| --- | --- |
| `28a2194` | Preserve the owner's release-status/workspace edits |
| `10b54d0` | Preserve gaming feasibility and inventory research |
| `aae6075` | Best-effort scanner, integration, configuration and obsolete-path retirement |
| `f481b0a` | Probe and bootstrap-staging lifecycle fixes |
| `f79718d` | Deferred-workflow gates, pinned VM QA, CI and release documentation |
| `4d56914` | Candidate blocker report and supervised owner-test handoff |

S1–S4 were originally developed and tested as uncommitted snapshots above
`f5b100f`; historical evidence sometimes records that old HEAD. Those entries
are **not tests of the pristine baseline**. Their manifests, archived source and
stage reports identify the working-tree snapshots. The later commits group the
work logically and do not pretend to be one independently tested commit per stage.

The owner's historical [`release-0.2-status.md`](../release-0.2-status.md) was
preserved, not rewritten to imply newer acceptance. Its old blockers, gaming
terminology and installation guidance are not the current status. Use this
report and [`release-0.2-candidate.md`](../release-0.2-candidate.md). Some stage
reports likewise intentionally retain statements such as "uncommitted" or
"stopped before S4" from their original recording date.

## 3. Stage-by-stage results

Verdicts apply to the stated stage scope. **PASS** does not transfer evidence to
a later source snapshot or imply real hardware/gameplay validation. **PARTIAL**
means implemented work exists but required acceptance remains incomplete.

### S1 — Small detector and actual runtime vertical slice

**Objective:** replace the production gaming path with a bounded attributed
detector, integrate through the actual worker/controller, enforce stale-result
inhibition and existing dwell, and prove recognised-fixture stop/recovery without
requiring session completeness or game-absence proof.

**Implementation and key files:** added `sparehash/gaming_scan.py` and
`tests/test_gaming_scan.py`; integrated immutable scan results in
`runtime.py`, `controller.py` and `controller_io.py`; exposed detection state in
`status.py`; added `THIRD_PARTY_NOTICES.md` and artifact inclusion. Reused the
existing worker, safety queue and controller/Jobs boundary. Old experimental
modules were temporarily retained for S2, not activated as fallbacks.

**Recorded tests:**

| Test | Result |
| --- | --- |
| Focused scanner/runtime/controller/strict-safety tests | 203 passed; 130 subtests passed |
| Complete local suite, Python 3.11 | 1,394 passed; 625 subtests passed |
| Mypy | Clean, 60 production files |
| Scoped lint | Passed; eight pre-existing findings in other touched production files unchanged by rule count |
| Documentation/build/attribution | Passed, including final offline wheel/sdist rebuild |
| Actual runtime/IPC/Jobs/systemd fixture, Ubuntu | Recognised fixture → empty miner cgroup: 0.834 s |
| Same runtime fixture, Bazzite | 0.812 s; SELinux Enforcing confirmed |

VMs exercised `runtime.run`, protected IPC, controller authorization and an
unprivileged idle dummy miner, not merely a matcher mock. A populated background
login/session did not itself block successful scanning. Scan-bound faults and
manual/other blocker independence were exercised. VM interpreters were Ubuntu
3.12.3 and Bazzite 3.14.8; this was not a full four-version unit matrix.

**Failures and limitations:** early rapid polling exhausted existing IPC limits
and caused safe authorization refusal; polling was slowed, not limits weakened.
Recovered bounded miner-job retries were recorded. A guest-agent transfer failed
and its VM was discarded. The S1 post-run Bazzite AVC filter failed with exit 1,
so S1 does **not** supply a clean-AVC assertion. An initial offline build lacked
cached Hatchling; fetching the declared build tool allowed subsequent builds.
Actual native/Proton games, hardware, installer lifecycle and comprehensive
game coverage were outside this stage.

**Evidence:** [S1 report](s1-steam-detection.md), scanner tests, attributed source,
and private `.omo/evidence/v020/task-2/s1-steam/` summaries, manifests, drivers,
timings, failed attempts and build logs. Pre-edit research was archived and
verified rather than discarded.

**Verdict: PASS — automated/synthetic S1 runtime gate only.**

### S2 — Focused retirement of the old gaming path

**Objective:** leave one live detector, retire unused log/session-completeness
code and public configuration, preserve useful research and ownership-safe old
installation removal, without a broad cleanup.

**Implementation and key files:** removed the four production files enumerated
in section 4.3. Simplified `gaming.py` to verified accounts/authenticated hooks and
`gaming_install.py` to legacy unmerge. Removed new-install GameMode package/home/
membership setup. Updated `config.py`, `wizard.py`, `preflight.py`, `install.py`,
`runtime.py`, `status.py`, tests and the gaming contract. New config serialization
has only `gaming.enabled` and `gaming.users`. An empty hook-user list is valid.

Old inert `integration="auto"` and `steam_log=false` are readable with warnings
and disappear on save. Explicit retired modes, `steam_log=true` and malformed
legacy selections refuse with migration guidance rather than silently selecting
a different policy. Disabled detection stays disabled and is reported explicitly.

**Recorded tests:** 354 focused tests and 226 subtests passed; full suite
**1,114 tests and 608 subtests passed**. Mypy was clean across 56 production
files. Scoped lint, README inventory (16 commands, 16 flags, 22 config keys),
whitespace and offline wheel/sdist/notice/no-runtime-dependency inspection passed.
Other touched-file lint comparisons recorded no new rule counts.

**Failures and limitations:** initial new status fixtures omitted `ready`; an
unused import and trailing whitespace were also corrected. No new VM lifecycle
or gameplay evidence was produced by S2. The lower suite count is not a set of
skips or expected failures: obsolete experiments and assertions were removed,
and replacement scanner/safety/migration tests remain. Legacy safe-unmerge code
and historical C1 QA research remain intentionally.

**Evidence:** [S2 report](s2-gaming-retirement.md), Git deletions/configuration
changes and private `.omo/evidence/v020/task-2/s2-retirement/`. The pre-S2 archive
preserves 51 source/test/document files with verified hashes and recovery data.
The historical S2 narrative refers to "six exclusive experiment suites";
the actual baseline-to-HEAD file deletions are four `test_gaming_*` modules and
one installer-consent fixture, explicitly listed below. That narrative count
must not be treated as six deleted test files.

**Verdict: PASS — focused retirement and compatibility gate.**

### S3 — Lifecycle and cross-distribution reliability

**Objective:** fix the receipt-blocking staging defect narrowly and validate
install → operation → removal/purge → explicit reinstall → actual reboot in
both disposable images, retaining foreign content, unprivileged mining and
SELinux/systemd containment.

**Implementation and key files:** `install_xmrig.py` now uses exclusive temporary
probe children of the existing executable `/opt`, not a permanent unreceipted
probe root or a location hidden by probe `PrivateTmp`. `bootstrap.py` consumes
only its verified staging copy after successful publication. Unexpected/changed
staging refuses cleanup and interrupted publication keeps staging. Added focused
regressions in `tests/test_portable_probe.py` and `tests/test_bootstrap.py`.
The uninstaller's receipt, foreign-content and empty-cgroup policy was not relaxed.

**Recorded tests:** 169 focused tests and 43 subtests passed; complete suite
**1,117 tests and 608 subtests passed**; mypy clean across 56 modules; scoped/
baseline lint, README, whitespace and offline builds passed. Four pre-existing
findings remained in the touched older source/test files.

Both final S3 guests passed **18 synthetic lifecycle checks each**, including
reboot with a changed boot ID and controller/miner autostart. Installed source
was checked against the same S3 source manifest; its recorded manifest SHA-256
is `595bf17dbd866b06f3a8cbd96f744b273b404e34f8f4733cb1baaeb28148283d`.
That is a different manifest/inventory scheme from the later S4 source digest,
not a substitute for final-candidate identity.

| Final S3 timing | Ubuntu | Bazzite |
| --- | ---: | ---: |
| Recognised synthetic game → empty under foreground load | 0.757 s | 1.010 s |
| Controller loss → empty miner cgroup | 0.116 s | 0.031 s |

**Failures and limitations:** an early Ubuntu run reproduced the leftover
bootstrap-staging defect; its pre-fix result was not final acceptance. Earlier
guest-agent failures caused forced disposal and remain unknown outcomes. The
successful final Ubuntu guest temporarily disconnected after reboot; bounded
reconnection checks preceded the actual passing assertions, without retrying a
hung command as success. A local full-suite attempt timed out before useful
failure details; subsequent full runs passed. Sensors and games were synthetic,
and the miner was a trusted external non-hashing fixture, not upstream XMRig.

**Evidence:** [S3 report](s3-lifecycle-reliability.md), regression tests and private
`.omo/evidence/v020/task-2/s3-lifecycle/`: `summary.json`, per-image final
`*-results.json`, source manifests, driver, collected journals and earlier failures.
Bazzite pre-/post-reboot kernel audit queries succeeded with no observed AVCs.

**Verdict: PASS — both images, for the recorded S3 source and synthetic scope.**

### S4 — Release surface, QA and documentation alignment

**Objective:** make a functioning destructive VM QA entrypoint with consent,
attribution and source SHA; refuse unsupported public workflows; align exits,
permissions/support/removal docs, Python 3.11–3.14 checks, CI and packaging.

**Implementation and key files:**

- `sparehash/__main__.py` and `install.py`: early exit-2 refusals for public
  update, upgrade completion, config editing, benchmark and installer
  upgrade/recovery/completion/reconfiguration flags, including empty IDs.
  Ordinary install also reads the protected CLI and refuses an existing target
  before retirement clearing/capability detection; no implicit reconfiguration
  workaround is advertised. Help labels deferrals; old internal algorithms remain.
- `preflight.py`: validated release-line matching only for Ubuntu `24.04` and
  Bazzite `44`; accurate group/user effect labels rather than a nonexistent
  `sparehash` user. The label does not authenticate an exact image or hardware.
- `qa/qa_release_smoke.sh`, `qa/release_smoke.py` and
  `qa/fixtures/synthetic_xmrig.py`: explicit `--yes`, disposable opt-in, source
  SHA, fresh-VM/evidence guards, fixed PATH, cleared inherited overrides, actual
  installer/IPC/systemd/removal and cleanup/AVC checks. No host-internal launcher
  or lab runtime dependency was added to the project.
- `qa/check_artifacts.py`, `.github/workflows/ci.yml`, metadata and root/platform/
  upgrade/security/QA/developer docs: matrix, locked development installation,
  scoped lint, build/dependency/license checks and honest release labels.

**Recorded tests:** final complete suites passed **1,159 tests and 608 subtests
on each of Python 3.11.13, 3.12.3, 3.13.5 and 3.14.9**; mypy clean across 56
modules under all four. Scoped lint, README inventory, whitespace, builds and
artifact checks passed. Final exact-source Bazzite smoke passed **11 checks plus
cleanup**, Enforcing, with no observed AVCs; recognised-game stop 1.012 s and
controller-loss stop 0.021 s. This entrypoint does not reboot the guest.

**Failures and limitations:** an older INI test incorrectly compared atime after
reads; it now retains exact content/backup and inode/mode/owner/size/mtime/ctime
assertions without assuming reads cannot change atime. After the existing-CLI
guard, 14 retirement tests failed because their fixtures did not isolate that
lookup. They were corrected to model completed removal while retaining their
hold/corruption/ID assertions. Final suites have zero reported failures.

An Ubuntu development snapshot passed 11 smoke checks before environment
hardening (game stop 1.007 s). Later hardened attempts timed out through the
guest agent, and the final installation-guard source was not accepted on Ubuntu
in S4. Older Ubuntu success is not promoted to final-source evidence. Stable
3.14.9 replaced an initially selected 3.14 RC interpreter for the counted matrix.
Hosted GitHub CI has not run. No globally clean Ruff or dual-image final smoke
claim is made.

**Evidence:** [S4 report](s4-release-alignment.md), release-surface/QA tests and
private `.omo/evidence/v020/task-2/s4-release/`: final matrix logs,
`final-s4-summary.json`, `bazzite-install-guard-summary.json`, exact-source VM
archive, failed attempts, artifact logs, lint comparisons and verified archives.

**Verdict: PARTIAL — implementation/local gates and Bazzite smoke pass; final
Ubuntu smoke acceptance remains incomplete.**

### S5 — Candidate-bound automated/VM acceptance and owner handoff

**Objective:** freeze candidate source/artifacts after S1–S4, complete automated
and disposable-VM gates before declaring an experimental RC, then provide an
owner matrix for real native/Proton and Bazzite mode testing.

**Implementation and key files:** selected commit/source/artifact identities in
section 2; assembled a pinned private payload and an S3-derived full lifecycle/
reboot driver with source-manifest and candidate checks. Added
`docs/release-0.2-candidate.md` and `docs/testing-0.2-owner.md`, with README links.
The checklist covers cautious staged installation, stop controls, already-running
games, Steam restart, surviving processes, recovery/dwell, native/Proton,
desktop/mode distinctions and foreground impact. No release tag, registry upload
or push was performed.

**Recorded tests:** inherited final four-version matrix applies to unchanged
committed implementation/test bytes. A handoff rerun on Python 3.11 passed
**1,159 tests and 608 subtests**, with mypy clean across 56 modules. Handoff docs,
whitespace, offline build and artifact checks passed. No candidate-wide VM
lifecycle/reboot pass was obtained.

The fresh Ubuntu guest booted, ran a Python 3.12.3/systemd transport check and
accepted transfer. Launching the pinned driver returned:

```text
guest agent command timed out: guest agent didn't respond to command within '5' seconds
```

No guest test verdict or final journals were returned. It was immediately forced
off and discarded. **The root cause is not diagnosed:** host/launcher/guest-agent
reliability is a blocker, not evidence that an application assertion failed or
that changing a timeout alone would fix it. No infrastructure repair, policy
weakening or baseline change was attempted. The required-baseline stop condition
was honoured; Bazzite candidate lifecycle/reboot was not replayed afterward.

**Unfinished:** restore reliable Ubuntu lab execution; complete the selected
source's final acceptance on both images, including reboot; obtain hosted CI
results through the review workflow; only then issue a VM-gated RC report.
Owner gameplay/hardware testing remains separate and pending. S4's same-source
Bazzite smoke is valid for that limited run, not a full S5 reboot result.

**Evidence:** [candidate report](../release-0.2-candidate.md),
[owner checklist](../testing-0.2-owner.md), and private
`.omo/evidence/v020/task-2/s5-candidate/`: `candidate.json`, source/payload hashes,
driver, matrix references, `ubuntu-main.log`, forced-disposal logs,
`final-summary.json` and handoff build/test logs.

**Verdict: PARTIAL — candidate selection/reporting implemented; final acceptance
blocked; real gameplay NOT TESTED; no RC declared.**

## 4. Current architecture and maintainability

### 4.1 Production control and privilege boundaries

```text
protected config + deployment/receipt state
                  |
       root sparehash.service
       runtime Providers / monitor workers
          | safety queue (priority) + ordinary messages
          v
       single-owner Controller / BlockerState
          | current-generation authorization + dwell + stop proof
          v
       Jobs / protected operation and execution specification
          v
       static sparehash-xmrig.service
       ExecCondition: trusted root authorization
       ExecStart: trusted wrapper, then unprivileged xmrig
```

`runtime.py` assembles power, thermal and gaming workers, the protected Unix
socket server, Jobs and the owner loop. Safety callbacks are drained before
ordinary IPC/job messages. Workers observe/enqueue; only the controller authorizes
mining. Startup is inhibited until credible required evidence arrives; a successful
gaming scan does not clear another blocker's authority.

`controller.py`, `controller_io.py`, `state.py`, `runtime_jobs.py` and
`runtime_spec.py` retain generations, cancellation, bounded job retries, empty
miner-cgroup proof, protected publication and fresh authorization. Manual state,
lifecycle leases and retirement are separate from automatic gaming observations.
Systemd binds the miner to the controller. The miner runs as `xmrig`, not root,
with nice 19, `SCHED_IDLE`, idle I/O and cgroup idle weighting where supported.
The root authorization `ExecCondition` is not the mining process.

Source deployment uses the existing trusted layout/ledger/XMRig manifest paths.
Install creates/reuses the `xmrig` user and `xmrig`/`sparehash` groups, not a
`sparehash` user. Protected config, token, source and unit files are not writable
by unprivileged miners. Uninstall retires first, requires empty proof and removes
only matching receipted artifacts; foreign or changed payload content refuses.
Default removal retains config/manual state. Purge removes unchanged owned
configuration, not all historical state, foreign data or an external XMRig.

### 4.2 Lutris-derived detection: scope and dependency model

`gaming_scan.py` adapts Lutris `get_steam_game_pids()` from
`lutris/runners/steam.py` at revision
`1f32fe0e46e2a5d92fad9db2846c7cb7becd20c3`, GPL-3.0-or-later.
Source headers and [`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md) record
the attribution, upstream location and modifications; the wheel and sdist carry
notices and GPL text. Lutris is neither a runtime dependency nor an endorsement.

The read-only scanner walks numeric procfs entries and matches a `reaper` launch
prefix with NUL-separated `SteamLaunch` followed by numeric `AppId=...`, before
the game's `--` suffix. Any numeric App ID is eligible. It bounds work to 16,384
processes, 65,536 bytes per cmdline and a cooperative 500 ms scan budget. Candidate
UID/start-time identity and cmdline are rechecked. Definite exit churn is tolerated;
persistent read/parse/identity errors or exhausted limits prevent negative success.
A recognised match still inhibits if another inspection fails.

The existing dedicated gaming worker polls once per second, with one scan in
flight. Results expire after two seconds. Controller-side expiry also runs at
apply/tick and synchronous authorization, independently of aggregate timestamps
or worker completion. A late out-of-generation CLEAR cannot erase a new inhibition.
Restart uses existing dwell, 60 seconds by default, not a new cooldown engine.
Status exposes `detected`, `not_detected`, `unknown` or `disabled`, fixed reasons,
scan age/duration and match count without raw process argv.

Limitations are fundamental to the approved heuristic: non-Steam/unrecognised
launchers, changed/missing reaper markers, namespace/visibility restrictions,
unmarked surviving children and spoofed argv can cause misses or false positives.
A successful no-match scan is not proof of absence or complete game lifetime.
Polling plus the measured five-second fixture objective is not a hard real-time
guarantee. A blocked read is not forcibly interrupted by a cooperative clock
check; freshness prevents stale clearance, but worker shutdown/wait behavior
remains a review consideration. Low priority does not eliminate contention.

Runtime Python dependencies remain empty (`dependencies = []`, Python >=3.11).
OS facilities/tools such as procfs, systemd/cgroups and existing power helpers are
still required; "standard-library-only" does not mean no operating-system
prerequisites. No Steam client, session bus, Steam log, Lutris or GameMode setup
is required for scanner readiness. Authenticated manual hooks remain optional,
not another automatic provider.

### 4.3 Obsolete components actually removed

Verified against `git diff --diff-filter=D f5b100f..4d56914`:

| Removed file | Former purpose |
| --- | --- |
| `sparehash/gaming_steam.py` | Steam-log/replay experiment |
| `sparehash/gaming_producer.py` | Gaming producer/completeness experiment |
| `sparehash/gaming_session.py` | Session inventory/completeness path |
| `sparehash/gaming_state.py` | Old gaming state/completeness reducer |
| `tests/test_gaming_steam.py` | Exclusive retired Steam experiment tests |
| `tests/test_gaming_producer.py` | Exclusive producer tests |
| `tests/test_gaming_session.py` | Exclusive session tests |
| `tests/test_gaming_state.py` | Exclusive old-state tests |
| `tests/gaming_install_consent.py` | New-install GameMode consent fixture |

The remaining `gaming.py`, `gaming_install.py` and `gaming_ini.py` were not
deleted: verified hooks and ownership-safe legacy removal still use them.
Removed GameMode query/setup functionality is not a hidden fallback.

### 4.4 Remaining duplication, dead paths and unnecessary review surface

These are disclosed review targets, not changes made by this reporting task:

- `install.py` retains `upgrade_install`, `recover_install`, `complete_install`
  and its old upgrade dispatch block. Public option validation refuses those
  flags first, making that dispatch block unreachable through normal validated
  CLI use. `preserve=args.reconfigure` likewise remains although public
  `--reconfigure` refuses. Internal tests invoke retained functions directly.
- `cli_lifecycle.py` retains XMRig-update transactions; `cli_config.py` retains
  config-edit and incomplete benchmark-output plumbing. The controller retains
  benchmark/lifecycle modes and IPC handling. Public CLI refusal does **not** mean
  all internal code/protocol branches were removed or made independently complete.
- Source-release migration/journaling in `upgrade.py` and XMRig version/update/
  rollback handling in `xmrig_install.py` have overlapping transaction concerns
  but different payloads. They remain substantial code to review, not two active
  gaming providers. The plan required preservation, not another redesign.
- Providers and the controller both check gaming-result freshness/inhibition.
  This is deliberate defense against stale aggregate data, not proof of an
  accidental duplicate detector. Any later deduplication must preserve independent
  owner/authorization expiry and tests.
- Legacy INI helpers and their safety tests remain necessary for old receipted
  removals. Their presence alone is not dead code or permission for new home edits.
- `qa/run_all.sh`, older `qa_*.sh` suites, `qa/probe_gaming.py`,
  `qa/local_pool.py` and old fixtures remain broader/historical tools. They are not
  the current required release gate, and not all are realigned to the live policy.
  The required smoke also overlaps some private S3/S5 driver scenarios; there is
  not one repository-contained harness that reproduces every historical reboot run.
- Older docs and internal docstrings still contain historical versions/Todo
  terminology. In particular the preserved owner's old status and original
  stage reports need scope-aware reading. This report does not silently rewrite
  historical evidence or claim a completed documentation-wide consistency audit.

The narrower live path is more maintainable, but the repository is not a minimal
implementation with all historical complexity eliminated. This task performs no
cleanup or refactoring of these retained areas.

### 4.5 Intentionally deferred beyond v0.2

- Public in-place source upgrades, XMRig update/rollback, failed-publication
  recovery and upgrade completion; no alternate advertised bypass.
- Public benchmark, config editing and in-place reconfiguration.
- Native RPM/DEB/Nix/Flatpak or new distribution/deployer channels.
- Authoritative game absence, session admission/FULL coverage, Steam-log fallback,
  complete game-lifetime tracking and all-launcher guarantees.
- Broader image/hardware compatibility claims without evidence, and any stable/
  production-readiness or real-game validation not yet earned by testing.

Old migration journals, staging and holds must be preserved for investigation,
not deleted to force installation or mining. Fresh source install and explicit
reinstall after completed removal are the experimental supported path.

## 5. Cross-distribution and mining evidence

### 5.1 Exact lab images and scope

| Image | Environment recorded in final S3 runs | Administration |
| --- | --- | --- |
| `ubuntu-noble-20260926-v1` | Ubuntu 24.04, Python 3.12.3, systemd 255 (`255.4-1ubuntu8.17`) | QEMU guest agent |
| `bazzite-kde-44-20261012-v1` | Bazzite 44, Python 3.14.8, systemd 259 (`259.9-1.fc44`) | Authenticated virtual serial console |

All privileged acceptance belongs in disposable VMs, one at a time, with guest
networking disabled, no host filesystem sharing and Bazzite SELinux Enforcing.
No workstation install was used as a test. Guests, including failed attempts,
were stopped/forced and discarded; recorded final lab state was empty. There is
no Debian/Mint/other Atomic/Deck certification by inheritance. Preflight's
`VALIDATED` label matches a release line, not an authenticated baseline image.

### 5.2 Results must stay bound to their source snapshots

| Check | Final S3 Ubuntu snapshot | Final S3 Bazzite snapshot | Selected S4/S5 implementation |
| --- | --- | --- | --- |
| Install and installed-byte/receipt checks | PASS | PASS | Bazzite smoke PASS; Ubuntu final outcome unavailable |
| Controller ready; miner unprivileged/idle | PASS | PASS | Bazzite smoke PASS; no final Ubuntu assertion returned |
| Battery/thermal/manual/lifecycle callbacks | PASS, synthetic | PASS, synthetic | Bazzite smoke PASS, synthetic |
| Controller loss empties miner cgroup | PASS | PASS | Bazzite smoke PASS |
| Foreign content refuses removal | PASS | PASS | Bazzite smoke PASS |
| Non-purge uninstall retains config/manual state | PASS | PASS | Bazzite smoke PASS |
| Explicit reinstall preserves pause until resume | PASS | PASS | Bazzite smoke PASS |
| Purge removes matching owned config/units/CLI | PASS | PASS | Bazzite smoke PASS |
| External artifact/foreign sentinels preserved | PASS | PASS | Bazzite smoke PASS |
| Post-purge fresh reinstall and actual reboot/autostart | PASS | PASS | Final candidate replay incomplete on both images |
| SELinux and captured audits | Not applicable | Enforcing; no AVCs observed pre/post reboot | Bazzite smoke Enforcing; no observed AVCs; no candidate reboot audit |

The release-smoke driver ends purged/retired with no miner and deliberately does
not reboot. S3 supplies actual reboot evidence for S3, not the selected later
installer source. An earlier pre-hardening Ubuntu smoke pass is real but is not
final-source acceptance. S5's failed guest-agent launch supplies no inferred
install, stop, removal or reboot verdict at all.

Sensor fixtures were files mounted read-only into the controller namespace, not
physical battery hotplug or CPU heat. Game fixtures were synthetic argv, not
native/Proton/Flatpak launches or KDE/Gaming Mode. Two foreground CPU workers
exercise concurrent load, not arbitrary latency, GPU/VR or hardware performance.
Three-second fixture dwell compresses scenarios; production defaults remain 60.
S1's unsafe-rate polling and S3's staging defects were repaired at the appropriate
fixture/product boundary; production IPC limits, cgroup proof and SELinux were
not relaxed to obtain a pass. Transport root cause remains unresolved.

### 5.3 Was actual XMRig mining proven against a local synthetic pool?

**No. The S1–S5 evidence does not establish that actual upstream XMRig accepted
synthetic work and produced a nonzero RandomX hashrate against `qa/local_pool.py`.**

S1 used an idle dummy miner. The later fixture is
`qa/fixtures/synthetic_xmrig.py`, a SpareHash-owned Python stand-in, explicitly
not upstream XMRig. It supports version/dry-run probe responses and an
authenticated loopback API returning `[0, 0, 0]` hashrate and
`synthetic; never connected`. It never hashes or connects to any pool. Executing
this fixture through the real sandbox validates process lifecycle and the
authorization boundary, not the RandomX engine, pool protocol or real mining.

`qa/local_pool.py` exists as historical/broader QA infrastructure and can provide
synthetic jobs, but its existence and a configured `127.0.0.1` pool address are
not proof it was used successfully by a real miner in these acceptance runs.
The older owner's status also records no hashrate; it does not close this gap.
Neither upstream signed-binary end-to-end installation nor real-pool mining,
accepted public shares, profitability or sustained mining performance is claimed.
Real-pool mining was prohibited in the lab. Approved synthetic fixtures satisfy
their stated lifecycle scope; this missing hashing evidence is disclosed rather
than retroactively invented or silently made a new implementation feature.

## 6. Quality, safety and security claims

### 6.1 Test counts and tooling

| Stage/snapshot | Complete suite | Focused suite | Typing |
| --- | --- | --- | --- |
| S1 | 1,394 tests + 625 subtests passed | 203 + 130 | 60 production files clean |
| S2 | 1,114 + 608 passed | 354 + 226 | 56 clean |
| S3 | 1,117 + 608 passed | 169 + 43 | 56 clean |
| Final S4 implementation / selected S5 code | 1,159 + 608 passed per interpreter | Final guard/preflight/install/bootstrap run: 74 + 7 passed | 56 clean on all four interpreters |
| S5 documentation handoff rerun, Python 3.11 | 1,159 + 608 passed | Not a new focused acceptance run | 56 clean |

Final full-matrix interpreters: **CPython 3.11.13, 3.12.3, 3.13.5 and 3.14.9**.
Bazzite's VM Python 3.14.8 is separate from that host matrix's 3.14.9. Counted
final runs have zero reported test failures. Historical failures/timeouts listed
above remain evidence; "passed" refers to completed final runs, not every attempt.
No new full suite or destructive test was run solely to write this report.

Development tools were locked through `uv.lock`; the final environments used
pytest 9.1.1, mypy 2.4.0 and Ruff 0.16.10. Runtime dependencies remain empty.
The four-version CI configuration uses `uv sync --locked`, `.venv/bin/python -m
pytest`, mypy and the README inventory checker on Ubuntu 24.04. It runs scoped
release lint and a separate wheel/sdist artifact job. These are configured jobs,
**not a successful hosted GitHub run**. At the handoff nothing was pushed; the
workflow triggers on `main` pushes and pull requests targeting `main`.

Scoped Ruff passed for `gaming_scan.py`, `__main__.py`, `preflight.py`, the new QA
driver/fixture/artifact checker and release-surface/QA tests. Existing findings
remain elsewhere: e.g. `install.py` retains I001/F401 and older tests retain
SIM117, N803 and B023 findings. Recorded baseline comparisons show no added rule
counts in the compared older files; they are not proof of globally lint-clean
code or absence of semantic defects. S1/S3/S4 baseline counts refer to different
file scopes and must not be summed as one repository-wide count.

README inventory and `git diff --check` passed. Offline `uv build` produced wheels
and sdists; artifact checks verified current wheel Python bytes, shipped GPL/
notices and absent `Requires-Dist` metadata, plus licence/notices in the sdist.
They do not certify upstream XMRig signatures, bit-for-bit reproducible builds
across machines or every possible installer input. The build backend is distinct
from runtime dependencies. Source tests/research and historical modules were
preserved before deletion; reduced test counts do not establish reduced safety
coverage by themselves, so the replaced test surface is an explicit review target.

### 6.2 Independent protections and evidence boundaries

| Protection | Current behavior and evidence | Limit |
| --- | --- | --- |
| Battery/power | Existing power evidence and priority callbacks can stop/inhibit independently of gaming; S3/S4 file-backed battery transition tests and unit regressions passed | No physical battery/UPS/hotplug certification; existing power/maintenance policy still applies |
| Thermal | Enabled guard retains hysteresis and inhibits on missing/invalid sensor input; file-backed high/cool readings and manual independence passed | Thermal monitoring defaults **off**; no blanket thermal-protection claim when disabled, and no physical sensor validation |
| Manual pause | Persistent manual state survives game-state changes and tested non-purge reinstall until explicit resume | Gaming recovery does not clear the manual hold; purge/removal semantics must be read precisely |
| Controller authority | Single owner controls state, generations, start permission and job ordering; provider output cannot directly execute mining; authorization rechecks freshness | This is tested design, not an independent proof of race freedom |
| Scanner failures/staleness | UNKNOWN, limits, malformed relevant input, genuine read errors, hung-worker expiry and late CLEAR generation cases inhibit in regression tests; proc cmdline-bound failure exercised in VMs | Successful negatives remain heuristic; unrecognised games may be missed |
| Lifecycle/retirement | Durable independent holds, required empty proof and receipt-safe removal retained; synthetic lease/removal tests passed | Old holds/journals are not automatically repaired or cleared to bypass refusal |
| Controller loss | BindsTo/actual Jobs/cgroup stop path tested with real systemd; measured cgroup-empty timings above | No claim of universal real-time stop latency on every host |
| Miner privilege/foreground priority | `xmrig` UID, SCHED_IDLE and nice 19 checked in VMs; protected wrapper/spec and static unit retained | No upstream hashing or zero-contention guarantee; controller itself remains privileged |
| Ownership-safe removal | Foreign source sentinel refused; matching-only removal and external/config sentinels survived; bootstrap/probe cleanup regression tests passed | No assurance that arbitrary historical partial installations can be force-recovered |

The unit templates, power/thermal/state policy modules, miner job/exec containment
and uninstaller policy were not changed by S1–S5's staging/release work; the
baseline-to-HEAD diff should be used to verify the precise scope. S1 did change
gaming reduction/freshness integration in the existing controller/runtime.
No SELinux policy module, security disablement, new root service, runtime lab
dependency or new permanent privileged staging root was introduced. A passing
synthetic run is evidence for those exercised paths, not a comprehensive
penetration test or assurance against every denial-of-service/input race.

## 7. Evidence access and independent GitHub review

Public review material is the code, tests, six commits, stage reports, gaming
contract, notices, candidate report, owner checklist and this document. Useful
entry points are:

- `git diff f5b100f..4d56914 -- sparehash tests systemd` for production/test scope.
- `tests/test_gaming_scan.py`, `tests/test_gaming_runtime.py`,
  `tests/test_gaming_coverage.py` and controller safety suites for the detection
  reducer, generations, errors and dwell.
- `tests/test_bootstrap.py`, `tests/test_portable_probe.py` and removal/retirement
  suites for ownership and staging boundaries.
- `tests/test_release_surface.py`, `tests/test_qa_release_smoke.py`,
  `.github/workflows/ci.yml` and `qa/check_artifacts.py` for the release gates.
- [`gaming-integration-contract.md`](../gaming-integration-contract.md),
  [`platforms.md`](../platforms.md) and the
  [owner checklist](../testing-0.2-owner.md) for public claims and operational limits.

Private evidence roots are `.omo/evidence/v020/task-2/s1-steam/`,
`s2-retirement/`, `s3-lifecycle/`, `s4-release/` and `s5-candidate/` under the same
task directory. These hold raw logs, source/fixture manifests, timing summaries,
VM archives, failed attempts and checksum records. Verified pre-edit archives
also retain retired research. They are deliberately gitignored and **will not
appear automatically in a GitHub review**. Their paths are provenance pointers,
not public downloadable artifacts or independent corroboration by themselves.

The shipped smoke can reproduce its stated non-reboot scope in an authorised
disposable VM; full historical S3/S5 drivers and host lab administration are not
shipped runtime dependencies. A reviewer needing raw/full-reboot evidence needs
an owner-provided, privacy-reviewed evidence bundle or an independently observed
rerun. Do not publish host-internal controllers, credentials, personal addresses,
wallets, tokens or unredacted logs just to make evidence public.

This reporting task adds only this document. It does not fix code, refactor
retained internals, repair the lab, rerun privileged tests, push a branch, open a
PR, trigger hosted CI or publish a release. Repository cleanliness, local tests
and implementation completion are distinct from hosted review and RC acceptance.

## 8. Release verdict and remaining actions

| Stage | Verdict | Qualification |
| --- | --- | --- |
| S1 | PASS | Small detector and synthetic actual-runtime gate |
| S2 | PASS | Focused retirement/configuration/safety regressions |
| S3 | PASS | Both named images, 18 checks each, S3 source only |
| S4 | PARTIAL | Code/local quality gates and exact-source Bazzite smoke passed; final Ubuntu smoke incomplete |
| S5 | PARTIAL | Selected artifacts/report/checklist exist; final dual-image candidate acceptance and owner gameplay incomplete |

**Independent review can begin now; an experimental RC should not be declared
from the present evidence.** The remaining sequence is:

1. Review the committed implementation and its retained complexity; obtain
   hosted GitHub CI results through an approved PR/push workflow. This report
   itself does not authorise pushing or weakening any release gate.
2. Diagnose and restore reliable Ubuntu guest-agent execution in the separate
   lab, with owner approval for infrastructure changes. A successful small
   command is not enough; sustained driver command/transfer/reboot reliability
   must be established without changing security or interpreting timeouts as passes.
3. Replay final selected-source acceptance on fresh Ubuntu and Bazzite guests,
   including install, removal/purge, explicit reinstall, actual reboot, sandbox/
   unprivileged miner, strict blockers, source identity and captured AVC checks.
   Bind any changed code/artifacts to new identities and repeat relevant gates.
4. Once automated and required VM gates pass, issue the experimental RC report
   with real-game/hardware validation explicitly pending. Record actual upstream
   hashing/pool testing separately if performed; do not describe the non-hashing
   fixture as that evidence.
5. Conduct owner-supervised native/Proton and intended desktop/mode tests;
   record recognised cases, misses, genuine scanner failures, stop/resume timing
   and foreground impact separately before upgrading public validation labels.

The outstanding transport and candidate-evidence gaps are real blockers, not
requests for new features. The best-effort misses and explicitly deferred
workflows are accepted scope limitations, not completed functionality or hidden
fallback promises. No additional implementation/refactoring is performed here.
