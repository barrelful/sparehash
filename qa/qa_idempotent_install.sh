#!/usr/bin/env bash
#
# qa_idempotent_install.sh - Integration QA for a repeatable install
#
# Destructive: re-runs the installer against the live installation.
#
# The contract this suite checks is that a second install is a *no-op on the
# system*: the same configuration is accepted, the receipt is re-issued with
# the same contents, no identity or unit is duplicated, and the retained
# configuration plus the API token keep their exact bytes.
#
#   Phase 1 - answers resolution: an explicit absolute path outside the
#             repository is honoured; otherwise the live public configuration
#             is copied verbatim to a private temporary file. The answers are
#             parsed as TOML and must carry the required wallet and pool.
#   Phase 2 - one install invocation: exactly one `sparehash install --answers
#             ... --use-existing`, exit status 0. No sudo wrapper, no retry,
#             no lifecycle-latch clearing, and the exit status is checked by
#             qa/auxiliary_check.py rather than being swallowed.
#   Phase 3 - state comparison: file digests, account ids, unit digests and
#             states, the /etc/sparehash inventory, and the parsed installer
#             receipt must be identical before and after.
#
# Exit code: 0 on success, 1 on failure

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
source "$SCRIPT_DIR/lib/runtime.sh"

AUX="$SCRIPT_DIR/auxiliary_check.py"
BEFORE="$(mktemp "${TMPDIR:-/tmp}/sparehash-idem-before-XXXXXX")"
AFTER="$(mktemp "${TMPDIR:-/tmp}/sparehash-idem-after-XXXXXX")"
ANSWER_COPY=""
ANSWERS=""

finish() {
    local result=$?
    rm -f -- "$BEFORE" "$AFTER" 2>/dev/null || true
    [[ -z $ANSWER_COPY ]] || rm -f -- "$ANSWER_COPY" 2>/dev/null || true
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
# Phase 1: answers
# ------------------------------------------------------------------
resolve_answers() {
    local candidate resolved
    if [[ -n ${QA_ANSWERS:-} ]]; then
        candidate=$QA_ANSWERS
    elif [[ -n ${QA_ANSWERS_FILE:-} ]]; then
        candidate=$QA_ANSWERS_FILE
    else
        ANSWER_COPY=$(mktemp "${TMPDIR:-/tmp}/sparehash-idem-answers-XXXXXX.toml")
        cp -- /etc/sparehash/config.toml "$ANSWER_COPY" \
            || die "cannot copy /etc/sparehash/config.toml into a private answers file"
        ANSWERS=$ANSWER_COPY
        echo "  answers: copied verbatim from /etc/sparehash/config.toml to $ANSWERS"
        return 0
    fi
    [[ $candidate == /* ]] || die "answers path must be absolute: $candidate"
    resolved=$(realpath -m -- "$candidate")
    case $resolved in
        "$REPO_DIR"/*) die "answers file must live outside the repository: $resolved" ;;
    esac
    [[ -f $resolved ]] || die "answers file not found: $resolved"
    ANSWERS=$resolved
    echo "  answers: explicit file $ANSWERS"
}

# ------------------------------------------------------------------
# Phase 3: state capture and comparison
# ------------------------------------------------------------------
capture_state() {
    python3 - "$1" "$REPO_DIR" <<'PY'
import hashlib
import json
import os
import pwd
import grp
import sys
from pathlib import Path

ETC = Path("/etc/sparehash")
UNIT_DIRS = (Path("/etc/systemd/system"), Path("/usr/lib/systemd/system"), Path("/lib/systemd/system"))
UNITS = ("sparehash.service", "sparehash-xmrig.service")
ACCOUNTS = ("xmrig", "sparehash")
TRACKED = (
    "/etc/sparehash/config.toml",
    "/etc/sparehash/xmrig.json",
    "/etc/sparehash/xmrig.runtime.json",
    "/usr/local/bin/sparehash",
    "/var/lib/sparehash/install-manifest.json",
)


def digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def identity(path: Path) -> list | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return [oct(info.st_mode & 0o7777), info.st_uid, info.st_gid]


def account(name: str) -> dict | None:
    try:
        entry = pwd.getpwnam(name)
    except KeyError:
        return None
    return {"uid": entry.pw_uid, "gid": entry.pw_gid,
            "groups": sorted(os.getgrouplist(name, entry.pw_gid))}


def group(name: str) -> dict | None:
    try:
        return {"gid": grp.getgrnam(name).gr_gid}
    except KeyError:
        return None


def inventory(root: Path) -> dict:
    records: dict[str, object] = {}
    if not root.is_dir():
        return records
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = str(path.relative_to(root))
        records[relative] = [digest(path) if path.is_file() else None, identity(path)]
    return records


def unit_record(name: str) -> dict:
    found: dict[str, object] = {}
    for directory in UNIT_DIRS:
        path = directory / name
        if path.exists():
            found[str(path)] = digest(path)
    return {"files": found}


def enabled(name: str) -> str:
    import subprocess
    result = subprocess.run(("/usr/bin/systemctl", "is-enabled", name),
                            text=True, capture_output=True, check=False)
    return (result.stdout or result.stderr).strip()


def receipt() -> dict:
    for candidate in (Path("/opt/sparehash/src"), Path(sys.argv[2])):
        if (candidate / "sparehash" / "xmrig_manifest.py").is_file():
            sys.path.insert(0, str(candidate))
            break
    from sparehash import xmrig_manifest
    contents = Path("/var/lib/sparehash/install-manifest.json").read_text(encoding="utf-8")
    manifest = xmrig_manifest.parse_manifest(contents)
    if manifest is None:
        raise SystemExit("install-manifest.json does not parse; capture refused")
    installer = manifest.installer
    if installer is None:
        raise SystemExit("install-manifest.json carries no installer receipt; capture refused")
    return {
        "phase": installer.phase,
        "identities": sorted(
            [item.kind, item.name, getattr(item, "uid", -1), item.gid, item.created]
            for item in installer.identities),
        "memberships": sorted(
            [item.user, item.group, item.gid, item.added]
            for item in installer.memberships),
        "artifacts": sorted(
            [item.id, item.path, item.kind, item.created, item.uid, item.gid,
             oct(item.mode), item.sha256 or ""]
            for item in installer.artifacts),
        "versions": sorted([item.version, item.source] for item in manifest.versions),
    }


document = {
    "files": {path: digest(Path(path)) for path in TRACKED},
    "modes": {path: identity(Path(path)) for path in TRACKED},
    "accounts": {name: account(name) for name in ACCOUNTS},
    "groups": {name: group(name) for name in ACCOUNTS},
    "units": {name: unit_record(name) for name in UNITS},
    "unit_state": {name: enabled(name) for name in UNITS},
    "package": inventory(Path("/opt/sparehash")),
    "etc": inventory(ETC),
    "receipt": receipt(),
}
Path(sys.argv[1]).write_text(json.dumps(document, indent=1, sort_keys=True), encoding="utf-8")
PY
}

compare_state() {
    python3 - "$1" "$2" <<'PY'
import json
import sys
from pathlib import Path


def differences(left, right, prefix=""):
    if type(left) is not type(right):
        return [f"{prefix}: type {type(left).__name__} != {type(right).__name__}"]
    if isinstance(left, dict):
        found = []
        for key in sorted(set(left) | set(right)):
            path = f"{prefix}.{key}" if prefix else key
            if key not in left:
                found.append(f"{path}: absent before")
            elif key not in right:
                found.append(f"{path}: absent after")
            else:
                found.extend(differences(left[key], right[key], path))
        return found
    if isinstance(left, list):
        if len(left) != len(right):
            return [f"{prefix}: length {len(left)} != {len(right)}"]
        found = []
        for index, (one, other) in enumerate(zip(left, right)):
            found.extend(differences(one, other, f"{prefix}[{index}]"))
        return found
    return [] if left == right else [f"{prefix}: {left!r} != {right!r}"]


before = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
after = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
found = differences(before, after)
if not found:
    print("  every captured field is identical before and after the re-install")
    raise SystemExit(0)
for line in found:
    print(f"  changed: {line}")
raise SystemExit(1)
PY
}

# ------------------------------------------------------------------
main() {
    echo "=== qa_idempotent_install.sh: Idempotent Install QA ==="
    [[ -f $AUX ]] || die "auxiliary checker $AUX is missing"
    [[ -x $CONTROLLER_BIN ]] || die "control CLI $CONTROLLER_BIN is missing or not executable"
    [[ -f /etc/sparehash/config.toml ]] || die "no installation present; nothing to re-install"

    if [[ -n ${QA_RUNTIME_OWNER:-} ]]; then
        qa_attach
    else
        qa_setup
    fi

    local status
    echo "--- Phase 1: answers resolution ---"
    resolve_answers
    python3 - "$ANSWERS" <<'PY' || die "answers file does not carry the required wallet and pool"
import sys
import tomllib
from pathlib import Path

document = tomllib.loads(Path(sys.argv[1]).read_bytes().decode("utf-8"))
if not isinstance(document.get("wallet"), str) or not document["wallet"]:
    raise SystemExit("answers must carry a wallet")
pool = document.get("pool")
if not isinstance(pool, dict) or not isinstance(pool.get("host"), str) or not pool["host"]:
    raise SystemExit("answers must carry pool.host")
print(f"  answers parse as TOML with wallet and pool.host={pool['host']!r}")
PY

    echo "--- Phase 2: single install invocation ---"
    capture_state "$BEFORE"
    echo "  state captured before the re-install"
    set +e
    "$CONTROLLER_BIN" install --answers "$answers" --use-existing
    status=$?
    set -e
    python3 "$AUX" action "$status" "sparehash install --answers ... --use-existing" 0 \
        || die "the re-install did not report success"
    echo "  re-install acknowledged with exit status 0"

    echo "--- Phase 3: state comparison ---"
    capture_state "$AFTER"
    compare_state "$BEFORE" "$AFTER" \
        || die "the re-install changed system state; idempotency broken (changes listed above)"

    python3 - <<'PY' || die "post-install unit contract broken after the re-install"
import subprocess
import sys

def enabled(name: str) -> str:
    result = subprocess.run(("/usr/bin/systemctl", "is-enabled", name),
                            text=True, capture_output=True, check=False)
    return (result.stdout or result.stderr).strip()

controller = enabled("sparehash.service")
miner = enabled("sparehash-xmrig.service")
if controller != "enabled":
    raise SystemExit(f"controller must stay enabled, observed {controller!r}")
if miner != "static":
    raise SystemExit(f"miner must stay static, observed {miner!r}")
print("  controller enabled, miner static, as the install contract requires")
PY

    python3 - "$REPO_DIR" <<'PY' || die "no lifecycle latch may be written by a successful install"
import sys
from pathlib import Path

for candidate in (Path("/opt/sparehash/src"), Path(sys.argv[1])):
    if (candidate / "sparehash" / "lifecycle.py").is_file():
        sys.path.insert(0, str(candidate))
        break
from sparehash import lifecycle

if lifecycle.is_retired():
    raise SystemExit("the lifecycle latch is set after a successful install")
print("  lifecycle latch clear; the system is not marked as uninstalled")
PY

    echo ""
    echo "=== qa_idempotent_install.sh: Complete ==="
    echo "  Phase 1 (answers resolution): PASS"
    echo "  Phase 2 (single acknowledged install): PASS"
    echo "  Phase 3 (identical state after re-install): PASS"
    exit 0
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
