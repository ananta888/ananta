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
    propose_dock_lines = _tutorial_propose_dock_lines(state, width)
    body_height = height - 5 - body_offset - len(propose_dock_lines)
    body_height = max(3, body_height)
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

    lines.extend(propose_dock_lines)
    lines.append(_rule(width))
    lines.append(_status_line(state, width, splash_state=splash_state))
    lines.append(_command_line(state, width))
    lines.append(_hints_line(state, width))
    if state.show_help or section.id == "help":
        lines.extend(_help_overlay(state, width))
    lines = _overlay_fullscreen_snake(lines, state, width=width)
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

    renderer_pref = os.environ.get("ANANTA_TUI_LOGO_RENDERER", "auto").strip().lower()
    if renderer_pref in {"", "auto", "ansi", "sixel", "kitty", "none"}:
        from client_surfaces.operator_tui.logo_renderer.animated_header import render_header_logo

        lines = render_header_logo(
            cols=cols,
            rows=COMPACT_HEADER_LINES,
            color=color,
            t_now=time.monotonic(),
        )
        if lines:
            return _left_align_logo_lines(lines)
        logo_enabled = os.environ.get("ANANTA_TUI_LOGO", "1").strip().lower() not in {"0", "false", "no", "off"}
        if renderer_pref == "none" or not logo_enabled:
            return [""] * COMPACT_HEADER_LINES

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
            has_snake_roster = bool(game_state.get("snakes")) if isinstance(game_state, dict) else False
            if game_state and (game_state.get("active") or has_snake_roster):
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
    """Snake-focused header (left: snake roster/help, right: status/config)."""
    from agent.cli.logo_layout import COMPACT_HEADER_LINES
    from agent.cli.status_snapshot import collect_status

    no_color = state.terminal_graphics.get("no_color", False) if state.terminal_graphics else False
    color = not no_color
    left_cols = max(34, min(56, _logo_cols_for_width(width)))
    right_width = max(20, width - left_cols - len(_LOGO_SEP))
    left_lines = _render_header_snake_lines(state, left_cols)

    if state.focus == FocusPane.HEADER or bool((state.header_logo_game or {}).get("active")):
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

    while len(left_lines) < COMPACT_HEADER_LINES:
        left_lines.append("")

    return _assemble_header_lines(left_lines, right_lines, COMPACT_HEADER_LINES, logo_cols=left_cols)


def _render_header_snake_lines(state: OperatorState, width: int) -> list[str]:
    game = dict(state.header_logo_game or {})
    local_id = str(game.get("local_snake_id") or "s1")
    active = bool(game.get("active") and game.get("ui_steering"))
    status = "running" if game.get("alive", True) else "game over"
    remote_access_raw = game.get("remote_access")
    remote_access = dict(remote_access_raw) if isinstance(remote_access_raw, dict) else {}

    snakes_raw = game.get("snakes")
    snakes: dict[str, dict[str, object]] = (
        {str(k): dict(v) for k, v in snakes_raw.items() if isinstance(v, dict)}
        if isinstance(snakes_raw, dict)
        else {}
    )
    if not snakes:
        snakes = {
            local_id: {
                "id": local_id,
                "pseudonym": str(game.get("pseudonym") or "local-snake"),
                "snake_color": str(game.get("snake_color") or "mint"),
            },
            "s-ai": {
                "id": "s-ai",
                "pseudonym": "tutorial-ai",
                "snake_color": "amber",
                "oidc_provider": "codecompass-ai",
            },
        }

    def _access_for(sid: str, snap: dict[str, object]) -> str:
        if sid == local_id:
            return "full"
        level = str(remote_access.get(sid) or snap.get("access_level") or ("view" if sid == "s-ai" else "cancel")).lower()
        if level not in {"cancel", "view", "full"}:
            return "cancel"
        return level

    ordered = sorted(snakes.items(), key=lambda kv: (0 if str(kv[0]) == local_id else (1 if str(kv[0]) == "s-ai" else 2), str(kv[0])))
    lines = [_pane_title("SNAKE", state.focus == FocusPane.HEADER)]
    if active:
        lines.append(_clip(f"{DEFAULT_THEME.muted_prefix} Snake-Modus aktiv ({status}).", width))
        lines.append(_clip(f"{DEFAULT_THEME.muted_prefix} X markiert Start/Ende (Multi-Select).", width))
        lines.append(_clip(f"{DEFAULT_THEME.muted_prefix} C kopiert Auswahl; U oeffnet Tutorial-Chat.", width))
        lines.append(_clip(f"{DEFAULT_THEME.muted_prefix} O mouse-follow; Klick bestaetigt Ziel, Scroll waermt Intent.", width))
        lines.append(_clip(f"{DEFAULT_THEME.muted_prefix} Freigaben: :snake-access <id> cancel|view|full", width))
    else:
        lines.append(_clip(f"{DEFAULT_THEME.muted_prefix} Ctrl+S startet Snake-Modus (lokal/KI/remote).", width))
        lines.append(_clip(f"{DEFAULT_THEME.muted_prefix} Im Modus: X markieren, C kopieren, V replace, U Chat.", width))
        lines.append(_clip(f"{DEFAULT_THEME.muted_prefix} Maus: O follow; Klick + Hover aktiviert Kontext-Chat.", width))
        lines.append(_clip(f"{DEFAULT_THEME.muted_prefix} Freigaben: :snake-access <id> cancel|view|full", width))

    for sid, snap in ordered:
        pseudo = str(snap.get("pseudonym") or sid)
        color_name = str(snap.get("snake_color") or "mint")
        access = _access_for(str(sid), snap)
        provider = str(snap.get("oidc_provider") or "")
        ident = f"{str(sid).upper()} {pseudo} [{color_name}] access={access}"
        if provider:
            ident += f" @{provider}"
        lines.append(_clip(f"{DEFAULT_THEME.muted_prefix} {ident}", width))
        if len(lines) >= 8:
            break
    return lines


def _render_header_config_lines(state: OperatorState, width: int) -> list[str]:
    from client_surfaces.operator_tui.header_config import CONFIG_ITEMS, CONFIG_LABELS, config_value, is_cycleable

    lines = [_pane_title("CONFIG", True)]
    game = state.header_logo_game or {}
    if game.get("active"):
        status = "running" if game.get("alive", True) else "game over"
        snakes = game.get("snakes") if isinstance(game.get("snakes"), dict) else {}
        peer_count = len([k for k in snakes.keys() if str(k) != str(game.get("local_snake_id") or "s1")]) if snakes else 0
        lines.append(_clip(f"{DEFAULT_THEME.muted_prefix} Snake-Modus aktiv  {status}", width))
        lines.append(_clip(f"{DEFAULT_THEME.muted_prefix} Snake-ID: {game.get('local_snake_id', 's1')} · Peers: {peer_count}", width))
        lines.append(_clip(f"{DEFAULT_THEME.muted_prefix} X=Markieren/Multi, C=Copy, V=Replace, U=Chat", width))
        lines.append(_clip(f"{DEFAULT_THEME.muted_prefix} O=MouseFollow, Klick=Intent+Chat, Scroll=Intent-Hinweis", width))
        lines.append(_clip(f"{DEFAULT_THEME.muted_prefix} Snakes (OIDC / Farbe / Nachricht):", width))
        if snakes:
            ordered = sorted(snakes.items(), key=lambda kv: (0 if str(kv[0]) == str(game.get("local_snake_id") or "s1") else 1, str(kv[0])))
            for sid, snap in ordered:
                if not isinstance(snap, dict):
                    continue
                color_name = str(snap.get("snake_color") or "mint")
                pseudonym = str(snap.get("pseudonym") or sid)
                provider = str(snap.get("oidc_provider") or "unknown-oidc")
                msg = str(snap.get("message") or "-")
                pal = _snake_palette(color_name)
                user_col = pal["head"]
                msg_col = pal["body"]
                prefix = f"{DEFAULT_THEME.muted_prefix} "
                user_plain = f"{str(sid).upper()} {pseudonym}@{provider} [{color_name}]"
                max_entry_len = max(0, width - len(_ANSI_STRIP.sub("", prefix)) - 2)  # ": "
                if len(user_plain) > max_entry_len:
                    user_plain = user_plain[: max(0, max_entry_len - 3)] + "..."
                    lines.append(prefix + f"\x1b[38;2;{user_col[0]};{user_col[1]};{user_col[2]}m{user_plain}\x1b[0m")
                    continue
                remaining = max(0, max_entry_len - len(user_plain))
                msg_plain = msg if len(msg) <= remaining else (msg[: max(0, remaining - 3)] + "...")
                user_colored = f"\x1b[38;2;{user_col[0]};{user_col[1]};{user_col[2]}m{user_plain}\x1b[0m"
                msg_colored = f"\x1b[38;2;{msg_col[0]};{msg_col[1]};{msg_col[2]}m{msg_plain}\x1b[0m"
                lines.append(prefix + f"{user_colored}: {msg_colored}")
        if game.get("message_mode"):
            draft = str(game.get("message_draft", ""))
            lines.append(_clip(f"{DEFAULT_THEME.muted_prefix} MSG* {draft}", width))
        return lines
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
        lines.extend(_dashboard_content_lines(payload, state=state, width=width))
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

    if state.markdown_source:
        lines.append("")
        for block in detect_diagram_blocks(state.markdown_source):
            lines.extend(render_diagram_fallback(block, width=width))
            lines.append("")
        max_lines = 24 if state.mode.value == "edit" else 8
        lines.append("markdown:")
        lines.extend(render_markdown_lines(state.markdown_source, width=width, max_lines=max_lines))

    return lines


def _dashboard_content_lines(payload: dict, *, state: OperatorState | None = None, width: int = 72) -> list[str]:
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
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    mouse_caps = (state.terminal_graphics or {}).get("mouse_support")
    mouse_state = game.get("mouse_state") if isinstance(game.get("mouse_state"), dict) else {}
    parts = [
        f"endpoint={state.endpoint}",
        f"auth={state.auth_state}",
        f"focus={state.focus.value}",
        f"mode={state.mode.value}",
        f"status={state.status_message}",
    ]
    if isinstance(mouse_caps, dict):
        parts.append(f"mouse_support={'enabled' if mouse_caps.get('enabled') else 'disabled'}")
        parts.append(f"term={mouse_caps.get('term')}")
    if isinstance(mouse_state, dict) and mouse_state.get("active"):
        parts.append(f"mouse={int(mouse_state.get('x', 0))},{int(mouse_state.get('y', 0))}")
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
        hints = "[Ctrl+S] Snake  [U] Tutorial-AI  [O] MouseFollow  [B] Frame  [X/C/V] Select/Copy/Replace  [Z] Clear"
    return _clip(hints, width)


def _tutorial_propose_dock_lines(state: OperatorState, width: int) -> list[str]:
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    history = game.get("tutorial_propose_history") if isinstance(game.get("tutorial_propose_history"), list) else []
    chat = game.get("artifact_chat_state") if isinstance(game.get("artifact_chat_state"), dict) else {}
    show_tutorial_flow = bool(game.get("tutorial_mode")) or bool(history) or bool(chat.get("active_target"))
    if not show_tutorial_flow:
        return []

    inner_width = max(24, int(width) - 4)
    top = f"+-{'-' * inner_width}-+"
    title = f"| {_clip('Tutorial-AI propose flow', inner_width).ljust(inner_width)} |"

    rows: list[str] = []
    marker_bits: list[str] = []
    if bool(game.get("mouse_follow_enabled")) and bool(game.get("mouse_state")):
        marker_bits.append("mouse-follow")
    confidence = str(game.get("artifact_intent_confidence") or "none")
    if confidence in {"likely", "confirmed"}:
        marker_bits.append("artifact-intent")
    if str(game.get("tutorial_ai_target_mode") or "") in {"fast_target", "explain_target"}:
        marker_bits.append("ai-fast-target")
    if isinstance(chat, dict) and chat.get("active_target"):
        marker_bits.append("artifact-chat-active")
    if marker_bits:
        marker_line = " ".join(f"[{bit}]" for bit in marker_bits)
        rows.append(f"| {shorten(marker_line, width=inner_width, placeholder='...').ljust(inner_width)} |")
    if isinstance(chat, dict) and isinstance(chat.get("active_target"), dict):
        active = chat.get("active_target") or {}
        label = str(active.get("label") or active.get("path") or active.get("id") or "(none)")
        rows.append(f"| {shorten(f'context: {label}', width=inner_width, placeholder='...').ljust(inner_width)} |")
    if history:
        for entry in history[-2:]:
            if not isinstance(entry, dict):
                continue
            source = str(entry.get("source") or "unknown")
            target = str(entry.get("target") or "content")
            text = str(entry.get("text") or "").strip()
            if not text:
                continue
            label = f"[{source}->{target}] {text}"
            rows.append(f"| {shorten(label, width=inner_width, placeholder='...').ljust(inner_width)} |")
    if not rows:
        rows.append(f"| {_clip('waiting for first propose...', inner_width).ljust(inner_width)} |")

    return [top, title, *rows, top]


def _overlay_fullscreen_snake(lines: list[str], state: OperatorState, *, width: int) -> list[str]:
    game = state.header_logo_game or {}
    if not game.get("active") or not game.get("free_mode"):
        return lines
    local_snake = game.get("snake") or []
    if not isinstance(local_snake, list) or not local_snake:
        return lines

    out = list(lines)
    local_id = str(game.get("local_snake_id") or "s1")
    snakes = _collect_snakes(game, local_snake_id=local_id)
    local_snapshot = snakes.get(local_id, {}) if isinstance(snakes.get(local_id), dict) else {}
    local_pal = _snake_palette(str(local_snapshot.get("snake_color") or game.get("snake_color") or "mint"))

    # Markings and selections are rendered in each snake's own color.
    for sid, snapshot in snakes.items():
        if not isinstance(snapshot, dict):
            continue
        pal = _snake_palette(str(snapshot.get("snake_color") or "mint"))
        mark_cells = snapshot.get("mark_cells") if isinstance(snapshot.get("mark_cells"), list) else []
        for item in mark_cells:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            x = int(item[0]) % max(1, width)
            y = int(item[1]) % max(1, len(out))
            base = _visible_char_at(out[y], x)
            if base == " ":
                continue
            mcol = pal["body"]
            repl = f"\x1b[48;2;{mcol[0]};{mcol[1]};{mcol[2]}m\x1b[38;2;20;20;20m{base}\x1b[0m"
            out[y] = _overlay_at_visible_col(out[y], x, repl)

        selection = snapshot.get("selection_cells") if isinstance(snapshot.get("selection_cells"), list) else []
        for item in selection:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            x = int(item[0]) % max(1, width)
            y = int(item[1]) % max(1, len(out))
            base = _visible_char_at(out[y], x)
            if base == " ":
                base = "░"
            scol = pal["head"]
            repl = f"\x1b[48;2;{scol[0]};{scol[1]};{scol[2]}m\x1b[38;2;15;15;15m{base}\x1b[0m"
            out[y] = _overlay_at_visible_col(out[y], x, repl)

    anchor = game.get("selection_anchor")
    if isinstance(anchor, (list, tuple)) and len(anchor) == 2:
        x = int(anchor[0]) % max(1, width)
        y = int(anchor[1]) % max(1, len(out))
        acol = local_pal["label"]
        repl = f"\x1b[38;2;{acol[0]};{acol[1]};{acol[2]}m◎\x1b[0m"
        out[y] = _overlay_at_visible_col(out[y], x, repl)

    if bool(game.get("selection_frame_mode")):
        frame_anchor = game.get("selection_frame_anchor")
        local_head = local_snake[0] if local_snake and isinstance(local_snake[0], (list, tuple)) else None
        if isinstance(frame_anchor, (list, tuple)) and len(frame_anchor) == 2 and isinstance(local_head, (list, tuple)) and len(local_head) == 2:
            ax, ay = int(frame_anchor[0]), int(frame_anchor[1])
            hx, hy = int(local_head[0]), int(local_head[1])
            out = _overlay_frame_preview(out, x1=ax, y1=ay, x2=hx, y2=hy, width=width, color=local_pal["label"])

    for sid, snapshot in snakes.items():
        snake = snapshot.get("snake") if isinstance(snapshot.get("snake"), list) else []
        if not snake:
            continue
        pal = _snake_palette(str(snapshot.get("snake_color") or "mint"))
        for idx, pos in enumerate(snake):
            if not isinstance(pos, (list, tuple)) or len(pos) != 2:
                continue
            x = int(pos[0]) % max(1, width)
            y = int(pos[1]) % max(1, len(out))
            ch = "●" if idx == 0 else ("◉" if idx < 4 else "·")
            col = pal["head"] if idx == 0 else pal["body"]
            repl = f"\x1b[38;2;{col[0]};{col[1]};{col[2]}m{ch}\x1b[0m"
            out[y] = _overlay_at_visible_col(out[y], x, repl)

        message = _display_message_for_snake(str(snapshot.get("message") or ""))
        style = str(snapshot.get("message_style") or "trail")
        trail = snapshot.get("trail_path") if isinstance(snapshot.get("trail_path"), list) else []
        if message and trail:
            trail_window = int(snapshot.get("trail_window") or os.environ.get("ANANTA_TUI_SNAKE_TRAIL_WINDOW", "10"))
            trail_speed = float(snapshot.get("trail_speed") or os.environ.get("ANANTA_TUI_SNAKE_TRAIL_SPEED", "8.0"))
            out = _overlay_snake_message_effect(
                out,
                snake=snake,
                trail=trail,
                message=message,
                width=width,
                mode=style,
                color=pal["label"],
                trail_window=trail_window,
                trail_speed=trail_speed,
            )

    return out


def _collect_snakes(game: dict[str, object], *, local_snake_id: str) -> dict[str, dict[str, object]]:
    raw = game.get("snakes")
    out: dict[str, dict[str, object]] = {}
    if isinstance(raw, dict):
        for sid, snapshot in raw.items():
            if isinstance(snapshot, dict):
                out[str(sid)] = dict(snapshot)
    if local_snake_id not in out:
        out[local_snake_id] = {
            "id": local_snake_id,
            "pseudonym": str(game.get("pseudonym") or local_snake_id),
            "oidc_provider": str(game.get("oidc_provider") or "unknown-oidc"),
            "snake": list(game.get("snake") or []),
            "trail_path": list(game.get("trail_path") or []),
            "mark_cells": list(game.get("mark_cells") or []),
            "selection_cells": list(game.get("selection_cells") or []),
            "selection_regions": list(game.get("selection_regions") or []),
            "message": str(game.get("message") or ""),
            "message_style": str(game.get("message_style") or "trail"),
            "snake_color": str(game.get("snake_color") or "mint"),
            "trail_window": int(game.get("trail_window") or os.environ.get("ANANTA_TUI_SNAKE_TRAIL_WINDOW", "10")),
            "trail_speed": float(game.get("trail_speed") or os.environ.get("ANANTA_TUI_SNAKE_TRAIL_SPEED", "8.0")),
            "local": True,
        }
    return out


def _snake_palette(name: str) -> dict[str, tuple[int, int, int]]:
    palettes = {
        "mint": {"head": (170, 255, 210), "body": (96, 215, 165), "label": (236, 255, 244)},
        "cyan": {"head": (120, 235, 255), "body": (75, 188, 224), "label": (220, 248, 255)},
        "violet": {"head": (212, 176, 255), "body": (163, 120, 228), "label": (242, 230, 255)},
        "amber": {"head": (255, 205, 130), "body": (224, 155, 84), "label": (255, 238, 202)},
        "rose": {"head": (255, 170, 200), "body": (222, 110, 156), "label": (255, 230, 240)},
    }
    return palettes.get(name, palettes["mint"])


def _display_message_for_snake(value: str) -> str:
    # Display-only mapping: keep stored/copied text unchanged.
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\n", "⏎")


def _overlay_snake_message_effect(
    out: list[str],
    *,
    snake: list[object],
    trail: list[object],
    message: str,
    width: int,
    mode: str,
    color: tuple[int, int, int],
    trail_window: int,
    trail_speed: float,
) -> list[str]:
    seq = f"{message}   "
    if not seq.strip():
        return out
    speed = max(0.2, min(60.0, float(trail_speed)))
    phase = int(time.monotonic() * speed)
    tail_offset = max(0, len(snake))
    height = max(1, len(out))

    if mode == "ticker":
        y = max(0, height - 2)
        start = max(0, width - ((phase * 2) % max(1, width + len(seq))))
        return _overlay_text(out, x=start, y=y, text=seq, color=color)

    if mode == "orbit":
        if not snake or not isinstance(snake[0], (list, tuple)) or len(snake[0]) != 2:
            return out
        hx, hy = int(snake[0][0]), int(snake[0][1])
        ring = [(-2, 0), (-1, -1), (0, -2), (1, -1), (2, 0), (1, 1), (0, 2), (-1, 1)]
        for i, ch in enumerate(seq):
            if ch == " ":
                continue
            dx, dy = ring[(i + phase) % len(ring)]
            x = (hx + dx) % max(1, width)
            y = (hy + dy) % height
            repl = f"\x1b[38;2;{color[0]};{color[1]};{color[2]}m{ch}\x1b[0m"
            out[y] = _overlay_at_visible_col(out[y], x, repl)
        return out

    # default: trailing text behind the tail
    window = max(1, min(240, int(trail_window)))
    if len(seq) > 0:
        seq_window = "".join(seq[(phase + i) % len(seq)] for i in range(window))
    else:
        seq_window = ""
    trail_positions = _message_trail_positions(
        snake=snake,
        trail=trail,
        width=width,
        height=height,
        tail_offset=tail_offset,
        needed=max(1, len(seq_window)),
    )
    max_chars = min(len(seq_window), len(trail_positions))
    for i in range(max_chars):
        pos = trail_positions[i]
        if not isinstance(pos, (list, tuple)) or len(pos) != 2:
            continue
        x = int(pos[0]) % max(1, width)
        y = int(pos[1]) % height
        ch = seq_window[i]
        if ch == " ":
            continue
        repl = f"\x1b[38;2;{color[0]};{color[1]};{color[2]}m{ch}\x1b[0m"
        out[y] = _overlay_at_visible_col(out[y], x, repl)
    return out


def _message_trail_positions(
    *,
    snake: list[object],
    trail: list[object],
    width: int,
    height: int,
    tail_offset: int,
    needed: int,
) -> list[tuple[int, int]]:
    w = max(1, width)
    h = max(1, height)
    positions: list[tuple[int, int]] = []
    for pos in trail[tail_offset:]:
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            positions.append((int(pos[0]) % w, int(pos[1]) % h))
            if len(positions) >= needed:
                return positions

    if snake and isinstance(snake[-1], (list, tuple)) and len(snake[-1]) == 2:
        tx, ty = int(snake[-1][0]) % w, int(snake[-1][1]) % h
    else:
        tx, ty = 0, 0

    dx, dy = -1, 0
    if len(snake) >= 2 and isinstance(snake[-1], (list, tuple)) and isinstance(snake[-2], (list, tuple)):
        if len(snake[-1]) == 2 and len(snake[-2]) == 2:
            tx2, ty2 = int(snake[-2][0]) % w, int(snake[-2][1]) % h
            dx = (tx - tx2)
            dy = (ty - ty2)
            if dx > 1:
                dx = -1
            elif dx < -1:
                dx = 1
            dx = 1 if dx > 0 else (-1 if dx < 0 else 0)
            if dy > 1:
                dy = -1
            elif dy < -1:
                dy = 1
            dy = 1 if dy > 0 else (-1 if dy < 0 else 0)
            if dx == 0 and dy == 0:
                dx, dy = -1, 0

    while len(positions) < needed:
        tx = (tx + dx) % w
        ty = (ty + dy) % h
        positions.append((tx, ty))
    return positions


def _overlay_text(
    out: list[str],
    *,
    x: int,
    y: int,
    text: str,
    color: tuple[int, int, int],
) -> list[str]:
    if y < 0 or y >= len(out):
        return out
    cx = max(0, x)
    for ch in text:
        repl = f"\x1b[38;2;{color[0]};{color[1]};{color[2]}m{ch}\x1b[0m"
        out[y] = _overlay_at_visible_col(out[y], cx, repl)
        cx += 1
    return out


def _overlay_frame_preview(
    out: list[str],
    *,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    width: int,
    color: tuple[int, int, int],
) -> list[str]:
    if not out:
        return out
    height = len(out)
    min_x, max_x = sorted((x1 % max(1, width), x2 % max(1, width)))
    min_y, max_y = sorted((y1 % max(1, height), y2 % max(1, height)))
    h = "═"
    v = "║"
    tl, tr, bl, br = "╔", "╗", "╚", "╝"

    for x in range(min_x, max_x + 1):
        out[min_y] = _overlay_at_visible_col(out[min_y], x, f"\x1b[38;2;{color[0]};{color[1]};{color[2]}m{h}\x1b[0m")
        out[max_y] = _overlay_at_visible_col(out[max_y], x, f"\x1b[38;2;{color[0]};{color[1]};{color[2]}m{h}\x1b[0m")
    for y in range(min_y, max_y + 1):
        out[y] = _overlay_at_visible_col(out[y], min_x, f"\x1b[38;2;{color[0]};{color[1]};{color[2]}m{v}\x1b[0m")
        out[y] = _overlay_at_visible_col(out[y], max_x, f"\x1b[38;2;{color[0]};{color[1]};{color[2]}m{v}\x1b[0m")
    out[min_y] = _overlay_at_visible_col(out[min_y], min_x, f"\x1b[38;2;{color[0]};{color[1]};{color[2]}m{tl}\x1b[0m")
    out[min_y] = _overlay_at_visible_col(out[min_y], max_x, f"\x1b[38;2;{color[0]};{color[1]};{color[2]}m{tr}\x1b[0m")
    out[max_y] = _overlay_at_visible_col(out[max_y], min_x, f"\x1b[38;2;{color[0]};{color[1]};{color[2]}m{bl}\x1b[0m")
    out[max_y] = _overlay_at_visible_col(out[max_y], max_x, f"\x1b[38;2;{color[0]};{color[1]};{color[2]}m{br}\x1b[0m")
    return out


def _overlay_at_visible_col(line: str, col: int, replacement: str) -> str:
    col = max(0, col)
    plain = _ANSI_STRIP.sub("", line)
    if len(plain) <= col:
        return line + (" " * (col - len(plain))) + replacement

    i = 0
    visible = 0
    out = ""
    n = len(line)
    while i < n:
        if line[i] == "\x1b":
            m = _ANSI_STRIP.match(line, i)
            if m:
                out += m.group(0)
                i = m.end()
                continue
        ch = line[i]
        if visible == col:
            out += replacement
            i += 1
            out += line[i:]
            return out
        out += ch
        i += 1
        visible += 1
    return line


def _visible_char_at(line: str, col: int) -> str:
    col = max(0, col)
    plain = _ANSI_STRIP.sub("", line)
    if col >= len(plain):
        return " "
    return plain[col]


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
