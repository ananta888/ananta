"""Tests for T06 — TokenUsageReport / normalize_usage."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent.services.token_budget_service import normalize_usage


SCHEMA_PATH = Path(__file__).parent.parent / "schemas/chat/token_usage_report.v1.json"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


# ── Basic normalization ───────────────────────────────────────────────────────

def test_openai_format_normalized():
    raw = {"prompt_tokens": 150, "completion_tokens": 75, "total_tokens": 225}
    result = normalize_usage(raw, provider="openai", model="gpt-4o")
    assert result["actual_prompt_tokens"] == 150
    assert result["actual_completion_tokens"] == 75
    assert result["actual_total_tokens"] == 225
    assert result["usage_source"] == "provider_reported"
    assert result["provider"] == "openai"
    assert result["model"] == "gpt-4o"


def test_ollama_format_normalized():
    raw = {"prompt_eval_count": 88, "eval_count": 44}
    result = normalize_usage(raw, provider="ollama")
    assert result["actual_prompt_tokens"] == 88
    assert result["actual_completion_tokens"] == 44
    assert result["actual_total_tokens"] == 132
    assert result["usage_source"] == "provider_reported"


def test_anthropic_format_normalized():
    raw = {"input_tokens": 500, "output_tokens": 250}
    result = normalize_usage(raw, provider="anthropic")
    assert result["actual_prompt_tokens"] == 500
    assert result["actual_completion_tokens"] == 250
    assert result["actual_total_tokens"] == 750
    assert result["usage_source"] == "provider_reported"


def test_missing_usage_estimate_only():
    result = normalize_usage({})
    assert result["actual_prompt_tokens"] is None
    assert result["actual_completion_tokens"] is None
    assert result["actual_total_tokens"] is None
    assert result["usage_source"] == "estimate_only"
    assert result["reason_code"] == "no_provider_usage_found"


def test_partial_usage_with_estimate():
    estimated = {"tokens": 120, "method": "chars_per_token_fallback"}
    result = normalize_usage({}, estimated=estimated)
    assert result["estimated_prompt_tokens"] == 120
    assert result["tokenizer_method"] == "chars_per_token_fallback"
    assert result["usage_source"] == "estimate_only"


def test_trace_ref_passed_through():
    result = normalize_usage({"prompt_tokens": 10, "completion_tokens": 5}, trace_ref="t-abc")
    assert result["trace_ref"] == "t-abc"
    assert result["usage_source"] == "provider_reported"


def test_schema_file_exists():
    assert SCHEMA_PATH.exists(), f"Schema file not found: {SCHEMA_PATH}"


def test_schema_has_required_fields():
    schema = _schema()
    required = set(schema.get("required", []))
    assert "estimated_prompt_tokens" in required
    assert "usage_source" in required
    assert "tokenizer_method" in required


def test_normalize_returns_all_schema_keys():
    """Result dict must contain all required schema keys."""
    schema = _schema()
    result = normalize_usage({"prompt_tokens": 1, "completion_tokens": 1})
    for key in schema.get("required", []):
        assert key in result, f"Missing required key: {key}"
