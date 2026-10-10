#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# ─── How to run ───
# python3 qa/spin.py 40 (one normal-priority foreground worker, batches/second)
"""Single-process foreground throughput probe, not a stress-ng replacement."""
from __future__ import annotations

import math
import sys
import time


def spin(seconds: float) -> float:
    """Return completed identical work batches per actual monotonic second."""
    if not math.isfinite(seconds) or seconds <= 0:
        raise SystemExit("duration must be finite and positive")
    started = time.monotonic()
    batches = 0
    while time.monotonic() - started < seconds:
        _ = sum(range(10000))
        batches += 1
    return batches / (time.monotonic() - started)


if __name__ == "__main__":
    print(spin(float(sys.argv[1]) if len(sys.argv) > 1 else 40.0))
