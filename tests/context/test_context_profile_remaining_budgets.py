"""The remaining window-dependent budgets follow the central context profile (32k / 64k / 128k)."""

from __future__ import annotations

import pytest

from agent.context_profile import ContextBudgets

PROFILE_OF = {32768: "standard_32k", 65536: "full_64k", 131072: "extended_128k"}
WINDOWS = list(PROFILE_OF)


@pytest.fixture
def window(monkeypatch):
    """Set the configured window through the profile (never default_context_tokens: that marks it explicit)."""
    from agent.config import settings

    def apply(tokens):
        monkeypatch.setattr(settings, "context_profile", PROFILE_OF[tokens])
        monkeypatch.setattr(settings, "default_provider", "llamacpp")
        return ContextBudgets(tokens)

    return apply


@pytest.mark.parametrize("tokens", WINDOWS)
def test_chat_context_follows_the_window_for_local_backends_only(window, tokens):
    from agent.services.chat_setting_catalog import effective_chat_context_chars

    budgets = window(tokens)
    scale = tokens // 32768
    assert effective_chat_context_chars({"chat_context_chars": 12000}) == 12000 * scale  # stored default: derived
    assert effective_chat_context_chars({}) == 12000 * scale
    assert effective_chat_context_chars({"chat_context_chars": 5000}) == 5000  # explicit
    assert effective_chat_context_chars({"chat_context_chars": 10_000_000}) == budgets.available * 4
    # a subscription/cloud chat backend is never limited by the Ananta profile
    assert effective_chat_context_chars({"chat_backend": "claude", "chat_context_chars": 500_000}) == 500_000
    assert effective_chat_context_chars({"chat_backend": "openai"}) == 12000


@pytest.mark.parametrize("tokens", WINDOWS)
def test_pre_model_context_and_curation_budgets(window, tokens):
    from agent.services.context_curation_pipeline import CurationPolicy
    from agent.services.pre_model_context_config import PreModelContextConfig

    budgets = window(tokens)
    scale = tokens // 32768
    assert PreModelContextConfig.from_raw({}).context_budget_chars == 12000 * scale
    assert PreModelContextConfig.from_raw({"pre_model_context": {"context_budget_chars": 12000}}) \
        .context_budget_chars == 12000 * scale
    assert PreModelContextConfig.from_raw({"pre_model_context": {"context_budget_chars": 5000}}) \
        .context_budget_chars == 5000
    assert CurationPolicy(allowed_paths=[], denied_paths=[]).budget_chars == min(40000 * scale, budgets.available * 4)
    assert CurationPolicy(allowed_paths=[], denied_paths=[], budget_chars=9000).budget_chars == 9000


@pytest.mark.parametrize("tokens", WINDOWS)
def test_visual_process_prompt_budgets(window, tokens):
    from agent.services.visual_process_context_service import (
        CONVERSATION_CONTEXT_BUDGET,
        SELECTED_CONTEXT_BUDGET,
        VisualProcessContextService,
    )

    window(tokens)
    scale = tokens // 32768
    assert VisualProcessContextService.context_budget("selected").max_prompt_tokens == 4096 * scale
    conversation = VisualProcessContextService.context_budget("conversation")
    assert conversation.max_prompt_tokens == 12000 * scale
    assert conversation.max_ranges == CONVERSATION_CONTEXT_BUDGET.max_ranges  # counts stay fixed
    assert SELECTED_CONTEXT_BUDGET.max_prompt_tokens == 4096  # the 32k constants are unchanged


@pytest.mark.parametrize("tokens", WINDOWS)
def test_worker_batch_sizes(window, tokens):
    from agent.cli_backends.architecture_scan import _window_worker_chars

    window(tokens)
    scale = tokens // 32768
    per_file = _window_worker_chars("ananta_worker_context_per_file_chars", share="worker_file",
                                    legacy="worker_file_chars", lo=500, hi=40_000)
    snippet = _window_worker_chars("ananta_worker_context_max_snippet_chars", share="worker_snippet",
                                   legacy="worker_snippet_chars", lo=200, hi=40_000)
    assert (per_file, snippet) == (4000 * scale, 8000 * scale)


@pytest.mark.parametrize("tokens", WINDOWS)
def test_snake_rag_budgets(tokens):
    from agent.routes.snakes_rag_iterative import _window_chars

    scale = tokens // 32768
    budgets = ContextBudgets(tokens)
    assert _window_chars(20000, window_tokens=tokens, share="snake_catalog", legacy="snake_catalog_chars",
                         minimum=5000) == 20000 * scale
    assert _window_chars(9000, window_tokens=tokens, share="snake_catalog", legacy="snake_catalog_chars",
                         minimum=5000) == 9000
    assert _window_chars(10_000_000, window_tokens=tokens, share="snake_tool_file",
                         legacy="snake_tool_file_chars", minimum=4000) == budgets.available * 4


@pytest.mark.parametrize("tokens", WINDOWS)
def test_pi_context_window_is_local_only(window, tokens):
    from agent.cli_backends.pi_configuration import PI_REMOTE_CONTEXT_WINDOW, pi_context_window
    from tests.pi.test_pi_coding_agent_provider import target

    window(tokens)
    local = target(provider_id="ollama", base_url="http://ollama:11434/v1", model="m", cli_model="m")
    remote = target(provider_id="openrouter", base_url="https://openrouter.ai/api/v1", model="p/m", cli_model="p/m")
    assert pi_context_window(local) == tokens
    assert pi_context_window(remote) == PI_REMOTE_CONTEXT_WINDOW


def test_recommendations_and_wizard_use_the_profiles():
    from agent.context_profile import nearest_profile
    from agent.services.runtime_profile_recommender import RuntimeRecommendationRequest, recommend_runtime_profile

    cpu = recommend_runtime_profile(RuntimeRecommendationRequest(environment="cpu-only"))
    gpu = recommend_runtime_profile(RuntimeRecommendationRequest(environment="nvidia-gpu"))
    assert (cpu.context_window_tokens, cpu.rag_budget_tokens) == (32768, 12288)
    assert (gpu.context_window_tokens, gpu.rag_budget_tokens) == (65536, 32768)
    assert [nearest_profile(value) for value in (8000, 32768, 65536, 200_000)] == \
        ["compact_12k", "standard_32k", "full_64k", "extended_128k"]
