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


def test_snake_mode_toggle_enables_and_disables_frame_mode() -> None:
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER)
    tui = InteractiveOperatorTui(state)

    tui._toggle_snake_mode()
    on_game = tui.state.header_logo_game or {}
    assert on_game.get("active") is True
    assert on_game.get("ui_steering") is True
    assert on_game.get("free_mode") is True

    tui._toggle_snake_mode()
    off_game = tui.state.header_logo_game or {}
    # Exiting snake mode restores the ambient AI snake (active=tutorial_default env)
    assert off_game.get("ui_steering") is False
    assert off_game.get("free_mode") is False
    assert off_game.get("tutorial_mode") is not None  # restored to env default



def test_snake_access_command_updates_remote_permission_levels() -> None:
    state = OperatorState(
        endpoint="http://localhost:5000",
        header_logo_game={
            "local_snake_id": "s1",
            "snakes": {
                "s1": {"id": "s1", "pseudonym": "alice", "snake_color": "mint"},
                "s2": {"id": "s2", "pseudonym": "bob", "snake_color": "violet"},
            },
        },
    )

    result = execute_command(":snake-access s2 full", state)
    game = result.state.header_logo_game or {}
    access = dict(game.get("remote_access") or {})
    snakes = dict(game.get("snakes") or {})

    assert result.handled is True
    assert access.get("s2") == "full"
    assert dict(snakes.get("s2") or {}).get("access_level") == "full"



def test_snake_message_style_and_color_can_cycle() -> None:
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER)
    tui = InteractiveOperatorTui(state)
    tui._toggle_snake_mode()

    before = dict(tui.state.header_logo_game or {})
    tui._snake_cycle_message_style()
    tui._snake_cycle_color()
    after = dict(tui.state.header_logo_game or {})

    assert before.get("message_style") != after.get("message_style")
    assert before.get("snake_color") != after.get("snake_color")



def test_snake_mode_does_not_auto_switch_focus_or_section() -> None:
    game = {
        "active": True,
        "alive": True,
        "board_w": 18,
        "board_h": 6,
        "snake": [(17, 2), (16, 2), (15, 2)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "food": (3, 3),
        "score": 0,
        "moves": 0,
        "last_move": 0.0,
    }
    state = OperatorState(
        endpoint="http://localhost:5000",
        focus=FocusPane.HEADER,
        header_logo_game=game,
    )
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.HEADER, section_id="dashboard")

    tui._tick_header_snake()

    assert tui.state.focus is FocusPane.HEADER
    assert tui.state.section_id == "dashboard"
    assert (tui.state.header_logo_game or {}).get("active") is True



def test_snake_tick_keeps_manual_ui_state_stable() -> None:
    game = {
        "active": True,
        "alive": True,
        "board_w": 18,
        "board_h": 6,
        "snake": [(17, 2), (16, 2), (15, 2)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "food": (3, 3),
        "score": 0,
        "moves": 0,
        "last_move": 0.0,
    }
    state = OperatorState(
        endpoint="http://localhost:5000",
        focus=FocusPane.HEADER,
        header_logo_game=game,
    )
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.NAVIGATION, section_id="tasks", selected_index=3)
    tui._tick_header_snake()
    assert tui.state.focus is FocusPane.NAVIGATION
    assert tui.state.section_id == "tasks"
    assert tui.state.selected_index == 3



def test_snake_does_not_switch_to_detail_by_position() -> None:
    game = {
        "active": True,
        "alive": True,
        "board_w": 18,
        "board_h": 6,
        "snake": [(4, 3), (3, 3), (2, 3)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "food": (3, 3),
        "score": 0,
        "moves": 1,
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.HEADER, section_id="dashboard")

    tui._tick_header_snake()

    assert tui.state.focus is FocusPane.HEADER
    assert tui.state.section_id == "dashboard"



def test_snake_remains_drivable_after_escape_outside_header_focus() -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "board_w": 18,
        "board_h": 6,
        "snake": [(17, 2), (16, 2), (15, 2)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "food": (3, 3),
        "score": 0,
        "moves": 0,
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.NAVIGATION, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.NAVIGATION)

    assert tui._try_header_snake_direction((0, 1)) is True
    assert (tui.state.header_logo_game or {}).get("next_direction") == (0, 1)



def test_snake_wraps_at_screen_border_and_stays_alive() -> None:
    head = (119, 2)
    game = {
        "active": True,
        "alive": True,
        "board_w": 18,
        "board_h": 6,
        "snake": [head, (max(0, head[0] - 1), head[1]), (max(0, head[0] - 2), head[1])],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "food": (3, 3),
        "boxes": [],
        "score": 0,
        "moves": 1,
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.HEADER)

    tui._tick_header_snake()

    snake = (tui.state.header_logo_game or {}).get("snake") or []
    assert (tui.state.header_logo_game or {}).get("alive") is True
    assert snake and snake[0][0] != 119



def test_snake_no_longer_selects_sections_from_screen_regions() -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "board_w": 18,
        "board_h": 6,
        "snake": [(4, 3), (3, 3), (2, 3)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "food": (3, 3),
        "score": 0,
        "moves": 0,
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.HEADER, section_id="dashboard")

    tui._tick_header_snake()

    assert tui.state.section_id == "dashboard"
    assert tui.state.focus is FocusPane.HEADER



def test_snake_message_can_be_saved_to_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "message_mode": True,
        "message_draft": "Hallo Snake",
        "board_w": 18,
        "board_h": 6,
        "snake": [(6, 3), (5, 3), (4, 3)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game)

    tui._snake_commit_message()

    cfg = Path(tmp_path) / ".config" / "ananta" / "snake-config.json"
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["snake_message"] == "Hallo Snake"
    assert data["tutorial_user_feed"] == "Hallo Snake"
    assert (tui.state.header_logo_game or {}).get("message") == "Hallo Snake"
    assert (tui.state.header_logo_game or {}).get("tutorial_user_feed") == "Hallo Snake"



def test_snake_message_template_command_updates_prompt_template(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "message_mode": True,
        "message_draft": "/template Explain zone={contact_zone} using feed={user_feed}.",
        "board_w": 18,
        "board_h": 6,
        "snake": [(6, 3), (5, 3), (4, 3)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game)

    tui._snake_commit_message()

    g = tui.state.header_logo_game or {}
    assert "Explain zone={contact_zone}" in str(g.get("tutorial_prompt_template") or "")
    assert "template set" in str(g.get("message") or "")



def test_snake_message_mode_typing_and_backspace() -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "board_w": 18,
        "board_h": 6,
        "snake": [(6, 3), (5, 3), (4, 3)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game)

    tui._toggle_snake_message_mode()
    tui._snake_message_append("A")
    tui._snake_message_append("B")
    tui._snake_message_backspace()

    g = tui.state.header_logo_game or {}
    assert g.get("message_mode") is True
    assert g.get("message_draft") == "A"



def test_snake_message_mode_accepts_command_bound_letters() -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "message_mode": True,
        "message_draft": "",
        "board_w": 18,
        "board_h": 6,
        "snake": [(6, 3), (5, 3), (4, 3)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game)

    tui._normal_or_text("e", lambda: None)
    tui._normal_or_text("m", lambda: None)

    g = tui.state.header_logo_game or {}
    assert g.get("message_mode") is True
    assert g.get("message_draft") == "em"



def test_fullscreen_snake_overlay_renders_message_tail_and_text_marking(monkeypatch) -> None:
    monkeypatch.setattr("client_surfaces.operator_tui.renderer.time.monotonic", lambda: 0.0)
    lines = ["abcdefghij" + " " * 30] + [" " * 40] * 19
    game = {
        "active": True,
        "free_mode": True,
        "snake": [(1, 0), (0, 0)],
        "trail_path": [(1, 0), (0, 0), (2, 0), (3, 0), (4, 0)],
        "mark_cells": [(5, 0, 8)],
        "message": "HI",
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    out = _overlay_fullscreen_snake(lines, state, width=40)
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", out[0])

    assert plain[2] == "H"
    assert plain[3] == "I"
    assert plain[5] == "f"



def test_snake_message_effect_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.setattr("client_surfaces.operator_tui.renderer.time.monotonic", lambda: 0.0)
    lines = [" " * 40] * 20
    game = {
        "active": True,
        "free_mode": True,
        "snake": [(1, 0), (0, 0)],
        "trail_path": [(1, 0), (0, 0), (2, 0), (3, 0)],
        "message": "SHOULD_NOT_RENDER",
        "message_style": "ticker",
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    out = _overlay_fullscreen_snake(lines, state, width=40)
    plain = "\n".join(re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", row) for row in out)

    assert "SHOULD_NOT_RENDER" not in plain



def test_fullscreen_overlay_renders_peer_snake_from_multi_snake_state(monkeypatch) -> None:
    monkeypatch.setattr("client_surfaces.operator_tui.renderer.time.monotonic", lambda: 0.0)
    lines = [" " * 20 for _ in range(5)]
    game = {
        "active": True,
        "free_mode": True,
        "local_snake_id": "s1",
        "snake": [(1, 1)],
        "trail_path": [(1, 1)],
        "snakes": {
            "s1": {
                "id": "s1",
                "snake": [(1, 1)],
                "trail_path": [(1, 1)],
                "message": "",
                "snake_color": "mint",
                "message_style": "trail",
            },
            "s2": {
                "id": "s2",
                "snake": [(5, 1), (4, 1)],
                "trail_path": [(5, 1), (4, 1), (3, 1)],
                "selection_cells": [(7, 1)],
                "message": "peer",
                "snake_color": "violet",
                "message_style": "trail",
            },
        },
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)
    out = _overlay_fullscreen_snake(lines, state, width=20)
    plain_row = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", out[1])

    assert plain_row[5] != " "
    assert "\x1b[48;2;212;176;255m" in out[1]



def test_snake_tick_populates_local_snapshot_for_collab_state() -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "free_mode": True,
        "local_snake_id": "s1",
        "snake": [(6, 3), (5, 3), (4, 3)],
        "trail_path": [(6, 3), (5, 3), (4, 3)],
        "mark_cells": [],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "vel_x": 0.0,
        "vel_y": 0.0,
        "accum_x": 0.0,
        "accum_y": 0.0,
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, focus=FocusPane.HEADER)

    tui._tick_header_snake()

    snakes = ((tui.state.header_logo_game or {}).get("snakes") or {})
    local = snakes.get("s1") if isinstance(snakes, dict) else None
    assert isinstance(local, dict)
    assert local.get("local") is True



def test_fullscreen_snake_overlay_preserves_header_and_footer_rows() -> None:
    game = {
        "active": True,
        "free_mode": True,
        "local_snake_id": "s1",
        "snake": [(118, 0), (117, 0), (116, 0)],
        "trail_path": [(118, 0), (117, 0), (116, 0)],
        "chat_panel_open": True,
    }
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game=game)

    output = render_operator_shell(state, width=120, height=32)
    plain_lines = [re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line) for line in output.splitlines()]

    assert "focus=" in plain_lines[-3]
    assert plain_lines[-1].startswith("[Ctrl+W]")
    assert "ACTIVE:" not in "\n".join(plain_lines[:8])



def test_snake_copy_selection_moves_text_to_clipboard_and_message(monkeypatch) -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "free_mode": True,
        "snake": [(2, 3), (1, 3), (0, 3)],
        "selection_cells": [(1, 1), (2, 1), (3, 1)],
        "board_w": 30,
        "board_h": 12,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game)
    monkeypatch.setattr(tui, "_snake_render_plain_lines", lambda: ["", "abcde", "", "", ""])

    tui._snake_copy_selection()

    g = tui.state.header_logo_game or {}
    assert g.get("clipboard") == "bcd"
    assert g.get("message") == "bcd"



def test_snake_copy_preserves_newlines_unchanged(monkeypatch) -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "free_mode": True,
        "snake": [(2, 3), (1, 3), (0, 3)],
        "selection_cells": [(0, 0), (1, 0), (0, 1), (1, 1)],
        "board_w": 30,
        "board_h": 12,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game)
    monkeypatch.setattr(tui, "_snake_render_plain_lines", lambda: ["ab", "cd", "", "", ""])

    tui._snake_copy_selection()

    copied = str((tui.state.header_logo_game or {}).get("clipboard") or "")
    assert copied == "ab\ncd"



def test_snake_frame_mode_collects_multiple_regions_and_copy(monkeypatch) -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "free_mode": True,
        "snake": [(2, 1), (1, 1), (0, 1)],
        "selection_cells": [],
        "selection_regions": [],
        "board_w": 30,
        "board_h": 12,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game)

    tui._snake_toggle_frame_mode()  # anchor at (2,1)
    g = dict(tui.state.header_logo_game or {})
    g["snake"] = [(4, 2), (3, 2), (2, 2)]
    tui.state = tui.state.with_updates(header_logo_game=g)
    tui._snake_toggle_selection()  # first frame

    g = dict(tui.state.header_logo_game or {})
    g["snake"] = [(8, 2), (7, 2), (6, 2)]
    tui.state = tui.state.with_updates(header_logo_game=g)
    tui._snake_toggle_selection()  # second frame

    g = tui.state.header_logo_game or {}
    regions = g.get("selection_regions") or []
    assert len(regions) == 2
    assert len(g.get("selection_cells") or []) > 0

    monkeypatch.setattr(tui, "_snake_render_plain_lines", lambda: ["0123456789", "abcdefghij", "klmnopqrst", "", ""])
    tui._snake_copy_selection()
    copied = (tui.state.header_logo_game or {}).get("clipboard") or ""
    assert copied



def test_snake_clear_visual_marks_resets_all_selection_state() -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "free_mode": True,
        "snake": [(2, 1), (1, 1), (0, 1)],
        "mark_cells": [(2, 1, 4)],
        "selection_anchor": (2, 1),
        "selection_cells": [(1, 1), (2, 1)],
        "selection_regions": [(1, 1, 2, 2)],
        "selection_frame_mode": True,
        "selection_frame_anchor": (1, 1),
        "snakes": {
            "s1": {"selection_cells": [(1, 1)], "mark_cells": [(1, 1, 2)], "selection_regions": [(1, 1, 1, 1)]},
            "s2": {"selection_cells": [(3, 3)], "mark_cells": [(3, 3, 2)], "selection_regions": [(3, 3, 3, 3)]},
        },
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game)

    tui._snake_clear_visual_marks()

    g = tui.state.header_logo_game or {}
    assert g.get("mark_cells") == []
    assert g.get("selection_cells") == []
    assert g.get("selection_regions") == []
    assert g.get("selection_frame_mode") is False
    snakes = g.get("snakes") or {}
    assert isinstance(snakes, dict)
    assert (snakes.get("s1") or {}).get("selection_cells") == []
    assert (snakes.get("s2") or {}).get("mark_cells") == []



def test_snake_replace_selection_only_in_command_line(monkeypatch) -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "free_mode": True,
        "snake": [(2, 3), (1, 3), (0, 3)],
        "selection_cells": [(2, 3), (3, 3)],
        "message": "ZZ",
        "board_w": 40,
        "board_h": 12,
    }
    state = OperatorState(
        endpoint="http://localhost:5000",
        focus=FocusPane.HEADER,
        mode=OperatorMode.COMMAND,
        command_line="abcdef",
        header_logo_game=game,
    )
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game, mode=OperatorMode.COMMAND, command_line="abcdef")
    monkeypatch.setattr(tui, "_snake_render_plain_lines", lambda: ["line0", "line1", "line2", ":abcdef", "line4"])

    tui._snake_replace_selection()

    assert tui.state.command_line == "aZZdef"



def test_snake_immediate_brake_sets_velocity_zero() -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "vel_x": 14.0,
        "vel_y": -3.0,
        "accum_x": 0.4,
        "accum_y": 0.8,
        "board_w": 18,
        "board_h": 6,
        "snake": [(6, 3), (5, 3), (4, 3)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    tui.state = tui.state.with_updates(header_logo_game=game)

    tui._snake_immediate_brake()

    g = tui.state.header_logo_game or {}
    assert g.get("vel_x") == 0.0
    assert g.get("vel_y") == 0.0
    assert g.get("accum_x") == 0.0
    assert g.get("accum_y") == 0.0



def test_snake_hover_selection_uses_delay_before_selecting_nav() -> None:
    game = {
        "active": True,
        "alive": True,
        "ui_steering": True,
        "board_w": 120,
        "board_h": 31,
        "snake": [(2, 12), (1, 12), (0, 12)],
        "direction": (0, 1),
        "next_direction": (0, 1),
        "last_move": 0.0,
    }
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.HEADER, header_logo_game=game)
    tui = InteractiveOperatorTui(state)
    base = tui.state.with_updates(header_logo_game=game, focus=FocusPane.HEADER)

    s1 = tui._apply_snake_hover_selection_delay(base, head=(2, 12), now=10.0)
    s2 = tui._apply_snake_hover_selection_delay(s1, head=(2, 12), now=10.2)
    s3 = tui._apply_snake_hover_selection_delay(s2, head=(2, 12), now=10.8)

    assert s1.focus is FocusPane.HEADER
    assert s2.focus is FocusPane.HEADER
    assert s3.focus is FocusPane.NAVIGATION


