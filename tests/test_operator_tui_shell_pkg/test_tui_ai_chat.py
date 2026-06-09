from __future__ import annotations

import json
import os
import re
import sys
import time
from importlib import reload
from argparse import Namespace
from pathlib import Path

from client_surfaces.operator_tui.adapters import SectionAdapterRegistry
from client_surfaces.operator_tui.app import build_initial_state, load_active_section
from client_surfaces.operator_tui.actions import dispatch_action, parse_action
from client_surfaces.operator_tui.ai_snake_config_view import (
    ai_snake_config_items,
    apply_ai_snake_config_value,
    chat_model_option_label,
    refresh_chat_backend_models,
)
from client_surfaces.operator_tui.browser import browser_fallback_url
from client_surfaces.operator_tui.capabilities import graphics_decision
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.diagrams import detect_diagram_blocks, render_diagram_fallback
from client_surfaces.operator_tui.markdown_renderer import render_markdown_lines
from client_surfaces.operator_tui.interactive import InteractiveOperatorTui
from client_surfaces.operator_tui.models import FocusPane, OperatorMode, OperatorState, PanelState, SectionLoadResult
from client_surfaces.operator_tui.performance import measure
from client_surfaces.operator_tui.read_models import build_goal_rows, build_task_rows
from client_surfaces.operator_tui.refresh import refresh_policy_for, should_refresh
from client_surfaces.operator_tui.renderer import _overlay_fullscreen_snake, render_operator_shell
from client_surfaces.operator_tui.rollout import operator_tui_enabled, rollback_hint, rollout_stage
from client_surfaces.operator_tui.sections import SECTIONS, move_section, normalize_section_id
from client_surfaces.operator_tui.smoke import run_fixture_smoke
from client_surfaces.operator_tui.snake_persistence import load_tui_chat_settings, save_tui_chat_settings
from client_surfaces.operator_tui.chat_state import sanitize_text
from agent.cli.main import _run_tui


def test_chat_rag_question_tokens_and_name_lookup_prefer_function_detail(tmp_path: Path) -> None:
    out_dir = tmp_path / "rag-out"
    out_dir.mkdir()
    (out_dir / "index.jsonl").write_text(
        json.dumps({"kind": "function", "file": "src/unrelated.py", "name": "toggle_misc"}) + "\n",
        encoding="utf-8",
    )
    details = [
        {"kind": "function", "file": "src/unrelated.py", "name": "other"},
        {"kind": "function", "file": "client_surfaces/operator_tui/snake_ops_mixin.py", "name": "_toggle_snake_mode"},
        {"kind": "class", "file": "client_surfaces/operator_tui/snake_ops_mixin.py", "name": "SnakeOpsMixin"},
    ]
    (out_dir / "details.jsonl").write_text("\n".join(json.dumps(d) for d in details) + "\n", encoding="utf-8")

    from client_surfaces.operator_tui.tutorial_ai_mixin import _load_rag_context_from_dir

    context = _load_rag_context_from_dir(out_dir, ["toggle", "snake", "mode"], 5, 1, scope_filter="full")
    joined = "\n".join(context)

    assert context
    assert "snake_ops_mixin.py" in context[0]
    assert "_toggle_snake_mode" in joined

    class_context = _load_rag_context_from_dir(out_dir, ["snakeopsmixin"], 5, 1, scope_filter="full")
    assert "SnakeOpsMixin" in "\n".join(class_context)



def test_chat_llm_prompt_honors_context_char_opt_out_and_prior_messages(monkeypatch) -> None:
    state = OperatorState(endpoint="http://localhost:5000")
    tui = InteractiveOperatorTui(state)
    captured: dict[str, object] = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

    def _fake_urlopen(req, timeout):
        captured["body"] = json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setenv("ANANTA_TUI_SNAKE_AI_API_BASE_URL", "http://lmstudio.test/v1")
    monkeypatch.setenv("ANANTA_TUI_CHAT_CONTEXT_CHARS", "500")
    monkeypatch.setenv("ANANTA_TUI_CHAT_STREAMING", "0")
    monkeypatch.setattr("client_surfaces.operator_tui.chat_mixin.urllib.request.urlopen", _fake_urlopen)

    answer = tui._tutorial_ai_llm_ask(
        question="und was bedeutet das?",
        context_text="x" * 900,
        depth="deep",
        prior_messages=[{"role": "assistant", "content": "vorherige antwort"}],
    )

    assert answer == "ok"
    body = captured["body"]
    assert isinstance(body, dict)
    messages = body["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].count("x") >= 500
    assert {"role": "assistant", "content": "vorherige antwort"} in messages
    assert body["max_tokens"] >= 400



def test_chat_channel_cycle_preserves_input_buffer() -> None:
    from client_surfaces.operator_tui.chat_state import get_sessions

    game = {"active": True, "alive": True, "ui_steering": True}
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui._chat_focus_enter()
    tui._chat_append("abc")

    # Capture the active channel right after chat focus — the cycle test
    # asserts the channel CHANGED after one cycle step, but the test
    # fixture starts on whatever channel the chat-focus defaults to.
    before_cycle = (tui.state.header_logo_game or {}).get("chat_state", {}).get("active_channel")

    tui._chat_cycle_channel()

    chat = (tui.state.header_logo_game or {}).get("chat_state") or {}
    # After upgrading to the sessions model the cycle order is
    # `[room:main, notes:self, system, ai:<session1>, ai:<session2>, ...]`.
    # The default first session is "code-help", so when the chat-focus
    # is on `ai:code-help` (the default), one cycle step moves to the
    # next session channel, which is `ai:writing-coach`. The input
    # buffer must be preserved through the cycle.
    assert chat.get("active_channel") != before_cycle
    assert chat.get("chat_input_buffer") == "abc"
    # The next session after code-help is writing-coach
    sessions = get_sessions(chat)
    second_id = str(sessions[1].get("id")) if len(sessions) > 1 else None
    if before_cycle == "ai:code-help" and second_id:
        # The cycle should have moved to the second session
        assert chat.get("active_channel") == f"ai:{second_id}"



def test_chat_input_supports_cursor_backspace_delete_and_history() -> None:
    from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state

    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game={"active": True})
    tui = InteractiveOperatorTui(state)
    tui._chat_focus_enter()
    tui._chat_append("abcd")
    tui._chat_move_cursor(-2)
    tui._chat_backspace()
    tui._chat_delete()

    game = dict(tui.state.header_logo_game or {})
    chat = get_chat_state(game)
    assert chat.get("chat_input_buffer") == "ad"
    assert int(chat.get("chat_input_cursor") or 0) == 1

    chat["chat_input_buffer"] = "draft"
    chat["chat_input_cursor"] = 5
    chat["chat_input_history"] = ["eins", "zwei"]
    chat["chat_input_history_index"] = None
    set_chat_state(game, chat)
    tui._set_state(tui.state.with_updates(header_logo_game=game))

    tui._chat_history_move(-1)
    chat = (tui.state.header_logo_game or {}).get("chat_state") or {}
    assert chat.get("chat_input_buffer") == "zwei"
    tui._chat_history_move(-1)
    chat = (tui.state.header_logo_game or {}).get("chat_state") or {}
    assert chat.get("chat_input_buffer") == "eins"
    tui._chat_history_move(1)
    chat = (tui.state.header_logo_game or {}).get("chat_state") or {}
    assert chat.get("chat_input_buffer") == "zwei"
    tui._chat_history_move(1)
    chat = (tui.state.header_logo_game or {}).get("chat_state") or {}
    assert chat.get("chat_input_buffer") == "draft"



def test_ai_chat_send_does_not_pause_snake() -> None:
    game = {"active": True, "alive": True, "ui_steering": True, "free_mode": True, "tutorial_mode": False}
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui._chat_focus_enter()
    tui._chat_cycle_channel()
    tui._chat_append("hi")

    tui._chat_send_message()

    updated = tui.state.header_logo_game or {}
    assert updated.get("paused") is not True
    assert updated.get("tutor_ask_question") == "hi"
    assert updated.get("_ask_submitted") is False
    assert updated.get("tutorial_mode") is False



def test_chat_panel_shows_timeout_progress_while_ai_typing() -> None:
    now = time.monotonic()
    game = {
        "chat_panel_open": True,
        "tutor_ask_at": now - 1.2,
        "tutor_ask_timeout_s": 8.0,
        "chat_state": {
            "active_channel": "ai:tutor",
            "chat_focus": True,
            "ai_typing": True,
            "channels": {
                "ai:tutor": {
                    "id": "ai:tutor",
                    "channel_type": "ai",
                    "display_name": "AI tutor-ai",
                    "messages": [{"sender_id": "s1", "sender_kind": "user", "text": "frage", "created_at": now - 2}],
                    "unread": 0,
                },
                "room:main": {"id": "room:main", "channel_type": "room", "display_name": "#room", "messages": [], "unread": 0},
                "notes:self": {"id": "notes:self", "channel_type": "notes", "display_name": "notes", "messages": [], "unread": 0},
            },
        },
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.DETAIL, section_id="tasks", header_logo_game=game)
    output = render_operator_shell(state, width=110, height=24)
    assert "timeout in" in output



def test_ai_snake_config_panel_toggles_in_middle_content() -> None:
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game={"tutorial_mode": True})
    tui = InteractiveOperatorTui(state)

    tui._toggle_ai_snake_config_panel()
    output_open = render_operator_shell(tui.state, width=110, height=24)
    assert "AI-SNAKE CONFIG" in output_open
    assert "CFG:on" in output_open

    tui._toggle_ai_snake_config_panel()
    output_closed = render_operator_shell(tui.state, width=110, height=24)
    assert "CFG:on" not in output_closed



def test_ai_snake_config_selected_can_disable_visual_ai() -> None:
    game = {
        "tutorial_mode": True,
        "ai_snake_config_open": True,
        "snakes": {"s-ai": {"id": "s-ai", "pseudonym": "tutor-ai"}},
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, selected_index=0, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT, selected_index=0)

    tui._toggle_ai_snake_config_selected()
    opened = tui.state.header_logo_game or {}
    combo = dict(opened.get("ai_snake_config_combo") or {})
    assert combo.get("open") is True

    tui._ai_snake_config_combo_append_filter("aus")
    tui._ai_snake_config_combo_commit()

    updated = tui.state.header_logo_game or {}
    assert updated.get("tutorial_mode") is False
    assert "s-ai" not in dict(updated.get("snakes") or {})



def test_ai_snake_config_open_resets_chat_focus_and_command_mode() -> None:
    from client_surfaces.operator_tui.chat_state import get_chat_state

    game = {
        "tutorial_mode": True,
        "chat_panel_open": True,
        "artifact_chat_focus": True,
    }
    state = OperatorState(
        endpoint="http://localhost:5000",
        focus=FocusPane.DETAIL,
        mode=OperatorMode.COMMAND,
        command_line="chat backend list",
        header_logo_game=game,
    )
    tui = InteractiveOperatorTui(state)
    tui._chat_focus_enter()

    tui._toggle_ai_snake_config_panel()

    updated = tui.state.header_logo_game or {}
    chat = get_chat_state(updated)
    assert tui.state.mode is OperatorMode.NORMAL
    assert tui.state.command_line == ""
    assert tui.state.focus is FocusPane.CONTENT
    assert chat.get("chat_focus") is False
    assert updated.get("artifact_chat_focus") is False



def test_ai_snake_config_combo_enter_applies_filter_input_value() -> None:
    game = {
        "ai_snake_config_open": True,
        "chat_backends_available": ["ananta-worker", "opencode", "lmstudio"],
        "chat_backend": "ananta-worker",
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, selected_index=4, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT, selected_index=4)

    tui._toggle_ai_snake_config_selected()
    tui._ai_snake_config_combo_append_filter("code|studio")
    tui._ai_snake_config_combo_commit()

    updated = tui.state.header_logo_game or {}
    assert updated.get("chat_backend") == "code|studio"



def test_ai_snake_config_combo_arrow_selection_applies_option() -> None:
    game = {
        "ai_snake_config_open": True,
        "chat_backends_available": ["ananta-worker", "opencode", "lmstudio"],
        "chat_backend": "ananta-worker",
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, selected_index=4, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT, selected_index=4)

    tui._toggle_ai_snake_config_selected()
    tui._ai_snake_config_combo_move(1)
    tui._ai_snake_config_combo_commit()

    updated = tui.state.header_logo_game or {}
    assert updated.get("chat_backend") == "opencode"



def test_ai_snake_config_chat_model_combo_loads_lmstudio_models(monkeypatch) -> None:
    game = {
        "ai_snake_config_open": True,
        "chat_backend": "lmstudio",
        "chat_backend_api_base": "http://lmstudio.test/v1",
        "chat_backend_models": [],
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, selected_index=5, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT, selected_index=5)

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "data": [
                        {"id": "qwen/qwen3-coder-30b"},
                        {"id": "google/gemma-3n-e4b"},
                    ]
                }
            ).encode("utf-8")

    monkeypatch.setattr(
        "client_surfaces.operator_tui.ai_snake_config_view.urllib.request.urlopen",
        lambda req, timeout=0: _FakeResp(),
    )

    tui._toggle_ai_snake_config_selected()

    updated = tui.state.header_logo_game or {}
    models = [str(item) for item in (updated.get("chat_backend_models") or [])]
    combo = dict(updated.get("ai_snake_config_combo") or {})
    assert bool(combo.get("open")) is True
    assert "qwen/qwen3-coder-30b" in models
    assert "google/gemma-3n-e4b" in models



def test_ai_snake_config_chat_model_fetch_falls_back_to_localhost(monkeypatch) -> None:
    game = {
        "ai_snake_config_open": True,
        "chat_backend": "lmstudio",
        "chat_backend_api_base": "http://192.168.178.100:1234/v1",
        "chat_backend_models": [],
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, selected_index=5, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT, selected_index=5)

    calls: list[str] = []

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"data": [{"id": "local/model-a"}]}).encode("utf-8")

    def _fake_urlopen(req, timeout=0):
        url = str(getattr(req, "full_url", ""))
        calls.append(url)
        if "localhost:1234" in url:
            return _FakeResp()
        raise OSError("unreachable")

    monkeypatch.setattr(
        "client_surfaces.operator_tui.ai_snake_config_view.urllib.request.urlopen",
        _fake_urlopen,
    )

    tui._toggle_ai_snake_config_selected()

    updated = tui.state.header_logo_game or {}
    models = [str(item) for item in (updated.get("chat_backend_models") or [])]
    assert "local/model-a" in models
    assert any("localhost:1234" in call for call in calls)
    assert str(updated.get("chat_backend_api_base") or "").startswith("http://localhost:1234")



def test_ai_snake_config_chat_model_fetch_includes_unloaded_lmstudio_models(monkeypatch) -> None:
    game = {
        "ai_snake_config_open": True,
        "chat_backend": "lmstudio",
        "chat_backend_api_base": "http://localhost:1234/v1",
        "chat_backend_models": [],
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, selected_index=5, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT, selected_index=5)

    class _FakeResp:
        def __init__(self, payload: dict[str, object] | list[object]) -> None:
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

    def _fake_urlopen(req, timeout=0):
        url = str(getattr(req, "full_url", ""))
        if url.endswith("/v1/models"):
            return _FakeResp({"data": [{"id": "google/gemma-4-e4b"}]})
        if url.endswith("/api/v0/models"):
            return _FakeResp(
                {
                    "data": [
                        {"id": "google/gemma-4-e4b", "loaded": True},
                        {"id": "microsoft_-_phi-3.5-mini-instruct", "loaded": False},
                    ]
                }
            )
        raise OSError("unexpected endpoint")

    monkeypatch.setattr(
        "client_surfaces.operator_tui.ai_snake_config_view.urllib.request.urlopen",
        _fake_urlopen,
    )

    tui._toggle_ai_snake_config_selected()

    updated = tui.state.header_logo_game or {}
    models = [str(item) for item in (updated.get("chat_backend_models") or [])]
    states = dict(updated.get("chat_backend_model_states") or {})
    assert "google/gemma-4-e4b" in models
    assert "microsoft_-_phi-3.5-mini-instruct" in models
    assert states.get("google/gemma-4-e4b") == "loaded"
    assert states.get("microsoft_-_phi-3.5-mini-instruct") == "not_loaded"



def test_chat_model_option_label_marks_loaded_status() -> None:
    game = {
        "chat_backend_model_states": {
            "google/gemma-4-e4b": "loaded",
            "microsoft_-_phi-3.5-mini-instruct": "not_loaded",
        }
    }

    assert chat_model_option_label(game, "google/gemma-4-e4b").endswith("[geladen]")
    assert chat_model_option_label(game, "microsoft_-_phi-3.5-mini-instruct").endswith("[nicht geladen]")
    assert chat_model_option_label(game, "unknown/model").endswith("[status unbekannt]")



def test_chat_codecompass_context_can_be_disabled_via_config() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        header_logo_game={"chat_use_codecompass": False},
    )
    tui = InteractiveOperatorTui(state)

    hints = tui._chat_codecompass_context_for_question(question="wie geht x")

    assert hints == []



def test_chat_double_slash_toggles_middle_shortcuts() -> None:
    game = {"active": True, "alive": True, "ui_steering": True, "free_mode": True}
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui._chat_focus_enter()
    tui._chat_append("/")
    tui._chat_append("/")

    tui._chat_send_message()

    updated = tui.state.header_logo_game or {}
    assert updated.get("shortcut_help_middle_open") is True
    assert updated.get("tutor_ask_question") is None

    tui._chat_focus_enter()
    tui._chat_append("/")
    tui._chat_append("/")
    tui._chat_send_message()

    updated = tui.state.header_logo_game or {}
    assert updated.get("shortcut_help_middle_open") is False



def test_chat_focus_toggle_uses_same_shortcut_for_enter_and_exit() -> None:
    game = {"active": True, "alive": True, "ui_steering": True, "free_mode": True}
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)

    tui._toggle_chat_focus()
    assert tui._chat_focus_active() is True

    tui._toggle_chat_focus()
    assert tui._chat_focus_active() is False



def test_ai_snake_config_includes_chat_ask_timeout_field() -> None:
    items = ai_snake_config_items({})
    keys = [str(item.get("key") or "") for item in items]
    assert "chat_ask_timeout_s" in keys



def test_ai_snake_config_includes_chat_provider_and_api_base_fields() -> None:
    items = ai_snake_config_items({})
    by_key = {str(item.get("key") or ""): dict(item) for item in items}
    assert "chat_backend" in by_key
    assert str(by_key["chat_backend"].get("label") or "") == "Chat Provider"
    assert "chat_api_base" in by_key



def test_ai_snake_config_includes_chat_context_control_fields() -> None:
    items = ai_snake_config_items({})
    keys = {str(item.get("key") or "") for item in items}
    assert "chat_use_codecompass" in keys
    assert "chat_include_local_project" in keys
    assert "chat_include_wikipedia" in keys
    assert "chat_source_pack_id" in keys
    assert "chat_retrieval_profile" in keys
    assert "chat_architecture_analysis_mode" in keys
    assert "chat_retrieval_domain_hint" in keys
    assert "chat_code_questions_repo_first" in keys
    assert "chat_context_chars" in keys
    assert "chat_max_tokens" in keys
    assert "chat_rag_top_k" in keys
    assert "chat_answer_chars" in keys



def test_ai_snake_config_applies_chat_ask_timeout_value() -> None:
    game: dict[str, object] = {}
    status = apply_ai_snake_config_value(game, key="chat_ask_timeout_s", value="90")
    assert game.get("chat_ask_timeout_s") == 90.0
    assert "90" in status



def test_ai_snake_config_applies_chat_context_settings() -> None:
    game: dict[str, object] = {}
    status_a = apply_ai_snake_config_value(game, key="chat_use_codecompass", value="AUS")
    status_b = apply_ai_snake_config_value(game, key="chat_source_pack_id", value="ananta-local-only")
    status_c = apply_ai_snake_config_value(game, key="chat_context_chars", value="6000")
    status_d = apply_ai_snake_config_value(game, key="chat_max_tokens", value="1200")
    status_e = apply_ai_snake_config_value(game, key="chat_rag_top_k", value="48")
    status_f = apply_ai_snake_config_value(game, key="chat_answer_chars", value="4000")
    status_g = apply_ai_snake_config_value(game, key="chat_retrieval_profile", value="repo_first")
    status_h = apply_ai_snake_config_value(game, key="chat_architecture_analysis_mode", value="full_scan")
    status_i = apply_ai_snake_config_value(game, key="chat_retrieval_domain_hint", value="worker")
    status_j = apply_ai_snake_config_value(game, key="chat_code_questions_repo_first", value="AN")
    assert game.get("chat_use_codecompass") is False
    assert game.get("chat_source_pack_id") == "ananta-local-only"
    assert game.get("chat_context_chars") == 6000
    assert game.get("chat_max_tokens") == 1200
    assert game.get("chat_rag_top_k") == 48
    assert game.get("chat_answer_chars") == 4000
    assert game.get("chat_retrieval_profile") == "repo_first"
    assert game.get("chat_architecture_analysis_mode") == "full_scan"
    assert game.get("chat_retrieval_domain_hint") == "worker"
    assert game.get("chat_code_questions_repo_first") is True
    assert "AUS" in status_a
    assert "ananta-local-only" in status_b
    assert "6000" in status_c
    assert "1200" in status_d
    assert "48" in status_e
    assert "4000" in status_f
    assert "repo_first" in status_g
    assert "full_scan" in status_h
    assert "worker" in status_i
    assert "AN" in status_j



def test_chat_state_sanitize_text_can_keep_long_messages() -> None:
    long_text = "x" * 1500
    assert len(sanitize_text(long_text, max_len=None)) == 1500



def test_ai_snake_config_backend_switch_fetches_lmstudio_models(monkeypatch) -> None:
    game: dict[str, object] = {
        "chat_backend_api_base": "http://lmstudio.test/v1",
        "chat_backend_models": [],
    }

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"data": [{"id": "meta-llama/llama-3.2-1b-instruct"}]}).encode("utf-8")

    monkeypatch.setattr(
        "client_surfaces.operator_tui.ai_snake_config_view.urllib.request.urlopen",
        lambda req, timeout=0: _FakeResp(),
    )

    status = apply_ai_snake_config_value(game, key="chat_backend", value="lmstudio")
    models = [str(item) for item in (game.get("chat_backend_models") or [])]

    assert "meta-llama/llama-3.2-1b-instruct" in models
    assert game.get("chat_backend_model") == "meta-llama/llama-3.2-1b-instruct"
    assert "modelle" in status



def test_ai_snake_config_backend_switch_sets_ananta_worker_default_model(monkeypatch) -> None:
    game: dict[str, object] = {"chat_backend_models": []}
    monkeypatch.setattr(
        "client_surfaces.operator_tui.ai_snake_config_view.urllib.request.urlopen",
        lambda req, timeout=0: (_ for _ in ()).throw(OSError("unreachable")),
    )

    status = apply_ai_snake_config_value(game, key="chat_backend", value="ananta-worker")
    models = [str(item) for item in (game.get("chat_backend_models") or [])]

    assert "google/gemma-4-e4b" in models
    assert game.get("chat_backend_model") == "google/gemma-4-e4b"
    assert "ananta-worker" in status



def test_ai_snake_config_backend_switch_uses_opencode_default_model(monkeypatch) -> None:
    game: dict[str, object] = {"chat_backend_models": []}
    monkeypatch.delenv("ANANTA_TUI_CHAT_MODEL", raising=False)
    monkeypatch.delenv("ANANTA_TUI_SNAKE_AI_MODEL", raising=False)
    monkeypatch.setenv("OPENCODE_DEFAULT_MODEL", "opencode/test-model")
    monkeypatch.setattr(
        "client_surfaces.operator_tui.ai_snake_config_view.urllib.request.urlopen",
        lambda req, timeout=0: (_ for _ in ()).throw(OSError("unreachable")),
    )

    status = apply_ai_snake_config_value(game, key="chat_backend", value="opencode")
    models = [str(item) for item in (game.get("chat_backend_models") or [])]

    assert "opencode/test-model" in models
    assert game.get("chat_backend_model") == "opencode/test-model"
    assert "opencode" in status



def test_chat_backend_use_sets_default_model_for_opencode(monkeypatch) -> None:
    monkeypatch.delenv("ANANTA_TUI_CHAT_MODEL", raising=False)
    monkeypatch.delenv("ANANTA_TUI_SNAKE_AI_MODEL", raising=False)
    monkeypatch.setenv("OPENCODE_DEFAULT_MODEL", "opencode/cli-default")
    monkeypatch.setattr(
        "client_surfaces.operator_tui.ai_snake_config_view.urllib.request.urlopen",
        lambda req, timeout=0: (_ for _ in ()).throw(OSError("unreachable")),
    )
    state = OperatorState(
        endpoint="http://localhost:5000",
        header_logo_game={
            "chat_backends_available": ["ananta-worker", "opencode", "lmstudio"],
            "chat_backend": "ananta-worker",
            "chat_backend_model": "-",
            "chat_backend_models": [],
        },
    )

    result = execute_command(":chat backend use opencode", state)
    game = dict(result.state.header_logo_game or {})

    assert game.get("chat_backend") == "opencode"
    assert game.get("chat_backend_model") == "opencode/cli-default"



def test_chat_ask_uses_timeout_from_ai_snake_config() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        header_logo_game={"chat_ask_timeout_s": 75.0},
    )
    result = execute_command(":ask timeout test", state)
    game = dict(result.state.header_logo_game or {})
    assert float(game.get("tutor_ask_timeout_s") or 0.0) == 75.0



def test_chat_panel_renders_in_detail_pane_without_overlay() -> None:
    from client_surfaces.operator_tui.chat_state import default_chat_state, make_message, append_message

    chat = default_chat_state("s1")
    chat["active_channel"] = "ai:tutor"
    append_message(
        chat,
        make_message(
            channel_id="ai:tutor",
            channel_type="ai",
            sender_id="s-ai",
            sender_kind="ai",
            text="Kurze Antwort im rechten Hauptbereich",
            delivery_state="received",
        ),
    )
    game = {
        "artifact_chat_state": {
            "active_target": {"kind": "file", "label": "sample.py", "path": "sample.py"},
            "messages": [],
        },
        "chat_panel_open": True,
        "chat_state": chat,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", render_operator_shell(state, width=100, height=24))

    assert "CHAT" in plain
    assert "ACTIVE: AI" in plain
    assert "rechten Hauptbereich" in plain
    assert "Tutorial-AI propose flow" not in plain



def test_chat_messages_use_participant_colors_and_labels() -> None:
    from client_surfaces.operator_tui.chat_state import default_chat_state, make_message, append_message

    chat = default_chat_state("s1")
    chat["active_channel"] = "room:main"
    append_message(
        chat,
        make_message(
            channel_id="room:main",
            channel_type="room",
            sender_id="s1",
            sender_kind="user",
            text="Hallo",
            delivery_state="received",
        ),
    )
    append_message(
        chat,
        make_message(
            channel_id="room:main",
            channel_type="room",
            sender_id="s-ai",
            sender_kind="ai",
            text="Bereit",
            delivery_state="received",
        ),
    )
    game = {
        "chat_panel_open": True,
        "local_snake_id": "s1",
        "snake_color": "mint",
        "pseudonym": "alice",
        "snakes": {
            "s1": {"id": "s1", "pseudonym": "alice", "snake_color": "mint"},
            "s-ai": {"id": "s-ai", "pseudonym": "tutor-ai", "snake_color": "amber"},
        },
        "chat_state": chat,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    output = render_operator_shell(state, width=100, height=24)

    assert "\x1b[38;2;170;255;210malice: Hallo\x1b[0m" in output
    assert "\x1b[38;2;255;205;130mAI-snake: Bereit\x1b[0m" in output


