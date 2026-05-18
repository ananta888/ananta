from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from flask import current_app

from agent.services.blueprint_planning_adapter import get_blueprint_planning_adapter
from agent.services.execution_focused_planning import match_execution_focused_goal_template
from agent.services.hub_llm_service import get_hub_llm_service
from agent.services.planning_model_profile_service import get_planning_model_profile_service
from agent.services.model_response_behavior_profile_service import get_model_response_behavior_profile_service
from agent.services.planning_prompt_registry import get_planning_prompt_registry
from agent.services.planning_template_catalog import get_planning_template_catalog
from agent.services.planning_utils import (
    build_planning_prompt,
    build_planning_prompt_en,
    parse_subtasks_from_llm_response,
    try_load_repo_context,
)

try:
    from agent.services.planning_utils import parse_subtasks_with_diagnostics
except ImportError:
    parse_subtasks_with_diagnostics = None


@dataclass(frozen=True)
class PlanningStrategyResult:
    subtasks: list[dict[str, Any]]
    raw_response: str | None
    context: str | None
    template_used: bool
    planning_mode: str
    planning_origin: str = "unknown"
    repair_strategy_used: str | None = None
    repair_attempt_count: int = 0
    parse_mode: str | None = None
    parse_confidence: str | None = None
    warnings: list[str] | None = None
    output_shape: str | None = None
    format_error_codes: list[str] | None = None
    parser_trace: list[dict[str, Any]] | None = None
    prompt_version_id: str | None = None
    planning_profile: str | None = None


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
                    planning_origin="template",
                )

            blueprint_subtasks = self._blueprint_adapter.resolve_subtasks(candidate)
            if blueprint_subtasks:
                return PlanningStrategyResult(
                    subtasks=blueprint_subtasks[: planner.max_subtasks_per_goal],
                    raw_response=None,
                    context=context,
                    template_used=True,
                    planning_mode="template",
                    planning_origin="template",
                )

        execution_focused_subtasks = match_execution_focused_goal_template(goal)
        if execution_focused_subtasks:
            return PlanningStrategyResult(
                subtasks=execution_focused_subtasks[: planner.max_subtasks_per_goal],
                raw_response=None,
                context=context,
                template_used=True,
                planning_mode="template",
                planning_origin="template",
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
    def _safe_int(value: Any, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = int(default)
        if minimum is not None and parsed < minimum:
            parsed = minimum
        if maximum is not None and parsed > maximum:
            parsed = maximum
        return parsed

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

    @staticmethod
    def _split_context_into_segments(context: str | None, *, segment_chars: int, max_segments: int) -> list[str]:
        text = str(context or "").strip()
        if not text:
            return []
        segment_chars = max(600, int(segment_chars))
        max_segments = max(1, int(max_segments))
        if len(text) <= segment_chars:
            return [text]
        chunks: list[str] = []
        cursor = 0
        for _ in range(max_segments):
            if cursor >= len(text):
                break
            end = min(len(text), cursor + segment_chars)
            if end < len(text):
                nl = text.rfind("\n", cursor, end)
                if nl > cursor + 200:
                    end = nl
            chunk = text[cursor:end].strip()
            if chunk:
                chunks.append(chunk)
            cursor = end
        return chunks

    def _execute_segmented_planning(
        self,
        *,
        planner: PlannerLike,
        goal: str,
        resolved_context: str | None,
        llm_config: dict[str, Any],
        planning_policy: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str, str] | None:
        if not bool(planning_policy.get("segmented_planning_enabled", False)):
            return None
        segment_chars = self._safe_int(planning_policy.get("segment_context_chars", 2400), default=2400, minimum=600, maximum=12000)
        max_segments = self._safe_int(planning_policy.get("max_segments", 3), default=3, minimum=1, maximum=8)
        segments = self._split_context_into_segments(resolved_context, segment_chars=segment_chars, max_segments=max_segments)
        if len(segments) <= 1:
            return None

        subtasks_merged: list[dict[str, Any]] = []
        raw_parts: list[str] = []
        seen_titles: set[str] = set()
        per_segment_budget = max(2, planner.max_subtasks_per_goal // len(segments))
        for index, segment in enumerate(segments, start=1):
            prompt = build_planning_prompt(
                goal=f"{goal}\n\nSegment {index}/{len(segments)}. Focus only on this segment and avoid duplicates.",
                context=segment,
                max_subtasks=per_segment_budget,
            )
            response = planner._call_llm_with_retry(prompt, llm_config, temperature=0.1)
            raw_parts.append(str(response or ""))
            parsed = parse_subtasks_from_llm_response(response, default_priority=planner.default_priority)
            for subtask in parsed:
                key = str(subtask.get("title") or "").strip().lower()
                if key and key in seen_titles:
                    continue
                if key:
                    seen_titles.add(key)
                subtasks_merged.append(subtask)
                if len(subtasks_merged) >= planner.max_subtasks_per_goal:
                    break
            if len(subtasks_merged) >= planner.max_subtasks_per_goal:
                break
        if not subtasks_merged:
            return None
        return subtasks_merged, "\n\n--- SEGMENT BREAK ---\n\n".join(raw_parts), "segmented_context"

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
        """Strict structural coverage check for new_software_project plans.

        Only structured execution signals count:
        - execution-relevant task kinds must carry required expected_artifacts
        - at least one verification_spec must be present
        """
        has_required_verification = False
        has_required_workspace_artifact = False
        execution_task_seen = False
        execution_kinds = {"coding", "testing", "ops"}
        for item in subtasks or []:
            task_kind = str(item.get("task_kind") or "").strip().lower()
            expected = [dict(x) for x in list(item.get("expected_artifacts") or []) if isinstance(x, dict)]
            verification_spec = dict(item.get("verification_spec") or {})
            if verification_spec:
                has_required_verification = True
            if task_kind not in execution_kinds:
                continue
            execution_task_seen = True
            required_artifacts = [a for a in expected if bool(a.get("required", False))]
            if not required_artifacts:
                return False
            if any(
                str(a.get("kind") or "").strip().lower()
                in {"workspace_change", "workspace_change_set", "generated_file", "project_structure_manifest"}
                for a in required_artifacts
            ):
                has_required_workspace_artifact = True
        if not execution_task_seen:
            return False
        return has_required_workspace_artifact and has_required_verification

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

        scoped_cfg = getattr(planner, "_goal_effective_config", None)
        if not isinstance(scoped_cfg, dict):
            scoped_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
        planning_policy = scoped_cfg.get("planning_policy") if isinstance(scoped_cfg.get("planning_policy"), dict) else {}

        # Configurable context truncation — helps small models with limited context windows
        context_max_chars = planning_policy.get("context_max_chars")
        if context_max_chars and resolved_context:
            limit = self._safe_int(context_max_chars, default=400, minimum=100)
            if len(resolved_context) > limit:
                resolved_context = resolved_context[:limit]

        if mode != "generic" and mode_data:
            mode_label = f"Mode: {mode}" if planning_policy.get("prompt_language", "de") == "en" else f"Modus: {mode}"
            mode_context = (
                f"{(resolved_context or '').strip()}\n\n"
                f"{mode_label}\n"
                f"{json.dumps(mode_data, indent=2)}"
            )
            resolved_context = mode_context.strip()

        # Configurable prompt language — "en" works better for small/embedded models
        llm_cfg = dict(scoped_cfg.get("llm_config") or {})
        profile = get_planning_model_profile_service().resolve_profile(
            provider=llm_cfg.get("provider"),
            model_name=llm_cfg.get("model"),
            explicit_profile=(planning_policy.get("planning_profile") or None),
        )
        prompt_language = str(
            planning_policy.get("prompt_language")
            or profile.get("prompt_language")
            or ("en" if bool(profile.get("requires_english_prompt")) else "de")
        ).strip().lower()
        prompt_mode = str(mode or "generic").strip() or "generic"
        resolved_prompt = get_planning_prompt_registry().resolve(
            goal=goal,
            context=resolved_context,
            mode=prompt_mode,
            language=prompt_language,
            model_family=profile.get("model_family"),
            behavior_profile=get_model_response_behavior_profile_service().resolve(
                provider=llm_cfg.get("provider"),
                model_name=llm_cfg.get("model"),
            ),
        )
        prompt = str(resolved_prompt.prompt or "")
        if not prompt:
            if prompt_language == "en":
                prompt = build_planning_prompt_en(goal, resolved_context, planner.max_subtasks_per_goal)
            else:
                prompt = build_planning_prompt(goal, resolved_context, planner.max_subtasks_per_goal)
        setattr(planner, "_resolved_planning_prompt_version_id", str(resolved_prompt.prompt_version_id or ""))
        setattr(planner, "_resolved_planning_profile", str(profile.get("profile_name") or ""))
        setattr(planner, "_resolved_planning_prompt_language", prompt_language)

        repair_attempts = self._safe_int(
            planning_policy.get("unstructured_repair_attempts", 3) or 3,
            default=3,
            minimum=1,
            maximum=6,
        )
        repair_strategies = self._resolve_repair_strategies(planning_policy, repair_attempts=repair_attempts)
        llm_config = llm_cfg

        # Configurable max_output_tokens for planning — reduces empty responses from small models
        policy_max_tokens = planning_policy.get("max_output_tokens")
        if not policy_max_tokens:
            policy_max_tokens = profile.get("max_output_tokens")
        if policy_max_tokens and "max_output_tokens" not in llm_config:
            llm_config = {
                **llm_config,
                "max_output_tokens": self._safe_int(policy_max_tokens, default=1024, minimum=128, maximum=8192),
            }
        # Respect planning-policy timeout for goal-scoped runs to avoid long request-thread stalls.
        policy_timeout = planning_policy.get("timeout_seconds")
        if policy_timeout and "timeout" not in llm_config:
            llm_config = {
                **llm_config,
                "timeout": self._safe_int(policy_timeout, default=20, minimum=5, maximum=300),
            }

        segmented_result = self._execute_segmented_planning(
            planner=planner,
            goal=goal,
            resolved_context=resolved_context,
            llm_config=llm_config,
            planning_policy=planning_policy,
        )
        if segmented_result is not None:
            subtasks, raw_response, parse_mode = segmented_result
            planning_origin = "llm_segmented"
            return PlanningStrategyResult(
                subtasks=subtasks,
                raw_response=raw_response,
                context=resolved_context,
                template_used=False,
                planning_mode="llm",
                planning_origin=planning_origin,
                repair_strategy_used=None,
                repair_attempt_count=0,
                parse_mode=parse_mode,
                parse_confidence="medium",
                warnings=[],
                output_shape="segmented",
                format_error_codes=[],
                parser_trace=[],
                prompt_version_id=str(getattr(planner, "_resolved_planning_prompt_version_id", "") or ""),
                planning_profile=str(getattr(planner, "_resolved_planning_profile", "") or ""),
            )

        raw_response = planner._call_llm_with_retry(prompt, llm_config)
        planning_origin = "llm"
        repair_strategy_used: str | None = None
        repair_attempt_count = 0
        if callable(parse_subtasks_with_diagnostics):
            subtasks, parse_diag = parse_subtasks_with_diagnostics(raw_response, default_priority=planner.default_priority)
            parse_mode = str(parse_diag.get("parse_mode") or "parse_failed")
            parse_confidence = str(parse_diag.get("confidence") or "low")
            warnings = list(parse_diag.get("warnings") or [])
            output_shape = str(parse_diag.get("output_shape") or "")
            format_error_codes = [str(x) for x in list(parse_diag.get("format_error_codes") or [])]
            parser_trace = [dict(x) for x in list(parse_diag.get("parser_trace") or []) if isinstance(x, dict)]
        else:
            subtasks = parse_subtasks_from_llm_response(raw_response, default_priority=planner.default_priority)
            parse_mode = "legacy_parser"
            parse_confidence = "low"
            warnings = []
            output_shape = ""
            format_error_codes = []
            parser_trace = []
        fast_fail_empty = bool(planning_policy.get("fast_fail_on_empty_response", mode == "new_software_project"))
        if not subtasks and not (fast_fail_empty and not str(raw_response or "").strip()):
            for idx, strategy in enumerate(repair_strategies):
                repair_attempt_count += 1
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
                                planning_origin = "llm_repair"
                                repair_strategy_used = "hub_copilot"
                                parse_mode = "repair_hub_copilot"
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
                        planning_origin = "llm_repair"
                        repair_strategy_used = "llm_config"
                        parse_mode = "repair_llm_config"
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
                planning_origin = "llm_repair"
                repair_strategy_used = "llm_config"
                parse_mode = "repair_llm_config"
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
                planning_origin = "llm_repair"
                repair_strategy_used = "llm_config"
                parse_mode = "repair_llm_config"
        return PlanningStrategyResult(
            subtasks=subtasks,
            raw_response=raw_response,
            context=resolved_context,
            template_used=False,
            planning_mode="llm",
            planning_origin=planning_origin,
            repair_strategy_used=repair_strategy_used,
            repair_attempt_count=repair_attempt_count,
            parse_mode=parse_mode,
            parse_confidence=parse_confidence,
            warnings=warnings,
            output_shape=output_shape,
            format_error_codes=format_error_codes,
            parser_trace=parser_trace,
            prompt_version_id=str(getattr(planner, "_resolved_planning_prompt_version_id", "") or ""),
            planning_profile=str(getattr(planner, "_resolved_planning_profile", "") or ""),
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
            planning_origin="hub_copilot",
        )
