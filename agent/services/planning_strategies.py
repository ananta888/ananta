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

    def _call_llm_with_retry(
        self,
        prompt: str,
        llm_config: dict,
        *,
        temperature: float | None = None,
    ) -> str: ...


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
            catalog_template = self._catalog.resolve_template(candidate)
            if catalog_template:
                catalog_subtasks = self._catalog_subtasks_with_metadata(catalog_template)
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

    @staticmethod
    def _catalog_subtasks_with_metadata(template: dict[str, Any]) -> list[dict[str, Any]]:
        template_id = str(template.get("id") or "").strip()
        template_name = str(template.get("title") or template_id).strip() or template_id
        subtasks: list[dict[str, Any]] = []
        for item in list(template.get("subtasks") or []):
            if not isinstance(item, dict):
                continue
            annotated = dict(item)
            if template_id:
                annotated.setdefault("template_id", template_id)
            if template_name:
                annotated.setdefault("template_name", template_name)
            subtasks.append(annotated)
        return subtasks


class LLMPlanningStrategy:
    @staticmethod
    def _resolve_repair_strategies(planning_policy: dict[str, Any], *, repair_attempts: int) -> list[dict[str, Any]]:
        raw = list(planning_policy.get("repair_strategies") or [])
        resolved: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip().lower()
            if name not in {"hub_copilot", "llm_config"}:
                continue
            try:
                temp = float(item.get("temperature")) if item.get("temperature") is not None else None
            except (TypeError, ValueError):
                temp = None
            if temp is not None:
                temp = max(0.0, min(2.0, temp))
            resolved.append({"name": name, "temperature": temp})
        if resolved:
            return resolved

        # Sensible generic default: first try hub/evolver at moderate temperature,
        # then local llm with progressively lower temperature for stricter JSON.
        defaults: list[dict[str, Any]] = [{"name": "hub_copilot", "temperature": 0.35}]
        for i in range(max(1, repair_attempts)):
            defaults.append({"name": "llm_config", "temperature": max(0.0, 0.30 - (0.10 * i))})
        return defaults

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

    @staticmethod
    def _build_new_project_execution_repair_prompt(
        *,
        goal: str,
        context: str | None,
        max_subtasks: int,
        previous_output: str,
        mode_data: Optional[dict] = None,
    ) -> str:
        prompt = (
            "Der vorherige Plan fuer new_software_project enthaelt keinen klaren Execution-Pfad.\n"
            "Erzeuge jetzt einen reparierten Plan als strikt valides JSON.\n\n"
            f"ZIEL:\n{goal}\n\n"
        )
        if mode_data:
            prompt = f"{prompt}STEUERUNGSDATEN:\n{json.dumps(mode_data, indent=2)}\n\n"
        prompt = (
            f"{prompt}"
            "MUSS-KRITERIEN:\n"
            f"1. Liefere mindestens 3 und hoechstens {max_subtasks} Teilaufgaben\n"
            "2. Enthalte mindestens eine konkrete Datei-/Projektstruktur-Aufgabe (Dateien oder Ordner anlegen/aktualisieren)\n"
            "3. Enthalte mindestens eine konkrete Verifikations-Aufgabe (Test/Check/Run/Verify inkl. Ergebnisnachweis)\n"
            "4. Jede Teilaufgabe muss title, description, priority enthalten\n"
            "5. priority nur: High, Medium, Low\n"
            "6. depends_on als Liste von Schrittnummern als Strings (z.B. [\"1\"])\n"
            "7. Keine Erklaerungen, keine Markdown-Fences\n\n"
            "AUSGABEFORMAT (nur JSON-Array):\n"
            "[\n"
            '  {"title":"...","description":"...","priority":"High|Medium|Low","depends_on":[]}\n'
            "]\n\n"
            "VORHERIGER OUTPUT:\n"
            f"{(previous_output or '').strip()[:3000]}"
        )
        if context:
            prompt = f"{prompt}\n\nKONTEXT:\n{context}"
        return prompt

    @staticmethod
    def _has_new_project_execution_coverage(subtasks: list[dict[str, Any]]) -> bool:
        file_tokens = {
            "file",
            "files",
            "datei",
            "dateien",
            "readme",
            "pyproject",
            "src",
            "tests",
            "ordner",
            "directory",
            "repository",
            "projektstruktur",
            "create",
            "write",
            "anlegen",
            "erstellen",
        }
        verify_tokens = {
            "test",
            "tests",
            "pytest",
            "verify",
            "verification",
            "check",
            "smoke",
            "run",
            "ausfuehren",
            "ausführen",
        }
        has_file_task = False
        has_verify_task = False
        for item in subtasks or []:
            text = f"{item.get('title', '')} {item.get('description', '')}".lower()
            if any(token in text for token in file_tokens):
                has_file_task = True
            if any(token in text for token in verify_tokens):
                has_verify_task = True
            if has_file_task and has_verify_task:
                return True
        return has_file_task and has_verify_task

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
        scoped_cfg = getattr(planner, "_goal_effective_config", None)
        if not isinstance(scoped_cfg, dict):
            scoped_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
        planning_policy = scoped_cfg.get("planning_policy") if isinstance(scoped_cfg.get("planning_policy"), dict) else {}
        repair_attempts = max(1, min(int(planning_policy.get("unstructured_repair_attempts", 3) or 3), 6))
        repair_strategies = self._resolve_repair_strategies(planning_policy, repair_attempts=repair_attempts)
        llm_config = dict(scoped_cfg.get("llm_config") or {})
        raw_response = planner._call_llm_with_retry(prompt, llm_config)
        subtasks = parse_subtasks_from_llm_response(raw_response, default_priority=planner.default_priority)
        if not subtasks:
            for idx, strategy in enumerate(repair_strategies):
                strategy_name = str(strategy.get("name") or "").strip().lower()
                retry_temperature = strategy.get("temperature")
                use_execution_prompt = mode == "new_software_project" and idx >= max(1, repair_attempts - 1)
                if use_execution_prompt:
                    repair_prompt = self._build_new_project_execution_repair_prompt(
                        goal=goal,
                        context=resolved_context,
                        max_subtasks=planner.max_subtasks_per_goal,
                        previous_output=raw_response,
                        mode_data=mode_data,
                    )
                else:
                    repair_prompt = self._build_planning_repair_prompt(
                        goal=goal,
                        context=resolved_context,
                        max_subtasks=planner.max_subtasks_per_goal,
                        previous_output=raw_response,
                        mode=mode,
                        mode_data=mode_data,
                    )
                if strategy_name == "hub_copilot":
                    try:
                        hub_llm = get_hub_llm_service()
                        copilot_cfg = hub_llm.resolve_copilot_config()
                        if (
                            copilot_cfg.get("enabled")
                            and copilot_cfg.get("supports_planning")
                            and copilot_cfg.get("active")
                        ):
                            hub_resp = hub_llm.plan_with_copilot(
                                prompt=repair_prompt,
                                timeout=getattr(planner, "llm_timeout", None),
                                temperature=retry_temperature,
                            )
                            hub_text = str(hub_resp.get("text") or "")
                            hub_subtasks = parse_subtasks_from_llm_response(
                                hub_text,
                                default_priority=planner.default_priority,
                            )
                            if hub_subtasks:
                                raw_response = hub_text
                                subtasks = hub_subtasks
                                break
                            if hub_text.strip():
                                raw_response = hub_text
                    except Exception:
                        pass
                elif strategy_name == "llm_config":
                    repaired_response = planner._call_llm_with_retry(
                        repair_prompt,
                        llm_config,
                        temperature=retry_temperature,
                    )
                    repaired_subtasks = parse_subtasks_from_llm_response(
                        repaired_response,
                        default_priority=planner.default_priority,
                    )
                    if repaired_subtasks:
                        raw_response = repaired_response
                        subtasks = repaired_subtasks
                        break
                    if str(repaired_response or "").strip():
                        raw_response = repaired_response
        if mode == "new_software_project" and not subtasks:
            # Last LLM-only repair attempt with stricter execution-focused framing.
            repair_prompt = self._build_new_project_execution_repair_prompt(
                goal=goal,
                context=resolved_context,
                max_subtasks=planner.max_subtasks_per_goal,
                previous_output=raw_response,
                mode_data=mode_data,
            )
            repaired_response = planner._call_llm_with_retry(repair_prompt, llm_config, temperature=0.1)
            repaired_subtasks = parse_subtasks_from_llm_response(
                repaired_response,
                default_priority=planner.default_priority,
            )
            if repaired_subtasks:
                raw_response = repaired_response
                subtasks = repaired_subtasks
        if mode == "new_software_project" and subtasks and not self._has_new_project_execution_coverage(subtasks):
            repair_prompt = self._build_new_project_execution_repair_prompt(
                goal=goal,
                context=resolved_context,
                max_subtasks=planner.max_subtasks_per_goal,
                previous_output=raw_response,
                mode_data=mode_data,
            )
            repaired_response = planner._call_llm_with_retry(repair_prompt, llm_config, temperature=0.1)
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
