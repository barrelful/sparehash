# Experimental v0.2 candidate acceptance

**Implementation complete; candidate-wide VM acceptance blocked by Ubuntu's
guest-agent transport. No RC or production-readiness declaration.**

Updated 2026-10-27. S1–S4 implementation and the S5 source/artifact selection and
owner-test handoff are complete. S5's final dual-image lifecycle/reboot acceptance
is not complete. Real-game testing also requires the owner; it cannot be completed
by this automated session.

This is the current revision-3 report. `release-0.2-status.md` is the preserved
owner-authored historical status for an older snapshot, not current installation
guidance. It remains intact in Git. The [S1](development/s1-steam-detection.md),
[S2](development/s2-gaming-retirement.md), [S3](development/s3-lifecycle-reliability.md)
and [S4](development/s4-release-alignment.md) reports retain their original scope.

## Selected implementation

- Commit: `f79718de43bed66fdbb77af202b6c57f27613a09`.
- Source/units/installer/fixture inventory SHA-256:
  `2f9e83812c8f3c7e0c8067854bd232448817b7917dcdd8251cda4f72c6535150`.
- Wheel SHA-256:
  `f314f2369a4b00d72d4020643cb0840b6382474781b57804991482b1a57c1468`.
- Source distribution SHA-256:
  `3bb74a34c83ca8532a2385cfe3407921642a9d1686149d19fab91e1f6b7e82c5`.

These are the selected snapshot's artifacts, not a promise that rebuilding a later
documentation commit produces identical archives. Follow-up reporting/checklist
commits must not change the tested runtime, installer, units or fixture inventory.
No release tag, package registry upload or remote push is part of this operation.

## Acceptance gates

| Gate | Result |
| --- | --- |
| Python 3.11.13 / 3.12.3 / 3.13.5 / 3.14.9 | 1,159 tests + 608 subtests passed on each |
| Mypy | Clean, 56 production modules under all four interpreters |
| Scoped release lint / README inventory / whitespace | Passed; older baseline lint findings are not claimed clean |
| Wheel/sdist | Clean builds, shipped GPL/attribution, no runtime dependencies |
| Hosted GitHub CI | Not run: no push; corresponding local locked-tool gates passed |
| Ubuntu Noble candidate-bound lifecycle/reboot | Blocked: guest-agent timeout; outcome unknown, not passed |
| Bazzite 44 candidate-bound lifecycle/reboot | Not rerun: stopped at the required Ubuntu baseline blocker |
| Real native/Proton gameplay and KDE/Gaming Mode | Pending owner testing |

The disposable acceptance driver replays S3's full install → safety callbacks →
strict foreign-content refusal → non-purge removal → pause-preserving reinstall →
purge → fresh reinstall → actual guest reboot sequence against this commit's
source inventory. Candidate identity, artifact digests, driver/payload bytes and
per-phase journals are recorded in private, gitignored
`.omo/evidence/v020/task-2/s5-candidate/`. Tests use the owned non-hashing fixture,
disabled guest networking, real systemd/IPC and file-backed sensor values. Bazzite
must remain SELinux Enforcing and an observed AVC fails acceptance.

S4's required release-smoke entrypoint passed its 11 checks and cleanup on the
same exact runtime/source digest on Bazzite, with a synthetic recognised-game
stop of 1.012 seconds. Older Ubuntu transport timeouts were unknown outcomes,
not passes. Final acceptance below must come from the selected source, not an
older development snapshot or relaxed lab security.

### Final Ubuntu attempt and stop condition

The fresh `ubuntu-noble-20260926-v1` disposable booted successfully, executed a
small Python/systemd transport check, and accepted the pinned payload transfer.
Launching the full candidate-bound driver then returned the lab error
`guest agent didn't respond to command within '5' seconds`. No guest assertion
result or final journals were returned. The guest was immediately forced off and
discarded; an unobserved run is not acceptance. No baseline, guest-agent
confinement, host controller, security policy or resource limit was altered.

The plan explicitly requires stopping when a required baseline is unavailable.
Accordingly no experimental RC is declared, no candidate-wide reboot pass is
inferred from S3, and no real-game validation is claimed. All owned VMs are
discarded. The working tree is intended for source review and deliberate testing,
not an automatic production installation or an in-place upgrade.

**Next acceptance step:** the owner/lab maintainer must restore reliable Noble
guest-agent command execution. Then replay the unchanged selected source/payload
in a fresh Noble guest and finish candidate-bound lifecycle/reboot acceptance on
both images. Preserve and inspect failed-run evidence; do not weaken containment
or replace the missing real-distro evidence with mocks. The private S5 payload
and driver are retained for this rerun; their SHA-256 identities are recorded with
the evidence. After both VM gates pass, issue the experimental RC report with
gameplay explicitly pending, then use the owner checklist.

## Repository handoff

Implementation was committed in logical groups: preservation of the owner's
status edit, retained feasibility research, the best-effort scanner/configuration
retirement, staging/lifecycle fixes, and release-surface/QA/CI alignment. This
report and the owner checklist are a separate documentation handoff commit.
Nothing was pushed, tagged or installed on the workstation. The owner's historical
`release-0.2-status.md` content was preserved without rewriting it to imply newer
acceptance. Full Git history and the private stage archives remain recoverable.

## Installation and owner testing

See the [owner checklist](testing-0.2-owner.md) for cautious staged installation,
manual stop controls, native/Proton and desktop/mode rows, timing and reporting.
There is no supported in-place upgrade/rollback/config-edit/benchmark route.
No per-game configuration or Lutris runtime dependency is introduced.

Automated success does not validate physical battery/thermal behavior, actual
upstream mining performance, real games, all launchers or zero foreground impact.
Final gameplay labels require owner results. Record recognised misses separately
from genuine scanner failures; strict-safety regressions remain release blockers.
