"""Retained non-destructive contracts for deferred local mining/foreground QA."""

import json
from typing import Any

import pytest

from qa import local_pool as pool
from qa import spin


class TestLocalPoolContract:
    """The bounded fixture answers Monero JSON-RPC; no external network."""

    @staticmethod
    def _call(method: str, request_id: int = 1) -> dict[str, Any]:
        line = json.dumps({"id": request_id, "method": method, "params": []}).encode()
        return json.loads(pool.response(line))

    def test_login_emits_the_fixed_local_job(self) -> None:
        reply = self._call("login")
        assert reply["id"] == 1
        assert reply["error"] is None
        result = reply["result"]
        assert result["id"] == "local-qa"
        assert result["status"] == "OK"
        assert result["job"]["job_id"] == "local-qa-only"
        assert result["job"]["algo"] == "rx/0"
        assert result["job"]["seed_hash"] == "00" * 32
        assert result["job"]["height"] == 1

    def test_submit_is_rejected_not_forwarded(self) -> None:
        reply = self._call("submit", request_id=9)
        assert reply["id"] == 9
        assert reply["result"] is None
        assert reply["error"] == {"code": -1, "message": "synthetic QA share rejected"}

    def test_subscribe_does_not_emit_a_job(self) -> None:
        reply = self._call("mining.subscribe")
        assert reply["result"] == {"status": "OK"}
        assert "job" not in reply["result"]

    @pytest.mark.parametrize("line", [
        b"", b"not json", b'{"id": 1}', b'{"method": "login"}',
        b'{"id": "x", "method": "login"}', b'{"id": 1, "method": 7}', b"[]",
    ])
    def test_rejects_invalid_request(self, line: bytes) -> None:
        with pytest.raises((SystemExit, json.JSONDecodeError)):
            _ = pool.response(line)


class TestForegroundProbe:
    @pytest.mark.parametrize("seconds", [0.0, -1.0, float("nan"), float("inf")])
    def test_rejects_nonpositive_or_nonfinite_duration(self, seconds: float) -> None:
        with pytest.raises(SystemExit):
            _ = spin.spin(seconds)

    def test_reports_positive_batches_per_actual_second(self) -> None:
        assert spin.spin(0.05) > 0
