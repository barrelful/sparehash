#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Owner uses this reviewed text as a shell heredoc, never sudo sh user-checkout/script.
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
mkdir -m 0700 "$stage" # Refuse existing or linked staging, never overwrite.
dd if="$archive" of="$stage/archive.tar" iflag=nofollow,nonblock,count_bytes count=4194305 conv=excl status=none
chmod 0600 "$stage/archive.tar"
printf '%s  %s\n' "$digest" "$stage/archive.tar" | sha256sum --check --status
tar --extract --to-stdout --file="$stage/archive.tar" -- qa/recovery_stage.py > "$stage/stage.py"
chmod 0600 "$stage/stage.py"
exec /usr/bin/python3 -I -B "$stage/stage.py" "$commit" "$digest" preflight
