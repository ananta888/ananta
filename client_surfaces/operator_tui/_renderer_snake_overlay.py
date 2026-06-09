"""Internal sub-module of the Operator TUI renderer.

Extracted from the monolithic client_surfaces.operator_tui/renderer.py to
keep the main module small. This module owns: Snake-specific overlays: fullscreen snake, paused states, message effects, trail positions, frame preview, palette, snake collect/display helpers.

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
from client_surfaces.operator_tui import _renderer_layout as _rl_x
from client_surfaces.operator_tui._renderer_layout import (
    _share_only_nav_mode,
    _RIGHT_PANEL_MIN_WIDTH,
    _RIGHT_PANEL_MAX_WIDTH,
)
from client_surfaces.operator_tui import _renderer_utils as _ru_x
from client_surfaces.operator_tui._renderer_utils import (_highlight_line_range, _overlay_at_visible_col, _overlay_frame_preview, _overlay_text, _visible_char_at, _overlay_snake_score_header, _snake_palette)
# === Module-level state (constants) ===

# === Functions extracted from the original renderer.py ===

def _snake_right_panel_width(width: int) -> int:
    return max(_RIGHT_PANEL_MIN_WIDTH, min(_RIGHT_PANEL_MAX_WIDTH, max(34, width // 3)))



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



def _ansi_color(text: str, color: tuple[int, int, int]) -> str:
    return f"\x1b[38;2;{color[0]};{color[1]};{color[2]}m{text}\x1b[0m"



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



