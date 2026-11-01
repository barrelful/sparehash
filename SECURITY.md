# Security Policy

## Supported versions

Only the latest release in the v0.x series is supported with security updates.
v0.2.1 is Experimental/Beta, not production-stable. Report vulnerabilities in
this release privately; experimental status is not a security guarantee.

| Version | Supported          |
| ------- | ------------------ |
| 0.2.1 (Experimental) | :white_check_mark: |
| 0.2.0   | :x:                |
| 0.1.1   | :x:                |
| 0.1.0   | :x:                |
| < 0.1.0 | :x:                |

## Reporting a vulnerability

Please report security issues privately to the repository maintainers via
GitHub Security Advisories. Do not open a public issue for a security bug.

When reporting, include:

- The SpareHash version or commit hash.
- Your Linux distribution and systemd version.
- A clear description of the vulnerability and its impact.
- Steps to reproduce, if possible.
- Whether the issue requires local access, root access, or can be triggered
  remotely.

Please do not include your wallet address, pool password, or API token in the
report.

## Threat model and design

SpareHash is designed around these security properties:

- **Root controller, unprivileged miner.** The controller (`sparehash.service`)
  runs as root so it can read system power/thermal state, manage cgroups, and
  authorize miner starts. The miner (`sparehash-xmrig.service`) always runs as
  the dedicated `xmrig` user and never as root.

- **Controller is the sole mining-start authority.** Every miner start is authorized through
  an `ExecCondition` that asks the running controller for a fresh,
  generation-scoped permission. A direct `systemctl start sparehash-xmrig.service`
  succeeds only when the controller would have started mining anyway.

- **Fail-closed.** If the controller crashes, is killed, or cannot determine a
  safety state, mining stops. Unknown battery state or stale observations inhibit
  mining. Thermal protection defaults off; when enabled, missing or unreadable
  credible sensors inhibit mining.

- **Controller-independent removal.** Root uninstall checks protected receipts
  and existing sidecar holds, durably retires, stops/disables services, proves the
  miner cgroup empty, and removes only receipt-owned artifacts. It does not need
  controller IPC or create a lease. Config is retained by default; retirement
  remains after partial failure. The durable sidecar still gates internal updates,
  but public updates/rollback, config editing, benchmark and in-place
  reconfiguration are deferred in experimental v0.2.

- **Preserved internal migration protections, not a public upgrade route.**
  Historical update holds and journals remain independent safety records. Public
  `update`, `complete-upgrade`, `config edit`, `benchmark` and installer
  `--upgrade`, `--recover`, `--complete-upgrade`, `--reconfigure` refuse before
  effects. Do not delete journals/staging or clear update holds to bypass this;
  seek maintainer assistance for partial historical migrations.

- **Verified supply chain.** XMRig releases are downloaded from GitHub and
  verified against a pinned GPG fingerprint and SHA-256 checksum before any file
  is executed or symlinked into place.
  Explicit `--use-existing` is different: a root-protected external binary is
  safely snapshotted and probed unprivileged, not authenticated as an upstream
  signed release. The operator is responsible for that artifact's provenance.

- **No network exposure.** The XMRig HTTP API is bound to `127.0.0.1` only.
  SpareHash does not open firewall ports, change DNS settings, or run a public
  service.

- **No privileged optimisation.** MSR access, kernel modules, sysctl drop-ins,
  and 1GiB hugepages are not implemented in v0.2. The miner runs with standard
  idle scheduling and optional 2MiB hugepages.

- **Gaming detection is best effort.** The Lutris-derived Steam reaper scanner
  can miss non-Steam/unrecognised games, changed markers or surviving children.
  A successful no-match scan may release gaming inhibition; it is not proof of
  game absence. Genuine failures and stale scans inhibit. Low priority does not
  guarantee noninterference during a missed game. No raw argv/environment data
  is published, no user logs/homes are read for detection, and no GameMode or
  Lutris installation is required. Real gameplay/mode validation remains pending.
  See `docs/gaming-integration-contract.md` for migration and exact limitations.

## What we consider in scope

- Privilege escalation from the `xmrig` user to root.
- Bypass of the controller authorization (`ExecCondition` bypass).
- Failure modes that leave XMRig running when it should be stopped.
- Unsafe subprocess, archive extraction, or file-permission handling.
- Tampering with the XMRig download/verification chain.
- Control-socket authorization flaws.
- Path traversal or symlink attacks against SpareHash-managed paths.

## What is not considered a vulnerability

- A user with root access can disable SpareHash entirely. This is expected;
  root is trusted.
- A user who can edit `/etc/sparehash/config.toml` (root or sparehash group)
  can change the pool or wallet. File permissions deliberately restrict this.
- A gaming user in the `sparehash` group can issue `gaming-start` and block
  mining. Gaming users are trusted by design.
- Mining profitability, electricity cost, or pool-side security are outside the
  scope of SpareHash.

## Verification

You can verify the XMRig verification chain independently:

```bash
python3 -m sparehash.xmrig --selfcheck
python3 -m sparehash.xmrig_release --selfcheck
```

These commands fetch upstream metadata only; they do not install or execute any
binary.
