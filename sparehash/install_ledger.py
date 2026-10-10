# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Write-ahead installer receipts on the single canonical, directory-locked store."""

from __future__ import annotations

import grp
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from typing import TypeAlias, final, assert_never
from uuid import uuid4

from .deployment import DeploymentError, Tree
from .install_manifest import StoreContext, locked_store
from .xmrig_ledger import Action, Artifact, GroupIdentity, Installer, Membership, Operation, UserIdentity
from .xmrig_manifest import MAX_METADATA_BYTES, Manifest, format_manifest

Receipt: TypeAlias = UserIdentity | GroupIdentity | Membership | Artifact


@final
class Ledger:
    def __init__(self, tree: Tree) -> None:
        self.tree = tree
        self.install_id = uuid4().hex

    def context(self) -> StoreContext:
        try:
            gid = grp.getgrnam("sparehash").gr_gid
        except KeyError:
            gid = 0
        return StoreContext(self.tree.trust, gid=gid if self.tree.uid == 0 else None, create=True)

    def initialize(self) -> None:
        with locked_store(self.context()) as store:
            prior = store.read()
            manifest = prior.manifest if prior else Manifest("/opt/xmrig", ())
            if manifest.installer is None:
                store.write(replace(manifest, installer=Installer(1, "installing", (), (), (), ())))

    def begin(self, target: str, operation: Operation) -> int:
        with locked_store(self.context()) as store:
            prior = store.read()
            manifest = prior.manifest if prior else Manifest("/opt/xmrig", ())
            installer = manifest.installer or Installer(1, "installing", (), (), (), ())
            if any(item.state == "pending" for item in installer.actions):
                raise DeploymentError("recovery", "pending installer action requires inspection; do not delete the manifest or rotate the token")
            sequence = max((item.sequence for item in installer.actions), default=0) + 1
            action = Action(sequence, self.install_id, datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), operation, target, "pending")
            updated = replace(manifest, installer=replace(installer, phase="installing", actions=(*installer.actions, action)))
            reserve = 16384 if operation == "merge-gaming" else 8192
            if len(format_manifest(updated)) + reserve > MAX_METADATA_BYTES:
                raise DeploymentError("manifest-budget", "insufficient space for action receipt; no mutation performed")
            store.write(updated)
            return sequence

    def applied(self, sequence: int, receipts: tuple[Receipt, ...] = ()) -> None:
        with locked_store(self.context()) as store:
            prior = store.read()
            if prior is None or prior.manifest.installer is None:
                raise DeploymentError("ledger", "write-ahead record disappeared")
            installer = prior.manifest.installer
            identities = {(item.kind, item.name): item for item in installer.identities}
            memberships = {(item.user, item.group): item for item in installer.memberships}
            artifacts = {item.id: item for item in installer.artifacts}
            for receipt in receipts:
                match receipt:
                    case UserIdentity() | GroupIdentity():
                        old = identities.get((receipt.kind, receipt.name))
                        identities[(receipt.kind, receipt.name)] = replace(receipt, created=receipt.created or (old is not None and old.created))
                    case Membership():
                        old_membership = memberships.get((receipt.user, receipt.group))
                        memberships[(receipt.user, receipt.group)] = replace(receipt, added=receipt.added or (old_membership is not None and old_membership.added))
                    case Artifact():
                        old_artifact = artifacts.get(receipt.id)
                        artifacts[receipt.id] = replace(receipt, created=receipt.created or (old_artifact is not None and old_artifact.created))
                    case _:
                        assert_never(receipt)
            updated = replace(installer, identities=tuple(identities.values()), memberships=tuple(memberships.values()), artifacts=tuple(artifacts.values()),
                              actions=tuple(replace(item, state="applied") if item.sequence == sequence else item for item in installer.actions))
            store.write(replace(prior.manifest, installer=updated))

    def perform(self, action: tuple[str, Operation], mutate: Callable[[], tuple[Receipt, ...]]) -> None:
        sequence = self.begin(*action)
        self.applied(sequence, mutate())

    def ready(self) -> None:
        with locked_store(self.context()) as store:
            prior = store.read()
            if prior is None or prior.manifest.installer is None:
                raise DeploymentError("ledger", "missing installer ledger")
            if any(item.state != "applied" for item in prior.manifest.installer.actions):
                raise DeploymentError("ledger", "installation has unresolved actions")
            store.write(replace(prior.manifest, installer=replace(prior.manifest.installer, phase="ready")))
