# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Legacy GameMode removal only; new installations never edit user homes."""

from __future__ import annotations

import pwd
from pathlib import Path

from .gaming import User, UserError
from .gaming_ini import Home, edit


def unmerge(user: User) -> bool:
    """Remove only exact owned hooks after the uninstall receipt checks."""
    account = pwd.getpwnam(user.name)
    if account.pw_uid != user.uid:
        raise UserError(user.name)
    return edit(Home(Path(account.pw_dir), account.pw_uid, account.pw_gid), False)
