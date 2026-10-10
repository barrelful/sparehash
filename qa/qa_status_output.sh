#!/usr/bin/env bash
#
# qa_status_output.sh - Integration QA for the status output contract
#
# Read-only: this suite never starts, stops, or reconfigures anything. The
# runner restarts the controller and waits for the miner before this suite, so
# status is captured while the installation is present and before any
# uninstall action.
#
#   Phase 1 - `sparehash status --json` is parsed as JSON (never grepped) and
#             checked field by field for its required types by
#             qa/auxiliary_check.py. The exit status is accepted only when it
#             matches the controller state the document reports: 3 exactly
#             when the controller is unavailable, 0 otherwise. An unavailable
#             controller here is a genuine failure because readiness was
#             checked before this suite.
#   Phase 2 - the plain renderer carries the same key set as the JSON
#             renderer, every plain line is a JSON value, and the documented
#             final MSR line is present.
#   Phase 3 - `dns_hint` is a typed nullable string and is never required to
#             contain a literal pool hostname. Its value is produced by a
#             synthetic read-only resolver surface (unit level, no network,
#             no DNS change).
#
# Exit code: 0 on success, 1 on failure

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
source "$SCRIPT_DIR/lib/runtime.sh"

AUX="$SCRIPT_DIR/auxiliary_check.py"
JSON_DOC="$(mktemp "${TMPDIR:-/tmp}/sparehash-status-json-XXXXXX")"
PLAIN_DOC="$(mktemp "${TMPDIR:-/tmp}/sparehash-status-plain-XXXXXX")"
STATUS_RC=0

finish() {
    local result=$?
    rm -f -- "$JSON_DOC" "$PLAIN_DOC" 2>/dev/null || true
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
# Capture
# ------------------------------------------------------------------
run_status() {
    # run_status OUTPUT [FLAG]: write stdout to OUTPUT, set STATUS_RC.
    # The controller's own mount namespace is used when it is running so the
    # captured document reflects the same view the controller reports.
    local target=$1 flag=${2:-} pid
    pid=$(systemctl show sparehash.service -p MainPID --value 2>/dev/null || true)
    set +e
    if [[ $pid =~ ^[1-9][0-9]*$ ]] && command -v nsenter >/dev/null 2>&1; then
        # shellcheck disable=SC2086
        nsenter --target "$pid" --mount -- "$CONTROLLER_BIN" status $flag > "$target"
    else
        "$CONTROLLER_BIN" status $flag > "$target"
    fi
    STATUS_RC=$?
    set -e
}

# ------------------------------------------------------------------
# Phase 1: JSON contract and exit status
# ------------------------------------------------------------------
phase_json() {
    echo "--- Phase 1: status --json contract ---"
    run_status "$JSON_DOC" --json
    echo "  exit status: $STATUS_RC"
    python3 "$AUX" action "$STATUS_RC" "sparehash status --json" 0,3 \
        || die "status --json exit status is outside the contract (0 or 3)"
    python3 "$AUX" status "$JSON_DOC" \
        || die "status --json does not satisfy the required field and type contract"
    python3 - "$JSON_DOC" "$STATUS_RC" <<'PY' \
        || die "status --json exit status does not match the reported controller state"
import json
import sys

document = json.loads(open(sys.argv[1], encoding="utf-8").read())
code = int(sys.argv[2])
controller = document["controller"]
expected = 3 if controller == "unavailable" else 0
if code != expected:
    raise SystemExit(
        f"controller={controller!r} requires exit {expected}, observed {code}")
if controller == "unavailable":
    raise SystemExit(
        "controller reports 'unavailable' although readiness was checked "
        "before this suite; status is being captured at the wrong moment")
print(f"  controller={controller!r} -> exit {code}, as the contract requires")
print("  required fields and types accepted")
PY
}

# ------------------------------------------------------------------
# Phase 2: plain renderer parity
# ------------------------------------------------------------------
phase_plain() {
    echo "--- Phase 2: plain renderer parity ---"
    run_status "$PLAIN_DOC"
    echo "  exit status: $STATUS_RC"
    python3 "$AUX" action "$STATUS_RC" "sparehash status" 0,3 \
        || die "plain status exit status is outside the contract (0 or 3)"
    python3 - "$JSON_DOC" "$PLAIN_DOC" <<'PY' \
        || die "plain and JSON renderers do not carry the same contract"
import json
import sys

MSR_LINE = "MSR: unavailable in v1"


def parse_plain(path: str) -> dict[str, object]:
    lines = open(path, encoding="utf-8").read().splitlines()
    if not lines:
        raise SystemExit("plain status output is empty")
    if lines[-1] != MSR_LINE:
        raise SystemExit(
            f"plain status must end with the documented MSR line, observed {lines[-1]!r}")
    fields: dict[str, object] = {}
    for line in lines[:-1]:
        name, separator, value = line.partition(": ")
        if not separator:
            raise SystemExit(f"malformed plain status line: {line!r}")
        if name in fields:
            raise SystemExit(f"duplicate plain status field: {name}")
        fields[name] = json.loads(value)
    return fields


plain = parse_plain(sys.argv[2])
document = json.loads(open(sys.argv[1], encoding="utf-8").read())
missing = sorted(set(document) - set(plain))
extra = sorted(set(plain) - set(document))
if missing or extra:
    raise SystemExit(
        f"key set mismatch: absent from plain={missing}, absent from json={extra}")
print(f"  {len(plain)} fields agree across both renderers")
print(f"  documented final line present: {MSR_LINE}")
PY
}

# ------------------------------------------------------------------
# Phase 3: dns_hint contract
# ------------------------------------------------------------------
phase_dns() {
    echo "--- Phase 3: dns_hint contract ---"
    python3 - "$JSON_DOC" <<'PY' \
        || die "dns_hint is not a nullable, non-empty string"
import json
import sys

value = json.loads(open(sys.argv[1], encoding="utf-8").read())["dns_hint"]
if value is None:
    print("  dns_hint field: null (resolved pool, nothing to report)")
elif not isinstance(value, str) or not value:
    raise SystemExit(f"dns_hint must be a non-empty string or null, observed {value!r}")
else:
    print(f"  dns_hint field: {value!r} (nullable string; no literal pool hostname required)")
PY

    if [[ ! -f /etc/sparehash/config.toml ]]; then
        echo "  resolver-driven hint surface: NOT APPLICABLE (no /etc/sparehash/config.toml on this host)"
        return 0
    fi
    python3 - "$REPO_DIR" <<'PY' \
        || die "a failing resolver must produce the documented dns hint"
import json
import sys
from pathlib import Path

repository = Path(sys.argv[1])
for candidate in (Path("/opt/sparehash/src"), repository):
    if (candidate / "sparehash" / "status.py").is_file():
        sys.path.insert(0, str(candidate))
        break
from sparehash import status as status_module


def controller_reader() -> dict[str, object]:
    return {
        "ok": True,
        "ready": True,
        "blockers": [],
        "debug": [],
        "generation": 1,
        "running": False,
        "reasons": {},
        "observation_age": 1.0,
        "gaming": [],
    }


provider = status_module.LiveProvider(
    controller_reader=controller_reader,
    resolver=lambda _host: False,
)
snapshot = provider.snapshot()
if snapshot.dns_hint != status_module.DNS_HINT:
    raise SystemExit(
        "a failing resolver must yield the documented hint, "
        f"observed {snapshot.dns_hint!r}")
rendered = json.loads(status_module.format_status(snapshot, True))
value = rendered.get("dns_hint")
if value != status_module.DNS_HINT or not isinstance(value, str):
    raise SystemExit(
        "the rendered document must carry the documented hint, "
        f"observed {value!r}")
print("  synthetic read-only resolver surface: failing resolver -> documented "
      "dns_hint string (no network, no DNS change)")
PY
}

# ------------------------------------------------------------------
main() {
    echo "=== qa_status_output.sh: Status Output QA ==="
    [[ -f $AUX ]] || die "auxiliary checker $AUX is missing"

    if [[ -n ${QA_RUNTIME_OWNER:-} ]]; then
        qa_attach
    else
        qa_setup
    fi

    # Status is captured before uninstall: the installation must still be here.
    [[ -f /var/lib/sparehash/install-manifest.json ]] \
        || die "installation manifest is missing; status must be captured before uninstall"
    [[ -f /etc/sparehash/config.toml ]] \
        || die "public configuration is missing; status must be captured before uninstall"
    echo "  installation present; status captured before any uninstall action"

    phase_json
    phase_plain
    phase_dns

    echo ""
    echo "=== qa_status_output.sh: Complete ==="
    echo "  Phase 1 (json fields, types, exit status): PASS"
    echo "  Phase 2 (plain renderer parity): PASS"
    echo "  Phase 3 (dns_hint contract): PASS"
    exit 0
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
