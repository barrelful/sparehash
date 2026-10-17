# Task 34 Boundary Audit

## Scope verification

Task 34 documents native ownership boundaries without implementing native channels.

### What was done

1. Created `docs/packaging-roadmap.md` documenting:
   - Runtime/deployer separation diagram
   - Future RPM package ownership (vendor units, sysusers, tmpfiles, presets)
   - Future Nix store + module (immutable paths, declarative config, activation rollback)
   - Proxmox-specific safeguards (VMs, cache/bandwidth, memory, hugepages, backup/storage, thermal/power)
   - Optional GUI-only Flatpak (unprivileged frontend, separate host service, narrow IPC)
   - "What v0.2.0 does NOT do" section (no RPM/Nix/Flatpak implemented, no spec, no new privileged API, no generic adapter framework)

2. Updated `docs/architecture.md` with:
   - New "Runtime and deployer separation" section
   - Description of external-provision capability
   - Fixed paths and contracts for future native packaging
   - Reference to `docs/packaging-roadmap.md`

3. Created `tests/test_runtime_deployment.py` with:
   - `test_accepts_noninteractive_provisioning`: verifies runtime doesn't import wizard/install modules
   - `test_refuses_hostile_paths_or_deployment_drift`: verifies runtime strips test environment variables and uses fixed paths

### What was NOT done (per scope)

- No RPM spec file created
- No Nix flake or module created
- No Flatpak manifest created
- No new privileged API added
- No generic adapter framework implemented
- No install.sh replacement implemented

### Import/effect trace

Runtime imports verified:
- `sparehash.runtime` does not import `sparehash.wizard`
- `sparehash.runtime` does not import `sparehash.install`
- `sparehash.runtime` does not import `sparehash.install_xmrig`
- `sparehash.runtime` uses shared validation interfaces from `deployment.py` and `install_manifest.py` for read-only integrity checks

Environment variable handling verified:
- `runtime.run()` strips `SPAREHASH_RUN_DIR`, `SPAREHASH_HWMON_ROOT`, `SPAREHASH_STATE_FILE` before config access
- `config.load()` uses fixed paths, not environment overrides

### Architectural compatibility vs. built/tested support

The runtime's deployer-agnostic design enables future native packaging, but architectural compatibility is not the same as built, tested, and supported packages. Each future channel requires separate implementation, testing, and maintenance.

## Test results

- `test_accepts_noninteractive_provisioning`: PASSED
- `test_refuses_hostile_paths_or_deployment_drift`: PASSED
- Full regression: 782 passed, 3 failed (pre-existing failures in test_gaming_coverage.py, unrelated to Task 34)
- mypy: Success, no issues found in 48 source files
- check_readme.py: PASSED

## Conclusion

Task 34 successfully documents the native ownership boundaries without implementing native channels. The runtime is architecturally compatible with future RPM/Nix/Flatpak packaging, but no package spec, flake, module, or manifest is implemented. The boundary between runtime and deployer is clear and tested.
