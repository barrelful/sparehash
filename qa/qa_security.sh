#!/usr/bin/env bash
# Requires root and an already-running controller/miner; never starts services.
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
    echo 'FAIL: security QA requires root' >&2
    exit 1
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Do not let fixture overrides redirect installed-system checks.
unset SPAREHASH_RUN_DIR
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C
exec python3 "$SCRIPT_DIR/security_check.py"
