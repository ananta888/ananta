"""ProposePolicy v1 — configurable strategy chain for propose_task_step.

Merge precedence: system_default → project → blueprint_role → task_kind_override.
Unsafe configurations are rejected unless an explicit admin override is set.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Strategy identifiers ───────────────────────────────────────────────────────

STRATEGY_DETERMINISTIC_HANDLER = "deterministic_handler"
STRATEGY_WORKER = "worker_strategy"
STRATEGY_TOOL_CALLING_LLM = "tool_calling_llm"
STRATEGY_JSON_SCHEMA_LLM = "json_schema_llm"
STRATEGY_FLEXIBLE_LLM_NORMALIZATION = "flexible_llm_normalization"
STRATEGY_ADVISORY_PROPOSAL = "advisory_proposal"
STRATEGY_HUMAN_REVIEW = "human_review"
STRATEGY_LEGACY_SGPT = "legacy_sgpt"
STRATEGY_REPAIR_PROCEDURE = "repair_procedure_runner"
STRATEGY_ARTIFACT_RECONCILIATION = "artifact_reconciliation"

# LLM mode values
LLM_MODE_DISABLED = "disabled"
LLM_MODE_FALLBACK = "fallback"
LLM_MODE_ASSISTED = "assisted"
LLM_MODE_PRIMARY_WITH_GUARDRAILS = "primary_with_guardrails"

# T003: strategies that call a real LLM provider
LLM_STRATEGY_IDS = frozenset({
    STRATEGY_TOOL_CALLING_LLM,
    STRATEGY_JSON_SCHEMA_LLM,
    STRATEGY_FLEXIBLE_LLM_NORMALIZATION,
})

_UNSAFE_STRATEGIES = {STRATEGY_LEGACY_SGPT}
_ADMIN_OVERRIDE_KEY = "allow_unsafe_strategies"

SAFE_DEFAULT_STRATEGY_ORDER = [
    STRATEGY_DETERMINISTIC_HANDLER,
    STRATEGY_WORKER,
    STRATEGY_TOOL_CALLING_LLM,
    STRATEGY_JSON_SCHEMA_LLM,
    STRATEGY_FLEXIBLE_LLM_NORMALIZATION,
    STRATEGY_ADVISORY_PROPOSAL,
    STRATEGY_HUMAN_REVIEW,
]


@dataclass
class ProposePolicy:
    """Per-task or per-project propose strategy policy."""

    strategy_order: list[str] = field(default_factory=lambda: list(SAFE_DEFAULT_STRATEGY_ORDER))
    llm_mode: str = LLM_MODE_ASSISTED
    accepted_output_formats: list[str] = field(default_factory=lambda: [
        "tool_calls", "strict_json", "fenced_json", "shell_block",
        "unified_diff", "file_blocks", "natural_language",
    ])
    allow_legacy_sgpt: bool = False
    allow_unstructured_text_as_execution: bool = False
    allow_shell_execution: bool = False  # T005: shell blocks only executable when True
    # T002: max_strategy_attempts no longer truncates the chain.
    # It documents the intended retry budget per strategy (future use). Default=1 = no retry.
    max_strategy_attempts: int = 1
    max_repair_attempts: int = 1
    requires_executable_step: bool = True
    on_parse_error: str = "next_strategy"
    on_all_strategies_declined: str = "needs_review"
    admin_overrides: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        if not self.allow_legacy_sgpt and STRATEGY_LEGACY_SGPT in self.strategy_order:
            if not self.admin_overrides.get(_ADMIN_OVERRIDE_KEY):
                raise ValueError(
                    "legacy_sgpt_in_strategy_order_but_allow_legacy_sgpt_is_false: "
                    "set allow_legacy_sgpt=True or remove legacy_sgpt from strategy_order"
                )
        if self.allow_unstructured_text_as_execution:
            if not self.admin_overrides.get(_ADMIN_OVERRIDE_KEY):
                raise ValueError(
                    "allow_unstructured_text_as_execution_requires_admin_override"
                )
        if self.llm_mode not in {
            LLM_MODE_DISABLED, LLM_MODE_FALLBACK, LLM_MODE_ASSISTED, LLM_MODE_PRIMARY_WITH_GUARDRAILS
        }:
            raise ValueError(f"invalid_llm_mode: {self.llm_mode!r}")
        if self.on_parse_error not in {"next_strategy", "needs_review", "failed"}:
            raise ValueError(f"invalid_on_parse_error: {self.on_parse_error!r}")
        if self.on_all_strategies_declined not in {"needs_review", "failed", "advisory"}:
            raise ValueError(f"invalid_on_all_strategies_declined: {self.on_all_strategies_declined!r}")

    @property
    def llm_required(self) -> bool:
        """True when LLM is mandatory: unavailable LLM must not fall back to deterministic."""
        return self.llm_mode == LLM_MODE_PRIMARY_WITH_GUARDRAILS

    def effective_strategy_order(self) -> list[str]:
        """Return strategy order with legacy_sgpt filtered unless explicitly allowed."""
        if self.allow_legacy_sgpt:
            return list(self.strategy_order)
        return [s for s in self.strategy_order if s != STRATEGY_LEGACY_SGPT]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "propose_policy.v1",
            "strategy_order": list(self.strategy_order),
            "llm_mode": self.llm_mode,
            "accepted_output_formats": list(self.accepted_output_formats),
            "allow_legacy_sgpt": self.allow_legacy_sgpt,
            "allow_unstructured_text_as_execution": self.allow_unstructured_text_as_execution,
            "allow_shell_execution": self.allow_shell_execution,
            "max_strategy_attempts": self.max_strategy_attempts,
            "max_repair_attempts": self.max_repair_attempts,
            "requires_executable_step": self.requires_executable_step,
            "on_parse_error": self.on_parse_error,
            "on_all_strategies_declined": self.on_all_strategies_declined,
        }


# ── Per-task-kind policy presets ───────────────────────────────────────────────

_TASK_KIND_PRESETS: dict[str, dict[str, Any]] = {
    "new_software_project": {
        "strategy_order": [
            STRATEGY_TOOL_CALLING_LLM,
            STRATEGY_JSON_SCHEMA_LLM,
            STRATEGY_FLEXIBLE_LLM_NORMALIZATION,
            STRATEGY_WORKER,
            STRATEGY_DETERMINISTIC_HANDLER,
            STRATEGY_ADVISORY_PROPOSAL,
            STRATEGY_HUMAN_REVIEW,
        ],
        "llm_mode": LLM_MODE_PRIMARY_WITH_GUARDRAILS,  # llm_required=True
        "allow_legacy_sgpt": False,
        "requires_executable_step": True,
    },
    "coding": {
        "strategy_order": [
            STRATEGY_DETERMINISTIC_HANDLER,
            STRATEGY_WORKER,
            STRATEGY_TOOL_CALLING_LLM,
            STRATEGY_JSON_SCHEMA_LLM,
            STRATEGY_ARTIFACT_RECONCILIATION,
            STRATEGY_HUMAN_REVIEW,
        ],
        "allow_legacy_sgpt": False,
        "requires_executable_step": True,
    },
    "research": {
        "strategy_order": [
            STRATEGY_TOOL_CALLING_LLM,
            STRATEGY_JSON_SCHEMA_LLM,
            STRATEGY_ADVISORY_PROPOSAL,
            STRATEGY_HUMAN_REVIEW,
        ],
        "requires_executable_step": False,
    },
    "repair": {
        "strategy_order": [
            STRATEGY_REPAIR_PROCEDURE,
            STRATEGY_DETERMINISTIC_HANDLER,
            STRATEGY_WORKER,
            STRATEGY_HUMAN_REVIEW,
        ],
        "requires_executable_step": True,
    },
    "documentation": {
        "strategy_order": [
            STRATEGY_TOOL_CALLING_LLM,
            STRATEGY_JSON_SCHEMA_LLM,
            STRATEGY_ADVISORY_PROPOSAL,
            STRATEGY_HUMAN_REVIEW,
        ],
        "requires_executable_step": False,
    },
}


def get_task_kind_preset(task_kind: str | None) -> dict[str, Any]:
    return dict(_TASK_KIND_PRESETS.get(str(task_kind or "").strip().lower()) or {})


def build_policy_from_dict(raw: dict[str, Any], *, admin_overrides: dict[str, Any] | None = None) -> ProposePolicy:
    """Build a ProposePolicy from a raw config dict."""
    kwargs: dict[str, Any] = {
        "admin_overrides": dict(admin_overrides or {}),
    }
    for key in (
        "strategy_order", "llm_mode", "accepted_output_formats",
        "allow_legacy_sgpt", "allow_unstructured_text_as_execution", "allow_shell_execution",
        "max_strategy_attempts", "max_repair_attempts",
        "requires_executable_step", "on_parse_error", "on_all_strategies_declined",
    ):
        if key in raw:
            kwargs[key] = raw[key]
    return ProposePolicy(**kwargs)
