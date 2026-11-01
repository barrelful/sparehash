# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Read-only public configuration; editing and benchmark are deferred."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .config import ConfigError, load, validate
from .paths import CONFIG_TOML


def show(path: Path = CONFIG_TOML) -> int:
    """Only serialize the public schema, never open generated credentials."""
    try:
        cfg = load(path)
        errors = validate(cfg)
        if errors:
            raise ConfigError("; ".join(errors))
        print(json.dumps(cfg.to_dict(), indent=2))
    except (ConfigError, OSError, UnicodeError):
        print("sparehash: config show: invalid or unavailable public config", file=sys.stderr)
        return 1
    return 0
