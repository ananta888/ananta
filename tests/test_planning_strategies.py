from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.services.planning_strategies import TemplatePlanningStrategy


@dataclass
class _PlannerStub:
    max_subtasks_per_goal: int = 10
    default_priority: str = "Medium"

    def _call_llm_with_retry(self, prompt: str, llm_config: dict) -> str:  # noqa: ARG002
        raise AssertionError("LLM should not be called in template strategy tests")


class _CatalogStub:
    def __init__(self, template_by_query: dict[str, list[dict[str, Any]]]) -> None:
        self._template_by_query = template_by_query

    def resolve_subtasks(self, query: str):  # noqa: ANN201
        return self._template_by_query.get(query)


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
