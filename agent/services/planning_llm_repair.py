"""Parse-and-repair loop of the LLM planning strategy.

Split out of ``planning_strategies.LLMPlanningStrategy.execute`` (SRP): once
the main planning prompt has been answered, this module owns the mutable
attempt state (subtasks, raw response, parse diagnostics, repair bookkeeping)
and the ordered repair steps -- configured repair strategies, the
new-software-project rescue prompt and the execution-coverage repair.
Prompt builders and parsers are injected, so the strategy stays the
composition point and tests keep their collaborator seam.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

NEW_SOFTWARE_PROJECT_MODE = "new_software_project"


@dataclass(frozen=True)
class RepairPromptBuilders:
    """Prompt builders and heuristics used by the repair loop (bound by the strategy)."""

    planning_repair: Callable[..., str]
    new_project_execution_repair: Callable[..., str]
    new_project_truncation_repair: Callable[..., str]
    looks_truncated_response: Callable[[Any, Any], bool]
    has_new_project_execution_coverage: Callable[[list[dict[str, Any]]], bool]


@dataclass(frozen=True)
class LLMPlanningRepairRequest:
    """Immutable inputs of one planning run that repair prompts are built from."""

    planner: Any
    goal: str
    context: str | None
    mode: str
    mode_data: Optional[dict]
    llm_config: dict[str, Any]
    preferred_output_format: str
    repair_attempts: int

    @property
    def is_new_software_project(self) -> bool:
        return self.mode == NEW_SOFTWARE_PROJECT_MODE


@dataclass
class LLMPlanningAttempt:
    """Mutable outcome of the main planning call and any accepted repair."""

    raw_response: Any
    subtasks: list[dict[str, Any]]
    parse_mode: str
    parse_confidence: str
    warnings: list[Any] = field(default_factory=list)
    output_shape: str = ""
    format_error_codes: list[str] = field(default_factory=list)
    parser_trace: list[dict[str, Any]] = field(default_factory=list)
    parse_diag: dict[str, Any] | None = None
    planning_origin: str = "llm"
    repair_strategy_used: str | None = None
    repair_attempt_count: int = 0
    is_truncated_response: bool = False

    def accept_repair(self, raw_response: Any, subtasks: list[dict[str, Any]], *, strategy: str) -> None:
        self.raw_response = raw_response
        self.subtasks = subtasks
        self.planning_origin = "llm_repair"
        self.repair_strategy_used = strategy
        self.parse_mode = f"repair_{strategy}"

    def apply_diagnostics(self, diag: dict[str, Any]) -> None:
        """Overlay a repaired response's diagnostics; missing values keep the previous ones."""
        self.parse_diag = diag
        self.parse_mode = str(diag.get("parse_mode") or self.parse_mode)
        self.parse_confidence = str(diag.get("confidence") or self.parse_confidence)
        self.warnings = list(diag.get("warnings") or self.warnings)
        self.output_shape = str(diag.get("output_shape") or self.output_shape)
        self.format_error_codes = [str(x) for x in list(diag.get("format_error_codes") or self.format_error_codes)]
        self.parser_trace = [
            dict(x) for x in list(diag.get("parser_trace") or self.parser_trace) if isinstance(x, dict)
        ]


class LLMPlanningRepairRunner:
    """Parse the main planning response and run the ordered repair steps."""

    def __init__(self, *, collaborators: Any, prompts: RepairPromptBuilders) -> None:
        self._collaborators = collaborators
        self._prompts = prompts

    @property
    def _has_diagnostic_parser(self) -> bool:
        return callable(self._collaborators.parse_subtasks_with_diagnostics)

    def parse_initial(self, raw_response: Any, *, default_priority: str) -> LLMPlanningAttempt:
        if not self._has_diagnostic_parser:
            return LLMPlanningAttempt(
                raw_response=raw_response,
                subtasks=self._collaborators.parse_subtasks(raw_response, default_priority=default_priority),
                parse_mode="legacy_parser",
                parse_confidence="low",
            )
        subtasks, parse_diag = self._collaborators.parse_subtasks_with_diagnostics(
            raw_response, default_priority=default_priority
        )
        attempt = LLMPlanningAttempt(
            raw_response=raw_response,
            subtasks=subtasks,
            parse_mode=str(parse_diag.get("parse_mode") or "parse_failed"),
            parse_confidence=str(parse_diag.get("confidence") or "low"),
            warnings=list(parse_diag.get("warnings") or []),
            output_shape=str(parse_diag.get("output_shape") or ""),
            format_error_codes=[str(x) for x in list(parse_diag.get("format_error_codes") or [])],
            parser_trace=[dict(x) for x in list(parse_diag.get("parser_trace") or []) if isinstance(x, dict)],
            parse_diag=parse_diag,
        )
        if not subtasks:
            legacy_subtasks = self._collaborators.parse_subtasks(raw_response, default_priority=default_priority)
            if legacy_subtasks:
                attempt.subtasks = legacy_subtasks
                attempt.parse_mode = "legacy_parser_fallback"
                attempt.parse_confidence = "medium"
        return attempt

    def repair(
        self,
        request: LLMPlanningRepairRequest,
        attempt: LLMPlanningAttempt,
        *,
        repair_strategies: list[dict[str, Any]],
        fast_fail_empty: bool,
    ) -> None:
        attempt.is_truncated_response = self._looks_truncated(attempt)
        if not attempt.subtasks and not (fast_fail_empty and not str(attempt.raw_response or "").strip()):
            self._run_repair_strategies(request, attempt, repair_strategies)
        if request.is_new_software_project and not attempt.subtasks:
            self._rescue_new_project(request, attempt)
        if (
            request.is_new_software_project
            and attempt.subtasks
            and not attempt.is_truncated_response
            and not self._prompts.has_new_project_execution_coverage(attempt.subtasks)
        ):
            self._repair_execution_coverage(request, attempt)

    def _looks_truncated(self, attempt: LLMPlanningAttempt) -> bool:
        return self._prompts.looks_truncated_response(
            attempt.raw_response, attempt.parse_diag if self._has_diagnostic_parser else None
        )

    def _run_repair_strategies(
        self, request: LLMPlanningRepairRequest, attempt: LLMPlanningAttempt, repair_strategies: list[dict[str, Any]]
    ) -> None:
        for idx, strategy in enumerate(repair_strategies):
            attempt.repair_attempt_count += 1
            strategy_name = str(strategy.get("name") or "").strip().lower()
            retry_temperature = strategy.get("temperature")
            repair_prompt = self._strategy_repair_prompt(request, attempt, idx)
            if strategy_name == "hub_copilot":
                if self._try_hub_copilot(request, attempt, repair_prompt, retry_temperature):
                    break
            elif strategy_name == "llm_config":
                if self._try_llm_config(request, attempt, repair_prompt, retry_temperature):
                    break
            if not attempt.subtasks:
                attempt.is_truncated_response = self._looks_truncated(attempt)
                if request.is_new_software_project and attempt.is_truncated_response:
                    break

    def _strategy_repair_prompt(self, request: LLMPlanningRepairRequest, attempt: LLMPlanningAttempt, idx: int) -> str:
        if request.is_new_software_project and idx >= max(1, request.repair_attempts - 1):
            return self._execution_repair_prompt(request, attempt)
        return self._prompts.planning_repair(
            goal=request.goal,
            context=request.context,
            max_subtasks=request.planner.max_subtasks_per_goal,
            previous_output=attempt.raw_response,
            mode=request.mode,
            mode_data=request.mode_data,
            output_shape=attempt.output_shape,
            preferred_output_format=request.preferred_output_format,
        )

    def _execution_repair_prompt(self, request: LLMPlanningRepairRequest, attempt: LLMPlanningAttempt) -> str:
        return self._prompts.new_project_execution_repair(
            goal=request.goal,
            context=request.context,
            max_subtasks=request.planner.max_subtasks_per_goal,
            previous_output=attempt.raw_response,
            mode_data=request.mode_data,
            output_shape=attempt.output_shape,
            preferred_output_format=request.preferred_output_format,
        )

    def _try_hub_copilot(
        self, request: LLMPlanningRepairRequest, attempt: LLMPlanningAttempt, repair_prompt: str, temperature: Any
    ) -> bool:
        planner = request.planner
        try:
            hub_llm = self._collaborators.hub_llm_service_provider()
            copilot_cfg = hub_llm.resolve_copilot_config()
            if not (copilot_cfg.get("enabled") and copilot_cfg.get("supports_planning") and copilot_cfg.get("active")):
                return False
            hub_resp = hub_llm.plan_with_copilot(
                prompt=repair_prompt,
                timeout=getattr(planner, "llm_timeout", None),
                temperature=temperature,
            )
            hub_text = str(hub_resp.get("text") or "")
            hub_subtasks = self._collaborators.parse_subtasks(
                hub_text,
                default_priority=planner.default_priority,
            )
            if hub_subtasks:
                attempt.accept_repair(hub_text, hub_subtasks, strategy="hub_copilot")
                return True
            if hub_text.strip():
                attempt.raw_response = hub_text
        except Exception:
            pass
        return False

    def _try_llm_config(
        self, request: LLMPlanningRepairRequest, attempt: LLMPlanningAttempt, repair_prompt: str, temperature: Any
    ) -> bool:
        planner = request.planner
        repaired_response = planner._call_llm_with_retry(
            repair_prompt,
            request.llm_config,
            temperature=temperature,
        )
        repaired_subtasks = self._parse_repaired_response(attempt, repaired_response, planner.default_priority)
        if repaired_subtasks:
            attempt.accept_repair(repaired_response, repaired_subtasks, strategy="llm_config")
            return True
        if str(repaired_response or "").strip():
            attempt.raw_response = repaired_response
        return False

    def _parse_repaired_response(
        self, attempt: LLMPlanningAttempt, repaired_response: Any, default_priority: str
    ) -> list[dict[str, Any]]:
        if not self._has_diagnostic_parser:
            return self._collaborators.parse_subtasks(repaired_response, default_priority=default_priority)
        repaired_subtasks, repaired_diag = self._collaborators.parse_subtasks_with_diagnostics(
            repaired_response,
            default_priority=default_priority,
        )
        attempt.apply_diagnostics(repaired_diag)
        if not repaired_subtasks:
            repaired_legacy = self._collaborators.parse_subtasks(
                repaired_response,
                default_priority=default_priority,
            )
            if repaired_legacy:
                repaired_subtasks = repaired_legacy
                attempt.parse_mode = "legacy_parser_fallback"
                attempt.parse_confidence = "medium"
        return repaired_subtasks

    def _rescue_new_project(self, request: LLMPlanningRepairRequest, attempt: LLMPlanningAttempt) -> None:
        if attempt.is_truncated_response:
            repair_prompt = self._prompts.new_project_truncation_repair(
                goal=request.goal,
                context=request.context,
                max_subtasks=request.planner.max_subtasks_per_goal,
                previous_output=attempt.raw_response,
                mode_data=request.mode_data,
                output_shape=attempt.output_shape,
                preferred_output_format=request.preferred_output_format,
            )
        else:
            repair_prompt = self._execution_repair_prompt(request, attempt)
        temperature = 0.05 if attempt.is_truncated_response else 0.1
        self._call_and_accept_llm_repair(request, attempt, repair_prompt, temperature)

    def _repair_execution_coverage(self, request: LLMPlanningRepairRequest, attempt: LLMPlanningAttempt) -> None:
        repair_prompt = self._execution_repair_prompt(request, attempt)
        self._call_and_accept_llm_repair(request, attempt, repair_prompt, 0.1)

    def _call_and_accept_llm_repair(
        self, request: LLMPlanningRepairRequest, attempt: LLMPlanningAttempt, repair_prompt: str, temperature: float
    ) -> None:
        planner = request.planner
        repaired_response = planner._call_llm_with_retry(repair_prompt, request.llm_config, temperature=temperature)
        repaired_subtasks = self._collaborators.parse_subtasks(
            repaired_response,
            default_priority=planner.default_priority,
        )
        if repaired_subtasks:
            attempt.accept_repair(repaired_response, repaired_subtasks, strategy="llm_config")


__all__ = [
    "LLMPlanningAttempt",
    "LLMPlanningRepairRequest",
    "LLMPlanningRepairRunner",
    "RepairPromptBuilders",
]
