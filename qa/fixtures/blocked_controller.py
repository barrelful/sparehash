#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Disposable-VM-only scanner-worker fault injection; installed code is unchanged."""

import os
import subprocess
import sys
import threading
from pathlib import Path

if os.geteuid() != 0 or os.environ.get("SPAREHASH_QA_DISPOSABLE_VM") != "YES":
    raise SystemExit("explicit disposable guest root required")
if subprocess.run(["systemd-detect-virt", "--vm", "--quiet"]).returncode:
    raise SystemExit("VM required")
sys.dont_write_bytecode = True
sys.path.insert(0, "/opt/sparehash/src")
from sparehash import runtime  # noqa: E402

original = runtime.Providers.__init__


def initialize(self, *args, **kwargs):
    original(self, *args, **kwargs)
    scan = self.gaming_scan

    def injected():
        if Path("/var/lib/sparehash/qa-block").exists():
            Path("/var/lib/sparehash/qa-entered").touch()
            threading.Event().wait()  # Intentionally uninterruptible worker join.
        return scan()

    self.gaming_scan = injected


runtime.Providers.__init__ = initialize
raise SystemExit(runtime.run())
