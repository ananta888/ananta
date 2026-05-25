from __future__ import annotations

from argparse import Namespace

from client_surfaces.operator_tui.adapters import SectionAdapterRegistry
from client_surfaces.operator_tui.app import build_initial_state, load_active_section
from client_surfaces.operator_tui.actions import dispatch_action, parse_action
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
from client_surfaces.operator_tui.renderer import render_operator_shell
from client_surfaces.operator_tui.rollout import operator_tui_enabled, rollback_hint, rollout_stage
from client_surfaces.operator_tui.sections import move_section, normalize_section_id
from client_surfaces.operator_tui.smoke import run_fixture_smoke
from agent.cli.main import _run_tui


def test_operator_tui_renders_first_paint_shell() -> None:
    state = load_active_section(OperatorState(endpoint="http://localhost:5000", auth_state="session_env"))

    output = render_operator_shell(state, width=96, height=22)

    assert "Ananta Operator TUI" in output
    assert "Dashboard" in output
    assert "endpoint=http://localhost:5000" in output
    assert "Commands:" in output
    assert ":refresh" in output


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


def test_operator_tui_adapter_maps_timeout_to_local_degraded_state() -> None:
    def loader(section_id: str) -> SectionLoadResult:
        raise TimeoutError(f"{section_id} timed out")

    result = SectionAdapterRegistry(loader).load("tasks")

    assert result.section_id == "tasks"
    assert result.state is PanelState.DEGRADED
    assert "timed out" in result.message


def test_operator_tui_refresh_policy_is_section_local() -> None:
    policy = refresh_policy_for("system")

    assert policy.timeout_seconds == 1.0
    assert should_refresh(elapsed_seconds=policy.refresh_interval_seconds, policy=policy)
    assert should_refresh(elapsed_seconds=0, policy=policy, force=True)


def test_operator_tui_markdown_renderer_handles_common_blocks() -> None:
    lines = render_markdown_lines("# Title\n- item\n```python\nprint('x')\n```", width=40)

    assert "# Title" in lines
    assert "- item" in lines
    assert "CODE" in lines
    assert "  print('x')" in lines


def test_operator_tui_detects_and_renders_mermaid_fallback() -> None:
    blocks = detect_diagram_blocks("```mermaid\ngraph TD\nA-->B\n```")

    assert len(blocks) == 1
    assert blocks[0].kind == "mermaid"
    assert any("A -> B" in line for line in render_diagram_fallback(blocks[0]))


def test_operator_tui_detects_and_renders_plantuml_fallback() -> None:
    blocks = detect_diagram_blocks("@startuml\nAlice -> Bob\n@enduml")

    assert len(blocks) == 1
    assert blocks[0].kind == "plantuml"
    assert any("Alice -> Bob" in line for line in render_diagram_fallback(blocks[0]))


def test_operator_tui_initial_state_carries_markdown_source() -> None:
    args = Namespace(
        base_url="http://localhost:5000",
        section="artifacts",
        mode="normal",
        focus="content",
        show_help=False,
        markdown_source="# Artifact\n```mermaid\ngraph TD\nA-->B\n```",
    )

    state = load_active_section(build_initial_state(args))
    output = render_operator_shell(state, width=100, height=40)

    assert "markdown:" in output
    assert "mermaid diagram preview" in output


def test_operator_tui_detects_terminal_graphics_capabilities() -> None:
    decision = graphics_decision({"KITTY_WINDOW_ID": "1"})

    assert decision["supported"] is True
    assert "kitty" in decision["protocols"]


def test_operator_tui_read_only_goal_and_task_rows() -> None:
    goals = build_goal_rows({"items": [{"id": "G-1", "status": "todo", "title": "Goal"}]})
    tasks = build_task_rows({"items": [{"id": "T-1", "status": "todo", "agent": "alpha", "title": "Task"}]})

    assert "G-1 [todo] Goal" in goals
    assert "T-1 [todo] agent=alpha Task" in tasks


def test_operator_tui_action_dispatch_requires_confirmation_for_risky_actions() -> None:
    action = parse_action("task_execute", risk="high")

    result = dispatch_action(action)
    confirmed = dispatch_action(action, confirmed=True)

    assert result.pending_action == action
    assert "confirmation required" in result.message
    assert confirmed.accepted is True
    assert confirmed.audit_context["intent"] == "mutation_request"


def test_operator_tui_commands_manage_pending_action_and_cancel() -> None:
    state = OperatorState(endpoint="http://localhost:5000")

    pending = execute_command(":action task_execute high", state)
    confirmed = execute_command(":confirm", pending.state)
    cancelled = execute_command(":cancel", pending.state)

    assert pending.state.pending_action is not None
    assert confirmed.state.pending_action is None
    assert cancelled.state.pending_action is None
    assert cancelled.state.mode is OperatorMode.NORMAL


def test_operator_tui_browser_fallback_url_is_section_aware() -> None:
    assert browser_fallback_url("http://localhost:5000", "tasks", "T-1") == "http://localhost:5000/tasks?target=T-1"


def test_operator_tui_fixture_smoke_detects_first_paint() -> None:
    args = Namespace(
        base_url="http://localhost:5000",
        section="dashboard",
        mode="normal",
        focus="navigation",
        show_help=False,
        markdown_source="",
    )

    result = run_fixture_smoke(args)

    assert result.ok is True
    assert "first_paint" in result.checks


def test_operator_tui_performance_measurement_reports_budget() -> None:
    result = measure("noop", 100.0, lambda: "ok")

    assert result.name == "noop"
    assert result.ok is True


def test_operator_tui_rollout_controls_are_explicit() -> None:
    assert operator_tui_enabled({"ANANTA_OPERATOR_TUI_ENABLED": "0"}) is False
    assert rollout_stage({"ANANTA_OPERATOR_TUI_STAGE": "advanced_opt_in"}) == "advanced_opt_in"
    assert "legacy" in rollback_hint()


def test_ananta_tui_default_uses_operator_render_once(capsys) -> None:
    exit_code = _run_tui(["--render-once", "--skip-splash", "--section", "tasks", "--width", "90", "--height", "20"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Ananta Operator TUI" in captured.out


def test_operator_tui_inspect_and_browser_commands_render_context() -> None:
    state = load_active_section(OperatorState(endpoint="http://localhost:5000", section_id="tasks"))
    state = execute_command(":inspect", state).state
    state = execute_command(":browser TUI-T26", state).state
    output = render_operator_shell(state, width=110, height=48)

    assert "inspect:" in output
    assert "browser=http://localhost:5000/t" in output


def test_tab_focus_header_activates_logo_snake_controls() -> None:
    state = OperatorState(endpoint="http://localhost:5000", focus=FocusPane.NAVIGATION)
    tui = InteractiveOperatorTui(state)

    tui._move_focus(-1)  # NAV -> HEADER

    game = tui.state.header_logo_game or {}
    assert tui.state.focus is FocusPane.HEADER
    assert game.get("active") is True
    assert isinstance(game.get("snake"), list)
    assert tui._try_header_snake_direction((0, -1)) is True


def test_header_focus_hints_show_snake_controls() -> None:
    game = {
        "active": True,
        "alive": True,
        "board_w": 18,
        "board_h": 6,
        "snake": [(4, 3), (3, 3), (2, 3)],
        "direction": (1, 0),
        "next_direction": (1, 0),
        "food": (8, 3),
        "score": 2,
        "moves": 5,
        "last_move": 0.0,
    }
    state = OperatorState(
        endpoint="http://localhost:5000",
        focus=FocusPane.HEADER,
        header_logo_game=game,
    )

    output = render_operator_shell(state, width=100, height=24)

    assert "Snake  score=2  running" in output
    assert "[←→↑↓] Snake" in output
