# Third-party notices

## Lutris Steam reaper detection

`sparehash/gaming_scan.py` adapts `get_steam_game_pids()` from Lutris,
`lutris/runners/steam.py`:

https://github.com/lutris/lutris/blob/1f32fe0e46e2a5d92fad9db2846c7cb7becd20c3/lutris/runners/steam.py#L58-L95

Upstream revision: `1f32fe0e46e2a5d92fad9db2846c7cb7becd20c3`.
Copyright: Lutris contributors; the project's copyright notice credits
Mathieu Comandon (2009). Licence: **GPL-3.0-or-later**, as declared by Lutris's
project metadata and `debian/copyright`. See the accompanying `LICENSE` for
the GNU General Public License, version 3. No warranty is provided.

Modified by SpareHash contributors on 2026-10-25: removed the predetermined
App ID requirement; parsed NUL-separated reaper launch arguments instead of
incidental substrings; bounded process count, reads and scan work; distinguished
normal process disappearance from errors; checked candidate identity; returned
immutable timestamped results. Lutris's runner, launcher, manifest handling,
per-game cache and launch grace period are not included. Lutris is not a runtime
dependency and does not maintain or endorse SpareHash.

S2 retires the former Steam-log/session-completeness experiments; the attributed
scanner remains the sole automatic gaming detector. No Lutris runner, GameMode
integration or optional log-reader fallback is shipped as an alternative.
