from __future__ import annotations

from agent.services.llm_interceptor.model_profiles import DEFAULT_MODEL_PROFILES, load_model_profiles


def test_load_model_profiles_merges_defaults_and_overrides():
    out = load_model_profiles({"profiles": {"intercepted-coder": {"repair_eligible": False}}})
    assert "intercepted-coder" in out
    assert out["intercepted-coder"]["repair_eligible"] is False
    assert "openai/gpt-4.1-mini" in out


def test_default_profiles_present():
    assert "intercepted-coder" in DEFAULT_MODEL_PROFILES

