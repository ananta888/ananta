from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from agent.routes.tasks.auto_planner import AutoPlanner


@pytest.mark.parametrize(
    "template_goal",
    [
        "bug_fix",
        "feature",
        "refactor",
        "test",
        "repo_analysis",
        "sys_diag",
        "code_fix",
        "new_software_project",
        "project_evolution",
    ],
)
def test_auto_planner_template_ids_resolve_without_llm_call(app, monkeypatch, template_goal: str) -> None:
    def _fail_if_called(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("LLM should not be called when catalog template resolves")

    monkeypatch.setattr("agent.routes.tasks.auto_planner.generate_text", _fail_if_called)
    monkeypatch.setattr("agent.routes.tasks.auto_planner.config_repo", MagicMock(save=MagicMock()))
    planner = AutoPlanner()
    planner.configure(auto_start_autopilot=False)

    with app.app_context():
        result = planner.plan_goal(template_goal, create_tasks=False, use_template=True, use_repo_context=False)

    assert result.get("error") is None
    assert result.get("template_used") is True
    assert len(result.get("subtasks") or []) >= 1


@pytest.mark.parametrize(
    "goal_text",
    [
        "Bitte behebe den Login Fehler und liefere einen Fix",
        "Fix crash in payment flow and add regression tests",
        "Erstelle ein neues Feature fuer Export und Dokumentation",
        "Please refactor module boundaries and improve test coverage",
    ],
)
def test_auto_planner_keyword_matching_works_for_german_and_english_goals(app, monkeypatch, goal_text: str) -> None:
    def _fail_if_called(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("LLM should not be called when keyword template resolves")

    monkeypatch.setattr("agent.routes.tasks.auto_planner.generate_text", _fail_if_called)
    monkeypatch.setattr("agent.routes.tasks.auto_planner.config_repo", MagicMock(save=MagicMock()))
    planner = AutoPlanner()
    planner.configure(auto_start_autopilot=False)

    with app.app_context():
        result = planner.plan_goal(goal_text, create_tasks=False, use_template=True, use_repo_context=False)

    assert result.get("error") is None
    assert result.get("template_used") is True
    assert result.get("subtasks")


def test_auto_planner_falls_back_to_llm_when_no_catalog_or_blueprint_match(app, monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.routes.tasks.auto_planner.generate_text",
        lambda prompt, provider=None, model=None, base_url=None, api_key=None, timeout=30: json.dumps(
            [{"title": "Investigate", "description": "Analyze unknown objective", "priority": "High"}]
        ),
    )
    monkeypatch.setattr("agent.routes.tasks.auto_planner.config_repo", MagicMock(save=MagicMock()))
    planner = AutoPlanner()
    planner.configure(auto_start_autopilot=False)

    with app.app_context():
        result = planner.plan_goal(
            "xqzv unmatched objective for synthetic fallback path",
            create_tasks=False,
            use_template=True,
            use_repo_context=False,
        )

    assert result.get("error") is None
    assert result.get("template_used") is False
    assert len(result.get("subtasks") or []) == 1
