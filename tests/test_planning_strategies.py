from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.services.planning_strategies import LLMPlanningStrategy, TemplatePlanningStrategy


@dataclass
class _PlannerStub:
    max_subtasks_per_goal: int = 10
    default_priority: str = "Medium"

    def _call_llm_with_retry(self, prompt: str, llm_config: dict) -> str:  # noqa: ARG002
        raise AssertionError("LLM should not be called in template strategy tests")


class _CatalogStub:
    def __init__(self, template_by_query: dict[str, list[dict[str, Any]]]) -> None:
        self._template_by_query = template_by_query

    def resolve_template(self, query: str):  # noqa: ANN201
        subtasks = self._template_by_query.get(query)
        if subtasks is None:
            return None
        return {"id": query, "title": query, "subtasks": subtasks}


class _AdapterStub:
    def __init__(self, subtasks_by_query: dict[str, list[dict[str, Any]]]) -> None:
        self._subtasks_by_query = subtasks_by_query

    def resolve_subtasks(self, query: str):  # noqa: ANN201
        return self._subtasks_by_query.get(query)


def test_template_planning_strategy_resolves_catalog_first() -> None:
    strategy = TemplatePlanningStrategy(enabled=True)
    strategy._catalog = _CatalogStub(
        {"code_fix": [{"title": "A", "description": "B", "priority": "High", "depends_on": []}]}
    )
    strategy._blueprint_adapter = _AdapterStub(
        {"code_fix": [{"title": "X", "description": "Y", "priority": "Low", "depends_on": []}]}
    )

    result = strategy.execute(_PlannerStub(), goal="irrelevant", context=None, mode="code_fix")

    assert result is not None
    assert result.template_used is True
    assert result.planning_mode == "template"
    assert result.subtasks[0]["title"] == "A"
    assert result.subtasks[0]["template_name"] == "code_fix"


def test_template_planning_strategy_uses_blueprint_adapter_when_catalog_has_no_match() -> None:
    strategy = TemplatePlanningStrategy(enabled=True)
    strategy._catalog = _CatalogStub({})
    strategy._blueprint_adapter = _AdapterStub(
        {
            "Please use TDD blueprint": [
                {"title": "BP", "description": "desc", "priority": "Medium", "depends_on": []}
            ]
        }
    )

    result = strategy.execute(_PlannerStub(), goal="Please use TDD blueprint", context=None)

    assert result is not None
    assert result.subtasks[0]["title"] == "BP"


def test_template_planning_strategy_uses_execution_focused_fallback_last() -> None:
    strategy = TemplatePlanningStrategy(enabled=True)
    strategy._catalog = _CatalogStub({})
    strategy._blueprint_adapter = _AdapterStub({})

    result = strategy.execute(_PlannerStub(), goal="Implement Python Fibonacci helper with tests", context=None)

    assert result is not None
    assert any("Fibonacci" in str(item.get("title") or "") for item in result.subtasks)


def test_template_planning_strategy_returns_none_when_disabled() -> None:
    strategy = TemplatePlanningStrategy(enabled=False)

    result = strategy.execute(_PlannerStub(), goal="bug fix", context=None)

    assert result is None


@dataclass
class _LLMPlannerStub:
    responses: list[str]
    max_subtasks_per_goal: int = 10
    default_priority: str = "Medium"

    def __post_init__(self) -> None:
        self.calls = 0

    def _call_llm_with_retry(self, prompt: str, llm_config: dict) -> str:  # noqa: ARG002
        idx = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[idx]


def test_llm_planning_strategy_repair_for_new_project_enforces_execution_coverage(app, monkeypatch) -> None:
    strategy = LLMPlanningStrategy(use_repo_context=False)
    planner = _LLMPlannerStub(responses=["first-response", "repair-response"])

    def _parse(raw: str, default_priority: str = "Medium"):  # noqa: ARG001
        if raw == "first-response":
            return [
                {"title": "Scope klären", "description": "Anforderungen sammeln", "priority": "High"},
                {"title": "Blueprint", "description": "Architektur skizzieren", "priority": "High"},
            ]
        return [
            {"title": "Projektdateien erstellen", "description": "README und src/tests anlegen", "priority": "High"},
            {"title": "Tests ausführen", "description": "pytest run und Ergebnis dokumentieren", "priority": "High"},
        ]

    monkeypatch.setattr("agent.services.planning_strategies.parse_subtasks_from_llm_response", _parse)
    with app.app_context():
        result = strategy.execute(
            planner,
            goal="Build a new software project",
            context=None,
            mode="new_software_project",
            mode_data={"project_idea": "demo"},
        )
    assert result is not None
    assert planner.calls == 2
    texts = [f"{s.get('title','')} {s.get('description','')}".lower() for s in result.subtasks]
    assert any(("datei" in t) or ("readme" in t) or ("src" in t) for t in texts)
    assert any(("test" in t) or ("pytest" in t) for t in texts)


def test_llm_planning_strategy_new_project_third_attempt_when_empty(app, monkeypatch) -> None:
    strategy = LLMPlanningStrategy(use_repo_context=False)
    planner = _LLMPlannerStub(responses=["first", "repair", "strict-repair"])

    def _parse(raw: str, default_priority: str = "Medium"):  # noqa: ARG001
        if raw in {"first", "repair"}:
            return []
        return [
            {"title": "Projektdateien erstellen", "description": "README und src/tests anlegen", "priority": "High"},
            {"title": "Tests ausführen", "description": "pytest run und Ergebnis dokumentieren", "priority": "High"},
        ]

    monkeypatch.setattr("agent.services.planning_strategies.parse_subtasks_from_llm_response", _parse)
    with app.app_context():
        result = strategy.execute(
            planner,
            goal="Build a new software project",
            context=None,
            mode="new_software_project",
            mode_data={"project_idea": "demo"},
        )
    assert result is not None
    assert planner.calls == 3
    assert len(result.subtasks) >= 2
