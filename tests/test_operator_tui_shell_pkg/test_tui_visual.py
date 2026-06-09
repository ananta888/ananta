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


def test_trail_message_window_and_speed_scroll_over_full_text(monkeypatch) -> None:
    times = iter([0.0, 2.0])
    monkeypatch.setattr("client_surfaces.operator_tui.renderer.time.monotonic", lambda: next(times))
    lines = [" " * 40] * 20
    game = {
        "active": True,
        "free_mode": True,
        "snake": [(1, 0), (0, 0)],
        "trail_path": [(1, 0), (0, 0), (2, 0), (3, 0), (4, 0), (5, 0), (6, 0)],
        "message": "ABCDE",
        "message_style": "trail",
        "snake_message_effect_enabled": True,
        "trail_window": 3,
        "trail_speed": 1.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    out1 = _overlay_fullscreen_snake(lines, state, width=40)
    plain1 = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", out1[0])
    out2 = _overlay_fullscreen_snake(lines, state, width=40)
    plain2 = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", out2[0])

    assert plain1[2:5] == "ABC"
    assert plain2[2:5] == "CDE"



def test_trail_message_remains_visible_when_snake_stops(monkeypatch) -> None:
    monkeypatch.setattr("client_surfaces.operator_tui.renderer.time.monotonic", lambda: 1.0)
    lines = [" " * 40] * 20
    game = {
        "active": True,
        "free_mode": True,
        "snake": [(1, 0), (0, 0)],
        "trail_path": [(1, 0), (0, 0)],  # no extra movement trail
        "message": "HELLO",
        "message_style": "trail",
        "snake_message_effect_enabled": True,
        "trail_window": 5,
        "trail_speed": 1.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    out = _overlay_fullscreen_snake(lines, state, width=40)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", out[0])

    letters = "".join(ch for ch in plain if ch.isalpha())
    assert len(letters) >= 4



def test_trail_message_translates_newlines_for_display_only(monkeypatch) -> None:
    monkeypatch.setattr("client_surfaces.operator_tui.renderer.time.monotonic", lambda: 0.0)
    lines = [" " * 40] * 20
    game = {
        "active": True,
        "free_mode": True,
        "snake": [(1, 0), (0, 0)],
        "trail_path": [(1, 0), (0, 0), (2, 0), (3, 0), (4, 0), (5, 0)],
        "message": "A\nB",
        "message_style": "trail",
        "snake_message_effect_enabled": True,
        "trail_window": 4,
        "trail_speed": 1.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    out = _overlay_fullscreen_snake(lines, state, width=40)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", out[0])

    assert "⏎" in plain



def test_split_snake_playfield_uses_full_terminal_width() -> None:
    lines = [" " * 120 for _ in range(32)]
    game = {
        "active": True,
        "free_mode": True,
        "local_snake_id": "s1",
        "snake": [(118, 4), (117, 4), (116, 4)],
        "trail_path": [(118, 4), (117, 4), (116, 4)],
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    out = _overlay_fullscreen_snake(lines, state, width=120)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", out[4])

    assert plain[118] in {"●", "◉", "·"}



def test_split_snake_chat_panel_stays_in_right_detail_slice() -> None:
    from client_surfaces.operator_tui.chat_state import append_message, default_chat_state, make_message

    chat = default_chat_state("s1")
    chat["active_channel"] = "ai:tutor"
    append_message(
        chat,
        make_message(
            channel_id="ai:tutor",
            channel_type="ai",
            sender_id="s-ai",
            sender_kind="ai",
            text="Antwort",
            delivery_state="received",
        ),
    )
    game = {
        "active": True,
        "free_mode": True,
        "local_snake_id": "s1",
        "snake": [(1, 1)],
        "trail_path": [(1, 1)],
        "chat_panel_open": True,
        "chat_state": chat,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    rendered = render_operator_shell(state, width=120, height=32)
    plain_lines = [re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line) for line in rendered.splitlines()]
    active_row = next(line for line in plain_lines if "ACTIVE: AI" in line)
    split_col = 120 - 34

    assert active_row.index("ACTIVE: AI") >= split_col + 2



def test_split_snake_chat_panel_does_not_blank_snake_under_empty_rows() -> None:
    lines = [" " * 120 for _ in range(32)]
    game = {
        "active": True,
        "free_mode": True,
        "local_snake_id": "s1",
        "snake": [(60, 20), (59, 20), (58, 20)],
        "trail_path": [(60, 20), (59, 20), (58, 20)],
        "chat_panel_open": True,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    out = _overlay_fullscreen_snake(lines, state, width=120)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", out[20])

    assert plain[60] == "●"



def test_visual_command_toggles_and_requests_view() -> None:
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game={})

    on = execute_command(":visual on", state).state
    game_on = dict(on.header_logo_game or {})
    assert bool(game_on.get("visual_viewport_enabled")) is True

    requested = execute_command(":visual view snake_debug_view", on).state
    game_requested = dict(requested.header_logo_game or {})
    assert game_requested.get("visual_viewport_active_view_request") == "snake_debug_view"
    assert bool(game_requested.get("visual_viewport_enabled")) is True



def test_visual_command_rejects_unknown_view_with_known_view_list() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        header_logo_game={"visual_viewport_available_views": ["logo_animation", "snake_debug_view"]},
    )

    result = execute_command(":visual view not-real", state)

    assert result.handled is False
    assert "logo_animation" in str(result.state.status_message or "")
    assert "snake_debug_view" in str(result.state.status_message or "")



def test_visual_viewport_content_lines_render_in_center_pane() -> None:
    game = {
        "visual_viewport": {"enabled": True},
        "visual_runtime_status": {
            "active_view": "renderer_diagnostics",
            "active_renderer": "ansi_blocks",
            "active_adapter": "ansi",
        },
        "visual_viewport_frame_lines": ["[renderer_diagnostics]", "view=renderer_diagnostics"],
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.CONTENT, header_logo_game=game)

    output = render_operator_shell(state, width=110, height=24)

    assert "VISUAL VIEWPORT" in output
    assert "[renderer_diagnostics]" in output



def test_compact_artifact_chat_input_sends_ai_question() -> None:
    game = {
        "artifact_chat_state": {
            "active_target": {"kind": "file", "label": "sample.py", "path": "sample.py", "id": "sample"},
            "messages": [],
        },
        "chat_panel_open": True,
        "tutorial_mode": False,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui._artifact_chat_focus_enter()
    tui._artifact_chat_append("Was macht das?")
    tui._artifact_chat_send_message()

    updated = tui.state.header_logo_game or {}
    assert updated.get("tutor_ask_question") == "Was macht das?"
    assert updated.get("tutorial_mode") is False
    artifact_messages = ((updated.get("artifact_chat_state") or {}).get("messages") or [])
    assert artifact_messages[-1]["source"] == "user"



def test_copy_chat_panel_snapshot_writes_clipboard(monkeypatch) -> None:
    from client_surfaces.operator_tui.chat_state import default_chat_state, append_message, make_message

    chat = default_chat_state("s1")
    chat["active_channel"] = "ai:tutor"
    append_message(
        chat,
        make_message(
            channel_id="ai:tutor",
            channel_type="ai",
            sender_id="s-ai",
            sender_kind="ai",
            text="copy me",
            delivery_state="received",
        ),
    )
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game={"chat_state": chat, "active": True})
    tui = InteractiveOperatorTui(state)
    monkeypatch.setattr(tui, "_copy_to_system_clipboard", lambda text: True)

    tui._copy_chat_panel_snapshot()

    game = tui.state.header_logo_game or {}
    copied = str(game.get("clipboard") or "")
    assert "CHAT" in copied
    assert "copy me" in copied
    assert "AI-snake" in copied



def test_copy_ai_status_snapshot_writes_clipboard(monkeypatch) -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        header_logo_game={
            "tutorial_mode": True,
            "chat_panel_open": True,
            "ai_snake_mode": "lurking_follow",
            "ai_snake_runtime_status": "running",
            "ai_snake_monitor_log": [{"event": "food_eaten", "label": "Food aufgenommen", "created_at": time.time()}],
        },
    )
    tui = InteractiveOperatorTui(state)
    monkeypatch.setattr(tui, "_copy_to_system_clipboard", lambda text: True)

    tui._copy_ai_status_snapshot()

    game = tui.state.header_logo_game or {}
    copied = str(game.get("clipboard") or "")
    assert "AI-SNAKE STATUS" in copied
    assert "ai_snake_mode=lurking_follow" in copied
    assert "events:" in copied



def test_copy_tui_snapshot_writes_entire_rendered_view(monkeypatch) -> None:
    state = OperatorState(endpoint="http://localhost:5000", status_message="snapshot-ready")
    tui = InteractiveOperatorTui(state)
    tui._rendered_text = "\x1b[31mHEADER\x1b[0m\nbody line\n"
    copied_values: list[str] = []
    monkeypatch.setattr(tui, "_copy_to_system_clipboard", lambda text: copied_values.append(text) or True)

    tui._copy_tui_snapshot()

    game = tui.state.header_logo_game or {}
    copied = str(game.get("clipboard") or "")
    assert copied == "HEADER\nbody line\n"
    assert copied_values == [copied]
    assert "System-Zwischenablage" in str(tui.state.status_message)



def test_region_index_tab_click_returns_tab_kind() -> None:
    from client_surfaces.operator_tui.region_index import build_region_index
    from client_surfaces.operator_tui.tab_manager import open_or_activate_tab
    state = OperatorState(endpoint="http://localhost:5000")
    state = open_or_activate_tab(state, section_id="dashboard", kind="section", label="Dashboard")
    state = open_or_activate_tab(state, section_id="goals", kind="section", label="Goals")
    ri = build_region_index(state, width=120, height=32)
    # Tab bar at y=9 (body_start-1 = 10-1) when ≥2 tabs
    target = ri.get_target_at(2, 9)
    assert target is not None
    assert target.kind in {"tab", "tab_close"}



def test_region_index_body_shifts_with_two_tabs() -> None:
    from client_surfaces.operator_tui.region_index import build_region_index
    from client_surfaces.operator_tui.tab_manager import open_or_activate_tab
    state_no_tabs = OperatorState(endpoint="http://localhost:5000")
    # 1 tab: no tab bar, body_start=9
    state_one_tab = open_or_activate_tab(state_no_tabs, section_id="dashboard", kind="section", label="Dashboard")
    # 2 tabs: tab bar visible, body_start=10
    state_two_tabs = open_or_activate_tab(state_one_tab, section_id="goals", kind="section", label="Goals")

    ri_one = build_region_index(state_one_tab, width=120, height=32)
    ri_two = build_region_index(state_two_tabs, width=120, height=32)

    # With 1 tab: y=9 is nav body (no tab bar)
    t_no = ri_one.get_target_at(2, 9)
    assert t_no is not None and t_no.pane in {"nav", "header"}

    # With 2 tabs: y=9 is tab bar, y=10 is nav body
    t_tab = ri_two.get_target_at(2, 9)
    assert t_tab is not None and t_tab.kind in {"tab", "tab_close", "tab_scroll_left", "tab_scroll_right"}

    t_nav = ri_two.get_target_at(2, 10)
    assert t_nav is not None and t_nav.pane in {"nav", "header"}



def test_region_index_tab_close_target() -> None:
    from client_surfaces.operator_tui.region_index import build_region_index
    from client_surfaces.operator_tui.tab_manager import open_or_activate_tab, tab_positions_for_render
    state = OperatorState(endpoint="http://localhost:5000")
    state = open_or_activate_tab(state, section_id="dashboard", kind="section", label="Dashboard")
    state = open_or_activate_tab(state, section_id="goals", kind="section", label="Goals")
    positions = tab_positions_for_render(state, width=120, y=9)
    assert positions, "should have at least one tab position"
    tp = positions[0]
    ri = build_region_index(state, width=120, height=32)
    close_target = ri.get_target_at(tp.close_x, 9)
    assert close_target is not None
    assert close_target.kind == "tab_close"
    assert close_target.payload.get("tab_id") == tp.tab_id


# ── T19: Integration Keyboard/Mouse ─────────────────────────────────────────

