"""StrategyModeService — WSM-T001/WSM-T005 named strategy mode presets."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StrategyMode:
    mode_id: str
    description: str
    propose_policy_overrides: dict[str, Any]


_MODE_PRESETS: dict[str, StrategyMode] = {
    "autopilot_no_human_review": StrategyMode(
        mode_id="autopilot_no_human_review",
        description="Autopilot mode for unattended runs without terminal human-review escalation.",
        propose_policy_overrides={
            "strategy_order": [
                "tool_calling_llm",
                "json_schema_llm",
                "flexible_llm_normalization",
                "worker_strategy",
                "advisory_proposal",
            ],
            "allow_shell_execution": False,
            "allow_json_schema_fallback": True,
            "allow_flexible_normalization": True,
            "allow_worker_fallback": True,
            "allow_deterministic_fallback": True,
            "allow_human_review": False,
            "requires_executable_step": True,
            "on_all_strategies_declined": "advisory",
        },
    ),
    "opencode_like": StrategyMode(
        mode_id="opencode_like",
        description="Interactive tool-loop oriented strategy mode.",
        propose_policy_overrides={
            "strategy_order": [
                "agent_loop_tool_calling",
                "tool_calling_llm",
                "json_schema_llm",
                "flexible_llm_normalization",
                "human_review",
            ],
            "allow_shell_execution": False,
            "allow_json_schema_fallback": True,
            "allow_flexible_normalization": True,
            "allow_worker_fallback": False,
            "allow_deterministic_fallback": False,
            "allow_human_review": True,
            "llm_mode": "primary_with_guardrails",
        },
    ),
    "codex_cli_like": StrategyMode(
        mode_id="codex_cli_like",
        description="Patch/command-oriented CLI worker mode.",
        propose_policy_overrides={
            "strategy_order": [
                "cli_agent_patch_strategy",
                "tool_calling_llm",
                "json_schema_llm",
                "flexible_llm_normalization",
                "human_review",
            ],
            "allow_shell_execution": False,
            "allow_json_schema_fallback": True,
            "allow_flexible_normalization": True,
            "allow_worker_fallback": False,
            "allow_deterministic_fallback": False,
            "allow_human_review": True,
        },
    ),
    "hermes_like": StrategyMode(
        mode_id="hermes_like",
        description="Proposal/review-first non-mutating mode.",
        propose_policy_overrides={
            "strategy_order": [
                "hermes_proposal_strategy",
                "json_schema_llm",
                "advisory_proposal",
                "human_review",
            ],
            "allow_json_schema_fallback": True,
            "allow_flexible_normalization": False,
            "allow_worker_fallback": False,
            "allow_deterministic_fallback": False,
            "allow_human_review": True,
        },
    ),
    "ananta_native": StrategyMode(
        mode_id="ananta_native",
        description="Deterministic/tool-first ananta-native mode.",
        propose_policy_overrides={
            "strategy_order": [
                "deterministic_handler",
                "tool_calling_llm",
                "json_schema_llm",
                "worker_strategy",
                "human_review",
            ],
            "allow_shell_execution": False,
            "allow_json_schema_fallback": True,
            "allow_flexible_normalization": False,
            "allow_worker_fallback": True,
            "allow_deterministic_fallback": True,
            "allow_human_review": True,
        },
    ),
    "openai_compatible_tool_calling": StrategyMode(
        mode_id="openai_compatible_tool_calling",
        description="Native tool-calling first, JSON-schema fallback second.",
        propose_policy_overrides={
            "strategy_order": [
                "tool_calling_llm",
                "json_schema_llm",
                "flexible_llm_normalization",
                "human_review",
            ],
            "allow_shell_execution": False,
            "allow_json_schema_fallback": True,
            "allow_flexible_normalization": True,
            "allow_worker_fallback": False,
            "allow_deterministic_fallback": False,
            "allow_human_review": True,
            "llm_mode": "primary_with_guardrails",
        },
    ),
}


class StrategyModeService:
    def get_mode(self, mode_id: str | None) -> StrategyMode | None:
        key = str(mode_id or "").strip().lower()
        if not key:
            return None
        return _MODE_PRESETS.get(key)

    def list_modes(self) -> list[str]:
        return sorted(_MODE_PRESETS.keys())

    def resolve_policy_overrides(self, mode_id: str | None) -> dict[str, Any]:
        mode = self.get_mode(mode_id)
        if mode is None:
            return {}
        return dict(mode.propose_policy_overrides)
