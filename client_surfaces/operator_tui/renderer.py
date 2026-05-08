from __future__ import annotations

from textwrap import shorten

from client_surfaces.operator_tui.diagrams import detect_diagram_blocks, render_diagram_fallback
from client_surfaces.operator_tui.keymap import bindings_for_mode
from client_surfaces.operator_tui.markdown_renderer import render_markdown_lines
from client_surfaces.operator_tui.models import FocusPane, OperatorState, PanelState
from client_surfaces.operator_tui.sections import SECTIONS, get_section
from client_surfaces.operator_tui.theme import DEFAULT_THEME, state_label, state_prefix


def render_operator_shell(state: OperatorState, *, width: int = 120, height: int = 32) -> str:
    width = max(72, int(width))
    height = max(18, int(height))
    left_width = 22
    detail_width = 34
    middle_width = width - left_width - detail_width - 6
    section = get_section(state.section_id)

    lines: list[str] = []
    lines.append(_clip(f"Ananta Operator TUI | {section.title} | mode={state.mode.value}", width))
    lines.append(_rule(width))

    nav_lines = _navigation_lines(state)
    content_lines = _content_lines(state, middle_width)
    detail_lines = _detail_lines(state, detail_width)
    body_height = height - 6
    for index in range(body_height):
        lines.append(
            " ".join(
                (
                    _cell(nav_lines, index, left_width),
                    "|",
                    _cell(content_lines, index, middle_width),
                    "|",
                    _cell(detail_lines, index, detail_width),
                )
            )
        )

    lines.append(_rule(width))
    lines.append(_status_line(state, width))
    lines.append(_command_line(state, width))
    if state.show_help or section.id == "help":
        lines.extend(_help_overlay(state, width))
    return "\n".join(_clip(line, width) for line in lines)


def _navigation_lines(state: OperatorState) -> list[str]:
    lines = [_pane_title("NAV", state.focus == FocusPane.NAVIGATION)]
    for section in SECTIONS:
        selected = DEFAULT_THEME.selected_prefix if section.id == state.section_id else DEFAULT_THEME.idle_prefix
        panel_state = (state.panel_states or {}).get(section.id)
        lines.append(f"{selected}{state_prefix(panel_state)} {section.title}")
    return lines


def _content_lines(state: OperatorState, width: int) -> list[str]:
    section = get_section(state.section_id)
    panel_state = (state.panel_states or {}).get(section.id, PanelState.LOADING)
    payload = (state.section_payloads or {}).get(section.id, {})
    lines = [_pane_title(section.title.upper(), state.focus == FocusPane.CONTENT)]
    lines.append(f"state={state_label(panel_state)}")
    lines.append(f"first_class={str(section.first_class).lower()}")
    lines.append(f"timeout_seconds={section.timeout_seconds:g}")
    lines.append(f"refresh_interval_seconds={section.refresh_interval_seconds:g}")
    lines.append("dependencies:")
    for dependency in section.primary_dependencies:
        lines.append(f"- {dependency}")
    lines.append("")
    lines.append("loading_policy=section_local")
    lines.append("render_policy=partial_first_paint")
    lines.append("mutation_policy=hub_dispatch_only")
    if payload:
        lines.append("")
        lines.append("payload:")
        for key in sorted(payload.keys()):
            value = payload[key]
            if isinstance(value, list):
                lines.append(f"- {key}=list[{len(value)}]")
            else:
                lines.append(f"- {key}={value}")
    if section.id == "dashboard":
        lines.append("")
        lines.append("summary:")
        lines.append("- hub health belongs to System")
        lines.append("- task and goal reads stay hub-owned")
        lines.append("- workers are never orchestrated here")
    elif section.id == "help":
        lines.append("")
        lines.extend(binding_line for binding_line in _binding_lines(state, width))
    if state.markdown_source and section.id in {"artifacts", "help"}:
        lines.append("")
        for block in detect_diagram_blocks(state.markdown_source):
            lines.extend(render_diagram_fallback(block, width=width))
            lines.append("")
        lines.append("markdown:")
        lines.extend(render_markdown_lines(state.markdown_source, width=width, max_lines=8))
    return lines


def _detail_lines(state: OperatorState, width: int) -> list[str]:
    section = get_section(state.section_id)
    panel_state = (state.panel_states or {}).get(section.id, PanelState.LOADING)
    lines = [_pane_title("DETAIL", state.focus == FocusPane.DETAIL)]
    lines.append(f"section={section.id}")
    lines.append(f"panel_state={state_label(panel_state)}")
    lines.append(f"fallback={section.fallback}")
    lines.append(f"selected_index={state.selected_index}")
    lines.append(f"refresh_count={state.refresh_count}")
    lines.append("")
    lines.append("commands:")
    lines.append(":section <id>")
    lines.append(":refresh")
    lines.append(":focus <pane>")
    lines.append(":help")
    lines.append(":sections")
    return [shorten(line, width=width, placeholder="...") for line in lines]


def _help_overlay(state: OperatorState, width: int) -> list[str]:
    lines = [_rule(width), "HELP"]
    lines.extend(_binding_lines(state, width))
    return lines


def _binding_lines(state: OperatorState, width: int) -> list[str]:
    lines = []
    for binding in bindings_for_mode(state.mode):
        lines.append(shorten(f"{binding.key:<7} {binding.action:<18} {binding.description}", width=width, placeholder="..."))
    return lines


def _pane_title(title: str, focused: bool) -> str:
    if focused:
        return f"{DEFAULT_THEME.focused_open}{title}{DEFAULT_THEME.focused_close}"
    return f" {title} "


def _cell(lines: list[str], index: int, width: int) -> str:
    value = lines[index] if index < len(lines) else ""
    return _clip(value, width).ljust(width)


def _status_line(state: OperatorState, width: int) -> str:
    value = (
        f"endpoint={state.endpoint} auth={state.auth_state} focus={state.focus.value} "
        f"mode={state.mode.value} status={state.status_message}"
    )
    return _clip(value, width)


def _command_line(state: OperatorState, width: int) -> str:
    prefix = ":" if state.mode.value == "command" else " "
    return _clip(f"{prefix}{state.command_line}", width)


def _rule(width: int) -> str:
    return "-" * width


def _clip(value: str, width: int) -> str:
    text = str(value)
    return text if len(text) <= width else text[: max(0, width - 3)] + "..."
