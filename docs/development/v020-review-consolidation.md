# v0.2 pre-RC review consolidation

Date: 2026-10-31. Review branch: `review/v0.2-pre-rc`, targeting `main` in
[`barrelful/sparehash`](https://github.com/barrelful/sparehash).

**Development review only, not a release or RC declaration.** This report
supersedes file-presence, test-count and publication statements in the historical
[S1–S5 report](v020-s1-s5-implementation-report.md) and
[candidate record](../release-0.2-candidate.md). Their older evidence/hashes remain
valid only for their identified snapshots. The current source changes invalidate
any assumption that old candidate artifacts describe this branch.

## Inventory and recovery

The initial working tree was clean at `af7f2cf`. Inventory covered all local
branches, freshly fetched remote branches, all seven stashes and their index/
untracked parents, all worktrees, untracked files and ignored evidence. There
was one worktree, no nonignored untracked files and 3,752 ignored files. Ignored
files comprised `.omo` (689), old `E` evidence (107), old `dist` output (5),
reproducible virtualenv/cache files and Python bytecode. No hidden worktree was
discarded. `origin/main` was the only remote branch at inventory time.

Previously verified T0 preservation was **not repeated**. A private incremental
archive named `review-consolidation-20261031-125351` under the existing external
`sparehash-archive` contains:

- A verified post-T0 Git bundle, ref/stash inventory and recovery instructions;
  it complements T0 rather than replacing its historical objects/reflogs.
- A hash-verified delta archive: 325 changed/new non-cache workspace files,
  3,309,009 uncompressed bytes, compared with T0's workspace manifest.
- Old `E/` and `dist/` trees moved outside the project after verifying every
  remaining file against T0 or the delta manifest. No blanket cleanup was used.

Existing per-stage archives, old branches, all stashes and the complete baseline
and development history remain intact. Reproducible virtualenvs/caches were
inventoried, not backed up as unique research. New verification evidence lives
under gitignored `.omo/evidence/v020/review-consolidation/` and is kept private.
No archive, host controller, token or personal network detail belongs in the PR.

Recovery was also verified in an isolated temporary bare repository: T0 plus the
two incremental Git bundles reconstruct the published review implementation;
`git fsck --full` passed. Every original ref and all seven stash IDs remained
unchanged. This verifies recovery without repeating the original T0 backup.

### Branch dispositions

Unique counts below compare the original branch tips with pre-cleanup `af7f2cf`;
they are not post-cherry-pick ancestry claims.

| Branch/ref | Original tip / unique commits | Classification and disposition |
| --- | --- | --- |
| Local `main` | `f5b100f`; 0 unique | Already integrated baseline; unchanged, not pushed or reset |
| Remote `origin/main` | `335804f`; 0 unique | Already integrated release baseline; left unchanged; PR target |
| `atlas/v020-task-2-g1-feasibility` | `af7f2cf`; review starting point | Already integrated S1–S5 implementation/report history; retained unchanged as a historical branch |
| `atlas/v020-task-2-observer` | `df08762`; 1 unique | Historical/superseded explicit-log observer, every observation UNKNOWN; not merged into the reaper detector. Its seven source/test/doc files remain recoverable from the branch/T0 archive |
| `atlas/v020-task-17-recovery-anchor` | `dd7d024`; 1 unique merge | Superseded FULL/conservative/GameMode admission/config/status design; useful refusal/no-silent-downgrade intent is already represented by revision-3 migration refusals. No merge and no architecture resurrection |
| `atlas/v020-task-3-harness` | `bff9104`; 4 unique | Valuable non-mining diagnostic fixes selectively integrated; original branch retained |

The harness commits were cherry-picked in original order, retaining authorship
and history rather than rewriting or merging unrelated experiments:

| Original | Review commit | Retained value |
| --- | --- | --- |
| `6f8d997` | `c193666` | Interpret legacy `nogroup`-unavailable evidence without pretending the actual primary-GID probe failed |
| `8c1741e` | `0d5031a` | Locale-independent audit query keywords with retained PID/window attribution |
| `7c36c0a` | `f2064e1` | Installed-policy context comparison by role/type/level for explicitly staged files; exact redirect-root checks retained |
| `bff9104` | `051bc6d` | Accept audit 4.2.1's raw empty no-match output only with the expected exit/command/window evidence |

These are diagnostic parsing/attribution fixes, **not** SELinux policy changes,
a bypass of the actual sandbox or fresh VM acceptance. Their tests include
failure/contradiction cases. A FEASIBLE diagnostic result still says
`release_acceptance=false`.

### Stash dispositions

All seven IDs/order match the verified T0 inventory; no stash was applied,
dropped, popped or rewritten. "Integrated" below denotes useful content/intent
already present in evolved code, not necessarily byte-identical whole patches.

| Stash | ID prefix | Classification and disposition |
| --- | --- | --- |
| `stash@{0}` | `0151a1d` | Superseded/deferred: README upgrade guide link and separated migration-journal/hold prose. Current docs preserve holds but explicitly defer public completion/recovery; old upgrade instructions not applied |
| `stash@{1}` | `53f11fb` | Historical/superseded: GameMode conservative/FULL admission and NO-GO documentation; incompatible with approved best effort |
| `stash@{2}` | `c1d3ed1` | Mixed already integrated/superseded: explicit consent, VM-only testing and independent manual pause guidance are represented in current docs; GameMode fallback and advertised `--upgrade` path rejected |
| `stash@{3}` | `3768582` | Mixed already integrated/superseded/deferred: source consent, preflight and deployer boundaries evolved into current code/docs; broad distro validation, old exit codes, FULL/GameMode modes and upgrade completion claims not adopted |
| `stash@{4}` | `80f3d22` | Historical/superseded gaming-session/admission prose; independent pause/latency caveats already documented in the live design |
| `stash@{5}` | `0cdd8ff` | Recovery intent already integrated and evolved: operation-ID-bound retirement, corrupt/unrelated hold refusal, recovery tests and VM guidance. Its untracked-parent files were inspected. Old wholesale migration/installer removal is deferred, not replayed over safer current recovery |
| `stash@{6}` | `9e241f1` | Already integrated: deployment spec, ledger/manifest schemas and tests are byte-identical at the reviewed tips except `xmrig_install.py`, which has subsequent changes. No stale overwrite/cherry-pick |

The stash-5 untracked parent also retains old per-task evidence and documentation;
these are archival provenance, not files to restore into the production tree.
There was no additional unintegrated revision-3 feature requiring a merge.

## Cleanup and deliberately retained debt

### Removed or consolidated

- Removed the tracked `E/task-31`–`task-34` report fragments and empty `scripts/`
  placeholder. Old ignored `E/` data and `dist/` packages moved to the verified
  private archive, not silently deleted. `/E/` and reproducible tool caches are
  explicitly ignored to avoid reintroducing local evidence into commits.
- Removed the obsolete `qa/run_all.sh`, old GameMode/session/admission entrypoints,
  old fixture overlays/shims and their exclusive helper tests. Those runners
  advertised implicit reinstall, omitted consent or assumed the retired gaming
  provider. Updating them would recreate a redundant unsupported QA architecture.
- Removed C1 completeness research `qa/probe_gaming.py` and its admission tests;
  historical Steam-log production modules were already removed in S2 and are
  not reintroduced by branch integration.
- Removed unused CLI configuration-edit/benchmark implementation from
  `cli_config.py`; kept the supported read-only `show` behavior, secret-separation
  tests and the seeded-wizard regression. Removed the unreachable validated
  installer upgrade dispatch block and unused `complete_upgrade_main` adapter.
  Public deferred verbs/flags still return exit 2 before effects.
- Removed three redundant pre-revision research narratives and the unapproved
  packaging proposal from shipped docs; links now point to their immutable Git
  history. Kept the detailed S1–S5 implementation report and scoped stage records.
- Replaced the obsolete owner's status page with a current-status pointer and
  link to its exact preserved historical text. No old observations were hidden
  through history rewriting. Marked old candidate/source hashes historical and
  corrected current docs' stale v1/v0.1.1 wording.
- Extracted and retained the local-pool/foreground pure contract tests before
  removing their obsolete contention-runner test container. Current runtime,
  controller, receipt, blocker and legacy-removal tests are not retired merely
  to reduce the total test count.

### Retained, with reasons

- **One required QA entrypoint:** `qa_release_smoke.sh` → `release_smoke.py`, plus
  its consent/source guards, owned non-hashing fixture and tests.
- **Atomic non-mining probe:** useful independent signed-executable/sandbox/audit
  diagnostics, now including the relevant branch fixes; not an RC gate.
- **Read-only security diagnostic and local-pool/spin utilities:** useful for
  later authorised diagnostics/performance work. Their pure tests stay. No
  claim that actual upstream hashing was performed or proven is added.
- **`cli_lifecycle.py`:** not just a dead public verb; uninstaller error handling
  imports its error type and controller-lifecycle IPC tests exercise its adapter.
  Removing it requires disentangling live recovery dependencies, beyond safe
  consolidation. Public `update` remains refused.
- **`upgrade.py`, `migration.py`, receipt migration and preservation tests:**
  retained internal transaction/recovery machinery with shared manifest/lease
  invariants. Public workflows remain disabled. Blind deletion could remove
  safety coverage or the ability to inspect existing historical holds.
- **Internal benchmark/controller modes:** retained tested protocol/job paths;
  public benchmark and its unused CLI adapter are not supported. Removing these
  modes would alter controller/runtime authorization rather than prune dead CLI.
- **Legacy `gaming_ini.py`/`gaming_install.py`:** required for ownership-safe
  uninstall of old receipted home hooks; never a new setup/provider fallback.
- **Duplicate freshness checks:** Providers and controller checks are deliberate
  stale-result defenses, not duplicate automatic detectors. Not deduplicated.

No detector, battery/thermal/manual policy, controller authority, miner unit,
receipt/removal policy, new runtime dependency or privileged path was changed.
No additional feature, distribution channel or safety relaxation was added.

## Verification and publication

| Check | Result |
| --- | --- |
| Complete local suite, CPython 3.11.13 | **1,091 tests + 605 subtests passed**, 53.08 s |
| Focused CLI/release/Atomic/local-pool checks | **241 tests + 80 subtests passed**, 9.80 s |
| Mypy | Clean, 56 production files |
| Scoped Ruff | Passed; no added rule counts in the compared changed older files |
| README inventory | Passed: 16 commands, 16 flags, 22 config keys |
| Local Markdown references | All 44 checked links resolve |
| Wheel/sdist | Offline build and source/GPL/notices/no-runtime-dependency checks passed |
| Secrets | Gitleaks 8.30.1: no leaks in outgoing history or the exported source tree; raw reports retained privately |
| Hosted CI | [Run 41204318369](https://github.com/barrelful/sparehash/actions/runs/41204318369) passed on `69126df`: Python 3.11–3.14 and packaging; each interpreter reported 1,090 passed, 1 skipped, 605 subtests passed |

Local development dependencies were installed with `uv sync --locked`. The
hosted workflow covers Python 3.11–3.14, typing, scoped lint, docs inventory and
packaging. Hosted runners reported one existing environment-prerequisite skip
per interpreter, not a new skip added by consolidation. The full local run
exercised all 1,091 tests. CI now prints skip reasons (`-ra`) rather than hiding
the distinction; current-head status is authoritative in the PR checks.
Existing global Ruff findings are not relabelled clean: remaining
older code includes an unused import in `install.py`, nested-context style in
`test_cli.py` and pre-existing security-diagnostic style findings.

One focused test attempt timed out after extraction of the seeded-wizard test
omitted the original valid pool hostname, causing the fixture's empty-answer
callback to repeat its prompt. Restoring a valid fixture pool fixed the test;
no production behavior, assertion or safety limit was relaxed. The completed
focused and full runs above passed without skips added for this cleanup.

Physical Python source line counts, relative to `af7f2cf` (blank/comment lines
included; bytecode and caches excluded):

| Tree | Before files / lines | After files / lines | Net lines |
| --- | ---: | ---: | ---: |
| Production `sparehash/` | 56 / 12,964 | 56 / 12,723 | **−241** |
| Tests `tests/` | 76 / 16,205 | 70 / 14,651 | **−1,554** |
| Python QA helpers `qa/` | 16 / 3,223 | 10 / 1,738 | **−1,485** |

The new live-source inventory SHA-256 is
`27d9833b4ebeaea735dc4261e9d6223d64055850493ca67d09b4b06a873953eb`.
It is not an old VM acceptance pin or a whole-repository hash. Documentation and
Atomic diagnostic changes outside that inventory are bound by Git history.

The decrease in total tests must be read alongside the explicit obsolete-helper
removals and newly integrated harness regressions; it is not a claim that fewer
tests prove greater safety. No fresh four-version local matrix is implied by the
one-version local run; the PR workflow supplies the hosted matrix.

No privileged/lifecycle VM run is needed to establish deletion of unreachable
CLI code and obsolete QA; no such run is claimed here. The earlier guest-agent
transport blocker remains unresolved, and the consolidated source is not covered
by an old candidate's final VM pass. No workstation installation is modified.

The published development branch is
[`review/v0.2-pre-rc`](https://github.com/barrelful/sparehash/tree/review/v0.2-pre-rc).
The [review PR is #1](https://github.com/barrelful/sparehash/pull/1), targeting
remote `main`; its head commit/check links identify the exact
reviewed state. No merge, release tag, force-push or historical branch deletion
is authorised or performed by this task.

## Remaining release blockers

1. Diagnose reliable Ubuntu guest-agent command/transfer/reboot operation in the
   separate lab before relying on a complete driver run. No infrastructure fix
   is attempted in this consolidation task.
2. Bind fresh source/artifact identities to final candidate-wide Ubuntu/Bazzite
   lifecycle/reboot acceptance. Older S3 passes and S4 Bazzite smoke retain their
   original source scope and are not rewritten as proof of this branch.
3. Actual upstream XMRig hashing against a local synthetic pool remains unproven;
   the release-smoke fixture never hashes or contacts a pool.
4. Real native/Proton, KDE/Gaming Mode, physical sensor behavior and foreground
   impact remain owner testing, not synthetic certification.

The PR is for independent review and later supervised hardware testing. It must
not be merged, tagged or represented as an experimental RC by this task.
