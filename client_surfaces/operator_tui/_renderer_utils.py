"""Internal sub-module of the Operator TUI renderer.

Extracted from the monolithic client_surfaces.operator_tui/renderer.py to
keep the main module small. This module owns: Low-level rendering helpers: scrollbar chars, ANSI strip, overlay text primitives, slicing/clipping.

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




# === Module-level state (constants) ===

# === Functions extracted from the original renderer.py ===

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



def _splice_inspector_into_chrome(
    out: list[str], inspector: list[str], height: int | None
) -> list[str]:
    """CRPS-007: insert the Profile Inspector lines into the visible chrome.

    Strategy:
    1. Find the first cyan-coloured sender line (the ``s-ai:`` row).
    2. Insert the inspector directly AFTER it.
    3. If a height is set, truncate the LAST N lines (bottom of body /
       scroll indicator) by the inspector's line count so the total height
       stays constant. The user sees the inspector at the top, the body
       shrinks slightly, the scroll indicator stays at the bottom.
    4. If no height is set, just append at the natural splice point.

    The first-line-sender anchor is the same one ``_clip_with_scroll`` uses
    to detect chrome — by splicing directly after it we never interfere
    with the scroll indicator at the bottom.
    """
    if not out:
        return out
    sender_idx = -1
    for idx, line in enumerate(out):
        if "\x1b[38;2;120;180;255m" in line and line.lstrip().startswith("\x1b"):
            sender_idx = idx
            break
    if sender_idx < 0:
        # No sender line found — append at end (fallback).
        out = out + inspector
    else:
        insert_at = sender_idx + 1
        out = out[:insert_at] + inspector + out[insert_at:]

    # Respect a fixed height: trim from the bottom (which holds the body
    # tail and possibly the scroll indicator). Trimming keeps the chrome
    # (title, question, sender, inspector) intact at the top.
    if height and height > 0 and len(out) > height:
        out = out[:height]
    return out



def _truncate_to_height(out: list[str], height: int | None) -> list[str]:
    if not height or height <= 0:
        return out
    if len(out) <= height:
        return out
    return out[:height]



def _clip_with_scroll(
    out: list[str], *, game: dict, height: int | None, width: int
) -> list[str]:
    """Clip *out* to *height* lines, honouring game['chat_long_message_scroll_offset'].

    The pane title, question header and sender row are kept fixed at the top
    (they are the "chrome" of the response and should never scroll away). Only
    the body lines are windowed. A status row at the bottom (showing
    "Zeilen N-M / TOTAL  ↑K ↓K") is appended when there is more body content
    than fits in the remaining room. Without height we return the unmodified
    list (other callers may not need the limit).
    """
    if not height or height <= 0:
        return out
    if len(out) <= height:
        return out

    # Identify the body offset: chrome = everything up to AND INCLUDING the
    # sender row ("  \x1b[38;2;120;180;255ms-ai:\x1b[0m"). Only the answer
    # text below it scrolls. We detect the sender row by the cyan colour
    # sequence; chrome ends just after the first such row.
    body_start = 0
    for idx, line in enumerate(out):
        if "\x1b[38;2;120;180;255m" in line and line.lstrip().startswith("\x1b"):
            body_start = idx + 1
            break
    # If we did not find a sender line, fall back to fixed first 4 lines.
    if body_start == 0 and out and not out[0].startswith("  "):
        body_start = min(4, len(out) - 1)

    chrome = out[:body_start]
    body = out[body_start:]
    # Always reserve one row for the scroll indicator
    available = max(1, height - len(chrome) - 1)

    raw_offset = int(game.get("chat_long_message_scroll_offset") or 0)
    max_offset = max(0, len(body) - available)
    offset = max(0, min(raw_offset, max_offset))
    window = body[offset : offset + available]
    if len(window) < available:
        window = window + [""] * (available - len(window))

    above = offset
    below = len(body) - (offset + available)
    if above <= 0 and below <= 0:
        indicator = ""
    elif above > 0 and below > 0:
        indicator = (
            f"\x1b[2m  ↑{above} ↓{below}  Zeilen {offset+1}-{offset+available}/{len(body)}\x1b[0m"
        )
    elif above > 0:
        indicator = (
            f"\x1b[2m  ↑{above}  Zeilen {offset+1}-{offset+available}/{len(body)}  ↓ Ende\x1b[0m"
        )
    else:
        indicator = (
            f"\x1b[2m  ↑ Anfang  Zeilen 1-{available}/{len(body)}  ↓{below}\x1b[0m"
        )
    return chrome + window + [_clip(indicator, width)]



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



def _clip(value: str, width: int) -> str:
    raw = str(value)
    plain = _ANSI_STRIP.sub("", raw)
    if len(plain) <= width:
        return raw
    return plain[: max(0, width - 3)] + "..."


# === Symbols moved here from snake_overlay to break circular import ===

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


# === Symbols moved here to break circular import ===

def _chat_channel_label(channel_id: str) -> str:
    return {
        "room:main": "#room",
        "ai:tutor": "AI",
        "notes:self": "notes",
        "system": "system",
    }.get(channel_id, channel_id.replace("direct:", "@"))

def _inline_input_with_cursor(text: str, cursor: int, width: int) -> str:
    raw = str(text or "")
    cur = max(0, min(len(raw), int(cursor)))
    rendered = raw[:cur] + "_" + raw[cur:]
    max_w = max(1, int(width))
    if len(rendered) <= max_w:
        return rendered
    start = max(0, min(cur, len(rendered) - max_w))
    return rendered[start:start + max_w]


# === More symbols moved to break circular imports ===

def _pane_title(title: str, focused: bool) -> str:
    if focused:
        return f"{DEFAULT_THEME.focused_open}{title}{DEFAULT_THEME.focused_close}"
    return f" {title} "

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

