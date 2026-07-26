"""Shared bounded transport contract for terminal model-chain exhaustion.

The signal is an execution fact, not an orchestration command.  Workers may
emit it and the Hub may inspect it, but this module never creates tasks,
selects recovery actions, or mutates task state.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

MODEL_RECOVERY_SIGNAL_SCHEMA = "model_recovery_signal.v1"
_MAX_DECISIONS = 16
_MAX_CALLS = 16
_MAX_STRATEGY_FAILURES = 8
_MAX_TEXT = 160
RECOVERABLE_TERMINAL_REASONS = frozenset(
    {
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
        "no_attempts",
    }
)
NON_RECOVERABLE_TERMINAL_REASONS = frozenset(
    {
        "policy_blocked",
        "http_4xx",
        "client_error",
        "provider_egress_denied",
        "provider_selection_binding_mismatch",
        "provider_retry_budget_exceeded",
        "provider_combined_retry_budget_denied",
        "provider_combined_retry_budget_unavailable",
        "provider_hub_budget_unavailable",
        "provider_token_budget_exceeded",
        "provider_cost_budget_exceeded",
        "provider_deadline_exceeded",
    }
)

__all__ = [
    "MODEL_RECOVERY_SIGNAL_SCHEMA",
    "NON_RECOVERABLE_TERMINAL_REASONS",
    "RECOVERABLE_TERMINAL_REASONS",
    "aggregate_model_recovery_signals",
    "build_model_recovery_signal",
    "is_recoverable_model_error_type",
    "metadata_from_llm_error",
    "normalize_model_recovery_error_type",
    "sanitize_terminal_model_recovery_signal",
]


def normalize_model_recovery_error_type(value: Any) -> str:
    """Canonicalize provider aliases before recovery-policy evaluation."""
    normalized = str(value or "").strip().lower()[:80]
    if normalized in {"server_error", "llm_server_error"}:
        return "http_5xx"
    if normalized in {"client_error", "llm_client_error"}:
        return "http_4xx"
    if normalized in {"connection_error", "llm_connection_failed"}:
        return "provider_unavailable"
    if normalized in {"provider_timeout", "llm_timeout"}:
        return "timeout"
    return normalized or "unknown"


def is_recoverable_model_error_type(value: Any) -> bool:
    """Deny by default unless a terminal model error is allowlisted."""
    return (
        normalize_model_recovery_error_type(value)
        in RECOVERABLE_TERMINAL_REASONS
    )


def _text(value: Any, *, limit: int = _MAX_TEXT) -> str | None:
    normalized = str(value or "").strip()
    return normalized[:limit] or None


def _bounded_int(value: Any, *, minimum: int = 0, maximum: int = 1_000_000) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return max(minimum, min(int(value), maximum))
    except (TypeError, ValueError):
        return None


def _sanitize_decision(raw: Mapping[str, Any]) -> dict[str, Any]:
    decision = {
        "reason": _text(raw.get("reason")),
        "previous_profile_id": _text(raw.get("previous_profile_id")),
        "next_profile_id": _text(raw.get("next_profile_id")),
        "trigger": _text(raw.get("trigger")),
        "terminal": bool(raw.get("terminal", False)),
    }
    failed_attempts = _bounded_int(raw.get("failed_attempts"))
    if failed_attempts is not None:
        decision["failed_attempts"] = failed_attempts
    blocked = raw.get("blocked_candidates")
    if isinstance(blocked, list):
        decision["blocked_candidate_count"] = min(len(blocked), 1_000_000)
    return {key: value for key, value in decision.items() if value is not None}


def _sanitize_call(raw: Mapping[str, Any]) -> dict[str, Any]:
    call = {
        "name": _text(raw.get("name")),
        "backend": _text(raw.get("backend")),
        "profile_id": _text(raw.get("profile_id")),
        "provider": _text(raw.get("provider")),
        "model": _text(raw.get("model")),
        "success": bool(raw.get("success", False)),
        "error_type": _text(raw.get("error_type")),
        "latency_ms": _bounded_int(raw.get("latency_ms")),
    }
    return {key: value for key, value in call.items() if value is not None}


def build_model_recovery_signal(
    *,
    terminal_reason: str | None,
    fallback_decisions: Iterable[Mapping[str, Any]] | None = None,
    llm_call_profile: Iterable[Mapping[str, Any]] | None = None,
    strategy_failures: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the bounded, non-sensitive v1 exhaustion fact.

    Prompts, responses, exception messages, tool arguments, and source
    identifiers are deliberately excluded.  This keeps the signal safe to
    persist in task metadata and prevents unbounded provider output from
    crossing the worker/Hub boundary.
    """

    decisions = [
        _sanitize_decision(item)
        for item in list(fallback_decisions or [])[-_MAX_DECISIONS:]
        if isinstance(item, Mapping)
    ]
    calls = [
        _sanitize_call(item)
        for item in list(llm_call_profile or [])[-_MAX_CALLS:]
        if isinstance(item, Mapping)
    ]
    failures: list[dict[str, Any]] = []
    for raw in list(strategy_failures or [])[-_MAX_STRATEGY_FAILURES:]:
        if not isinstance(raw, Mapping):
            continue
        item = {
            "strategy_id": _text(raw.get("strategy_id")),
            "terminal_reason": _text(raw.get("terminal_reason")),
            "attempt_count": _bounded_int(raw.get("attempt_count")),
        }
        failures.append({key: value for key, value in item.items() if value is not None})

    normalized_reason = _text(terminal_reason) or (
        _text(decisions[-1].get("trigger")) if decisions else None
    ) or (
        _text(calls[-1].get("error_type")) if calls else None
    ) or "unknown"
    attempted_profile_ids = list(
        dict.fromkeys(
            str(item.get("profile_id"))
            for item in calls
            if _text(item.get("profile_id"))
        )
    )
    error_types = list(
        dict.fromkeys(
            str(value)
            for value in (
                [item.get("error_type") for item in calls]
                + [item.get("trigger") for item in decisions]
            )
            if _text(value)
        )
    )
    return {
        "schema": MODEL_RECOVERY_SIGNAL_SCHEMA,
        "state": "exhausted",
        "terminal": True,
        "reason_code": "model_fallback_exhausted",
        "terminal_reason": normalized_reason,
        "attempt_count": len(calls),
        "attempted_profile_ids": attempted_profile_ids,
        # Compatibility aliases consumed by the Hub recovery summarizer.
        "failed_profile_ids": attempted_profile_ids,
        "error_types": error_types,
        "fallback_decisions": decisions,
        "llm_calls": calls,
        "strategy_failures": failures,
    }


def sanitize_terminal_model_recovery_signal(
    value: Any,
) -> dict[str, Any] | None:
    """Validate and bound a recoverable terminal v1 signal.

    Policy, security, budget, and malformed terminal facts deliberately do not
    become recovery signals.  This boundary is shared by Worker transport and
    Hub persistence so neither side trusts an arbitrary metadata dictionary.
    """
    if not isinstance(value, Mapping):
        return None
    if str(value.get("schema") or "") != MODEL_RECOVERY_SIGNAL_SCHEMA:
        return None
    if str(value.get("state") or "") != "exhausted":
        return None
    if value.get("terminal") is not True:
        return None
    if str(value.get("reason_code") or "") != "model_fallback_exhausted":
        return None

    decisions = [
        dict(item)
        for item in list(value.get("fallback_decisions") or [])[-_MAX_DECISIONS:]
        if isinstance(item, Mapping)
    ]
    calls = [
        dict(item)
        for item in list(value.get("llm_calls") or [])[-_MAX_CALLS:]
        if isinstance(item, Mapping)
    ]
    failures = [
        dict(item)
        for item in list(value.get("strategy_failures") or [])[
            -_MAX_STRATEGY_FAILURES:
        ]
        if isinstance(item, Mapping)
    ]
    terminal_reason = normalize_model_recovery_error_type(
        _text(value.get("terminal_reason")) or "unknown"
    )
    error_types = {
        normalize_model_recovery_error_type(item)
        for item in list(value.get("error_types") or [])
        if str(item or "").strip()
    }
    error_types.update(
        normalize_model_recovery_error_type(item.get("error_type"))
        for item in calls
        if str(item.get("error_type") or "").strip()
    )
    error_types.update(
        normalize_model_recovery_error_type(item.get("trigger"))
        for item in decisions
        if str(item.get("trigger") or "").strip()
    )
    error_types.add(terminal_reason)
    if not is_recoverable_model_error_type(terminal_reason):
        return None
    if any(
        not is_recoverable_model_error_type(error_type)
        for error_type in error_types
    ):
        return None

    sanitized = build_model_recovery_signal(
        terminal_reason=terminal_reason,
        fallback_decisions=decisions,
        llm_call_profile=calls,
        strategy_failures=failures,
    )
    attempted = list(
        dict.fromkeys(
            str(item or "").strip()[:160]
            for item in (
                list(value.get("attempted_profile_ids") or [])
                + list(value.get("failed_profile_ids") or [])
                + list(sanitized.get("attempted_profile_ids") or [])
            )
            if str(item or "").strip()
        )
    )[:32]
    sanitized["attempted_profile_ids"] = attempted
    sanitized["failed_profile_ids"] = attempted
    sanitized["error_types"] = sorted(error_types)[:32]
    supplied_attempt_count = _bounded_int(value.get("attempt_count"))
    if supplied_attempt_count is not None:
        sanitized["attempt_count"] = max(
            int(sanitized.get("attempt_count") or 0),
            supplied_attempt_count,
        )
    return sanitized


def metadata_from_llm_error(exc: BaseException) -> dict[str, Any]:
    """Return additive strategy metadata without depending on exception type."""

    profile = [
        dict(item)
        for item in list(getattr(exc, "llm_call_profile", []) or [])
        if isinstance(item, Mapping)
    ]
    decisions = [
        dict(item)
        for item in list(getattr(exc, "fallback_decisions", []) or [])
        if isinstance(item, Mapping)
    ]
    signal = getattr(exc, "model_recovery_signal", None)
    if not isinstance(signal, Mapping):
        signal = build_model_recovery_signal(
            terminal_reason=getattr(exc, "terminal_reason", None),
            fallback_decisions=decisions,
            llm_call_profile=profile,
        )
    validated_signal = sanitize_terminal_model_recovery_signal(signal)
    metadata: dict[str, Any] = {}
    if validated_signal is not None:
        metadata["model_recovery_signal"] = validated_signal
    if profile:
        metadata["llm_call_profile"] = profile
    if decisions:
        metadata["fallback_decisions"] = [
            dict(item)
            for item in list(signal.get("fallback_decisions") or [])
            if isinstance(item, Mapping)
        ]
    return metadata


def aggregate_model_recovery_signals(
    signals: Iterable[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Merge strategy-local terminal facts into one bounded orchestration input."""

    valid = [
        sanitized
        for item in signals
        if (sanitized := sanitize_terminal_model_recovery_signal(item)) is not None
    ]
    if not valid:
        return None
    decisions: list[Mapping[str, Any]] = []
    calls: list[Mapping[str, Any]] = []
    failures: list[dict[str, Any]] = []
    attempted_profile_ids: list[str] = []
    error_types: set[str] = set()
    attempt_count = 0
    for signal in valid:
        decisions.extend(item for item in list(signal.get("fallback_decisions") or []) if isinstance(item, Mapping))
        calls.extend(item for item in list(signal.get("llm_calls") or []) if isinstance(item, Mapping))
        attempt_count += max(0, int(signal.get("attempt_count") or 0))
        attempted_profile_ids.extend(
            str(item or "").strip()[:160]
            for item in list(signal.get("attempted_profile_ids") or [])
            if str(item or "").strip()
        )
        error_types.update(
            str(item or "").strip().lower()[:80]
            for item in list(signal.get("error_types") or [])
            if str(item or "").strip()
        )
        for failure in list(signal.get("strategy_failures") or []):
            if isinstance(failure, Mapping):
                failures.append(dict(failure))
    terminal_reason = _text(valid[-1].get("terminal_reason"))
    aggregated = build_model_recovery_signal(
        terminal_reason=terminal_reason,
        fallback_decisions=decisions,
        llm_call_profile=calls,
        strategy_failures=failures,
    )
    attempted = list(
        dict.fromkeys(
            attempted_profile_ids
            + list(aggregated.get("attempted_profile_ids") or [])
        )
    )[:32]
    aggregated["attempted_profile_ids"] = attempted
    aggregated["failed_profile_ids"] = attempted
    aggregated["error_types"] = sorted(
        error_types.union(aggregated.get("error_types") or [])
    )[:32]
    aggregated["attempt_count"] = max(
        int(aggregated.get("attempt_count") or 0),
        attempt_count,
    )
    return aggregated
