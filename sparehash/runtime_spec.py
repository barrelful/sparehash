# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Fixed protected operation consumer; only the xmrig identity may exec a miner."""

from __future__ import annotations

import json
import os
import pwd
import stat
from contextlib import ExitStack
from pathlib import Path
from typing import Final

from . import config
from .deployment import ENV, LOAD_JSON, DeploymentError, Tree, binary as binary
from .xmrig_ledger import integer, keys, mapping, text

DIRECTORY: Final = "/run/sparehash-miner"
SPEC: Final = DIRECTORY + "/operation.json"
FALLBACK: Final = "/etc/sparehash/xmrig.runtime.json"
BENCHMARK: Final = DIRECTORY + "/benchmark.json"


def publish(cfg: config.Config, generation: int, mode: str) -> None:
    if mode not in {"mine", "benchmark"}:
        raise DeploymentError("operation", "unsupported mode")
    gid = pwd.getpwnam("xmrig").pw_gid
    tree = Tree()
    with ExitStack() as stack:
        fd = tree.pin(stack, DIRECTORY)
        os.fchown(fd, 0, gid)
        os.fchmod(fd, 0o750)
    target = FALLBACK if mode == "mine" else BENCHMARK
    executable = binary(cfg.xmrig.binary)
    if mode == "benchmark":
        from .xmrig_config import render_config, read_token
        from .install_xmrig import dry_run

        document = render_config(cfg, read_token(), executable, runner=dry_run)
        document["pools"] = []
        document["http"] = {"enabled": False}
        tree.write(BENCHMARK, ((json.dumps(document) + "\n").encode(), 0o640, gid))
    document = {"generation": generation, "mode": mode, "binary": str(executable), "config": target}
    tree.write(SPEC, ((json.dumps(document) + "\n").encode(), 0o640, gid))


def protected(name: str, gid: int) -> bytes:
    tree = Tree()
    with ExitStack() as stack:
        parent = tree.pin(stack, str(Path(name).parent))
        info = os.stat(Path(name).name, dir_fd=parent, follow_symlinks=False)
        if info.st_uid != 0 or info.st_gid != gid or stat.S_IMODE(info.st_mode) != 0o640 or not stat.S_ISREG(info.st_mode):
            raise DeploymentError("operation", "incorrect protected file ownership/mode")
        if name == SPEC:
            directory = os.fstat(parent)
            if directory.st_gid != gid or stat.S_IMODE(directory.st_mode) != 0o750:
                raise DeploymentError("operation", "incorrect protected operation directory")
    data = tree.read(name)
    if data is None:
        raise DeploymentError("operation", "missing protected file")
    return data


def miner() -> int:
    try:
        account = pwd.getpwnam("xmrig")
        if os.geteuid() == 0 or os.geteuid() != account.pw_uid:
            raise DeploymentError("identity", "miner execution requires the unprivileged xmrig identity")
        document = mapping(LOAD_JSON(protected(SPEC, account.pw_gid)))
        keys(document, "generation mode binary config")
        _ = integer(document["generation"])
        mode = text(document["mode"])
        if mode not in {"mine", "benchmark"}:
            raise DeploymentError("operation", "unrecognized mode")
        cfg_data = Tree().read("/etc/sparehash/config.toml")
        if cfg_data is None:
            raise DeploymentError("config", "missing public configuration")
        cfg = config.load()
        executable = binary(text(document["binary"]))
        if str(executable) != cfg.xmrig.binary:
            raise DeploymentError("operation", "binary differs from the protected configuration")
        target = BENCHMARK if mode == "benchmark" else FALLBACK
        if document["config"] != target:
            raise DeploymentError("operation", "caller-selected config is forbidden")
        _ = protected(target, account.pw_gid)
        credentials = os.environ.get("CREDENTIALS_DIRECTORY")
        if credentials and mode == "mine":
            if credentials != "/run/credentials/sparehash-xmrig.service":
                raise DeploymentError("credential", "unexpected credential directory")
            credential = Path(credentials) / "xmrig.json"
            if not os.statvfs(credential).f_flag & os.ST_RDONLY:
                raise DeploymentError("credential", "credentials must be on a read-only systemd mount")
            target = str(credential)
        argv = (str(executable), "--config", target, *(("--bench=1M",) if mode == "benchmark" else ()))
        os.execve(executable, argv, ENV)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(f"sparehash: miner: {error}")
        return 1
