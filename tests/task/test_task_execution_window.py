"""The Hub sizes a task for the runtime that will execute it: local profile or the subscription/cloud window."""

from __future__ import annotations

import pytest

from agent.services.task_execution_window_service import execution_window

TASK = {"id": "T-1", "task_kind": "analysis", "title": "Analyse", "description": "Analysiere die Doku"}


@pytest.fixture(autouse=True)
def local_32k(monkeypatch):
    from agent.config import settings

    monkeypatch.setattr(settings, "context_profile", "standard_32k")
    monkeypatch.setattr(settings, "max_prompt_tokens", None)


def _cfg(backend, **extra):
    return {"sgpt_routing": {"default_backend": backend, "task_kind_backend": {}}, **extra}


def test_a_local_runtime_gets_the_ananta_profile():
    window = execution_window(TASK, _cfg("ananta-worker", default_provider="llamacpp", default_model="bonsai"))
    assert (window.tokens, window.local, window.reason) == (32768, True, "local_runtime")


def test_claude_cli_keeps_its_own_window():
    window = execution_window(TASK, _cfg("claude_code", default_model="claude-sonnet-4"))
    assert (window.tokens, window.local, window.reason) == (200_000, False, "subscription_cli")


def test_a_cloud_provider_keeps_its_own_window():
    window = execution_window(TASK, _cfg("ananta-worker", default_provider="anthropic", default_model="claude-opus-4"))
    assert window.tokens == 200_000 and not window.local
    assert execution_window(TASK, _cfg("ananta-worker", default_provider="openai", default_model="gpt-4.1")).tokens \
        == 128_000


def test_opencode_follows_its_model():
    cloud = execution_window(TASK, _cfg("opencode", default_model="anthropic/claude-sonnet-4"))
    local = execution_window(TASK, _cfg("opencode", default_model="ollama/qwen2.5-coder"))
    assert (cloud.tokens, cloud.local) == (200_000, False) and (local.tokens, local.local) == (32768, True)


def test_a_failing_prediction_sizes_for_the_local_window(monkeypatch):
    monkeypatch.setattr("agent.services._task_scoped_runtime.resolve_task_cli_backend",
                        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    window = execution_window(TASK, _cfg("claude_code"))
    assert (window.tokens, window.local, window.reason) == (32768, True, "prediction_failed")


def test_a_task_for_claude_is_not_split_but_the_same_task_for_a_local_model_is(app):
    from agent.services.long_context_coordinator import material_fit

    material = "Zeile mit Inhalt. " * 11_200  # ~50k tokens
    with app.app_context():
        assert not material_fit(material, None, 32768).fits
        assert material_fit(material, None, execution_window(TASK, _cfg("claude_code")).tokens).fits
