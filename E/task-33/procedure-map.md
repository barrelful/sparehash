# Task 33: Procedure Map

This document maps the documented upgrade/recovery procedures to their implementation in the codebase.

## Documented Procedures

### 1. Pre-flight checklist

**Documented in:** `docs/upgrade-v020.md` → "Pre-flight checklist"

**Implementation:**
- Local checkout requirement: `sparehash/upgrade.py:158-159` (source must be a directory, not a symlink)
- Hold check: `sparehash/upgrade.py:118-133` (`acquire_compatible_hold` refuses unrelated holds)
- Operation ID validation: `sparehash/upgrade.py:103-107` (regex `[A-Za-z0-9._-]{1,128}`)

### 2. Stage and publish (`--upgrade OPERATION_ID`)

**Documented in:** `docs/upgrade-v020.md` → "Phase 1: Stage and publish"

**Implementation:**
- Entry point: `sparehash/install.py:340-342` (argument parser)
- Dispatch: `sparehash/install.py:371-378` (calls `upgrade_install`)
- Hold acquisition: `sparehash/upgrade.py:118-133` (`acquire_compatible_hold`)
- Journal init: `sparehash/upgrade.py:136-141` (`begin` writes `prepared` phase)
- Staging: `sparehash/upgrade.py:150-183` (`stage_source` copies to private staging, verifies digest)
- Pre-publication verify: `sparehash/upgrade.py:186-210` (`verify_pre_publication` checks inventory and runtime paths)
- Publication: `sparehash/upgrade.py:213-255` (`publish` renames old tree, materializes new, rolls back on failure)
- Journal advance: `sparehash/upgrade.py:247-250` (writes `published` phase)

**Preserved state:**
- Config/token: `sparehash/upgrade.py:48-54` (`RUNTIME_PATHS` does not include config files)
- Startup choices: `sparehash/install.py:213-220` (`_upgrade_context` sets `preserve_startup_choices=True`)
- Manual pause: Not touched by upgrade flow (independent of lifecycle hold)

### 3. Recovery (`--recover OPERATION_ID`)

**Documented in:** `docs/upgrade-v020.md` → "Phase 2: Recovery"

**Implementation:**
- Entry point: `sparehash/install.py:343-345` (argument parser)
- Dispatch: `sparehash/install.py:371-378` (calls `recover_install`)
- Hold check: `sparehash/install.py:237-240` (refuses if hold is absent or mismatched)
- Journal read: `sparehash/upgrade.py:328-357` (`read_journal` validates operation ID and phase)
- Staging verify: `sparehash/upgrade.py:266-271` (checks staging exists and digest matches)
- Roll-forward: `sparehash/upgrade.py:272-291` (materializes staging to target, handles backup)
- Journal advance: `sparehash/upgrade.py:289-290` (writes `published` phase)

**Recovery when CLI is absent:**
- Direct invocation: `python3 -m sparehash.install --recover OPERATION_ID`
- Bypasses installed CLI, runs from source tree

**Refusal conditions:**
- Hold absent/mismatched: `sparehash/install.py:237-240`
- Staging missing: `sparehash/upgrade.py:267-268`
- Digest mismatch: `sparehash/upgrade.py:270-271`
- Already complete: `sparehash/upgrade.py:264-265`

### 4. Complete upgrade (`--complete-upgrade OPERATION_ID`)

**Documented in:** `docs/upgrade-v020.md` → "Phase 3: Complete the upgrade"

**Implementation:**
- Entry point: `sparehash/install.py:346-348` (argument parser)
- Dispatch: `sparehash/install.py:371-378` (calls `complete_install`)
- Journal verify: `sparehash/upgrade.py:302-304` (phase must be `published`)
- Hold verify: `sparehash/upgrade.py:305-308` (hold must exist and match)
- Payload verify: `sparehash/upgrade.py:309-314` (target exists and digest matches)
- Empty proof: `sparehash/upgrade.py:315-316` (neither service active)
- Journal removal: `sparehash/upgrade.py:317` (`remove_journal`)
- Hold clear: `sparehash/upgrade.py:318` (`store.clear()`)
- Staging cleanup: `sparehash/upgrade.py:319-324` (removes staging and backup)

### 5. Reinstall after uninstall (`--clear-lifecycle-latch`)

**Documented in:** `docs/upgrade-v020.md` → "Reinstall after uninstall"

**Implementation:**
- Entry point: `sparehash/install.py:277-278` (argument parser)
- Dispatch: `sparehash/install.py:289-300` (checks `is_retired()`, clears latch if flag present)
- Latch clear: `sparehash/lifecycle.py` (`clear_lifecycle_latch` removes retirement marker)

**Refusal conditions:**
- Active hold: Latch clear refuses if hold exists (not cleared by flag)
- Unreadable lease: Latch clear refuses if lease is corrupt

### 6. Uninstall flow

**Documented in:** `docs/upgrade-v020.md` → "Uninstall flow"

**Implementation:**
- Entry point: `sparehash/__main__.py:169-172` (dispatches to `cli_uninstall.uninstall`)
- Hold check: Refuses if active update hold exists
- Retirement latch: Writes to `/var/lib/sparehash/lifecycle.json`
- Service stop: Stops controller and miner
- Empty proof: Requires neither service active
- Receipt-owned removal: Removes only files tracked in manifest
- Purge flag: `--purge` removes config files too

## What Never Happens

**Documented in:** `docs/upgrade-v020.md` → "What never happens"

**Implementation:**
- Token rotation: No code path rotates API token on read failure
- Manual latch deletion: No supported command deletes `lifecycle.json` or `lifecycle-lease.json`
- Resume clears holds: `sparehash resume` only clears manual pause, not update holds
- Network update: `update` command exits 2 (unsupported)
- Forced hold clear: No command clears unrelated holds
- Blind staging overwrite: Staging must be empty or match source digest

## Error Messages and Recovery

| Error Message | Source | Recovery |
|---|---|---|
| `unrelated update hold retained; recovery uses its own request id` | `upgrade.py:130` | Complete or recover the original operation |
| `recorded staging is gone; cannot recover` | `upgrade.py:268` | Start new upgrade with new operation ID |
| `recorded staging digest mismatches; refusing recovery` | `upgrade.py:271` | Investigate staging modification |
| `fresh empty proof is unavailable; miner may still be running` | `upgrade.py:316` | Stop services, retry completion |
| `publication is not complete; run --recover or finish --upgrade` | `upgrade.py:304` | Run `--recover` or re-run `--upgrade` |
| `matching update hold is absent; refusing completion` | `upgrade.py:308` | Investigate hold deletion |
| `selected source is not a protected directory` | `upgrade.py:159` | Use a real directory, not a symlink |
| `private staging is not empty; resolve or recover the prior attempt first` | `upgrade.py:165` | Use `--recover` or resolve prior attempt |
| `staged payload does not match the source inventory; refused` | `upgrade.py:175` | Source mutated during staging; retry |
| `source subtree mutated during staging; refused` | `upgrade.py:179` | Source mutated during staging; retry |
| `deployment inventory does not match the staged payload` | `upgrade.py:198` | Staging corrupted; investigate |
| `staged digest mismatch: <path>` | `upgrade.py:201` | Staging corrupted; investigate |
| `runtime path is an unexpected symlink: <path>` | `upgrade.py:205` | Layout corrupted; investigate |
| `runtime path does not resolve beneath the layout root: <path>` | `upgrade.py:207` | Layout corrupted; investigate |
| `runtime path escapes the layout root: <path>` | `upgrade.py:209` | Layout corrupted; investigate |

## Test Coverage

**Existing tests:**
- `tests/test_upgrade_transaction.py`: Covers upgrade flow, recovery, completion, refusal conditions (in task-20 branch)

**Note:** The documented procedures are based on the actual implementation in `sparehash/upgrade.py` and `sparehash/install.py` from the task-20 upgrade branch.
