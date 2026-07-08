from agent.services.performance_comparator_service import PerformanceComparatorService


def test_performance_comparator_passes_above_threshold():
    base = {"run_id": "b", "duration_seconds": 1.0, "metrics": {"wall_time": {"samples": [1.0]}}}
    cand = {"run_id": "c", "duration_seconds": 0.8, "metrics": {"wall_time": {"samples": [0.8]}}}
    result = PerformanceComparatorService().compare(baseline_run=base, candidate_run=cand)
    assert result["pass_fail"] == "passed"


def test_performance_comparator_marks_small_delta_inconclusive():
    base = {"run_id": "b", "duration_seconds": 1.0, "metrics": {"wall_time": {"samples": [1.0]}}}
    cand = {"run_id": "c", "duration_seconds": 0.99, "metrics": {"wall_time": {"samples": [0.99]}}}
    result = PerformanceComparatorService().compare(baseline_run=base, candidate_run=cand)
    assert result["pass_fail"] == "inconclusive"
