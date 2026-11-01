"""Read-only benchmark helpers; never execute a CPU workload in local tests."""

import importlib.util
from pathlib import Path

import pytest


def module():
    spec = importlib.util.spec_from_file_location("cpu_contention", Path("qa/cpu_contention.py"))
    assert spec is not None and spec.loader is not None
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_comparison_reports_absolute_throughput_and_degradation():
    params = {"workload": "sha256-input-mib-v1", "workers": 4, "seconds": 3, "repeats": 3, "warmup": 1}
    result = module().compare({**params, "median_mib_per_second": 100},
                              {**params, "median_mib_per_second": 95})
    assert result == {"baseline_mib_per_second": 100, "mining_mib_per_second": 95,
                      "degradation_percent": 5}


@pytest.mark.parametrize("key,value", [("workers", 2), ("seconds", 4), ("repeats", 4),
                                       ("warmup", 2), ("workload", "other")])
def test_comparison_refuses_mismatched_workloads(key, value):
    row = {"workload": "sha256-input-mib-v1", "workers": 4, "seconds": 3, "repeats": 3,
           "warmup": 1, "median_mib_per_second": 100}
    with pytest.raises(ValueError, match="parameters differ"):
        module().compare(row, {**row, key: value})


def test_measure_refuses_root_before_spawning(monkeypatch):
    benchmark = module()
    monkeypatch.setattr(benchmark.os, "geteuid", lambda: 0)
    with pytest.raises(ValueError, match="ordinary user"):
        benchmark.measure(4, 3, 3, 1)
