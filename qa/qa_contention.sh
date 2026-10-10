#!/usr/bin/env bash
#
# qa_contention.sh - thin gate for the contention acceptance helper.
#
# Every assertion (yield <10%, foreground degradation <=25%, recovery >=20%)
# lives in qa/contention.py; this wrapper only proves it is running as root
# inside a disposable systemd VM, then execs the helper.
#
# Exit code: 0 on success, 1 on refusal/failure

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo 'FAIL: contention QA requires root' >&2
    exit 1
fi

if [[ ${SPAREHASH_QA_DISPOSABLE_VM:-} != YES ]]; then
    echo 'FAIL: set SPAREHASH_QA_DISPOSABLE_VM=YES only inside a disposable VM' >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

# Repository first, then the installed candidate, then any caller extras.
export PYTHONPATH="$REPO_DIR:/opt/sparehash/src${PYTHONPATH:+:$PYTHONPATH}"

exec python3 "$SCRIPT_DIR/contention.py"
