#!/usr/bin/env bash
# Eight sequential acceptance checkpoints. DESTRUCTIVE: disposable systemd VM only.
set -Eeuo pipefail
if [[ ${SPAREHASH_QA_DISPOSABLE_VM:-} != YES ]]; then
    echo 'FAIL: SPAREHASH_QA_DISPOSABLE_VM=YES is required (disposable VM only)' >&2
    exit 1
fi
SCRIPT_DIR=$(realpath "$(dirname "${BASH_SOURCE[0]}")")
REPO=$(dirname "$SCRIPT_DIR")
source "$SCRIPT_DIR/lib/runtime.sh"
qa_preflight() {
    [[ ${SPAREHASH_QA_DISPOSABLE_VM:-} == YES ]] || {
        echo 'FAIL: set SPAREHASH_QA_DISPOSABLE_VM=YES only inside a disposable VM' >&2
        return 1
    }
    [[ $EUID == 0 ]]
    [[ $(cat /proc/1/comm) == systemd ]]
    command -v nsenter >/dev/null
}
qa_preflight
[[ ${CANDIDATE_SHA:-} =~ ^[0-9a-f]{40}$ ]]
# An extracted sdist needs the independently transferred source identity beside it.
if [[ -e $REPO/.git ]]; then
    [[ $(GIT_MASTER=1 git -C "$REPO" rev-parse HEAD) == "$CANDIDATE_SHA" ]]
    [[ -z $(GIT_MASTER=1 git -C "$REPO" status --porcelain --untracked-files=no) ]]
else
    [[ $(cat "$REPO/candidate.sha") == "$CANDIDATE_SHA" ]]
fi
EVIDENCE_DIR=${EVIDENCE_DIR:-/var/tmp/sparehash-v010-evidence}
mkdir -m 0700 "$EVIDENCE_DIR" # refuse an existing evidence directory
export EVIDENCE_DIR CANDIDATE_SHA
exec > >(tee "$EVIDENCE_DIR/vm-smoke.log") 2>&1
CHECKPOINT=PREFLIGHT
printf '' > "$EVIDENCE_DIR/checkpoints.tsv"
printf '' > "$EVIDENCE_DIR/hashes.jsonl"
begin() { CHECKPOINT=$1; echo "=== $CHECKPOINT ==="; }
pass() { printf '%s\tPASS\n' "$CHECKPOINT" >> "$EVIDENCE_DIR/checkpoints.tsv"; }
trap 'printf "%s\tFAIL\n" "$CHECKPOINT" >> "$EVIDENCE_DIR/checkpoints.tsv"; echo "FAIL: $CHECKPOINT line $LINENO" >&2' ERR
finish() {
    local result=$? stopped=0
    trap - EXIT ERR
    set +e
    # Never remove fixture mounts or stop their pool before both services stop.
    if [[ -x $CONTROLLER_BIN ]]; then
        "$CONTROLLER_BIN" stop && stopped=1
    else
        systemctl stop sparehash.service sparehash-xmrig.service
        [[ ${UNINSTALLED:-0} == 1 ]] && stopped=1
    fi
    journalctl -u sparehash.service -u sparehash-xmrig.service --no-pager > "$EVIDENCE_DIR/journal.log"
    [[ -f $QA_ROOT/local_pool.log ]] && cp "$QA_ROOT/local_pool.log" "$EVIDENCE_DIR/local_pool.log"
    [[ $stopped == 1 ]] || { result=1; echo 'FAIL: cleanup stop; fixtures retained'; }
    if [[ $stopped == 1 ]]; then
        qa_pool_stop || result=1
        if [[ ${QA_OWNS_DROPIN:-0} == 1 ]]; then rm -- "$QA_DROPIN" || result=1; fi
        if [[ ${QA_OWNS_MINER_DROPIN:-0} == 1 ]]; then rm -- "$QA_MINER_DROPIN" || result=1; fi
        systemctl daemon-reload || result=1
        if [[ ${QA_OWNS_RUNTIME:-0} == 1 ]]; then rm -rf -- "$QA_ROOT" "$QA_SHIMS_DIR" || result=1; fi
    fi
    python3 - "$result" "$stopped" <<'PY'
import json, os, platform, subprocess, sys
from pathlib import Path
e = Path(os.environ["EVIDENCE_DIR"])
names = ("INSTALL", "BASELINE", "PAUSE/RESUME", "BATTERY", "THERMAL", "GAMING", "CONTROLLER LOSS", "OFFLINE UNINSTALL")
checks = dict(line.split("\t") for line in (e / "checkpoints.tsv").read_text().splitlines())
removal = json.loads((e / "removal.json").read_text()) if (e / "removal.json").exists() else None
(e / "vm-summary.json").write_text(json.dumps({
    "candidate_sha": os.environ["CANDIDATE_SHA"], "exit_code": int(sys.argv[1]),
    "environment": {"host": platform.node(), "kernel": platform.release(), "python": platform.python_version(),
                    "os_release": Path("/etc/os-release").read_text(),
                    "systemd": subprocess.check_output(["systemctl", "--version"], text=True)},
    "checkpoints": {name: checks.get(name, "NOT_RUN") for name in names},
    "hash_counts": [json.loads(line) for line in (e / "hashes.jsonl").read_text().splitlines()],
    "cgroup_retirement_removal": removal, "cleanup_services_stopped": sys.argv[2] == "1",
}, indent=2) + "\n")
PY
    [[ $? == 0 ]] || result=1
    exit "$result"
}
trap finish EXIT
# Helpers execute against INSTALLED imports, never checkout production modules.
life() { python3 -c 'import runpy,sys; sys.path.insert(0,"/opt/sparehash/src"); p=sys.argv.pop(1); runpy.run_path(p,run_name="__main__")' "$SCRIPT_DIR/lifecycle_check.py" "$@"; }
hashing() {
    local deadline
    deadline=$(python3 -c 'import time; print(time.monotonic()+180)')
    qa_wait_miner active 180
    python3 -c '
import json,os,pwd,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,"/opt/sparehash/src")
sys.path.append(sys.argv[1])
from sparehash.xmrig_config import ApiError,read_token,summary
from contention import Workload
from lifecycle_check import prop,tasks,process_birth
raw=json.loads(Path("/etc/sparehash/xmrig.json").read_text())
assert raw["http"]["host"] == "127.0.0.1" and raw["http"]["enabled"] is True
work=Workload(raw["http"]["port"],read_token())
pid=int(prop("sparehash-xmrig.service","MainPID")); birth=process_birth(pid)
group=prop("sparehash-xmrig.service","ControlGroup")
assert pid>0 and birth and group.startswith("/") and group != "/"
assert pid in tasks(Path("/sys/fs/cgroup")/group.lstrip("/"))
uids=next(line.split()[1:] for line in Path(f"/proc/{pid}/status").read_text().splitlines() if line.startswith("Uid:"))
assert all(int(uid)==pwd.getpwnam("xmrig").pw_uid and int(uid)>0 for uid in uids)
listeners=subprocess.check_output(["ss","-ltnp"],text=True)
api=[line.split()[3] for line in listeners.splitlines() if f"pid={pid}," in line]
assert api and all(address == f"127.0.0.1:{work.port}" for address in api)
deadline=min(time.monotonic()+120,float(sys.argv[3])); before=None
while time.monotonic()<deadline:
    assert int(prop("sparehash-xmrig.service","MainPID"))==pid and process_birth(pid)==birth
    try:
        total=work.hashes()
    except ApiError:
        time.sleep(.2); continue
    if before is not None and total>before:
        with (Path(os.environ["EVIDENCE_DIR"])/"hashes.jsonl").open("a") as out:
            out.write(json.dumps({"checkpoint":sys.argv[2],"pid":pid,"birth":birth,"before":before,"after":total,"at":time.time(),"cgroup":group})+"\n")
        print(f"hash progress pid={pid} {before}->{total}"); break
    if before is None: before=total
    time.sleep(.5)
else: raise SystemExit("FAIL: no same-PID hash progress within 120s")
' "$SCRIPT_DIR" "$CHECKPOINT" "$deadline"
}
blocked() { qa_wait_miner inactive "$2"; life blocked "$1" 1; }

begin INSTALL
ANSWERS=$(mktemp /var/tmp/sparehash-release-answers-XXXXXXXX.toml)
python3 - "$SCRIPT_DIR/fixtures/answers.toml" "$ANSWERS" <<'PY'
import sys
from pathlib import Path
source = Path(sys.argv[1]).read_text().replace("REPLACE_WITH_YOUR_MONERO_WALLET_ADDRESS", "4" + "1" * 94)
Path(sys.argv[2]).write_text(source.replace("[gaming]\nenabled = true", "[gaming]\nenabled = false"))
PY
"$REPO/install.sh" --answers "$ANSWERS"
rm -- "$ANSWERS"
[[ $(systemctl is-enabled sparehash.service) == enabled ]]
[[ $(systemctl show sparehash.service -p ActiveState --value) == inactive ]]
[[ $(systemctl is-enabled sparehash-xmrig.service) == static ]]
python3 - "$REPO" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, "/opt/sparehash/src")
from sparehash.xmrig_install import current_binary
source, installed = Path(sys.argv[1]), Path("/opt/sparehash/src")
for directory, pattern in (("sparehash", "*.py"), ("systemd", "*.service.in")):
    wanted = {p.name: p.read_bytes() for p in (source / directory).glob(pattern)}
    actual = {p.name: p.read_bytes() for p in (installed / directory).glob(pattern)}
    assert wanted and wanted == actual, f"candidate byte mismatch: {directory}"
binary = current_binary()
assert binary is not None and binary.is_file(), "missing verified receipt-authorized XMRig"
print("installed package/templates byte-equal; verified managed XMRig present")
PY
pass

begin BASELINE
[[ -d /opt/sparehash/src/sparehash && -x $CONTROLLER_BIN ]] || { echo 'FAIL: installed payload missing after install' >&2; exit 1; }
qa_quiesce; qa_setup; qa_pool_start; qa_fixture_overlays
python3 - "$QA_ROOT" <<'PY'
import json, sys
from pathlib import Path
for name in ("xmrig.zero.json", "xmrig.zero.runtime.json"):
    data = json.loads((Path(sys.argv[1]) / name).read_text())
    assert data["donate-level"] == 0 and data["pools"]
    assert all(p["url"] == "127.0.0.1:3333" and p["tls"] is False for p in data["pools"])
PY
qa_restart; hashing; pass

begin PAUSE/RESUME
qa_stop_deadline; "$CONTROLLER_BIN" pause; blocked manual 5
"$CONTROLLER_BIN" resume; hashing; pass

begin BATTERY
qa_stop_deadline; qa_power_files 0 Discharging; qa_upower true true; blocked battery 5
"$CONTROLLER_BIN" pause
qa_power_files 0 Full; qa_upower false true
python3 -c 'import sys; sys.path.insert(0,"/opt/sparehash/src"); sys.path.append(sys.argv[1]); from lifecycle_check import wait_until,status; wait_until(lambda: "battery" not in status().blockers,10)' "$SCRIPT_DIR"
life blocked manual 5; life hold manual
"$CONTROLLER_BIN" resume; hashing; pass

begin THERMAL
qa_stop_deadline 10; printf '86000\n' > "$QA_ROOT/hwmon/hwmon0/temp1_input"; blocked thermal 10
printf '80000\n' > "$QA_ROOT/hwmon/hwmon0/temp1_input"; life hold thermal
printf '74000\n' > "$QA_ROOT/hwmon/hwmon0/temp1_input"; hashing
rm -- "$QA_ROOT/hwmon/hwmon0/temp1_input"
qa_restart; life blocked thermal 10; life hold thermal
printf '74000\n' > "$QA_ROOT/hwmon/hwmon0/temp1_input"
qa_restart; hashing; pass

begin GAMING
qa_stop_deadline 20; qa_gamemode_state active; blocked gaming 20
qa_gamemode_state inactive; hashing
qa_stop_deadline; "$CONTROLLER_BIN" gaming-start; blocked gaming 5
"$CONTROLLER_BIN" gaming-end; hashing; pass

begin 'CONTROLLER LOSS'
life loss; hashing; pass

begin 'OFFLINE UNINSTALL'
GROUP=$(systemctl show sparehash-xmrig.service -p ControlGroup --value)
[[ $GROUP == /* && $GROUP != / ]]
SENTINEL=/var/lib/sparehash/foreign-release-smoke.txt
(set -o noclobber; printf 'foreign release smoke sentinel\n' > "$SENTINEL")
"$CONTROLLER_BIN" stop
[[ ! -e /run/sparehash/control.sock ]]
[[ ! -e /var/lib/sparehash/lifecycle-lease.json ]] # no manufactured lease
"$CONTROLLER_BIN" uninstall
UNINSTALLED=1
python3 - "$GROUP" "$SENTINEL" <<'PY'
import grp,json,os,pwd,sys
from pathlib import Path
group=Path("/sys/fs/cgroup")/sys.argv[1].lstrip("/")
assert not any(p.read_text().strip() for p in group.glob("**/cgroup.procs"))
removed=("/etc/systemd/system/sparehash.service", "/etc/systemd/system/sparehash-xmrig.service", "/usr/local/bin/sparehash")
assert all(not os.path.lexists(p) for p in removed)
assert not list(Path("/opt/sparehash/src").glob("**/*.py"))
assert not list(Path("/opt/sparehash/src").glob("**/*.service.in"))
assert not os.path.lexists("/opt/xmrig/current") and not list(Path("/opt/xmrig").glob("*/xmrig"))
for name in ("xmrig","sparehash"):
    try: pwd.getpwnam(name)
    except KeyError: pass
    else: raise SystemExit(f"FAIL: user remains: {name}")
    try: grp.getgrnam(name)
    except KeyError: pass
    else: raise SystemExit(f"FAIL: group remains: {name}")
assert all(Path("/etc/sparehash",name).is_file() for name in ("config.toml","xmrig.json","xmrig.runtime.json"))
assert json.loads(Path("/var/lib/sparehash/lifecycle.json").read_text())["retired"] is True
assert Path(sys.argv[2]).read_text()=="foreign release smoke sentinel\n"
(Path(os.environ["EVIDENCE_DIR"])/"removal.json").write_text(json.dumps({"cgroup_empty":True,"owned_artifacts_removed":True,"config_retained":True,"retired":True,"foreign_sentinel_preserved":True})+"\n")
PY
pass
