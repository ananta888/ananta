from __future__ import annotations

from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import OperatorState
from client_surfaces.operator_tui.renderer import render_operator_shell


def _state() -> OperatorState:
    return OperatorState(endpoint="http://localhost:5000", section_id="artifacts", header_logo_game={})


def test_diff3_renderer_three_columns_for_wide_layout() -> None:
    state = execute_command(":diff3", _state()).state
    state = execute_command(":diff3 panel A current", state).state
    state = execute_command(":diff3 panel B current --mode summary", state).state
    state = execute_command(":diff3 panel C ai review", state).state
    output = render_operator_shell(state, width=176, height=34)
    assert "DIFF3: active panel=" in output
    assert "[A] Current Diff" in output
    assert "[B] Current Diff" in output
    assert "[C] ai_review" in output


def test_diff3_renderer_tabbed_mode_for_small_width() -> None:
    state = execute_command(":diff3", _state()).state
    state = execute_command(":diff3 focus B", state).state
    output = render_operator_shell(state, width=110, height=32)
    assert "tabbed mode" in output
    assert "active panel=B" in output


def test_diff3_renderer_shows_active_panel_and_headers() -> None:
    state = execute_command(":diff3", _state()).state
    state = execute_command(":diff3 panel A current", state).state
    state = execute_command(":diff3 panel B current --mode summary", state).state
    state = execute_command(":diff3 panel C ai review", state).state
    state = execute_command(":diff3 focus C", state).state
    output = render_operator_shell(state, width=176, height=34)
    assert "active panel=C" in output
    assert "Current Diff" in output
    assert "ai_review" in output
