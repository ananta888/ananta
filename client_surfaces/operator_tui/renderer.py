from __future__ import annotations

import re
from textwrap import shorten
from typing import TYPE_CHECKING

_ANSI_STRIP = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

from client_surfaces.operator_tui.diagrams import detect_diagram_blocks, render_diagram_fallback
from client_surfaces.operator_tui.keymap import bindings_for_mode, hints_for_mode
from client_surfaces.operator_tui.markdown_renderer import render_markdown_lines
from client_surfaces.operator_tui.models import FocusPane, OperatorState, PanelState
from client_surfaces.operator_tui.read_models import build_goal_rows, build_inspection_detail, build_task_rows
from client_surfaces.operator_tui.sections import SECTIONS, get_section
from client_surfaces.operator_tui.theme import DEFAULT_THEME, state_label, state_prefix

if TYPE_CHECKING:
    from agent.cli.splash import SplashMachine, SplashState


def render_operator_shell(
    state: OperatorState,
    *,
    width: int = 120,
    height: int = 32,
    splash: SplashMachine | None = None,
) -> str:
    width = max(72, int(width))
    height = max(18, int(height))

    if splash is not None:
        splash_lines = _render_splash_header(splash, state, width=width)
        splash_state = splash.context.state.value if splash else ""
    else:
        splash_lines = []
        splash_state = ""

    splash_line_count = len(splash_lines)
    if splash_line_count > 0 and splash_state not in ("disabled", "skipped"):
        persistent_header: list[str] = []
        rule_line = _rule(width)
        body_offset = splash_line_count
    else:
        persistent_header = _render_persistent_header(state, width)
        rule_line = _rule(width)
        body_offset = len(persistent_header) + 1  # +1 for the rule

    left_width = 22
    detail_width = 34
    middle_width = width - left_width - detail_width - 6
    section = get_section(state.section_id)

    lines: list[str] = []
    lines.extend(splash_lines)
    if persistent_header:
        lines.extend(persistent_header)
        lines.append(rule_line)
    elif not splash_lines:
        lines.append(rule_line)

    nav_lines = _navigation_lines(state)
    content_lines = _content_lines(state, middle_width)
    detail_lines = _detail_lines(state, detail_width)
    body_height = height - 5 - body_offset
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
    lines.append(_status_line(state, width, splash_state=splash_state))
    lines.append(_command_line(state, width))
    lines.append(_hints_line(state, width))
    if state.show_help or section.id == "help":
        lines.extend(_help_overlay(state, width))
    return "\n".join(_clip(line, width) for line in lines)


def _render_persistent_header(state: OperatorState, width: int) -> list[str]:
    """Compact logo + live status shown permanently once splash is done."""
    from agent.cli.logo_layout import render_compact_header
    from agent.cli.status_snapshot import collect_status

    no_color = state.terminal_graphics.get("no_color", False) if state.terminal_graphics else False
    snapshot = collect_status(
        mode=state.mode.value,
        endpoint=state.endpoint,
        auth_state=state.auth_state,
        section=state.section_id,
    )
    return render_compact_header(snapshot, terminal_width=width, color=not no_color)


def _render_splash_header(splash: SplashMachine, state: OperatorState, width: int) -> list[str]:
    from agent.cli.splash import SplashState
    from agent.cli.status_snapshot import collect_status

    ctx = splash.context
    if ctx.state in (SplashState.DISABLED, SplashState.SKIPPED):
        return []

    snapshot = collect_status(
        mode=state.mode.value,
        endpoint=state.endpoint,
        auth_state=state.auth_state,
        section=state.section_id,
    )

    color = not state.terminal_graphics.get("no_color", False) if state.terminal_graphics else True

    return splash.render(snapshot, width=width, color=color)


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

    if panel_state == PanelState.LOADING:
        lines.append("  loading...")
        return lines
    if panel_state == PanelState.UNAUTHORIZED:
        lines.append("  ! access denied")
        lines.append("    export ANANTA_USER=admin")
        lines.append("    export ANANTA_PASSWORD=...")
        return lines
    if panel_state == PanelState.DEGRADED:
        lines.append(f"  ! degraded — {state.status_message or 'check system logs'}")
        lines.append("    press r to retry")
        return lines

    if section.id == "dashboard":
        lines.extend(_dashboard_content_lines(payload))
    elif section.id == "goals":
        items = payload.get("items") or []
        if not items:
            lines.append('  no goals — try: ananta plan "..."')
        else:
            for i, item in enumerate(items):
                marker = DEFAULT_THEME.selected_prefix if i == state.selected_index else " "
                lines.append(f"{marker} {item.get('id','?')}  [{item.get('status','?')}]  {item.get('title','')}")
    elif section.id == "tasks":
        items = payload.get("items") or []
        if not items:
            lines.append("  no tasks yet")
        else:
            for i, item in enumerate(items):
                marker = DEFAULT_THEME.selected_prefix if i == state.selected_index else " "
                lines.append(f"{marker} {item.get('id','?')}  [{item.get('status','?')}]  agent={item.get('agent','?')}  {item.get('title','')}")
        timeline = payload.get("timeline") or []
        if timeline:
            lines.append("")
            lines.append("  Timeline:")
            for entry in timeline[:3]:
                lines.append(f"    {entry.get('id','?')}  {entry.get('summary','')}")
    elif section.id == "system":
        lines.extend(_system_content_lines(payload))
    elif section.id == "terminal":
        lines.extend(_terminal_content_lines(payload, state, width))
    elif section.id == "help":
        lines.append("")
        lines.extend(_binding_lines(state, width))
    else:
        items = payload.get("items") or []
        if panel_state == PanelState.EMPTY or not items:
            lines.append("  (empty)")
            lines.append("  press r to refresh")
        else:
            for i, item in enumerate(items[:20]):
                marker = DEFAULT_THEME.selected_prefix if i == state.selected_index else " "
                label = item.get("title") or item.get("id") or str(item)
                lines.append(f"{marker} {label}")

    if state.markdown_source and section.id in {"artifacts", "help"}:
        lines.append("")
        for block in detect_diagram_blocks(state.markdown_source):
            lines.extend(render_diagram_fallback(block, width=width))
            lines.append("")
        lines.append("markdown:")
        lines.extend(render_markdown_lines(state.markdown_source, width=width, max_lines=8))

    return lines


def _dashboard_content_lines(payload: dict) -> list[str]:
    lines = []
    agents = payload.get("agents") or {}
    llm = payload.get("llm_providers") or {}
    queue = payload.get("queue") or {}
    goal_summary = payload.get("goal_summary") or payload.get("goals") or {}
    task_summary = payload.get("task_summary") or payload.get("tasks") or {}

    lines.append("  System")
    if agents:
        online = agents.get("online", "?")
        total = agents.get("total", "?")
        lines.append(f"    Agents  {online}/{total} online")
    if llm:
        for provider, status in llm.items():
            lines.append(f"    {provider:<10} {status}")
    if queue:
        depth = queue.get("depth", 0)
        lines.append(f"    Queue   {depth} tasks pending")
    if not agents and not llm and not queue:
        lines.append("    go to System for health info")

    if goal_summary or task_summary:
        lines.append("")
        lines.append("  Overview")
        if goal_summary:
            lines.append(f"    Goals:  {goal_summary}")
        if task_summary:
            lines.append(f"    Tasks:  {task_summary}")
    else:
        lines.append("")
        lines.append("  go to Goals or Tasks for details")

    return lines


def _system_content_lines(payload: dict) -> list[str]:
    lines = []
    agents = payload.get("agents") or {}
    llm = payload.get("llm_providers") or {}
    queue = payload.get("queue") or {}
    contracts = payload.get("contracts") or []

    if agents:
        online = agents.get("online", "?")
        total = agents.get("total", "?")
        lines.append(f"  Agents:    {online}/{total} online")
    if llm:
        for provider, status in llm.items():
            lines.append(f"  {provider:<12} {status}")
    if queue:
        depth = queue.get("depth", 0)
        counts = queue.get("counts") or {}
        lines.append(f"  Queue:     {depth} pending")
        if counts:
            parts = [f"{k}={v}" for k, v in counts.items() if v]
            if parts:
                lines.append(f"             {' '.join(parts)}")
    if contracts:
        lines.append("")
        lines.append("  Contracts:")
        for c in contracts[:5]:
            lines.append(f"    {c}")
    if not lines:
        lines.append("  press r to load system data")

    return lines


def _terminal_content_lines(payload: dict, state: OperatorState, width: int) -> list[str]:
    lines: list[str] = []
    targets = payload.get("targets") or []
    sessions = payload.get("sessions") or []

    lines.append("  Targets:")
    if not targets:
        lines.append("    no targets available (terminal feature disabled?)")
    else:
        for i, t in enumerate(targets):
            marker = DEFAULT_THEME.selected_prefix if i == state.selected_index else " "
            ttype = t.get("target_type", "?")
            tid = t.get("target_id", "?")
            risk = " [HIGH RISK]" if ttype in {"hub", "hub_as_worker"} else ""
            lines.append(f"{marker} {ttype:<16} {tid}{risk}")

    lines.append("")
    lines.append("  Sessions:")
    if not sessions:
        lines.append("    no active sessions")
    else:
        for s in sessions:
            sid = (s.get("id") or "?")[:16] + "…"
            stype = s.get("target_type", "?")
            status = s.get("status", "?")
            ro = " [ro]" if s.get("read_only") else ""
            lines.append(f"  {sid} {stype:<14} {status}{ro}")

    lines.append("")
    lines.append("  Commands:")
    lines.append("    :tmux targets    list targets")
    lines.append("    :tmux start      create session")
    lines.append("    :tmux attach <id> attach")
    lines.append("    :tmux kill <id>  kill session")
    return lines


def _detail_lines(state: OperatorState, width: int) -> list[str]:
    section = get_section(state.section_id)
    lines = [_pane_title("DETAIL", state.focus == FocusPane.DETAIL)]

    if state.mode.value == "inspect":
        lines.append("")
        lines.append("  inspect:")
        lines.extend(
            f"    {l}"
            for l in build_inspection_detail(
                section.id, (state.section_payloads or {}).get(section.id, {}), state.selected_index
            )
        )

    if state.pending_action:
        lines.append("")
        lines.append("  ! Pending action:")
        lines.append(f"    {state.pending_action.get('name')}")
        lines.append(f"    risk={state.pending_action.get('risk')}")
        lines.append("    :confirm  to execute")
        lines.append("    :cancel   to abort")

    if state.audit_context:
        lines.append("")
        lines.append("  Audit:")
        lines.append(f"    intent={state.audit_context.get('intent')}")
        lines.append(f"    action={state.audit_context.get('action')}")

    if state.browser_fallback_url:
        lines.append("")
        lines.append(f"browser={state.browser_fallback_url}")

    lines.append("")
    lines.append("  Commands:")
    lines.append("    :section <id>   switch section")
    lines.append("    :refresh        reload data")
    lines.append("    :focus <pane>   nav/content/detail")
    lines.append("    :help           keybindings")
    if section.id in {"goals", "tasks"}:
        lines.append("    :inspect        show selected")
        lines.append("    :action <n> <r> dispatch action")

    return [_clip(line, width) for line in lines]


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


def _status_line(state: OperatorState, width: int, splash_state: str = "") -> str:
    parts = [
        f"endpoint={state.endpoint}",
        f"auth={state.auth_state}",
        f"focus={state.focus.value}",
        f"mode={state.mode.value}",
        f"status={state.status_message}",
    ]
    if splash_state:
        parts.append(f"splash={splash_state}")
    return _clip(" ".join(parts), width)


def _command_line(state: OperatorState, width: int) -> str:
    prefix = ":" if state.mode.value == "command" else " "
    return _clip(f"{prefix}{state.command_line}", width)


def _hints_line(state: OperatorState, width: int) -> str:
    return _clip(hints_for_mode(state.mode), width)


def _rule(width: int) -> str:
    return "-" * width


def _clip(value: str, width: int) -> str:
    text = _ANSI_STRIP.sub("", str(value))
    return text if len(text) <= width else text[: max(0, width - 3)] + "..."
