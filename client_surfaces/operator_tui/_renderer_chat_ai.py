"""Internal sub-module of the Operator TUI renderer.

Extracted from the monolithic client_surfaces.operator_tui/renderer.py to
keep the main module small. This module owns: Chat and AI-snake renderers: chat ask/chat panel, ai-snake-config, channel selectors, message trail, snake chat overlays.

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
from client_surfaces.operator_tui._renderer_utils import (_overlay_at_visible_col, _snake_palette, _chat_channel_label)
# === Module-level state (constants) ===

# === Functions extracted from the original renderer.py ===

def _is_chat_ask_mode(game: dict) -> bool:
    """True when the chat panel has an active :ask question (waiting or answered).

    In :ask mode the middle pane renders the LLM answer as plain text instead
    of going through the visual viewport (compressed tree, narrow wrap).
    """
    return bool(str(game.get("tutor_ask_question") or "").strip())



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



def _chat_msg_timestamp(msg: dict[str, object]) -> str:
    created_at = msg.get("created_at")
    if not isinstance(created_at, (int, float)):
        return "--:--"
    try:
        return time.strftime("%H:%M", time.localtime(float(created_at)))
    except (OverflowError, OSError, ValueError):
        return "--:--"



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



def _overlay_artifact_chat_compact(lines: list[str], state: OperatorState, *, width: int) -> list[str]:
    """Compact bottom-right artifact-chat overlay.

    Shown when the tutorial AI has an active artifact context (set by a left-click
    or confirmed hover) but the fullscreen snake overlay is NOT active (free_mode=False).
    This lets the user see AI explanations without having to enter snake mode.
    """
    from client_surfaces.operator_tui import _renderer_snake_overlay as _rso_lazy
    _overlay_fullscreen_snake = _rso_lazy._overlay_fullscreen_snake
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
    active_session_name = ""
    try:
        from client_surfaces.operator_tui.chat_state import get_chat_state, get_active_session
        chat_state = get_chat_state(dict(game))
        active_ch_id = str(chat_state.get("active_channel") or "ai:tutor")
        ch = (chat_state.get("channels") or {}).get(active_ch_id) or {}
        unread_count = sum(int(c.get("unread") or 0) for c in (chat_state.get("channels") or {}).values())
        _sess = get_active_session(chat_state)
        active_session_name = str(_sess.get("name") or "") if _sess else ""
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
    sess_prefix = f"\x1b[38;2;100;220;180m[{active_session_name}]\x1b[1;38;2;100;180;255m " if active_session_name else ""
    panel.append(f"{active_col}{sess_prefix}ACTIVE: {active_label}{focus_note}\x1b[0m")
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



