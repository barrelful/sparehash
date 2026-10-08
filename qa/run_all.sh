#!/usr/bin/env bash
#
# run_all.sh - Run all QA suites and write evidence logs
#
# Executes every QA suite and writes qa/evidence/qa-<suite>.log for each.
# Exits non-zero on any failure.
#
# This script is designed to run in a disposable systemd VM with the SpareHash
# controller and miner already running.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
QA_DIR="$REPO_DIR/qa"
EVIDENCE_DIR="/omo/evidence"

# Ensure evidence directory exists
mkdir -p "$EVIDENCE_DIR" 2>/dev/null || true

# List of QA suites to run (in order, for dependency awareness)
QA_SUITES=(
    "qa_power_shim.sh"
    "qa_thermal.sh"
    "qa_gaming.sh"
    "qa_contention.sh"
    "qa_security.sh"
    "qa_controller_lifecycle.sh"
    "qa_uninstall.sh"
    "qa_idempotent_install.sh"
    "qa_status_output.sh"
)

# Track overall success
OVERALL_EXIT=0

echo "=== run_all.sh: Running all QA suites ==="
echo ""

# Run each QA suite
for suite in "${QA_SUITES[@]}"; do
    suite_path="$QA_DIR/$suite"
    echo "--------------------------------------------"
    echo "Running: $suite"
    echo "--------------------------------------------"

    if [ -x "$suite_path" ]; then
        # Run the suite and capture output/log
        log_file="$EVIDENCE_DIR/qa-${suite%.sh}.log"

        # Run the suite, tee output to stdout and log file
        set +e
        if bash "$suite_path" 2>&1 | tee "$log_file"; then
            echo "  PASS: $suite completed successfully"
        else
            echo "  FAIL: $suite exited non-zero"
            OVERALL_EXIT=1
        fi
        set -e
    else
        echo "  SKIP: $suite is not executable or not found"
        OVERALL_EXIT=1
    fi

    echo ""
    echo "--------------------------------------------"
done

# -------------------------------------------------------
# Final: capture sparehash status as evidence
# -------------------------------------------------------
echo ""
echo "=== Final state capture ==="
if systemctl is-active "sparehash.service" >/dev/null 2>&1; then
    echo " Capturing: sparehash status"
    $REPO_DIR/usr/local/bin/sparehash status > "$EVIDENCE_DIR/final-status.txt" 2>&1 || true
    echo " Capturing: systemctl status sparehash.service sparehash-xmrig.service"
    systemctl status "sparehash.service" "sparehash-xmrig.service" > "$EVIDENCE_DIR/final-systemctl.txt" 2>&1 || true
else
    echo "  Controller not active, skipping final status capture"
fi

# -------------------------------------------------------
# Summary
# -------------------------------------------------------
echo ""
echo "=== run_all.sh: Complete ==="
echo "  Overall exit code: $OVERALL_EXIT"
echo "  Evidence written to: $EVIDENCE_DIR/"

# Exit with non-zero if any suite failed
exit $OVERALL_EXIT