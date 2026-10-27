# SpareHash Packaging Roadmap

> **Deferred research, not v0.2 distribution channels.** RPM/DEB/Nix/Flatpak and
> other native deployment proposals below are not implemented or advertised as
> supported installation routes. No new packaging channel is approved by this
> document. Experimental v0.2 uses the source installer; see `docs/platforms.md`.

This document describes the architectural boundaries between SpareHash runtime and future native packaging channels. It specifies ownership separation without implementing any package spec, flake, module, or Flatpak manifest.

## Runtime and deployer separation

SpareHash v0.2.0 runtime is deployer-agnostic. The controller reads protected deployment descriptions and configuration from fixed paths, but does not claim ownership of package manager files or invoke imperative installers.

```
┌─────────────────────────────────────────────────────────────┐
│                    Deployer (future)                         │
│  RPM spec / Nix derivation / Flatpak manifest / install.sh  │
└─────────────────────────────────────────────────────────────┘
                            │
                            │ provisions
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    Runtime (v0.2.0)                          │
│                                                              │
│  Reads:                                                      │
│    - /etc/sparehash/config.toml (public configuration)      │
│    - /etc/sparehash/xmrig.json (generated, mode 0640)       │
│    - /etc/sparehash/deployment.json (schema 1, owner info)  │
│    - /opt/xmrig/current (managed binary symlink)            │
│                                                              │
│  Manages:                                                    │
│    - /var/lib/sparehash/ (runtime state, leases, manifests) │
│    - /run/sparehash/ (IPC socket, operation spec)           │
│    - systemd unit lifecycle (start/stop/authorize)          │
│                                                              │
│  Does NOT:                                                   │
│    - Rewrite package manager files                          │
│    - Chown package-owned directories                        │
│    - Invoke install.sh or wizard                            │
│    - Claim ownership of /usr, /opt, /etc package content    │
└─────────────────────────────────────────────────────────────┘
```

The runtime strips test environment variables (`SPAREHASH_RUN_DIR`, `SPAREHASH_HWMON_ROOT`, `SPAREHASH_STATE_FILE`) before accessing configuration. Protected paths come from fixed locations or explicit deployment descriptions, not environment overrides.

## Future RPM package (Fedora/COPR)

**Status:** Not implemented in v0.2.0. Architectural compatibility only.

### Package ownership

An RPM package would own:

- `/usr/lib/python3.X/site-packages/sparehash/` (stdlib runtime)
- `/usr/lib/systemd/system/sparehash.service` (vendor unit)
- `/usr/lib/systemd/system/sparehash-xmrig.service` (vendor unit)
- `/usr/lib/sysusers.d/sparehash.conf` (identity creation)
- `/usr/lib/tmpfiles.d/sparehash.conf` (runtime directory creation)
- `/usr/lib/systemd/system-preset/50-sparehash.preset` (default enablement)

The package manager owns code installation and removal, not installer receipts. Uninstall via `dnf remove` removes package-owned files; it does not invoke SpareHash uninstall logic.

### Configuration and state

- `/etc/sparehash/` remains configuration directory (mode 0755, root:root)
- `/var/lib/sparehash/` remains runtime state (mode 0750, root:sparehash)
- `/opt/xmrig/` remains managed XMRig tree (separate package or manual install)

The RPM package does not own `/opt/xmrig/` or XMRig binaries. XMRig licensing, source availability, and reproducibility must be investigated before Fedora packaging.

### Identity and directory creation

`sysusers.d` creates the `sparehash` and `xmrig` system accounts. `tmpfiles.d` creates `/var/lib/sparehash` and `/run/sparehash` with correct ownership. The package does not invoke `useradd` or `mkdir` in post-install scripts.

### Atomic desktop considerations

On Fedora Silverblue/Kinoite/Bazzite, RPM layering via `rpm-ostree install` is possible but not automatically preferred over custom image delivery. Layering persists across rebases but increases image size and may conflict with base image updates. Honest evaluation required; do not present COPR layering as the preferred Atomic UX.

## Future NixOS module

**Status:** Not implemented in v0.2.0. Architectural compatibility only.

### Store paths and immutability

A Nix derivation would place SpareHash runtime in the immutable Nix store:

- `/nix/store/<hash>-sparehash-0.2.0/lib/python3.X/site-packages/sparehash/`
- `/nix/store/<hash>-sparehash-0.2.0/bin/sparehash`

The store path is content-addressed and immutable. Activation creates symlinks in `/run/current-system/sw/bin/` and `/etc/systemd/system/`.

### Declarative configuration

A NixOS module would declare:

```nix
services.sparehash = {
  enable = true;
  wallet = "4...";
  pool.host = "pool.example.com";
  pool.port = 3333;
  thermal.enabled = true;
  thermal.pauseC = 85;
  gaming.enable = true;
  gaming.users = [ "player" ];
};
```

The module generates `/etc/sparehash/config.toml` and `/etc/sparehash/xmrig.json` from declarative configuration. Secrets (API token, wallet) are injected from protected runtime paths, not world-readable store paths.

### User and group creation

The module declares users and groups declaratively:

```nix
users.users.sparehash = {
  isSystemUser = true;
  group = "sparehash";
};
users.users.xmrig = {
  isSystemUser = true;
  group = "xmrig";
};
users.groups.sparehash = {};
users.groups.xmrig = {};
```

No `useradd` or `groupadd` invocation. Activation creates identities declaratively.

### Activation and rollback

NixOS activation creates systemd units, starts services, and manages state. Rollback via `nixos-revert` restores the previous generation, including SpareHash configuration and service state. The module does not invoke `install.sh` during activation.

### XMRig packaging

XMRig would be a separate Nix derivation. The SpareHash module references the XMRig store path in configuration. XMRig licensing and source availability must be verified before nixpkgs submission.

## Proxmox VE safeguards

**Status:** Explicitly unsupported in v0.2.0. Future policies require host-specific acceptance.

### Host detection

SpareHash detects Proxmox VE hosts via `pve-manager` package presence or `pveversion`/`pvesh` commands. Detection is read-only; no remote API queries or network access required.

### Why unsupported

Proxmox hosts run virtual machines and containers. Mining on the host competes with guest workloads for CPU, memory, storage I/O, and network bandwidth. The safety model assumes a single-workstation context, not a multi-tenant hypervisor.

### Future safeguards (not implemented)

If Proxmox support is considered, it must address:

- **VM/container contention:** Mining must not starve guest workloads. Idle scheduling (`Nice=19`, `SCHED_IDLE`) may be insufficient for I/O-bound guests.
- **Cache and bandwidth:** Mining increases memory bandwidth and cache pressure. Guests may experience latency spikes.
- **Memory and hugepages:** Host hugepage reservation reduces memory available to guests. Multi-NUMA systems require explicit tuning.
- **Backup and storage:** Mining I/O may interfere with backup jobs or snapshot operations.
- **Thermal and power:** Hypervisor hosts often run 24/7 in enclosed spaces. Thermal guards must account for ambient conditions and rack density.
- **Host-specific acceptance:** Debian ancestry is insufficient. Each Proxmox deployment requires explicit contention acceptance from the operator.

### Current behavior

SpareHash refuses to install on detected Proxmox hosts. The imperative installer exits with an error message explaining the unsupported status. Future policies must require host-specific contention acceptance before any support claim.

## Optional Flatpak GUI

**Status:** Not implemented in v0.2.0. No GUI, no Flatpak, no new daemon API.

### Architecture (future)

A future GUI would be an unprivileged Flatpak frontend communicating with a separate host daemon:

```
┌──────────────────────────────────┐
│  Flatpak (unprivileged)          │
│    - GUI frontend                │
│    - No root access              │
│    - No systemd unit management  │
└──────────────────────────────────┘
            │
            │ narrow authenticated IPC
            ▼
┌──────────────────────────────────┐
│  Host daemon (root)              │
│    - sparehash.service           │
│    - Existing controller         │
│    - Deliberate authorization    │
└──────────────────────────────────┘
```

### Authorization review

The host daemon would require explicit authorization for each GUI request. No new privileged API is added in v0.2.0. Any future IPC endpoint must undergo deliberate security review.

### What v0.2.0 does NOT do

- No GUI
- No Flatpak manifest
- No new daemon API for GUI communication
- No Flathub submission
- No privileged Flatpak daemon

## What v0.2.0 does NOT do

This section clarifies the boundary between architectural compatibility and implemented support.

### No native packages implemented

- **No RPM spec file.** Fedora/COPR packaging is not implemented. The runtime is architecturally compatible with future RPM packaging, but no spec exists.
- **No Nix flake or module.** NixOS packaging is not implemented. The runtime is architecturally compatible with future Nix derivations, but no flake or module exists.
- **No Flatpak manifest.** GUI packaging is not implemented. No Flatpak manifest exists.

### No new privileged API

The runtime does not add new IPC verbs, socket endpoints, or privileged helpers for native packaging. Future packaging channels would use the existing controller IPC with deliberate authorization review.

### No generic adapter framework

The runtime does not implement a plugin system, packaging adapter interface, or deployment abstraction layer. Each future packaging channel (RPM, Nix, Flatpak) would integrate directly with the runtime's fixed paths and contracts.

### No install.sh replacement

The imperative `install.sh` remains the supported installation method for v0.2.0. Future native packages would bypass `install.sh` entirely, provisioning files and identities through their own mechanisms.

### Architectural compatibility vs. built/tested support

The runtime's deployer-agnostic design enables future native packaging, but architectural compatibility is not the same as built, tested, and supported packages. Each future channel requires:

- Package spec/derivation/manifest implementation
- Platform-specific testing (Fedora CI, NixOS VM, Flatpak build)
- XMRig licensing and source verification
- Distribution policy compliance (Fedora packaging guidelines, nixpkgs maintainer review, Flathub review)
- Ongoing maintenance and updates

v0.2.0 provides the runtime foundation. Native packages are deferred to future work.

## Reading list

- `sparehash/runtime.py` — controller assembly without installer mutation
- `sparehash/runtime_spec.py` — protected operation consumer
- `sparehash/deployment.py` — root-controlled deployment I/O
- `sparehash/config.py` — fixed-path configuration loading
- `tests/test_runtime_deployment.py` — external provision contract tests
- `docs/architecture.md` — design invariants and component overview
- Plan section I — deferred roadmap and architectural compatibility
