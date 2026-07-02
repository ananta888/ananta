"""Tests for T02 — ModelProfile new token-budget fields.

Verifies that:
- Legacy profiles without new fields work (safe defaults)
- New fields are correctly loaded
- Missing fields receive safe defaults
"""
from __future__ import annotations

import pytest
from agent.services.model_profile_loader import ModelProfile, ModelProfileLoader


_BASE_PROFILE = {
    "profile_id": "test-local",
    "provider_id": "ollama",
    "model": "qwen2.5:7b",
}


def _load_one(raw_profile: dict) -> ModelProfile:
    loader = ModelProfileLoader()
    result = loader.load_dict({"profiles": [raw_profile]})
    assert not result.errors, f"Unexpected errors: {result.errors}"
    assert len(result.profiles) == 1
    return result.profiles[0]


# ── Safe defaults for legacy profiles ─────────────────────────────────────────

def test_legacy_profile_loads_without_new_fields():
    profile = _load_one(_BASE_PROFILE)
    assert profile.profile_id == "test-local"
    assert profile.context_window_tokens is None
    assert profile.hard_max_output_tokens is None
    assert profile.tokenizer_strategy == "chars_per_token"
    assert profile.tokenizer_name is None
    assert profile.input_cost_per_1m_tokens is None
    assert profile.output_cost_per_1m_tokens is None


def test_legacy_profile_existing_fields_unaffected():
    profile = _load_one({**_BASE_PROFILE, "context_tokens": 8192, "max_output_tokens": 4096})
    assert profile.context_tokens == 8192
    assert profile.max_output_tokens == 4096


# ── New field loading ─────────────────────────────────────────────────────────

def test_context_window_tokens_loaded():
    profile = _load_one({**_BASE_PROFILE, "context_window_tokens": 131072})
    assert profile.context_window_tokens == 131072


def test_hard_max_output_tokens_loaded():
    profile = _load_one({**_BASE_PROFILE, "hard_max_output_tokens": 8192})
    assert profile.hard_max_output_tokens == 8192


def test_tokenizer_strategy_tiktoken_cl100k():
    profile = _load_one({**_BASE_PROFILE, "tokenizer_strategy": "tiktoken_cl100k"})
    assert profile.tokenizer_strategy == "tiktoken_cl100k"


def test_tokenizer_strategy_tiktoken_llama3():
    profile = _load_one({**_BASE_PROFILE, "tokenizer_strategy": "tiktoken_llama3"})
    assert profile.tokenizer_strategy == "tiktoken_llama3"


def test_tokenizer_name_loaded():
    profile = _load_one({**_BASE_PROFILE, "tokenizer_name": "cl100k_base"})
    assert profile.tokenizer_name == "cl100k_base"


def test_input_cost_loaded():
    profile = _load_one({**_BASE_PROFILE, "input_cost_per_1m_tokens": 0.15})
    assert profile.input_cost_per_1m_tokens == pytest.approx(0.15)


def test_output_cost_loaded():
    profile = _load_one({**_BASE_PROFILE, "output_cost_per_1m_tokens": 0.60})
    assert profile.output_cost_per_1m_tokens == pytest.approx(0.60)


def test_all_new_fields_together():
    raw = {
        **_BASE_PROFILE,
        "context_window_tokens": 32768,
        "hard_max_output_tokens": 4096,
        "tokenizer_strategy": "tiktoken_cl100k",
        "tokenizer_name": "cl100k_base",
        "input_cost_per_1m_tokens": 1.0,
        "output_cost_per_1m_tokens": 3.0,
    }
    profile = _load_one(raw)
    assert profile.context_window_tokens == 32768
    assert profile.hard_max_output_tokens == 4096
    assert profile.tokenizer_strategy == "tiktoken_cl100k"
    assert profile.tokenizer_name == "cl100k_base"
    assert profile.input_cost_per_1m_tokens == pytest.approx(1.0)
    assert profile.output_cost_per_1m_tokens == pytest.approx(3.0)


def test_null_cost_fields():
    profile = _load_one({**_BASE_PROFILE, "input_cost_per_1m_tokens": None})
    assert profile.input_cost_per_1m_tokens is None


def test_model_profile_dataclass_direct():
    """Verify ModelProfile dataclass can be constructed without new fields."""
    p = ModelProfile(profile_id="x", provider_id="ollama", model="llama3")
    assert p.context_window_tokens is None
    assert p.hard_max_output_tokens is None
    assert p.tokenizer_strategy == "chars_per_token"
    assert p.tokenizer_name is None
    assert p.input_cost_per_1m_tokens is None
    assert p.output_cost_per_1m_tokens is None
