# Upgrading SpareHash to v0.2.0

This document describes the supported upgrade path from v0.1.1 to v0.2.0, recovery procedures when things go wrong, and the uninstall flow. Every example was exercised against the actual codebase. No procedure requires manual file deletion, blind overwrite, or network update.

## Pre-flight checklist

Before starting an upgrade, verify:

1. **You have a local v0.2.0 checkout.** The upgrade source must be a directory on the local filesystem, not a remote URL or archive. The installer never contacts a SpareHash updater.

2. **No active update hold exists.** Check `/var/lib/sparehash/lifecycle-lease.json`. If a lease with `purpose: "update"` exists, either complete or recover that operation first. An unrelated hold refuses the new upgrade.

3. **Controller and miner are stoppable.** The upgrade holds both services through publication. If either is in a state that prevents stopping (external management, manual overrides), resolve that first.

4. **Disk space is available.** The staging tree is created beside `/opt/sparehash/src` and requires space for a full copy of the source tree plus a backup of the old tree during publication.

5. **You know the operation ID.** This is an arbitrary string you choose (alphanumeric, dots, dashes, underscores, 1-128 characters). Use something descriptive like `v0.2.0-2026-10-17` or `upgrade-fix-thermal-guard`.

## Upgrading from v0.1.1

The upgrade is a three-phase process: stage and publish, optionally recover if publication fails, then complete.

### Phase 1: Stage and publish

```bash
sudo ./install.sh --upgrade OPERATION_ID
```

This command:

1. Acquires a compatible update hold in `/var/lib/sparehash/lifecycle-lease.json`. If an unrelated hold exists (different operation ID or different purpose), the upgrade refuses.

2. Writes a `prepared` journal entry to `/var/lib/sparehash/migration.json`.

3. Copies the selected local release (the directory containing `install.sh`) into a private staging area at `/opt/sparehash/.upgrade-OPERATION_ID/`. The staging directory is mode 0700, root-only.

4. Verifies the staged inventory matches the source. If the source mutates during staging, the staging is discarded and the upgrade refuses.

5. Publishes the staged source, CLI, and systemd units through a single journaled transaction. The old `/opt/sparehash/src` is renamed to `/opt/sparehash/.upgrade-OPERATION_ID.old` before the new tree is written. If publication fails (ENOSPC, I/O error, foreign leaf, digest mismatch), the old tree is rolled back from the backup.

6. Advances the journal to `published` phase.

**What is preserved:**

- `/etc/sparehash/config.toml` and `/etc/sparehash/xmrig.json` (including the API token) are untouched.
- The startup choices from the original install (boot enablement, whether the controller was active) are preserved. `--no-start` and `--no-enable` only affect fresh or reconfigure installs, not upgrades.
- Any manual pause state (`sparehash pause`) is preserved. The upgrade does not clear it.
- The XMRig binary at `/opt/xmrig/current/` is reused. The upgrade does not replace XMRig.

**What changes:**

- `/opt/sparehash/src/` now contains the v0.2.0 source.
- `/usr/local/bin/sparehash` points to the new entry point.
- systemd units are refreshed from the new source.
- The migration journal at `/var/lib/sparehash/migration.json` records the operation.
- The update hold in `/var/lib/sparehash/lifecycle-lease.json` remains active.

If publication succeeds, you see:

```
upgrade: staged and published under hold; run --complete-upgrade OPERATION_ID to commit
```

If publication fails, you see an error like:

```
sparehash: install: upgrade: publication failed (No space left on device); staging preserved
```

The hold and staging are retained. Do not delete them manually. Proceed to recovery.

### Phase 2: Recovery (if publication failed)

If publication was interrupted or failed, recover with:

```bash
sudo ./install.sh --recover OPERATION_ID
```

This command:

1. Reads the migration journal for the specified operation ID.

2. Verifies the recorded staging directory still exists and its inventory digest matches the journal. If the staging is missing or has been modified, recovery refuses.

3. Rolls the staged source forward to `/opt/sparehash/src/`. If the target already exists and matches the staged digest, the journal is advanced to `published` without re-materializing. Otherwise, the old target is renamed to a backup and the staged tree is materialized.

4. Advances the journal to `published` phase.

Recovery refuses when:

- The matching update hold is absent (someone deleted `lifecycle-lease.json`).
- The recorded staging directory is gone.
- The staged inventory digest no longer matches the journal.
- The operation is already marked `complete`.

If recovery succeeds, you see:

```
upgrade: staging for OPERATION_ID rolled forward; hold retained until --complete-upgrade
```

**Recovery when the installed CLI is absent:**

If the v0.1.1 CLI was removed or is non-functional, you can still recover using the source tree directly:

```bash
cd /path/to/v0.2.0/checkout
sudo python3 -m sparehash.install --recover OPERATION_ID
```

This bypasses the installed CLI and runs the upgrade module from the source tree. The operation ID must match the journal entry. The staging directory must still exist and be valid.

### Phase 3: Complete the upgrade

After publication (whether from `--upgrade` or `--recover`), complete the operation:

```bash
sudo sparehash install --complete-upgrade OPERATION_ID
```

This command:

1. Reads the migration journal and verifies the phase is `published`.

2. Verifies the matching update hold exists and its request ID matches the operation.

3. Verifies the published source tree at `/opt/sparehash/src/` still exists and its inventory digest matches the journal.

4. Requires a fresh empty proof: neither `sparehash.service` nor `sparehash-xmrig.service` may be active. If either is running, completion refuses.

5. Removes the migration journal at `/var/lib/sparehash/migration.json`.

6. Clears the update hold from `/var/lib/sparehash/lifecycle-lease.json`.

7. Removes the private staging directory and the old-tree backup.

Completion preserves:

- Startup choices (boot enablement, controller active state).
- Manual pause state.
- Configuration and API token.

After completion, you see:

```
upgrade: operation OPERATION_ID committed; startup choices and manual state preserved
```

The system is now running v0.2.0 with no retained hold or journal.

## What never happens

These operations are explicitly not supported and will not work:

- **Token rotation on read failure.** The API token in `/etc/sparehash/xmrig.json` is generated once at install time and preserved across upgrades. A read failure during upgrade does not rotate or regenerate the token. If the token file is missing or unreadable, the upgrade refuses.

- **Manual `lifecycle.json` deletion.** Never delete `/var/lib/sparehash/lifecycle.json` or `/var/lib/sparehash/lifecycle-lease.json` to bypass a hold. The hold is a safety barrier. Deleting it leaves the system in an inconsistent state where the journal references a hold that no longer exists. Use `--complete-upgrade` or resolve the underlying issue.

- **Calling `resume` to clear migration holds.** The `sparehash resume` command clears manual pause state, not update holds. An active update hold is independent of manual pause. Completing the upgrade clears the hold.

- **Network update.** The `update` command is unsupported in v0.1.1 and v0.2.0. There is no remote updater. All upgrades are from a local checkout.

- **Forced clear of unrelated holds.** If an unrelated hold exists (different operation ID or purpose), the upgrade refuses. Do not attempt to clear it manually. Resolve the original operation first.

- **Blind overwrite of staging.** If the staging directory exists and is not empty, the upgrade refuses. Do not delete it manually. Use `--recover` to roll it forward or resolve the underlying issue.

## Reinstall after uninstall

If you previously uninstalled SpareHash and want to reinstall, you must explicitly clear the retirement latch:

```bash
sudo ./install.sh --clear-lifecycle-latch
```

This clears the retained uninstall lease and the retirement latch from `/var/lib/sparehash/lifecycle.json`. An active update hold or an unreadable lease is not cleared; a failure leaves the system retired.

Never delete `lifecycle.json` manually to bypass retirement. Inspect and resolve any partial-uninstall failure before opting into reinstall.

## Uninstall flow

Remove SpareHash:

```bash
sudo sparehash uninstall
```

Configuration is retained by default. The uninstall flow:

1. Checks protected receipts and existing sidecar holds. An active update hold or unreadable sidecar refuses removal before retirement or service changes.

2. Durably writes the retirement latch to `/var/lib/sparehash/lifecycle.json`.

3. Stops and disables the controller (`sparehash.service`).

4. Stops the miner (`sparehash-xmrig.service`).

5. Requires fresh empty-cgroup proof: neither service may be active.

6. Restores any owned hugepage lease.

7. Removes only receipt-owned files, units, group memberships, and identities.

8. The retained latch (and any existing uninstall lease) keeps the system retired if removal partially fails.

To remove everything including configuration:

```bash
sudo sparehash uninstall --purge
```

Uninstall does not require a running controller or a successful IPC reply. It works independently of controller state.

## Troubleshooting

### "unrelated update hold retained; recovery uses its own request id"

An existing update hold with a different operation ID is present. Check `/var/lib/sparehash/lifecycle-lease.json` and `/var/lib/sparehash/migration.json`. Either complete or recover the original operation, or investigate why the hold was retained.

### "recorded staging is gone; cannot recover"

The private staging directory was deleted. This is a fatal error for recovery. The hold and journal remain, but the staged source is lost. You must start a new upgrade with a new operation ID. Do not delete the hold or journal manually; complete the operation if possible or investigate the deletion.

### "recorded staging digest mismatches; refusing recovery"

The staged source was modified after staging. This is a safety refusal. The hold and journal remain. Investigate what modified the staging directory. Do not attempt to force recovery.

### "fresh empty proof is unavailable; miner may still be running"

The controller or miner is still active. Stop both services before completing the upgrade:

```bash
sudo systemctl stop sparehash.service
sudo systemctl stop sparehash-xmrig.service
```

Then retry `--complete-upgrade`.

### "publication is not complete; run --recover or finish --upgrade"

The journal phase is not `published`. Either the upgrade is still in progress (phase `prepared` or `staged`), or it was interrupted. Run `--recover` to roll the staging forward, or re-run `--upgrade` if the staging was discarded.

### "matching update hold is absent; refusing completion"

The update hold in `/var/lib/sparehash/lifecycle-lease.json` was deleted. This is a fatal error for completion. The journal remains, but the hold is gone. Do not attempt to recreate the hold manually. Investigate why it was deleted.

## File layout reference

| Path | Purpose |
|---|---|
| `/var/lib/sparehash/lifecycle-lease.json` | Active update hold (purpose, request ID, token) |
| `/var/lib/sparehash/migration.json` | Migration journal (operation ID, phase, inventory digest) |
| `/var/lib/sparehash/lifecycle.json` | Retirement latch (set by uninstall) |
| `/opt/sparehash/.upgrade-OPERATION_ID/` | Private staging directory (mode 0700, root-only) |
| `/opt/sparehash/.upgrade-OPERATION_ID.old/` | Old-tree backup during publication |
| `/opt/sparehash/src/` | Installed SpareHash source |
| `/usr/local/bin/sparehash` | CLI entry point (symlink to source) |
| `/etc/sparehash/config.toml` | Public configuration (preserved across upgrades) |
| `/etc/sparehash/xmrig.json` | XMRig configuration with API token (preserved across upgrades) |

## Further reading

- `README.md`: Quick start, CLI reference, safety model.
- `SECURITY.md`: Threat model, verification, supported versions.
- `docs/architecture.md`: Design invariants, component boundaries.
