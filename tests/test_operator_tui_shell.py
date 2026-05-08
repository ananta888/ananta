from __future__ import annotations

from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import FocusPane, OperatorMode, OperatorState
from client_surfaces.operator_tui.renderer import render_operator_shell
from client_surfaces.operator_tui.sections import move_section, normalize_section_id


def test_operator_tui_renders_first_paint_shell() -> None:
    state = OperatorState(endpoint="http://localhost:5000", auth_state="session_env")

    output = render_operator_shell(state, width=96, height=22)

    assert "Ananta Operator TUI" in output
    assert "Dashboard" in output
    assert "endpoint=http://localhost:5000" in output
    assert "mutation_policy=hub_dispatch_only" in output


def test_operator_tui_section_commands_update_state() -> None:
    state = OperatorState(endpoint="http://localhost:5000")

    result = execute_command(":section Tasks", state)

    assert result.handled is True
    assert result.state.section_id == "tasks"
    assert result.state.mode is OperatorMode.NORMAL


def test_operator_tui_unknown_command_is_visible() -> None:
    state = OperatorState(endpoint="http://localhost:5000")

    result = execute_command(":explode", state)

    assert result.handled is False
    assert "unknown command" in result.message
    assert "unknown command" in result.state.status_message


def test_operator_tui_focus_command_is_typed() -> None:
    state = OperatorState(endpoint="http://localhost:5000")

    result = execute_command(":focus detail", state)

    assert result.handled is True
    assert result.state.focus is FocusPane.DETAIL


def test_operator_tui_section_aliases_and_navigation() -> None:
    assert normalize_section_id("task") == "tasks"
    assert normalize_section_id("?") == "help"
    assert move_section("dashboard", 1) == "goals"
