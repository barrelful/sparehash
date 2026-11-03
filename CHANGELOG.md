# Changelog

All notable changes to SpareHash will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### v0.3.0 experimental release preparation

- Rootless/offline version and full source-commit identity; one dynamic version
  source and a clean exact-tag local archive build/sign/verify command.
- Numeric release discovery including GitHub pre-releases, bounded downloads,
  detached GPG verification and safe extraction shared by online/offline updates.
- App-only update transaction, one protected backup/journal, automatic restoration
  on ordinary failure, and interrupted recovery through the known-good source CLI.
  Preserves config/credentials/manual pause/startup/XMRig; no post-success rollback.
- Journal-based mining inhibition even with a missing lifecycle hold, protected
  receipt refresh and removal of validated owned updater leftovers.
- Superseded migration/completion engine and installer upgrade flags removed.
- The owner-confirmed existing Nitrokey public certificate/primary pin is bundled
  separately from XMRig. Exact-commit pre-tag builds require that signer and verify
  against installed trust; no private key or test bypass is shipped. Final
  signed-artifact verification remains required before owner-approved publication.

## [0.2.1] - 2026-11-01

**Experimental/Beta, not production-stable.** See [release notes](docs/release-0.2.1.md).

### Fixed

- Add explicit `--acknowledge-unverified` to preflight and installation; eligible
  Mint/other UNVERIFIED hosts can reach READY without changing their support tier.
  Default refusal, unsupported/unsafe/inconclusive precedence, receipt checks and
  separate installation-effects consent remain. Fixes issue #3. v0.2.0 is immutable.

### Validation

- Physical Mint 22.3 installation, actual mining, idle scheduling, manual-pause
  persistence, battery inhibition and partial hugepage allocation/restoration passed.
  Mint remains UNVERIFIED; real Mint gameplay and thermal fault injection are untested.
- Bazzite 44 synthetic VM smoke passed all 11 checks with SELinux Enforcing.
  The v0.2.1 Ubuntu VM attempt was inconclusive after guest-agent disconnection,
  not a pass or a diagnosed product failure. Prior v0.2.0 reboot evidence remains
  historical, not a newly executed v0.2.1 reboot test.

## [0.2.0] - 2026-11-01

**Experimental/Beta, not production-stable.** See [release notes](docs/release-0.2.0.md).

### Changed

- Single attributed Lutris-derived best-effort Steam reaper scanner, bounded
  polling/freshness and independent controller protections; log/completeness
  experiments retired after verified preservation.
- Source install/removal/purge/reinstall/reboot passed synthetic disposable Ubuntu
  24.04 and Bazzite 44 lifecycle checks; staging residue fixed without relaxing
  foreign-content/receipt checks. Final-release gameplay and physical hardware
  validation remain separate from VM evidence.
- Upgrades/rollback, native packaging, benchmark and in-place reconfiguration
  deferred; public unsupported paths refuse before effects.
- Release QA uses explicit VM consent, source SHA-256 and an attributed non-hashing
  fixture. Python 3.11–3.14 quality gates and shipped licence/notices are required.
- Improved idle-priority scheduling and protected runtime layout; controller
  remains the sole miner-start authority. The owner accepted the physical CPU
  comparison without a zero-contention claim.
- Removal now preflights receipts and identities, checkpoints cleanup and retains
  source/launcher through unit, account and daemon-reload failures. A crash during
  final self-source removal can still require maintainer assistance.

### Historical pre-revision-3 branch notes — 2026-10-25 (superseded)

### Fixed

- Explicit conservative/gamemode integration counts as acknowledged reduced coverage (`ec61d84`).
- Dry preflight accepts `--answers` (`857e8cd`).
- Identity resolution reads `/etc`, `/usr/lib`, `/usr/etc` in NSS order (`348ab09`).
- Attestation uses the resolved probe uid instead of hardcoded 65534 (`cfd8c62`).
- Probe staging relocation, mechanism unproven (`36f400d`, `ce04d6b`).
- Gaming producer (`287718f`).
- QA observer (`df08762`).

### Known limitations

- Lifecycle verification is incomplete: uninstall is blocked by unrecorded content
  under `/opt/sparehash` from the probe staging snapshot (see
  `docs/release-0.2-status.md`).

## [0.1.1] - 2026-10-10

### Fixed

- Installation now asks separately about boot startup and starting the controller
  immediately (both default Yes; unattended `--answers` installs use both defaults),
  verifies the selected states, and summarizes activity and mining blockers.
  The miner remains static and is never enabled or started by the installer.
- Added opt-in `[power] maintenance_mode = true` for AC-connected firmware
  maintenance discharge of at most 1W when UPower confirms AC operation.
  Conservative defaults and fail-closed unknown/stale evidence are preserved;
  status includes AC online state and battery discharge rate.
- GameMode setup now asks for explicit consent before installing the package,
  verifies binaries and user session availability, warns when group membership
  requires a re-login, and disables gaming protection (with a clear warning)
  when setup cannot complete or when using a noninteractive `--answers` install.
- `sparehash status` now requires root; non-root calls show an actionable
  message and exit nonzero. IPC failures are distinguished from a genuinely
  stopped controller (`ipc-error` vs `unavailable`).
- Enabled thermal protection now fails closed when no credible sensor exists.
- Root uninstall works without controller IPC, retiring before service stops and
  requiring empty-cgroup proof before receipt-owned removal.

### Changed

- Deferred public `config edit`, `update`, and `benchmark` in experimental
  v0.1.1; they exit 2 before workflow effects.

### Added

- Eight-checkpoint disposable-VM release acceptance smoke using synthetic local
  hashing only, with an opt-in safety guard and candidate-bound evidence.

## [0.1.0] - 2026-10-09

### Added

- Initial public release of SpareHash.
- CPU-only Monero (RandomX) mining manager for Linux x86_64.
- systemd-based controller (`sparehash.service`) and unprivileged miner
  (`sparehash-xmrig.service`).
- Blocker-based safety system: battery, gaming, manual, and thermal blockers.
- Battery guard using UPower and `/sys/class/power_supply` with conservative
  fail-closed logic.
- GameMode integration via `gamemoded -s` and user hooks.
- Optional thermal guard with hysteresis and credible-sensor discovery.
- Optional dynamic 2MiB hugepage reservation with lease-based restore.
- Pinned GPG fingerprint and SHA-256 verification for XMRig releases.
- Localhost-only XMRig HTTP API.
- CLI commands: install, uninstall, start, stop, pause, resume, status, logs,
  config show, gaming-start, gaming-end, debug. Config edit, benchmark, and update
  are deferred for this minimal release.
- Interactive setup wizard and unattended answers-file install.
- Receipt-authoritative install/uninstall ledger.
- Lifecycle latch to prevent accidental mining after uninstall.
- Unit tests and destructive VM integration QA suites.
- Public documentation: README, SECURITY.md, CONTRIBUTING.md,
  docs/architecture.md, CHANGELOG.md.

### Security

- Controller is the sole authority for every miner start via `ExecCondition`.
- Miner runs as dedicated `xmrig` user; root execution is rejected.
- Controller loss stops mining through `BindsTo=` dependency.
- No MSR, kernel module, firewall, DNS, or bootloader changes in v1.

### Notes

- This is an experimental first release. See `README.md` for v1 limitations.

[Unreleased]: https://github.com/barrelful/sparehash/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/barrelful/sparehash/releases/tag/v0.2.1
[0.2.0]: https://github.com/barrelful/sparehash/releases/tag/v0.2.0
[0.1.1]: https://github.com/barrelful/sparehash/releases/tag/v0.1.1
[0.1.0]: https://github.com/barrelful/sparehash/releases/tag/v0.1.0
