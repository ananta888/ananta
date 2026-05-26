from __future__ import annotations

import os
import re
import time
from textwrap import shorten
from typing import TYPE_CHECKING

_ANSI_STRIP = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

from client_surfaces.operator_tui.diagrams import detect_diagram_blocks, render_diagram_fallback
from client_surfaces.operator_tui.goal_artifact_filters import filter_goal_artifact_view
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
    """Hybrid header: logo in normal mode, snake panel in active snake mode."""
    from agent.cli.logo_layout import COMPACT_HEADER_LINES
    from agent.cli.status_snapshot import collect_status

    no_color = state.terminal_graphics.get("no_color", False) if state.terminal_graphics else False
    color = not no_color
    left_cols = max(34, min(56, _logo_cols_for_width(width)))
    right_width = max(20, width - left_cols - len(_LOGO_SEP))
    game = state.header_logo_game or {}
    snake_mode_active = bool(game.get("active"))
    if snake_mode_active:
        left_lines = _render_header_snake_lines(state, left_cols)
    else:
        left_lines = _load_logo_lines(cols=left_cols, color=color, state=state)

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
    active = bool(game.get("active"))
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
    # T02.04: tutor pointer – blink marker next to target section
    game = state.header_logo_game or {}
    ptr = game.get("tutor_pointer") if isinstance(game.get("tutor_pointer"), dict) else {}
    ptr_target = str(ptr.get("target") or "") if ptr else ""
    ptr_blink = int(ptr.get("blink_frame", 0)) if ptr else 0
    ptr_visible = ptr_blink % 2 == 0  # blink: visible on even frames
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
        pointer_suffix = ""
        if ptr_target == section.id and ptr_visible:
            pointer_suffix = " \x1b[38;2;255;205;130m←\x1b[0m"
        lines.append(f"{cursor}{state_prefix(panel_state)} {section.title}{pointer_suffix}")
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
    elif section.id == "artifacts" and bool(payload.get("goal_artifacts_mode")):
        lines.extend(_goal_artifacts_content_lines(payload, width=width, compact=width < 74))
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
    if section.id == "artifacts":
        payload = (state.section_payloads or {}).get(section.id, {})
        if bool(payload.get("goal_artifacts_mode")):
            lines.append("    :goal artifacts [filter ...|clear-filter]")
            lines.append("    :goal sources candidates")
            lines.append("    :goal source grant/revoke/detail ...")
            lines.append("    :artifact provenance <output-id>")
            lines.append("    :artifact prompt <output-id>")
            lines.append("    :artifact config <output-id>")

    return [_clip(line, width) for line in lines]


def _goal_artifacts_content_lines(payload: dict, *, width: int, compact: bool) -> list[str]:
    def _safe(value: object) -> str:
        text = str(value or "")
        text = _ANSI_STRIP.sub("", text)
        return text.replace("\r", " ").replace("\n", " ")

    goal_id = str(payload.get("goal_id") or "unknown")
    filters = dict(payload.get("filters") or {})
    filtered = filter_goal_artifact_view(
        source_grants=list(payload.get("source_grants") or []),
        source_usages=list(payload.get("source_usages") or []),
        output_artifacts=list(payload.get("output_artifacts") or []),
        filters=filters,
    )
    grants = list(filtered.get("source_grants") or [])
    usages = list(filtered.get("source_usages") or [])
    outputs = list(filtered.get("output_artifacts") or [])
    usage_grant_ids = {_safe(item.get("grant_id") or "") for item in usages}
    lines = [
        f"  Goal Artifacts: {goal_id}",
        f"  Filters: {', '.join([f'{k}={v}' for k, v in filters.items()]) if filters else 'none'}",
    ]
    if compact:
        lines.append("  --- compact view ---")
        for grant in grants[:5]:
            grant_id = str(grant.get("grant_id") or "?")
            marker = "✓" if grant_id in usage_grant_ids else "~"
            lines.append(
                _clip(
                    f"  {marker} grant {grant_id} source={_safe(grant.get('artifact_ref') or '-')}",
                    width,
                )
            )
        for usage in usages[:5]:
            lines.append(_clip(f"  • usage {_safe(usage.get('usage_id'))} -> {_safe(usage.get('artifact_ref'))}", width))
        for output in outputs[:6]:
            provenance_note = " provenance-missing" if not _safe(output.get("provenance_id")) else ""
            lines.append(
                _clip(
                    "  ◦ output "
                    f"{_safe(output.get('output_artifact_id'))} type={_safe(output.get('artifact_type'))} "
                    f"status={_safe(output.get('status'))}{provenance_note} "
                    f"exec={_safe(output.get('execution_summary') or '')}",
                    width,
                )
            )
        if not grants and not usages and not outputs:
            lines.append("  (empty goal artifact graph)")
        return lines

    lines.append("  [Freigegeben]")
    if not grants:
        lines.append("    - none")
    for grant in grants[:8]:
        grant_id = _safe(grant.get("grant_id") or "?")
        used = grant_id in usage_grant_ids
        marker = "used" if used else "granted-not-used"
        lines.append(
            _clip(
                f"    {grant_id} [{marker}] sensitivity={_safe(grant.get('sensitivity'))} "
                f"boundary={_safe(grant.get('data_boundary'))} ref={_safe(grant.get('artifact_ref'))}",
                width,
            )
        )

    lines.append("  [Genutzt]")
    if not usages:
        lines.append("    - none")
    for usage in usages[:8]:
        lines.append(
            _clip(
                f"    {_safe(usage.get('usage_id'))} grant={_safe(usage.get('grant_id'))} "
                f"task={_safe(usage.get('task_id'))} worker={_safe(usage.get('worker_id'))} "
                f"ref={_safe(usage.get('artifact_ref'))}",
                width,
            )
        )

    lines.append("  [Erzeugt]")
    if not outputs:
        lines.append("    - none")
    for output in outputs[:10]:
        provenance_note = "provenance missing" if not _safe(output.get("provenance_id")) else f"prov={_safe(output.get('provenance_id'))}"
        lines.append(
            _clip(
                f"    {_safe(output.get('output_artifact_id'))} type={_safe(output.get('artifact_type'))} "
                f"status={_safe(output.get('status'))} task={_safe(output.get('task_id'))} "
                f"worker={_safe(output.get('worker_id'))} {provenance_note} created_at={_safe(output.get('created_at'))}",
                width,
            )
        )
        summary = _safe(output.get("execution_summary"))
        if summary:
            lines.append(_clip(f"      exec: {summary}", width))
    return lines


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
    active_goal_id = str(game.get("active_goal_id") or "").strip()
    if active_goal_id:
        parts.append(f"goal={active_goal_id}")
        goal_filters = dict(game.get("goal_artifact_filters") or {})
        if goal_filters:
            compact_filters = ",".join(f"{k}:{v}" for k, v in goal_filters.items())
            parts.append(f"goal_filters={compact_filters}")
    ai_mode = str(game.get("ai_snake_mode") or "").strip()
    if ai_mode:
        runtime = str(game.get("ai_snake_runtime_status") or "idle")
        parts.append(f"ai={ai_mode}/{runtime}")
        prediction = game.get("ai_snake_prediction")
        if isinstance(prediction, dict):
            pred_intent = str(prediction.get("predicted_intent") or "unknown")
            pred_conf = float(prediction.get("confidence") or 0.0)
            parts.append(f"pred={pred_intent}:{pred_conf:.2f}")
        debug = game.get("ai_snake_debug")
        if isinstance(debug, dict):
            reason = str(debug.get("gate_reason") or "")
            if reason:
                parts.append(f"gate={reason}")
            active_refs = debug.get("active_pattern_refs")
            if isinstance(active_refs, list):
                parts.append(f"patterns={len(active_refs)}")
                if active_refs and isinstance(active_refs[0], dict):
                    parts.append(f"last_pattern={str(active_refs[0].get('pattern_id') or '-')}")
                parts.append("learned=yes" if active_refs else "learned=no")
                if not active_refs:
                    parts.append("no_learned_profile_yet")
            source = str(debug.get("prediction_source") or "")
            if source:
                parts.append(f"src={source}")
        envelope = game.get("ai_snake_context_envelope")
        if isinstance(envelope, dict):
            parts.append(f"ctx={str(envelope.get('context_hash') or 'missing')}")
    if os.environ.get("ANANTA_TUI_GFX_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}:
        try:
            from client_surfaces.operator_tui.logo_renderer.animated_header import get_last_render_metrics

            metrics = get_last_render_metrics()
        except Exception:
            metrics = {}
        if metrics:
            parts.append(
                "gfx="
                f"{metrics.get('backend','?')}:"
                f"{metrics.get('render_ms','?')}/"
                f"{metrics.get('encode_ms','?')}/"
                f"{metrics.get('output_ms','?')}ms"
            )
            parts.append(f"gfx_fps={metrics.get('fps','?')}")
            frame_w = metrics.get("frame_w")
            frame_h = metrics.get("frame_h")
            if frame_w and frame_h:
                parts.append(f"gfx_frame={frame_w}x{frame_h}")
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
        chat_raw = game.get("chat_state")
        chat_focus = isinstance(chat_raw, dict) and bool(chat_raw.get("chat_focus"))
        if chat_focus:
            active_ch = ""
            if isinstance(chat_raw, dict):
                active_ch = str(chat_raw.get("active_channel") or "room:main")
            hints = f"[Esc] game  [Enter] send  [PageUp/Down] scroll  [{active_ch}]"
        elif game.get("paused"):
            hints = "[Space] Resume  [c] chat  [U] Tutorial-AI  [O] MouseFollow  [B] Frame  [X/C/V] Select  [Z] Clear"
        else:
            hints = "[Ctrl+S] Snake  [Space] Pause  [c] chat  [U] Tutorial-AI  [O] MouseFollow  [B] Frame  [Z] Clear"
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

    # Split-view: if wide enough, reserve right portion for AI+Chat panels (T01.01)
    ai_panel_width = 40
    split_col = width - ai_panel_width - 2  # 2 for divider
    split_view = width >= 100
    # Chat panel: bottom portion of the right column (requires width>=120, height>=32)
    chat_panel_enabled = width >= 120 and len(lines) >= 32

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

    # Pause overlay (T01.02)
    if bool(game.get("paused")):
        out = _overlay_snake_paused(out, width=width, height=len(out))

    # Split-view AI panel (T01.01)
    ai_panel_height = len(out)
    if split_view and chat_panel_enabled:
        # Reserve bottom portion for chat panel
        ai_panel_height = max(8, len(out) - 10)
    if split_view:
        out = _overlay_snake_ai_panel(out, game, split_col=split_col, panel_width=ai_panel_width, height=ai_panel_height)

    # Chat panel (E01)
    if split_view and chat_panel_enabled:
        out = _overlay_snake_chat_panel(out, game, split_col=split_col, panel_width=ai_panel_width, ai_rows=ai_panel_height, height=len(out))
    elif split_view:
        # Compact unread status line at bottom-right when chat panel doesn't fit
        out = _overlay_snake_chat_unread(out, game, split_col=split_col, panel_width=ai_panel_width, height=len(out))

    # Score / highscore header (T01.05)
    out = _overlay_snake_score_header(out, game, width=width)

    # Min-size warning (T01.03)
    if width < 40 or len(out) < 18:
        warn = "Terminal zu klein für Snake"
        out[0] = _overlay_text(out[:1], x=2, y=0, text=warn, color=(255, 80, 80))[0] if out else out[0]

    return out


def _overlay_snake_paused(lines: list[str], *, width: int, height: int) -> list[str]:
    """Render PAUSED overlay centered on the game area (T01.02)."""
    out = list(lines)
    label = " [ PAUSED ] "
    cy = max(0, height // 2 - 1)
    cx = max(0, (width // 2) - len(label) // 2)
    # PAUSED label in amber
    _overlay_text(out, x=cx, y=cy, text=label, color=(255, 200, 80))
    # hint line below
    hint = "Space zum Fortsetzen"
    hx = max(0, (width // 2) - len(hint) // 2)
    _overlay_text(out, x=hx, y=min(cy + 1, height - 1), text=hint, color=(160, 160, 160))
    return out


def _overlay_snake_ai_panel(
    lines: list[str],
    game: dict[str, object],
    *,
    split_col: int,
    panel_width: int,
    height: int,
) -> list[str]:
    """Render the AI explanation panel on the right side (T01.01)."""
    out = list(lines)
    if not out:
        return out

    # Build panel content lines
    panel_lines: list[str] = []

    # Header
    snakes_raw = game.get("snakes")
    ai_snap: dict[str, object] = {}
    if isinstance(snakes_raw, dict):
        ai_snap = dict(snakes_raw.get("s-ai") or {})
    depth = str(game.get("tutor_depth_mode") or "overview")
    tutorial_mode = bool(game.get("tutorial_mode"))
    paused = bool(game.get("paused"))
    ai_color = "amber"
    pal = _snake_palette(ai_color)
    hcol = pal["head"]

    status_parts = [f"tutor-ai [{ai_color}]", depth]
    if paused:
        status_parts.append("⏸ paused")
    if tutorial_mode:
        queue_raw = game.get("tutor_event_queue") or []
        q_depth = len(queue_raw) if isinstance(queue_raw, list) else 0
        if q_depth > 0:
            status_parts.append(f"queue:{q_depth}")
    header_text = " · ".join(status_parts)
    panel_lines.append(f"\x1b[38;2;{hcol[0]};{hcol[1]};{hcol[2]}m{header_text[:panel_width]}\x1b[0m")
    panel_lines.append("─" * panel_width)

    # Tutorial step header (T04.01)
    ts_raw = game.get("tutorial_state")
    if isinstance(ts_raw, dict) and ts_raw.get("active"):
        try:
            from client_surfaces.operator_tui.snake_tutorial import format_step_header, get_current_step
            step_header = format_step_header(ts_raw)
            step = get_current_step(ts_raw)
            if step_header:
                panel_lines.append(step_header[:panel_width])
            if step:
                panel_lines.append(f"▶ {step['title'][:panel_width - 2]}")
                task_text = step.get("task") or ""
                # wrap task text
                words = task_text.split()
                row = ""
                for word in words:
                    if len(row) + len(word) + 1 > panel_width - 2:
                        panel_lines.append(f"  {row}")
                        row = word
                    else:
                        row = (row + " " + word).strip()
                if row:
                    panel_lines.append(f"  {row}")
                hint = step.get("hint") or ""
                if hint:
                    panel_lines.append(f"\x1b[38;2;120;120;120m  ↳ {hint[:panel_width - 5]}\x1b[0m")
            panel_lines.append("─" * panel_width)
        except Exception:
            pass

    # Current AI tip
    tip = str(ai_snap.get("message") or "")
    if not tip:
        history = game.get("tutorial_propose_history") if isinstance(game.get("tutorial_propose_history"), list) else []
        if history:
            last = history[-1]
            tip = str(last.get("text") or "") if isinstance(last, dict) else ""
    if tip:
        panel_lines.append("\x1b[38;2;255;205;130mAktuell:\x1b[0m")
        # word-wrap
        words = tip.split()
        row = ""
        for word in words:
            if len(row) + len(word) + 1 > panel_width - 1:
                panel_lines.append(f" {row}")
                row = word
            else:
                row = (row + " " + word).strip()
        if row:
            panel_lines.append(f" {row}")

    # Ask Q&A state
    question = str(game.get("tutor_ask_question") or "")
    if question and not bool(game.get("tutor_ask_answered")):
        panel_lines.append("─" * panel_width)
        panel_lines.append(f"\x1b[38;2;180;220;255m? {question[:panel_width - 2]}\x1b[0m")
        panel_lines.append("\x1b[38;2;120;120;120m  (lädt...)\x1b[0m")
    elif question and bool(game.get("tutor_ask_answered")):
        answer = str(game.get("tutor_ask_answer") or "")
        if answer:
            panel_lines.append("─" * panel_width)
            panel_lines.append(f"\x1b[38;2;180;220;255m? {question[:panel_width - 2]}\x1b[0m")
            words = answer.split()
            row = ""
            for word in words:
                if len(row) + len(word) + 1 > panel_width - 1:
                    panel_lines.append(f" {row}")
                    row = word
                else:
                    row = (row + " " + word).strip()
            if row:
                panel_lines.append(f" {row}")

    # Recent history (last 2 proposals)
    history_raw = game.get("tutorial_propose_history") if isinstance(game.get("tutorial_propose_history"), list) else []
    if len(history_raw) > 1:
        panel_lines.append("─" * panel_width)
        panel_lines.append("\x1b[38;2;120;120;120mVerlauf:\x1b[0m")
        for entry in history_raw[-2:]:
            if not isinstance(entry, dict):
                continue
            txt = str(entry.get("text") or "").strip()
            src = str(entry.get("source") or "?")
            if txt:
                panel_lines.append(f"\x1b[38;2;100;100;100m[{src}] {txt[:panel_width - len(src) - 4]}\x1b[0m")

    # Snakes peer list (T03.05)
    snakes_dict = dict(snakes_raw) if isinstance(snakes_raw, dict) else {}
    online = [sid for sid, s in snakes_dict.items() if isinstance(s, dict) and s.get("active")]
    if len(online) > 1:
        panel_lines.append("─" * panel_width)
        panel_lines.append(f"\x1b[38;2;120;120;120mSnakes online: {len(online)}\x1b[0m")
        local_id = str(game.get("local_snake_id") or "s1")
        for sid in sorted(online)[:4]:
            snap = snakes_dict.get(sid) or {}
            if not isinstance(snap, dict):
                continue
            pseudo = str(snap.get("pseudonym") or sid)
            color_name = str(snap.get("snake_color") or "mint")
            role = str(snap.get("role") or ("player" if snap.get("local") else "tutor"))
            col = _snake_palette(color_name)["head"]
            marker = "●" if sid == local_id else "·"
            panel_lines.append(
                f"\x1b[38;2;{col[0]};{col[1]};{col[2]}m{marker} {pseudo[:12]} [{color_name}/{role}]\x1b[0m"
            )

    # Speed indicator
    speed_level = int(game.get("speed_level") or 3)
    panel_lines.append("─" * panel_width)
    tps = game.get("tps_override") or 18
    panel_lines.append(f"\x1b[38;2;120;120;120mSpeed: {speed_level}/5 · {tps}tps\x1b[0m")

    # Render panel lines into the right side of each output row
    divider_col = split_col
    for row_idx in range(min(height, len(out))):
        # vertical divider
        if row_idx < len(out):
            out[row_idx] = _overlay_at_visible_col(out[row_idx], divider_col, "\x1b[38;2;60;60;80m│\x1b[0m")
        # panel content
        if row_idx < len(panel_lines):
            pcol = divider_col + 2
            raw = _ANSI_STRIP.sub("", panel_lines[row_idx])
            visible = panel_lines[row_idx]
            total_width = split_col + panel_width + 4
            if pcol < total_width and raw:
                # pad to panel width
                pad = max(0, panel_width - len(raw))
                padded = visible + (" " * pad)
                out[row_idx] = _overlay_at_visible_col(out[row_idx], pcol, padded)

    return out


def _overlay_snake_chat_panel(
    lines: list[str],
    game: dict[str, object],
    *,
    split_col: int,
    panel_width: int,
    ai_rows: int,
    height: int,
) -> list[str]:
    """Render Chat/Notes panel below the AI panel in the right column (E01)."""
    out = list(lines)
    if not out or height <= ai_rows:
        return out

    from client_surfaces.operator_tui.chat_state import get_chat_state, sanitize_text

    chat = get_chat_state(dict(game))
    active_ch_id = str(chat.get("active_channel") or "room:main")
    channels = chat.get("channels") or {}
    ch = channels.get(active_ch_id) or {}
    ch_type = str(ch.get("channel_type") or "room")
    display_name = str(ch.get("display_name") or active_ch_id)
    unread_total = sum(int(c.get("unread") or 0) for c in channels.values())
    chat_focus = bool(chat.get("chat_focus"))
    ai_typing = bool(chat.get("ai_typing"))

    panel_lines: list[str] = []

    # Separator between AI panel and Chat panel
    panel_lines.append("═" * panel_width)

    # Header: channel name, visibility, unread
    is_notes = ch_type == "notes"
    header_color = (160, 100, 220) if is_notes else (100, 180, 255)
    focus_marker = "▶" if chat_focus else " "
    visibility_marker = " local-only" if is_notes else ""
    unread_str = f" +{unread_total}" if unread_total > 0 else ""
    header_text = f"{focus_marker}CHAT {display_name[:14]}{visibility_marker}{unread_str}"
    hcol = header_color
    panel_lines.append(
        f"\x1b[38;2;{hcol[0]};{hcol[1]};{hcol[2]}m{header_text[:panel_width]}\x1b[0m"
    )
    if ai_typing:
        panel_lines.append(f"\x1b[38;2;120;120;120m  (AI schreibt...)\x1b[0m")

    panel_lines.append("─" * panel_width)

    # Messages
    msgs: list[dict] = list(ch.get("messages") or [])
    available_rows = max(2, height - ai_rows - len(panel_lines) - 2)  # -2 for input line
    scroll_offset = int(chat.get("scroll_offset") or 0)

    # Build rendered message lines (with word-wrap)
    rendered: list[str] = []
    snakes_raw = game.get("snakes") or {}
    for msg in msgs:
        if not isinstance(msg, dict):
            continue
        sender = str(msg.get("sender_id") or "?")
        sender_kind = str(msg.get("sender_kind") or "user")
        text = sanitize_text(str(msg.get("text") or ""))
        delivery = str(msg.get("delivery_state") or "")

        # Color by sender kind
        if sender_kind == "system":
            line_col = (100, 100, 100)
            prefix = "* "
        elif sender_kind == "ai":
            line_col = (255, 205, 130)
            prefix = f"[ai] "
        else:
            # Try to match snake color
            snap = snakes_raw.get(sender) if isinstance(snakes_raw, dict) else None
            color_name = str(snap.get("snake_color") or "mint") if isinstance(snap, dict) else "mint"
            pal = _snake_palette(color_name)
            line_col = pal["head"]
            short_sender = sender[:8]
            state_mark = "" if delivery in {"sent", "received", ""} else f"[{delivery}]"
            prefix = f"{short_sender}{state_mark}: "

        col_str = f"\x1b[38;2;{line_col[0]};{line_col[1]};{line_col[2]}m"
        # First line has prefix; continuation lines indent
        words = text.split()
        row = prefix
        first = True
        for word in words:
            if len(row) + len(word) + 1 > panel_width - 1:
                if first:
                    rendered.append(f"{col_str}{row}\x1b[0m")
                    first = False
                else:
                    rendered.append(f"{col_str}  {row}\x1b[0m")
                row = word
            else:
                row = (row + " " + word).strip() if row.strip() else word
        remainder = row.strip()
        if remainder:
            if first:
                rendered.append(f"{col_str}{remainder}\x1b[0m")
            else:
                rendered.append(f"{col_str}  {remainder}\x1b[0m")

    # Apply scroll
    total = len(rendered)
    if scroll_offset > 0:
        start = max(0, total - available_rows - scroll_offset)
    else:
        start = max(0, total - available_rows)
    visible_msgs = rendered[start:start + available_rows]
    panel_lines.extend(visible_msgs)

    # Pad to fill panel
    while len(panel_lines) < height - ai_rows - 2:
        panel_lines.append("")

    # Input line
    if chat_focus:
        buf = str(chat.get("chat_input_buffer") or "")
        prompt_map = {"room": "#room>", "direct": "@>", "ai": "@ai>", "notes": "notes>", "system": ">"}
        prompt = prompt_map.get(ch_type, ">")
        input_line = f"\x1b[38;2;200;200;80m{prompt}\x1b[0m {buf[:panel_width - len(prompt) - 2]}_"
        panel_lines.append(input_line)
    else:
        panel_lines.append(f"\x1b[38;2;80;80;80m[c] chat focus\x1b[0m")

    # Render panel lines into right column starting at ai_rows
    divider_col = split_col
    for row_idx_offset, pline in enumerate(panel_lines):
        row_idx = ai_rows + row_idx_offset
        if row_idx >= height or row_idx >= len(out):
            break
        # vertical divider
        out[row_idx] = _overlay_at_visible_col(out[row_idx], divider_col, "\x1b[38;2;60;60;80m│\x1b[0m")
        pcol = divider_col + 2
        raw = _ANSI_STRIP.sub("", pline)
        total_width = split_col + panel_width + 4
        if pcol < total_width and raw:
            pad = max(0, panel_width - len(raw))
            padded = pline + (" " * pad)
            out[row_idx] = _overlay_at_visible_col(out[row_idx], pcol, padded)

    return out


def _overlay_snake_chat_unread(
    lines: list[str],
    game: dict[str, object],
    *,
    split_col: int,
    panel_width: int,
    height: int,
) -> list[str]:
    """Compact unread indicator when chat panel doesn't fit (E01.01)."""
    out = list(lines)
    if not out or height < 1:
        return out

    from client_surfaces.operator_tui.chat_state import get_chat_state

    chat = get_chat_state(dict(game))
    unread = sum(int(c.get("unread") or 0) for c in (chat.get("channels") or {}).values())
    if unread == 0:
        return out

    last_row = min(height - 1, len(out) - 1)
    label = f"chat +{unread}"
    col = (160, 100, 220)
    text = f"\x1b[38;2;{col[0]};{col[1]};{col[2]}m{label}\x1b[0m"
    pcol = split_col + 2
    out[last_row] = _overlay_at_visible_col(out[last_row], pcol, text)
    return out


def _overlay_snake_score_header(lines: list[str], game: dict[str, object], *, width: int) -> list[str]:
    """Overlay score and highscore in the top-right area (T01.05)."""
    out = list(lines)
    if not out:
        return out
    score = int(game.get("score") or 0)
    scores_raw = game.get("_scores_cache")
    high = int(scores_raw.get("high") or 0) if isinstance(scores_raw, dict) else 0
    speed_level = int(game.get("speed_level") or 3)
    new_high = score > 0 and score >= high
    col = (255, 200, 80) if new_high else (120, 150, 120)
    label = f"score: {score}  best: {max(score, high)}  speed: {speed_level}/5"
    x = max(0, width - len(label) - 2)
    if len(out) > 0:
        out = _overlay_text(out, x=x, y=0, text=label, color=col)
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
        "mint":   {"head": (170, 255, 210), "body": (96, 215, 165),  "label": (236, 255, 244)},
        "cyan":   {"head": (120, 235, 255), "body": (75, 188, 224),  "label": (220, 248, 255)},
        "violet": {"head": (212, 176, 255), "body": (163, 120, 228), "label": (242, 230, 255)},
        "amber":  {"head": (255, 205, 130), "body": (224, 155, 84),  "label": (255, 238, 202)},
        "rose":   {"head": (255, 170, 200), "body": (222, 110, 156), "label": (255, 230, 240)},
        # extended palette (T03.03)
        "lime":   {"head": (200, 255, 100), "body": (155, 220, 60),  "label": (230, 255, 180)},
        "sky":    {"head": (100, 210, 255), "body": (60, 170, 230),  "label": (200, 240, 255)},
        "coral":  {"head": (255, 160, 120), "body": (230, 110, 80),  "label": (255, 220, 200)},
        "ice":    {"head": (200, 240, 255), "body": (160, 210, 240), "label": (230, 248, 255)},
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
