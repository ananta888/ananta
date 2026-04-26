from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from flask import current_app

from agent.services.blueprint_planning_adapter import get_blueprint_planning_adapter
from agent.services.execution_focused_planning import match_execution_focused_goal_template
from agent.services.hub_llm_service import get_hub_llm_service
from agent.services.planning_template_catalog import get_planning_template_catalog
from agent.services.planning_utils import (
    build_planning_prompt,
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
    def execute(
        self,
        planner: PlannerLike,
        goal: str,
        context: str | None,
        mode: str = "generic",
        mode_data: Optional[dict] = None,
    ) -> PlanningStrategyResult | None: ...


class TemplatePlanningStrategy:
    def __init__(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._catalog = get_planning_template_catalog()
        self._blueprint_adapter = get_blueprint_planning_adapter()

    def execute(
        self,
        planner: PlannerLike,
        goal: str,
        context: str | None,
        mode: str = "generic",
        mode_data: Optional[dict] = None,
    ) -> PlanningStrategyResult | None:
        if not self._enabled:
            return None

        query_candidates: list[str] = []
        if mode and mode != "generic":
            query_candidates.append(str(mode).strip())
            template_id_hint = str((mode_data or {}).get("template_id") or "").strip()
            if template_id_hint:
                query_candidates.append(template_id_hint)
        query_candidates.append(str(goal).strip())
        query_candidates = [candidate for candidate in dict.fromkeys(query_candidates) if candidate]

        for candidate in query_candidates:
            catalog_subtasks = self._catalog.resolve_subtasks(candidate)
            if catalog_subtasks:
                return PlanningStrategyResult(
                    subtasks=catalog_subtasks[: planner.max_subtasks_per_goal],
                    raw_response=None,
                    context=context,
                    template_used=True,
                    planning_mode="template",
                )

            blueprint_subtasks = self._blueprint_adapter.resolve_subtasks(candidate)
            if blueprint_subtasks:
                return PlanningStrategyResult(
                    subtasks=blueprint_subtasks[: planner.max_subtasks_per_goal],
                    raw_response=None,
                    context=context,
                    template_used=True,
                    planning_mode="template",
                )

        execution_focused_subtasks = match_execution_focused_goal_template(goal)
        if execution_focused_subtasks:
            return PlanningStrategyResult(
                subtasks=execution_focused_subtasks[: planner.max_subtasks_per_goal],
                raw_response=None,
                context=context,
                template_used=True,
                planning_mode="template",
            )
        return None


class LLMPlanningStrategy:
    def __init__(self, use_repo_context: bool) -> None:
        self._use_repo_context = bool(use_repo_context)

    @staticmethod
    def _build_planning_repair_prompt(
        *,
        goal: str,
        context: str | None,
        max_subtasks: int,
        previous_output: str,
        mode: str = "generic",
        mode_data: Optional[dict] = None,
    ) -> str:
        prompt = (
            "Der vorherige Planungs-Output war unstrukturiert oder leer.\n"
            "Erzeuge jetzt einen reparierten Plan als strikt valides JSON.\n\n"
            f"ZIEL:\n{goal}\n\n"
        )
        if mode != "generic" and mode_data:
            prompt = f"{prompt}STEUERUNGSDATEN (Modus: {mode}):\n{json.dumps(mode_data, indent=2)}\n\n"

        prompt = (
            f"{prompt}"
            "ANFORDERUNGEN:\n"
            f"1. Liefere mindestens 3 und hoechstens {max_subtasks} Teilaufgaben\n"
            "2. Jede Teilaufgabe muss title, description, priority enthalten\n"
            "3. priority nur: High, Medium, Low\n"
            "4. depends_on als Liste von Schrittnummern als Strings (z.B. [\"1\"])\n"
            "5. Keine Erklaerungen, keine Markdown-Fences\n\n"
            "AUSGABEFORMAT (nur JSON-Array):\n"
            "[\n"
            '  {"title":"...","description":"...","priority":"High|Medium|Low","depends_on":[]}\n'
            "]\n\n"
            "VORHERIGER FEHLERHAFTER OUTPUT:\n"
            f"{(previous_output or '').strip()[:3000]}"
        )
        if context:
            prompt = f"{prompt}\n\nKONTEXT:\n{context}"
        return prompt

    def execute(
        self,
        planner: PlannerLike,
        goal: str,
        context: str | None,
        mode: str = "generic",
        mode_data: Optional[dict] = None,
    ) -> PlanningStrategyResult | None:
        resolved_context = context
        if self._use_repo_context and not resolved_context:
            repo_context = try_load_repo_context(goal)
            if repo_context:
                resolved_context = repo_context

        if mode != "generic" and mode_data:
            mode_context = (
                f"{(resolved_context or '').strip()}\n\n"
                f"STEUERUNGSDATEN (Modus: {mode}):\n"
                f"{json.dumps(mode_data, indent=2)}"
            )
            resolved_context = mode_context.strip()

        prompt = build_planning_prompt(goal, resolved_context, planner.max_subtasks_per_goal)
        llm_config = current_app.config.get("AGENT_CONFIG", {}).get("llm_config", {})
        raw_response = planner._call_llm_with_retry(prompt, llm_config)
        subtasks = parse_subtasks_from_llm_response(raw_response, default_priority=planner.default_priority)
        if not subtasks:
            repair_prompt = self._build_planning_repair_prompt(
                goal=goal,
                context=resolved_context,
                max_subtasks=planner.max_subtasks_per_goal,
                previous_output=raw_response,
                mode=mode,
                mode_data=mode_data,
            )
            repaired_response = planner._call_llm_with_retry(repair_prompt, llm_config)
            repaired_subtasks = parse_subtasks_from_llm_response(
                repaired_response,
                default_priority=planner.default_priority,
            )
            if repaired_subtasks:
                raw_response = repaired_response
                subtasks = repaired_subtasks
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

    def execute(
        self,
        planner: PlannerLike,
        goal: str,
        context: str | None,
        mode: str = "generic",
        mode_data: Optional[dict] = None,
    ) -> PlanningStrategyResult | None:
        hub_llm = get_hub_llm_service()
        copilot_config = hub_llm.resolve_copilot_config()
        if (
            not copilot_config.get("enabled")
            or not copilot_config.get("supports_planning")
            or not copilot_config.get("active")
        ):
            return None

        resolved_context = context
        if self._use_repo_context and not resolved_context:
            repo_context = try_load_repo_context(goal)
            if repo_context:
                resolved_context = repo_context

        if mode != "generic" and mode_data:
            mode_context = (
                f"{(resolved_context or '').strip()}\n\n"
                f"STEUERUNGSDATEN (Modus: {mode}):\n"
                f"{json.dumps(mode_data, indent=2)}"
            )
            resolved_context = mode_context.strip()

        prompt = build_planning_prompt(goal, resolved_context, planner.max_subtasks_per_goal)
        response = hub_llm.plan_with_copilot(prompt=prompt, timeout=getattr(planner, "llm_timeout", None))
        raw_response = str(response.get("text") or "")
        subtasks = parse_subtasks_from_llm_response(raw_response, default_priority=planner.default_priority)
        if not subtasks:
            # Hub-Copilot darf den Planungsfluss nicht mit leerem/ungueltigem Output blockieren.
            # In diesem Fall faellt die Strategie bewusst auf den naechsten Planungsweg (LLM) durch.
            return None
        return PlanningStrategyResult(
            subtasks=subtasks,
            raw_response=raw_response,
            context=resolved_context,
            template_used=False,
            planning_mode="hub_copilot",
        )
