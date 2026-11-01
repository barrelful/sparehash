#!/bin/sh
# SpareHash bootstrap launcher: fixed environment, no host mutation.
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077
PYTHONDONTWRITEBYTECODE=1
export PYTHONDONTWRITEBYTECODE

# Drop every inherited import/override control before trusting the source tree.
for variable in PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONUSERBASE PYTHONEXECUTABLE PYTHONWARNINGS PYTHONSAFEPATH; do
    unset "$variable" 2>/dev/null || :
done
for variable in $(env | sed -n 's/^\(SPAREHASH_[A-Za-z0-9_]*\)=.*/\1/p'); do
    unset "$variable" 2>/dev/null || :
done

refuse() {
    printf 'sparehash: %s\n' "$1" >&2
    exit 1
}

if [ "$(id -u)" -ne 0 ]; then
    refuse 'install.sh must be run as root'
fi
if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != x86_64 ]; then
    refuse 'supported platform is Linux x86_64 only'
fi
if [ ! -d /run/systemd/system ]; then
    refuse 'systemd must be running as PID 1'
fi
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || {
    refuse 'Python 3.11 or newer is required'
}

# Fixed launcher-owned import root, derived only from this script's location.
sparehash_source=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P) || refuse 'cannot resolve installer directory'
source_package=$sparehash_source/sparehash
[ -d "$source_package" ] && [ ! -L "$source_package" ] || refuse 'package source is missing or unsafe'
PYTHONPATH=$sparehash_source
export PYTHONPATH

exec python3 -I -B "$source_package/__main__.py" install "$@"
