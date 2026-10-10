# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Public XMRig compatibility surface and metadata-only selfcheck entrypoint."""

import sys
from collections.abc import Sequence

from .xmrig_detect import (
    Capabilities,
    Classification,
    Install,
    ManagedBy,
    Recommendation,
    classify,
    detect_installations,
    is_supported,
    parse_version,
)
from .xmrig_release import (
    Asset,
    FetchError,
    Opener,
    Release,
    download,
    fetch_latest_release,
    select_asset,
)

__all__ = [
    "Capabilities",
    "Classification",
    "Install",
    "ManagedBy",
    "Recommendation",
    "classify",
    "detect_installations",
    "is_supported",
    "parse_version",
    "Asset",
    "FetchError",
    "Release",
    "download",
    "fetch_latest_release",
    "select_asset",
]


def main(argv: Sequence[str] | None = None, opener: Opener | None = None) -> int:
    """Print one release tag and three URLs without downloading release assets."""
    args = sys.argv[1:] if argv is None else list(argv)
    if args != ["--selfcheck"]:
        print("Usage: python3 -m sparehash.xmrig --selfcheck", file=sys.stderr)
        return 2
    try:
        release = fetch_latest_release(opener)
        urls = select_asset(release.assets)
    except FetchError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(release.tag_name, *urls, sep="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
