from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

from flask import current_app, has_app_context

from agent.services.blueprint_planning_adapter import get_blueprint_planning_adapter
from agent.services.execution_focused_planning import match_execution_focused_goal_template
from agent.services.hub_llm_service import get_hub_llm_service
from agent.services.model_response_behavior_profile_service import get_model_response_behavior_profile_service
from agent.services.planning_domain_hints_service import get_planning_domain_hints_service
from agent.services.planning_llm_repair import (
    LLMPlanningRepairRequest,
    LLMPlanningRepairRunner,
    RepairPromptBuilders,
)
from agent.services.planning_model_profile_service import get_planning_model_profile_service
from agent.services.planning_prompt_registry import get_planning_prompt_registry
from agent.services.planning_strategy_repair_prompts import (
    build_new_project_execution_repair_prompt,
    build_new_project_truncation_repair_prompt,
    build_planning_repair_prompt,
    compact_mode_data_for_prompt,
    compact_repair_output,
    format_adaptive_repair_guidance,
    has_new_project_execution_coverage,
    looks_truncated_response,
)
from agent.services.planning_template_catalog import get_planning_template_catalog
from agent.services.planning_utils import (
    build_planning_prompt,
    build_planning_prompt_en,
    match_goal_template,
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


class RepoContextLoader(Protocol):
    def __call__(self, goal: str) -> str | None: ...


class SubtaskParser(Protocol):
    def __call__(self, raw: str, default_priority: str = "Medium") -> list[dict[str, Any]]: ...


class DiagnosticSubtaskParser(Protocol):
    def __call__(
        self, raw: str, default_priority: str = "Medium"
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]: ...


@dataclass(frozen=True)
class PlanningStrategyCollaborators:
    """Explicit collaborators of the LLM-backed planning strategies (DIP/ISP).

    ``default()`` binds the production implementations at construction time;
    tests and alternative planners pass their own bundle instead of patching
    module-level names. ``parse_subtasks_with_diagnostics`` may be ``None``
    when the diagnostic parser is unavailable (legacy parser only).
    """

    repo_context_loader: RepoContextLoader
    hub_llm_service_provider: Callable[[], Any]
    parse_subtasks: SubtaskParser
    parse_subtasks_with_diagnostics: DiagnosticSubtaskParser | None = None

    @classmethod
    def default(cls) -> "PlanningStrategyCollaborators":
        return cls(
            repo_context_loader=try_load_repo_context,
            hub_llm_service_provider=get_hub_llm_service,
            parse_subtasks=parse_subtasks_from_llm_response,
            parse_subtasks_with_diagnostics=parse_subtasks_with_diagnostics,
        )


PLANNING_STRATEGY_COLLABORATORS_EXTENSION = "ananta.planning_strategy_collaborators"


def install_planning_strategy_collaborators(
    app: Any,
    collaborators: PlanningStrategyCollaborators | None,
) -> None:
    """Install collaborators for one Flask application (``None`` removes them)."""

    if collaborators is None:
        app.extensions.pop(PLANNING_STRATEGY_COLLABORATORS_EXTENSION, None)
    else:
        app.extensions[PLANNING_STRATEGY_COLLABORATORS_EXTENSION] = collaborators


def current_planning_strategy_collaborators() -> PlanningStrategyCollaborators:
    """The current application's installed collaborators, else the production ones.

    Strategies constructed without an explicit bundle use this per-application
    composition seam; there is no process-global override.
    """

    if has_app_context():
        installed = current_app.extensions.get(PLANNING_STRATEGY_COLLABORATORS_EXTENSION)
        if installed is not None:
            return installed
    return PlanningStrategyCollaborators.default()


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

        fallback_template = match_goal_template(goal)
        if fallback_template:
            fallback_subtasks = self._catalog_subtasks_with_metadata(
                {
                    "id": "goal-template-fallback",
                    "title": str(goal or "").strip() or "goal-template-fallback",
                    "subtasks": list(fallback_template),
                }
            )
            return PlanningStrategyResult(
                subtasks=fallback_subtasks[: planner.max_subtasks_per_goal],
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
        return [
            {"name": "hub_copilot", "temperature": 0.15},
            {"name": "llm_config", "temperature": 0.1},
        ][: max(1, min(repair_attempts, 6))]

    _compact_mode_data_for_prompt = staticmethod(compact_mode_data_for_prompt)
    _compact_repair_output = staticmethod(compact_repair_output)
    _looks_truncated_response = staticmethod(looks_truncated_response)
    _format_adaptive_repair_guidance = staticmethod(format_adaptive_repair_guidance)

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
        prompt_mode: str,
        prompt_language: str,
        model_family: str | None,
        preferred_prompt_version_id: str | None,
        preferred_output_format: str,
        domain_hints: list[str],
        behavior_profile: dict[str, Any] | None,
    ) -> tuple[list[dict[str, Any]], str, str] | None:
        if not bool(planning_policy.get("segmented_planning_enabled", False)):
            return None
        segment_chars, max_segments = self.segmentation(planning_policy, len(resolved_context or ""))
        segments = self._split_context_into_segments(resolved_context, segment_chars=segment_chars, max_segments=max_segments)
        if len(segments) <= 1:
            return None

        subtasks_merged: list[dict[str, Any]] = []
        raw_parts: list[str] = []
        seen_titles: set[str] = set()
        per_segment_budget = max(2, planner.max_subtasks_per_goal // len(segments))
        for index, segment in enumerate(segments, start=1):
            resolved_prompt = get_planning_prompt_registry().resolve(
                goal=f"{goal}\n\nSegment {index}/{len(segments)}. Focus only on this segment and avoid duplicates.",
                context=segment,
                mode=prompt_mode,
                language=prompt_language,
                model_family=model_family,
                preferred_prompt_version_id=preferred_prompt_version_id,
                preferred_output_format=preferred_output_format,
                domain_hints=domain_hints,
                behavior_profile=behavior_profile,
            )
            prompt = str(resolved_prompt.prompt or "")
            if not prompt:
                prompt = build_planning_prompt_en(
                    goal=f"{goal}\n\nSegment {index}/{len(segments)}. Focus only on this segment and avoid duplicates.",
                    context=segment,
                    max_tasks=per_segment_budget,
                )
            response = planner._call_llm_with_retry(prompt, llm_config, temperature=0.1)
            raw_parts.append(str(response or ""))
            parsed = self._collaborators.parse_subtasks(response, default_priority=planner.default_priority)
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

    def __init__(
        self,
        use_repo_context: bool,
        *,
        collaborators: PlanningStrategyCollaborators | None = None,
    ) -> None:
        self._use_repo_context = bool(use_repo_context)
        self._collaborators = collaborators or current_planning_strategy_collaborators()

    _build_planning_repair_prompt = staticmethod(build_planning_repair_prompt)
    _build_new_project_execution_repair_prompt = staticmethod(build_new_project_execution_repair_prompt)
    _build_new_project_truncation_repair_prompt = staticmethod(build_new_project_truncation_repair_prompt)
    _has_new_project_execution_coverage = staticmethod(has_new_project_execution_coverage)

    MAX_SEGMENTS = 8

    @classmethod
    def segment_chars(cls, planning_policy: dict[str, Any]) -> int:
        """One planning segment: the "planning_segment" share of the effective window (8000 chars at 32k).
        A configured ``segment_context_chars`` (hardware profiles, e.g. 1400 for a laptop) still applies --
        the persisted historical 8000 counts as unset -- never beyond the planning share of the window."""
        from agent.context_profile import budget_override, context_budgets

        budgets = context_budgets()
        explicit = budget_override(planning_policy.get("segment_context_chars"), legacy="planning_segment_chars")
        room = budgets.chars("planning_context")
        return max(600, min(explicit or budgets.chars("planning_segment"), room))

    @classmethod
    def segmentation(cls, planning_policy: dict[str, Any], context_chars: int) -> tuple[int, int]:
        """``(segment_chars, segments)``: with segmented planning the segments grow with the context up to 8,
        so context is planned over rather than cut (LCTX-009); without it, the configured product is the limit."""
        segment_chars = cls.segment_chars(planning_policy)
        segments = cls._safe_int(planning_policy.get("max_segments", 3), default=3, minimum=1, maximum=cls.MAX_SEGMENTS)
        if bool(planning_policy.get("segmented_planning_enabled", False)) and context_chars > segment_chars * segments:
            segments = min(cls.MAX_SEGMENTS, -(-int(context_chars) // segment_chars))
        return segment_chars, segments

    @staticmethod
    def effective_planning_policy(scoped_cfg: dict[str, Any], mode_data: Optional[dict]) -> dict[str, Any]:
        """The scoped planning policy; a context-recovery "segment_planning" request forces segmentation."""
        policy = scoped_cfg.get("planning_policy") if isinstance(scoped_cfg.get("planning_policy"), dict) else {}
        if isinstance(mode_data, dict) and mode_data.get("segment_planning"):
            policy = {**policy, "segmented_planning_enabled": True}
        return policy

    def execute(
        self,
        planner: PlannerLike,
        goal: str,
        context: str | None,
        mode: str = "generic",
        mode_data: Optional[dict] = None,
    ) -> PlanningStrategyResult | None:
        resolved_context = self._resolve_repo_context(goal, context)

        scoped_cfg = getattr(planner, "_goal_effective_config", None)
        if not isinstance(scoped_cfg, dict):
            scoped_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
        planning_policy = self.effective_planning_policy(scoped_cfg, mode_data)
        team_id = str((scoped_cfg.get("routing") or {}).get("team_id") or "").strip() or None
        runtime_profile = _default_runtime_profile(planning_policy)

        resolved_context = self._truncate_context(planning_policy, resolved_context)
        resolved_context = self._with_mode_context(
            resolved_context, mode=mode, mode_data=mode_data, planning_policy=planning_policy
        )

        llm_cfg = dict(scoped_cfg.get("llm_config") or {})
        setup = self._resolve_prompt_setup(
            planner,
            goal=goal,
            resolved_context=resolved_context,
            mode=mode,
            planning_policy=planning_policy,
            runtime_profile=runtime_profile,
            llm_cfg=llm_cfg,
            team_id=team_id,
        )

        repair_attempts = self._safe_int(
            planning_policy.get("unstructured_repair_attempts", 3) or 3,
            default=3,
            minimum=1,
            maximum=6,
        )
        repair_strategies = self._resolve_repair_strategies(planning_policy, repair_attempts=repair_attempts)
        llm_config = self._planning_llm_config(llm_cfg, planning_policy, setup.profile)

        segmented_result = self._execute_segmented_planning(
            planner=planner,
            goal=goal,
            resolved_context=resolved_context,
            llm_config=llm_config,
            planning_policy=planning_policy,
            prompt_mode=setup.prompt_mode,
            prompt_language=setup.prompt_language,
            model_family=setup.profile.get("model_family"),
            preferred_prompt_version_id=setup.profile.get("preferred_prompt_version_id"),
            preferred_output_format=setup.preferred_output_format,
            domain_hints=setup.domain_hints,
            behavior_profile=setup.behavior_profile,
        )
        if segmented_result is not None:
            subtasks, raw_response, parse_mode = segmented_result
            return PlanningStrategyResult(
                subtasks=subtasks,
                raw_response=raw_response,
                context=resolved_context,
                template_used=False,
                planning_mode="llm",
                planning_origin="llm_segmented",
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

        raw_response = planner._call_llm_with_retry(setup.prompt, llm_config)
        import logging
        logging.getLogger(__name__).debug(f"LLMPlanningStrategy: main LLM response: {raw_response}")
        runner = LLMPlanningRepairRunner(collaborators=self._collaborators, prompts=self._repair_prompt_builders())
        attempt = runner.parse_initial(raw_response, default_priority=planner.default_priority)
        runner.repair(
            LLMPlanningRepairRequest(
                planner=planner,
                goal=goal,
                context=resolved_context,
                mode=mode,
                mode_data=mode_data,
                llm_config=llm_config,
                preferred_output_format=setup.preferred_output_format,
                repair_attempts=repair_attempts,
            ),
            attempt,
            repair_strategies=repair_strategies,
            fast_fail_empty=bool(planning_policy.get("fast_fail_on_empty_response", mode == "new_software_project")),
        )
        return PlanningStrategyResult(
            subtasks=attempt.subtasks,
            raw_response=attempt.raw_response,
            context=resolved_context,
            template_used=False,
            planning_mode="llm",
            planning_origin=attempt.planning_origin,
            repair_strategy_used=attempt.repair_strategy_used,
            repair_attempt_count=attempt.repair_attempt_count,
            parse_mode=attempt.parse_mode,
            parse_confidence=attempt.parse_confidence,
            warnings=attempt.warnings,
            output_shape=attempt.output_shape,
            format_error_codes=attempt.format_error_codes,
            parser_trace=attempt.parser_trace,
            prompt_version_id=str(getattr(planner, "_resolved_planning_prompt_version_id", "") or ""),
            planning_profile=str(getattr(planner, "_resolved_planning_profile", "") or ""),
        )

    def _repair_prompt_builders(self) -> RepairPromptBuilders:
        return RepairPromptBuilders(
            planning_repair=self._build_planning_repair_prompt,
            new_project_execution_repair=self._build_new_project_execution_repair_prompt,
            new_project_truncation_repair=self._build_new_project_truncation_repair_prompt,
            looks_truncated_response=self._looks_truncated_response,
            has_new_project_execution_coverage=self._has_new_project_execution_coverage,
        )

    def _resolve_repo_context(self, goal: str, context: str | None) -> str | None:
        if self._use_repo_context and not context:
            repo_context = self._collaborators.repo_context_loader(goal)
            if repo_context:
                return repo_context
        return context

    def _truncate_context(self, planning_policy: dict[str, Any], resolved_context: str | None) -> str | None:
        """Configurable context truncation -- helps small models with limited context windows."""
        context_max_chars = planning_policy.get("context_max_chars")
        if not context_max_chars:
            segment_chars, max_segments = self.segmentation(planning_policy, len(resolved_context or ""))
            context_max_chars = segment_chars * max_segments
        if not (context_max_chars and resolved_context):
            return resolved_context
        limit = self._safe_int(context_max_chars, default=400, minimum=100)
        if len(resolved_context) <= limit:
            return resolved_context
        from agent.context_window import estimate_tokens, record_truncation

        record_truncation("planning.context", "char_cut", before_tokens=estimate_tokens(resolved_context),
                          after_tokens=estimate_tokens(resolved_context[:limit]), limit_chars=limit)
        return resolved_context[:limit]

    def _with_mode_context(
        self, resolved_context: str | None, *, mode: str, mode_data: Optional[dict], planning_policy: dict[str, Any]
    ) -> str | None:
        if mode == "generic" or not mode_data:
            return resolved_context
        compact_mode_data = self._compact_mode_data_for_prompt(
            mode_data,
            max_chars=1600 if mode == "new_software_project" else 4000,
        )
        mode_label = f"Mode: {mode}" if planning_policy.get("prompt_language", "de") == "en" else f"Modus: {mode}"
        mode_context = (
            f"{(resolved_context or '').strip()}\n\n"
            f"{mode_label}\n"
            f"{json.dumps(compact_mode_data, indent=2)}"
        )
        return mode_context.strip()

    def _resolve_prompt_setup(
        self,
        planner: PlannerLike,
        *,
        goal: str,
        resolved_context: str | None,
        mode: str,
        planning_policy: dict[str, Any],
        runtime_profile: dict[str, Any],
        llm_cfg: dict[str, Any],
        team_id: str | None,
    ) -> _PlanningPromptSetup:
        # Configurable prompt language — "en" works better for small/embedded models
        profile = get_planning_model_profile_service().resolve_profile(
            provider=llm_cfg.get("provider"),
            model_name=llm_cfg.get("model"),
            explicit_profile=(planning_policy.get("planning_profile") or None),
        )
        preferred_output_format = str(
            planning_policy.get("preferred_output_format")
            or runtime_profile.get("preferred_output_format")
            or llm_cfg.get("planner_output_format")
            or profile.get("preferred_output_format")
            or "json"
        ).strip().lower()
        prompt_language = str(
            planning_policy.get("prompt_language")
            or profile.get("prompt_language")
            or ("en" if bool(profile.get("requires_english_prompt")) else "de")
        ).strip().lower()
        prompt_mode = str(mode or "generic").strip() or "generic"
        behavior_profile = get_model_response_behavior_profile_service().resolve(
            provider=llm_cfg.get("provider"),
            model_name=llm_cfg.get("model"),
        )
        domain_hints = get_planning_domain_hints_service().derive_hints(
            goal=goal,
            mode=prompt_mode,
            team_id=team_id,
            planning_policy=planning_policy,
        )
        resolved_prompt = get_planning_prompt_registry().resolve(
            goal=goal,
            context=resolved_context,
            mode=prompt_mode,
            language=prompt_language,
            model_family=profile.get("model_family"),
            preferred_prompt_version_id=profile.get("preferred_prompt_version_id"),
            preferred_output_format=preferred_output_format,
            domain_hints=domain_hints,
            behavior_profile=behavior_profile,
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
        return _PlanningPromptSetup(
            prompt=prompt,
            profile=profile,
            preferred_output_format=preferred_output_format,
            prompt_language=prompt_language,
            prompt_mode=prompt_mode,
            behavior_profile=behavior_profile,
            domain_hints=domain_hints,
        )

    def _planning_llm_config(
        self, llm_cfg: dict[str, Any], planning_policy: dict[str, Any], profile: dict[str, Any]
    ) -> dict[str, Any]:
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
        return llm_config


@dataclass(frozen=True)
class _PlanningPromptSetup:
    """Resolved prompt and model/prompt profile of one LLM planning run."""

    prompt: str
    profile: dict[str, Any]
    preferred_output_format: str
    prompt_language: str
    prompt_mode: str
    behavior_profile: Any
    domain_hints: Any


def _default_runtime_profile(planning_policy: dict[str, Any]) -> dict[str, Any]:
    runtime_profiles = (
        planning_policy.get("runtime_profiles") if isinstance(planning_policy.get("runtime_profiles"), dict) else {}
    )
    runtime_profile_id = str(planning_policy.get("default_runtime_profile") or "").strip()
    runtime_profile = (
        runtime_profiles.get(runtime_profile_id) if runtime_profile_id and isinstance(runtime_profiles, dict) else {}
    )
    return runtime_profile if isinstance(runtime_profile, dict) else {}


class HubCopilotPlanningStrategy:
    def __init__(
        self,
        use_repo_context: bool,
        *,
        collaborators: PlanningStrategyCollaborators | None = None,
    ) -> None:
        self._use_repo_context = bool(use_repo_context)
        self._collaborators = collaborators or current_planning_strategy_collaborators()

    def execute(
        self,
        planner: PlannerLike,
        goal: str,
        context: str | None,
        mode: str = "generic",
        mode_data: Optional[dict] = None,
    ) -> PlanningStrategyResult | None:
        hub_llm = self._collaborators.hub_llm_service_provider()
        copilot_config = hub_llm.resolve_copilot_config()
        if (
            not copilot_config.get("enabled")
            or not copilot_config.get("supports_planning")
            or not copilot_config.get("active")
        ):
            return None

        resolved_context = context
        if self._use_repo_context and not resolved_context:
            repo_context = self._collaborators.repo_context_loader(goal)
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
        subtasks = self._collaborators.parse_subtasks(raw_response, default_priority=planner.default_priority)
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
