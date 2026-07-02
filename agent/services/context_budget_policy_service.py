"""ContextBudgetPolicyService — T03

Entscheidet welcher Kontext-Modus für einen Chat-Intent gilt und
welche Context-Quellen erlaubt/blockiert sind.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

MODES = ("safe_minimal_chat", "project_chat", "tool_enabled_chat", "deep_analysis")

# ── Sources ───────────────────────────────────────────────────────────────────

_ALL_SOURCES = [
    "user_message",
    "short_history",
    "full_history",
    "rag",
    "codecompass",
    "tool_schemas",
    "file_context",
    "compaction",
]

_MODE_CONFIGS: dict[str, dict[str, Any]] = {
    "safe_minimal_chat": {
        "allowed_context_sources": ["user_message", "short_history"],
        "blocked_context_sources": ["rag", "codecompass", "tool_schemas", "full_history", "compaction"],
        "allowed_model_tiers": ["local", "cheap_cloud"],
        "reason_codes": ["safe_minimal_mode_smalltalk"],
        "fail_closed": True,
    },
    "project_chat": {
        "allowed_context_sources": ["user_message", "short_history", "rag", "file_context"],
        "blocked_context_sources": ["tool_schemas", "full_history", "compaction", "codecompass"],
        "allowed_model_tiers": ["local", "cheap_cloud", "cloud"],
        "reason_codes": ["project_chat_mode_code_question"],
        "fail_closed": True,
    },
    "tool_enabled_chat": {
        "allowed_context_sources": ["user_message", "short_history", "rag", "tool_schemas", "file_context"],
        "blocked_context_sources": ["full_history", "compaction", "codecompass"],
        "allowed_model_tiers": ["local", "cheap_cloud", "cloud"],
        "reason_codes": ["tool_enabled_mode_tool_request"],
        "fail_closed": True,
    },
    "deep_analysis": {
        "allowed_context_sources": list(_ALL_SOURCES),
        "blocked_context_sources": [],
        "allowed_model_tiers": ["local", "cheap_cloud", "cloud", "frontier"],
        "reason_codes": ["deep_analysis_mode_explicit_trigger"],
        "fail_closed": False,
    },
}


@dataclass
class ContextBudgetDecision:
    mode: str
    max_input_tokens: int
    max_output_tokens: int
    allowed_context_sources: list[str]
    blocked_context_sources: list[str]
    allowed_model_tiers: list[str]
    reason_codes: list[str]
    fail_closed: bool
    decision_ref: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "allowed_context_sources": list(self.allowed_context_sources),
            "blocked_context_sources": list(self.blocked_context_sources),
            "allowed_model_tiers": list(self.allowed_model_tiers),
            "reason_codes": list(self.reason_codes),
            "fail_closed": self.fail_closed,
            "decision_ref": self.decision_ref,
        }


def decide_context_budget(
    *,
    intent: str = "",
    channel_type: str = "ai",
    model_profile: Any = None,
    max_input_tokens: int = 8192,
    max_output_tokens: int = 2048,
    fail_closed: bool = True,
) -> ContextBudgetDecision:
    """Decide which context budget mode applies for the given intent and profile.

    - No model_profile + fail_closed → safe_minimal_chat
    - intent="smalltalk" → safe_minimal_chat
    - intent="code_question" → project_chat
    - intent="tool_request" → tool_enabled_chat
    - intent="analysis" → deep_analysis (requires explicit trigger)
    - unknown / empty → safe_minimal_chat when fail_closed else project_chat
    """
    intent = str(intent or "").strip().lower()

    if intent == "smalltalk":
        mode = "safe_minimal_chat"
        extra_reason = []
    elif intent == "code_question":
        mode = "project_chat"
        extra_reason = []
    elif intent == "tool_request":
        mode = "tool_enabled_chat"
        extra_reason = []
    elif intent == "analysis":
        mode = "deep_analysis"
        extra_reason = []
    elif model_profile is None and fail_closed:
        # No specific intent AND no model profile → most conservative mode
        mode = "safe_minimal_chat"
        extra_reason = ["no_model_profile_fail_closed"]
    else:
        # unknown or empty intent
        mode = "safe_minimal_chat" if fail_closed else "project_chat"
        extra_reason = ["unknown_intent_defaulted"] if fail_closed else ["unknown_intent_relaxed"]

    cfg = _MODE_CONFIGS[mode]
    reason_codes = list(cfg["reason_codes"]) + extra_reason

    return ContextBudgetDecision(
        mode=mode,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        allowed_context_sources=list(cfg["allowed_context_sources"]),
        blocked_context_sources=list(cfg["blocked_context_sources"]),
        allowed_model_tiers=list(cfg["allowed_model_tiers"]),
        reason_codes=reason_codes,
        fail_closed=bool(cfg["fail_closed"]) and fail_closed,
        decision_ref=str(uuid.uuid4()),
    )
