"""Destructive QA has explicit approval gates; host tests only exercise refusal."""
import os
import subprocess
import sys
from pathlib import Path


def test_update_vm_driver_requires_explicit_disposable_approval() -> None:
    script = Path(__file__).resolve().parents[1] / "qa/update_acceptance.py"
    result = subprocess.run([sys.executable, "-I", "-B", str(script), "--yes", "--phase", "before-reboot",
                             "--source-sha256", "0" * 64],
                            env={"PATH": os.defpath}, capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 2
    assert "SPAREHASH_QA_DISPOSABLE_VM=YES required" in result.stderr
