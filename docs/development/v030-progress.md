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
- M3: application transaction/recovery implemented on `atlas/v030-m3-update-recovery`.
  Host-safe suite 1,289 passed / 568 subtests, mypy/focused lint/README passed.
  Private-tree tests exercise restoration, missing CLI/source, interrupted
  restoration, committed finalisation, no historical rollback, preservation,
  receipt additions/deletions and foreign-content refusal. Controller tests prove
  corrupt/missing-hold journal inhibition and generic-completion refusal.
  Focused disposable-VM driver added; real-distro execution pending.
- M4: cleanup/release documentation and final VM/CI acceptance pending.
- Access: GitHub Git and authenticated local `gh` work; disposable lab reachable.
  SSH writes timed out; HTTPS works. OAuth lacks `workflow` scope, so CI workflow
  remains unchanged; new tests run in its existing full suite and touched new
  files are linted locally.
- Production prerequisite: owner must independently confirm/provide maintainer
  primary fingerprint and public key. Production trust remains empty/fail-closed;
  isolated test signing does not authorize production releases.
- No tag, published release, production signing or workstation lifecycle actions.
- Next: commit M3, build isolated signed test-successor fixtures and run all seven
  scenarios on disposable Ubuntu/Bazzite (power-cycle between driver phases).
