#!/usr/bin/env bash
#
# qa_thermal.sh - Integration QA for thermal guard hysteresis
#
# Polls the real ThermalGuard.poll() path in sequence over a private fixture
# tree: 84 -> CLEAR, 86 -> TRIPPED, 80 -> TRIPPED (hold), 74 -> CLEAR.
# The fixture lives in a unique temporary directory that a trap removes, so a
# crashed run never leaves a shared /tmp path behind for the next run.
#
# Exit code: 0 on success, 1 on failure

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

FIXTURE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/sparehash-thermal-XXXXXX")"

cleanup() {
    rm -rf -- "$FIXTURE_ROOT"
}
trap cleanup EXIT
trap 'cleanup; exit 1' HUP INT TERM

mkdir -m 0755 "$FIXTURE_ROOT/hwmon0"
printf 'coretemp\n' > "$FIXTURE_ROOT/hwmon0/name"
printf 'Package id 0\n' > "$FIXTURE_ROOT/hwmon0/temp1_label"
printf '84000\n' > "$FIXTURE_ROOT/hwmon0/temp1_input"

echo "=== qa_thermal.sh: Thermal Guard QA ==="
echo "  fixture: $FIXTURE_ROOT (unique directory, removed by trap)"

python3 - "$REPO_DIR" "$FIXTURE_ROOT" <<'PY'
import sys
from pathlib import Path

repository = Path(sys.argv[1])
root = Path(sys.argv[2])
sys.path.insert(0, str(repository))

from sparehash.thermal import ThermalGuard, ThermalStatus, find_sensor

sensor = find_sensor(root)
if sensor is None:
    print("FAIL: no credible sensor found in the fixture", file=sys.stderr)
    raise SystemExit(1)

guard = ThermalGuard(enabled=True, pause_c=85, resume_c=75, sensor=sensor)
expectations = (
    (84, ThermalStatus.CLEAR),
    (86, ThermalStatus.TRIPPED),
    (80, ThermalStatus.TRIPPED),
    (74, ThermalStatus.CLEAR),
)
for index, (celsius, expected) in enumerate(expectations, start=1):
    (root / "hwmon0" / "temp1_input").write_text(f"{celsius * 1000}\n", encoding="ascii")
    result = guard.poll()
    print(f"  poll {index}: {celsius}C -> {result.status.value} (expected {expected.value})", flush=True)
    if result.status is not expected:
        print(f"FAIL: expected {expected.value} at {celsius}C, got {result.status.value}", file=sys.stderr)
        raise SystemExit(1)
print("  one guard instance held hysteresis across the full 84/86/80/74 sequence", flush=True)
PY

echo "=== qa_thermal.sh: Complete ==="
echo "  Phase 1 (sequential hysteresis 84/86/80/74): PASS"
exit 0
