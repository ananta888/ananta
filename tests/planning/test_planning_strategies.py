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
        self.prompts: list[str] = []

    def _call_llm_with_retry(self, prompt: str, llm_config: dict, *, temperature=None) -> str:  # noqa: ARG002
        self.prompts.append(prompt)
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
            {
                "title": "Projektdateien erstellen",
                "description": "README und src/tests anlegen in src/app.py und tests/test_app.py",
                "priority": "High",
                "task_kind": "coding",
                "expected_artifacts": [{"kind": "workspace_change", "required": True}],
                "verification_spec": {"tests": True},
            },
            {
                "title": "Tests ausführen",
                "description": "pytest run und Ergebnis dokumentieren",
                "priority": "High",
                "task_kind": "testing",
                "expected_artifacts": [{"kind": "test_report", "required": True}],
                "verification_spec": {"tests": True},
            },
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
            {
                "title": "Projektdateien erstellen",
                "description": "README und src/tests anlegen in src/app.py und tests/test_app.py",
                "priority": "High",
                "task_kind": "coding",
                "expected_artifacts": [{"kind": "workspace_change", "required": True}],
                "verification_spec": {"tests": True},
            },
            {
                "title": "Tests ausführen",
                "description": "pytest run und Ergebnis dokumentieren",
                "priority": "High",
                "task_kind": "testing",
                "expected_artifacts": [{"kind": "test_report", "required": True}],
                "verification_spec": {"tests": True},
            },
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


def test_llm_planning_strategy_new_project_prompt_allows_model_native_structure(app, monkeypatch) -> None:
    strategy = LLMPlanningStrategy(use_repo_context=False)
    planner = _LLMPlannerStub(
        responses=[
            '[{"title":"Task 1","description":"Desc","priority":"High","depends_on":[]}]',
        ]
    )

    def _parse(raw: str, default_priority: str = "Medium"):  # noqa: ARG001
        return (
            [
                {
                    "title": "Implement API endpoint",
                    "description": "implement file src/app.py endpoint for fibonacci",
                    "priority": "High",
                    "depends_on": [],
                    "task_kind": "coding",
                    "expected_artifacts": [{"kind": "workspace_change", "required": True}],
                    "verification_spec": {"tests": True},
                }
            ],
            {
                "parse_mode": "strict_json",
                "confidence": "high",
                "warnings": [],
                "output_shape": "json_array",
                "detected_shapes": ["json_array"],
                "format_error_codes": [],
                "parser_trace": [],
            },
        )

    monkeypatch.setattr("agent.services.planning_strategies.parse_subtasks_with_diagnostics", _parse)
    # Force English prompt lookup: this test asserts the presence of the
    # EN-only "Markdown fences are acceptable" guidance from
    # config/planning_prompts.default.json. The default profile resolves
    # to prompt_language="de" when no LLM model is configured (global
    # fallback), which yields the inline-fallback prompt in the full
    # pytest run. Pin the language explicitly to keep the test stable.
    planner._goal_effective_config = {
        "planning_policy": {"prompt_language": "en"},
    }
    with app.app_context():
        result = strategy.execute(
            planner,
            goal="Build a new software project",
            context=None,
            mode="new_software_project",
            mode_data={"project_idea": "demo"},
        )

    assert result is not None
    assert planner.calls == 1
    prompt = planner.prompts[0]
    assert "Markdown fences are acceptable" in prompt
    assert "structured task list" in prompt.lower()


def test_new_project_execution_repair_prompt_compacts_previous_output() -> None:
    strategy = LLMPlanningStrategy(use_repo_context=False)
    long_previous = '{"title":"' + ("x" * 3000) + '"}'
    prompt = strategy._build_new_project_execution_repair_prompt(
        goal="Build a new software project",
        context="ctx",
        max_subtasks=8,
        previous_output=long_previous,
        mode_data={"huge": "y" * 5000},
    )

    assert "[truncated]" in prompt
    assert "Prefer JSON" in prompt or "structured and parseable" in prompt
    assert len(prompt) < 6000


def test_llm_planning_strategy_uses_truncation_repair_prompt(app, monkeypatch) -> None:
    strategy = LLMPlanningStrategy(use_repo_context=False)
    planner = _LLMPlannerStub(
        responses=[
            "plain analysis text",
            '{"title":"Task 1","description":"Desc","priority":"High",',
            '[{"title":"Task 1","description":"Desc","priority":"High","depends_on":[]}]',
        ]
    )

    def _parse(raw: str, default_priority: str = "Medium"):  # noqa: ARG001
        if raw == "plain analysis text":
            return [], {
                "parse_mode": "parse_failed",
                "confidence": "low",
                "warnings": [],
                "output_shape": "",
                "detected_shapes": [],
                "format_error_codes": [],
                "parser_trace": [],
            }
        if raw.startswith('{"title":"Task 1"'):
            return [], {
                "parse_mode": "parse_failed",
                "confidence": "low",
                "warnings": ["truncated_json_recovered"],
                "output_shape": "json_object",
                "detected_shapes": ["json_object"],
                "format_error_codes": ["unterminated_string"],
                "parser_trace": [],
            }
        return (
            [
                {
                    "title": "Implement API endpoint",
                    "description": "implement file src/app.py endpoint for fibonacci",
                    "priority": "High",
                    "depends_on": [],
                    "task_kind": "coding",
                    "expected_artifacts": [{"kind": "workspace_change", "required": True}],
                    "verification_spec": {"tests": True},
                }
            ],
            {
                "parse_mode": "strict_json",
                "confidence": "high",
                "warnings": [],
                "output_shape": "json_array",
                "detected_shapes": ["json_array"],
                "format_error_codes": [],
                "parser_trace": [],
            },
        )

    monkeypatch.setattr("agent.services.planning_strategies.parse_subtasks_with_diagnostics", _parse)

    class _DisabledHub:
        def resolve_copilot_config(self):
            return {"enabled": False, "supports_planning": False, "active": False}

    monkeypatch.setattr("agent.services.planning_strategies.get_hub_llm_service", lambda: _DisabledHub())

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
    assert any("abgeschnitten" in prompt.lower() for prompt in planner.prompts[1:])
    assert any("prefer json" in prompt.lower() or "structured and parseable" in prompt.lower() for prompt in planner.prompts[1:])
