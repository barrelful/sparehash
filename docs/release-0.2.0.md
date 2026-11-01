# SpareHash v0.2.0 (Experimental)

v0.2.0 is **Experimental/Beta**, not RC1 or production-stable. Use the annotated
`v0.2.0` tag and its GitHub pre-release, not an old review branch or candidate
artifact. The published release records the exact commit and artifact checksums.
No production functionality was changed for release documentation housekeeping.

## Scope

- CPU-only Monero RandomX mining on Linux x86_64 with an unprivileged `xmrig`
  service. The controller runs as root and is the sole miner-start authority.
- Independent controller-managed battery, thermal, manual and gaming blockers;
  fresh authorization is required to start mining. Controller loss stops the
  bound miner. Unknown/stale safety observations inhibit where applicable.
- One attributed Lutris-derived, bounded **best-effort Steam reaper scanner**,
  including numeric Steam App IDs. No Lutris/GameMode dependency, Steam log reader
  or per-game setup is required. Unrecognised/non-Steam games, changed markers
  and games outliving markers can be missed. Pause manually when in doubt.
- Low-priority CPU execution: nice 19, SCHED_IDLE and conditional cgroup idle
  settings. These reduce scheduling priority, not memory/cache contention or all
  foreground impact. The owner accepted the completed physical CPU comparison;
  it is not a universal performance guarantee.
- Conservative battery/AC detection and optional thermal hysteresis. Thermal
  protection defaults **off**; enable only after checking real CPU sensors and
  suitable thresholds. Unreadable credible sensors inhibit when enabled.
- Optional, default-off 2MiB hugepage allocation with recorded baseline and
  empty-cgroup restoration. Automatic allocation requires a single memory-bearing
  NUMA node. Kernel refusal can leave a degraded warning; no 1GiB pages.
- Receipt-authoritative installation/removal and explicit retirement-bound
  reinstall. Removal preflights ownership and retains source through most late
  cleanup failures; interruption during final self-source deletion can still
  require maintainer assistance. Preserve evidence on any failure.

## Validation and platforms

Corrected implementation passed **22 acceptance checks on each** disposable
Ubuntu 24.04 and Bazzite 44 baseline: actual reboot/autostart, safety callbacks,
controller loss, hugepage allocation/restoration, non-purge removal, pause-preserving
reinstall and purge. Bazzite remained SELinux Enforcing. These runs used synthetic,
non-hashing artifacts and file-backed sensors; they are not real-game/hardware
or pool-connectivity certification. Exact historical source pins and failures
are retained in the [acceptance record](release-0.2-candidate.md).

Python 3.11–3.14 is targeted; runtime uses only the standard library. Linux
x86_64 and systemd PID 1 are required. Ubuntu 24.04 and Bazzite 44 support labels
are limited to the tested release lines/images, not every derivative or machine.
**Mint 22.3, Debian, other Ubuntu/Fedora/Atomic releases, Deck-specific hardware,
Arch and openSUSE remain UNVERIFIED.** NixOS and Proxmox are unsupported and
refused. See the [platform matrix](platforms.md).

Historical owner observations of Portal 2 native and Cyberpunk 2077 Proton in
Bazzite Gaming Mode do not establish gameplay acceptance of the final tagged
installation. Record new physical results separately, including untested desktop
and Gaming Mode rows. Never substitute VM fixtures for real sessions.

## Installation and removal

Use a new checkout; retain any existing work and configuration privately:

```sh
git clone --branch v0.2.0 --depth 1 https://github.com/barrelful/sparehash.git
cd sparehash
git rev-parse HEAD
sudo ./install.sh --preflight
sudo ./install.sh --no-start --no-enable
```

Enter wallet/pool details only in the local interactive installer, not in issue
reports or chat. Verify files, ownership, configuration, sensors and unit hardening
before a supervised `sudo systemctl start sparehash.service`. Inspect
`sudo sparehash status --json`. Do not enable at boot until physical checks pass;
`sparehash start` enables boot startup as well as starting now.

**No supported in-place upgrade or rollback exists.** For an existing installation,
first preserve a protected, verified backup of configuration, manual state,
receipts, source identity and lifecycle records. Review older uninstall code before
running it, especially after partial removal. Normal non-purge removal is:

```sh
sudo sparehash uninstall
```

Only after completed removal, use the tagged installer with explicit reinstall
consent and the operation ID **if required by its printed guidance**:

```sh
sudo ./install.sh --clear-lifecycle-latch --no-start --no-enable
# Add --operation-id ID only when the retirement guidance requires it.
```

Retain existing wallet/pool and manual pause privately; verify preservation before
resuming. `--purge` removes additional unchanged receipt-owned configuration, not
all state/foreign files. Never delete `/opt`, config, receipts, lifecycle holds or
journals to force reinstall. Stop and retain evidence after a refusal/failure.
See the [owner checklist](testing-0.2-owner.md).

## Limitations and security

- `update`, `complete-upgrade`, `config edit`, `benchmark` and corresponding
  deferred installer modes refuse without effects. No native RPM/DEB/Flatpak/Nix
  distribution channel or automatic updater is delivered.
- XMRig downloads are checked using a pinned GPG fingerprint and SHA-256.
  Explicit external `--use-existing` artifacts are protected/probed, not thereby
  authenticated as upstream signed binaries; provenance remains the operator's
  responsibility. SpareHash package artifacts do not include XMRig.
- No GPU mining, MSR access/helper, kernel module, firewall/DNS/SELinux policy
  modification or security-mechanism bypass. Keep SELinux/AppArmor enabled.
- The API binds to localhost and uses a private token. Trusted control-socket
  group members can pause/resume and send gaming hooks. Root is trusted.
- Low priority, best-effort game recognition and optional thermal protection are
  not guarantees of no contention, no heat or profitable mining. Mining can cost
  more in electricity than it earns.

Report vulnerabilities privately as described in [SECURITY.md](../SECURITY.md).
Do not upload wallets, API tokens, private config, raw process arguments or host
backups. A published tag is immutable; fixes require a new v0.2.1 release.
