from __future__ import annotations

from types import SimpleNamespace

from agent.services.planning_metrics_service import PlanningMetricsService
from agent.services.model_response_behavior_aggregation_service import ModelResponseBehaviorAggregationService


class _RunRepo:
    def __init__(self, runs):
        self.runs = list(runs)

    def get_recent(self, limit=500):
        return list(self.runs)[:limit]


class _Registry:
    def __init__(self, runs):
        self.planning_run_repo = _RunRepo(runs)


def _run(**kwargs):
    base = {
        "model_provider": "lmstudio",
        "model_name": "gemma",
        "planning_profile": "lmstudio_laptop",
        "parse_mode": "parse_failed",
        "repair_needed": True,
        "validation_success": False,
        "generated_task_count": 0,
        "mode_data": {},
        "parse_warnings": [],
        "created_at": 1.0,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_planning_metrics_service_reports_quality_and_trend(monkeypatch):
    import agent.services.planning_metrics_service as mod

    reg = _Registry(
        [
            _run(created_at=1.0, parse_mode="parse_failed", repair_needed=True, validation_success=False, generated_task_count=0),
            _run(created_at=2.0, parse_mode="parse_failed", repair_needed=True, validation_success=False, generated_task_count=0),
            _run(created_at=3.0, parse_mode="strict_json", repair_needed=False, validation_success=True, generated_task_count=2),
            _run(created_at=4.0, parse_mode="strict_json", repair_needed=False, validation_success=True, generated_task_count=2),
        ]
    )
    monkeypatch.setattr(mod, "get_repository_registry", lambda: reg)

    result = PlanningMetricsService().summarize(group_by_profile=True, limit=10)

    assert result["run_count"] == 4
    group = result["groups"][0]
    assert group["quality_score"] > 0
    assert group["trend_direction"] == "improving"
    assert group["sample_size_is_small"] is True


def test_behavior_aggregation_filters_by_profile_and_prompt_version(monkeypatch):
    import agent.services.model_response_behavior_aggregation_service as mod

    reg = _Registry(
        [
            _run(planning_profile="lmstudio_laptop", prompt_version_id="p1", parse_mode="strict_json", repair_needed=False),
            _run(planning_profile="other_profile", prompt_version_id="p2", parse_mode="parse_failed", repair_needed=True),
        ]
    )
    monkeypatch.setattr(mod, "get_repository_registry", lambda: reg)

    result = ModelResponseBehaviorAggregationService().aggregate(
        behavior_profile_name="lmstudio_laptop",
        prompt_version="p1",
        limit=10,
    )

    assert result["observed_run_count"] == 1
    assert result["primary_output_shape_distribution"] == {"unknown": 1.0}
