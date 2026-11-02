# v0.3.0 progress

- M1: version/release identity ready for PR on `atlas/v030-m1-release-identity`.
  Host-safe suite: 1,270 passed / 568 subtests; mypy, focused lint, README inventory,
  wheel/sdist build passed. Real isolated GPG signature and clean-tag builder tests
  verify full commit stamping in the archive and wheel, tamper rejection and
  dirty-source/overwrite refusal. No VM lifecycle tests needed for M1.
- M2–M4: pending, in dependency order; no updater acceptance claimed.
- Access: GitHub Git and authenticated local `gh` work; disposable lab reachable.
  SSH writes timed out; HTTPS works. OAuth lacks `workflow` scope, so CI workflow
  remains unchanged; new tests run in its existing full suite and touched new
  files are linted locally.
- Production prerequisite: owner must independently confirm/provide maintainer
  primary fingerprint and public key. Production trust remains empty/fail-closed;
  isolated test signing does not authorize production releases.
- No tag, published release, production signing or workstation lifecycle actions.
- Next: open M1 PR, check CI, squash merge only if passing; then M2 discovery.
