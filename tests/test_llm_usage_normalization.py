from unittest.mock import patch

from flask import g

from agent.tool_guardrails import ToolGuardrailDecision



# Split from tests/test_llm_usage.py to keep source files below 1000 lines.

# LLM-003: Provider usage normalization for multiple response shapes
def _normalize(usage):
    from agent.llm_integration import _normalize_llm_usage
    return _normalize_llm_usage(usage)


def test_normalize_usage_openai_shape():
    result = _normalize({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    assert result == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def test_normalize_usage_openai_computes_total_when_missing():
    result = _normalize({"prompt_tokens": 8, "completion_tokens": 4})
    assert result == {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12}


def test_normalize_usage_anthropic_shape():
    result = _normalize({"input_tokens": 20, "output_tokens": 10})
    assert result == {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}


def test_normalize_usage_ollama_shape():
    result = _normalize({"prompt_eval_count": 50, "eval_count": 25})
    assert result == {"prompt_tokens": 50, "completion_tokens": 25, "total_tokens": 75}


def test_normalize_usage_lmstudio_openai_compat():
    # LM Studio uses OpenAI-compat shape
    result = _normalize({"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140})
    assert result == {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140}


def test_normalize_usage_keeps_explicit_total_even_if_sum_differs():
    result = _normalize({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 20})
    assert result["total_tokens"] == 20


def test_normalize_usage_partial_only_prompt():
    result = _normalize({"prompt_tokens": 7})
    assert result["prompt_tokens"] == 7
    assert result["completion_tokens"] == 0
    assert result["total_tokens"] == 7


def test_normalize_usage_empty_dict_returns_zeros():
    result = _normalize({})
    assert result == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_normalize_usage_none_returns_empty():
    result = _normalize(None)
    assert result == {}


def test_normalize_usage_non_dict_returns_empty():
    assert _normalize("garbage") == {}
    assert _normalize(42) == {}
    assert _normalize([1, 2]) == {}


def test_normalize_usage_string_numbers_coerced():
    result = _normalize({"prompt_tokens": "15", "completion_tokens": "5"})
    assert result["prompt_tokens"] == 15
    assert result["completion_tokens"] == 5
    assert result["total_tokens"] == 20


def test_normalize_usage_negative_values_clamped_to_zero():
    result = _normalize({"prompt_tokens": -5, "completion_tokens": 3})
    assert result["prompt_tokens"] == 0
    assert result["completion_tokens"] == 3


def test_normalize_usage_malformed_string_returns_empty():
    result = _normalize({"prompt_tokens": "not-a-number"})
    assert result == {}
