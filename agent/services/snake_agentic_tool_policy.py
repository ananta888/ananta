"""Hub-owned policy for bounded AI-Snake repository tool use."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.services.retrieval_profile_service import (
    DOMAIN_CODECOMPASS,
    INTENT_ARCHITECTURE,
    INTENT_ARCHITECTURE_FULL_SCAN,
    INTENT_CODE_EXPLANATION,
    _is_rag_iterative_intent,
    resolve_profile,
)


@dataclass(frozen=True, slots=True)
class SnakeAgenticToolDecision:
    enabled: bool
    trigger: str
    profile_id: str
    max_tool_calls: int | None
    max_search_calls: int | None
    final_task_kind: str


def resolve_snake_agentic_tool_decision(
    query: str,
    config: dict[str, Any],
) -> SnakeAgenticToolDecision:
    explicit = _is_rag_iterative_intent(config)
    profile = resolve_profile(query, config)
    raw_enabled = config.get("chat_use_codecompass", True)
    codecompass_enabled = raw_enabled is True or str(raw_enabled).strip().lower() in {"1", "true", "yes", "on"}
    automatic = (
        codecompass_enabled
        and profile.domain == DOMAIN_CODECOMPASS
        and profile.intent in {
            INTENT_CODE_EXPLANATION,
            INTENT_ARCHITECTURE,
            INTENT_ARCHITECTURE_FULL_SCAN,
        }
    )
    simple = automatic and not explicit and profile.intent == INTENT_CODE_EXPLANATION
    def _budget(key: str, default: int) -> int:
        try:
            return max(0, min(100, int(config.get(key, default))))
        except (TypeError, ValueError):
            return default

    return SnakeAgenticToolDecision(
        enabled=explicit or automatic,
        trigger="explicit_session_mode" if explicit else "code_question" if automatic else "none",
        profile_id=profile.profile_id,
        max_tool_calls=_budget("chat_code_question_max_tool_calls", 12) if simple else None,
        max_search_calls=_budget("chat_code_question_max_search_calls", 1) if simple else None,
        final_task_kind="classification" if simple else "repo_analysis",
    )
