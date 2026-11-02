# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors
"""Rootless discovery and root-only authenticated application transactions."""
from __future__ import annotations

import fcntl
import os
import secrets
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

from . import __version__, systemd, upgrade
from .app_release import AppReleaseError, discover, stage_release, version_tuple
from .deployment import DeploymentError, Tree
from .identity import display
from .install_manifest import StoreContext, StoreError, locked_store
from .lifecycle_lease import LeaseError
from .sysdetect import Capabilities
from .update_record import JOURNAL, STAGE_PREFIX, load


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


def mutate(*, local: str | None = None, recovery: bool = False,
           geteuid: Callable[[], int] = os.geteuid) -> int:
    if geteuid() != 0:
        print("sparehash: update requires root", file=sys.stderr)
        return 2
    tree = Tree()
    infra = upgrade.Infrastructure()
    stage: str | None = None
    old_handler = signal.getsignal(signal.SIGTERM)

    def interrupted(_number: int, _frame: object) -> None:
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, interrupted)
        with ExitStack() as stack:
            # Separate directory flock serialises this operation with recovery,
            # without holding the manifest lock during service or IPC waits.
            fd = tree.pin(stack, "/opt/sparehash")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if recovery:
                changed = upgrade.recover(tree, infra)
                print("Update recovery completed." if changed else "No pending application update; no version change.")
                return 0
            with locked_store(StoreContext(tree.trust)):
                upgrade.pending(tree)
                upgrade.validate_live(tree, upgrade._manifest(tree))
            selected = None if local is not None else discover()
            installed = upgrade.identity(tree.path(upgrade.SOURCE))
            if selected is not None and version_tuple(selected.version) <= version_tuple(installed[0]):
                print("Already current; no application or service changes.")
                return 0
            # Authenticate and render before downtime. Temporary raw archives and
            # full source are never treated as publication/recovery material.
            with tempfile.TemporaryDirectory(prefix=".update-verify-", dir=tree.path("/opt/sparehash")) as directory:
                source, version, commit = stage_release(Path(directory), local=Path(local).absolute() if local else None,
                                                         release=selected)
                if version_tuple(version) < version_tuple(installed[0]):
                    raise AppReleaseError("Application downgrades are not supported")
                if version == installed[0]:
                    if commit != installed[1]:
                        raise AppReleaseError("Conflicting source identities at the same version")
                    print("Identical release; no application or service changes.")
                    return 0
                stage = STAGE_PREFIX + secrets.token_hex(16)
                stage_digest = upgrade.candidate_bundle(tree, stage, source, Capabilities.detect())
                infra.verify_units(tree.path(stage + "/units"))
                infra.verify_identity(tree.path(stage + "/src"))
            upgrade.prepare(tree, stage, stage_digest, infra)
            code = upgrade.run_from_backup(tree)
            if code != 0:
                if load(tree) is None:
                    print("sparehash: update failed; previous application automatically restored", file=sys.stderr)
                else:
                    print("sparehash: update incomplete; mining inhibited. Run: sparehash update --recover", file=sys.stderr)
                return 1
            print(f"Installed SpareHash {version} (commit {commit}).")
            return 0
    except KeyboardInterrupt:
        print("sparehash: update interrupted; retain recovery material. Run: sudo sparehash update --recover\n"
              + upgrade.FALLBACK, file=sys.stderr)
        return 1
    except (AppReleaseError, DeploymentError, StoreError, LeaseError, OSError, RuntimeError,
            systemd.UnitError, subprocess.SubprocessError) as error:
        print(f"sparehash: update: {error}\nRecovery: sudo sparehash update --recover\n{upgrade.FALLBACK}", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, old_handler)
        if stage is not None:
            # Cleanup only a sealed, unchanged operation-owned bundle and only
            # when no journal reserves it. Foreign/incomplete material survives.
            try:
                if tree.read(JOURNAL) is None and os.path.lexists(tree.path(stage)):
                    upgrade.discard_bundle(tree, stage, backup=False)
            except (OSError, RuntimeError):
                pass
