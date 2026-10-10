#!/usr/bin/env bash
# Disposable systemd VM only. The parent runner owns mounts and power fixtures.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/runtime.sh"
CONTROLLER_SERVICE=sparehash.service
MINER_SERVICE=sparehash-xmrig.service
POWER=$QA_ROOT/power
DROPIN=
POWER_BACKUP=

finish() {
    local result=$?
    trap - EXIT
    set +e
    cleanup
    (exit "$result")
    qa_cleanup
}
trap finish EXIT

check() { PYTHONPATH="/opt/sparehash/src:${SCRIPT_DIR}/.." python3 "$SCRIPT_DIR/lifecycle_check.py" "$@"; }
start_controller() { timeout 40 systemctl start "$CONTROLLER_SERVICE"; }
stop_controller() { timeout 40 "$CONTROLLER_BIN" stop; check off; }
baseline() { check baseline; }

# ExecCondition exit 1 skips activation successfully: systemctl's return code
# alone is NOT a denial proof. Require the condition result and no miner PID.
deny_direct_start() {
    timeout 15 systemctl start "$MINER_SERVICE"
    check denied
}

restore_namespace() {
    if [[ -n "$DROPIN" ]]; then
        rm "$DROPIN"
        DROPIN=
        systemctl daemon-reload
    fi
}

cleanup() {
    restore_namespace
    if [[ -n "$POWER_BACKUP" ]]; then
        cp "$POWER_BACKUP/online" "$POWER/AC/online"
        if [[ -d "$POWER_BACKUP/BAT0" ]]; then
            for attribute in type present status; do
                if [[ -f "$POWER_BACKUP/BAT0/$attribute" ]]; then
                    cp "$POWER_BACKUP/BAT0/$attribute" "$POWER/BAT0/$attribute"
                else
                    rm "$POWER/BAT0/$attribute"
                fi
            done
        else
            rm "$POWER/BAT0/type" "$POWER/BAT0/present" "$POWER/BAT0/status"
            rmdir "$POWER/BAT0"
        fi
        rm -r "$POWER_BACKUP"
        POWER_BACKUP=
    fi
}

main() {
    [[ $EUID -eq 0 ]] || { echo 'FAIL: disposable VM root required' >&2; return 1; }
    if [[ -n ${QA_RUNTIME_OWNER:-} ]]; then
        qa_attach
    else
        qa_quiesce
        qa_setup
        qa_fixture_overlays
    fi
    [[ -f "$POWER/AC/type" && -f "$POWER/AC/online" ]]
    [[ $(stat -c %u "$POWER") == 0 ]]
    # Mutation is in-place; replacing a bind-mounted source directory is wrong.
    echo '=== Controller lifecycle acceptance ==='
    start_controller
    baseline
    stop_controller
    echo 'PASS: explicit stop shuts down controller and miner'

    start_controller
    baseline
    check loss
    echo 'PASS: old miner tasks/cgroup empty <=5s; automatic controller restart and fresh recovery'

    for blocker in battery gaming thermal manual; do
        baseline
        check ipc debug-set "$blocker"
        check debug "$blocker"
        deny_direct_start
        check ipc debug-clear "$blocker"
        # Explicit resume bypasses dwell, never other blockers.
        check ipc resume
        baseline
        echo "PASS: live IPC $blocker override stops mining and denies direct start"
    done

    baseline
    check ipc pause
    check blocked manual
    stop_controller
    start_controller
    check blocked manual
    deny_direct_start
    check hold manual
    check ipc resume
    baseline
    echo 'PASS: acknowledged manual pause survives restart beyond dwell; resume recovers'

    baseline
    # BAT0 is optional in the runner baseline. Add attributes, never replace POWER.
    POWER_BACKUP=$(mktemp -d "$QA_ROOT/lifecycle-power.XXXXXX")
    cp "$POWER/AC/online" "$POWER_BACKUP/online"
    if [[ -d "$POWER/BAT0" ]]; then cp -a "$POWER/BAT0" "$POWER_BACKUP/BAT0"; fi
    mkdir -p "$POWER/BAT0"
    printf 'Battery\n' > "$POWER/BAT0/type"
    printf '1\n' > "$POWER/BAT0/present"
    printf 'Discharging\n' > "$POWER/BAT0/status"
    printf '0\n' > "$POWER/AC/online"
    check blocked battery
    stop_controller
    start_controller
    check blocked battery
    deny_direct_start
    check hold battery
    printf 'Full\n' > "$POWER/BAT0/status"
    printf '1\n' > "$POWER/AC/online"
    check ipc resume
    baseline
    cleanup
    echo 'PASS: persistent sysfs battery evidence inhibits restart and permits recovery on AC'

    baseline
    printf '86000\n' > "$QA_ROOT/hwmon/hwmon0/temp1_input"
    check blocked thermal 45
    stop_controller
    start_controller
    check blocked thermal 45
    deny_direct_start
    check hold thermal
    printf '74000\n' > "$QA_ROOT/hwmon/hwmon0/temp1_input"
    check baseline
    printf '40000\n' > "$QA_ROOT/hwmon/hwmon0/temp1_input"
    echo 'PASS: thermal fixture evidence inhibits restart and recovers below resume'

    baseline
    qa_gamemode_state active
    check blocked gaming 60
    stop_controller
    start_controller
    check blocked gaming 45
    deny_direct_start
    check hold gaming
    qa_gamemode_state inactive
    check baseline
    echo 'PASS: provider-driven gaming evidence inhibits restart and recovers when idle'

    baseline
    stop_controller
    # Runtime-only, uniquely owned namespace overlay: never chmod/write config.
    mkdir -p /run/systemd/system/sparehash.service.d
    DROPIN=$(mktemp /run/systemd/system/sparehash.service.d/qa-lifecycle-XXXXXX.conf)
    printf '[Service]\nInaccessiblePaths=/etc/sparehash/config.toml\n' > "$DROPIN"
    systemctl daemon-reload
    if timeout 40 systemctl start "$CONTROLLER_SERVICE"; then
        echo 'FAIL: controller became ready with inaccessible config' >&2
        return 1
    else
        [[ $? -eq 1 ]] || { echo 'FAIL: start failed for reason other than unit failure' >&2; return 1; }
    fi
    # on-failure may keep retrying: require no ready controller/miner, not inactive.
    check unready
    timeout 40 systemctl stop "$CONTROLLER_SERVICE"
    restore_namespace
    systemctl reset-failed "$CONTROLLER_SERVICE"
    start_controller
    baseline
    echo 'PASS: failed initial readiness keeps miner off; namespace removal recovers'

    for i in 1 2 3; do
        stop_controller
        start_controller
        baseline
    done
    echo 'PASS: three bounded stop/start cycles recover mining without deadlock'
    echo '=== Lifecycle acceptance complete ==='
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
