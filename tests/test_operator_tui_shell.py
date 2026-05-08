from __future__ import annotations

from argparse import Namespace

from client_surfaces.operator_tui.adapters import SectionAdapterRegistry
from client_surfaces.operator_tui.app import build_initial_state, load_active_section
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.diagrams import detect_diagram_blocks, render_diagram_fallback
from client_surfaces.operator_tui.markdown_renderer import render_markdown_lines
from client_surfaces.operator_tui.models import FocusPane, OperatorMode, OperatorState, PanelState, SectionLoadResult
from client_surfaces.operator_tui.refresh import refresh_policy_for, should_refresh
from client_surfaces.operator_tui.renderer import render_operator_shell
from client_surfaces.operator_tui.sections import move_section, normalize_section_id


def test_operator_tui_renders_first_paint_shell() -> None:
    state = load_active_section(OperatorState(endpoint="http://localhost:5000", auth_state="session_env"))

    output = render_operator_shell(state, width=96, height=22)

    assert "Ananta Operator TUI" in output
    assert "Dashboard" in output
    assert "endpoint=http://localhost:5000" in output
    assert "mutation_policy=hub_dispatch_only" in output
    assert "state=ok" in output


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
