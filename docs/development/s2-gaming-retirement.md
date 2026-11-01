# S2: focused gaming retirement

**S2 acceptance passed for unit/configuration, typing, scoped lint and packaging.**
Stopped before S3. No new gameplay, physical sensor, installer or cross-distro
lifecycle validation is claimed. S1's synthetic VM results remain the runtime
evidence, not proof of real native/Proton or KDE/Gaming Mode behavior.

## Disposition

- Removed `gaming_steam.py`, `gaming_producer.py`, `gaming_session.py` and
  `gaming_state.py` after auditing production, test and QA callers. Their only
  remaining code consumers were each other and six exclusive experiment suites.
  Those six suites and the old GameMode installer-consent fixture were archived
  and removed from the default suite, not skipped or marked expected-failure.
- `gaming_scan.py` remains the only automatic detector, unchanged from S1.
  No Lutris dependency, log activation path, session-completeness guard or
  automatic provider fallback remains.
- Removed GameMode queries/aggregation/helper resolution from `gaming.py`;
  retained verified accounts and authenticated hooks. Removed unused runtime
  home lookup and the temporary legacy-mode scanner branch.
- Removed new-install GameMode consent, package installation, group additions,
  home/INI merging and session-bus checks. `gaming_install.py` now contains only
  legacy unmerge. Existing receipt, account/path ownership and membership removal
  logic remains unchanged; the shared legacy INI engine/security regressions
  remain available for safe removal and fixture construction.
- Removed `GamingIntegration`, the Steam-log field and obsolete migration
  property from the live schema. New serialization writes only `gaming.enabled`
  and `gaming.users`. The latter retains hook-account permissions, not scanner
  coverage; an empty list is valid even with scanning enabled.
- Deprecated `integration="auto"` and `steam_log=false` remain readable with
  warnings and are omitted on save. Explicit conservative/GameMode modes,
  unknown modes, `steam_log=true` and malformed legacy values refuse with
  actionable migration guidance. An explicit disabled value is never re-enabled.
- Wizard/preflight no longer require FULL admission, Steam, GameMode, a bus or
  configured accounts. Preflight reports `BEST_EFFORT` or `DISABLED`, not a game
  coverage certification. An explicit disabled switch is allowed unattended and
  reported; strict non-gaming compatibility/refusal gates remain unchanged.
- Removed unused provider-completeness status types and admission report fields.
  Live status distinguishes `BEST_EFFORT`, `DISABLED` and unavailable `UNVERIFIED`.
- Updated the gaming contract, README, security/architecture/platform gaming
  sections and third-party notices. Preserved investigation findings with
  historical banners. The C1 QA research probe and its pure tests remain for
  reproducing research, explicitly not production/readiness dependencies.
  Broader destructive release-QA alignment remains S4, not this stage.

## Recoverability

A targeted pre-S2 archive was verified under
`~/sparehash-archive/s2-gaming-retirement-20261026-065738/`:

- `sources.tar.gz` and `manifest.json`: 51 current source/test/document files,
  including every retired module/test and the untracked G1/evidence suites.
- Binary working-tree patch and original status; additional documentation
  snapshot before its edits. Source members were read directly from the archive
  and SHA-256-verified before retirement and again at completion.
- T0, the pre-S1 snapshot, earlier branches/stashes and private task evidence
  were not replaced or deleted. No full repository preservation was repeated.

The user's dirty `docs/release-0.2-status.md` remains untouched. The S1 scanner,
controller/controller interfaces, miner Jobs/cgroup empty proof, state, power,
thermal and unit templates remain unchanged by S2. No supervisor, service,
IPC protocol, policy module, privileged path or runtime dependency was added.

## Checks

| Check | Result |
| --- | --- |
| Focused gaming, migration, wizard/preflight, installer/removal fixtures and strict safety regressions | 354 passed; 226 subtests passed |
| Complete unit suite | 1,114 passed; 608 subtests passed |
| Mypy | Clean across 56 production files |
| Scoped scanner/gaming/setup/status and changed test lint | Passed |
| Other touched-file Ruff baseline comparison | No new findings; existing findings left outside focused retirement |
| README inventory checker | Passed: 16 commands, 16 flags, 22 config keys |
| `git diff --check` | Passed |
| Offline wheel and sdist build | Passed |
| Artifact inspection | No retired modules; attribution/GPL present; no mandatory runtime dependencies |

The lower suite count compared with S1 (1,394 tests) reflects archived log replay,
session completeness, GameMode query/setup and obsolete admission assertions,
plus their replacements. Scanner/controller fault, churn, identity, freshness,
generation, dwell, priority-stop, blocker independence and hook authorization
regressions remain. Additional checks cover scan/hook hold independence, legacy
unmerge account revalidation and disabled-versus-best-effort status.

The initial final-check run exposed two incomplete new status fixtures (missing
the required `ready` field), a newly unused runtime import and trailing whitespace;
these were corrected. No production safety relaxation or test skips were used.
Older-file Ruff findings are explicitly recorded by baseline rule counts, not
reported as a globally clean repository.

Only hermetic unit/private-tree fixtures were run on the development host; no
host installation or service was modified. Full installation/removal/reinstall/
reboot and SELinux acceptance are still S3, requiring disposable VMs. Actual
gameplay remains owner testing after automated/VM RC gates.

Evidence: `.omo/evidence/v020/task-2/s2-retirement/` (gitignored), including test
logs, recovery/artifact checks, lint comparison and final source hashes. Work is
uncommitted on `atlas/v020-task-2-g1-feasibility`; no push was performed.
**Recommend S3 only after explicit owner approval.**
