# One-off owner recovery: retired f348ed1 installation

**Only the owner runs these commands. The assistant has not run physical recovery.**
This is for the reported partial uninstall: f348ed1 source, 58 journalled source
removals, `leaf-receipts` completed, retirement active, launcher surviving but
modules gone. Do not use the broken installed `sparehash` CLI for recovery.

Bazzite 44 passed all 11 checks, including real failure reproduction, SIGKILL/
retry, preservation and explicit disabled-startup reinstall. Ubuntu is **blocked**
by guest process/agent failures; the owner explicitly waived that prerequisite
for this Bazzite-specific procedure. See [evidence and limits](v020-manifest-uninstall-recovery.md).

## Prerequisites and abort rule

- Use your regular user in an interactive terminal and an updated, reviewed
  SpareHash checkout containing `qa/build_recovery.py`. Git must contain both
  the recovery commit below and `f348ed165358668d8101cd121cf07ee2f001295f`.
- Keep SELinux Enforcing and both product services stopped. Check with
  `systemctl is-active sparehash.service` and
  `systemctl is-active sparehash-xmrig.service`: both must print `inactive`
  (the expected exit status is nonzero). Run no other lifecycle/service commands
  concurrently. Save work; use a reliable session and power supply.
- Never edit the marker, manifest, lifecycle latch, lease or progress journal.
  Never use purge/force, restore source manually, or change permissions to make
  a refusal pass. Do not share credential contents.
- **On any unexpected output, nonzero recovery exit or failed checksum: stop.**
  Leave services stopped and preserve staging/journals for review. Do not advance
  to reinstall. Interrupted apply may be retried with the same protected entrypoint
  only if its preflight still passes; ambiguous state must refuse.

## 1. Build and independently verify the exact bundle — ordinary user, no sudo

Keep this terminal open so the variables remain set. These are fixed tested pins,
not values calculated from whatever archive happens to be present:

```sh
RECOVERY_COMMIT=21d4960d99813f2da4d1d3c923f9d373e2973158
RECOVERY_SHA256=6a804efb61a0f10a95421d9badea04487fc6dbc51ecbe092527f1aa7f93aad30
RECOVERY_ARCHIVE="$HOME/sparehash-recovery.tar"
RECOVERY_STAGE="/run/sparehash/recovery/$RECOVERY_COMMIT"

python3 qa/build_recovery.py "$RECOVERY_COMMIT" "$RECOVERY_ARCHIVE"
printf '%s  %s\n' "$RECOVERY_SHA256" "$RECOVERY_ARCHIVE" | sha256sum --check
```

Expected: the published hash above and an `OK` checksum result. The builder uses
exact Git objects, not working-tree source. It refuses to overwrite an existing
archive; if the same archive already exists, verify it rather than rebuilding it.
Do **not** replace the expected checksum with an unverified locally generated one.

## 2. Protected bootstrap and read-only installation preflight

Paste this **reviewed shell text**, not `sudo sh qa/recovery_bootstrap.sh` and not
root Python from the checkout. It is the tested bootstrap body. Only stock system
tools run before the protected archive copy passes its independent checksum.
The bootstrap extracts one fixed gate file to protected storage; that gate rejects
links, traversal, duplicate/unexpected members and hash/inventory mismatches before
importing any application source. Existing unsafe staging objects are refused.

```sh
sudo /bin/sh -s -- "$RECOVERY_ARCHIVE" "$RECOVERY_COMMIT" "$RECOVERY_SHA256" <<'BOOTSTRAP'
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077
[ "$(id -u)" = 0 ] || exit 1
[ "$#" = 3 ] || exit 1
archive=$1 commit=$2 digest=$3
case "$commit$digest" in *[!0-9a-f]*) exit 1;; esac
[ "${#commit}" = 40 ] && [ "${#digest}" = 64 ] || exit 1
for path in / /run; do
    [ -d "$path" ] && [ ! -L "$path" ] && [ "$(stat -c %u "$path")" = 0 ] || exit 1
    mode=$(stat -c %a "$path")
    [ "$((0$mode & 022))" = 0 ] || exit 1
done
if [ ! -e /run/sparehash ] && [ ! -L /run/sparehash ]; then
    mkdir -m 0711 /run/sparehash
fi
[ -d /run/sparehash ] && [ ! -L /run/sparehash ] && [ "$(stat -c %u /run/sparehash)" = 0 ] || exit 1
mode=$(stat -c %a /run/sparehash)
[ "$((0$mode & 022))" = 0 ] || exit 1
base=/run/sparehash/recovery
if [ ! -e "$base" ] && [ ! -L "$base" ]; then mkdir -m 0700 "$base"; fi
[ -d "$base" ] && [ ! -L "$base" ] && [ "$(stat -c %u:%g:%a "$base")" = 0:0:700 ] || exit 1
stage=$base/$commit
mkdir -m 0700 "$stage"
dd if="$archive" of="$stage/archive.tar" iflag=nofollow,nonblock,count_bytes count=4194305 conv=excl status=none
chmod 0600 "$stage/archive.tar"
printf '%s  %s\n' "$digest" "$stage/archive.tar" | sha256sum --check --status
tar --extract --to-stdout --file="$stage/archive.tar" -- qa/recovery_stage.py > "$stage/stage.py"
chmod 0600 "$stage/stage.py"
exec /usr/bin/python3 -I -B "$stage/stage.py" "$commit" "$digest" preflight
BOOTSTRAP
```

Expected: `Recovery preflight: ... non-purge recovery eligible.` and exit 0.
Staging is root:root 0700 with 0600 source files; existing parent metadata is not
changed. Preflight writes only verified temporary staging—not installed state,
journals, services or hugepages. **This is the clean abort point:** do not run apply
if you do not want removal. Do not repeat the bootstrap over existing staging.

## 3. Apply non-purge recovery

```sh
sudo /usr/bin/python3 -I -B "$RECOVERY_STAGE/stage.py" "$RECOVERY_COMMIT" "$RECOVERY_SHA256" apply
```

Expected: `Recovery complete; configuration and retirement retained.` and exit 0.
The original shim is receipt-verified but never executed. Required cleanup runs
from verified source against the **old** runtime layout. Owned hugepages restore
their recorded baseline if a valid lease exists; unrelated global pages are not
reset. Receipt/journal-authorized remaining files and owned accounts are removed.
Configuration, credentials, foreign files and `~/sparehash-contention-results/`
are retained. The retired latch is not cleared. A protected completion receipt is
fsynced before completed removal progress is cleared, preventing its reuse against
a new installation. Repeated apply after completion is a no-op, not management.

## 4. Explicit reinstall from the same verified protected source — no auto-start

Only after step 3 succeeds, reverify staging, then invoke the existing installer:

```sh
sudo /usr/bin/python3 -I -B "$RECOVERY_STAGE/stage.py" "$RECOVERY_COMMIT" "$RECOVERY_SHA256" preflight &&
sudo /usr/bin/test -f "$RECOVERY_STAGE/complete.json" &&
sudo /usr/bin/python3 -I -B "$RECOVERY_STAGE/source/sparehash/__main__.py" install --clear-lifecycle-latch --no-start --no-enable
```

The `&&` gates prevent reinstall if source verification or the completion-receipt
check fails. Use the existing private configuration in the interactive wizard; review choices
locally, without sharing credentials. Normal upstream artifact verification remains
the installer's responsibility. The VM used a non-hashing offline existing binary
with `--use-existing`; do not add that flag unless you have a suitable verified
existing binary. Upstream download/pool connectivity was not tested by recovery.

Expected: installation succeeds, boot startup disabled, controller inactive and
miner static. Verify with ordinary-user queries:

```sh
systemctl is-enabled sparehash.service
systemctl is-active sparehash.service
systemctl is-active sparehash-xmrig.service
```

Expected outputs: `disabled`, `inactive`, `inactive` (nonzero statuses are normal).
Do not start mining as part of recovery. If reinstall fails, preserve staging and
state for review; the one-off recovery must not be used to remove the new/partial
installation. Never clear a latch to bypass a failed recovery.

## 5. Dispose of temporary privileged source

After successful reinstall and the stopped/disabled checks:

```sh
sudo /usr/bin/python3 -I -B "$RECOVERY_STAGE/stage.py" "$RECOVERY_COMMIT" "$RECOVERY_SHA256" remove-staging
```

Expected: `Verified temporary recovery source removed.` It reverifies and removes
only the finite staging inventory, refusing foreign content rather than recursively
deleting it. No recovery entrypoint/unit is installed. Empty runtime scaffolding
may remain; do not recursively delete `/run/sparehash` or `/run/sparehash-miner`.
The ordinary-user archive may be retained for audit. Recovery source is ephemeral
under `/run`; do not reboot midway or treat it as a persistent management tool.

Sudden power loss, unresolved hugepage evidence or state differing from the tested
case can require separate investigation. A refusal is not permission to edit the
protected records. This procedure is Bazzite-tested recovery, not release readiness.
