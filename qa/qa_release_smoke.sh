#!/usr/bin/env bash
# DESTRUCTIVE: opt-in disposable VM only; no host/lab controller dependency.
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
if [[ ${SPAREHASH_QA_DISPOSABLE_VM:-} != YES ]]; then
    echo 'FAIL: SPAREHASH_QA_DISPOSABLE_VM=YES is required (disposable VM only)' >&2
    exit 1
fi
SCRIPT_DIR=$(realpath "$(dirname "${BASH_SOURCE[0]}")")
exec /usr/bin/python3 -I -B "$SCRIPT_DIR/release_smoke.py" "$@"
