from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from flask import current_app

from agent.services.hub_llm_service import get_hub_llm_service
from agent.services.planning_utils import (
    build_planning_prompt,
    match_goal_template,
    parse_subtasks_from_llm_response,
    try_load_repo_context,
)


@dataclass(frozen=True)
class PlanningStrategyResult:
    subtasks: list[dict[str, Any]]
    raw_response: str | None
    context: str | None
    template_used: bool
    planning_mode: str


class PlannerLike(Protocol):
    max_subtasks_per_goal: int
    default_priority: str

    def _call_llm_with_retry(self, prompt: str, llm_config: dict) -> str: ...


class PlanningStrategy(Protocol):
    def execute(self, planner: PlannerLike, goal: str, context: str | None) -> PlanningStrategyResult | None: ...


class TemplatePlanningStrategy:
    def __init__(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    def execute(self, planner: PlannerLike, goal: str, context: str | None) -> PlanningStrategyResult | None:
        if not self._enabled:
            return None
        template_subtasks = match_goal_template(goal)
        if not template_subtasks:
            return None
        return PlanningStrategyResult(
            subtasks=template_subtasks[: planner.max_subtasks_per_goal],
            raw_response=None,
            context=context,
            template_used=True,
            planning_mode="template",
        )


class LLMPlanningStrategy:
    def __init__(self, use_repo_context: bool) -> None:
        self._use_repo_context = bool(use_repo_context)

    def execute(self, planner: PlannerLike, goal: str, context: str | None) -> PlanningStrategyResult | None:
        resolved_context = context
        if self._use_repo_context and not resolved_context:
            repo_context = try_load_repo_context(goal)
            if repo_context:
                resolved_context = repo_context

        prompt = build_planning_prompt(goal, resolved_context, planner.max_subtasks_per_goal)
        llm_config = current_app.config.get("AGENT_CONFIG", {}).get("llm_config", {})
        raw_response = planner._call_llm_with_retry(prompt, llm_config)
        subtasks = parse_subtasks_from_llm_response(raw_response, default_priority=planner.default_priority)
        return PlanningStrategyResult(
            subtasks=subtasks,
            raw_response=raw_response,
            context=resolved_context,
            template_used=False,
            planning_mode="llm",
        )


class HubCopilotPlanningStrategy:
    def __init__(self, use_repo_context: bool) -> None:
        self._use_repo_context = bool(use_repo_context)

    def execute(self, planner: PlannerLike, goal: str, context: str | None) -> PlanningStrategyResult | None:
        hub_llm = get_hub_llm_service()
        copilot_config = hub_llm.resolve_copilot_config()
        if not copilot_config.get("enabled") or not copilot_config.get("supports_planning") or not copilot_config.get("active"):
            return None

        resolved_context = context
        if self._use_repo_context and not resolved_context:
            repo_context = try_load_repo_context(goal)
            if repo_context:
                resolved_context = repo_context

        prompt = build_planning_prompt(goal, resolved_context, planner.max_subtasks_per_goal)
        response = hub_llm.plan_with_copilot(prompt=prompt, timeout=getattr(planner, "llm_timeout", None))
        raw_response = str(response.get("text") or "")
        subtasks = parse_subtasks_from_llm_response(raw_response, default_priority=planner.default_priority)
        return PlanningStrategyResult(
            subtasks=subtasks,
            raw_response=raw_response,
            context=resolved_context,
            template_used=False,
            planning_mode="hub_copilot",
        )
