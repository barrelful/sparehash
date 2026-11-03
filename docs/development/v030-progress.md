# v0.3.0 implementation handover

Implementation is complete; **release signoff remains blocked** below. All PRs
use squash merge only after five green CI checks. GitHub links carry live final
merge/check status; no release approval is implied by merging preparation code.

| Milestone | PR | Implementation status |
| --- | --- | --- |
| 1: version / signed release identity | [#4](https://github.com/barrelful/sparehash/pull/4) | Merged after green CI |
| 2: discovery / verified staging | [#5](https://github.com/barrelful/sparehash/pull/5) | Merged after green CI |
| 3: app transaction / restoration recovery | [#6](https://github.com/barrelful/sparehash/pull/6) | Merged after green CI |
| 4: cleanup / acceptance / release preparation | [#7](https://github.com/barrelful/sparehash/pull/7) | Code/docs complete; acceptance caveat below |

## Validation

- Frozen runtime source: `5b24684d156453e5701386197ea8e74ddaae420f`; subsequent
  changes are documentation only. Host-safe suite: **1,258 passed / 567 subtests**.
  Package typing, supervised-QA typing, focused lint, README inventory, stamped
  wheel/sdist/source/licence/dependency checks and isolated real GPG signing pass.
- All seven actual scenarios passed on **final-source Bazzite**, SELinux Enforcing,
  zero captured AVCs, including power-cycle/broken-live-CLI backup recovery,
  automatic activation-failure restoration and receipt-aware uninstall.
- All seven passed on earlier Ubuntu/Bazzite source `a275f7a`. Intermediate M4
  Ubuntu `5aa3184` passed seven plus actual fresh-reinstall/receipt-reset/uninstall.
- **Final-source Ubuntu: INCONCLUSIVE, not a pass.** Two disposable attempts lost
  the guest-agent transport; a controlled power-cycle still had no connected
  agent. No diagnostic product conclusion or mock substitution is claimed.
- All guests were stopped/discarded; the lab is empty. Networking stayed disabled;
  no host lifecycle/mining, real wallets/pools, policy changes or storage shares.
- Evidence (gitignored): `.omo/evidence/v030/milestone-3/{ubuntu,bazzite}/`,
  `milestone-4/{ubuntu-pre-final,bazzite-final}/`, and
  `milestone-4/ubuntu-final-blocker.json`; final fixture manifest/public test key
  and signed fixture bundle are also under milestone-4. An initial Bazzite serial
  transfer timeout was resolved by stop/restart and compact, runtime-identical
  signed fixtures, not by weakening lab policy.
- The real builder produced verified **test-key-only** complete source + `.asc`
  and fully stamped wheel/sdist at `/tmp/opencode/v030-verified-test-artifacts/`.
  No-Git identity agrees on the full frozen commit. Temporary isolated-clone test
  tags were removed; no project/origin v0.3.0 tag or published release exists.

## Blockers and exact next task

1. Owner/lab maintainer: repair and revalidate Ubuntu guest-agent administration,
   or approve another disposable Ubuntu environment; rerun the seven final-source
   scenarios. Do not use the workstation or silently substitute mocks.
2. Owner: independently confirm/provide the maintainer public certificate/full
   primary fingerprint, pin it separately from XMRig, and confirm signing-key
   availability. Production trust is deliberately **empty/fail-closed**; no test
   certificate or bypass is embedded in production.
3. After those gates, review the key/release PR with passing CI and verify the
   final production-signed artifact. Only the owner then approves the clean merged
   tag, draft assets and experimental publication; see `docs/release-0.3.0.md`.

Real read-only GitHub discovery was exercised; existing releases lack `.asc`.
Production-signed download verification and online end-to-end guest coverage are
not claimed. GitHub HTTPS works; SSH writes timed out. OAuth lacks `workflow`
scope, so existing CI stayed unchanged; new tests run in its full suite and new
files were linted locally. No further runtime work is pending absent new evidence.
