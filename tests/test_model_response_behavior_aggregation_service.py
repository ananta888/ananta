from __future__ import annotations

from types import SimpleNamespace

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
        "model_name": "google/gemma-4-e4b",
        "planning_profile": "lmstudio_laptop",
        "prompt_version_id": "prompt-v1",
        "parse_mode": "strict_json",
        "validation_success": True,
        "generated_task_count": 2,
        "repair_needed": False,
        "parse_warnings": [],
        "mode_data": {"__output_shape__": "json_in_markdown_fence", "__truncated__": False},
        "created_at": 1.0,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_behavior_aggregation_reports_family_styles_and_stability(monkeypatch):
    import agent.services.model_response_behavior_aggregation_service as mod

    reg = _Registry(
        [
            _run(created_at=1.0, mode_data={"__output_shape__": "json_in_markdown_fence"}),
            _run(created_at=2.0, mode_data={"__output_shape__": "json_in_markdown_fence"}),
            _run(created_at=3.0, mode_data={"__output_shape__": "json_in_markdown_fence"}),
            _run(created_at=4.0, mode_data={"__output_shape__": "json_in_markdown_fence"}),
            _run(created_at=5.0, mode_data={"__output_shape__": "partial_json", "__truncated__": True}),
            _run(
                created_at=6.0,
                model_name="qwen/qwen-2.5-coder-32b-instruct",
                parse_mode="parse_failed",
                validation_success=False,
                generated_task_count=0,
                repair_needed=True,
                mode_data={"__output_shape__": "markdown_bullets"},
            ),
            _run(
                created_at=7.0,
                model_name="qwen/qwen-2.5-coder-32b-instruct",
                parse_mode="parse_failed",
                validation_success=False,
                generated_task_count=0,
                repair_needed=True,
                mode_data={"__output_shape__": "markdown_bullets"},
            ),
        ]
    )
    monkeypatch.setattr(mod, "get_repository_registry", lambda: reg)

    result = ModelResponseBehaviorAggregationService().aggregate(limit=20)

    assert result["observed_run_count"] == 7
    assert result["model_family_distribution"] == {"gemma": 0.7143, "qwen": 0.2857}
    assert result["preferred_output_shape"]["value"] == "json_in_markdown_fence"
    assert result["preferred_output_shape"]["state"] == "candidate"
    assert result["preferred_model_family"]["value"] == "gemma"

    gemma = next(row for row in result["family_behavior_profiles"] if row["model_family"] == "gemma")
    assert gemma["run_count"] == 5
    assert gemma["preferred_output_shape"]["value"] == "json_in_markdown_fence"
    assert gemma["preferred_output_shape"]["state"] == "stable"
    assert gemma["behavior_state"] == "stable"

    qwen = next(row for row in result["family_behavior_profiles"] if row["model_family"] == "qwen")
    assert qwen["run_count"] == 2
    assert qwen["preferred_output_shape"]["value"] == "markdown_bullets"
    assert qwen["preferred_output_shape"]["state"] == "candidate"
    assert qwen["behavior_state"] == "candidate"
