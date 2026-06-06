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
from client_surfaces.operator_tui.keybindings_config import display_for_action, shortcut_tokens_for_area
from client_surfaces.operator_tui.ai_snake_config_view import ai_snake_config_filter_options, ai_snake_config_items, chat_model_option_label
from client_surfaces.operator_tui.chat_long_message import (
    compact_chat_message_text,
    get_render_mode,
    is_showing_chat_long_message,
    long_message_history_rows,
    should_use_middle_view_for_message,
)
from client_surfaces.operator_tui.chat_state import get_active_channel
from client_surfaces.operator_tui.markdown_renderer import render_markdown_lines
from client_surfaces.operator_tui.models import FocusPane, OperatorMode, OperatorState, PanelState
from client_surfaces.operator_tui.read_models import build_goal_rows, build_inspection_detail, build_task_rows
from client_surfaces.operator_tui.sections import SECTIONS, get_section
from client_surfaces.operator_tui.audit_nav import grouped_audit_items, audit_nav_items
from client_surfaces.operator_tui.template_nav import grouped_template_items, template_nav_items
from client_surfaces.operator_tui.theme import DEFAULT_THEME, state_label, state_prefix
from client_surfaces.operator_tui.scroll.scrollbar_renderer import minimal_scroll_indicator, render_scrollbar_column

if TYPE_CHECKING:
    from agent.cli.splash import SplashMachine, SplashState


def _share_only_nav_mode() -> bool:
    return str(os.environ.get("ANANTA_TUI_E2E_SHARE_ONLY_NAV") or "").strip().lower() in {"1", "true", "yes", "on"}


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

    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    browser_active = bool(game.get("center_browser_active"))
    wide_browser_layout = bool(game.get("center_browser_wide_layout")) or (
        str(os.environ.get("ANANTA_TUI_BROWSER_WIDE_LAYOUT") or "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    # Keep legacy layout as default. Wider browser center is opt-in only.
    if browser_active and wide_browser_layout:
        left_width = 12 if width >= 100 else 10
        detail_width = 18 if width >= 100 else 14
    else:
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

    body_height = height - 5 - body_offset
    if len(state.open_tabs) >= 2 and not _share_only_nav_mode():
        tab_line = _tab_bar_line(state, width)
        lines.append(tab_line)
        body_height -= 1
    body_height = max(3, body_height)
    nav_lines = _navigation_lines(state)
    content_lines = _content_lines(state, middle_width, height=body_height)
    detail_lines = _detail_lines(state, detail_width, height=body_height)
    body_start = len(lines)
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
    body_end = len(lines)
    tutorial_dock_lines = _tutorial_propose_dock_lines(state, width)
    if tutorial_dock_lines:
        lines.extend(tutorial_dock_lines)

    lines.append(_rule(width))
    lines.append(_status_line(state, width, splash_state=splash_state))
    lines.append(_command_line(state, width))
    lines.append(_hints_line(state, width))
    if state.show_help or section.id == "help":
        lines.extend(_help_overlay(state, width))
    lines = _overlay_fullscreen_snake(lines, state, width=width, body_start=body_start, body_end=body_end)
    return "\n".join(_clip(line, width) for line in lines)


_LOGO_COLS = 50
_LOGO_COLS_MAX = 72
_LOGO_SEP = " │ "
_RIGHT_PANEL_MIN_WIDTH = 40
_RIGHT_PANEL_MAX_WIDTH = 52


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


def _snake_right_panel_width(width: int) -> int:
    return max(_RIGHT_PANEL_MIN_WIDTH, min(_RIGHT_PANEL_MAX_WIDTH, max(34, width // 3)))


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
        lines.append(
            _clip(
                f"{DEFAULT_THEME.muted_prefix} {display_for_action('snake_toggle_selection', 'Ctrl+X')} markiert Start/Ende.",
                width,
            )
        )
        lines.append(
            _clip(
                f"{DEFAULT_THEME.muted_prefix} {display_for_action('chat_focus', 'Ctrl+E')} Chat-Fokus; "
                f"{display_for_action('toggle_tutorial_ai', 'Ctrl+U')} Auto-Heuristik.",
                width,
            )
        )
        lines.append(
            _clip(
                f"{DEFAULT_THEME.muted_prefix} {display_for_action('toggle_mouse_follow', 'Ctrl+O')} mouse-follow; "
                "Klick bestaetigt Ziel, Scroll waermt Intent.",
                width,
            )
        )
        lines.append(_clip(f"{DEFAULT_THEME.muted_prefix} Freigaben: :snake-access <id> cancel|view|full", width))
    else:
        lines.append(
            _clip(
                f"{DEFAULT_THEME.muted_prefix} {display_for_action('toggle_snake_mode', 'Ctrl+S')} startet Snake-Modus.",
                width,
            )
        )
        lines.append(
            _clip(
                f"{DEFAULT_THEME.muted_prefix} Im Modus: {display_for_action('snake_toggle_selection', 'Ctrl+X')} markieren, "
                f"{display_for_action('snake_replace_selection', 'Ctrl+V')} replace, "
                f"{display_for_action('chat_focus', 'Ctrl+E')} Chat.",
                width,
            )
        )
        lines.append(
            _clip(
                f"{DEFAULT_THEME.muted_prefix} Maus: {display_for_action('toggle_mouse_follow', 'Ctrl+O')} follow; "
                "Klick + Hover aktiviert Kontext-Chat.",
                width,
            )
        )
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
        lines.append(
            _clip(
                f"{DEFAULT_THEME.muted_prefix} {display_for_action('snake_toggle_selection', 'Ctrl+X')}=Markieren, "
                f"{display_for_action('snake_replace_selection', 'Ctrl+V')}=Replace, "
                f"{display_for_action('chat_focus', 'Ctrl+E')}=Chat",
                width,
            )
        )
        lines.append(
            _clip(
                f"{DEFAULT_THEME.muted_prefix} {display_for_action('toggle_mouse_follow', 'Ctrl+O')}=MouseFollow, "
                "Klick=Intent+Chat, Scroll=Intent-Hinweis",
                width,
            )
        )
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
    if _share_only_nav_mode():
        panel_state = (state.panel_states or {}).get("share")
        cursor = DEFAULT_THEME.selected_prefix if state.section_id == "share" else DEFAULT_THEME.idle_prefix
        lines.append(f"{cursor}{state_prefix(panel_state)} Share / Teilnehmer")
        return lines
    nav_focused = state.focus == FocusPane.NAVIGATION
    # T02.04: tutor pointer – blink marker next to target section
    game = state.header_logo_game or {}
    ptr = game.get("tutor_pointer") if isinstance(game.get("tutor_pointer"), dict) else {}
    ptr_target = str(ptr.get("target") or "") if ptr else ""
    ptr_blink = int(ptr.get("blink_frame", 0)) if ptr else 0
    ptr_visible = ptr_blink % 2 == 0  # blink: visible on even frames
    template_payload = dict((state.section_payloads or {}).get("templates") or {})
    template_groups = grouped_template_items(template_payload) if state.section_id == "templates" else []
    template_flat = template_nav_items(template_payload) if state.section_id == "templates" else []
    audit_payload = dict((state.section_payloads or {}).get("audit") or {})
    audit_groups = grouped_audit_items(audit_payload) if state.section_id == "audit" else []
    audit_flat = audit_nav_items(audit_payload) if state.section_id == "audit" else []
    template_base_index = len(SECTIONS)
    template_row_index = template_base_index
    audit_row_index = len(SECTIONS)
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
        if section.id == "templates" and template_groups:
            for group_idx, (group_name, group_rows) in enumerate(template_groups):
                group_branch = "└" if group_idx == len(template_groups) - 1 else "├"
                lines.append(f"   {group_branch}─ {group_name} ({len(group_rows)})")
                for leaf_idx, (_, item) in enumerate(group_rows):
                    leaf_branch = "└" if leaf_idx == len(group_rows) - 1 else "├"
                    child_prefix = "      " if group_idx == len(template_groups) - 1 else "   │  "
                    if nav_focused and template_row_index == state.selected_index:
                        leaf_cursor = DEFAULT_THEME.selected_prefix
                    else:
                        leaf_cursor = DEFAULT_THEME.idle_prefix
                    title = str(item.get("title") or item.get("id") or "?")
                    lines.append(f"{leaf_cursor}{child_prefix}{leaf_branch}─ {title}")
                    template_row_index += 1
        if section.id == "audit" and audit_groups:
            for group_idx, (group_name, group_rows) in enumerate(audit_groups):
                group_branch = "└" if group_idx == len(audit_groups) - 1 else "├"
                lines.append(f"   {group_branch}─ {group_name} ({len(group_rows)})")
                for leaf_idx, (_, item) in enumerate(group_rows):
                    leaf_branch = "└" if leaf_idx == len(group_rows) - 1 else "├"
                    child_prefix = "      " if group_idx == len(audit_groups) - 1 else "   │  "
                    if nav_focused and audit_row_index == state.selected_index:
                        leaf_cursor = DEFAULT_THEME.selected_prefix
                    else:
                        leaf_cursor = DEFAULT_THEME.idle_prefix
                    title = str(item.get("title") or item.get("id") or "Audit")
                    status = str(item.get("status") or "")
                    suffix = "" if not status or status == "ok" else " ⚠"
                    lines.append(f"{leaf_cursor}{child_prefix}{leaf_branch}─ {title}{suffix}")
                    audit_row_index += 1
    history_rows = long_message_history_rows(game)
    if history_rows:
        lines.append("")
        lines.append("  Chat History")
        current_channel = ""
        for offset, entry in enumerate(history_rows):
            channel = str(entry.get("channel_id") or "room:main")
            if channel != current_channel:
                current_channel = channel
                lines.append(f"  ▸ {channel}")
            row_index = len(SECTIONS) + len(template_flat) + len(audit_flat) + offset
            if nav_focused and row_index == state.selected_index:
                cursor = DEFAULT_THEME.selected_prefix
            else:
                cursor = DEFAULT_THEME.idle_prefix
            sender = str(entry.get("sender_kind") or "message")
            preview = str(entry.get("preview") or entry.get("text") or "").replace("\n", " ")
            preview = shorten(preview, width=42, placeholder="...")
            lines.append(f"{cursor}  └─ [{sender}] {preview}")
    return lines


def _content_browser_lines(game: dict, width: int, *, height: int | None = None) -> list[str]:
    """Render Carbonyl browser output via pyte virtual terminal."""
    h = max(3, int(height or 20))
    status = str(game.get("center_browser_status") or "")
    url = str(game.get("center_browser_url") or "")
    error = str(game.get("center_browser_error") or "")
    render_mode = str(
        game.get("center_browser_render_mode")
        or os.environ.get("ANANTA_TUI_BROWSER_RENDER_MODE")
        or "pyte_auto"
    ).strip().lower()
    header = f"\x1b[38;2;80;140;220m[BROWSER]\x1b[0m {url[:max(4, width - 12)]}"

    if status in ("requested", "starting"):
        return [header, "  \x1b[2mstarte carbonyl…\x1b[0m"]
    if status == "error" or error:
        return [header,
                f"  \x1b[38;2;220;80;80m✗ {error[:width - 4]}\x1b[0m",
                "  \x1b[2mCtrl+2 zum Schliessen\x1b[0m"]

    raw_bytes: bytes = bytes(game.get("_browser_frame_bytes") or b"")
    if not raw_bytes:
        return [header, "  \x1b[2mwarte auf Carbonyl-Output…\x1b[0m"]

    def _raw_ansi_lines() -> list[str]:
        try:
            text = raw_bytes[-65536:].decode("utf-8", errors="replace")
        except Exception:
            text = ""
        ansi_lines = text.split("\n")
        visible = ansi_lines[-h:]
        result = [header] + [_clip(line, width) for line in visible]
        while len(result) < h:
            result.append("")
        return result[:h]

    def _raw_fallback_lines() -> list[str]:
        try:
            text = raw_bytes[-32768:].decode("utf-8", errors="replace")
        except Exception:
            text = ""
        plain_lines = _ANSI_STRIP.sub("", text).split("\n")
        visible = [l for l in plain_lines if l.strip()][-h:]
        result = [header] + [_clip(l, width) for l in visible]
        while len(result) < h:
            result.append("")
        return result[:h]

    if render_mode == "raw_ansi":
        return _raw_ansi_lines()

    try:
        import pyte
        screen = pyte.Screen(width, h)
        stream = pyte.ByteStream(screen)
        # Feed the last 128 KB to the virtual terminal
        stream.feed(raw_bytes[-131072:])
        result = [header]
        non_space_chars = 0
        for row_idx in range(h):
            row = screen.buffer.get(row_idx, {})
            cells: list[str] = []
            for col in range(width):
                cell = row.get(col)
                if not cell:
                    cells.append(" ")
                    continue
                ch = str(getattr(cell, "data", " ") or " ")
                if ch == " ":
                    # Carbonyl often paints with colored background spaces.
                    # Preserve visibility by showing a block for styled spaces.
                    bg = str(getattr(cell, "bg", "default") or "default").lower()
                    fg = str(getattr(cell, "fg", "default") or "default").lower()
                    reverse = bool(getattr(cell, "reverse", False))
                    styled_space = (
                        bg not in {"default", "black", "000000", "#000000"}
                        or fg not in {"default"}
                        or reverse
                    )
                    if styled_space:
                        ch = "█"
                cells.append(ch)
            line = "".join(cells).rstrip()
            non_space_chars += sum(1 for ch in line if not ch.isspace())
            result.append(line)
        # Some Carbonyl/frame combinations render as nearly-empty in pyte; prefer raw fallback then.
        if non_space_chars <= max(8, width // 6):
            return _raw_fallback_lines()
        return result[:h + 1]
    except Exception:
        # pyte not available or parse error — fall back to last raw lines
        return _raw_fallback_lines()


def _content_lines(state: OperatorState, width: int, *, height: int | None = None) -> list[str]:
    section = get_section(state.section_id)
    panel_state = (state.panel_states or {}).get(section.id, PanelState.LOADING)
    payload = (state.section_payloads or {}).get(section.id, {})
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    lines = [_pane_title(section.title.upper(), state.focus == FocusPane.CONTENT)]

    if bool(game.get("shortcut_help_middle_open")):
        return _content_shortcut_lines(state, width)
    if bool(game.get("ai_snake_config_open")):
        return _content_ai_snake_config_lines(state, width)
    if bool(game.get("center_browser_active")):
        return _content_browser_lines(game, width, height=height)
    if _is_chat_ask_mode(game) and not bool(game.get("chat_long_message_streaming")):
        ask_lines = _content_chat_plain_ask_lines(state, width, height=height)
        if ask_lines is not None:
            return ask_lines
    if bool(dict(game.get("visual_viewport") or {}).get("enabled")):
        return _content_visual_viewport_lines(state, width)

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
    elif section.id == "templates":
        editor = dict(game.get("template_editor") or {})
        if state.mode is OperatorMode.EDIT and bool(editor.get("active")):
            lines.extend(_templates_editor_content_lines(state, width, viewport_height=height))
        else:
            lines.extend(_templates_content_lines(payload, state, width))
    elif section.id == "audit":
        viewer = dict(game.get("audit_viewer") or {})
        if bool(viewer.get("active")):
            lines.extend(_audit_viewer_content_lines(state, width, viewport_height=height))
        else:
            items = payload.get("items") or []
            if not items:
                lines.append("  (empty)")
                lines.append("  press r to refresh")
            else:
                lines.append("  Audit-Datasets (read-only)")
                for i, item in enumerate(items[:20]):
                    marker = DEFAULT_THEME.selected_prefix if i == state.selected_index else " "
                    title = str(item.get("title") or item.get("id") or "dataset")
                    group = str(item.get("group") or "")
                    summary = str(item.get("summary") or "")
                    status = str(item.get("status") or "")
                    warn = " ⚠" if status and status != "ok" else ""
                    parts = [p for p in (group, summary) if p]
                    lines.append(f"{marker} {title}{warn}" + (f" — {' · '.join(parts)}" if parts else ""))
    elif section.id == "system":
        lines.extend(_system_content_lines(payload))
    elif section.id == "terminal":
        lines.extend(_terminal_content_lines(payload, state, width))
    elif section.id == "share":
        lines.extend(_share_section_content_lines(payload, state, width))
    elif section.id == "help":
        lines.append("")
        lines.extend(_binding_lines(state, width))
    elif section.id == "artifacts" and bool(payload.get("diff3_mode")):
        lines.extend(_diff3_content_lines(payload, width=width))
    elif section.id == "artifacts" and bool(payload.get("planning_track_mode")):
        lines.extend(_planning_track_content_lines(payload, width=width, compact=width < 74))
    elif section.id == "artifacts" and bool(payload.get("mail_mode")):
        lines.extend(_mail_content_lines(payload, width=width, compact=width < 74))
    elif section.id == "artifacts" and bool(payload.get("helpcenter_mode")):
        lines.extend(_helpcenter_content_lines(payload, width=width, compact=width < 74))
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


def _render_vscrollbar_char(bar_char: str) -> str:
    """Colorize a single scrollbar character."""
    _dim = "\x1b[38;2;60;70;90m"
    _thumb = "\x1b[38;2;120;160;200m"
    _r = "\x1b[0m"
    if bar_char in ("█", "|"):
        return f"{_thumb}{bar_char}{_r}"
    if bar_char == " ":
        return " "
    return f"{_dim}{bar_char}{_r}"


def _render_hscrollbar_row(*, content_width: int, viewport_width: int, offset: int, track_width: int) -> str:
    """Build a one-line horizontal scrollbar string."""
    _dim = "\x1b[38;2;60;70;90m"
    _thumb = "\x1b[38;2;120;160;200m"
    _r = "\x1b[0m"
    max_scroll = max(1, content_width - viewport_width)
    tw = max(4, track_width)
    thumb_w = max(1, round(tw * viewport_width / max(1, content_width)))
    thumb_w = min(thumb_w, tw)
    thumb_p = round((tw - thumb_w) * min(offset, max_scroll) / max_scroll)
    chars = []
    for p in range(tw):
        if thumb_p <= p < thumb_p + thumb_w:
            chars.append(f"{_thumb}▬{_r}")
        else:
            chars.append(f"{_dim}─{_r}")
    return f"{_dim}◄{_r}{''.join(chars)}{_dim}►{_r}"


_TPL_THEME = {
    "blueprint": ("\x1b[38;2;130;200;255m", "\x1b[0m"),
    "template":  ("\x1b[38;2;180;230;150m", "\x1b[0m"),
    "seed":      "\x1b[38;2;255;205;100m★\x1b[0m",
    "header":    ("\x1b[38;2;100;120;150m", "\x1b[0m"),
}
_TPL_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\.\[\]-]*$")
_TPL_VAR_COLOR = "\x1b[38;2;130;210;255m"
_TPL_WARN_COLOR = "\x1b[38;2;255;195;90m"
_TPL_ERR_COLOR = "\x1b[38;2;255;120;120m"
_TPL_RESET = "\x1b[0m"


def _templates_content_lines(payload: dict, state: OperatorState, width: int) -> list[str]:
    items: list[dict] = payload.get("items") or []
    bp_count  = int(payload.get("blueprints_count") or 0)
    tpl_count = int(payload.get("templates_count") or 0)
    sys_count = int(payload.get("system_prompts_count") or 0)

    lines: list[str] = []
    summary = f"  {bp_count} blueprints · {tpl_count} templates"
    if sys_count:
        summary += f" · {sys_count} system"
    lines.append(summary)

    sel = state.selected_index
    item_idx = 0

    bp_items  = [it for it in items if it.get("kind") == "blueprint"]
    tpl_items = [it for it in items if it.get("kind") == "template"]
    sys_items = [it for it in items if it.get("kind") == "system_prompt"]

    tree_groups: list[tuple[str, list[dict]]] = []
    if bp_items:
        tree_groups.append(("Blueprints", bp_items))
    if tpl_items:
        tree_groups.append(("Prompt-Templates", tpl_items))
    if sys_items:
        tree_groups.append(("System-Prompts", sys_items))

    if tree_groups:
        lines.append("  Tree:")
    for group_index, (group_name, group_items) in enumerate(tree_groups):
        group_branch = "└" if group_index == len(tree_groups) - 1 else "├"
        lines.append(f"  {group_branch}─ {group_name} ({len(group_items)})")
        for leaf_index, item in enumerate(group_items):
            marker = DEFAULT_THEME.selected_prefix if item_idx == sel else " "
            leaf_branch = "└" if leaf_index == len(group_items) - 1 else "├"
            child_prefix = "     " if group_index == len(tree_groups) - 1 else "  │  "
            title = str(item.get("title") or "")
            if str(item.get("kind") or "") == "blueprint":
                roles = int(item.get("roles_count") or 0)
                arts = int(item.get("artifacts_count") or 0)
                base = str(item.get("base_team_type") or "")
                meta_parts = [f"{roles}r"]
                if arts:
                    meta_parts.append(f"{arts}a")
                if base:
                    meta_parts.append(base)
                if item.get("is_seed"):
                    meta_parts.append("seed")
                meta = f" [{', '.join(meta_parts)}]" if meta_parts else ""
            elif str(item.get("kind") or "") == "system_prompt":
                svc = str(item.get("service") or "")
                meta = f" [{svc}]" if svc else ""
            else:
                preview = str(item.get("prompt_preview") or "").strip()
                meta = f" :: {preview[: max(0, width // 3)]}" if preview else ""
            lines.append(f"{marker}{child_prefix}{leaf_branch}─ {title}{meta}")
            item_idx += 1

    if not items:
        lines.append("  (keine Templates, Blueprints oder System-Prompts)")
        lines.append("  press r to refresh")

    return [_clip(line, width) for line in lines]


def _audit_viewer_content_lines(state: OperatorState, width: int, *, viewport_height: int | None = None) -> list[str]:
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    viewer = dict(game.get("audit_viewer") or {})
    title = str(viewer.get("title") or "dataset")
    group = str(viewer.get("group") or "")
    mode = str(viewer.get("mode") or "read_only")
    text = str(viewer.get("text") or "")
    text_lines = text.splitlines() or [""]
    if mode == "confirm_cleanup":
        header = f"  Audit Cleanup · {title}" + (f" ({group})" if group else "")
        hint = "  Links/Rechts waehlt Button · Enter ausfuehren · Esc abbrechen"
    elif mode == "cleanup_result":
        header = f"  Audit Cleanup Ergebnis · {title}" + (f" ({group})" if group else "")
        hint = "  Enter/Esc schliesst diese Meldung"
    else:
        header = f"  Audit Viewer · {title}" + (f" ({group})" if group else "")
        hint = "  read-only · Esc schließen · ↑/↓ scroll · ←/→ horizontal scroll"
    lines = [
        header,
        hint,
        "",
    ]
    pane_title_rows = 1
    viewer_header_rows = 3
    visible_rows = max(1, int(viewport_height or 24) - pane_title_rows - viewer_header_rows)
    view_line_offset = max(0, int(viewer.get("view_line_offset") or 0))
    view_col_offset = max(0, int(viewer.get("view_col_offset") or 0))
    max_line_width = max((len(line) for line in text_lines), default=0)
    visible_cols = max(8, width - 8)
    max_col_offset = max(0, max_line_width - visible_cols)
    view_col_offset = min(view_col_offset, max_col_offset)
    max_line_offset = max(0, len(text_lines) - visible_rows)
    view_line_offset = min(view_line_offset, max_line_offset)
    end_line = min(len(text_lines), view_line_offset + visible_rows)
    for row_index in range(view_line_offset, end_line):
        line = text_lines[row_index]
        snippet = line[view_col_offset : view_col_offset + visible_cols]
        lines.append(f"  {row_index + 1:>4} {snippet}")
    if end_line < len(text_lines):
        lines.append(f"  ... ({len(text_lines) - end_line} weitere Zeilen)")
    if mode == "confirm_cleanup":
        choice = str(viewer.get("confirm_choice") or "cancel").strip().lower()
        delete_selected = choice == "delete"
        delete_button = ">[ Loeschen ]<" if delete_selected else " [ Loeschen ] "
        cancel_button = ">[ Abbrechen ]<" if not delete_selected else " [ Abbrechen ] "
        lines.append("")
        lines.append(f"  {delete_button}   {cancel_button}")
    return [_clip(line, width) for line in lines]


def _highlight_template_line(line: str) -> tuple[str, int]:
    if not line:
        return "", 0
    out: list[str] = []
    issues = 0
    i = 0
    n = len(line)
    while i < n:
        if line.startswith("{{", i):
            end = line.find("}}", i + 2)
            if end < 0:
                issues += 1
                out.append(f"{_TPL_ERR_COLOR}{line[i:]}{_TPL_RESET}")
                break
            token = line[i : end + 2]
            expr = line[i + 2 : end].strip()
            if expr and _TPL_VAR_NAME_RE.match(expr):
                out.append(f"{_TPL_VAR_COLOR}{token}{_TPL_RESET}")
            else:
                issues += 1
                out.append(f"{_TPL_WARN_COLOR}{token}{_TPL_RESET}")
            i = end + 2
            continue
        if line.startswith("}}", i):
            issues += 1
            out.append(f"{_TPL_ERR_COLOR}}}}}{_TPL_RESET}")
            i += 2
            continue
        out.append(line[i])
        i += 1
    return "".join(out), issues


def _templates_editor_content_lines(state: OperatorState, width: int, *, viewport_height: int | None = None) -> list[str]:
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    editor = dict(game.get("template_editor") or {})
    title = str(editor.get("title") or "template")
    kind = str(editor.get("kind") or "template")
    text = str(editor.get("text") or "")
    cursor = max(0, min(len(text), int(editor.get("cursor") or 0)))
    dirty = bool(editor.get("dirty"))
    text_lines = text.splitlines() or [""]
    lint_total = 0
    lint_by_line: dict[int, int] = {}
    for idx, source in enumerate(text_lines):
        _, issues = _highlight_template_line(source)
        if issues > 0:
            lint_by_line[idx] = issues
            lint_total += issues
    lines = [
        f"  Template Editor · {title} ({kind}){' *' if dirty else ''}",
        (
            "  Ctrl+S speichern · Esc schließen · "
            + (
                f"{_TPL_ERR_COLOR}Lint: {lint_total} issue(s){_TPL_RESET}"
                if lint_total
                else "Lint: ok"
            )
        ),
        "",
    ]

    before = text[:cursor]
    cursor_line = before.count("\n")
    cursor_col = len(before.rsplit("\n", 1)[-1])
    # Use the full visible middle-pane height instead of a fixed 20-line window.
    pane_title_rows = 1  # added by _content_lines
    editor_header_rows = 3
    visible_rows = max(1, int(viewport_height or 24) - pane_title_rows - editor_header_rows)
    start_line = max(0, int(editor.get("view_line_offset") or 0))
    if start_line + visible_rows > len(text_lines):
        start_line = max(0, len(text_lines) - visible_rows)
    end_line = min(len(text_lines), start_line + visible_rows)
    visible_cols = max(8, width - 6)
    max_line_width = max((len(line) for line in text_lines), default=0)
    col_offset = max(0, int(editor.get("view_col_offset") or 0))
    max_col_offset = max(0, max_line_width - visible_cols)
    col_offset = min(col_offset, max_col_offset)
    for row_index in range(start_line, end_line):
        source_line = text_lines[row_index]
        issues_on_line = int(lint_by_line.get(row_index) or 0)
        if row_index == cursor_line:
            line_prefix = ">"
        elif issues_on_line > 0:
            line_prefix = f"{_TPL_ERR_COLOR}!{_TPL_RESET}"
        else:
            line_prefix = " "
        line_view = source_line[col_offset : col_offset + visible_cols]
        rendered_line, _ = _highlight_template_line(line_view)
        if row_index == cursor_line:
            local_col = max(0, min(visible_cols, cursor_col - col_offset))
            rendered_line = _overlay_at_visible_col(
                rendered_line,
                local_col,
                f"{_TPL_WARN_COLOR}|{_TPL_RESET}",
            )
        lines.append(f"{line_prefix} {row_index + 1:>3} {rendered_line}")
    if end_line < len(text_lines):
        lines.append(f"  ... ({len(text_lines) - end_line} weitere Zeilen)")
    return [_clip(line, width) for line in lines]


def _is_chat_ask_mode(game: dict) -> bool:
    """True when the chat panel has an active :ask question (waiting or answered).

    In :ask mode the middle pane renders the LLM answer as plain text instead
    of going through the visual viewport (compressed tree, narrow wrap).
    """
    return bool(str(game.get("tutor_ask_question") or "").strip())


def _latest_ai_message_text(game: dict) -> tuple[str, str] | None:
    """Return (sender_label, plain_text) for the most recent AI message in
    the active chat channel, or None if the channel has no AI message yet.
    The streaming partial (game['llm_streaming_partial']) takes precedence so
    that a still-arriving answer is shown word-for-word as it grows.
    """
    partial = str(game.get("llm_streaming_partial") or "").strip()
    if partial:
        return ("AI-Snake", partial)
    chat = game.get("chat_state") if isinstance(game.get("chat_state"), dict) else {}
    if not chat:
        return None
    channel = get_active_channel(chat) or {}
    if not isinstance(channel, dict):
        return None
    messages = list(channel.get("messages") or [])
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if str(msg.get("sender_kind") or "") == "ai":
            text = str(msg.get("text") or "").strip()
            if text:
                sender_id = str(msg.get("sender_id") or "s-ai")
                return (sender_id, text)
    return None


def _content_chat_plain_ask_lines(
    state: OperatorState, width: int, *, height: int | None = None
) -> list[str] | None:
    """Render the :ask answer as plain text in the middle pane (no visual
    viewport, no tree compression, no markdown rendering). Returns None if
    there is no AI answer to show yet — caller falls back to the visual
    viewport.
    """
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    question = str(game.get("tutor_ask_question") or "").strip()
    latest = _latest_ai_message_text(game)
    if latest is None and not question:
        return None
    sender_label, answer_text = latest if latest is not None else ("AI-Snake", "")

    title = _pane_title("AI-SNAKE ANTWORT", state.focus == FocusPane.CONTENT)
    lines: list[str] = [title]

    # Frage als Header (Echo der User-Eingabe)
    if question:
        q_header = f"  ? {question[: max(4, width - 6)]}"
        lines.append(_clip(q_header, width))
        lines.append("  " + "─" * max(4, width - 4))

    if not answer_text:
        # Noch keine Antwort — Wartezustand
        lines.append("  \x1b[2m…warte auf Antwort\x1b[0m")
        return [_clip(l, width) for l in lines]

    # Plain-Text-Antwort, hart an width gewrappt (kein visuelles Viewport-Wrapping).
    body_width = max(8, width - 2)
    lines.append(f"  \x1b[38;2;120;180;255m{sender_label}:\x1b[0m")
    body: list[str] = []
    for raw_line in answer_text.splitlines() or [""]:
        if not raw_line:
            body.append("")
            continue
        body.extend(_wrap_plain(raw_line, body_width) or [""])
    lines.extend("  " + row for row in body)

    # Optionaler scroll-Footer
    total = len(lines)
    avail = int(height) if height else 0
    if avail and total > avail:
        hidden = total - avail
        lines.append("  " + "─" * max(4, width - 4))
        lines.append(_clip(f"  \x1b[2m({hidden} weitere Zeilen — Scroll mit Shift+Up/Down)\x1b[0m", width))

    return [_clip(l, width) for l in lines]


def _content_visual_viewport_lines(state: OperatorState, width: int) -> list[str]:
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    runtime = dict(game.get("visual_runtime_status") or {})
    title = _pane_title("VISUAL VIEWPORT", state.focus == FocusPane.CONTENT)
    show_doc_diag = str(os.environ.get("ANANTA_TUI_DOC_DIAGNOSTICS") or "").strip().lower() in {"1", "true", "yes", "on"}

    frame_lines = [str(row) for row in (game.get("visual_viewport_frame_lines") or []) if isinstance(row, str)]
    if not frame_lines:
        lines = [title, "  visual runtime aktiv, warte auf frame ..."]
        view = str(runtime.get("active_view") or game.get("visual_viewport_active_view") or "-")
        lines.append(f"  view={view}")
        return [_clip(l, width) for l in lines]

    # Scroll metadata from scene (populated by _sync_visual_viewport_state)
    vp_meta = dict(game.get("visual_viewport_scene_meta") or {})
    content_lines = int(vp_meta.get("content_lines") or 0)
    max_line_width = int(vp_meta.get("max_line_width") or 0)
    v_offset = int(vp_meta.get("scroll_offset") or 0)
    h_offset = int(vp_meta.get("h_offset") or 0)
    n_frame = len(frame_lines)

    # V-scrollbar: content must exceed viewport
    needs_v = content_lines > n_frame and n_frame > 0
    # H-scrollbar: content wider than full panel (frame renders at region.columns ≈ width)
    # Use a tolerance of 2 to avoid triggering due to v-scrollbar char
    needs_h = max_line_width > width + 2 and n_frame > 0

    # H-scrollbar occupies the last frame line
    h_bar_rows = 1 if needs_h else 0
    body_rows = max(0, n_frame - h_bar_rows)

    # Build vertical scrollbar chars for body rows
    v_bar: list[str] = []
    if needs_v:
        v_bar = render_scrollbar_column(
            content_height=content_lines,
            viewport_height=body_rows if body_rows > 0 else n_frame,
            offset=v_offset,
            height=body_rows if body_rows > 0 else n_frame,
        )

    # Compose output lines
    lines: list[str] = [title]
    if show_doc_diag and str(runtime.get("active_view") or game.get("visual_viewport_active_view") or "") == "markdown_mermaid_document":
        diag = dict(game.get("visual_viewport_scene_meta") or {})
        profile = str(diag.get("docs_graphics_profile") or "-")
        backend = str(diag.get("mermaid_renderer_used") or "-")
        fallback_count = int(diag.get("mermaid_fallback_count") or 0)
        cache_hits = int(diag.get("mermaid_cache_hits") or 0)
        cache_misses = int(diag.get("mermaid_cache_misses") or 0)
        cache_ratio = (cache_hits / max(1, cache_hits + cache_misses)) * 100.0
        lines.append(
            _clip(
                f"  docdiag profile={profile} backend={backend} fallback={fallback_count} cache={cache_hits}/{cache_misses} ({cache_ratio:.0f}%)",
                width,
            )
        )

    # Body lines: v-scrollbar overlaid as LAST character of each line (no width change)
    for i in range(body_rows):
        raw = frame_lines[i] if i < len(frame_lines) else ""
        if needs_v:
            bar_ch = v_bar[i] if i < len(v_bar) else " "
            sb = _render_vscrollbar_char(bar_ch)
            # Clip to width-1, then append scrollbar char → total = width chars
            lines.append(_clip(raw, width - 1) + sb)
        else:
            lines.append(_clip(raw, width))

    # Last row: h-scrollbar or remaining frame line
    if needs_h:
        track_w = max(4, width - 4)
        h_row = _render_hscrollbar_row(
            content_width=max_line_width,
            viewport_width=width,
            offset=h_offset,
            track_width=track_w,
        )
        lines.append(_clip(h_row, width))
    elif n_frame > body_rows:
        raw = frame_lines[body_rows] if body_rows < len(frame_lines) else ""
        if needs_v and len(v_bar) > body_rows:
            sb = _render_vscrollbar_char(v_bar[body_rows])
            lines.append(_clip(raw, width - 1) + sb)
        else:
            lines.append(_clip(raw, width))

    # Mode hint (Ctrl+Space toggle): replace last body line, not touching the h-scrollbar
    if is_showing_chat_long_message(game) and body_rows > 0 and len(lines) >= body_rows + 1:
        mode = get_render_mode(game)
        other = "Plain-Text" if mode == "rendered" else "Markdown/Mermaid"
        shortcut = display_for_action("open_long_chat_message", "Ctrl+Space")
        hint_col = "\x1b[38;2;80;120;80m" if mode == "rendered" else "\x1b[38;2;100;100;140m"
        mode_label = "Markdown/Mermaid" if mode == "rendered" else "Plain-Text"
        hint = f"{hint_col}  ▸ {mode_label}  {shortcut}→{other}\x1b[0m"
        # Replace the last body line (index: title=0, body=1..body_rows)
        lines[body_rows] = _clip(hint, width)

    # Status footer
    view = str(runtime.get("active_view") or game.get("visual_viewport_active_view") or "-")
    renderer_name = str(runtime.get("active_renderer") or "-")
    adapter = str(runtime.get("active_adapter") or "-")
    fallback = str(runtime.get("fallback_reason") or "").strip()
    lines.append("")
    lines.append(f"  {view} · {renderer_name} · {adapter}")
    if state.focus == FocusPane.CONTENT:
        up = display_for_action("scroll_line_up", "Shift+Up")
        down = display_for_action("scroll_line_down", "Shift+Down")
        page_up = display_for_action("scroll_page_up", "PgUp")
        page_down = display_for_action("scroll_page_down", "PgDn")
        left = display_for_action("scroll_left", "Shift+Left")
        right = display_for_action("scroll_right", "Shift+Right")
        lines.append(_clip(f"  Scroll {up}/{down} {page_up}/{page_down}  H {left}/{right}  Ctrl+Cursor  Maus/Rad", width))
        if content_lines > 0:
            max_v = max(0, content_lines - max(1, body_rows))
            max_h = max(0, max_line_width - max(1, width))
            lines.append(_clip(f"  pos v:{v_offset}/{max_v} h:{h_offset}/{max_h}", width))
    if fallback:
        lines.append(f"  fallback: {fallback}")
    return [_clip(l, width) for l in lines]


def _content_shortcut_lines(state: OperatorState, width: int) -> list[str]:
    lines = [_pane_title("SHORTCUTS", state.focus == FocusPane.CONTENT)]
    for combo, label in shortcut_tokens_for_area("shortcuts"):
        lines.append(f"  {combo} {label}")
    lines.append("")
    lines.append("  Chat:")
    lines.append("    // im Chat-Input: Shortcut-Ansicht ein/aus")
    lines.append("    /  im Chat-Input: Command-Modus")
    lines.append("    :  normal: Command-Modus")
    return [_clip(line, width) for line in lines]


def _content_ai_snake_config_lines(state: OperatorState, width: int) -> list[str]:
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    combo = dict(game.get("ai_snake_config_combo") or {})
    combo_open = bool(combo.get("open"))
    lines = [_pane_title("AI-SNAKE CONFIG", state.focus == FocusPane.CONTENT)]
    lines.append("  Visual + Chat getrennt konfigurieren")
    lines.append("  Enter/Click oeffnet Auswahl | Auswahl mit Ctrl+J/K")
    lines.append("")
    items = ai_snake_config_items(dict(game))
    selected = max(0, int(state.selected_index))
    for idx, item in enumerate(items):
        marker = DEFAULT_THEME.selected_prefix if idx == selected else " "
        label = str(item.get("label") or item.get("key") or f"item-{idx}")
        key = str(item.get("key") or "")
        value = item.get("value")
        value_text = ("AN" if value else "AUS") if isinstance(value, bool) else str(value or "-")
        if key == "chat_model":
            value_text = chat_model_option_label(dict(game), value_text)
        lines.append(f"{marker} {label:<22} {value_text}")
    if combo_open:
        key = str(combo.get("key") or "")
        item = next((row for row in items if str(row.get("key") or "") == key), {})
        label = str(item.get("label") or key or "-")
        filter_text = str(combo.get("filter") or "")
        cursor = max(0, min(len(filter_text), int(combo.get("filter_cursor") or len(filter_text))))
        filtered, filter_error = ai_snake_config_filter_options(dict(game), key=key, regex_filter=filter_text)
        selected_option = max(0, min(max(0, len(filtered) - 1), int(combo.get("selected_option") or 0)))
        lines.append("")
        lines.append(f"  COMBOBOX: {label}")
        lines.append("  Regex-Filter (Enter uebernimmt Eingabe):")
        lines.append("  > " + _inline_input_with_cursor(filter_text, cursor, max(8, width - 6)))
        if filter_error:
            lines.append(f"  ! {filter_error}")
        if not filtered:
            lines.append("    (keine Treffer)")
        else:
            for idx, option in enumerate(filtered[:10]):
                marker = ">" if idx == selected_option else " "
                option_text = chat_model_option_label(dict(game), option) if key == "chat_model" else option
                lines.append(f"  {marker} {option_text}")
        lines.append("  Up/Down oder Click waehlt Option | Esc schliesst Auswahl")
    lines.append("")
    lines.append(f"  {display_for_action('toggle_ai_snake_config', 'Ctrl+A')} Config ein/aus")
    lines.append(f"  {display_for_action('toggle_tutorial_ai', 'Ctrl+U')} Visual AI hart an/aus")
    return [_clip(line, width) for line in lines]


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


def _share_section_content_lines(payload: dict, state: OperatorState, width: int) -> list[str]:
    from client_surfaces.operator_tui.share_menu import share_section_lines
    # Live-Status aus game state in payload einbauen: Share-Aktionen laufen im
    # Hintergrund und sollen sofort im Menü sichtbar werden.
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    merged = dict(payload)
    if game.get("oidc_device_flow"):
        merged["oidc_device_flow"] = dict(game["oidc_device_flow"])
    if game.get("share_status_message"):
        merged["share_status_message"] = str(game["share_status_message"])
    if game.get("share_audit_items"):
        merged["share_audit_items"] = list(game.get("share_audit_items") or [])
    active_session = dict(game.get("share_active_session") or {})
    if active_session:
        active_id = str(active_session.get("id") or "")
        # sessions (gesamt)
        sessions = [dict(s) for s in list(merged.get("sessions") or []) if isinstance(s, dict)]
        if active_id:
            sessions = [s for s in sessions if str(s.get("id") or "") != active_id]
        merged["sessions"] = [active_session, *sessions]
        # sessions_mine: aktive Session an erster Stelle
        mine = [dict(s) for s in list(merged.get("sessions_mine") or []) if isinstance(s, dict)]
        if active_id:
            mine = [s for s in mine if str(s.get("id") or "") != active_id]
        merged["sessions_mine"] = [active_session, *mine]
        merged["selected_session"] = active_session
        if active_session.get("participants") and not merged.get("participants"):
            merged["participants"] = list(active_session.get("participants") or [])
    return share_section_lines(merged, width=width, selected_index=state.selected_index)


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


def _detail_lines(state: OperatorState, width: int, *, height: int | None = None) -> list[str]:
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    if bool(game.get("shortcut_help_open")):
        return _context_shortcut_lines(state, width)
    if bool(game.get("active")) and bool(game.get("free_mode")):
        return _snake_ai_chat_detail_lines(state, width, height=max(3, int(height or 18)))
    if height is None:
        return [*_standard_detail_lines(state, width), "", *_chat_detail_lines(state, width)]
    chat_height = max(7, min(int(height), int(height * 0.45)))
    detail_height = max(0, int(height) - chat_height)
    detail_lines = _standard_detail_lines(state, width)[:detail_height]
    if len(detail_lines) < detail_height:
        detail_lines.extend([""] * (detail_height - len(detail_lines)))
    chat_lines = _chat_detail_lines(state, width, max_height=chat_height, bottom_align=True)
    return [_clip(line, width) for line in [*detail_lines, *chat_lines]][: int(height)]


def _standard_detail_lines(state: OperatorState, width: int) -> list[str]:
    section = get_section(state.section_id)
    lines = [_pane_title("DETAIL", state.focus == FocusPane.DETAIL)]
    runtime_lines = _runtime_detail_lines(state, width)
    if runtime_lines:
        lines.extend(runtime_lines)

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
    if section.id == "templates":
        lines.append("    :inspect        detail ansicht")
        lines.append("    :tpl new        neues template")
        lines.append("    :bp new         neues blueprint")
        lines.append("    :bp instantiate team erstellen")
    if section.id == "artifacts":
        payload = (state.section_payloads or {}).get(section.id, {})
        if bool(payload.get("diff3_mode")):
            lines.append("    :diff3")
            lines.append("    :diff3 panel <A|B|C> current [--mode <mode>]")
            lines.append("    :diff3 panel <A|B|C> output <output-id>")
            lines.append("    :diff3 panel <A|B|C> ai <review|explain|risk|tests|patch|chat>")
            lines.append("    :diff3 panel <A|B|C> mode <render-mode>")
            lines.append("    :diff3 panel <A|B|C> filter key=value ...")
            lines.append("    :diff3 focus <A|B|C> | :diff3 scroll ...")
            lines.append("    :diff3 sync on|off | :diff3 ai <mode> | :diff3 ai run [mode]")
        if bool(payload.get("goal_artifacts_mode")):
            lines.append("    :goal artifacts [filter ...|clear-filter]")
            lines.append("    :goal sources candidates")
            lines.append("    :goal source grant/revoke/detail ...")
            lines.append("    :artifact provenance <output-id>")
            lines.append("    :artifact prompt <output-id>")
            lines.append("    :artifact config <output-id>")
        if bool(payload.get("planning_track_mode")):
            lines.append("    :plan track [--from-goal <goal-id>]")
            lines.append("    :plan track filter status=... priority=... risk=... type=...")
            lines.append("    :plan track clear-filter")
            lines.append("    :plan track adopt <output-id> | reject <output-id>")
            lines.append("    :plan track execute-next | sync-status <plan-task-id> <status>")
            lines.append("    :plan track diff <left-output-id> <right-output-id>")
            lines.append("    :plan summary doctor <file> | fix <file> | recompute")
        if bool(payload.get("helpcenter_mode")):
            lines.append("    :helpcenter")
            lines.append("    :helpcenter ingest github-failures [--repo owner/repo] [--limit N] [--dry-run]")
            lines.append("    :helpcenter open <analysis-id>")
            lines.append("    :helpcenter suggest-followup [analysis-id]")
        if bool(payload.get("mail_mode")):
            lines.append("    :mail")
            lines.append("    :mail account list|status|create|use|disable|delete")
            lines.append("    :mail mailbox <name> | :mail open <message-id|uid> | :mail load-body [id]")
            lines.append("    :mail search from:... to:... subject:... mailbox:... date:YYYY..YYYY unread:true")
            lines.append("    :mail filter key=value ... | :mail scroll <delta>")
            lines.append("    :mail note add <text> | :mail link-current-to-goal <goal-id>")
            lines.append("    :mail artifact register-current [--scope metadata_only|excerpt|full_body]")
            lines.append("    :mail attachment list|download <filename>|register <filename>")
            lines.append("    :mail export current --format json|text|eml [--include-body --confirm-body] [--goal <goal-id>]")
            lines.append("    :mail grant-current-to-goal <goal-id> [--scope ...] [--confirm-full-body]")
            lines.append("    :mail revoke-grant <goal-id> <grant-id> | :mail context-envelope <goal-id> [--target ...]")
            lines.append("    :mail snake-explain")
    return [_clip(line, width) for line in lines]


def _context_shortcut_lines(state: OperatorState, width: int) -> list[str]:
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    lines = [_pane_title("SHORTCUTS", state.focus == FocusPane.DETAIL)]
    for combo, label in shortcut_tokens_for_area("shortcuts"):
        lines.append(f"  {combo} {label}")
    lines.append("  : Command-Modus (vim-ähnlich)")
    if bool(game.get("free_mode")) or bool(game.get("ui_steering")):
        lines.append("")
        lines.append("  Snake:")
        lines.append("    Left drag: mark area")
        lines.append("    Left click: select/explain")
        lines.append("    Right click: copy mark")
        lines.append(f"    {display_for_action('snake_toggle_selection', 'Ctrl+X')}: select/frame")
        lines.append(f"    {display_for_action('snake_replace_selection', 'Ctrl+V')}: replace command text")
        lines.append(f"    {display_for_action('snake_clear_marks', 'Ctrl+Z')}: clear marks")
        lines.append(f"    {display_for_action('toggle_mouse_follow', 'Ctrl+O')}: mouse follow")
    if bool(game.get("chat_panel_open")) or bool(game.get("artifact_chat_focus")):
        lines.append("")
        lines.append("  Chat:")
        lines.append(f"    {display_for_action('cycle_focus_or_channel', 'Ctrl+W')}: channel")
        lines.append("    Enter: send")
        lines.append("    Esc: leave input")
        lines.append(f"    {display_for_action('clear_chat_input', 'Ctrl+L')}: clear input")
    lines.append("")
    lines.append("  Commands:")
    lines.append("    :help full help")
    lines.append("    :section <id>")
    lines.append("    :refresh")
    return [_clip(line, width) for line in lines]


def _chat_detail_lines(
    state: OperatorState,
    width: int,
    *,
    max_height: int | None = None,
    bottom_align: bool = False,
) -> list[str]:
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    try:
        from client_surfaces.operator_tui.chat_state import get_chat_state, sanitize_text
        chat = get_chat_state(dict(game))
    except Exception:
        chat = {}
        sanitize_text = lambda value: str(value)

    active_ch_id = str(chat.get("active_channel") or "room:main")
    channels = chat.get("channels") if isinstance(chat.get("channels"), dict) else {}
    ch = channels.get(active_ch_id) if isinstance(channels, dict) else {}
    if not isinstance(ch, dict):
        ch = {}
    ch_type_raw = ch.get("channel_type") or "room"
    ch_type = str(getattr(ch_type_raw, "value", ch_type_raw))
    chat_focus = bool(chat.get("chat_focus")) or bool(game.get("artifact_chat_focus"))
    active_label = _chat_channel_label(active_ch_id)
    unread_total = 0
    if isinstance(channels, dict):
        unread_total = sum(int(c.get("unread") or 0) for c in channels.values() if isinstance(c, dict))

    header_lines = [_pane_title("CHAT", state.focus == FocusPane.DETAIL)]
    focus_note = " INPUT" if chat_focus else ""
    header_lines.append(f"  ACTIVE: {active_label}{focus_note}")
    if unread_total:
        header_lines.append(f"  unread: {unread_total}")
    selector = _plain_channel_selector(active_ch_id)
    header_lines.append(f"  {selector}")
    header_lines.append("  " + "-" * max(8, width - 4))

    messages: list[dict] = []
    for msg in list(ch.get("messages") or [])[-10:]:
        if isinstance(msg, dict):
            messages.append(msg)
    partial = str(game.get("llm_streaming_partial") or "").strip()
    if partial and active_ch_id == "ai:tutor":
        messages.append({"sender_kind": "ai", "sender_id": "s-ai", "text": partial, "delivery_state": "streaming"})
    message_lines: list[str] = []
    if not messages:
        message_lines.append("  keine Nachrichten")
    for msg in messages:
        sender_kind = str(msg.get("sender_kind") or "user")
        sender = str(msg.get("sender_id") or "?")
        text = sanitize_text(str(msg.get("text") or ""), max_len=6000)
        if should_use_middle_view_for_message(msg | {"text": text}):
            text = compact_chat_message_text(text)
        line_col = _participant_color(game, sender_id=sender, sender_kind=sender_kind)
        if sender_kind == "system":
            prefix = "* "
        elif sender_kind == "ai":
            prefix = _participant_label(game, sender, fallback="AI") + ": "
        else:
            prefix = _participant_label(game, sender, fallback="Du" if active_ch_id == "ai:tutor" else sender[:8]) + ": "
        for row in _wrap_plain(prefix + text, max(8, width - 2)):
            message_lines.append("  " + _ansi_color(row, line_col))
    if bool(chat.get("ai_typing")) and active_ch_id == "ai:tutor":
        message_lines.append("  " + _chat_timeout_progress_text(game))

    footer_lines = ["  " + "-" * max(8, width - 4)]
    if chat_focus:
        if bool(game.get("artifact_chat_focus")):
            buf = str(game.get("artifact_chat_input") or "")
            cursor = int(game.get("artifact_chat_cursor") or len(buf))
        else:
            buf = str(chat.get("chat_input_buffer") or "")
            cursor = int(chat.get("chat_input_cursor") or len(buf))
        prompt_map = {"room": "#room>", "direct": "@>", "ai": "AI>", "notes": "notes>", "system": ">"}
        prompt = prompt_map.get(ch_type, ">")
        # History indicator: show ▲N if history available (↑/↓ to navigate)
        history = [str(h) for h in (chat.get("chat_input_history") or []) if str(h).strip()]
        hist_note = f" \x1b[2m▲{len(history)}\x1b[0m" if history else ""
        visible = _inline_input_with_cursor(buf, cursor, max(1, width - len(prompt) - 3 - len(hist_note.replace("\x1b[2m","").replace("\x1b[0m",""))))
        footer_lines.append(f"  {prompt}{hist_note} {visible}")
    else:
        footer_lines.append(
            f"  {display_for_action('chat_focus', 'Ctrl+E')} Eingabe  "
            f"{display_for_action('cycle_focus_or_channel', 'Ctrl+W')} Kanal"
        )
    if max_height is not None:
        available = max(0, int(max_height) - len(header_lines) - len(footer_lines))
        min_messages = 2 if bool(game.get("chat_panel_open")) else 1
        if available < min_messages and len(header_lines) > 2:
            trim = min(len(header_lines) - 2, min_messages - available)
            header_lines = header_lines[:-trim]
            available = max(0, int(max_height) - len(header_lines) - len(footer_lines))
        message_lines = message_lines[-available:] if available else []
    lines = [*header_lines, *message_lines, *footer_lines]
    if max_height is not None and bottom_align and len(lines) < int(max_height):
        lines = [""] * (int(max_height) - len(lines)) + lines
    return [_clip(line, width) for line in lines]


def _snake_ai_chat_detail_lines(state: OperatorState, width: int, *, height: int) -> list[str]:
    chat_height = max(8, min(height, int(height * 0.58)))
    ai_height = max(0, height - chat_height)
    ai_lines = _snake_ai_detail_lines(state, width)[:ai_height]
    if len(ai_lines) < ai_height:
        ai_lines.extend([""] * (ai_height - len(ai_lines)))
    chat_lines = _chat_detail_lines(state, width, max_height=chat_height, bottom_align=True)
    return [_clip(line, width) for line in [*ai_lines, *chat_lines]][:height]


def _snake_ai_detail_lines(state: OperatorState, width: int) -> list[str]:
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    depth = str(game.get("tutor_depth_mode") or "overview")
    score = int(game.get("score") or 0)
    scores_raw = game.get("_scores_cache")
    high = int(scores_raw.get("high") or 0) if isinstance(scores_raw, dict) else 0
    speed_level = int(game.get("speed_level") or 3)
    tutorial_enabled = bool(game.get("tutorial_mode"))
    paused = bool(game.get("paused"))
    llm_status = game.get("llm_status") if isinstance(game.get("llm_status"), dict) else {}
    model = str(llm_status.get("model") or "LM")[:12] if llm_status.get("reachable") else "lokal"
    lines = [_pane_title("AI-SNAKE", state.focus == FocusPane.DETAIL)]
    lines.append(f"  tutor-ai {depth}")
    lines.append(f"  score:{score} best:{max(score, high)} spd:{speed_level}/5")
    lines.append(f"  llm:{model}" + (" paused" if paused else ""))
    question = str(game.get("tutor_ask_question") or "")
    if question and not bool(game.get("tutor_ask_answered")):
        lines.append(_clip(f"  ask: loading {question}", width))
    elif question:
        lines.append(_clip(f"  ask: ready {question}", width))
    lines.append("  " + "-" * max(8, width - 4))
    lines.append(f"  {display_for_action('toggle_tutorial_ai', 'Ctrl+U')} Heuristik:{'AN' if tutorial_enabled else 'AUS'}")
    lines.append(f"  {display_for_action('chat_focus', 'Ctrl+E')} Chat-Fokus")
    lines.append(f"  {display_for_action('copy_ai_status', 'Ctrl+I')} Status kopieren")
    lines.append(f"  {display_for_action('toggle_mouse_follow', 'Ctrl+O')} MouseFollow")
    lines.append("  " + "-" * max(8, width - 4))
    runtime_status = str(game.get("ai_snake_runtime_status") or "idle")
    ai_mode = str(game.get("ai_snake_mode") or "lurking_follow")
    lines.append(_clip(f"  mode={ai_mode}", width))
    lines.append(_clip(f"  runtime={runtime_status}", width))
    monitor_log = game.get("ai_snake_monitor_log")
    rows = [dict(item) for item in monitor_log if isinstance(item, dict)] if isinstance(monitor_log, list) else []
    if rows:
        for item in rows[-3:]:
            label = str(item.get("label") or item.get("event") or "event")
            lines.append(_clip(f"  {label}", width))
    return [_clip(line, width) for line in lines]


def _participant_color(game: dict[str, object], *, sender_id: str, sender_kind: str) -> tuple[int, int, int]:
    if sender_kind == "system":
        return (120, 120, 120)
    if sender_kind == "ai" or sender_id == "s-ai":
        return _snake_palette("amber")["head"]
    snakes = game.get("snakes") if isinstance(game.get("snakes"), dict) else {}
    snap = snakes.get(sender_id) if isinstance(snakes, dict) else None
    if isinstance(snap, dict):
        return _snake_palette(str(snap.get("snake_color") or "mint"))["head"]
    local_id = str(game.get("local_snake_id") or "s1")
    if sender_id == local_id:
        return _snake_palette(str(game.get("snake_color") or "mint"))["head"]
    return _snake_palette("cyan")["head"]


def _participant_label(game: dict[str, object], sender_id: str, *, fallback: str) -> str:
    if sender_id == "s-ai":
        return "AI-snake"
    snakes = game.get("snakes") if isinstance(game.get("snakes"), dict) else {}
    snap = snakes.get(sender_id) if isinstance(snakes, dict) else None
    if isinstance(snap, dict):
        return str(snap.get("pseudonym") or fallback)
    local_id = str(game.get("local_snake_id") or "s1")
    if sender_id == local_id:
        return str(game.get("pseudonym") or fallback)
    return fallback


def _ansi_color(text: str, color: tuple[int, int, int]) -> str:
    return f"\x1b[38;2;{color[0]};{color[1]};{color[2]}m{text}\x1b[0m"


def _plain_channel_selector(active_ch_id: str) -> str:
    labels = []
    for cid, label in [("room:main", "#room"), ("ai:tutor", "AI"), ("notes:self", "notes")]:
        labels.append(f"[{label}]" if cid == active_ch_id else label)
    return " ".join(labels)


def _wrap_plain(text: str, width: int) -> list[str]:
    words = str(text or "").split()
    if not words:
        return [""]
    rows: list[str] = []
    row = ""
    for word in words:
        if len(row) + len(word) + 1 > width:
            rows.append(row)
            row = word
        else:
            row = (row + " " + word).strip() if row else word
    if row:
        rows.append(row)
    return rows


def _inline_input_with_cursor(text: str, cursor: int, width: int) -> str:
    raw = str(text or "")
    cur = max(0, min(len(raw), int(cursor)))
    rendered = raw[:cur] + "_" + raw[cur:]
    max_w = max(1, int(width))
    if len(rendered) <= max_w:
        return rendered
    start = max(0, min(cur, len(rendered) - max_w))
    return rendered[start:start + max_w]


def _chat_msg_timestamp(msg: dict[str, object]) -> str:
    created_at = msg.get("created_at")
    if not isinstance(created_at, (int, float)):
        return "--:--"
    try:
        return time.strftime("%H:%M", time.localtime(float(created_at)))
    except (OverflowError, OSError, ValueError):
        return "--:--"


def _runtime_detail_lines(state: OperatorState, width: int) -> list[str]:
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    if not game:
        return []
    lines = ["", "  Runtime:"]
    llm = game.get("llm_status") if isinstance(game.get("llm_status"), dict) else {}
    if llm:
        lm_label = str(llm.get("model") or "LM") if llm.get("reachable") else "local/offline"
        lines.append(f"    LLM: {lm_label[:max(8, width - 10)]}")
    cc_status = str(game.get("codecompass_build_status") or "").strip()
    if cc_status:
        lines.append(f"    CodeCompass: {cc_status}")
    if bool(game.get("chat_panel_open")):
        lines.append("    Chat: panel open")
    try:
        from client_surfaces.operator_tui.chat_state import get_chat_state, unread_total
        unread = unread_total(get_chat_state(dict(game)))
    except Exception:
        unread = 0
    if unread > 0:
        lines.append(f"    Chat unread: {unread}")
    artifact_chat = game.get("artifact_chat_state") if isinstance(game.get("artifact_chat_state"), dict) else {}
    target = artifact_chat.get("active_target") if isinstance(artifact_chat, dict) else None
    if isinstance(target, dict):
        label = str(target.get("label") or target.get("path") or target.get("id") or "artifact")
        lines.append(f"    Active: {label[:max(8, width - 12)]}")
    history = game.get("tutorial_propose_history") if isinstance(game.get("tutorial_propose_history"), list) else []
    if history:
        lines.append("    AI Flow:")
        for entry in history[-2:]:
            if not isinstance(entry, dict):
                continue
            source = str(entry.get("source") or "unknown")
            target_name = str(entry.get("target") or "content")
            text = str(entry.get("text") or "").strip()
            label = f"{source}->{target_name}: {text}" if text else f"{source}->{target_name}"
            lines.append(f"      {label[:max(8, width - 8)]}")
    if len(lines) == 2:
        return []
    return lines


def _planning_track_content_lines(payload: dict, *, width: int, compact: bool) -> list[str]:
    goal_id = str(payload.get("goal_id") or "unknown")
    status = str(payload.get("planning_status") or "idle")
    lifecycle = [str(item) for item in list(payload.get("planning_lifecycle") or []) if str(item).strip()]
    selected_track = dict(payload.get("selected_track") or {})
    selected_output = str(payload.get("selected_output_id") or "")
    active_output = str(payload.get("active_output_id") or "")
    filters = dict(payload.get("task_filters") or {})
    warnings = list(selected_track.get("quality_gate_warnings") or [])
    rows = list(payload.get("track_rows") or [])

    lines = [
        f"  Planning Track: {goal_id}",
        f"  Status: {status}  lifecycle={' -> '.join(lifecycle) if lifecycle else '-'}",
        f"  Selected output: {selected_output or '-'}  active={active_output or '-'}",
        f"  Filters: {', '.join([f'{k}={v}' for k, v in filters.items()]) if filters else 'none'}",
    ]
    if compact:
        lines.append("  --- compact view ---")
    if not selected_track:
        if rows:
            lines.append("  planning outputs available, but selected track payload missing")
        else:
            lines.append("  no planning track outputs")
        return lines

    owner = str(selected_track.get("owner") or "-")
    track = str(selected_track.get("track") or "-")
    goal = str(selected_track.get("goal") or goal_id)
    progress = dict(selected_track.get("progress_summary") or {})
    summary = dict(selected_track.get("tasks_status_summary") or {})
    weighted = dict(selected_track.get("weighted_progress_summary") or {})
    metadata = dict(selected_track.get("derived_summary_metadata") or {})
    type_summary = dict(selected_track.get("tasks_type_summary") or {})
    provenance = dict(selected_track.get("provenance") or {})
    mapping = dict(selected_track.get("task_mapping") or {})
    source_refs = [str(item) for item in list(selected_track.get("source_references") or []) if str(item).strip()]
    context_refs = [str(item) for item in list(selected_track.get("context_references") or []) if str(item).strip()]
    raw_summary_status = str(selected_track.get("summary_recalculation_status") or "not_needed")
    summary_status = (
        "repaired"
        if raw_summary_status == "repaired"
        else ("invalid" if raw_summary_status == "failed" else "fresh")
    )
    repaired_fields = [str(item) for item in list(selected_track.get("repaired_fields") or []) if str(item).strip()]
    lines.append(f"  Header: owner={owner} track={track} goal={goal}")
    lines.append(
        _clip(
            "  Summary: "
            f"state={progress.get('state') or '-'} done={summary.get('by_status', {}).get('done', 0)} "
            f"todo={summary.get('by_status', {}).get('todo', 0)} total={summary.get('total', 0)}",
            width,
        )
    )
    lines.append(
        _clip(
            "  Progress: "
            f"count_based={progress.get('count_based_percent', '-')}% "
            f"weighted={progress.get('weighted_percent', '-')}% "
            f"blocked_count={summary.get('by_status', {}).get('blocked', 0)} "
            f"blocked_weight={weighted.get('blocked_weight', 0)}",
            width,
        )
    )
    lines.append(
        _clip(
            "  Critical path: "
            f"done={summary.get('critical_path', {}).get('done', 0)}/"
            f"{summary.get('critical_path', {}).get('total', 0)} "
            f"remaining={summary.get('critical_path', {}).get('remaining', 0)}",
            width,
        )
    )
    lines.append(
        _clip(
            "  Derived summary: "
            f"status={summary_status} "
            f"source_hash={str(metadata.get('source_hash') or '-')[:12]} "
            f"repaired_fields={','.join(repaired_fields) if repaired_fields else '-'}",
            width,
        )
    )

    milestones = [dict(item) for item in list(selected_track.get("milestones") or []) if isinstance(item, dict)]
    lines.append("  [Milestones]")
    if not milestones:
        lines.append("    - none")
    for milestone in milestones[:8]:
        lines.append(
            _clip(
                f"    {milestone.get('id')} [{milestone.get('status')}] "
                f"{milestone.get('title')} tasks={','.join([str(x) for x in list(milestone.get('task_ids') or [])])}",
                width,
            )
        )

    tasks = [dict(item) for item in list(selected_track.get("tasks_filtered") or []) if isinstance(item, dict)]
    lines.append("  [Tasks]")
    if not tasks:
        lines.append("    - none (filtered)")
    for task in tasks[:16]:
        lines.append(
            _clip(
                f"    {task.get('id')} [{task.get('status')}] {task.get('priority')}/{task.get('risk')} "
                f"type={task.get('type') or '-'} {task.get('title')}",
                width,
            )
        )

    critical = [str(item) for item in list(selected_track.get("critical_path_tasks") or []) if str(item).strip()]
    lines.append(f"  Critical path tasks: {', '.join(critical) if critical else 'none'}")
    by_priority = dict(summary.get("by_priority") or {})
    by_risk = dict(summary.get("by_risk") or {})
    if by_priority:
        lines.append(_clip(f"  Priority breakdown: {', '.join([f'{k}={v}' for k, v in by_priority.items()])}", width))
    if by_risk:
        lines.append(_clip(f"  Risk breakdown: {', '.join([f'{k}={v}' for k, v in by_risk.items()])}", width))
    by_type = dict(type_summary.get("by_type") or {})
    if by_type:
        lines.append("  [Type progress]")
        for key in sorted(by_type.keys())[:8]:
            bucket = dict(by_type.get(key) or {})
            lines.append(
                _clip(
                    f"    {key}: total={bucket.get('total', 0)} done={bucket.get('done', 0)} "
                    f"partial={bucket.get('partial', 0)} blocked={bucket.get('blocked', 0)} "
                    f"progress={bucket.get('progress_percent', 0)}%",
                    width,
                )
            )
    if provenance:
        lines.append(
            _clip(
                f"  Provenance: {provenance.get('provenance_id') or '-'} model={dict(provenance.get('model_ref') or {}).get('model_id') or '-'}",
                width,
            )
        )
    lines.append(_clip(f"  Plan mapping: {len(mapping)} task refs", width))
    lines.append(_clip(f"  Sources: {len(source_refs)} refs  Context: {len(context_refs)} refs", width))

    if warnings:
        lines.append("  [Quality warnings]")
        for warning in warnings[:5]:
            if not isinstance(warning, dict):
                continue
            lines.append(_clip(f"    {warning.get('path')}: {warning.get('reason_code')}", width))

    status_issues = [dict(item) for item in list(payload.get("status_issues") or []) if isinstance(item, dict)]
    if status_issues:
        lines.append("  [Validation issues]")
        for issue in status_issues[:5]:
            lines.append(_clip(f"    {issue.get('path')}: {issue.get('reason_code')}", width))

    diff = dict(payload.get("plan_diff") or {})
    if diff:
        lines.append("  [Plan diff]")
        lines.append(
            f"    {diff.get('left_output_id')} -> {diff.get('right_output_id')} "
            f"new={len(list(diff.get('new_tasks') or []))} "
            f"changed={len(list(diff.get('changed_tasks') or []))} "
            f"removed={len(list(diff.get('removed_tasks') or []))}"
        )
    return lines


def _helpcenter_content_lines(payload: dict, *, width: int, compact: bool) -> list[str]:
    rows = [dict(item) for item in list(payload.get("reports") or []) if isinstance(item, dict)]
    selected_id = str(payload.get("selected_analysis_id") or "")
    selected_report = dict(payload.get("selected_report") or {})
    selected_analysis = dict(payload.get("selected_analysis") or {})
    last_ingest = dict(payload.get("last_ingest") or {})
    lines = [
        "  Helpcenter",
        f"  Reports: {len(rows)} selected={selected_id or '-'}",
    ]
    if last_ingest:
        lines.append(
            _clip(
                f"  Last ingest: repo={last_ingest.get('repo') or '-'} found={last_ingest.get('found', 0)} "
                f"written={last_ingest.get('written', 0)} dry_run={bool(last_ingest.get('dry_run'))}",
                width,
            )
        )
    if not rows:
        lines.append("  no helpcenter reports")
        return lines
    lines.append("  [Reports]")
    preview_rows = rows[:12] if not compact else rows[:6]
    for row in preview_rows:
        marker = "*" if str(row.get("analysis_id") or "") == selected_id else "-"
        lines.append(
            _clip(
                f"  {marker} {row.get('analysis_id')} [{row.get('status')}] "
                f"{row.get('severity')} {row.get('source_kind')} at {row.get('created_at')}",
                width,
            )
        )
    if not selected_report:
        return lines
    lines.append("  [Detail]")
    lines.append(
        _clip(
            f"  Source: kind={selected_report.get('source_kind') or '-'} "
            f"ref={selected_analysis.get('source_refs', ['-'])[0] if isinstance(selected_analysis.get('source_refs'), list) and selected_analysis.get('source_refs') else '-'}",
            width,
        )
    )
    lines.append(_clip(f"  Summary: {selected_analysis.get('failure_summary') or '-'}", width))
    lines.append(
        _clip(
            f"  no_auto_fix={bool(selected_analysis.get('no_auto_fix'))} "
            f"md={selected_report.get('report_ref') or '-'} json={selected_report.get('json_ref') or '-'}",
            width,
        )
    )
    causes = [str(item) for item in list(selected_analysis.get("likely_causes") or []) if str(item).strip()]
    if causes:
        lines.append("  Likely causes:")
        for item in causes[:4]:
            lines.append(_clip(f"    - {item}", width))
    next_steps = [str(item) for item in list(selected_analysis.get("next_steps") or []) if str(item).strip()]
    if next_steps:
        lines.append("  Next steps:")
        for item in next_steps[:4]:
            lines.append(_clip(f"    - {item}", width))
    followup = str(payload.get("followup_suggestion") or "").strip()
    lines.append(_clip(f"  Follow-up suggestion: {followup or '-'}", width))
    return lines


def _mail_content_lines(payload: dict, *, width: int, compact: bool) -> list[str]:
    accounts = [dict(item) for item in list(payload.get("accounts") or []) if isinstance(item, dict)]
    selected_account_id = str(payload.get("selected_account_id") or "")
    mailboxes = [str(item) for item in list(payload.get("mailboxes") or []) if str(item).strip()]
    selected_mailbox = str(payload.get("selected_mailbox") or "")
    filters = dict(payload.get("filters") or {})
    rows = [dict(item) for item in list(payload.get("messages") or []) if isinstance(item, dict)]
    total_messages = int(payload.get("total_messages") or 0)
    selected_key = str(payload.get("selected_message_key") or "")
    detail = dict(payload.get("selected_detail") or {})
    last_search_query = str(payload.get("last_search_query") or "")
    search_refs = [str(item) for item in list(payload.get("search_result_refs") or []) if str(item).strip()]
    notes = [dict(item) for item in list(payload.get("notes") or []) if isinstance(item, dict)]
    linked_goal_refs = [str(item) for item in list(payload.get("linked_goal_refs") or []) if str(item).strip()]
    current_artifact_ref = str(payload.get("current_artifact_ref") or "")
    lines = [
        "  Mail",
        f"  Accounts: {len(accounts)} selected={selected_account_id or '-'} mailbox={selected_mailbox or '-'}",
        f"  Mailboxes: {', '.join(mailboxes) if mailboxes else '-'}",
        f"  Filters: {', '.join([f'{k}={v}' for k, v in filters.items()]) if filters else 'none'}",
        f"  Messages: showing={len(rows)} total={total_messages} offset={int(payload.get('list_offset') or 0)}",
        f"  Search: query={last_search_query or '-'} refs={len(search_refs)}",
        f"  Notes={len(notes)} linked-goals={len(linked_goal_refs)} artifacts={int(payload.get('artifact_count') or 0)}",
    ]
    if accounts:
        lines.append("  [Accounts]")
        for row in accounts[:6]:
            marker = "*" if str(row.get("account_id") or "") == selected_account_id else "-"
            lines.append(
                _clip(
                    f"  {marker} {row.get('display_name') or row.get('account_id')} "
                    f"state={row.get('state')} enabled={bool(row.get('enabled'))}",
                    width,
                )
            )
    if not rows:
        lines.append("  no mail messages")
        return lines
    lines.append("  [Mailbox list]")
    preview = rows[:8] if compact else rows[:14]
    for row in preview:
        ref = dict(row.get("message_ref") or {})
        header = dict(row.get("header_meta") or {})
        marker = "*" if str(ref.get("message_id") or "") == selected_key else "-"
        flags = []
        if bool(header.get("unread")):
            flags.append("unread")
        if bool(header.get("starred")):
            flags.append("starred")
        flags_text = ",".join(flags) or "-"
        lines.append(
            _clip(
                f"  {marker} uid={ref.get('uid')} date={ref.get('date')} from={ref.get('from')} "
                f"subject={header.get('subject') or '-'} flags={flags_text} "
                f"policy={row.get('body_scope') or 'metadata_only'} thread={row.get('thread_count') or 1}",
                width,
            )
        )
    if not detail:
        return lines
    lines.append("  [Detail]")
    detail_ref = dict(detail.get("message_ref") or {})
    detail_header = dict(detail.get("header_meta") or {})
    lines.append(_clip(f"  Message: id={detail_ref.get('message_id') or '-'} uid={detail_ref.get('uid') or '-'}", width))
    lines.append(_clip(f"  Subject: {detail_header.get('subject') or '-'}", width))
    lines.append(
        _clip(
            f"  Body loaded={bool(detail.get('body_loaded'))} "
            f"scope={detail.get('body_scope') or 'metadata_only'} "
            f"redaction={detail.get('redaction_status') or '-'}",
            width,
        )
    )
    lines.append(_clip(f"  Artifact: {current_artifact_ref or '-'}", width))
    body_text = str(detail.get("body_text") or "").strip()
    lines.append(_clip(f"  Body preview: {body_text[:200] if body_text else '(not loaded)'}", width))
    attachments = [dict(item) for item in list(detail.get("attachments") or []) if isinstance(item, dict)]
    lines.append(f"  Attachments: {len(attachments)}")
    for attachment in attachments[:4]:
        lines.append(
            _clip(
                f"    - {attachment.get('filename') or '-'} "
                f"type={attachment.get('content_type') or '-'} "
                f"size={attachment.get('size') or 0} "
                f"danger={bool(attachment.get('danger'))}",
                width,
            )
        )
    downloaded = dict(detail.get("attachment_downloaded") or {})
    if downloaded:
        lines.append(
            _clip(
                f"  Last download: {downloaded.get('filename') or '-'} "
                f"sha256={str(downloaded.get('sha256') or '')[:16]}... "
                f"danger={bool(downloaded.get('dangerous'))}",
                width,
            )
        )
    return lines


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


def _diff3_content_lines(payload: dict, *, width: int) -> list[str]:
    rows = list(payload.get("panel_summaries") or [])
    active_panel = str(payload.get("active_panel") or "A")
    sync = bool(payload.get("sync_scroll"))
    lines = [
        f"  DIFF3: active panel={active_panel} sync={'on' if sync else 'off'}",
    ]
    ai_state = dict(payload.get("ai_panel_state") or {})
    if ai_state:
        lines.append(
            _clip(
                f"  AI: mode={ai_state.get('mode')} status={ai_state.get('status')} "
                f"prompt={ai_state.get('prompt_template_ref')} last={ai_state.get('last_response_ref') or '-'}",
                width,
            )
        )
        findings = list(payload.get("raw_state", {}).get("extensions", {}).get("ai_last_findings") or [])
        if findings:
            lines.append(_clip(f"  AI findings: {findings[0]}", width))
    if not rows:
        lines.append("  (empty diff3 session)")
        return lines

    if width < 58:
        lines.append("  --- tabbed mode (<120 terminal width) ---")
        active = next((row for row in rows if str(row.get("panel_id") or "") == active_panel), rows[0])
        filters = dict(active.get("filters") or {})
        lines.append(
            _clip(
                f"  [{active.get('panel_id')}] {active.get('source_label')} "
                f"mode={active.get('render_mode')} status={active.get('status')}",
                width,
            )
        )
        if filters:
            lines.append(_clip(f"  filters: {', '.join(f'{k}={v}' for k, v in filters.items())}", width))
        stats = dict(active.get("stats") or {})
        if stats:
            lines.append(
                _clip(
                    f"  stats: files={stats.get('files',0)} hunks={stats.get('hunks',0)} truncated={stats.get('truncated',False)}",
                    width,
                )
            )
        return lines

    if width >= 84:
        cols = max(18, (width - 4) // 3)

        def _cell(text: str) -> str:
            return _clip(text, cols).ljust(cols)

        headers: list[str] = []
        details: list[str] = []
        filters_line: list[str] = []
        for row in rows[:3]:
            headers.append(_cell(f"[{row.get('panel_id')}] {row.get('source_label')}"))
            details.append(_cell(f"{row.get('render_mode')} | {row.get('status')}"))
            filters = dict(row.get("filters") or {})
            if filters:
                filters_line.append(_cell(",".join(f"{k}={v}" for k, v in filters.items())))
            else:
                filters_line.append(_cell("filters:none"))
        lines.append("  " + " | ".join(headers))
        lines.append("  " + " | ".join(details))
        lines.append("  " + " | ".join(filters_line))
        return lines

    lines.append("  --- compact diff3 view ---")
    for row in rows:
        filters = dict(row.get("filters") or {})
        filter_label = ",".join(f"{k}={v}" for k, v in filters.items()) if filters else "none"
        lines.append(
            _clip(
                f"  [{row.get('panel_id')}] {row.get('source_label')} "
                f"mode={row.get('render_mode')} status={row.get('status')} filters={filter_label}",
                width,
            )
        )
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
    clipped = _clip(value, width)
    visible_len = len(_ANSI_STRIP.sub("", clipped))
    return clipped + " " * max(0, width - visible_len)


def _status_line(state: OperatorState, width: int, splash_state: str = "") -> str:
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    visual = dict(game.get("visual_runtime_status") or {})
    visual_enabled = bool(dict(game.get("visual_viewport") or {}).get("enabled")) or bool(game.get("visual_viewport_enabled"))
    parts = [
        f"endpoint={state.endpoint}",
        f"auth={state.auth_state}",
        f"focus={state.focus.value}",
        f"mode={state.mode.value}",
    ]
    if visual_enabled:
        visual_prefix = "VVP:on"
        view = str(visual.get("active_view") or game.get("visual_viewport_active_view") or "").strip()
        renderer = str(visual.get("active_renderer") or "").strip()
        adapter = str(visual.get("active_adapter") or "").strip()
        if view:
            visual_prefix += f" vv={view}"
        if renderer:
            visual_prefix += f" vr={renderer}"
        if adapter:
            visual_prefix += f" va={adapter}"
        parts.insert(0, visual_prefix)
        parts.append("VVP:on")
        if view:
            parts.append(f"vv={view}")
        if renderer:
            parts.append(f"vr={renderer}")
        if adapter:
            parts.append(f"va={adapter}")
    parts.append(str(state.status_message or "ready")[:48])
    active_goal_id = str(game.get("active_goal_id") or "").strip()
    if active_goal_id:
        parts.append(f"goal={active_goal_id}")
    llm = game.get("llm_status") if isinstance(game.get("llm_status"), dict) else {}
    if llm:
        parts.append("LLM:on" if llm.get("reachable") else "LLM:local")
    cc_status = str(game.get("codecompass_build_status") or "").strip()
    if cc_status:
        parts.append(f"CC:{cc_status}")
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
    parts.append("VAI:on" if bool(game.get("tutorial_mode")) else "VAI:off")
    if bool(game.get("ai_snake_config_open")):
        parts.append("CFG:on")
    if bool(game.get("chat_panel_open")):
        parts.append("[C]")
    if visual_enabled:
        runtime_error = str(visual.get("runtime_error") or "").strip()
        if runtime_error:
            parts.append("vvp_err")
    try:
        from client_surfaces.operator_tui.chat_state import get_chat_state
        active_chat = str(get_chat_state(dict(game)).get("active_channel") or "room:main")
    except Exception:
        active_chat = ""
    if active_chat and (bool(game.get("chat_panel_open")) or bool(game.get("artifact_chat_focus")) or bool(game.get("free_mode"))):
        parts.append(f"chat={_chat_channel_label(active_chat)}")
    if not bool(game.get("free_mode")):
        try:
            from client_surfaces.operator_tui.chat_state import get_chat_state, unread_total
            unread = unread_total(get_chat_state(dict(game)))
        except Exception:
            unread = 0
        if unread > 0:
            parts.append(f"[chat +{unread}]")
    return _clip(" ".join(parts), width)


def _chat_channel_label(channel_id: str) -> str:
    return {
        "room:main": "#room",
        "ai:tutor": "AI",
        "notes:self": "notes",
        "system": "system",
    }.get(channel_id, channel_id.replace("direct:", "@"))


def _chat_timeout_progress_text(game: dict[str, object]) -> str:
    ask_at = game.get("tutor_ask_at")
    if not isinstance(ask_at, (int, float)):
        return "(AI schreibt...)"
    timeout_s_raw = game.get("tutor_ask_timeout_s")
    if not isinstance(timeout_s_raw, (int, float)):
        return "(AI schreibt...)"
    timeout_s = max(0.1, float(timeout_s_raw))
    elapsed = max(0.0, time.monotonic() - float(ask_at))
    remaining = max(0.0, timeout_s - elapsed)
    progress = max(0, min(100, int((elapsed / timeout_s) * 100)))
    return f"(AI schreibt... timeout in {remaining:0.1f}s [{progress}%])"


def _command_line(state: OperatorState, width: int) -> str:
    import time as _time
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    if state.mode.value != "command":
        # Show last command feedback for 4 seconds after executing a command
        feedback = str(game.get("_cmd_feedback") or "")
        feedback_at = float(game.get("_cmd_feedback_at") or 0.0)
        if feedback and (_time.monotonic() - feedback_at) < 4.0:
            age = _time.monotonic() - feedback_at
            # Fade: bright for first 2s, dim for next 2s
            col = "\x1b[38;2;180;220;255m" if age < 2.0 else "\x1b[2;38;2;120;150;180m"
            return _clip(f"{col}» {feedback}\x1b[0m", width)
        return _clip(f" {state.command_line}", width)
    buf = str(state.command_line or "")
    cursor = int(game.get("command_input_cursor") or len(buf))
    # History indicator: show ▲N if command history available (↑/↓ to navigate)
    cmd_history = game.get("_command_history_count")
    hist_sfx = f" \x1b[2m▲{cmd_history}\x1b[0m" if cmd_history else ""
    hist_len = len(str(cmd_history or "")) + 2 if cmd_history else 0
    visible = _inline_input_with_cursor(buf, cursor, max(1, width - 1 - hist_len))
    return _clip(f":{visible}{hist_sfx}", width)


def _tab_bar_line(state: OperatorState, width: int) -> str:
    """Render the tab bar as a single line. Active tab is shown inverted."""
    tabs = state.open_tabs
    if len(tabs) < 2:
        return ""

    offset = max(0, state.tab_scroll_offset)
    visible = tabs[offset:]
    overflow_left = offset > 0

    parts: list[str] = []
    used_width = 2 if overflow_left else 0  # '‹ '

    overflow_right = False
    visible_count = 0
    for tab in visible:
        seg_plain = f" {tab.label} × "
        sep_w = 1 if visible_count > 0 else 0
        if used_width + sep_w + len(seg_plain) + 2 > width:  # +2 reserve for '›'
            overflow_right = True
            break
        used_width += sep_w + len(seg_plain)
        visible_count += 1

    result = ""
    if overflow_left:
        result += "\x1b[2m‹\x1b[0m "

    for i, tab in enumerate(visible[:visible_count]):
        if i > 0:
            result += "│"
        seg = f" {tab.label} × "
        if tab.id == state.active_tab_id:
            result += f"\x1b[7m{seg}\x1b[0m"
        else:
            result += seg

    if overflow_right:
        result += " \x1b[2m›\x1b[0m"

    plain_len = len(_ANSI_STRIP.sub("", result))
    if plain_len < width:
        result += " " * (width - plain_len)
    return result


def _hints_line(state: OperatorState, width: int) -> str:
    hints = hints_for_mode(state.mode)
    snapshot_copy_shortcut = display_for_action("copy_tui_snapshot", "Ctrl+\\")
    snapshot_save_shortcut = display_for_action("save_tui_snapshot", "Ctrl+_")
    game = state.header_logo_game or {}
    if game.get("active") and (state.focus is FocusPane.HEADER or game.get("ui_steering")):
        chat_raw = game.get("chat_state")
        chat_focus = isinstance(chat_raw, dict) and bool(chat_raw.get("chat_focus"))
        if chat_focus:
            active_ch = ""
            if isinstance(chat_raw, dict):
                active_ch = str(chat_raw.get("active_channel") or "room:main")
            hints = (
                f"[Esc] game  [Enter] send  "
                f"[{display_for_action('cycle_focus_or_channel', 'Ctrl+W')}] channel  "
                f"[{display_for_action('clear_chat_input', 'Ctrl+L')}] clear  "
                f"[{display_for_action('toggle_ai_snake_config', 'Ctrl+A')}] AI-Config  "
                "[:config]  "
                f"[{active_ch}]"
            )
        elif bool(game.get("artifact_chat_focus")):
            hints = (
                f"[Esc] close  [Enter] send  "
                f"[{display_for_action('cycle_focus_or_channel', 'Ctrl+W')}] channel  "
                f"[{display_for_action('clear_chat_input', 'Ctrl+L')}] clear  "
                f"[{display_for_action('toggle_ai_snake_config', 'Ctrl+A')}] AI-Config  "
                "[:config]"
            )
        elif game.get("paused"):
            hints = (
                f"[{display_for_action('snake_pause', 'Ctrl+P')}] Resume  "
                f"[{display_for_action('chat_focus', 'Ctrl+E')}] chat  "
                f"[{display_for_action('toggle_tutorial_ai', 'Ctrl+U')}] Tutorial-AI  "
                f"[{display_for_action('toggle_mouse_follow', 'Ctrl+O')}] MouseFollow  "
                f"[{display_for_action('snake_toggle_frame', 'Ctrl+B')}] Frame  "
                f"[{display_for_action('snake_toggle_selection', 'Ctrl+X')}/"
                f"{display_for_action('snake_replace_selection', 'Ctrl+V')}] Select  "
                f"[{display_for_action('snake_clear_marks', 'Ctrl+Z')}] Clear  "
                f"[{snapshot_copy_shortcut}/{snapshot_save_shortcut}] Snapshot"
            )
        else:
            hints = (
                "[:config]  "
                f"[{display_for_action('toggle_snake_mode', 'Ctrl+S')}] Snake  "
                f"[{display_for_action('toggle_chat_panel', 'Ctrl+G')}] Chat  "
                f"[{display_for_action('chat_focus', 'Ctrl+E')}] Input  "
                f"[{display_for_action('snake_pause', 'Ctrl+P')}] Pause  "
                f"[{display_for_action('toggle_tutorial_ai', 'Ctrl+U')}] Tutorial-AI  "
                f"[{display_for_action('toggle_ai_snake_config', 'Ctrl+A')}] AI-Config  "
                f"[{snapshot_copy_shortcut}/{snapshot_save_shortcut}] Snapshot"
            )
    if len(state.open_tabs) >= 2 and state.mode is OperatorMode.NORMAL and not _share_only_nav_mode():
        tab_hints = (
            f"[{display_for_action('tab_close', 'Ctrl+W')}] Tab×  "
            f"[{display_for_action('tab_next', 'Ctrl+Tab')}] Tab→  "
        )
        hints = tab_hints + hints
    if "Commands:" not in hints and not bool(game.get("active")):
        hints = f"Commands: :refresh :section <id>  {hints}"
    return _clip(hints, width)


def _tutorial_propose_dock_lines(state: OperatorState, width: int) -> list[str]:
    game = state.header_logo_game if isinstance(state.header_logo_game, dict) else {}
    history = game.get("tutorial_propose_history") if isinstance(game.get("tutorial_propose_history"), list) else []
    chat = game.get("artifact_chat_state") if isinstance(game.get("artifact_chat_state"), dict) else {}
    show_tutorial_flow = (bool(game.get("tutorial_mode")) or bool(history) or bool(chat.get("active_target"))) and not bool(game.get("chat_panel_open"))
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


def _overlay_fullscreen_snake(
    lines: list[str],
    state: OperatorState,
    *,
    width: int,
    body_start: int = 0,
    body_end: int | None = None,
) -> list[str]:
    if _share_only_nav_mode():
        return lines
    game = state.header_logo_game or {}
    if not game.get("active") or not game.get("free_mode"):
        return lines
    local_snake = game.get("snake") or []
    if not isinstance(local_snake, list) or not local_snake:
        return lines

    shell = list(lines)
    # Snake bodies are foreground — they render on ALL rows of the terminal.
    out = list(shell)
    if not out:
        return shell

    # Body bounds used only for AI/chat panel placement (panels stay in the body area).
    body_s = max(0, min(len(shell), int(body_start)))
    body_e = len(shell) if body_end is None else max(body_s, min(len(shell), int(body_end)))
    body_h = body_e - body_s

    split_view = width >= 100

    # Snake playfield always uses the full terminal width.
    # Right-side panels may still be rendered, but they are overlays and do not
    # constrain snake coordinates or wrapping.
    play_width = max(1, width)

    def _project_x(raw_x: int) -> int:
        return int(raw_x) % play_width

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
            x = _project_x(int(item[0]))
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
            x = _project_x(int(item[0]))
            y = int(item[1]) % max(1, len(out))
            base = _visible_char_at(out[y], x)
            if base == " ":
                base = "░"
            scol = pal["head"]
            repl = f"\x1b[48;2;{scol[0]};{scol[1]};{scol[2]}m\x1b[38;2;15;15;15m{base}\x1b[0m"
            out[y] = _overlay_at_visible_col(out[y], x, repl)

    anchor = game.get("selection_anchor")
    if isinstance(anchor, (list, tuple)) and len(anchor) == 2:
        x = _project_x(int(anchor[0]))
        y = int(anchor[1]) % max(1, len(out))
        acol = local_pal["label"]
        repl = f"\x1b[38;2;{acol[0]};{acol[1]};{acol[2]}m◎\x1b[0m"
        out[y] = _overlay_at_visible_col(out[y], x, repl)

    # Mouse drag selection: efficient range-based rendering (linear or block)
    _mouse_sel_ranges: list[dict] = []
    _active_range = game.get("mouse_selection_range")
    if isinstance(_active_range, dict):
        _mouse_sel_ranges.append(_active_range)
    for _committed in (game.get("mouse_selection_committed_ranges") or []):
        if isinstance(_committed, dict):
            _mouse_sel_ranges.append(_committed)
    if _mouse_sel_ranges:
        _scol = local_pal["head"]
        _fg = (15, 15, 15)
        for _r in _mouse_sel_ranges:
            _sx = int(_r.get("start_x", 0))
            _sy = int(_r.get("start_y", 0))
            _ex = int(_r.get("end_x", 0))
            _ey = int(_r.get("end_y", 0))
            _mode = str(_r.get("mode", "linear"))
            if _sy > _ey or (_sy == _ey and _sx > _ex):
                _sx, _ex = _ex, _sx
                _sy, _ey = _ey, _sy
            for _ly in range(max(0, _sy), min(_ey + 1, len(out))):
                if _mode == "block":
                    _lx1, _lx2 = sorted([_sx, _ex])
                elif _ly == _sy and _ly == _ey:
                    _lx1, _lx2 = sorted([_sx, _ex])
                elif _ly == _sy:
                    _lx1, _lx2 = _sx, width - 1
                elif _ly == _ey:
                    _lx1, _lx2 = 0, _ex
                else:
                    _lx1, _lx2 = 0, width - 1
                out[_ly] = _highlight_line_range(out[_ly], _lx1, _lx2, _fg, _scol)

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
            x = _project_x(int(pos[0]))
            y = int(pos[1]) % max(1, len(out))
            ch = "●" if idx == 0 else ("◉" if idx < 4 else "·")
            col = pal["head"] if idx == 0 else pal["body"]
            repl = f"\x1b[38;2;{col[0]};{col[1]};{col[2]}m{ch}\x1b[0m"
            out[y] = _overlay_at_visible_col(out[y], x, repl)

        style = str(snapshot.get("message_style") or "trail")
        explicit_effect_flag = snapshot.get("snake_message_effect_enabled", game.get("snake_message_effect_enabled"))
        if explicit_effect_flag is None:
            message_effect_enabled = style != "ticker"
        else:
            message_effect_enabled = bool(explicit_effect_flag)
        message_effect_enabled = message_effect_enabled or (
            os.environ.get("ANANTA_TUI_SNAKE_MESSAGE_EFFECT", "").strip().lower() in {"1", "true", "yes", "on"}
        )
        message = _display_message_for_snake(str(snapshot.get("message") or "")) if message_effect_enabled else ""
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

    # Pause overlay (T01.02) — centered in the body area
    if bool(game.get("paused")):
        pause_cy = body_s + max(0, body_h // 2 - 1)
        out = _overlay_snake_paused_at(out, width=width, center_y=pause_cy)

    # Score / highscore header (T01.05) — in top row of body area
    if not split_view and body_h > 0:
        out = _overlay_snake_score_header(out, game, width=width, row=body_s)

    # Min-size warning (T01.03)
    if width < 40 or body_h < 18:
        warn = "Terminal zu klein für Snake"
        if body_s < len(out):
            out[body_s] = _overlay_text(out[body_s:body_s + 1], x=2, y=0, text=warn, color=(255, 80, 80))[0]

    return out


def _reserve_snake_right_dock(lines: list[str], *, split_col: int, width: int) -> list[str]:
    out = list(lines)
    dock_width = max(0, width - split_col)
    if dock_width <= 0:
        return out
    blank = " " * dock_width
    for idx, line in enumerate(out):
        out[idx] = _overlay_at_visible_col(line, split_col, blank)
    return out


def _overlay_artifact_chat_compact(lines: list[str], state: OperatorState, *, width: int) -> list[str]:
    """Compact bottom-right artifact-chat overlay.

    Shown when the tutorial AI has an active artifact context (set by a left-click
    or confirmed hover) but the fullscreen snake overlay is NOT active (free_mode=False).
    This lets the user see AI explanations without having to enter snake mode.
    """
    game = state.header_logo_game or {}
    if bool(game.get("free_mode")):
        return lines  # already rendered by _overlay_fullscreen_snake
    if bool(game.get("chat_panel_open")):
        return lines

    chat_raw = game.get("artifact_chat_state")
    chat_panel_open = bool(game.get("chat_panel_open"))
    if not isinstance(chat_raw, dict) and not chat_panel_open:
        return lines
    artifact_chat = chat_raw if isinstance(chat_raw, dict) else {}
    active_target = artifact_chat.get("active_target")
    has_target = isinstance(active_target, dict)
    if not has_target and not chat_panel_open:
        return lines

    messages = [m for m in (artifact_chat.get("messages") or []) if isinstance(m, dict)]
    active_ch_id = "ai:tutor"
    unread_count = 0
    try:
        from client_surfaces.operator_tui.chat_state import get_chat_state
        chat_state = get_chat_state(dict(game))
        active_ch_id = str(chat_state.get("active_channel") or "ai:tutor")
        ch = (chat_state.get("channels") or {}).get(active_ch_id) or {}
        unread_count = sum(int(c.get("unread") or 0) for c in (chat_state.get("channels") or {}).values())
        if chat_panel_open or not messages:
            messages = []
            for msg in list(ch.get("messages") or [])[-8:]:
                if not isinstance(msg, dict):
                    continue
                kind = str(msg.get("sender_kind") or "user")
                source = "ai" if kind == "ai" else ("system" if kind == "system" else "user")
                messages.append({"source": source, "text": str(msg.get("text") or "")})
    except Exception:
        pass
    partial = str(game.get("llm_streaming_partial") or "").strip()
    if partial:
        messages.append({"source": "ai", "text": partial})
    focus_active = bool(game.get("artifact_chat_focus"))
    input_text = str(game.get("artifact_chat_input") or "")
    if not messages and not focus_active and not chat_panel_open:
        return lines

    if width <= 60 or len(lines) < 8:
        return lines

    ultra_compact = width < 80
    panel_width = min(46, width // 3) if not ultra_compact else min(30, max(20, width // 3))
    split_col = width - panel_width - 1
    if has_target:
        active_dict = active_target if isinstance(active_target, dict) else {}
        label = str(active_dict.get("label") or active_dict.get("path") or "Artefakt")
    else:
        label = active_ch_id
    label = label[:max(4, panel_width - 14)]

    # How many rows can we show? reserve last 2 rows for status/command
    max_rows = min(3 if ultra_compact else 12, len(lines) - 3)
    start_row = len(lines) - 2 - max_rows

    out = list(lines)

    # Build panel lines: header + messages (word-wrapped)
    panel: list[str] = []
    # header row
    hcol = (255, 205, 130)
    llm_status = game.get("llm_status") if isinstance(game.get("llm_status"), dict) else {}
    if llm_status.get("reachable"):
        llm_label = f"● {str(llm_status.get('model') or 'LM')[:12]}"
    else:
        llm_label = "○ kein LLM" if not llm_status else "○ lokal"
    unread_label = f" +{unread_count}" if unread_count else ""
    panel.append(
        f"\x1b[38;2;{hcol[0]};{hcol[1]};{hcol[2]}m AI · {label}{unread_label} · {llm_label}\x1b[0m"
    )
    active_label = _chat_channel_label(active_ch_id)
    active_col = "\x1b[1;38;2;100;180;255m"
    focus_note = " INPUT" if focus_active else ""
    panel.append(f"{active_col}ACTIVE: {active_label}{focus_note}\x1b[0m")
    panel.append("\x1b[38;2;60;60;80m" + "─" * panel_width + "\x1b[0m")
    if chat_panel_open and not ultra_compact:
        panel.append(_compact_channel_selector(active_ch_id, panel_width))

    message_budget = max(1, max_rows - (5 if focus_active else 2))
    for msg in messages[-message_budget:]:
        source = str(msg.get("source") or "?")
        text = str(msg.get("text") or "").strip()
        if not text:
            continue
        if source == "ai":
            col = "\x1b[38;2;255;205;130m"
            pref = ""
            text = compact_chat_message_text(text)
        elif source == "system":
            col = "\x1b[38;2;100;100;100m"
            pref = "* "
        else:
            col = "\x1b[38;2;160;200;255m"
            pref = "▶ "
        # word-wrap
        words = (pref + text).split()
        row_buf = ""
        for word in words:
            if len(row_buf) + len(word) + 1 > panel_width - 1:
                panel.append(f"{col}{row_buf}\x1b[0m")
                row_buf = word
            else:
                row_buf = (row_buf + " " + word).strip() if row_buf else word
        if row_buf:
            panel.append(f"{col}{row_buf}\x1b[0m")

    if focus_active:
        panel.append("\x1b[38;2;60;60;80m" + "─" * panel_width + "\x1b[0m")
        prompt = "▶ "
        cursor_idx = int(game.get("artifact_chat_cursor") or len(input_text))
        visible_input = _inline_input_with_cursor(input_text, cursor_idx, max(1, panel_width - len(prompt) - 1))
        panel.append(f"\x1b[38;2;220;220;90m{prompt}{visible_input}\x1b[0m")

    # Overlay panel lines into output rows
    for i, pline in enumerate(panel[:max_rows]):
        row_idx = start_row + i
        if row_idx < 0 or row_idx >= len(out):
            continue
        raw_len = len(_ANSI_STRIP.sub("", pline))
        pad = max(0, panel_width - raw_len)
        padded = pline + (" " * pad)
        out[row_idx] = _overlay_at_visible_col(out[row_idx], split_col, "\x1b[38;2;60;60;80m│\x1b[0m")
        out[row_idx] = _overlay_at_visible_col(out[row_idx], split_col + 2, padded)

    return out


def _compact_channel_selector(active_ch_id: str, width: int) -> str:
    parts: list[str] = []
    for cid, label in [("room:main", "#room"), ("ai:tutor", "AI"), ("notes:self", "notes")]:
        if cid == active_ch_id:
            parts.append(f"\x1b[1;38;2;100;180;255m[{label}]\x1b[0m")
        else:
            parts.append(f"\x1b[38;2;120;120;120m {label} \x1b[0m")
    raw = " ".join(parts)
    if len(_ANSI_STRIP.sub("", raw)) <= width:
        return raw
    return raw[:width]


def _overlay_snake_paused(lines: list[str], *, width: int, height: int) -> list[str]:
    """Render PAUSED overlay centered on the game area (T01.02)."""
    out = list(lines)
    label = " [ PAUSED ] "
    cy = max(0, height // 2 - 1)
    cx = max(0, (width // 2) - len(label) // 2)
    _overlay_text(out, x=cx, y=cy, text=label, color=(255, 200, 80))
    hint = "Space zum Fortsetzen"
    hx = max(0, (width // 2) - len(hint) // 2)
    _overlay_text(out, x=hx, y=min(cy + 1, height - 1), text=hint, color=(160, 160, 160))
    return out


def _overlay_snake_paused_at(lines: list[str], *, width: int, center_y: int) -> list[str]:
    """Render PAUSED overlay at an explicit center row (for body-relative positioning)."""
    out = list(lines)
    label = " [ PAUSED ] "
    cy = max(0, min(center_y, len(out) - 2))
    cx = max(0, (width // 2) - len(label) // 2)
    _overlay_text(out, x=cx, y=cy, text=label, color=(255, 200, 80))
    hint = "Space zum Fortsetzen"
    hx = max(0, (width // 2) - len(hint) // 2)
    _overlay_text(out, x=hx, y=min(cy + 1, len(out) - 1), text=hint, color=(160, 160, 160))
    return out


def _overlay_snake_ai_panel(
    lines: list[str],
    game: dict[str, object],
    *,
    split_col: int,
    panel_width: int,
    height: int,
    row_start: int = 0,
    chat_enabled: bool = True,
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

    score = int(game.get("score") or 0)
    scores_raw = game.get("_scores_cache")
    high = int(scores_raw.get("high") or 0) if isinstance(scores_raw, dict) else 0
    speed_level = int(game.get("speed_level") or 3)
    chat_raw = game.get("chat_state")
    chat_focus = isinstance(chat_raw, dict) and bool(chat_raw.get("chat_focus"))
    active_marker = "◉" if not chat_focus else "○"
    tutorial_enabled = bool(game.get("tutorial_mode"))
    status_parts = [f"{active_marker} tutor-ai", depth, f"score:{score}", f"best:{max(score, high)}", f"spd:{speed_level}/5"]
    llm_status = game.get("llm_status") if isinstance(game.get("llm_status"), dict) else {}
    if llm_status.get("reachable"):
        status_parts.append(f"● {str(llm_status.get('model') or 'LM')[:16]}")
    else:
        status_parts.append("○ kein LLM" if not llm_status else "○ lokal")
    codecompass_status = str(game.get("codecompass_build_status") or "")
    if codecompass_status:
        status_parts.append(f"cc:{codecompass_status}")
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
    artifact_chat_status = game.get("artifact_chat_state") if isinstance(game.get("artifact_chat_state"), dict) else {}
    active_target = artifact_chat_status.get("active_target") if isinstance(artifact_chat_status, dict) else None
    if isinstance(active_target, dict):
        target_label = str(active_target.get("label") or active_target.get("path") or active_target.get("id") or "artifact")
        panel_lines.append(f"\x1b[38;2;120;120;120mtarget: {target_label[:panel_width - 8]}\x1b[0m")
    question_status = str(game.get("tutor_ask_question") or "")
    if question_status and not bool(game.get("tutor_ask_answered")):
        panel_lines.append(f"\x1b[38;2;180;220;255mask: loading {question_status[:panel_width - 13]}\x1b[0m")
    elif question_status:
        panel_lines.append(f"\x1b[38;2;180;220;255mask: ready {question_status[:panel_width - 11]}\x1b[0m")
    if paused:
        panel_lines.append("\x1b[38;2;255;200;80mpaused\x1b[0m")
    panel_lines.append("─" * panel_width)
    heur_state = "AN" if tutorial_enabled else "AUS"
    chat_state = "AN" if chat_enabled else "AUS"
    panel_lines.append(
        f"\x1b[38;2;120;180;255mAuto-Heuristik [{display_for_action('toggle_tutorial_ai', 'Ctrl+U')}]: {heur_state}\x1b[0m"
    )
    panel_lines.append(
        f"\x1b[38;2;120;180;255mAI-Chat [{display_for_action('toggle_chat_panel', 'Ctrl+G')}]: {chat_state}\x1b[0m"
    )
    panel_lines.append(
        f"\x1b[38;2;90;90;90mChat-Fokus [{display_for_action('chat_focus', 'Ctrl+E')}]\x1b[0m"
    )
    panel_lines.append(
        f"\x1b[38;2;90;90;90mCopy Status [{display_for_action('copy_ai_status', 'Ctrl+I')}]\x1b[0m"
    )
    panel_lines.append("─" * panel_width)
    ai_mode = str(game.get("ai_snake_mode") or "lurking_follow")
    runtime_status = str(game.get("ai_snake_runtime_status") or "idle")
    provider = str(game.get("ai_snake_provider_preference") or "lmstudio")
    model = str(game.get("ai_snake_provider_model") or "ananta-smoke")
    chat_backend = str(game.get("chat_backend") or "ananta-worker")
    chat_model = str(game.get("chat_backend_model") or "-")
    panel_lines.append(f"\x1b[38;2;180;220;255mSteuerung: mode={ai_mode} runtime={runtime_status}\x1b[0m")
    panel_lines.append(f"\x1b[38;2;120;120;120mProvider: {provider}/{model[:max(6, panel_width - 14)]}\x1b[0m")
    panel_lines.append(f"\x1b[38;2;120;120;120mChat: {chat_backend}/{chat_model[:max(6, panel_width - 10)]}\x1b[0m")
    panel_lines.append("─" * panel_width)
    panel_lines.append("\x1b[38;2;255;205;130mAI-Snake Verlauf:\x1b[0m")
    monitor_log = game.get("ai_snake_monitor_log")
    if isinstance(monitor_log, list) and monitor_log:
        for item in monitor_log[-3:]:
            if not isinstance(item, dict):
                continue
            created_at = item.get("created_at")
            ts = "--:--"
            if isinstance(created_at, (int, float)):
                ts = time.strftime("%H:%M", time.localtime(float(created_at)))
            label = str(item.get("label") or item.get("event") or "event")
            panel_lines.append(f"\x1b[38;2;160;190;230m{ts} {label[:max(4, panel_width - 6)]}\x1b[0m")
    else:
        panel_lines.append("\x1b[38;2;120;120;120m--:-- warte auf Heuristik-Events\x1b[0m")

    divider_col = split_col
    for i in range(min(height, len(out) - row_start)):
        row_idx = row_start + i
        if row_idx < len(out):
            out[row_idx] = _overlay_at_visible_col(out[row_idx], divider_col, "\x1b[38;2;60;60;80m│\x1b[0m")
        if i < len(panel_lines):
            pcol = divider_col + 2
            raw = _ANSI_STRIP.sub("", panel_lines[i])
            if raw:
                pad = max(0, panel_width - len(raw))
                out[row_idx] = _overlay_at_visible_col(out[row_idx], pcol, panel_lines[i] + (" " * pad))
    return out

def _overlay_snake_chat_panel(
    lines: list[str],
    game: dict[str, object],
    *,
    split_col: int,
    panel_width: int,
    ai_rows: int,
    height: int,
    enabled: bool = True,
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
    ch_type_raw = ch.get("channel_type") or "room"
    ch_type = str(getattr(ch_type_raw, "value", ch_type_raw))
    display_name = str(ch.get("display_name") or active_ch_id)
    unread_total = sum(int(c.get("unread") or 0) for c in channels.values())
    chat_focus = bool(chat.get("chat_focus"))
    ai_typing = bool(chat.get("ai_typing"))

    panel_lines: list[str] = []

    # Separator between AI panel and Chat panel
    panel_lines.append("═" * panel_width)

    # Header: all channels, active channel and unread badges.
    focus_marker = "◉" if chat_focus else "○"
    compact = panel_width < 34
    channel_labels: list[str] = []
    for cid, short in [("room:main", "#"), ("ai:tutor", "AI"), ("notes:self", "N")]:
        cdata = channels.get(cid) or {}
        unread = int(cdata.get("unread") or 0)
        badge = f"+{unread}" if unread else ""
        label = short if compact else str(cdata.get("display_name") or cid).replace(" local-only", "")
        text = f"[{label}{(' ' + badge) if badge else ''}]"
        if cid == active_ch_id:
            channel_labels.append(f"\x1b[1;38;2;100;180;255m{text}\x1b[0m")
        elif unread:
            channel_labels.append(f"\x1b[38;2;255;170;80m{text}\x1b[0m")
        else:
            channel_labels.append(f"\x1b[38;2;120;120;120m{text}\x1b[0m")
    active_label = _chat_channel_label(active_ch_id)
    focus_note = " INPUT" if chat_focus else ""
    panel_lines.append(f"\x1b[1;38;2;100;180;255m{focus_marker}ACTIVE: {active_label}{focus_note}\x1b[0m")
    panel_lines.append("CHAT User↔Snake " + " ".join(channel_labels))
    panel_lines.append(
        f"\x1b[38;2;90;90;90mbackend={str(game.get('chat_backend') or 'ananta-worker')} "
        f"model={str(game.get('chat_backend_model') or '-')[:max(4, panel_width - 22)]}\x1b[0m"
    )
    panel_lines.append(
        f"\x1b[38;2;90;90;90m{display_for_action('cycle_focus_or_channel', 'Ctrl+W')}=Kanal "
        f"{display_for_action('chat_focus', 'Ctrl+E')}=Eingabe PgUp/Dn=Scroll Esc=raus\x1b[0m"
    )
    panel_lines.append("\x1b[38;2;90;90;90m:chat backend list|use <id>  :chat model list|use <id>\x1b[0m")
    panel_lines.append(
        f"\x1b[38;2;90;90;90mCopy Chat [{display_for_action('copy_chat_panel', 'Ctrl+C')}] "
        f"Lang [{display_for_action('open_long_chat_message', 'Ctrl+Space')}]\x1b[0m"
    )
    if ai_typing:
        panel_lines.append(f"\x1b[38;2;120;120;120m  {_chat_timeout_progress_text(game)}\x1b[0m")

    panel_lines.append("─" * panel_width)

    if not enabled:
        panel_lines.append("\x1b[38;2;120;120;120mAI-Chat ist deaktiviert.\x1b[0m")
        panel_lines.append(
            f"\x1b[38;2;120;120;120mMit {display_for_action('toggle_chat_panel', 'Ctrl+G')} wieder aktivieren.\x1b[0m"
        )
        panel_lines.append("─" * panel_width)
        panel_lines.append(
            f"\x1b[38;2;80;80;80m[{display_for_action('chat_focus', 'Ctrl+E')}] chat focus\x1b[0m"
        )
        divider_col = split_col
        for row_idx_offset, pline in enumerate(panel_lines):
            row_idx = ai_rows + row_idx_offset
            if row_idx >= height or row_idx >= len(out):
                break
            out[row_idx] = _overlay_at_visible_col(out[row_idx], divider_col, "\x1b[38;2;60;60;80m│\x1b[0m")
            pcol = divider_col + 2
            raw = _ANSI_STRIP.sub("", pline)
            total_width = split_col + panel_width + 4
            if pcol < total_width and raw:
                pad = max(0, panel_width - len(raw))
                padded = pline + (" " * pad)
                out[row_idx] = _overlay_at_visible_col(out[row_idx], pcol, padded)
        return out

    # Messages
    msgs: list[dict] = list(ch.get("messages") or [])
    partial = str(game.get("llm_streaming_partial") or "").strip()
    if partial and active_ch_id == "ai:tutor":
        msgs.append({"sender_id": "s-ai", "sender_kind": "ai", "text": partial, "delivery_state": "streaming"})
    available_rows = max(2, height - ai_rows - len(panel_lines) - 2)  # -2 for input line
    scroll_offset = int(chat.get("scroll_offset") or 0)

    # Build rendered message lines (with word-wrap)
    rendered: list[str] = []
    for msg in msgs:
        if not isinstance(msg, dict):
            continue
        sender = str(msg.get("sender_id") or "?")
        sender_kind = str(msg.get("sender_kind") or "user")
        text = sanitize_text(str(msg.get("text") or ""), max_len=6000)
        if should_use_middle_view_for_message(msg | {"text": text}):
            text = compact_chat_message_text(text)
        delivery = str(msg.get("delivery_state") or "")

        ts = _chat_msg_timestamp(msg)
        # Color by sender kind
        line_col = _participant_color(game, sender_id=sender, sender_kind=sender_kind)
        if sender_kind == "system":
            prefix = f"{ts} * "
        elif sender_kind == "ai":
            prefix = f"{ts} [{_participant_label(game, sender, fallback='AI')}] "
        else:
            state_mark = "" if delivery in {"sent", "received", ""} else f"[{delivery}]"
            prefix = f"{ts} {_participant_label(game, sender, fallback=sender[:8])}{state_mark}: "

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
    # Scrollbar indicator: append compact indicator when scrolled away from bottom
    if total > available_rows and scroll_offset > 0:
        max_s = max(0, total - available_rows)
        indicator = minimal_scroll_indicator(offset=scroll_offset, max_scroll=max_s)
        if indicator:
            indicator_line = f"\x1b[38;2;100;100;120m{indicator}\x1b[0m"
            visible_msgs = list(visible_msgs)
            if visible_msgs:
                visible_msgs[-1] = indicator_line
            else:
                visible_msgs.append(indicator_line)
    panel_lines.extend(visible_msgs)

    # Input line
    if chat_focus:
        buf = str(chat.get("chat_input_buffer") or "")
        prompt_map = {"room": "#room>", "direct": "@>", "ai": "@ai>", "notes": "notes>", "system": ">"}
        prompt = prompt_map.get(ch_type, ">")
        cursor = int(chat.get("chat_input_cursor") or len(buf))
        visible = _inline_input_with_cursor(buf, cursor, max(1, panel_width - len(prompt) - 2))
        input_line = f"\x1b[38;2;200;200;80m{prompt}\x1b[0m {visible}"
        panel_lines.append(input_line)
    else:
        panel_lines.append(
            f"\x1b[38;2;80;80;80m[{display_for_action('chat_focus', 'Ctrl+E')}] chat focus\x1b[0m"
        )

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


def _overlay_snake_score_header(lines: list[str], game: dict[str, object], *, width: int, row: int = 0) -> list[str]:
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

    # Compact heuristic mode badge (T07.05) — shown only if heuristic_mode is set
    heuristic_mode = game.get("heuristic_mode")
    if heuristic_mode:
        _MODE_BADGE = {
            "shadow": "[DSL: shadow]",
            "experimental": "[DSL: exp]",
            "active": "[DSL: active]",
        }
        badge = _MODE_BADGE.get(str(heuristic_mode), f"[DSL: {heuristic_mode}]")
        label = badge + "  " + label

    x = max(0, width - len(label) - 2)
    target_row = max(0, min(row, len(out) - 1))
    if len(out) > 0:
        out = _overlay_text(out, x=x, y=target_row, text=label, color=col)
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


def _highlight_line_range(line: str, x1: int, x2: int, fg: tuple, bg: tuple) -> str:
    """Highlight visible columns x1..x2 inclusive in a single O(len) pass."""
    x1, x2 = sorted((max(0, x1), max(0, x2)))
    plain = _ANSI_STRIP.sub("", line)
    if x1 >= len(plain):
        return line
    x2 = min(x2, len(plain) - 1)
    i = 0
    visible = 0
    n = len(line)
    pos_x1: int | None = None
    pos_x2_end: int | None = None
    while i < n:
        if line[i] == "\x1b":
            m = _ANSI_STRIP.match(line, i)
            if m:
                i = m.end()
                continue
        if visible == x1:
            pos_x1 = i
        if visible == x2 + 1:
            pos_x2_end = i
            break
        visible += 1
        i += 1
    if pos_x1 is None:
        return line
    if pos_x2_end is None:
        pos_x2_end = n
    mid_plain = _ANSI_STRIP.sub("", line[pos_x1:pos_x2_end])
    bg_code = f"\x1b[48;2;{bg[0]};{bg[1]};{bg[2]}m"
    fg_code = f"\x1b[38;2;{fg[0]};{fg[1]};{fg[2]}m"
    return line[:pos_x1] + "\x1b[0m" + bg_code + fg_code + mid_plain + "\x1b[0m" + line[pos_x2_end:]


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
