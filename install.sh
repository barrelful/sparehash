#!/bin/sh
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077

refuse() {
    printf 'sparehash: %s\n' "$1" >&2
    exit 1
}

if [ "$(id -u)" -ne 0 ]; then
    refuse 'install.sh must be run as root'
fi

python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || {
    refuse 'Python 3.11 or newer is required'
}

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
    refuse 'supported platform is Linux x86_64 only'
fi

if [ ! -d /run/systemd/system ]; then
    refuse 'systemd must be running as PID 1'
fi

source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P) || refuse 'cannot resolve installer directory'
source_package=$source_dir/sparehash
[ -d "$source_package" ] && [ ! -L "$source_package" ] || refuse 'package source is missing or unsafe'

for directory in /opt /opt/sparehash /opt/sparehash/src /usr/local /usr/local/bin; do
    if [ -L "$directory" ]; then
        refuse "refusing symlinked directory: $directory"
    fi
done

for directory in /opt /usr/local /usr/local/bin; do
    [ -d "$directory" ] || refuse "required directory is missing: $directory"
    [ "$(stat -c '%u:%a' "$directory")" = 0:755 ] || refuse "directory ownership or mode is unsafe: $directory"
done

for directory in /opt/sparehash /opt/sparehash/src; do
    if [ ! -e "$directory" ]; then
        mkdir -m 0755 "$directory" || refuse "cannot create directory: $directory"
        set -- "$@" --bootstrap-created "$directory"
    fi
    [ -d "$directory" ] && [ "$(stat -c '%u:%a' "$directory")" = 0:755 ] || refuse "directory ownership or mode is unsafe: $directory"
done

if [ ! -e /opt/sparehash/src/sparehash ]; then
    mkdir -m 0755 /opt/sparehash/src/sparehash || refuse 'cannot create package directory'
    set -- "$@" --bootstrap-created /opt/sparehash/src/sparehash
fi
[ -d /opt/sparehash/src/sparehash ] && [ ! -L /opt/sparehash/src/sparehash ] || refuse 'installed package path is unsafe'
[ "$(stat -c '%u:%a' /opt/sparehash/src/sparehash)" = 0:755 ] || refuse 'installed package directory ownership or mode is unsafe'

source_count=0
for source_file in "$source_package"/*.py; do
    [ -f "$source_file" ] && [ ! -L "$source_file" ] || refuse "package source file is unsafe: $source_file"
    source_count=$((source_count + 1))
    [ "$source_count" -le 128 ] || refuse 'package contains too many Python modules'
    file=${source_file##*/}
    target_file=/opt/sparehash/src/sparehash/$file
    if [ -e "$target_file" ] || [ -L "$target_file" ]; then
        [ -f "$target_file" ] && [ ! -L "$target_file" ] || refuse "installed package file is unsafe: $file"
        [ "$(stat -c '%u:%a' "$target_file")" = 0:644 ] || refuse "installed package file ownership or mode is unsafe: $file"
        cmp -s "$source_file" "$target_file" || refuse "installed package differs; refusing overwrite: $file"
    else
        install -o root -g root -m 0644 "$source_file" "$target_file" || refuse "cannot install package file: $file"
        set -- "$@" --bootstrap-created "$target_file"
    fi
done

template_source=$source_dir/systemd
[ -d "$template_source" ] && [ ! -L "$template_source" ] || refuse 'systemd templates are missing or unsafe'
if [ ! -e /opt/sparehash/src/systemd ]; then
    mkdir -m 0755 /opt/sparehash/src/systemd || refuse 'cannot create template directory'
    set -- "$@" --bootstrap-created /opt/sparehash/src/systemd
fi
[ -d /opt/sparehash/src/systemd ] && [ ! -L /opt/sparehash/src/systemd ] || refuse 'installed template directory is unsafe'
[ "$(stat -c '%u:%a' /opt/sparehash/src/systemd)" = 0:755 ] || refuse 'installed template directory ownership or mode is unsafe'
for unit in sparehash.service.in sparehash-xmrig.service.in; do
    source_file=$template_source/$unit
    target_file=/opt/sparehash/src/systemd/$unit
    [ -f "$source_file" ] && [ ! -L "$source_file" ] || refuse "unsafe unit template: $unit"
    if [ -e "$target_file" ] || [ -L "$target_file" ]; then
        [ -f "$target_file" ] && [ ! -L "$target_file" ] || refuse "unsafe installed template: $unit"
        [ "$(stat -c '%u:%a' "$target_file")" = 0:644 ] || refuse "unsafe template protection: $unit"
        cmp -s "$source_file" "$target_file" || refuse "installed template differs: $unit"
    else
        install -o root -g root -m 0644 "$source_file" "$target_file" || refuse "cannot install template: $unit"
        set -- "$@" --bootstrap-created "$target_file"
    fi
done

shim_tmp=$(mktemp /usr/local/bin/.sparehash.XXXXXX) || refuse 'cannot create temporary CLI shim'
trap 'rm -f "$shim_tmp"' 0 HUP INT TERM
cat > "$shim_tmp" <<'PYTHON_SHIM'
#!/usr/bin/python3
import sys

sys.path.insert(0, "/opt/sparehash/src")

from sparehash.__main__ import main

raise SystemExit(main())
PYTHON_SHIM
chown root:root "$shim_tmp" || refuse 'cannot set CLI shim ownership'
chmod 0755 "$shim_tmp" || refuse 'cannot set CLI shim mode'

if [ -e /usr/local/bin/sparehash ] || [ -L /usr/local/bin/sparehash ]; then
    [ -f /usr/local/bin/sparehash ] && [ ! -L /usr/local/bin/sparehash ] || refuse 'existing CLI path is unsafe'
    [ "$(stat -c '%u:%a' /usr/local/bin/sparehash)" = 0:755 ] || refuse 'existing CLI ownership or mode is unsafe'
    cmp -s "$shim_tmp" /usr/local/bin/sparehash || refuse 'existing CLI differs; refusing overwrite'
    rm -f "$shim_tmp"
else
    mv "$shim_tmp" /usr/local/bin/sparehash || refuse 'cannot install CLI shim'
    set -- "$@" --bootstrap-created /usr/local/bin/sparehash
fi
trap - 0 HUP INT TERM

exec /usr/local/bin/sparehash install "$@"
