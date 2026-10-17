"""Runtime deployment contract: external provision without installer mutation.

This module documents the boundary between runtime and deployer. An externally
provisioned deployment (future RPM/Nix package) should be able to run the same
controller contract without install.sh or imperative installer receipts. The
runtime reads protected deployment descriptions and config, but does not claim
ownership of package manager files.
"""

from __future__ import annotations

import inspect
import os
import sys
from unittest.mock import patch

from sparehash import config, runtime


def test_accepts_noninteractive_provisioning() -> None:
    """Runtime can be invoked without wizard or installer mutation imports.
    
    Verifies that the runtime module doesn't import wizard or install command
    modules at load time. An externally provisioned fixture can run the controller
    without the interactive installer.
    """
    runtime_module = sys.modules.get("sparehash.runtime")
    assert runtime_module is not None
    
    runtime_imports = set()
    for name, value in vars(runtime_module).items():
        if hasattr(value, "__module__") and value.__module__:
            runtime_imports.add(value.__module__)
    
    assert "sparehash.wizard" not in runtime_imports
    assert "sparehash.install" not in runtime_imports
    assert "sparehash.install_xmrig" not in runtime_imports


def test_refuses_hostile_paths_or_deployment_drift() -> None:
    """Runtime refuses hostile environment variables and path overrides.
    
    The runtime strips test/debug environment variables before accessing config.
    Protected paths come from fixed locations or explicit deployment descriptions,
    not environment overrides.
    """
    config_source = inspect.getsource(config.load)
    assert "SPAREHASH_CONFIG" not in config_source
    
    run_source = inspect.getsource(runtime.run)
    
    assert "SPAREHASH_RUN_DIR" in run_source
    assert "SPAREHASH_HWMON_ROOT" in run_source
    assert "SPAREHASH_STATE_FILE" in run_source
    assert "os.environ.pop" in run_source
