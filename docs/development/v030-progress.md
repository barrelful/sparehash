# v0.3.0 progress

- M1: [PR #4](https://github.com/barrelful/sparehash/pull/4) squash merged after
  all five CI checks passed; main `a94769b`.
  Host-safe suite: 1,270 passed / 568 subtests; mypy, focused lint, README inventory,
  wheel/sdist build passed. Real isolated GPG signature and clean-tag builder tests
  verify full commit stamping in the archive and wheel, tamper rejection and
  dirty-source/overwrite refusal. No VM lifecycle tests needed for M1.
- M2: [PR #5](https://github.com/barrelful/sparehash/pull/5) squash merged after
  all five CI checks passed; main `d1a873f2fe51f8b4b50b128fd63c54a7aa8d537e`.
  Discovery and shared authentication/staging host-safe suite:
  1,289 passed / 569 subtests; mypy, focused lint and README inventory passed.
  Real read-only GitHub check correctly refuses: existing releases lack `.asc`.
  Tests cover prereleases/integer ordering/pagination, network errors, no-follow
  snapshots, real signed offline staging, tampering, traversal/links/duplicates,
  size limits and authenticated online-version mismatch. Mutation still refuses.
- M3: [PR #6](https://github.com/barrelful/sparehash/pull/6) squash merged after
  all five CI checks passed. Application transaction/recovery implemented.
  Host-safe suite 1,289 passed / 568 subtests, mypy/focused lint/README passed.
  Private-tree tests exercise restoration, missing CLI/source, interrupted
  restoration, committed finalisation, no historical rollback, preservation,
  receipt additions/deletions and foreign-content refusal. Controller tests prove
  corrupt/missing-hold journal inhibition and generic-completion refusal.
  All seven actual scenarios passed on disposable Ubuntu and Bazzite at product
  source `a275f7ae250f3a5577bae393ddf20a14de0442fc`; evidence is in
  `.omo/evidence/v030/milestone-3/{ubuntu,bazzite}/evidence.tar.gz` (gitignored).
  Both guests were power-cycled during pending publication, then discarded.
  Bazzite stayed Enforcing with zero captured AVCs. Serial transfer initially
  timed out; stop/restart plus a smaller signed fixture bundle succeeded. Runtime
  bytes/source digest matched; compact fixtures omitted unrelated docs/tests.
- M4: legacy engine/flags/preservation cleanup, essential docs and release notes
  prepared. Fresh reinstall drops obsolete removed-module/action receipts while
  retaining matching config ownership. Recovery also handles killed atomic-write
  temporaries; offline preflight compiles modules and checks credential agreement.
  [PR #7](https://github.com/barrelful/sparehash/pull/7) is open. Host-safe suite
  1,258 passed / 567 subtests; typing, lint and docs checks pass. Initial M4 CI
  passed all five checks. Final hardening rejects sparse archive members and
  passes the operation-lock descriptor into the backup worker so launcher death
  cannot permit concurrent recovery. Artifact QA distinguishes source assets
  from package sdists. Runtime changes are frozen pending final two-distro rerun.
  An intermediate M4 Ubuntu run passed all seven scenarios plus actual fresh
  reinstall/receipt-reset/uninstall; its evidence is under milestone-4/ubuntu-pre-final.
  The real local builder passed against an isolated test-tag clone, producing
  signed complete source plus stamped wheel/sdist; public no-Git identity and
  source/licence/dependency checks passed. The project/origin has no v0.3.0 tag.
- Access: GitHub Git and authenticated local `gh` work; disposable lab reachable.
  SSH writes timed out; HTTPS works. OAuth lacks `workflow` scope, so CI workflow
  remains unchanged; new tests run in its existing full suite and touched new
  files are linted locally.
- Production prerequisite: owner must independently confirm/provide maintainer
  primary fingerprint and public key. Production trust remains empty/fail-closed;
  isolated test signing does not authorize production releases.
- No tag, published release, production signing or workstation lifecycle actions.
- Limitation: guest networking stays disabled. Real host-side GitHub discovery
  was exercised, but existing application releases lack `.asc`; no production
  signed download/final-artifact verification can pass until owner trust/assets
  are provided. No online end-to-end guest test is claimed.
- Next: commit M4, rerun seven scenarios on both final-source disposable baselines,
  verify test-key release-preparation artifacts, open M4 PR and merge passing CI.
