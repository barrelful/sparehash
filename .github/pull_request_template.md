## Summary

Briefly describe the change and why it is needed.

## Related issue

Link to the issue this PR addresses, if any.

## Checklist

- [ ] Unit tests pass (`python3 -m pytest tests/ -q`)
- [ ] README inventory checker passes (`python3 qa/check_readme.py`)
- [ ] README/CONTRIBUTING/CHANGELOG updated if needed
- [ ] No MSR/kernel/firewall/DNS/bootloader weakening introduced
- [ ] Controller remains the sole authority for miner starts
- [ ] XMRig continues to run unprivileged

## Notes for reviewers

Highlight any trade-offs, security implications, or areas that need careful
review.
