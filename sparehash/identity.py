# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors
"""Public identity only: no Git, host detection, config or IPC."""

from . import __version__

try:
    from ._build import COMMIT
except ModuleNotFoundError:
    COMMIT = "unknown"


def display() -> str:
    return f"SpareHash {__version__} (commit {COMMIT})"
