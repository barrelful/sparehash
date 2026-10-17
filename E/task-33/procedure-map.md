# Task 33: Procedure Map

Maps documented procedures to implementation in `sparehash/upgrade.py` and `sparehash/install.py`.

## Procedures

1. **Pre-flight**: `upgrade.py:103-133` (operation ID validation, hold acquisition)
2. **Stage/publish** (`--upgrade`): `install.py:340-378`, `upgrade.py:136-255` (journal, staging, verify, publish)
3. **Recovery** (`--recover`): `install.py:237-240`, `upgrade.py:257-291` (hold check, staging verify, roll-forward)
4. **Complete** (`--complete-upgrade`): `upgrade.py:294-325` (journal/hold/payload verify, empty proof, cleanup)
5. **Reinstall** (`--clear-lifecycle-latch`): `install.py:277-300` (retirement check, latch clear)
6. **Uninstall**: `__main__.py:169-172`, `cli_uninstall.py` (hold check, retirement, service stop, receipt removal)

## What never happens

- No token rotation on read failure
- No manual lifecycle file deletion supported
- `resume` does not clear update holds
- No network update path
- No forced clear of unrelated holds
- No blind staging overwrite

## Error messages

See `sparehash/upgrade.py` for exact refusal conditions and recovery guidance.
