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
from ananta_contracts.model_recovery import (
    normalize_model_recovery_error_type,
)


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

# Retrying a request which is known to exceed the context window only repeats
# the same deterministic failure.  The Hub must instead select a configured
# context-recovery action (compaction, segmentation, approval, or stop).
NON_RETRYABLE_SAME_PROFILE_ERROR_TYPES = frozenset({
    "context_too_large",
    *TERMINAL_ERROR_TYPES,
})
MAX_PROFILE_RETRY_BUDGET = 8


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

    @classmethod
    def allows_fallback(cls, error_type: str | None) -> bool:
        """Return whether an error may advance through a model chain."""
        return cls.normalize_error_type(error_type) in FALLBACK_ERROR_TYPES

    @classmethod
    def allows_same_profile_retry(cls, error_type: str | None) -> bool:
        """Return whether repeating the unchanged model request is safe."""
        normalized = cls.normalize_error_type(error_type)
        return (
            normalized in FALLBACK_ERROR_TYPES
            and normalized not in NON_RETRYABLE_SAME_PROFILE_ERROR_TYPES
        )

    def should_retry_profile(
        self,
        *,
        error_type: str,
        profile: ModelProfile | None,
        failed_attempts: int,
    ) -> bool:
        """Return whether the current profile gets another invocation.

        ``retry_budget`` is the number of *additional* attempts after the
        initial call.  Keeping that definition on the profile makes a policy
        portable across routes while the invocation service remains a simple
        transport adapter.  A context overflow is deliberately excluded: it
        requires Hub-level recovery, never an unchanged retry.
        """
        normalized = self.normalize_error_type(error_type)
        if profile is None or not self.allows_same_profile_retry(normalized):
            return False
        retry_budget = min(
            MAX_PROFILE_RETRY_BUDGET,
            max(0, int(profile.retry_budget or 0)),
        )
        return int(failed_attempts) <= retry_budget

    @classmethod
    def candidate_allows_trigger(
        cls,
        profile: ModelProfile | None,
        error_type: str | None,
    ) -> bool:
        if profile is None:
            return True
        raw = profile.extra.get("central_fallback_triggers")
        if not isinstance(raw, (list, tuple, set)) or not raw:
            return True
        allowed = {cls.normalize_error_type(value) for value in raw}
        return cls.normalize_error_type(error_type) in allowed

    @staticmethod
    def normalize_error_type(error_type: str | None) -> str:
        return normalize_model_recovery_error_type(error_type)
