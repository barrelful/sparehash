# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors
"""Single bounded app-update journal, shared with the startup safety boundary."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, fields, replace
from typing import Literal, TypedDict, Unpack

from .deployment import DeploymentError, Tree

JOURNAL = "/var/lib/sparehash/update.json"
BACKUP = "/opt/sparehash/.update-backup"
STAGE_PREFIX = "/opt/sparehash/.update-stage-"
Phase = Literal["prepared", "installing", "restoring", "committed"]


@dataclass(frozen=True)
class UpdateRecord:
    operation_id: str
    phase: Phase
    active: bool
    boot: str
    stage_digest: str
    backup_digest: str
    preserved: dict[str, str | None]

    @property
    def stage(self) -> str:
        return STAGE_PREFIX + self.operation_id


def load(tree: Tree) -> UpdateRecord | None:
    data = tree.read(JOURNAL)
    if data is None:
        return None
    if len(data) > 16384:
        raise DeploymentError("update-journal", "journal exceeds byte bound; mining remains inhibited")
    try:
        def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate field")
                result[key] = value
            return result

        doc = json.loads(data, object_pairs_hook=unique)
        if not isinstance(doc, dict) or set(doc) != {field.name for field in fields(UpdateRecord)}:
            raise ValueError("invalid fields")
        if (not isinstance(doc["operation_id"], str) or re.fullmatch(r"[0-9a-f]{32}", doc["operation_id"]) is None
                or doc["phase"] not in ("prepared", "installing", "restoring", "committed")
                or type(doc["active"]) is not bool or doc["boot"] not in ("enabled", "disabled")
                or not isinstance(doc["stage_digest"], str) or re.fullmatch(r"[0-9a-f]{64}", doc["stage_digest"]) is None
                or not isinstance(doc["backup_digest"], str)
                or (doc["backup_digest"] != "" and re.fullmatch(r"[0-9a-f]{64}", doc["backup_digest"]) is None)
                or (doc["phase"] in ("installing", "restoring") and not doc["backup_digest"])
                or not isinstance(doc["preserved"], dict)
                or set(doc["preserved"]) != set(PRESERVED)
                or any(v is not None and (not isinstance(v, str) or re.fullmatch(r"[0-9a-f]{64}", v) is None)
                       for v in doc["preserved"].values())):
            raise ValueError("invalid journal values")
        return UpdateRecord(**doc)
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise DeploymentError("update-journal", f"corrupt record: {error}; mining remains inhibited") from error


PRESERVED = ("/etc/sparehash/config.toml", "/etc/sparehash/xmrig.json",
             "/etc/sparehash/xmrig.runtime.json", "/var/lib/sparehash/state.json")


class Changes(TypedDict, total=False):
    phase: Phase
    backup_digest: str
    preserved: dict[str, str | None]


def save(tree: Tree, record: UpdateRecord, **changes: Unpack[Changes]) -> UpdateRecord:
    updated = replace(record, **changes)
    tree.write(JOURNAL, (json.dumps(asdict(updated), sort_keys=True, separators=(",", ":")).encode(), 0o600, 0))
    return updated
