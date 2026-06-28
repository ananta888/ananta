"""Generic model fallback policy for invocation-level retries.

The resolver owns candidate selection. This service owns retry semantics:
which error classes may advance to the next candidate and how the decision is
recorded. Security decisions remain resolver-owned and are never overridden.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.services.model_profile_loader import ModelProfile
from agent.services.model_profile_resolver import ProviderHealthCache


FALLBACK_ERROR_TYPES = frozenset({
    "provider_unavailable",
    "connection_error",
    "timeout",
    "http_5xx",
    "server_error",
    "invalid_json_response",
    "empty_content",
    "schema_validation_failed",
    "tool_not_allowed",
    "tool_args_invalid",
    "repeated_tool_failure",
    "context_too_large",
})

TERMINAL_ERROR_TYPES = frozenset({
    "policy_blocked",
    "http_4xx",
    "client_error",
})


@dataclass
class FallbackDecision:
    reason: str
    previous_profile_id: str | None
    next_profile_id: str | None
    trigger: str
    blocked_candidates: list[dict[str, Any]] = field(default_factory=list)
    terminal: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "previous_profile_id": self.previous_profile_id,
            "next_profile_id": self.next_profile_id,
            "trigger": self.trigger,
            "blocked_candidates": list(self.blocked_candidates),
            "terminal": self.terminal,
        }


class ModelFallbackPolicyService:
    """Determines whether an invocation error should try the next profile."""

    def __init__(self, health_cache: ProviderHealthCache | None = None) -> None:
        self._health_cache = health_cache

    def should_fallback(
        self,
        *,
        error_type: str,
        previous_profile: ModelProfile | None,
        next_profile: ModelProfile | None,
        blocked_candidates: list[dict[str, Any]] | None = None,
    ) -> FallbackDecision:
        normalized = self.normalize_error_type(error_type)
        if previous_profile and normalized in {"provider_unavailable", "timeout", "http_5xx", "server_error", "connection_error"}:
            if self._health_cache is not None:
                self._health_cache.mark_unavailable(previous_profile.provider_id)

        if normalized in TERMINAL_ERROR_TYPES:
            return FallbackDecision(
                reason=f"terminal_error:{normalized}",
                previous_profile_id=previous_profile.profile_id if previous_profile else None,
                next_profile_id=None,
                trigger=normalized,
                blocked_candidates=list(blocked_candidates or []),
                terminal=True,
            )
        if normalized not in FALLBACK_ERROR_TYPES:
            return FallbackDecision(
                reason=f"unsupported_fallback_trigger:{normalized}",
                previous_profile_id=previous_profile.profile_id if previous_profile else None,
                next_profile_id=None,
                trigger=normalized,
                blocked_candidates=list(blocked_candidates or []),
                terminal=True,
            )
        if next_profile is None:
            return FallbackDecision(
                reason="candidate_chain_exhausted",
                previous_profile_id=previous_profile.profile_id if previous_profile else None,
                next_profile_id=None,
                trigger=normalized,
                blocked_candidates=list(blocked_candidates or []),
                terminal=True,
            )
        return FallbackDecision(
            reason="fallback_allowed",
            previous_profile_id=previous_profile.profile_id if previous_profile else None,
            next_profile_id=next_profile.profile_id,
            trigger=normalized,
            blocked_candidates=list(blocked_candidates or []),
            terminal=False,
        )

    @staticmethod
    def normalize_error_type(error_type: str | None) -> str:
        value = str(error_type or "").strip().lower()
        if value in {"server_error", "llm_server_error"}:
            return "http_5xx"
        if value in {"client_error", "llm_client_error"}:
            return "http_4xx"
        if value in {"connection_error", "llm_connection_failed"}:
            return "provider_unavailable"
        return value or "unknown"
