"""Release smoke safety guard; never invoke destructive VM actions in unit tests."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_release_smoke_refuses_when_disposable_vm_opt_in_missing(tmp_path: Path) -> None:
    # Given
    script = Path(__file__).resolve().parents[1] / "qa/qa_release_smoke.sh"
    evidence = tmp_path / "evidence"
    environment = {key: value for key, value in os.environ.items()
                   if key != "SPAREHASH_QA_DISPOSABLE_VM"}
    environment["EVIDENCE_DIR"] = str(evidence)
    # When
    result = subprocess.run(["bash", str(script)], env=environment,
                            capture_output=True, text=True, timeout=5, check=False)
    # Then
    assert result.returncode != 0
    assert "SPAREHASH_QA_DISPOSABLE_VM=YES" in result.stderr
    assert not evidence.exists()
