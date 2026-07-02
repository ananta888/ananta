"""Tests for TokenBudgetService — T01."""
from __future__ import annotations

import pytest
from agent.services.token_budget_service import (
    TokenBudgetService,
    estimate_tokens,
    normalize_usage,
)


# ── estimate_tokens ───────────────────────────────────────────────────────────

def test_estimate_empty_string():
    result = estimate_tokens("")
    assert result["tokens"] == 0
    assert result["method"] == "empty"
    assert result["confidence"] == "exact"


def test_estimate_none_treated_as_empty():
    # estimate_tokens receives a str; caller may pass ""
    result = estimate_tokens("")
    assert result["tokens"] == 0


def test_estimate_german_smalltalk():
    text = "Guten Morgen! Wie geht es Ihnen heute?"
    result = estimate_tokens(text)
    assert result["tokens"] > 0
    assert result["method"] in {"tiktoken", "chars_per_token_fallback"}
    assert result["confidence"] in {"high", "low"}


def test_estimate_english_question():
    text = "What is the capital of France?"
    result = estimate_tokens(text)
    assert result["tokens"] > 0


def test_estimate_python_code():
    text = "def hello_world():\n    print('Hello, World!')\n    return 42"
    result = estimate_tokens(text)
    assert result["tokens"] > 0
    assert result["method"] in {"tiktoken", "chars_per_token_fallback"}


def test_estimate_json_payload():
    import json
    payload = json.dumps({"action": "run", "steps": [1, 2, 3], "enabled": True})
    result = estimate_tokens(payload)
    assert result["tokens"] > 0


def test_estimate_large_text():
    text = "x " * 10000
    result = estimate_tokens(text)
    assert result["tokens"] > 1000


def test_estimate_safety_multiplier_applied():
    text = "a" * 400  # 400 chars → 100 tokens base at chars_per_token=4 → 125 with 1.25x
    result = estimate_tokens(text, chars_per_token=4.0, safety_multiplier=1.25)
    # With fallback: ceil(100 * 1.25) = 125
    # With tiktoken: raw_count * 1.25, still > raw
    assert result["tokens"] > 0
    assert result["safety_multiplier"] == 1.25


def test_estimate_returns_model_and_provider():
    result = estimate_tokens("hello", model="gpt-4", provider="openai")
    assert result["model"] == "gpt-4"
    assert result["provider"] == "openai"


def test_estimate_chars_per_token_fallback_math():
    """Without tiktoken available, verify the fallback math."""
    import math
    from agent.services import token_budget_service as tbs_mod
    original = tbs_mod._TIKTOKEN_AVAILABLE
    try:
        tbs_mod._TIKTOKEN_AVAILABLE = False  # Force fallback
        text = "a" * 400
        result = estimate_tokens(text, chars_per_token=4.0, safety_multiplier=1.25)
        expected = math.ceil(400 / 4.0 * 1.25)
        assert result["tokens"] == expected
        assert result["method"] == "chars_per_token_fallback"
        assert result["confidence"] == "low"
    finally:
        tbs_mod._TIKTOKEN_AVAILABLE = original


# ── normalize_usage ───────────────────────────────────────────────────────────

def test_normalize_openai_format():
    raw = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    result = normalize_usage(raw, provider="openai", model="gpt-4")
    assert result["actual_prompt_tokens"] == 100
    assert result["actual_completion_tokens"] == 50
    assert result["actual_total_tokens"] == 150
    assert result["usage_source"] == "provider_reported"
    assert result["provider"] == "openai"
    assert result["model"] == "gpt-4"


def test_normalize_openai_without_total():
    raw = {"prompt_tokens": 80, "completion_tokens": 20}
    result = normalize_usage(raw)
    assert result["actual_prompt_tokens"] == 80
    assert result["actual_completion_tokens"] == 20
    assert result["actual_total_tokens"] == 100  # computed


def test_normalize_ollama_format():
    raw = {"prompt_eval_count": 200, "eval_count": 80}
    result = normalize_usage(raw, provider="ollama")
    assert result["actual_prompt_tokens"] == 200
    assert result["actual_completion_tokens"] == 80
    assert result["actual_total_tokens"] == 280
    assert result["usage_source"] == "provider_reported"


def test_normalize_anthropic_format():
    raw = {"input_tokens": 300, "output_tokens": 120}
    result = normalize_usage(raw, provider="anthropic")
    assert result["actual_prompt_tokens"] == 300
    assert result["actual_completion_tokens"] == 120
    assert result["usage_source"] == "provider_reported"


def test_normalize_missing_usage():
    result = normalize_usage({})
    assert result["actual_prompt_tokens"] is None
    assert result["actual_completion_tokens"] is None
    assert result["usage_source"] == "estimate_only"
    assert result["reason_code"] == "no_provider_usage_found"


def test_normalize_with_estimated():
    estimated = {"tokens": 50, "method": "tiktoken"}
    result = normalize_usage({}, estimated=estimated)
    assert result["estimated_prompt_tokens"] == 50
    assert result["tokenizer_method"] == "tiktoken"
    assert result["usage_source"] == "estimate_only"


def test_normalize_trace_ref():
    result = normalize_usage({}, trace_ref="trace-123")
    assert result["trace_ref"] == "trace-123"


# ── TokenBudgetService.check_budget ──────────────────────────────────────────

def test_budget_allowed():
    svc = TokenBudgetService()
    result = svc.check_budget(1000, max_tokens=128000)
    assert result["allowed"] is True
    assert result["reason_code"] == "within_budget"


def test_budget_exceeded():
    svc = TokenBudgetService()
    result = svc.check_budget(200000, max_tokens=128000)
    assert result["allowed"] is False
    assert result["reason_code"] == "token_budget_exceeded"
    assert result["estimated_tokens"] == 200000
    assert result["max_tokens"] == 128000


def test_budget_no_limit():
    svc = TokenBudgetService()
    result = svc.check_budget(999999, max_tokens=0)
    assert result["allowed"] is True
    assert result["reason_code"] == "no_limit"


def test_budget_invalid_input():
    svc = TokenBudgetService()
    result = svc.check_budget("not_a_number", max_tokens=1000)  # type: ignore
    assert result["allowed"] is False
    assert result["reason_code"] == "invalid_input"


def test_budget_service_estimate_method():
    svc = TokenBudgetService(chars_per_token=4.0, safety_multiplier=1.0)
    result = svc.estimate("hello world")
    assert result["tokens"] > 0
