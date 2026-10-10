# Changelog

All notable changes to SpareHash will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
  v0.1.0; they exit 2 before workflow effects.

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

[Unreleased]: https://github.com/barrelful/sparehash/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/barrelful/sparehash/releases/tag/v0.1.0
