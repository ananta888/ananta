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



# Split from tests/test_operator_tui_shell_pkg/test_tui_command_modes.py to keep source files below 1000 lines.

def test_enter_handles_config_even_when_focus_is_not_content() -> None:
    game = {
        "ai_snake_config_open": True,
        "chat_backends_available": ["ananta-worker", "opencode", "lmstudio"],
        "chat_backend": "ananta-worker",
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.DETAIL, selected_index=4, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.DETAIL, selected_index=4)

    assert tui._open_audit_viewer_for_selected() is True

    updated = tui.state.header_logo_game or {}
    combo = dict(updated.get("ai_snake_config_combo") or {})
    assert tui.state.focus is FocusPane.CONTENT
    assert bool(combo.get("open")) is True



def test_enter_on_navigation_history_opens_cached_original_output() -> None:
    game = {
        "chat_long_message_history": [
            {
                "id": "answer-1",
                "channel_id": "ai:tutor",
                "sender_kind": "ai",
                "text": "Antwort " + ("lang " * 30),
                "markdown": "# Chat-Nachricht\n\nAntwort " + ("lang " * 30),
                "created_at": 10.0,
            }
        ]
    }
    state = OperatorState(
        endpoint="http://localhost:5000",
        focus=FocusPane.NAVIGATION,
        selected_index=len(SECTIONS),
        header_logo_game=game,
    )
    tui = InteractiveOperatorTui(state)

    assert tui._open_audit_viewer_for_selected() is True

    updated = tui.state.header_logo_game or {}
    assert tui.state.focus is FocusPane.CONTENT
    assert updated["chat_long_message_plain_text"].startswith("Antwort lang")
    assert updated["markdown_stream_plain"] is True
    assert updated["markdown_mermaid_render_requested"] is False
    assert tui.state.status_message == "Chat-History: Originalausgabe"



def test_enter_command_mode_from_anywhere_closes_chat_artifact_and_config() -> None:
    game = {
        "ai_snake_config_open": True,
        "ai_snake_config_combo": {"open": True, "key": "chat_backend"},
        "artifact_chat_focus": True,
        "chat_panel_open": True,
        "chat_state": {"chat_focus": True, "chat_input_buffer": "x", "chat_input_cursor": 1},
    }
    state = OperatorState(endpoint="http://localhost:5000", mode=OperatorMode.NORMAL, header_logo_game=game)
    tui = InteractiveOperatorTui(state)

    tui._enter_command_mode_from_anywhere()

    updated = tui.state.header_logo_game or {}
    chat_state = dict(updated.get("chat_state") or {})
    assert tui.state.mode is OperatorMode.COMMAND
    assert chat_state.get("chat_focus") is False
    assert updated.get("artifact_chat_focus") is False
    assert bool(dict(updated.get("ai_snake_config_combo") or {}).get("open")) is False
    assert updated.get("ai_snake_config_open") is False



def test_global_shortcut_exit_command_mode_resets_to_normal() -> None:
    state = OperatorState(endpoint="http://localhost:5000", mode=OperatorMode.COMMAND, command_line=":help")
    tui = InteractiveOperatorTui(state)
    tui._command_buffer = ":help"
    tui._command_cursor = 5

    tui._exit_command_mode_for_global_shortcut()

    assert tui.state.mode is OperatorMode.NORMAL
    assert tui.state.command_line == ""
    assert tui._command_buffer == ""



def test_context_help_explains_terminal_context_shortcut() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        header_logo_game={"shortcut_help_open": True},
    )

    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", render_operator_shell(state, width=160, height=24))

    assert "SHORTCUTS" in plain
    assert "Ctrl+S Snake" in plain



def test_artifact_chat_input_supports_cursor_delete_and_history() -> None:
    game = {
        "artifact_chat_state": {"active_target": {"kind": "file", "label": "sample.py"}},
        "chat_panel_open": True,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui._artifact_chat_focus_enter()
    tui._artifact_chat_append("wxyz")
    tui._artifact_chat_move_cursor(-2)
    tui._artifact_chat_backspace()
    tui._artifact_chat_delete()
    updated = tui.state.header_logo_game or {}
    assert updated.get("artifact_chat_input") == "wz"
    assert int(updated.get("artifact_chat_cursor") or 0) == 1

    updated["artifact_chat_input"] = "draft-art"
    updated["artifact_chat_cursor"] = len("draft-art")
    updated["artifact_chat_history"] = ["alt-1", "alt-2"]
    updated["artifact_chat_history_index"] = None
    tui._set_state(tui.state.with_updates(header_logo_game=updated))
    tui._artifact_chat_history_move(-1)
    assert (tui.state.header_logo_game or {}).get("artifact_chat_input") == "alt-2"
    tui._artifact_chat_history_move(1)
    assert (tui.state.header_logo_game or {}).get("artifact_chat_input") == "draft-art"



def test_save_tui_snapshot_writes_file(monkeypatch, tmp_path) -> None:
    state = OperatorState(endpoint="http://localhost:5000", status_message="snapshot-ready")
    tui = InteractiveOperatorTui(state)
    tui._rendered_text = "HEADER\nbody line\n"
    monkeypatch.setenv("ANANTA_TUI_SNAPSHOT_DIR", str(tmp_path))

    tui._save_tui_snapshot()

    game = tui.state.header_logo_game or {}
    target = Path(str(game.get("last_tui_snapshot_path") or ""))
    assert target.exists()
    assert target.parent == tmp_path
    assert target.read_text(encoding="utf-8") == "HEADER\nbody line\n"
    assert "gespeichert" in str(tui.state.status_message)



def test_prediction_comments_are_routed_to_ai_monitor_not_chat() -> None:
    from client_surfaces.operator_tui.chat_state import default_chat_state

    game = {
        "chat_state": default_chat_state("s1"),
        "ai_snake_monitor_log": [],
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    prediction = {"predicted_intent": "artifact_explain", "target_ref": "DETAIL", "confidence": 0.78}

    changed = tui._route_prediction_comment_to_monitor(
        game,
        prediction=prediction,
        now=100.0,
        quiet=False,
        forced=False,
        cooldown_seconds=20,
    )

    assert changed is True
    chat = game.get("chat_state") or {}
    ai_msgs = (((chat.get("channels") or {}).get("ai:tutor") or {}).get("messages") or [])
    assert ai_msgs == []
    monitor = game.get("ai_snake_monitor_log") or []
    assert monitor
    assert "Ich glaube, du willst zu DETAIL" in str(monitor[-1].get("label") or "")



def test_keyboard_nav_while_visual_viewport_active_closes_viewport(monkeypatch) -> None:
    """Regression: Ctrl+J (selection_down) in nav pane must also close the visual viewport."""
    monkeypatch.setattr(
        "client_surfaces.operator_tui.mouse_artifact_mixin.shutil.get_terminal_size",
        lambda fallback=(120, 32): os.terminal_size((120, 33)),
    )
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="dashboard",
        focus=FocusPane.NAVIGATION,
        selected_index=0,
    )
    tui = InteractiveOperatorTui(state)
    game = dict(tui.state.header_logo_game or {})
    game["visual_viewport_enabled"] = True
    game["visual_viewport"] = {"enabled": True}
    tui.state = tui.state.with_updates(header_logo_game=game)

    # Simulate Ctrl+J (selection_down) which calls _set_selected_index(1) → Goals
    tui._set_selected_index(1)

    result_game = tui.state.header_logo_game or {}
    assert tui.state.section_id == "goals"
    assert result_game.get("visual_viewport_enabled") is False
    assert dict(result_game.get("visual_viewport") or {}).get("enabled") is False



def test_streaming_update_activates_middle_view_and_stores_history() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="goals",
        focus=FocusPane.CONTENT,
    )
    tui = InteractiveOperatorTui(state)
    game = dict(tui.state.header_logo_game or {})
    game["visual_viewport_enabled"] = False
    game["chat_state"] = {"ai_pending_msg_channel": "ai:tutor"}
    tui.state = tui.state.with_updates(header_logo_game=game)

    setattr(tui, "_llm_streaming_partial", "antwort " * 30)
    tui._poll_tutor_ask_result(game)

    # Streaming always activates the middle view so the user sees real-time output.
    assert game.get("visual_viewport_enabled") is True
    assert game.get("visual_viewport_active_view_request") == "markdown_mermaid_document"
    assert game.get("chat_long_message_streaming") is True
    rows = list(game.get("chat_long_message_history") or [])
    assert rows


# ── T17: Tab-Bar Renderer ────────────────────────────────────────────────────


def test_ctrl_e_chat_focus_opens_chat_panel_from_audit_viewer() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="audit",
        focus=FocusPane.CONTENT,
        header_logo_game={"audit_viewer": {"active": True}, "chat_panel_open": False},
    )
    tui = InteractiveOperatorTui(state)

    tui._toggle_chat_focus()

    game = dict(tui.state.header_logo_game or {})
    assert game.get("chat_panel_open") is True
    assert game.get("artifact_chat_focus") is True



def test_enter_sends_chat_even_with_active_audit_viewer(monkeypatch) -> None:
    called: list[bool] = []
    state = OperatorState(
        endpoint="http://localhost:5000",
        section_id="audit",
        focus=FocusPane.CONTENT,
        header_logo_game={
            "audit_viewer": {"active": True},
            "artifact_chat_focus": True,
            "chat_panel_open": True,
        },
    )
    tui = InteractiveOperatorTui(state)
    monkeypatch.setattr(tui, "_artifact_chat_send_message", lambda: called.append(True))

    tui._handle_enter_key()

    assert called == [True]


