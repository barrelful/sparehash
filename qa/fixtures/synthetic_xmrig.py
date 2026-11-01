#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors
"""SpareHash-owned lifecycle fixture, NOT upstream XMRig; no mining or pools."""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.dont_write_bytecode = True
assert os.geteuid() != 0, "fixture must never execute as root"
if "--version" in sys.argv:
    print("XMRig 6.26.0 (SpareHash SYNTHETIC non-hashing lifecycle fixture)")
    raise SystemExit(0)
cfg = json.loads(Path(sys.argv[sys.argv.index("--config") + 1]).read_text())
if "--dry-run" in sys.argv:
    assert cfg["http"]["access-token"] == "0" * 64
    raise SystemExit(0)
assert cfg["pools"][0]["url"].startswith("127.0.0.1:")
assert cfg["http"]["host"] == "127.0.0.1"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer " + cfg["http"]["access-token"]:
            self.send_error(403)
            return
        data = json.dumps({"hashrate": {"total": [0, 0, 0]}, "connection": {"pool": "synthetic; never connected"}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", cfg["http"]["port"]), Handler).serve_forever()
