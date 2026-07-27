from __future__ import annotations

from copy import deepcopy
from functools import lru_cache

import pytest

from scripts.benchmark.semantic_media_program import evaluate
from scripts.benchmark.semantic_media_program_executor import METRIC_FIELDS, run_benchmark

pytestmark = pytest.mark.integration


@lru_cache(maxsize=1)
def _measured_report() -> dict:
    return run_benchmark(timeout_seconds=180)


def test_executor_measures_the_complete_matrix_and_explicit_availability() -> None:
    report = _measured_report()
    rows = report["rows"]
    assert len(rows) == 32
    live = [row for row in rows if row["topology"] != "offline"]
    offline = [row for row in rows if row["topology"] == "offline"]
    assert {
        (row["topology"], row["window_seconds"], row["receivers"])
        for row in live
    } == {
        (topology, window, receivers)
        for topology in ("pair", "group", "evidence")
        for window in (2, 10, 20)
        for receivers in (2, 10, 100)
    }
    assert {row["offline_factor"] for row in offline} == {1, 2, 5, 10, 20}
    for row in rows:
        assert row["ordinary"]["binding_sha256"] == row["comparison_binding_sha256"]
        assert row["semantic"]["binding_sha256"] == row["comparison_binding_sha256"]
        for mode in ("ordinary", "semantic"):
            assert set(row[mode]["values"]) == set(METRIC_FIELDS)
            for name, state in row[mode]["availability"].items():
                if state["status"] != "measured":
                    assert row[mode]["values"][name] is None
                    assert state["reason_code"]
    evidence, measurements = evaluate(report)
    assert measurements["verified_runs"] == 1
    assert measurements["row_count"] == 32
    # This environment may or may not expose all hardware counters.  Either
    # outcome is valid, but unavailable telemetry can never become a pass.
    if measurements["unavailable_metric_count"]:
        assert evidence.status == "failed"


def test_offline_saturation_uses_product_policies_and_tampering_is_rejected() -> None:
    report = _measured_report()
    offline = [row for row in report["rows"] if row["topology"] == "offline"]
    assert len(offline) == 5
    for row in offline:
        baseline = row["ordinary"]["offline_saturation"]
        saturated = row["semantic"]["offline_saturation"]
        assert baseline["saturation_active"] is False and baseline["resolver_cycles"] == 0
        assert saturated["saturation_active"] is True and saturated["resolver_cycles"] > 0
        assert all(
            saturated[name] is True
            for name in (
                "resource_policy_admitted",
                "live_pressure_blocked",
                "foreground_pressure_blocked",
                "budget_overrun_blocked",
                "resource_limit_stopped",
            )
        )
        assert saturated["sound_p50_ms"] <= saturated["sound_p95_ms"] <= saturated["sound_p99_ms"]
        assert saturated["text_p50_ms"] <= saturated["text_p95_ms"] <= saturated["text_p99_ms"]
        assert saturated["ui_p50_ms"] <= saturated["ui_p95_ms"] <= saturated["ui_p99_ms"]

    tampered = deepcopy(report)
    tampered["rows"][0]["comparison_binding_sha256"] = "f" * 64
    evidence, _ = evaluate(tampered)
    assert evidence.status == "failed"
    assert {
        "program_benchmark_comparison_binding_mismatch",
        "program_benchmark_mode_binding_mismatch",
    } <= set(evidence.reason_codes)
