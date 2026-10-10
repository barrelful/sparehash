# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Scalar configuration checks shared by parsing and validation."""

import json
import re
from json import JSONDecodeError
from typing import Final

_HOSTNAME_LABEL: Final = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


def valid_pool_host(host: str) -> bool:
    if not host:
        return True
    if len(host) > 253 or host.startswith(".") or host.endswith("."):
        return False
    if "/" in host or host.startswith("http:") or host.startswith("https:") or ":" in host:
        return False
    return all(_HOSTNAME_LABEL.fullmatch(label) is not None for label in host.split("."))


def custom_json_error(value: str) -> str | None:
    if not value:
        return "cpu.custom_json is required when cpu.mode is custom"
    try:
        if not isinstance(json.loads(value), dict):
            return "cpu.custom_json must contain a JSON object"
    except JSONDecodeError:
        return "cpu.custom_json must be valid JSON"
    return None
