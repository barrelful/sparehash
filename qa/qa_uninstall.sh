#!/usr/bin/env bash
#
# qa_uninstall.sh - Integration QA for a residue-free uninstall
#
# Destructive: removes the installation. This suite runs last.
#
#   Phase 1 - pre-action: the CLI, both unit files, the gamemoded shim, a live
#             controller status document (the lease the uninstall acquires),
#             and a parseable installer receipt must all be present. A foreign
#             decoy is written inside /etc/sparehash, which the receipt does
#             not inventory, so it has to survive the removal. Digests of the
#             retained configuration and of the decoy are recorded here, and
#             the miner cgroup path is captured while the unit still exists.
#   Phase 2 - one `sparehash uninstall` without --purge; the exit status is
#             checked by qa/auxiliary_check.py and only 0 is accepted.
#   Phase 3 - receipt-driven removal: every created unit, the CLI shim, the
#             pruned package tree, the managed XMRig root, and every created
#             identity are gone; retained configuration and the decoy still
#             match their recorded digests; foreign accounts are untouched;
#             the lifecycle latch is retired; the miner cgroup is empty;
#             nothing is left enabled.
#
# Exit code: 0 on success, 1 on failure

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
source "$SCRIPT_DIR/lib/runtime.sh"

AUX="$SCRIPT_DIR/auxiliary_check.py"
UNIT_DIR=/etc/systemd/system
CONTROLLER_UNIT="$UNIT_DIR/sparehash.service"
MINER_UNIT="$UNIT_DIR/sparehash-xmrig.service"
SHIM=/usr/local/bin/sparehash
ETC=/etc/sparehash
IPC_DOC="$(mktemp "${TMPDIR:-/tmp}/sparehash-uninstall-ipc-XXXXXX")"
EXPECT="$(mktemp "${TMPDIR:-/tmp}/sparehash-uninstall-expect-XXXXXX")"
DIGESTS="$(mktemp "${TMPDIR:-/tmp}/sparehash-uninstall-digests-XXXXXX")"
DECOY="$ETC/nonreceipt-qa-$$.txt"
MINER_CGROUP=""

finish() {
    local result=$?
    rm -f -- "$IPC_DOC" "$EXPECT" "$DIGESTS" 2>/dev/null || true
    # The decoy is suite-owned; it is removed only after the retention check.
    rm -f -- "$DECOY" 2>/dev/null || true
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

read_ipc() {
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

# ------------------------------------------------------------------
# Phase 1: everything the uninstall consumes must be here first
# ------------------------------------------------------------------
phase_before() {
    echo "--- Phase 1: pre-action state ---"

    [[ -x $SHIM ]] || die "CLI $SHIM is missing or not executable"
    echo "  CLI present: $SHIM"
    [[ -f $CONTROLLER_UNIT ]] || die "controller unit $CONTROLLER_UNIT is missing"
    [[ -f $MINER_UNIT ]] || die "miner unit $MINER_UNIT is missing"
    echo "  both unit files present"
    [[ -x $SCRIPT_DIR/shims/gamemoded ]] || die "gaming shim $SCRIPT_DIR/shims/gamemoded is missing"
    echo "  gamemoded shim present in the suite"

    read_ipc || die "the controller is not reachable; the live lease cannot be acquired"
    python3 "$AUX" ipc "$IPC_DOC" || die "the controller status document does not satisfy its contract"
    echo "  live controller document accepted; uninstall can acquire the lease"

    printf 'sparehash QA foreign content; must survive uninstall\n' > "$DECOY" \
        || die "cannot write the foreign decoy at $DECOY"
    MINER_CGROUP=$(systemctl show sparehash-xmrig.service -p ControlGroup --value)
    [[ $MINER_CGROUP == /* && $MINER_CGROUP != / ]] \
        || die "miner cgroup path is not observable before uninstall: '$MINER_CGROUP'"
    echo "  foreign decoy written outside the receipt; miner cgroup path captured"

    python3 - "$REPO_DIR" "$EXPECT" "$DIGESTS" "$DECOY" \
        "$CONTROLLER_UNIT" "$MINER_UNIT" "$SHIM" <<'PY' \
        || die "cannot derive removal expectations from the installer receipt (reason above)"
import hashlib
import json
import sys
from pathlib import Path

repository, target, digests, decoy, controller_unit, miner_unit, shim = sys.argv[1:8]
for candidate in (Path("/opt/sparehash/src"), Path(repository)):
    if (candidate / "sparehash" / "xmrig_manifest.py").is_file():
        sys.path.insert(0, str(candidate))
        break
from sparehash import xmrig_manifest
from sparehash.xmrig_remove import PRUNE_DIRS

manifest_path = Path("/var/lib/sparehash/install-manifest.json")
if not manifest_path.is_file():
    raise SystemExit(f"installer receipt {manifest_path} is missing; removal cannot be authoritative")
manifest = xmrig_manifest.parse_manifest(manifest_path.read_text(encoding="utf-8"))
if manifest is None or manifest.installer is None:
    raise SystemExit("installer receipt does not parse; removal cannot be authoritative")
installer = manifest.installer
artifacts = list(installer.artifacts)
recorded = {item.path for item in artifacts}
if decoy in recorded:
    raise SystemExit(f"the decoy path {decoy} is inventoried; it cannot prove foreign retention")
for path in ("/etc/sparehash/config.toml", "/etc/sparehash/xmrig.json"):
    if path not in recorded:
        raise SystemExit(f"retained configuration {path} is not in the receipt")

retained = ["/etc/sparehash/config.toml", "/etc/sparehash/xmrig.json", decoy]
checksums = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in retained}
with open(digests, "w", encoding="utf-8") as sink:
    for path in sorted(checksums):
        sink.write(f"{checksums[path]}  {path}\n")

created = {item.path for item in artifacts if item.created}
expectations = {
    "prune_dirs": sorted(path for path in PRUNE_DIRS if path in created),
    "created_identities": sorted(
        [item.kind, item.name] for item in installer.identities if item.created),
    "units": [controller_unit, miner_unit],
    "shim": shim,
    "has_versions": bool(manifest.versions),
}
Path(target).write_text(json.dumps(expectations, indent=1, sort_keys=True), encoding="utf-8")
print(f"  receipt parsed: {len(recorded)} recorded paths, "
      f"{len(expectations['prune_dirs'])} pruned directories, "
      f"{len(expectations['created_identities'])} created identities")
print(f"  retained digests recorded for {len(checksums)} paths (including the decoy)")
PY
}

# ------------------------------------------------------------------
# Phase 2: the uninstall itself
# ------------------------------------------------------------------
phase_uninstall() {
    echo ""
    echo "--- Phase 2: uninstall ---"
    local status
    set +e
    "$CONTROLLER_BIN" uninstall
    status=$?
    set -e
    python3 "$AUX" action "$status" "sparehash uninstall" 0 \
        || die "sparehash uninstall did not report success"
    echo "  sparehash uninstall acknowledged with exit status 0"
}

# ------------------------------------------------------------------
# Phase 3: receipt-driven removal and foreign retention
# ------------------------------------------------------------------
phase_after() {
    local group=$1
    echo ""
    echo "--- Phase 3: post-uninstall assertions ---"

    [[ ! -e $SHIM ]] || die "CLI $SHIM survived the uninstall although the receipt recorded it"
    [[ ! -e $CONTROLLER_UNIT ]] || die "controller unit survived the uninstall"
    [[ ! -e $MINER_UNIT ]] || die "miner unit survived the uninstall"
    echo "  CLI shim and both unit files removed"

    python3 - "$EXPECT" <<'PY' || die "receipt-owned trees or identities survived the uninstall (reason above)"
import json
import subprocess
import sys
from pathlib import Path

expectations = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
problems: list[str] = []

for path in expectations["prune_dirs"]:
    if Path(path).exists():
        problems.append(f"pruned directory still present: {path}")
if expectations["has_versions"] and Path("/opt/xmrig").exists():
    problems.append("managed XMRig root /opt/xmrig still present after version removal")
if Path(expectations["shim"]).exists():
    problems.append(f"CLI {expectations['shim']} still present")
for unit in expectations["units"]:
    if Path(unit).exists():
        problems.append(f"unit file still present: {unit}")

for kind, name in expectations["created_identities"]:
    command = ("getent", "passwd", name) if kind == "user" else ("getent", "group", name)
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode == 0 and result.stdout.strip():
        problems.append(f"created {kind} {name!r} still present: {result.stdout.strip()}")

for name in ("root", "daemon", "nobody"):
    result = subprocess.run(("getent", "passwd", name), text=True, capture_output=True, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        problems.append(f"foreign account {name!r} was removed")

if problems:
    raise SystemExit("\n".join("  " + problem for problem in problems))
print(f"  {len(expectations['prune_dirs'])} pruned directories and "
      f"{len(expectations['created_identities'])} created identities removed; "
      "managed XMRig root gone; foreign accounts untouched")
PY

    local digest path
    while read -r digest path; do
        [[ -n ${path:-} ]] || continue
        python3 "$AUX" bytes "$path" "$digest" \
            || die "retained file changed or disappeared: $path"
    done < "$DIGESTS"
    echo "  retained configuration and foreign decoy keep their exact bytes"

    python3 - "$group" <<'PY' || die "lifecycle latch, unit state, or miner cgroup is wrong after uninstall (reason above)"
import json
import subprocess
import sys
from pathlib import Path

problems: list[str] = []

latch = Path("/var/lib/sparehash/lifecycle.json")
if not latch.is_file():
    problems.append("lifecycle latch /var/lib/sparehash/lifecycle.json is missing")
else:
    try:
        document = json.loads(latch.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        problems.append(f"lifecycle latch is not JSON: {error}")
    else:
        if document.get("retired") is not True:
            problems.append(f"lifecycle latch is not retired: {document!r}")

for unit in ("sparehash.service", "sparehash-xmrig.service"):
    result = subprocess.run(("/usr/bin/systemctl", "is-enabled", unit),
                            text=True, capture_output=True, check=False)
    state = (result.stdout or result.stderr).strip()
    if state == "enabled":
        problems.append(f"{unit} is still enabled")

group = sys.argv[1]
if group:
    base = Path("/sys/fs/cgroup") / group.lstrip("/")
    if base.exists():
        populated = any(path.read_text().strip() for path in base.rglob("cgroup.procs"))
        if populated:
            problems.append(f"miner cgroup {group} is still populated")

if problems:
    raise SystemExit("\n".join("  " + problem for problem in problems))
print("  lifecycle latch retired, no service enabled, miner cgroup empty")
PY
}

main() {
    echo "=== qa_uninstall.sh: Uninstall QA ==="
    [[ -f $AUX ]] || die "auxiliary checker $AUX is missing"
    [[ -f $ETC/config.toml ]] || die "no installation present; nothing to uninstall"

    if [[ -n ${QA_RUNTIME_OWNER:-} ]]; then
        qa_attach
    else
        qa_setup
    fi

    phase_before
    phase_uninstall
    phase_after "$MINER_CGROUP"

    echo ""
    echo "=== qa_uninstall.sh: Complete ==="
    echo "  Phase 1 (pre-action CLI, units, shim, live controller): PASS"
    echo "  Phase 2 (acknowledged uninstall): PASS"
    echo "  Phase 3 (receipt-driven removal, foreign retention): PASS"
    exit 0
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
