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

If publication succeeds, you see:

```
upgrade: staged and published under hold; run --complete-upgrade OPERATION_ID to commit
```

### Phase 2: Recovery (if publication failed)

If publication was interrupted or failed, recover with:

```bash
sudo ./install.sh --recover OPERATION_ID
```

Recovery refuses when the matching hold is absent, the recorded staging is gone, or the staged digest mismatches. If the installed CLI is absent, recover directly from the source tree:

```bash
cd /path/to/v0.2.0/checkout
sudo python3 -m sparehash.install --recover OPERATION_ID
```

### Phase 3: Complete the upgrade

After publication, complete the operation:

```bash
sudo sparehash install --complete-upgrade OPERATION_ID
```

Completion requires a fresh empty proof (neither service active), removes the migration journal and hold, and cleans up staging. Startup choices and manual pause are preserved.

## What never happens

- **Token rotation on read failure.** The API token is generated once and preserved. Read failures refuse, not rotate.
- **Manual `lifecycle.json` deletion.** Never delete lifecycle files to bypass holds. Use `--complete-upgrade`.
- **Calling `resume` to clear migration holds.** `resume` clears manual pause, not update holds.
- **Network update.** No remote updater exists. All upgrades are local.
- **Forced clear of unrelated holds.** Unrelated holds refuse; resolve the original operation.

## Reinstall after uninstall

```bash
sudo ./install.sh --clear-lifecycle-latch
```

This clears the retirement latch. An active update hold or unreadable lease is not cleared.

## Uninstall flow

```bash
sudo sparehash uninstall           # retain config
sudo sparehash uninstall --purge   # remove everything
```

Uninstall refuses with an active update hold. It durably latches retirement, stops services, requires empty proof, and removes only receipt-owned artifacts.

## Troubleshooting

See the error messages in `sparehash/upgrade.py` for exact refusal conditions. Common cases:

- "unrelated update hold retained": complete or recover the original operation
- "recorded staging is gone": start a new upgrade with a new operation ID
- "fresh empty proof is unavailable": stop both services before completing
- "matching update hold is absent": investigate hold deletion; do not recreate manually
