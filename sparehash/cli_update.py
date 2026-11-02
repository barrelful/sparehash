# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors
"""Read-only application update discovery; mutation is wired in milestone 3."""
from __future__ import annotations

import sys

from . import __version__
from .app_release import AppReleaseError, discover, version_tuple
from .identity import display


def check() -> int:
    try:
        release = discover()
        print(f"Installed: {display()}")
        print(f"Latest signed-asset release: {release.version}")
        print(release.url)
        print("Update available" if version_tuple(release.version) > version_tuple(__version__)
              else "No newer release available")
        print("Discovery is availability information, not artifact authentication.")
        return 0
    except AppReleaseError as error:
        print(f"sparehash: {error}", file=sys.stderr)
        return 1
