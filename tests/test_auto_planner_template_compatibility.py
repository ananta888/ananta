from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from agent.routes.tasks.auto_planner import AutoPlanner
from agent.services.planning_utils import match_goal_template


@pytest.mark.parametrize(
    "template_id",
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
def test_legacy_template_ids_still_produce_subtasks(template_id: str) -> None:
    subtasks = match_goal_template(template_id)
    assert subtasks is not None
    assert len(subtasks) >= 1


@pytest.mark.parametrize(
    "goal_text",
    [
        "Bitte behebe den Fehler im Auth-Flow",
        "Fix broken endpoint and add tests",
        "Neues Feature fuer Export erstellen",
        "Refactor the module and improve quality",
    ],
)
def test_keyword_matching_works_for_german_and_english(goal_text: str) -> None:
    subtasks = match_goal_template(goal_text)
    assert subtasks is not None
    assert len(subtasks) >= 1


def test_template_resolution_avoids_llm_call_when_template_matches(app, monkeypatch) -> None:
    def _fail_if_called(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("LLM should not be called when template resolves")

    monkeypatch.setattr("agent.routes.tasks.auto_planner.generate_text", _fail_if_called)
    monkeypatch.setattr("agent.routes.tasks.auto_planner.config_repo", MagicMock(save=MagicMock()))
    planner = AutoPlanner()
    planner.configure(auto_start_autopilot=False)

    with app.app_context():
        result = planner.plan_goal("Fix critical bug in auth flow", create_tasks=False, use_template=True, use_repo_context=False)

    assert result.get("error") is None
    assert result.get("template_used") is True
    assert result.get("subtasks")


def test_fallback_to_llm_still_works_when_no_template_matches(app, monkeypatch) -> None:
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
        result = planner.plan_goal("zzzz unmatched planner request", create_tasks=False, use_template=True, use_repo_context=False)

    assert result.get("error") is None
    assert result.get("template_used") is False
    assert len(result.get("subtasks") or []) == 1
