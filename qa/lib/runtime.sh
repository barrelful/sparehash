#!/usr/bin/env bash
# Shared VM-only fixture ownership; never mount over host sysfs or binaries.
QA_ROOT=/run/sparehash-qa
# Shims are executable binaries in the controller/miner namespaces, so their
# bind sources must live on a filesystem mounted with exec. /run is typically
# noexec; keep shim copies under the persistent state directory instead.
QA_SHIMS_DIR=${QA_SHIMS_DIR:-/var/lib/sparehash/qa-shims}
QA_DROPIN=/run/systemd/system/sparehash.service.d/90-sparehash-qa.conf
QA_MINER_DROPIN=/run/systemd/system/sparehash-xmrig.service.d/90-sparehash-qa.conf
QA_ETC_DIR=${QA_ETC_DIR:-/etc/sparehash}
CONTROLLER_BIN=${CONTROLLER_BIN:-/usr/local/bin/sparehash}
# Suites that must observe the controller's own namespace view (fixture-bound
# /etc files, gamemoded shim, /run/user). The runner enters that namespace with
# nsenter; the view survives controller stop/start because the suite process
# holds the mount namespace open, and nsenter passes the caller environment.
QA_NS_SUITES=("qa_gaming.sh" "qa_contention.sh" "qa_controller_lifecycle.sh")

qa_preflight() {
    [[ ${SPAREHASH_QA_DISPOSABLE_VM:-} == YES ]] || {
        echo 'FAIL: set SPAREHASH_QA_DISPOSABLE_VM=YES only inside a disposable VM' >&2
        return 1
    }
    [[ $EUID == 0 && -x $CONTROLLER_BIN ]]
    [[ $(cat /proc/1/comm) == systemd ]]
    command -v nsenter >/dev/null
}

qa_quiesce() {
    # Never let a pre-existing controller mine before the local pool and the
    # zero-donation fixtures are in place.
    if systemctl is-active --quiet sparehash.service; then
        echo 'quiesce: stopping a pre-existing controller before fixture setup'
        "$CONTROLLER_BIN" stop
    fi
}

qa_setup() {
    qa_preflight
    # Exclusive creation rejects prior/foreign fixtures, including symlinks.
    mkdir -m 0700 "$QA_ROOT"
    QA_OWNS_RUNTIME=1
    export QA_RUNTIME_OWNER=$$
    printf '%s\n' "$QA_RUNTIME_OWNER" > "$QA_ROOT/owner"
    RUN_DIR=$(mktemp -d "$QA_ROOT/run-XXXXXXXX")
    mkdir "$QA_ROOT/power" "$QA_ROOT/hwmon"
    mkdir "$QA_ROOT/hwmon/hwmon0"
    printf 'coretemp\n' > "$QA_ROOT/hwmon/hwmon0/name"
    printf 'Package id 0\n' > "$QA_ROOT/hwmon/hwmon0/temp1_label"
    printf '40000\n' > "$QA_ROOT/hwmon/hwmon0/temp1_input"
    # Keep executable shim bind sources off /run, which is usually noexec.
    rm -rf "$QA_SHIMS_DIR"
    mkdir -m 0755 "$QA_SHIMS_DIR"
    cp "$SCRIPT_DIR/shims/gdbus" "$QA_SHIMS_DIR/gdbus"
    cp "$SCRIPT_DIR/shims/busctl" "$QA_SHIMS_DIR/busctl"
    # Root-owned 0755 gamemoded copy: the controller resolves the helper in its
    # own namespace and rejects anything not root-owned without group/world
    # write bits, so the bind source (not the repository path) must qualify.
    cp "$SCRIPT_DIR/shims/gamemoded" "$QA_SHIMS_DIR/gamemoded"
    chmod 0755 "$QA_SHIMS_DIR/gdbus" "$QA_SHIMS_DIR/busctl" "$QA_SHIMS_DIR/gamemoded"
    qa_power_files 0 Full
    qa_upower false true
    [[ ! -e $QA_DROPIN && ! -L $QA_DROPIN ]]
    mkdir -p "$(dirname "$QA_DROPIN")"
    QA_OWNS_DROPIN=1
    cat > "$QA_DROPIN" <<EOF
# SpareHash disposable VM QA: owned by $QA_RUNTIME_OWNER
[Service]
PrivateTmp=yes
BindReadOnlyPaths=$QA_ROOT/power:/sys/class/power_supply
BindReadOnlyPaths=$QA_ROOT/hwmon:/sys/class/hwmon
BindReadOnlyPaths=$QA_SHIMS_DIR/gdbus:/usr/bin/gdbus
BindReadOnlyPaths=$QA_SHIMS_DIR/busctl:/usr/bin/busctl
EOF
    systemctl daemon-reload
}

qa_fixture_overlays() {
    # Derive TEST-ONLY copies from the installed bytes (the receipt files stay
    # untouched), then bind them over the exact runtime paths inside the
    # controller and miner namespaces. Called after installer acceptance so
    # the installer always observes the host namespace and receipt configs.
    [[ ${QA_OWNS_RUNTIME:-0} == 1 && -n ${RUN_DIR:-} && -d $RUN_DIR ]] || {
        echo 'FAIL: fixture overlays require a QA-owned runtime' >&2
        return 1
    }
    [[ ${QA_OVERLAYS_DONE:-0} == 0 ]] || return 0
    local users_root=${QA_USERS_ROOT:-$QA_ROOT/users}
    echo "=== QA fixture overlays (TEST-ONLY; installed bytes preserved) ==="
    python3 "$SCRIPT_DIR/fixture_setup.py" --qa-root "$QA_ROOT" --etc "$QA_ETC_DIR"
    qa_gamemode_state inactive
    # A bound UNIX socket file (not a plain path): the gaming provider lstat()s
    # /run/user/<uid>/bus and requires S_ISSOCK before it runs gamemoded -s.
    # Only the controller namespace sees this tree; the file persists after the
    # creating process exits, and nobody ever connects to it (the shim does not
    # speak D-Bus).
    mkdir -p "$users_root/0"
    python3 - "$users_root/0/bus" <<'PY'
import os
import socket
import stat
import sys
from pathlib import Path

path = Path(sys.argv[1])
if path.exists() and stat.S_ISSOCK(path.lstat().st_mode):
    raise SystemExit(0)
path.unlink(missing_ok=True)
holder = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
holder.bind(str(path))
holder.close()  # the socket file itself is the fixture and stays behind
if os.geteuid() == 0:
    os.chmod(path, 0o666)  # world-connectable in the disposable namespace
PY
    # Exclusive: reject any foreign miner drop-in before we claim ownership.
    [[ ! -e $QA_MINER_DROPIN && ! -L $QA_MINER_DROPIN ]]
    mkdir -p "$(dirname "$QA_MINER_DROPIN")"
    QA_OWNS_MINER_DROPIN=1
    # Controller namespace: fixture config for both guards, zero-donation miner
    # JSONs at the exact installed paths, the root-only gamemoded shim, and the
    # bound session bus tree. Destination files may be missing (e.g. a host
    # without GameMode); systemd creates them for the namespace.
    cat > "$QA_DROPIN" <<EOF
# SpareHash disposable VM QA: owned by ${QA_RUNTIME_OWNER:-unspecified}
[Service]
PrivateTmp=yes
BindReadOnlyPaths=$QA_ROOT/power:/sys/class/power_supply
BindReadOnlyPaths=$QA_ROOT/hwmon:/sys/class/hwmon
BindReadOnlyPaths=$QA_SHIMS_DIR/gdbus:/usr/bin/gdbus
BindReadOnlyPaths=$QA_SHIMS_DIR/busctl:/usr/bin/busctl
BindReadOnlyPaths=$QA_SHIMS_DIR/gamemoded:/usr/bin/gamemoded
BindReadOnlyPaths=$users_root:/run/user
BindReadOnlyPaths=$QA_ROOT/config.test.toml:/etc/sparehash/config.toml
BindReadOnlyPaths=$QA_ROOT/xmrig.zero.json:/etc/sparehash/xmrig.json
BindReadOnlyPaths=$QA_ROOT/xmrig.zero.runtime.json:/etc/sparehash/xmrig.runtime.json
EOF
    # Miner namespace: the manager reads LoadCredential sources in the HOST
    # namespace, so the credential must be repointed at the fixture (a plain
    # bind cannot redirect it); the wrapper fallback path is bound as well.
    cat > "$QA_MINER_DROPIN" <<EOF
# SpareHash disposable VM QA: owned by ${QA_RUNTIME_OWNER:-unspecified}
[Service]
LoadCredential=
LoadCredential=xmrig.json:$QA_ROOT/xmrig.zero.json
BindReadOnlyPaths=$QA_ROOT/xmrig.zero.runtime.json:/etc/sparehash/xmrig.runtime.json
EOF
    QA_OVERLAYS_DONE=1
    export QA_OVERLAYS_DONE
    systemctl daemon-reload
    echo "  controller drop-in: $QA_DROPIN"
    echo "  miner drop-in: $QA_MINER_DROPIN"
    echo "  gamemoded fixture: ${QA_GAMEMODE_FIXTURE:-$QA_ROOT/gamemoded.json} (root-only, default inactive)"
    echo "  session bus fixture: $users_root/0/bus (bound in controller namespace only)"
}

qa_gamemode_state() {
    # qa_gamemode_state active|inactive: atomically publish the root-only
    # gamemoded fixture the shim reads (default inactive when absent). The
    # path is derived here so suites may relocate QA_ROOT after sourcing.
    case $1 in
        active | inactive) ;;
        *)
            echo "FAIL: qa_gamemode_state expects active|inactive, got '$1'" >&2
            return 1
            ;;
    esac
    local fixture=${QA_GAMEMODE_FIXTURE:-$QA_ROOT/gamemoded.json}
    python3 - "$fixture" "$1" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
active = sys.argv[2] == "active"
path.parent.mkdir(parents=True, exist_ok=True)
temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
with temporary.open("w", encoding="utf-8") as sink:
    json.dump({"active": active,
               "status": "gamemode is active" if active else "gamemode is inactive"},
              sink, indent=2, sort_keys=True)
    sink.write("\n")
    sink.flush()
    os.fsync(sink.fileno())
os.chmod(temporary, 0o600)
if os.geteuid() == 0:
    os.chown(temporary, 0, 0)
os.replace(temporary, path)
PY
}

qa_pool_start() {
    # Bounded local-only workload: synthetic rx/0 work on 127.0.0.1:3333 so
    # the real miner hashes without ever reaching a public pool. Readiness is
    # observed (stdout line or a socket probe), never slept for.
    [[ -f $SCRIPT_DIR/local_pool.py ]] || {
        echo 'FAIL: qa/local_pool.py missing' >&2
        return 1
    }
    local log=${QA_POOL_LOG:-$QA_ROOT/local_pool.log}
    python3 "$SCRIPT_DIR/local_pool.py" 1800 >"$log" 2>&1 &
    QA_POOL_PID=$!
    export QA_POOL_PID
    printf '%s\n' "$QA_POOL_PID" > "$QA_ROOT/local_pool.pid"
    python3 - "$QA_POOL_PID" "$log" <<'PY'
import os
import socket
import sys
import time

pid, log = int(sys.argv[1]), sys.argv[2]
deadline = time.monotonic() + 10


def alive() -> bool:
    try:
        return b"local_pool.py" in open(f"/proc/{pid}/cmdline", "rb").read()
    except OSError:
        return False


while time.monotonic() < deadline:
    try:
        with open(log, encoding="utf-8", errors="replace") as source:
            if "LOCAL ONLY fixture ready" in source.read():
                break
    except OSError:
        pass
    if not alive():
        raise SystemExit(f"FAIL: local_pool.py exited before readiness (see {log})")
    time.sleep(0.05)
else:
    raise SystemExit(f"FAIL: local_pool.py did not report readiness within 10s (see {log})")
print(f"local pool ready (pid {pid})", flush=True)
PY
}

qa_pool_stop() {
    # Only the recorded child, verified by cmdline, is ever signalled: never
    # pkill, never a pattern match on other processes.
    [[ -n ${QA_POOL_PID:-} ]] || return 0
    local pid=$QA_POOL_PID attempt
    QA_POOL_PID=
    if [[ -r /proc/$pid/cmdline ]] && tr '\0' ' ' < "/proc/$pid/cmdline" | grep -q 'local_pool\.py'; then
        kill -TERM "$pid" 2>/dev/null || true
        for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
            kill -0 "$pid" 2>/dev/null || return 0
            sleep 0.1
        done
        kill -KILL "$pid" 2>/dev/null || true
        for attempt in 1 2 3 4 5 6 7 8 9 10; do
            kill -0 "$pid" 2>/dev/null || return 0
            sleep 0.1
        done
        echo "FAIL: local pool pid $pid survived TERM and KILL" >&2
        return 1
    fi
    return 0
}

qa_attach() {
    qa_preflight
    [[ -n ${QA_RUNTIME_OWNER:-} && ! -L $QA_ROOT && $(stat -c '%u:%a' "$QA_ROOT") == 0:700 ]]
    [[ $(cat "$QA_ROOT/owner") == "$QA_RUNTIME_OWNER" ]]
}

qa_cleanup() {
    local result=$?
    trap - EXIT
    set +e
    if [[ ${QA_OWNS_RUNTIME:-0} == 1 ]]; then
        if [[ -x $CONTROLLER_BIN ]]; then
            "$CONTROLLER_BIN" stop || {
                echo 'FAIL: cleanup stop failed; retaining mounted fixtures for diagnosis' >&2
                [[ $result != 0 ]] || result=1
                exit "$result"
            }
        fi
        qa_pool_stop || { [[ $result != 0 ]] || result=1; }
        if [[ ${QA_OWNS_DROPIN:-0} == 1 ]]; then
            rm -- "$QA_DROPIN" || { [[ $result != 0 ]] || result=1; }
        fi
        if [[ ${QA_OWNS_MINER_DROPIN:-0} == 1 ]]; then
            rm -- "$QA_MINER_DROPIN" || { [[ $result != 0 ]] || result=1; }
        fi
        if [[ ${QA_OWNS_DROPIN:-0} == 1 || ${QA_OWNS_MINER_DROPIN:-0} == 1 ]]; then
            systemctl daemon-reload || { [[ $result != 0 ]] || result=1; }
        fi
        rm -rf -- "$QA_ROOT" || { [[ $result != 0 ]] || result=1; }
        rm -rf -- "$QA_SHIMS_DIR" || { [[ $result != 0 ]] || result=1; }
    fi
    exit "$result"
}

qa_power_files() {
    # Keep the mounted root and device directory inodes stable across cases.
    # Optional third argument models AC availability; default preserves callers.
    mkdir -p "$QA_ROOT/power/AC" "$QA_ROOT/power/BAT0"
    printf 'Mains\n' > "$QA_ROOT/power/AC/type"
    printf '%s\n' "${3:-1}" > "$QA_ROOT/power/AC/online"
    printf 'Battery\n' > "$QA_ROOT/power/BAT0/type"
    printf '%s\n' "$1" > "$QA_ROOT/power/BAT0/present"
    printf '%s\n' "$2" > "$QA_ROOT/power/BAT0/status"
}

qa_upower() {
    python3 - "$QA_ROOT/upower.json" "$1" "$2" <<'PY'
import json
import os
import sys
import time
from pathlib import Path
path = Path(sys.argv[1])
data = {"available": sys.argv[3] == "true", "generation": time.monotonic_ns(),
        "org.freedesktop.UPower": {"OnBattery": sys.argv[2] == "true"}}
temporary = path.with_suffix(".new")
temporary.write_text(json.dumps(data), encoding="ascii")
os.replace(temporary, path)
PY
}

qa_restart() {
    "$CONTROLLER_BIN" stop
    "$CONTROLLER_BIN" start
}

qa_status() {
    local pid
    pid=$(systemctl show sparehash.service -p MainPID --value)
    [[ $pid =~ ^[1-9][0-9]*$ ]]
    nsenter --target "$pid" --mount -- "$CONTROLLER_BIN" status --json
}

qa_wait_miner() {
    # Deadline includes status calls and the recursive cgroup assertion.
    python3 - "$1" "$2" <<'PY'
import subprocess
import os
import sys
import time
from pathlib import Path
want, seconds = sys.argv[1], float(sys.argv[2])
deadline = float(os.environ["QA_STOP_DEADLINE"]) if want == "inactive" else time.monotonic() + seconds
while time.monotonic() < deadline:
    result = subprocess.run(["systemctl", "show", "sparehash-xmrig.service",
                             "-p", "ActiveState", "-p", "ControlGroup"],
                            text=True, capture_output=True, check=True, timeout=1)
    values = dict(line.split("=", 1) for line in result.stdout.splitlines())
    group = values["ControlGroup"]
    groups = {group, os.environ.get("QA_STOP_CGROUP", "")}
    paths = [path for name in groups if name
             for path in (Path("/sys/fs/cgroup") / name.lstrip("/")).rglob("cgroup.procs")]
    populated = any(path.read_text().strip() for path in paths)
    good = (values["ActiveState"] == "active" and bool(group) and populated) if want == "active" else (
        values["ActiveState"] == "inactive" and not populated)
    if good and time.monotonic() <= deadline:
        print(f"miner {want}; cgroup populated={populated}", flush=True)
        break
    time.sleep(min(0.05, max(0, deadline - time.monotonic())))
else:
    sys.exit(f"FAIL: miner/cgroup did not become {want} within {seconds}s")
PY
    unset QA_STOP_DEADLINE QA_STOP_CGROUP
}

qa_stop_deadline() {
    # qa_stop_deadline [SECONDS]: anchor an inactive-wait deadline at SECONDS
    # from now (5 by default: the safety deadline for event-driven stops; the
    # provider path passes a documented reconcile-window budget instead).
    local seconds=${1:-5}
    QA_STOP_DEADLINE=$(python3 -c "import time; print(time.monotonic() + $seconds)")
    QA_STOP_CGROUP=$(systemctl show sparehash-xmrig.service -p ControlGroup --value)
    [[ $QA_STOP_CGROUP == /* && $QA_STOP_CGROUP != / ]]
    export QA_STOP_DEADLINE QA_STOP_CGROUP
}

qa_assert_power() {
    qa_status > "$QA_ROOT/status.json"
    cat "$QA_ROOT/status.json"
    python3 - "$QA_ROOT/status.json" "$1" "$2" "$3" <<'PY'
import json
import sys
from pathlib import Path
data = json.loads(Path(sys.argv[1]).read_text())
expected = sys.argv[2] == "true"
assert data["power"]["inventory"]["value"] is expected, data
assert data["power"]["upower"]["value"] == json.loads(sys.argv[3]), data
assert data["power"]["sysfs"]["value"] == json.loads(sys.argv[4]), data
assert data["power"]["inventory"]["stale"] is False, data
blocked = expected and (data["power"]["upower"]["value"] is True or data["power"]["sysfs"]["value"] is True)
assert ("battery" in dict(data["blockers"])) is blocked, data
assert (data["miner"] == "stopped") is blocked, data
PY
}
