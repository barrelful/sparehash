# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Optional sysfs power attributes parsed at the hardware boundary."""

from pathlib import Path


def read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None


def discharge_rate(entry: Path) -> float | None:
    try:
        watts = read(entry / "power_now")
        if watts is not None:
            return abs(int(watts)) / 1e6
        current, voltage = read(entry / "current_now"), read(entry / "voltage_now")
        return abs(int(current) * int(voltage)) / 1e12 if current is not None and voltage is not None else None
    except ValueError:
        return None
