#!/usr/bin/env bash
# Task 3 only. Never install/start controller, miner service, or pool.
set -Eeuo pipefail
if [[ ${1:-} == --help ]]; then
    /usr/bin/cat <<'HELP'
Task-3 nonmining Atomic feasibility probe (disposable enforcing QEMU/KVM only).
Usage: SPAREHASH_QA_DISPOSABLE_VM=YES CANDIDATE_SHA=<40 hex> \
 EVIDENCE_DIR=/var/tmp/sparehash-task3-NAME \
 bash qa/qa_atomic_probe.sh --environment-manifest /var/tmp/authority/environment.json

Root-owned no-follow JSON manifest (<=16KiB, nlink=1, no writable ancestry
except root sticky /tmp or /var/tmp); EXACT fields, all strings:
 {"kind":"disposable-vm", "surface":"atomic-task3-nonmining",
  "machine_id":"<actual /etc/machine-id>", "root_identity":"<root st_dev:st_ino>",
  "candidate_sha":"<40 hex>", "source_sha256":"<inventory digest>",
  "executable":"/var/tmp/authority/xmrig", "executable_sha256":"<authenticated digest>"}
An independent authority must authenticate the offline XMRig executable through
the existing pinned GPG/checksum chain and put its executable SHA256 here.
Computing a candidate's own hash is NOT signature verification. No downloads.
Root-controlled extracted source must contain candidate.sha matching CANDIDATE_SHA.
source_sha256 covers sorted sparehash/*.py, systemd/*.in, qa/atomic_probe*.py,
qa/__init__.py if present, and qa/qa_atomic_probe.sh: relative UTF-8 path + NUL +
raw SHA256(file) for each. Authority pins it
before transfer; changing any included byte requires new evidence. The caller
explicitly trusts this harness. Host root/kernel are trusted, not adversarial.

Consent discloses: create NEW /var/opt/xmrig/task3/{xmrig,config.json}; refuse
ANY existing /var/opt/xmrig, including empty dirs/links. Inspect existing Atomic
redirects/prefixes (never change them). Start three bounded transient units:
legacy Group=nogroup comparison and actual-primary-GID --version/--dry-run.
All use nobody, mandatory isolation, zero donation, fake token, loopback dummy
pool, no GPU/MSR/hugepage changes, no mining. No installed product units touched.
Record effective properties, id UID/GID/PID from the sandbox before exec,
installed-policy expected/actual contexts, mount/noexec facts, and
PID-attributed raw AVCs over the retained recent/now audit window with
auditing enabled/loss counters unchanged. Missing evidence BLOCKS.
Only installed default labels are used: NO restorecon, policy module or bypass.

Always attempt stop/reset-failed for our three unpredictable transient names.
Receipt: evidence/ownership durably records device/inode/mode/UID after each
exclusive root/task3/leaf creation. Cleanup pins no-follow parents/objects and
validates the whole graph before unlinking original leaves from the same parents.
Missing owned objects, replaced child/leaf entries or extra children block.
Partial staging can remove only receipted originals, never certify a complete run.
Foreign replacements/failed stops retain staging and report BLOCKED; discard VM.
Evidence remains under the new EVIDENCE_DIR, including exact raw observations,
cleanup receipt and probe.json. FEASIBLE is Task-3-only, NOT release acceptance.
Guest networking must already be disabled; no active non-loopback interface.
No VM is created by this script. Never use host sudo to run it.
HELP
    exit 0
fi
[[ ${SPAREHASH_QA_DISPOSABLE_VM:-} == YES ]] || {
    printf '%s\n' 'BLOCKED: SPAREHASH_QA_DISPOSABLE_VM=YES required' >&2; exit 1;
}
[[ $# == 2 && $1 == --environment-manifest ]] || {
    printf '%s\n' 'BLOCKED: expected --environment-manifest PATH (see --help)' >&2; exit 1;
}
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C LC_ALL=C TZ=UTC PYTHONDONTWRITEBYTECODE=1
unset PYTHONPATH PYTHONHOME
unset SYSTEMD_BUS_ADDRESS DBUS_SYSTEM_BUS_ADDRESS SYSTEMD_HOST SYSTEMD_MACHINE LD_PRELOAD LD_LIBRARY_PATH
HERE=$(builtin cd -- "${BASH_SOURCE[0]%/*}" && pwd -P)
guard=$(/usr/bin/python3 -I -B "$HERE/atomic_probe.py" guard "$2") || exit 1
mapfile -t identity <<< "$guard"
uid=${identity[0]} gid=${identity[1]} executable=${identity[2]} digest=${identity[3]}
[[ ${CANDIDATE_SHA:-} =~ ^[0-9a-f]{40}$ ]] || { printf 'BLOCKED: CANDIDATE_SHA required\n' >&2; exit 1; }
[[ $(/usr/bin/cat "$HERE/../candidate.sha") == "$CANDIDATE_SHA" ]] || exit 1
umask 077
/usr/bin/mkdir -m 0700 -- "$EVIDENCE_DIR"
E=$EVIDENCE_DIR
printf '%s %s\n' "$uid" "$gid" > "$E/identity"
printf '%s\n' "${identity[4]}" > "$E/authorization.json"
nonce=$(/usr/bin/cat /proc/sys/kernel/random/uuid)
units=("sparehash-task3-$nonce-legacy.service" "sparehash-task3-$nonce-version.service" "sparehash-task3-$nonce-dry-run.service")
owned=0
finish() {
    local result=$? clean=1 unit
    trap - EXIT
    trap '' INT TERM HUP
    set +e
    for unit in "${units[@]}"; do
        /usr/bin/timeout 5 /usr/bin/systemctl stop "$unit" >> "$E/cleanup.log" 2>&1
        # Nonexistent units were never started; a loaded failed unit still needs stop.
        if [[ $? != 0 ]]; then
            [[ $(/usr/bin/timeout 5 /usr/bin/systemctl show "$unit" -p LoadState --value) == not-found ]] || clean=0
        fi
        /usr/bin/timeout 5 /usr/bin/systemctl reset-failed "$unit" >> "$E/cleanup.log" 2>&1
    done
    if [[ $owned == 1 && $clean == 1 ]]; then
        /usr/bin/python3 -I -B "$HERE/atomic_probe.py" cleanup "$MANIFEST" >> "$E/cleanup.log" 2>&1 || clean=0
    else
        clean=0
    fi
    [[ $clean == 1 ]] && printf 'removed-owned-staging-and-units\n' > "$E/cleanup"
    /usr/bin/python3 -I -B "$HERE/atomic_probe.py" summarize "$E" || result=1
    [[ $clean == 1 ]] || result=1
    exit "$result"
}
trap finish EXIT
trap 'exit 130' INT TERM HUP
MANIFEST=$2
owned=1
/usr/bin/python3 -I -B "$HERE/atomic_probe.py" stage "$MANIFEST" > "$E/staging.log" 2>&1
# The protected executable is independently bounded to 64 MiB. Limit evidence
# and probe outputs only after staging, not the authenticated binary copy.
ulimit -f 128
printf '%s\n' "$CANDIDATE_SHA" > "$E/candidate.sha"
/usr/bin/python3 -I -B --version > "$E/interpreter"
/usr/bin/systemctl --version > "$E/systemd"
/usr/bin/cat /proc/self/mountinfo > "$E/mountinfo"
/usr/bin/ls -ldZ /opt /usr/local /var/opt /var/usrlocal /var/usrlocal/bin > "$E/prefixes"
/usr/bin/ls -l /sys/fs/cgroup/cgroup.controllers > "$E/cgroups"
/usr/bin/findmnt -no TARGET,OPTIONS -T /var/opt/xmrig/task3/xmrig > "$E/executable-mount"
for path in /var/opt /var/usrlocal /var/usrlocal/bin /var/opt/xmrig/task3/xmrig /var/opt/xmrig/task3/config.json; do
    expected=$(/usr/bin/matchpathcon -n "$path")
    actual=$(/usr/bin/stat -c %C "$path")
    printf '%s\t%s\n' "$expected" "$actual" >> "$E/contexts"
    printf '%s\t%s\t%s\n' "$path" "$expected" "$actual" >> "$E/contexts-paths"
done
properties=(ProtectSystem=strict ProtectHome=yes PrivateNetwork=yes NoNewPrivileges=yes PrivateTmp=yes)
index=0
for mode in legacy version dry-run; do
    unit=${units[$index]}; index=$((index+1))
    group=$gid; [[ $mode != legacy ]] || group=nogroup
    arguments=(--version)
    [[ $mode != dry-run ]] || arguments=(--config /var/opt/xmrig/task3/config.json --dry-run)
    set +e
    /usr/bin/timeout 5 /usr/sbin/auditctl -s > "$E/$mode.audit-before" 2>&1
    printf '%s\n' "$?" > "$E/$mode.audit-before.exit"
    set -e
    read -r start_date start_time <<< "$(/usr/bin/date '+%m/%d/%Y %H:%M:%S')"
    set +e
    (
        ulimit -f 128
        /usr/bin/timeout --kill-after=3 40 /usr/bin/systemd-run --quiet --expand-environment=no --unit="$unit" \
            --property=Type=oneshot --property=RemainAfterExit=yes \
            --property="StandardOutput=append:$E/$mode.output" --property=StandardError=inherit \
            --property=StandardInput=null \
            --property="User=$uid" --property="Group=$group" --property=SupplementaryGroups= \
            --property=RuntimeMaxSec=30s --property=TimeoutStartSec=30s --property=TimeoutStopSec=2s \
            --property=MemoryMax=256M --property=LimitFSIZE=65536 --property=TasksMax=16 \
            "${properties[@]/#/--property=}" \
            /usr/bin/sh -c '/usr/bin/printf "%s %s %s\n" "$(/usr/bin/id -u)" "$(/usr/bin/id -g)" "$$"; exec "$@"' \
            task3 /var/opt/xmrig/task3/xmrig "${arguments[@]}"
    ) > "$E/$mode.output" 2>&1
    printf '%s\n' "$?" > "$E/$mode.exit"
    set -e
    set +e
    /usr/bin/timeout 5 /usr/bin/systemctl show "$unit" -p User -p Group -p ExecMainPID -p ExecMainStatus \
        -p ProtectSystem -p ProtectHome -p PrivateNetwork -p NoNewPrivileges -p PrivateTmp > "$E/$mode.properties" 2>&1
    printf '%s\n' "$?" > "$E/$mode.properties.exit"
    set -e
    read -r observed_uid observed_gid pid remainder < "$E/$mode.output" || true
    if [[ ${pid:-} =~ ^[1-9][0-9]*$ ]]; then
        read -r end_date end_time <<< "$(/usr/bin/date '+%m/%d/%Y %H:%M:%S')"
        printf 'pid=%s\nstart=%s %s\nend=%s %s\n' "$pid" "$start_date" "$start_time" "$end_date" "$end_time" > "$E/$mode.audit-window"
        # LC_ALL=C is exported above and ausearch absolute dates go through
        # strptime(3) %x, which rejects the emitted %m/%d/%Y form ("Error
        # parsing start date").  Query with the locale-independent recent/now
        # keywords; audit-window retains the wall-clock execution bounds.
        # Observed no-match capture shapes (stderr is merged): audit 4.2.1
        # (Fedora 44) --raw prints nothing and exits 1; older/non-raw ausearch
        # prints "<no matches>" on stderr and exits 1.  Real errors against
        # this exact command remain non-empty text.
        query=(/usr/sbin/ausearch -m AVC,USER_AVC -p "$pid" -ts recent -te now --raw)
        printf '%s\n' "${query[@]}" > "$E/$mode.audit-command"
        set +e
        (ulimit -f 128; /usr/bin/timeout 5 "${query[@]}") > "$E/$mode.audit-query" 2>&1
        audit_rc=$?
        printf '%s\n' "$audit_rc" > "$E/$mode.audit-query.exit"
        set -e
        printf '=== %s PID %s ===\n' "$mode" "$pid" >> "$E/avc.txt"
        /usr/bin/cat "$E/$mode.audit-query" >> "$E/avc.txt"
        set +e
        /usr/bin/timeout 5 /usr/sbin/auditctl -s > "$E/$mode.audit-after" 2>&1
        printf '%s\n' "$?" > "$E/$mode.audit-after.exit"
        set -e
        if [[ $audit_rc == 0 ]]; then
            /usr/bin/cp "$E/$mode.audit-query" "$E/$mode.avc"
        elif [[ $audit_rc == 1 && ( -z "$(<"$E/$mode.audit-query")" || $(<"$E/$mode.audit-query") == '<no matches>' ) ]]; then
            : > "$E/$mode.avc"
        fi
        if [[ -f $E/$mode.avc ]] &&
           /usr/bin/grep -qEx 'enabled [12]' "$E/$mode.audit-before" && /usr/bin/grep -qx 'lost 0' "$E/$mode.audit-before" &&
           /usr/bin/grep -qEx 'enabled [12]' "$E/$mode.audit-after" && /usr/bin/grep -qx 'lost 0' "$E/$mode.audit-after" &&
           /usr/bin/grep -qx 'backlog 0' "$E/$mode.audit-after"; then
            printf 'yes\n' > "$E/$mode.audit-known"
        fi
    fi
done
