#!/usr/bin/env bash
# VM-only power acceptance. Never relax the five-second safety deadline.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/runtime.sh"

main() {
    if [[ -n ${QA_RUNTIME_OWNER:-} ]]; then
        qa_attach
    else
        trap qa_cleanup EXIT
        qa_setup
    fi
    echo '=== no-battery baseline ==='
    qa_power_files 0 Full
    qa_upower false true
    qa_restart
    qa_wait_miner active 90
    qa_assert_power false false null

    echo '=== battery-present AC baseline ==='
    qa_power_files 1 Charging
    qa_upower false true
    qa_restart
    qa_wait_miner active 90
    qa_assert_power true false false

    echo '=== discharging: event-driven stop <=5s ==='
    qa_stop_deadline
    qa_power_files 1 Discharging
    qa_upower true true
    qa_wait_miner inactive 5
    qa_assert_power true true true

    echo '=== contradiction: UPower true, sysfs AC ==='
    qa_power_files 1 Charging
    qa_upower false true
    qa_restart
    qa_wait_miner active 90
    qa_assert_power true false false
    qa_stop_deadline
    qa_upower true true
    qa_wait_miner inactive 5
    qa_assert_power true true false

    echo '=== contradiction: UPower false, sysfs discharging ==='
    qa_power_files 1 Charging
    qa_upower false true
    qa_restart
    qa_wait_miner active 90
    qa_stop_deadline
    qa_power_files 1 Discharging
    # The generation changes to emit PropertiesChanged with OnBattery still false.
    qa_upower false true
    qa_wait_miner inactive 5
    qa_assert_power true false true

    echo '=== both UPower helpers unavailable: no-battery sysfs fallback ==='
    qa_power_files 0 Full
    qa_upower false false
    qa_restart
    qa_wait_miner active 90
    qa_assert_power false null null
    echo '=== restore shared no-battery environment ==='
    qa_upower false true
    qa_restart
    qa_wait_miner active 90
    qa_assert_power false false null
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
