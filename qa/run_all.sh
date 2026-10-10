#!/usr/bin/env bash
# Run only inside a disposable VM containing the already installed candidate.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
source "$SCRIPT_DIR/lib/runtime.sh"

# Evidence goes to a unique per-run directory; a pre-set EVIDENCE_DIR wins.
# Nothing is created at source time - main() mkdirs after preflight.
if [[ -z ${EVIDENCE_DIR:-} ]]; then
    if [[ -d /omo && -w /omo ]]; then
        EVIDENCE_DIR="/omo/evidence/run-$(date +%s)-$$"
    else
        EVIDENCE_DIR="$SCRIPT_DIR/evidence/run-$(date +%s)-$$"
    fi
fi

# Suites executed by the runner; uninstall must stay last.
QA_SUITES=(
    "qa_idempotent_install.sh"
    "qa_thermal.sh"
    "qa_gaming.sh"
    "qa_contention.sh"
    "qa_security.sh"
    "qa_controller_lifecycle.sh"
    "qa_power_shim.sh"
    "qa_status_output.sh"
    "qa_uninstall.sh"
)

run_suite() {
    local suite=$1 log_file="$EVIDENCE_DIR/qa-${1%.sh}.log" result=0 pid
    [[ -f $SCRIPT_DIR/$suite && -x $SCRIPT_DIR/$suite ]] || {
        echo "FAIL: required suite missing/not executable: $suite" >&2
        return 1
    }
    if _qa_ns_suite "$suite"; then
        pid=$(systemctl show sparehash.service -p MainPID --value)
        [[ $pid =~ ^[1-9][0-9]*$ ]] || {
            echo "FAIL: controller has no live MainPID; cannot enter its namespace for $suite" >&2
            return 1
        }
        # nsenter keeps the caller environment, so ownership/fixture variables
        # reach the suite; the mount namespace stays alive for the whole suite
        # because the nsenter process holds it open, surviving controller stops.
        nsenter --target "$pid" --mount -- bash "$SCRIPT_DIR/$suite" 2>&1 | tee "$log_file" || result=$?
    else
        bash "$SCRIPT_DIR/$suite" 2>&1 | tee "$log_file" || result=$?
    fi
    [[ $result == 0 ]] || return "$result"
    # A suite that exits 0 while reporting skipped/unsupported/FAIL/UNAVAILABLE
    # coverage is not green. Two deliberate patterns:
    #  - case-insensitive word matches for skip/skipping/skipped/unsupported
    #    plus a line-leading FAIL (FAIL followed by a letter, e.g. "failed",
    #    does not match: passing output legitimately contains both words);
    #  - UNAVAILABLE only case-sensitive at line start, because lowercase
    #    "unavailable" appears in passing diagnostics ("MSR: unavailable in v1",
    #    install warnings mentioning UNAVAILABLE mid-line) and must not trip.
    if grep -Eiq '(^|[^[:alpha:]])(skip|skipping|skipped|unsupported)([^[:alpha:]]|$)|^[[:space:]]*FAIL([:[:space:]]|$)' "$log_file" \
        || grep -Eq '^[[:space:]]*UNAVAILABLE([:[:space:]]|$)' "$log_file"; then
        echo "FAIL: required suite reported failure, skipped, or unsupported coverage: $suite" >&2
        return 1
    fi
}

_qa_ns_suite() {
    local candidate
    for candidate in "${QA_NS_SUITES[@]}"; do
        [[ $candidate == "$1" ]] && return 0
    done
    return 1
}

qa_answers_contract() {
    # Runner-state contract: only an absolute, root-owned, repo-external
    # answers file with a local pool and the QA dwell of 5 seconds may drive
    # the installer. The result is recorded as evidence for later audits.
    [[ -n ${QA_ANSWERS_FILE:-} ]] || {
        echo 'FAIL: QA_ANSWERS_FILE must be set to an absolute path outside the repository' >&2
        return 1
    }
    [[ $QA_ANSWERS_FILE == /* ]] || {
        echo "FAIL: QA_ANSWERS_FILE must be absolute, got '$QA_ANSWERS_FILE'" >&2
        return 1
    }
    [[ -f $QA_ANSWERS_FILE && ! -L $QA_ANSWERS_FILE ]] || {
        echo "FAIL: QA_ANSWERS_FILE is not a regular file: $QA_ANSWERS_FILE" >&2
        return 1
    }
    case $QA_ANSWERS_FILE in
        "$SCRIPT_DIR"/* | "$REPO_DIR"/*)
            echo "FAIL: QA_ANSWERS_FILE must live outside the repository: $QA_ANSWERS_FILE" >&2
            return 1
            ;;
    esac
    python3 - "$QA_ANSWERS_FILE" <<'PY'
import sys
import tomllib
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
except (OSError, tomllib.TOMLDecodeError) as error:
    raise SystemExit(f"FAIL: QA answers file is unreadable TOML: {error}")
pool = data.get("pool")
if not isinstance(pool, dict) or pool.get("host") not in ("127.0.0.1", "localhost", "::1"):
    raise SystemExit("FAIL: QA answers must target a local pool host (127.0.0.1/localhost/::1)")
controller = data.get("controller")
dwell = controller.get("min_dwell_seconds") if isinstance(controller, dict) else None
if dwell != 5:
    raise SystemExit(f"FAIL: QA answers must set controller.min_dwell_seconds = 5, got {dwell!r}")
PY
    # Ownership is checked after content so error messages stay about the
    # contract itself, not the file metadata, when both are wrong.
    [[ $(stat -c %u "$QA_ANSWERS_FILE") == 0 ]] || {
        echo "FAIL: QA_ANSWERS_FILE must be root-owned: $QA_ANSWERS_FILE" >&2
        return 1
    }
    python3 - "$QA_ANSWERS_FILE" "$EVIDENCE_DIR/runner-state.json" <<'PY'
import hashlib
import json
import os
import sys
import time
from pathlib import Path

answers = Path(sys.argv[1])
document = {
    "answers_file": str(answers),
    "answers_sha256": hashlib.sha256(answers.read_bytes()).hexdigest(),
    "answers_owner_uid": answers.stat().st_uid,
    "min_dwell_seconds": 5,
    "evidence_dir": sys.argv[2].rsplit("/", 1)[0],
    "runner_pid": os.getpid(),
    "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
}
Path(sys.argv[2]).write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
PY
    echo "runner state recorded: $EVIDENCE_DIR/runner-state.json"
}

qa_install_acceptance() {
    [[ -n ${QA_ANSWERS_FILE:-} && -f $QA_ANSWERS_FILE ]]
    python3 - "$QA_ANSWERS_FILE" <<'PY'
import sys
import tomllib
from pathlib import Path
data = tomllib.loads(Path(sys.argv[1]).read_text())
assert data["pool"]["host"] in ("127.0.0.1", "localhost", "::1"), "QA requires a local pool"
PY
    echo '=== install: existing candidate, checked unattended re-install ==='
    "$CONTROLLER_BIN" install --answers "$QA_ANSWERS_FILE" --use-existing
    echo '=== idempotency: compare complete config, miner config, and runtime copy ==='
    cp /etc/sparehash/config.toml "$QA_ROOT/config.before"
    cp /etc/sparehash/xmrig.json "$QA_ROOT/xmrig.before"
    cp /etc/sparehash/xmrig.runtime.json "$QA_ROOT/xmrig-runtime.before"
    "$CONTROLLER_BIN" install --answers "$QA_ANSWERS_FILE" --use-existing
    cmp /etc/sparehash/config.toml "$QA_ROOT/config.before"
    cmp /etc/sparehash/xmrig.json "$QA_ROOT/xmrig.before"
    cmp /etc/sparehash/xmrig.runtime.json "$QA_ROOT/xmrig-runtime.before"
    echo '  re-install preserved the public config and both miner configs byte-for-byte'
}

qa_verify_final_status() {
    # Final capture must prove the fixtures were genuinely present: the thermal
    # sensor resolved, and the root gaming provider observed through the real
    # IPC with the session idle at capture time.
    python3 - "$1" <<'PY'
import json
import sys
from pathlib import Path

document = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if document.get("thermal_status") != "available":
    raise SystemExit(f"FAIL: thermal_status is {document.get('thermal_status')!r}, "
                     "expected 'available' (sensor fixture not observed)")
entries = document.get("gaming") or []
root = [entry for entry in entries if isinstance(entry, dict) and entry.get("uid") == 0]
if not root:
    raise SystemExit(f"FAIL: gaming observation carries no uid 0 entry: {entries!r}")
if not any(entry.get("active") is False for entry in root):
    raise SystemExit(f"FAIL: root gaming provider is not idle at capture: {root!r}")
print(f"  final status verified: thermal available, gaming providers {entries!r}")
PY
}

main() {
    qa_preflight
    mkdir -p "$EVIDENCE_DIR"
    trap qa_cleanup EXIT
    qa_answers_contract
    # A pre-existing controller must be quiesced before any fixture or local
    # pool exists, so nothing can hash against the real pool in the meantime.
    qa_quiesce
    qa_setup
    qa_pool_start 2>&1 | tee "$EVIDENCE_DIR/qa-local-pool.log"
    qa_install_acceptance 2>&1 | tee "$EVIDENCE_DIR/qa-install-idempotency.log"
    qa_fixture_overlays 2>&1 | tee "$EVIDENCE_DIR/qa-fixture-overlays.log"
    qa_restart
    qa_wait_miner active 90
    qa_assert_power false false null
    for suite in "${QA_SUITES[@]}"; do
        echo "=== Running: $suite ==="
        # Readiness is checked before status, never after destructive uninstall.
        if [[ $suite == qa_status_output.sh ]]; then
            qa_restart
            qa_wait_miner active 90
            qa_status | tee "$EVIDENCE_DIR/final-status.json"
            qa_verify_final_status "$EVIDENCE_DIR/final-status.json"
        fi
        run_suite "$suite"
    done
    echo "=== All required suites passed; uninstall was last ==="
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
