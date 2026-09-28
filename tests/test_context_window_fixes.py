"""32k context: generate_text honours the window, CLI prompt gates, the tool loop's total budget and
context-recovery segmentation reach the code that acts on them."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.timeout(30)


def test_generate_text_gets_the_configured_or_default_context_window(app, monkeypatch):
    from agent import llm_integration

    seen = []
    monkeypatch.setattr(llm_integration, "_call_llm", lambda *a, **k: seen.append(k["max_context_tokens"]) or "ok")
    monkeypatch.setattr(llm_integration, "resolve_preferred_local_runtime", lambda p, urls, timeout: {"provider": p})
    monkeypatch.setattr(llm_integration, "resolve_ollama_model", lambda m, url, t: m)
    with app.app_context():
        app.config["AGENT_CONFIG"] = {**(app.config.get("AGENT_CONFIG") or {}),
                                      "llm_config": {"provider": "llamacpp", "context_limit": 16384}}
        llm_integration.generate_text("hi", provider="llamacpp", model="m")
        llm_integration.generate_text("hi", provider="ollama", model="m")
        llm_integration.generate_text("hi", provider="openai", model="m")
        llm_integration.generate_text("hi", provider="llamacpp", model="m", max_context_tokens=4096)
    # declared model limit, the local default window, cloud keeps its own window, an explicit narrower limit
    assert seen == [16384, 32768, None, 4096]


def test_cli_prompt_gates_follow_the_context_window(monkeypatch):
    from agent.cli_backends.budget import prompt_token_limit
    from agent.config import settings

    monkeypatch.setattr(settings, "max_prompt_tokens", None)
    monkeypatch.setattr(settings, "opencode_default_model", "ollama/qwen2.5-coder")
    # local runtimes: the effective Ananta window
    assert prompt_token_limit("sgpt") == prompt_token_limit("opencode") == 32768
    assert prompt_token_limit("opencode", model="lmstudio/qwen") == 32768
    # subscription / cloud models: their own limit, never the Ananta profile
    assert prompt_token_limit("claude") == 200_000 and prompt_token_limit("codex") == 272_000
    assert prompt_token_limit("opencode", model="anthropic/claude-sonnet-4") == 200_000
    assert prompt_token_limit("opencode", model="openai/gpt-4.1") == 128_000
    monkeypatch.setattr(settings, "opencode_default_model", "anthropic/claude-opus-4")
    assert prompt_token_limit("opencode") == 200_000  # the configured default model decides
    monkeypatch.setattr(settings, "context_profile", "extended_128k")
    assert prompt_token_limit("sgpt") == 131072
    monkeypatch.setattr(settings, "max_prompt_tokens", 20000)
    assert prompt_token_limit("sgpt") == prompt_token_limit("claude") == prompt_token_limit("opencode") == 20000


def test_the_tool_loop_condenses_older_results_beyond_its_total_budget():
    from agent.cli_backends.tool_loop import build_tool_loop_prompt, get_tool_loop_config

    results = [{"tool_name": f"repo.read_file_range#{i}", "status": "ok", "data": {"text": str(i) * 7000}}
               for i in range(6)]
    prompt = build_tool_loop_prompt(original_prompt="task", instructions="rules", tool_results=results,
                                    iteration=7, max_iterations=8, max_tool_result_chars=8000,
                                    max_total_tool_result_chars=20000)
    assert "5" * 7000 in prompt and "4" * 7000 in prompt  # the newest results stay complete
    assert "0" * 7000 not in prompt and prompt.count('"condensed"') == 4  # older ones are marked, not dropped
    assert "repo.read_file_range#0" in prompt
    assert get_tool_loop_config()["max_total_tool_result_chars"] == 32768 * 4 // 2


def test_context_recovery_segmentation_reaches_the_planner():
    from agent.services.planning_strategies import LLMPlanningStrategy

    scoped = {"planning_policy": {"segmented_planning_enabled": False, "max_segments": 3}}
    assert LLMPlanningStrategy.effective_planning_policy(scoped, {"segment_planning": True}) == {
        "segmented_planning_enabled": True, "max_segments": 3}
    assert LLMPlanningStrategy.effective_planning_policy(scoped, None)["segmented_planning_enabled"] is False
    assert LLMPlanningStrategy.effective_planning_policy({}, {})  == {}


def test_the_planning_strategies_can_build_their_german_prompt():
    """The German prompt builder was called (LLM fallback, hub copilot) but never imported: a NameError."""
    from agent.services import planning_strategies

    assert "Ein Ziel" in planning_strategies.build_planning_prompt("Ein Ziel", None, 3)
