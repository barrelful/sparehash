#!/usr/bin/env bash
#
# qa_gaming.sh - Integration QA for the gaming blocker
#
# Every assertion here goes through the running controller: `sparehash
# gaming-start` / `gaming-end` are real control-socket calls whose
# acknowledgement is checked, and every observation is read back from the
# controller's own status document. No fixture is edited to manufacture a
# result.
#
#   Phase 1 - acknowledged double start: gaming-start is acknowledged twice,
#             the gaming blocker is present exactly once with a stable
#             generation (idempotent inside one generation), the miner stops
#             within the five-second safety deadline, gaming-end clears the
#             blocker, and the miner recovers after the configured dwell.
#   Phase 2 - missed-end recovery through the user provider. The fixture-backed
#             gamemoded state is driven active with no gaming-start hook; the
#             configured provider (root through the shim) must report the
#             session, block mining, and then clear it when the fixture goes
#             inactive, all without any hook contribution. A missing provider
#             is a hard failure, never reported as passed coverage.
#
# Exit code: 0 on success, 1 on failure

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
source "$SCRIPT_DIR/lib/runtime.sh"

AUX="$SCRIPT_DIR/auxiliary_check.py"
IPC_DOC="$(mktemp "${TMPDIR:-/tmp}/sparehash-gaming-XXXXXX")"
PHASE_TWO="not exercised"

finish() {
    local result=$?
    rm -f -- "$IPC_DOC" 2>/dev/null || true
    trap - EXIT
    set +e
    (exit "$result")
    qa_cleanup
}
trap finish EXIT

die() {
    echo "FAIL: $*" >&2
    exit 1
}

# ------------------------------------------------------------------
# Controller observations
# ------------------------------------------------------------------
read_ipc() {
    # Refresh $IPC_DOC from the live control socket.
    python3 - "$REPO_DIR" "$IPC_DOC" <<'PY'
import json
import sys
from pathlib import Path

repository = Path(sys.argv[1])
target = Path(sys.argv[2])
for candidate in (Path("/opt/sparehash/src"), repository):
    if (candidate / "sparehash" / "ipc.py").is_file():
        sys.path.insert(0, str(candidate))
        break
from sparehash import ipc

target.write_text(json.dumps(ipc.call({"cmd": "status"}, timeout=2)), encoding="utf-8")
PY
}

observed() {
    # A fresh, contract-checked status document is available in $IPC_DOC.
    read_ipc >/dev/null 2>&1 && python3 "$AUX" ipc "$IPC_DOC" >/dev/null
}

has_blocker() {
    python3 - "$IPC_DOC" "$1" <<'PY'
import json
import sys

document = json.loads(open(sys.argv[1], encoding="utf-8").read())
raise SystemExit(0 if sys.argv[2] in document["blockers"] else 1)
PY
}

lacks_blocker() {
    python3 - "$IPC_DOC" "$1" <<'PY'
import json
import sys

document = json.loads(open(sys.argv[1], encoding="utf-8").read())
raise SystemExit(0 if sys.argv[2] not in document["blockers"] else 1)
PY
}

generation() {
    python3 - "$IPC_DOC" <<'PY'
import json
import sys

document = json.loads(open(sys.argv[1], encoding="utf-8").read())
print(document["generation"])
PY
}

wait_for() {
    # wait_for SECONDS COMMAND [ARGS...]: poll until COMMAND succeeds.
    local deadline=$(( $(date +%s) + $1 ))
    shift
    while (( $(date +%s) <= deadline )); do
        if "$@"; then
            return 0
        fi
        sleep 1
    done
    return 1
}

wait_for_blocker() {
    local name=$1 seconds=$2
    wait_for "$seconds" _blocker_seen "$name" || die "gaming blocker '$name' never appeared within ${seconds}s"
    echo "  blocker '$name' acknowledged by the controller"
}

_blocker_seen() {
    observed && has_blocker "$1"
}

_blocker_gone() {
    observed && lacks_blocker "$1"
}

wait_for_clear() {
    local name=$1 seconds=$2
    wait_for "$seconds" _blocker_gone "$name" || die "gaming blocker '$name' never cleared within ${seconds}s"
    echo "  blocker '$name' cleared by the controller"
}

hook() {
    # hook VERB: run the real CLI verb and require an acknowledged exit status.
    local status
    set +e
    "$CONTROLLER_BIN" "$1"
    status=$?
    set -e
    python3 "$AUX" action "$status" "sparehash $1" 0 || die "sparehash $1 was not acknowledged"
    echo "  sparehash $1 acknowledged"
}

configured_gaming_users() {
    # Prefer the QA fixture config (the exact bytes the controller observes
    # through the bind overlay) over the receipt-owned installed path.
    local config=${QA_ROOT}/config.test.toml
    [[ -f $config ]] || config=/etc/sparehash/config.toml
    python3 - "$config" <<'PY'
import sys
import tomllib
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(0)
document = tomllib.loads(path.read_text(encoding="utf-8"))
gaming = document.get("gaming")
if not isinstance(gaming, dict) or gaming.get("enabled") is not True:
    raise SystemExit(0)
users = gaming.get("users")
if not isinstance(users, list) or not users:
    raise SystemExit(0)
print(",".join(user for user in users if isinstance(user, str)))
PY
}

provider_entries() {
    # Print one "<uid>:<active>" per configured provider observation, none when empty.
    python3 - "$IPC_DOC" <<'PY'
import json
import sys

document = json.loads(open(sys.argv[1], encoding="utf-8").read())
for entry in document["gaming"]:
    print(f"{entry['uid']}:{str(entry['active']).lower()}")
PY
}

# ------------------------------------------------------------------
# Phase 1: acknowledged double start, stop, and recovery
# ------------------------------------------------------------------
phase_double_start() {
    echo "--- Phase 1: acknowledged double start and miner stop/recovery ---"

    qa_restart
    qa_wait_miner active 90

    observed || die "controller status document is unavailable before the first hook"
    echo "  baseline status document accepted by the auxiliary contract"
    if has_blocker gaming; then
        die "gaming blocker is already active at baseline; hooks were not reset"
    fi
    echo "  baseline: gaming blocker clear, miner running"

    # The stop deadline is anchored at the trigger, not at the first probe.
    qa_stop_deadline
    hook gaming-start
    qa_wait_miner inactive 5
    echo "  miner stopped within the five-second safety deadline"

    wait_for_blocker gaming 10
    local generation_first
    generation_first=$(generation)
    echo "  generation after the first start: $generation_first"

    hook gaming-start
    local index sample_generation
    for index in 1 2 3; do
        observed || die "controller status document is unavailable during the idempotency check"
        has_blocker gaming || die "gaming blocker disappeared after the second start"
        sample_generation=$(generation)
        if [[ $sample_generation != "$generation_first" ]]; then
            die "second gaming-start was not idempotent: generation moved from $generation_first to $sample_generation"
        fi
        echo "  sample $index: gaming blocker held once, generation still $sample_generation"
        sleep 1
    done
    echo "  double start is idempotent inside one generation (hooks are a set, one blocker)"

    hook gaming-end
    wait_for_clear gaming 10
    echo "  miner recovery: waiting out the configured minimum dwell"
    qa_wait_miner active 120
    echo "  miner recovered after gaming-end"
}

# ------------------------------------------------------------------
# Phase 2: missed-end recovery through the configured user provider
# ------------------------------------------------------------------
phase_provider() {
    echo "--- Phase 2: missed-end recovery through the configured user provider ---"
    local users
    users=$(configured_gaming_users) || die "cannot read gaming providers from the effective configuration"
    [[ -n $users ]] || die "no gaming user provider configured; the provider contract must be exercised"
    echo "  configured gaming providers: $users"

    # The 45s budget is anchored at the fixture transition, covering provider
    # observation and the miner stop together.
    qa_stop_deadline 45
    qa_gamemode_state active

    wait_for 35 _provider_active || die "provider never reported an active root session within 35s"
    echo "  provider reports an active session without any gaming-start hook"
    observed || die "controller status document is unavailable while the provider blocks"
    has_blocker gaming || die "provider-reported session did not activate the gaming blocker"
    echo "  gaming blocker is provider-driven: active with no hook contribution"
    qa_wait_miner inactive 45
    echo "  miner stopped within the provider-driven 45s budget"

    qa_gamemode_state inactive
    wait_for 45 _provider_idle || die "provider never reported idle after the fixture went inactive"
    echo "  provider reports every configured user inactive"
    wait_for_clear gaming 45
    observed || die "controller status document is unavailable after provider recovery"
    echo "  missed-end recovery: blocker cleared through the provider with no gaming-end hook"
    qa_wait_miner active 120
    echo "  miner recovered after provider-driven missed-end recovery"
    PHASE_TWO="PASS (provider-driven missed-end recovery, no gaming-end hook)"
}

_provider_reported() {
    observed || return 1
    [[ -n $(provider_entries) ]]
}

_provider_active() {
    _provider_reported || return 1
    provider_entries | awk -F: '$1 == "0" && $2 == "true" { found = 1 } END { exit !found }'
}

_provider_idle() {
    _provider_reported || return 1
    provider_entries | awk -F: '$2 != "false" { dirty = 1 } END { exit dirty }'
}

main() {
    echo "=== qa_gaming.sh: Gaming Blocker QA ==="
    [[ -x $CONTROLLER_BIN ]] || die "control CLI $CONTROLLER_BIN is missing or not executable"
    [[ -f $AUX ]] || die "auxiliary checker $AUX is missing or executable bit is irrelevant"

    if [[ -n ${QA_RUNTIME_OWNER:-} ]]; then
        qa_attach
    else
        qa_quiesce
        qa_setup
        qa_fixture_overlays
    fi

    phase_double_start
    phase_provider

    echo ""
    echo "=== qa_gaming.sh: Complete ==="
    echo "  Phase 1 (acknowledged double start, stop, recovery): PASS"
    echo "  Phase 2 (missed-end provider recovery): $PHASE_TWO"
    exit 0
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
