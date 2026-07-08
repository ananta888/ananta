"""Compare baseline and candidate benchmark runs."""

from __future__ import annotations

from typing import Any

from agent.performance.artifacts import build_performance_comparison_artifact


class PerformanceComparatorService:
    def compare(
        self,
        *,
        baseline_run: dict[str, Any],
        candidate_run: dict[str, Any],
        metric: str = "wall_time",
        min_relative_improvement_percent: float = 5.0,
        regression_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not baseline_run or not candidate_run:
            return {
                "schema": "performance_comparison_artifact.v1",
                "comparison_id": "cmp-missing",
                "baseline_run_id": baseline_run.get("run_id") if baseline_run else "",
                "candidate_run_id": candidate_run.get("run_id") if candidate_run else "",
                "metric_deltas": {},
                "confidence": 0.0,
                "noise_estimate": {"method": "none", "value": None},
                "pass_fail": "inconclusive",
                "reason_code": "missing_baseline_or_candidate",
                "code_delta": {},
                "config_delta": {},
                "data_delta": {},
                "hardware_delta": {},
                "caveats": ["baseline or candidate run missing"],
            }
        regression_passed = not regression_result or regression_result.get("status") == "candidate_passed"
        return build_performance_comparison_artifact(
            baseline_run=baseline_run,
            candidate_run=candidate_run,
            metric=metric,
            min_relative_improvement_percent=min_relative_improvement_percent,
            regression_passed=regression_passed,
        )


def get_performance_comparator_service() -> PerformanceComparatorService:
    return PerformanceComparatorService()
