# Security Policy

## Supported versions

Only the latest release in the v0.x series is supported with security updates.
SpareHash v1 is the initial public release.

| Version | Supported          |
| ------- | ------------------ |
| 0.1.0   | :white_check_mark: |
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
  but public `update`, `config edit`, and `benchmark` are unsupported in v0.1.0.

- **Verified supply chain.** XMRig releases are downloaded from GitHub and
  verified against a pinned GPG fingerprint and SHA-256 checksum before any file
  is executed or symlinked into place.

- **No network exposure.** The XMRig HTTP API is bound to `127.0.0.1` only.
  SpareHash does not open firewall ports, change DNS settings, or run a public
  service.

- **No privileged optimisation.** MSR access, kernel modules, sysctl drop-ins,
  and 1GiB hugepages are not implemented in v1. The miner runs with standard
  idle scheduling and optional 2MiB hugepages.

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
