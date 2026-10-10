"""Controller entry preflight without host service or configuration access."""

from __future__ import annotations

import io
import os
from contextlib import redirect_stderr
from unittest.mock import patch

from sparehash import runtime


def test_controller_denies_without_notify_socket_before_config_access() -> None:
    environment = {
        "SPAREHASH_RUN_DIR": "/untrusted/run",
        "SPAREHASH_HWMON_ROOT": "/untrusted/hwmon",
        "SPAREHASH_STATE_FILE": "/untrusted/state",
    }
    with (
        patch.dict(os.environ, environment, clear=True),
        patch.object(runtime.os, "geteuid", return_value=0),
        patch.object(runtime.config, "load", side_effect=AssertionError("configuration accessed")) as load,
        redirect_stderr(io.StringIO()) as errors,
    ):
        result = runtime.run()
        assert result == 1
        assert "NOTIFY_SOCKET missing" in errors.getvalue()
        assert all(name not in os.environ for name in environment)
        load.assert_not_called()


def test_controller_denies_unprivileged_entry_before_config_access() -> None:
    with (
        patch.object(runtime.os, "geteuid", return_value=1000),
        patch.object(runtime.config, "load", side_effect=AssertionError("configuration accessed")) as load,
    ):
        result = runtime.run()
        assert result == 1
        load.assert_not_called()
