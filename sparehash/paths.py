"""Fixed production paths for Sparehash."""

from pathlib import Path
from typing import Final

ETC_DIR: Final = Path("/etc/sparehash")
CONFIG_TOML: Final = ETC_DIR / "config.toml"
XMRIG_JSON: Final = ETC_DIR / "xmrig.json"
XMRIG_RUNTIME_JSON: Final = ETC_DIR / "xmrig.runtime.json"

STATE_FILE: Final = Path("/var/lib/sparehash/state.json")
STATE_DIR: Final = STATE_FILE.parent
HUGEPAGE_LEASE_FILE: Final = STATE_DIR / "hugepage-lease.json"
INSTALL_MANIFEST_FILE: Final = STATE_DIR / "install-manifest.json"
RUN_DIR: Final = Path("/run/sparehash")
CONTROL_SOCK: Final = RUN_DIR / "control.sock"
MINER_RUN_DIR: Final = Path("/run/sparehash-miner")

OPT_DIR: Final = Path("/opt/sparehash")
OPT_SRC: Final = OPT_DIR / "src"
XMRIG_OPT: Final = Path("/opt/xmrig")
XMRIG_CURRENT: Final = XMRIG_OPT / "current" / "xmrig"
CLI_PATH: Final = Path("/usr/local/bin/sparehash")

UNIT_CONTROLLER: Final = "sparehash.service"
UNIT_MINER: Final = "sparehash-xmrig.service"

SPAREHASH_SYSFS_ROOT: Final = "SPAREHASH_SYSFS_ROOT"
SPAREHASH_HWMON_ROOT: Final = "SPAREHASH_HWMON_ROOT"
SPAREHASH_RUN_DIR: Final = "SPAREHASH_RUN_DIR"
SPAREHASH_STATE_FILE: Final = "SPAREHASH_STATE_FILE"
