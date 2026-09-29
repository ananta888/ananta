from types import SimpleNamespace

from flask import Flask

from agent.services import planning_evaluation_service as module
from agent.services.planning_evaluation_service import PlanningEvaluationService
from agent.services.text_quality.models import (
    ContentKind,
    EvaluationStatus,
    TextQualityEvaluationResult,
)


class _Repo:
    def __init__(self, value=None):
        self.value = value
        self.saved = []

    def get_by_id(self, _):
        return self.value

    def get_by_run_id(self, _):
        return None

    def get_by_goal_id(self, _):
        return [SimpleNamespace(id="plan-1")]

    def get_by_plan_id(self, _):
        return [
            SimpleNamespace(
                title="Timeout begrenzen",
                description="Nach drei Fehlern 30 Sekunden warten.",
                rationale={},
                depends_on=[],
                verification_spec={},
            )
        ]

    def save(self, value):
        self.saved.append(value)
        return value


def _run():
    return SimpleNamespace(
        id="run-1",
        parse_mode="strict_json",
        validation_success=True,
        generated_task_count=1,
        expected_artifacts_count=1,
        verification_spec_count=1,
        status="completed",
        error_classification=None,
        repair_attempt_count=0,
        prompt_language="de",
        prompt_version_id="prompt-1",
        mode_data={},
    )


def test_planning_text_quality_is_feature_gated_and_uses_task_text(monkeypatch):
    run = _run()
    registry = SimpleNamespace(
        planning_run_repo=_Repo(run),
        planning_evaluation_repo=_Repo(),
        plan_repo=_Repo(),
        plan_node_repo=_Repo(),
    )
    monkeypatch.setattr(module, "get_repository_registry", lambda: registry)

    captured = {}
    result = TextQualityEvaluationResult(
        evaluation_id="eval-1",
        slop_score=0.1,
        depth_score=0.85,
        style_fit_score=0.9,
        criteria_version="1",
        language="de",
        content_kind=ContentKind.PLANNING_TASK_DESCRIPTION,
        confidence=1,
        status=EvaluationStatus.COMPLETED,
    )

    class Runtime:
        def evaluate(self, **kwargs):
            captured.update(kwargs)
            return result, SimpleNamespace(id="eval-1")

    import agent.services.text_quality.runtime_service as runtime_module

    monkeypatch.setattr(runtime_module, "get_text_quality_runtime_service", lambda: Runtime())
    app = Flask(__name__)
    app.config["AGENT_CONFIG"] = {
        "text_quality": {"enabled": True, "evaluate_planning_outputs": True}
    }
    with app.app_context():
        evaluation = PlanningEvaluationService().evaluate(
            planning_run_id="run-1", goal_id="goal-1", trace_id="trace-1"
        )
    assert captured["content_kind"] == ContentKind.PLANNING_TASK_DESCRIPTION
    assert "Timeout begrenzen" in captured["text"]
    assert evaluation.details["text_quality"]["evaluation_id"] == "eval-1"
    assert "__text_quality__" in run.mode_data

