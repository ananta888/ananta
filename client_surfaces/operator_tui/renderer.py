from __future__ import annotations

import os
import re
import time
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
    if splash_line_count > 0 and splash_state in ("fullscreen", "transition"):
        lines = splash_lines[:height]
        while len(lines) < height:
            lines.append("")
        return "\n".join(_clip(line, width) for line in lines)

    header_focused = state.focus == FocusPane.HEADER

    if splash_line_count > 0 and splash_state not in ("disabled", "skipped"):
        persistent_header: list[str] = []
        rule_line = _rule(width)
        body_offset = splash_line_count
    else:
        persistent_header = _render_persistent_header(state, width)
        rule_line = _header_rule(width, focused=header_focused)
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


_LOGO_COLS = 50
_LOGO_COLS_MAX = 72
_LOGO_SEP = " │ "


def _trim_visible_leading_spaces(line: str, spaces: int) -> str:
    if spaces <= 0:
        return line
    i = 0
    removed = 0
    prefix: list[str] = []
    n = len(line)
    while i < n and removed < spaces:
        if line[i] == "\x1b":
            m = _ANSI_STRIP.match(line, i)
            if m:
                prefix.append(m.group(0))
                i = m.end()
                continue
        if line[i] == " ":
            removed += 1
            i += 1
            continue
        break
    return "".join(prefix) + line[i:]


def _left_align_logo_lines(lines: list[str]) -> list[str]:
    leading: list[int] = []
    for line in lines:
        plain = _ANSI_STRIP.sub("", line)
        if plain.strip():
            leading.append(len(plain) - len(plain.lstrip(" ")))
    if not leading:
        return lines
    trim = min(leading)
    if trim <= 0:
        return lines
    return [_trim_visible_leading_spaces(line, trim) for line in lines]


def _logo_cols_for_width(width: int) -> int:
    # Keep a readable right panel while maximizing logo fidelity on wide terminals.
    return max(_LOGO_COLS, min(_LOGO_COLS_MAX, width - 28 - len(_LOGO_SEP)))


def _load_logo_lines(*, cols: int, color: bool = True, state: OperatorState | None = None) -> list[str]:
    """Return logo lines preferring highest-fidelity original-SVG renderers."""
    from agent.cli.logo_layout import COMPACT_HEADER_LINES

    header_3d = os.environ.get("ANANTA_TUI_HEADER_3D", "1").strip().lower() not in {"0", "false", "no", "off"}
    no_3d = (state.terminal_graphics or {}).get("no_3d", False) if state is not None else False
    if color:
        from client_surfaces.operator_tui.logo_inline import (
            render_logo_braille,
            render_logo_braille_animated,
            render_logo_snake_game_playable,
            render_logo_snake_game_animated,
            render_logo_halfblock_animated,
            render_logo_halfblock,
        )

        if header_3d and not no_3d:
            speed = float(os.environ.get("ANANTA_TUI_HEADER_3D_SPEED", "1.2"))
            anim_mode = os.environ.get("ANANTA_TUI_HEADER_ANIM", "snake_game").strip().lower()
            t_now = time.monotonic()
            lines = None
            game_state = state.header_logo_game if state is not None else None
            if game_state and game_state.get("active"):
                lines = render_logo_snake_game_playable(
                    cols=cols,
                    rows=COMPACT_HEADER_LINES,
                    game_state=game_state,
                    t=t_now,
                    speed=max(0.2, min(4.0, speed)),
                )
            if not lines and anim_mode in {"snake", "snake_game", "game"}:
                lines = render_logo_snake_game_animated(
                    cols=cols,
                    rows=COMPACT_HEADER_LINES,
                    t=t_now,
                    speed=max(0.2, min(4.0, speed)),
                )
            if not lines:
                lines = render_logo_braille_animated(
                    cols=cols,
                    rows=COMPACT_HEADER_LINES,
                    t=t_now,
                    speed=max(0.2, min(4.0, speed)),
                )
            if lines:
                return _left_align_logo_lines(lines)
            lines = render_logo_halfblock_animated(
                cols=cols,
                rows=COMPACT_HEADER_LINES,
                t=t_now,
                speed=max(0.2, min(4.0, speed)),
            )
            if lines:
                return _left_align_logo_lines(lines)

        # Highest resolution in terminal cells (2x4 pixels per char)
        lines = render_logo_braille(cols=cols, rows=COMPACT_HEADER_LINES)
        if lines:
            return _left_align_logo_lines(lines)

        # Fallback to half-block renderer (2 vertical pixels per char)
        lines = render_logo_halfblock(cols=cols, rows=COMPACT_HEADER_LINES)
        if lines:
            return _left_align_logo_lines(lines)

    # Fallback: existing ASCII art via logo_layout (pass snapshot=None → logo only)
    from agent.cli.logo_layout import render_compact_header
    return _left_align_logo_lines(
        render_compact_header(snapshot=None, terminal_width=cols + 20, color=color)
    )


def _assemble_header_lines(logo_lines: list[str], right_lines: list[str], n_rows: int, *, logo_cols: int) -> list[str]:
    """Combine logo and right-side lines with │ separator, padded to n_rows."""
    result = []
    for i in range(n_rows):
        logo_part = logo_lines[i] if i < len(logo_lines) else ""
        right_part = right_lines[i] if i < len(right_lines) else ""
        visible = len(_ANSI_STRIP.sub("", logo_part))
        padded = logo_part + " " * max(0, logo_cols - visible)
        result.append(padded + _LOGO_SEP + right_part)
    return result


def _render_persistent_header(state: OperatorState, width: int) -> list[str]:
    """Compact logo + live status (or interactive config when HEADER focused)."""
    from agent.cli.logo_layout import COMPACT_HEADER_LINES
    from agent.cli.status_snapshot import collect_status

    no_color = state.terminal_graphics.get("no_color", False) if state.terminal_graphics else False
    color = not no_color
    logo_cols = _logo_cols_for_width(width)
    right_width = max(20, width - logo_cols - len(_LOGO_SEP))

    logo_lines = _load_logo_lines(cols=logo_cols, color=color, state=state)

    if state.focus == FocusPane.HEADER:
        right_lines = _render_header_config_lines(state, right_width)
    else:
        snapshot = collect_status(
            mode=state.mode.value,
            endpoint=state.endpoint,
            auth_state=state.auth_state,
            section=state.section_id,
        )
        from agent.cli.status_snapshot import format_status_lines
        right_lines = format_status_lines(snapshot, color=color, width=right_width)

    while len(right_lines) < COMPACT_HEADER_LINES:
        right_lines.append("")

    return _assemble_header_lines(logo_lines, right_lines, COMPACT_HEADER_LINES, logo_cols=logo_cols)


def _render_header_config_lines(state: OperatorState, width: int) -> list[str]:
    from client_surfaces.operator_tui.header_config import CONFIG_ITEMS, CONFIG_LABELS, config_value, is_cycleable

    lines = [_pane_title("CONFIG", True)]
    game = state.header_logo_game or {}
    if game.get("active"):
        score = int(game.get("score", 0))
        status = "running" if game.get("alive", True) else "game over"
        lines.append(_clip(f"{DEFAULT_THEME.muted_prefix} Snake  score={score}  {status}", width))
        lines.append(_clip(f"{DEFAULT_THEME.muted_prefix} Arrows steuern · Tab verlässt Fokus", width))
        gaps = game.get("gaps") if isinstance(game.get("gaps"), dict) else {}
        if gaps:
            lines.append(_clip(f"{DEFAULT_THEME.muted_prefix} A-Lücke öffnet zufällig: Snake kann ins Dreieck", width))
    for i, key in enumerate(CONFIG_ITEMS):
        cursor = DEFAULT_THEME.selected_prefix if i == state.selected_index else DEFAULT_THEME.idle_prefix
        label = CONFIG_LABELS[key]
        value = config_value(state, key)
        hint = " [↵]" if is_cycleable(key) else "    "
        lines.append(_clip(f"{cursor} {label}= {value}{hint}", width))
    return lines


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
    nav_focused = state.focus == FocusPane.NAVIGATION
    for i, section in enumerate(SECTIONS):
        panel_state = (state.panel_states or {}).get(section.id)
        if nav_focused:
            # cursor shows selected_index; "*" marks the currently loaded section
            if i == state.selected_index:
                cursor = DEFAULT_THEME.selected_prefix
            elif section.id == state.section_id:
                cursor = "*"
            else:
                cursor = DEFAULT_THEME.idle_prefix
        else:
            cursor = DEFAULT_THEME.selected_prefix if section.id == state.section_id else DEFAULT_THEME.idle_prefix
        lines.append(f"{cursor}{state_prefix(panel_state)} {section.title}")
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
    hints = hints_for_mode(state.mode)
    game = state.header_logo_game or {}
    if game.get("active") and (state.focus is FocusPane.HEADER or game.get("ui_steering")):
        hints = "[←→↑↓] Snake steuert UI (ohne Game Over)  [oben-mitte] Input-Fokus  [Tab] Header"
    return _clip(hints, width)


def _rule(width: int) -> str:
    return "-" * width


def _header_rule(width: int, focused: bool = False) -> str:
    if not focused:
        return "-" * width
    label = " [HEADER] "
    dashes = width - len(label)
    left = dashes // 2
    right = dashes - left
    return "-" * left + label + "-" * right


def _clip(value: str, width: int) -> str:
    raw = str(value)
    plain = _ANSI_STRIP.sub("", raw)
    if len(plain) <= width:
        return raw
    return plain[: max(0, width - 3)] + "..."
