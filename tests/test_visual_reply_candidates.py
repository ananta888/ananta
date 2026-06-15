"""Tests for multi-candidate _spawn_visual_reply and _visual_session_settings."""
from __future__ import annotations

import importlib
import pytest


@pytest.fixture
def reset_visual_state():
    """Reset module-global visual state before AND after each test so state
    doesn't leak into other test files."""
    ser = importlib.import_module("agent.routes.snakes_execution_routes")
    ser._visual_last_snapshot = ""
    ser._visual_last_reply_at = 0.0
    yield ser
    ser._visual_last_snapshot = ""
    ser._visual_last_reply_at = 0.0


def _set_pug_settings(settings: dict) -> None:
    from client_surfaces.operator_tui.config.user_config_manager import get_manager
    from client_surfaces.operator_tui.chat_state import make_session
    mgr = get_manager()
    sess = make_session(
        session_id="ananta-visual", name="Visual Snake Log",
        icon="🐍", group="Konfiguration", settings=settings,
    )
    mgr.save({"chat_sessions": [sess], "chat_active_session_id": ""})
    mgr.load()


# ── _visual_session_settings ────────────────────────────────────────────────


def test_visual_session_settings_returns_defaults_when_session_missing(app):
    from agent.routes.snakes_execution_routes import _visual_session_settings
    from client_surfaces.operator_tui.chat_state import _DEFAULT_SESSION_SETTINGS

    result = _visual_session_settings()
    defaults = {k: v for k, v in _DEFAULT_SESSION_SETTINGS.items() if k.startswith("predictive_guide_")}
    assert result == defaults


def test_visual_session_settings_reads_persisted_values(app):
    _set_pug_settings({"predictive_guide_enabled": True, "predictive_guide_multi_candidates": 5})
    from agent.routes.snakes_execution_routes import _visual_session_settings

    result = _visual_session_settings()
    assert result["predictive_guide_enabled"] is True
    assert result["predictive_guide_multi_candidates"] == 5


def test_visual_session_settings_falls_back_for_missing_keys(app):
    _set_pug_settings({"predictive_guide_enabled": True})
    from agent.routes.snakes_execution_routes import _visual_session_settings
    from client_surfaces.operator_tui.chat_state import _DEFAULT_SESSION_SETTINGS

    result = _visual_session_settings()
    assert result["predictive_guide_enabled"] is True
    # Other keys fall back to defaults
    assert result["predictive_guide_dwell_ms"] == _DEFAULT_SESSION_SETTINGS["predictive_guide_dwell_ms"]


def test_visual_session_log_deltas_only_delegates_to_settings(app):
    _set_pug_settings({"predictive_guide_log_deltas_only": False})
    from agent.routes.snakes_execution_routes import _visual_session_log_deltas_only

    assert _visual_session_log_deltas_only() is False


# ── _spawn_visual_reply: PUG disabled guard ──────────────────────────────────


def test_spawn_visual_reply_skips_when_pug_disabled(app, reset_visual_state, monkeypatch):
    """When predictive_guide_enabled=False, _spawn_visual_reply does nothing."""
    _set_pug_settings({"predictive_guide_enabled": False})

    called = []
    monkeypatch.setattr(
        "agent.routes.snakes_execution_routes._append_room_ai_message",
        lambda **kwargs: called.append(kwargs),
    )

    from agent.routes.snakes_execution_routes import _spawn_visual_reply
    _spawn_visual_reply("/teams | h:Teams")

    # No AI message appended
    assert not called


def test_spawn_visual_reply_skips_when_snapshot_unchanged(app, reset_visual_state, monkeypatch):
    """When the snapshot equals _visual_last_snapshot, early-return before any API call."""
    import agent.routes.snakes_execution_routes as ser
    ser._visual_last_snapshot = "/same | h:Same"
    _set_pug_settings({"predictive_guide_enabled": True})

    called = []
    monkeypatch.setattr(
        "agent.routes.snakes_execution_routes._append_room_ai_message",
        lambda **kwargs: called.append(kwargs),
    )

    from agent.routes.snakes_execution_routes import _spawn_visual_reply
    _spawn_visual_reply("/same | h:Same")

    assert not called


def test_spawn_visual_reply_respects_throttle(app, reset_visual_state, monkeypatch):
    """When _visual_last_reply_at is recent, skip even if snapshot differs."""
    import time
    import agent.routes.snakes_execution_routes as ser
    ser._visual_last_snapshot = "/old"
    ser._visual_last_reply_at = time.time() - 1.0  # only 1s ago, throttle = 25s
    _set_pug_settings({"predictive_guide_enabled": True})

    called = []
    monkeypatch.setattr(
        "agent.routes.snakes_execution_routes._append_room_ai_message",
        lambda **kwargs: called.append(kwargs),
    )

    from agent.routes.snakes_execution_routes import _spawn_visual_reply
    _spawn_visual_reply("/new | h:New")

    assert not called


# ── Multi-candidate prompt selection ────────────────────────────────────────


def test_spawn_visual_reply_uses_candidates_prompt_for_n_gt_1(app, reset_visual_state, monkeypatch):
    """With n_candidates > 1, the system prompt mentions __CANDIDATES__:."""
    _set_pug_settings({"predictive_guide_enabled": True, "predictive_guide_multi_candidates": 3})

    captured_prompts: list[str] = []

    class FakeResp:
        class choices:
            class _msg:
                content = '__CANDIDATES__: [{"label":"primary","bubble":"test","steps":[]}]'
            choices = [type("C", (), {"message": _msg})()]

    class FakeClient:
        def __init__(self, **_): pass
        class chat:
            class completions:
                @staticmethod
                def create(*, model, messages, **_):
                    captured_prompts.append(messages[0]["content"])
                    return FakeResp

    monkeypatch.setattr("openai.OpenAI", FakeClient)
    monkeypatch.setattr(
        "agent.routes.snakes_execution_routes._append_room_ai_message",
        lambda **kwargs: None,
    )
    # _current_config is imported inside the function from ai_snake_config
    monkeypatch.setattr(
        "agent.routes.ai_snake_config._current_config",
        lambda: {"chat_model": "gpt-4o-mini", "chat_api_key": "k"},
    )

    from agent.routes import snakes_execution_routes as ser
    ser._visual_last_reply_at = 0.0
    from agent.routes.snakes_execution_routes import _spawn_visual_reply
    _spawn_visual_reply("/teams | h:Teams")

    assert captured_prompts, "LLM was not called"
    assert "__CANDIDATES__:" in captured_prompts[0]


def test_spawn_visual_reply_uses_guide_prompt_for_single_candidate(app, reset_visual_state, monkeypatch):
    """With n_candidates == 1, the legacy __GUIDE__: format is used."""
    _set_pug_settings({"predictive_guide_enabled": True, "predictive_guide_multi_candidates": 1})

    captured_prompts: list[str] = []

    class FakeResp:
        class choices:
            class _msg:
                content = "Hier ist ein Hinweis."
            choices = [type("C", (), {"message": _msg})()]

    class FakeClient:
        def __init__(self, **_): pass
        class chat:
            class completions:
                @staticmethod
                def create(*, model, messages, **_):
                    captured_prompts.append(messages[0]["content"])
                    return FakeResp

    monkeypatch.setattr("openai.OpenAI", FakeClient)
    monkeypatch.setattr(
        "agent.routes.snakes_execution_routes._append_room_ai_message",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "agent.routes.ai_snake_config._current_config",
        lambda: {"chat_model": "gpt-4o-mini", "chat_api_key": "k"},
    )

    from agent.routes import snakes_execution_routes as ser
    ser._visual_last_reply_at = 0.0
    from agent.routes.snakes_execution_routes import _spawn_visual_reply
    _spawn_visual_reply("/chats | h:Chats")

    assert captured_prompts
    assert "__GUIDE__:" in captured_prompts[0]
    assert "__CANDIDATES__:" not in captured_prompts[0]
