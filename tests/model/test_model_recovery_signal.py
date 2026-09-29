from __future__ import annotations

import json

import pytest

from agent.services.model_recovery_signal import (
    aggregate_model_recovery_signals,
    build_model_recovery_signal,
    sanitize_terminal_model_recovery_signal,
)


def test_signal_is_bounded_and_excludes_provider_messages() -> None:
    calls = [
        {
            "profile_id": f"profile-{index}",
            "provider": "ollama",
            "model": "phi4-mini",
            "success": False,
            "error_type": "schema_validation_failed",
            "error_message": "secret provider response " * 100,
        }
        for index in range(40)
    ]
    decisions = [
        {
            "reason": "same_profile_retry_allowed",
            "previous_profile_id": "phi",
            "next_profile_id": "phi",
            "trigger": "schema_validation_failed",
            "failed_attempts": index,
        }
        for index in range(40)
    ]

    signal = build_model_recovery_signal(
        terminal_reason="schema_validation_failed",
        fallback_decisions=decisions,
        llm_call_profile=calls,
    )

    assert signal["schema"] == "model_recovery_signal.v1"
    assert len(signal["llm_calls"]) == 16
    assert len(signal["fallback_decisions"]) == 16
    assert "secret provider response" not in json.dumps(signal)


def test_aggregate_preserves_strategy_failure_summary() -> None:
    first = build_model_recovery_signal(
        terminal_reason="timeout",
        llm_call_profile=[{"profile_id": "phi", "success": False, "error_type": "timeout"}],
        strategy_failures=[
            {"strategy_id": "tool_calling_llm", "terminal_reason": "timeout", "attempt_count": 1}
        ],
    )
    second = build_model_recovery_signal(
        terminal_reason="schema_validation_failed",
        llm_call_profile=[
            {"profile_id": "gemma", "success": False, "error_type": "schema_validation_failed"}
        ],
        strategy_failures=[
            {
                "strategy_id": "json_schema_llm",
                "terminal_reason": "schema_validation_failed",
                "attempt_count": 1,
            }
        ],
    )

    merged = aggregate_model_recovery_signals([first, second])

    assert merged is not None
    assert merged["terminal_reason"] == "schema_validation_failed"
    assert merged["attempted_profile_ids"] == ["phi", "gemma"]
    assert [item["strategy_id"] for item in merged["strategy_failures"]] == [
        "tool_calling_llm",
        "json_schema_llm",
    ]


@pytest.mark.parametrize(
    "signal",
    [
        None,
        {},
        {
            "schema": "model_recovery_signal.v2",
            "state": "exhausted",
            "terminal": True,
            "reason_code": "model_fallback_exhausted",
            "terminal_reason": "timeout",
        },
        {
            "schema": "model_recovery_signal.v1",
            "state": "retrying",
            "terminal": True,
            "reason_code": "model_fallback_exhausted",
            "terminal_reason": "timeout",
        },
        {
            "schema": "model_recovery_signal.v1",
            "state": "exhausted",
            "terminal": False,
            "reason_code": "model_fallback_exhausted",
            "terminal_reason": "timeout",
        },
        {
            "schema": "model_recovery_signal.v1",
            "state": "exhausted",
            "terminal": True,
            "terminal_reason": "timeout",
        },
        {
            "schema": "model_recovery_signal.v1",
            "state": "exhausted",
            "terminal": True,
            "reason_code": "worker_requests_new_tasks",
            "terminal_reason": "timeout",
        },
        {
            "schema": "model_recovery_signal.v1",
            "state": "exhausted",
            "terminal": True,
            "reason_code": "model_fallback_exhausted",
            "terminal_reason": "unknown_failure",
        },
    ],
)
def test_sanitizer_rejects_malformed_or_unverified_terminal_signals(signal) -> None:
    assert sanitize_terminal_model_recovery_signal(signal) is None


@pytest.mark.parametrize(
    "terminal_reason",
    [
        "policy_blocked",
        "http_4xx",
        "provider_egress_denied",
        "provider_token_budget_exceeded",
        "provider_cost_budget_exceeded",
        "provider_deadline_exceeded",
    ],
)
def test_sanitizer_rejects_policy_and_budget_failures_even_with_retryable_noise(
    terminal_reason: str,
) -> None:
    signal = {
        "schema": "model_recovery_signal.v1",
        "state": "exhausted",
        "terminal": True,
        "reason_code": "model_fallback_exhausted",
        "terminal_reason": terminal_reason,
        "error_types": ["timeout"],
        "llm_calls": [
            {
                "profile_id": "local-phi",
                "success": False,
                "error_type": "timeout",
                "error_message": "must not cross the boundary",
            }
        ],
    }

    assert sanitize_terminal_model_recovery_signal(signal) is None


@pytest.mark.parametrize(
    "terminal_reason",
    (
        "provider_attempt_plan_sequence_denied",
        "provider_endpoint_binding_mismatch",
        "unknown_provider_denial",
        "provider_token_budget_exceeded",
    ),
)
def test_sanitizer_rejects_unknown_terminal_denial_after_timeout(
    terminal_reason: str,
) -> None:
    signal = build_model_recovery_signal(
        terminal_reason=terminal_reason,
        llm_call_profile=[
            {
                "profile_id": "local-phi",
                "success": False,
                "error_type": "timeout",
            },
            {
                "profile_id": "local-phi",
                "success": False,
                "error_type": terminal_reason,
            },
        ],
        fallback_decisions=[
            {
                "reason": "terminal provider decision",
                "previous_profile_id": "local-phi",
                "next_profile_id": None,
                "trigger": terminal_reason,
                "terminal": True,
            }
        ],
    )

    assert sanitize_terminal_model_recovery_signal(signal) is None


def test_sanitizer_accepts_timeout_only_terminal_history() -> None:
    signal = build_model_recovery_signal(
        terminal_reason="timeout",
        llm_call_profile=[
            {
                "profile_id": "local-phi",
                "success": False,
                "error_type": "timeout",
            }
        ],
        fallback_decisions=[
            {
                "reason": "candidate_chain_exhausted",
                "previous_profile_id": "local-phi",
                "next_profile_id": None,
                "trigger": "timeout",
                "terminal": True,
            }
        ],
    )

    assert sanitize_terminal_model_recovery_signal(signal) is not None


def test_sanitizer_accepts_and_bounds_a_recoverable_terminal_signal() -> None:
    signal = {
        "schema": "model_recovery_signal.v1",
        "state": "exhausted",
        "terminal": True,
        "reason_code": "model_fallback_exhausted",
        "terminal_reason": "schema_validation_failed",
        "attempt_count": 5,
        "attempted_profile_ids": ["local-phi", "local-gemma", "local-phi"],
        "error_types": ["schema_validation_failed"],
        "llm_calls": [
            {
                "profile_id": "local-gemma",
                "success": False,
                "error_type": "schema_validation_failed",
                "error_message": "private provider response",
            }
        ],
    }

    sanitized = sanitize_terminal_model_recovery_signal(signal)

    assert sanitized is not None
    assert sanitized["attempt_count"] == 5
    assert sanitized["attempted_profile_ids"] == ["local-phi", "local-gemma"]
    assert "private provider response" not in json.dumps(sanitized)
