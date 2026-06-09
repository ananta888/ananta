"""Internal sub-module of the Operator TUI renderer.

Extracted from the monolithic client_surfaces.operator_tui/renderer.py to
keep the main module small. This module owns: Layout primitives: header/logo/splash, navigation lines, status/command/tab bar, hint lines.

Public re-exports: the public ``client_surfaces.operator_tui.renderer``
module continues to expose every function via thin delegating wrappers
so existing imports keep working.
"""

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





# === Cross-module imports (resolve symbols from other sub-modules) ===
from client_surfaces.operator_tui import _renderer_utils as _ru_x
from client_surfaces.operator_tui._renderer_utils import (_clip, _overlay_text, _snake_palette, _trim_visible_leading_spaces, _chat_channel_label, _inline_input_with_cursor, _pane_title)
# === Module-level state (constants) ===
_LOGO_COLS = 50
_LOGO_COLS_MAX = 72
_LOGO_SEP = " │ "
_RIGHT_PANEL_MIN_WIDTH = 40
_RIGHT_PANEL_MAX_WIDTH = 52



# === Functions extracted from the original renderer.py ===

def _share_only_nav_mode() -> bool:
    return str(os.environ.get("ANANTA_TUI_E2E_SHARE_ONLY_NAV") or "").strip().lower() in {"1", "true", "yes", "on"}



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
        try:
            from client_surfaces.operator_tui.chat_state import get_chat_state as _gcs2, get_active_session as _gas2
            _sess = _gas2(_gcs2(dict(game)))
            _sess_name = str(_sess.get("name") or "") if _sess else ""
        except Exception:
            _sess_name = ""
        _ch_label = _chat_channel_label(active_chat)
        parts.append(f"[{_sess_name}] {_ch_label}" if _sess_name else f"chat={_ch_label}")
    if not bool(game.get("free_mode")):
        try:
            from client_surfaces.operator_tui.chat_state import get_chat_state, unread_total
            unread = unread_total(get_chat_state(dict(game)))
        except Exception:
            unread = 0
        if unread > 0:
            parts.append(f"[chat +{unread}]")
    return _clip(" ".join(parts), width)



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



def _header_rule(width: int, focused: bool = False) -> str:
    if not focused:
        return "-" * width
    label = " [HEADER] "
    dashes = width - len(label)
    left = dashes // 2
    right = dashes - left
    return "-" * left + label + "-" * right



