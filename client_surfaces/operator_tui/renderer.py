"""Operator TUI shell renderer.

This module used to be a single 4000+ LOC file with 88 rendering helpers.
It has been split into focused sub-modules so each can evolve and be
tested in isolation:

  - ``_renderer_utils.py``        - scrollbar chars, ANSI strip, overlay primitives
  - ``_renderer_layout.py``       - header / logo / splash / nav / status / hints
  - ``_renderer_content.py``      - content panes (dashboard, browser, system, etc.)
  - ``_renderer_chat_ai.py``      - chat + AI-snake-config renderers
  - ``_renderer_snake_overlay.py``- fullscreen snake, message effects, trail

The public surface of this module is unchanged. The 7 symbols callers
import (``render_operator_shell`` plus six internal ``_...`` helpers)
are re-exported here as thin delegating wrappers, and every other
function in the original file is also still importable as a wrapper
so monkey-patching in tests continues to work.
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



# === Sub-module delegations (extracted from this file's old monolithic body) ===
from client_surfaces.operator_tui import _renderer_utils as _ru
from client_surfaces.operator_tui import _renderer_layout as _rl
from client_surfaces.operator_tui import _renderer_content as _rc
from client_surfaces.operator_tui import _renderer_chat_ai as _rca
from client_surfaces.operator_tui import _renderer_snake_overlay as _rso

# Re-export the module-level constants that used to live at indent-0 in the
# monolithic file. They are now owned by their respective sub-modules, but
# callers (and tests) that imported them from this module keep working.
from client_surfaces.operator_tui._renderer_layout import (  # noqa: F401
    _LOGO_COLS,
    _LOGO_COLS_MAX,
    _LOGO_SEP,
    _RIGHT_PANEL_MIN_WIDTH,
    _RIGHT_PANEL_MAX_WIDTH,
)
from client_surfaces.operator_tui._renderer_content import (  # noqa: F401
    _TPL_THEME,
    _TPL_VAR_NAME_RE,
    _TPL_VAR_COLOR,
    _TPL_WARN_COLOR,
    _TPL_ERR_COLOR,
    _TPL_RESET,
)

def _share_only_nav_mode():
    return _rl._share_only_nav_mode()

def _trim_visible_leading_spaces(line: str, spaces: int):
    return _ru._trim_visible_leading_spaces(line, spaces)

def _left_align_logo_lines(lines: list[str]):
    return _rl._left_align_logo_lines(lines)

def _logo_cols_for_width(width: int):
    return _rl._logo_cols_for_width(width)

def _snake_right_panel_width(width: int):
    return _rso._snake_right_panel_width(width)

def _load_logo_lines(*, cols: int, color: bool = True, state: OperatorState | None = None):
    return _rl._load_logo_lines(cols=cols, color=color, state=state)

def _assemble_header_lines(logo_lines: list[str], right_lines: list[str], n_rows: int, *, logo_cols: int):
    return _rl._assemble_header_lines(logo_lines, right_lines, n_rows, logo_cols=logo_cols)

def _render_persistent_header(state: OperatorState, width: int):
    return _rl._render_persistent_header(state, width)

def _render_header_snake_lines(state: OperatorState, width: int):
    return _rl._render_header_snake_lines(state, width)

def _render_header_config_lines(state: OperatorState, width: int):
    return _rl._render_header_config_lines(state, width)

def _render_splash_header(splash: SplashMachine, state: OperatorState, width: int):
    return _rl._render_splash_header(splash, state, width)

def _navigation_lines(state: OperatorState):
    return _rl._navigation_lines(state)

def _content_browser_lines(game: dict, width: int, *, height: int | None = None):
    return _rc._content_browser_lines(game, width, height=height)

def _content_lines(state: OperatorState, width: int, *, height: int | None = None):
    return _rc._content_lines(state, width, height=height)

def _render_vscrollbar_char(bar_char: str):
    return _ru._render_vscrollbar_char(bar_char)

def _render_hscrollbar_row(*, content_width: int, viewport_width: int, offset: int, track_width: int):
    return _ru._render_hscrollbar_row(content_width=content_width, viewport_width=viewport_width, offset=offset, track_width=track_width)

def _templates_content_lines(payload: dict, state: OperatorState, width: int):
    return _rc._templates_content_lines(payload, state, width)

def _audit_viewer_content_lines(state: OperatorState, width: int, *, viewport_height: int | None = None):
    return _rc._audit_viewer_content_lines(state, width, viewport_height=viewport_height)

def _highlight_template_line(line: str):
    return _rc._highlight_template_line(line)

def _templates_editor_content_lines(state: OperatorState, width: int, *, viewport_height: int | None = None):
    return _rc._templates_editor_content_lines(state, width, viewport_height=viewport_height)

def _is_chat_ask_mode(game: dict):
    return _rca._is_chat_ask_mode(game)

def _latest_ai_message_text(game: dict):
    return _rso._latest_ai_message_text(game)

def _content_chat_plain_ask_lines(
    state: OperatorState, width: int, *, height: int | None = None
):
    return _rc._content_chat_plain_ask_lines(state, width, height=height)

def _splice_inspector_into_chrome(
    out: list[str], inspector: list[str], height: int | None
):
    return _ru._splice_inspector_into_chrome(out, inspector, height)

def _profile_inspector_lines(game: dict, width: int):
    return _rc._profile_inspector_lines(game, width)

def _truncate_to_height(out: list[str], height: int | None):
    return _ru._truncate_to_height(out, height)

def _clip_with_scroll(
    out: list[str], *, game: dict, height: int | None, width: int
):
    return _ru._clip_with_scroll(out, game=game, height=height, width=width)

def _content_visual_viewport_lines(state: OperatorState, width: int):
    return _rc._content_visual_viewport_lines(state, width)

def _content_shortcut_lines(state: OperatorState, width: int):
    return _rc._content_shortcut_lines(state, width)

def _content_ai_snake_config_lines(state: OperatorState, width: int):
    return _rc._content_ai_snake_config_lines(state, width)

def _dashboard_content_lines(payload: dict, *, state: OperatorState | None = None, width: int = 72):
    return _rc._dashboard_content_lines(payload, state=state, width=width)

def _system_content_lines(payload: dict):
    return _rc._system_content_lines(payload)

def _share_section_content_lines(payload: dict, state: OperatorState, width: int):
    return _rc._share_section_content_lines(payload, state, width)

def _terminal_content_lines(payload: dict, state: OperatorState, width: int):
    return _rc._terminal_content_lines(payload, state, width)

def _detail_lines(state: OperatorState, width: int, *, height: int | None = None):
    return _rc._detail_lines(state, width, height=height)

def _standard_detail_lines(state: OperatorState, width: int):
    return _rc._standard_detail_lines(state, width)

def _context_shortcut_lines(state: OperatorState, width: int):
    return _rc._context_shortcut_lines(state, width)

def _chat_detail_lines(
    state: OperatorState,
    width: int,
    *,
    max_height: int | None = None,
    bottom_align: bool = False,
):
    return _rc._chat_detail_lines(state, width, max_height=max_height, bottom_align=bottom_align)

def _snake_ai_chat_detail_lines(state: OperatorState, width: int, *, height: int):
    return _rc._snake_ai_chat_detail_lines(state, width, height=height)

def _snake_ai_detail_lines(state: OperatorState, width: int):
    return _rc._snake_ai_detail_lines(state, width)

def _participant_color(game: dict[str, object], *, sender_id: str, sender_kind: str):
    return _rca._participant_color(game, sender_id=sender_id, sender_kind=sender_kind)

def _participant_label(game: dict[str, object], sender_id: str, *, fallback: str):
    return _rca._participant_label(game, sender_id, fallback=fallback)

def _ansi_color(text: str, color: tuple[int, int, int]):
    return _rso._ansi_color(text, color)

def _plain_channel_selector(active_ch_id: str):
    return _rca._plain_channel_selector(active_ch_id)

def _wrap_plain(text: str, width: int):
    return _rca._wrap_plain(text, width)

def _inline_input_with_cursor(text: str, cursor: int, width: int):
    return _ru._inline_input_with_cursor(text, cursor, width)

def _chat_msg_timestamp(msg: dict[str, object]):
    return _rca._chat_msg_timestamp(msg)

def _runtime_detail_lines(state: OperatorState, width: int):
    return _rl._runtime_detail_lines(state, width)

def _planning_track_content_lines(payload: dict, *, width: int, compact: bool):
    return _rc._planning_track_content_lines(payload, width=width, compact=compact)

def _helpcenter_content_lines(payload: dict, *, width: int, compact: bool):
    return _rc._helpcenter_content_lines(payload, width=width, compact=compact)

def _mail_content_lines(payload: dict, *, width: int, compact: bool):
    return _rc._mail_content_lines(payload, width=width, compact=compact)

def _goal_artifacts_content_lines(payload: dict, *, width: int, compact: bool):
    return _rc._goal_artifacts_content_lines(payload, width=width, compact=compact)

def _diff3_content_lines(payload: dict, *, width: int):
    return _rc._diff3_content_lines(payload, width=width)

def _help_overlay(state: OperatorState, width: int):
    return _rc._help_overlay(state, width)

def _binding_lines(state: OperatorState, width: int):
    return _rc._binding_lines(state, width)

def _pane_title(title: str, focused: bool):
    return _ru._pane_title(title, focused)

def _cell(lines: list[str], index: int, width: int):
    return _rc._cell(lines, index, width)

def _status_line(state: OperatorState, width: int, splash_state: str = ""):
    return _rl._status_line(state, width, splash_state)

def _chat_channel_label(channel_id: str):
    return _ru._chat_channel_label(channel_id)

def _chat_timeout_progress_text(game: dict[str, object]):
    return _rca._chat_timeout_progress_text(game)

def _command_line(state: OperatorState, width: int):
    return _rl._command_line(state, width)

def _tab_bar_line(state: OperatorState, width: int):
    return _rl._tab_bar_line(state, width)

def _hints_line(state: OperatorState, width: int):
    return _rl._hints_line(state, width)

def _tutorial_propose_dock_lines(state: OperatorState, width: int):
    return _rc._tutorial_propose_dock_lines(state, width)

def _overlay_fullscreen_snake(
    lines: list[str],
    state: OperatorState,
    *,
    width: int,
    body_start: int = 0,
    body_end: int | None = None,
):
    return _rso._overlay_fullscreen_snake(lines, state, width=width, body_start=body_start, body_end=body_end)

def _reserve_snake_right_dock(lines: list[str], *, split_col: int, width: int):
    return _rso._reserve_snake_right_dock(lines, split_col=split_col, width=width)

def _overlay_artifact_chat_compact(lines: list[str], state: OperatorState, *, width: int):
    return _rca._overlay_artifact_chat_compact(lines, state, width=width)

def _compact_channel_selector(active_ch_id: str, width: int):
    return _rca._compact_channel_selector(active_ch_id, width)

def _overlay_snake_paused(lines: list[str], *, width: int, height: int):
    return _rso._overlay_snake_paused(lines, width=width, height=height)

def _overlay_snake_paused_at(lines: list[str], *, width: int, center_y: int):
    return _rso._overlay_snake_paused_at(lines, width=width, center_y=center_y)

def _overlay_snake_ai_panel(
    lines: list[str],
    game: dict[str, object],
    *,
    split_col: int,
    panel_width: int,
    height: int,
    row_start: int = 0,
    chat_enabled: bool = True,
):
    return _rso._overlay_snake_ai_panel(lines, game, split_col=split_col, panel_width=panel_width, height=height, row_start=row_start, chat_enabled=chat_enabled)

def _overlay_snake_chat_panel(
    lines: list[str],
    game: dict[str, object],
    *,
    split_col: int,
    panel_width: int,
    ai_rows: int,
    height: int,
    enabled: bool = True,
):
    return _rca._overlay_snake_chat_panel(lines, game, split_col=split_col, panel_width=panel_width, ai_rows=ai_rows, height=height, enabled=enabled)

def _overlay_snake_chat_unread(
    lines: list[str],
    game: dict[str, object],
    *,
    split_col: int,
    panel_width: int,
    height: int,
):
    return _rca._overlay_snake_chat_unread(lines, game, split_col=split_col, panel_width=panel_width, height=height)

def _overlay_snake_score_header(lines: list[str], game: dict[str, object], *, width: int, row: int = 0):
    return _ru._overlay_snake_score_header(lines, game, width=width, row=row)

def _collect_snakes(game: dict[str, object], *, local_snake_id: str):
    return _rso._collect_snakes(game, local_snake_id=local_snake_id)

def _snake_palette(name: str):
    return _ru._snake_palette(name)

def _display_message_for_snake(value: str):
    return _rso._display_message_for_snake(value)

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
):
    return _rso._overlay_snake_message_effect(out, snake=snake, trail=trail, message=message, width=width, mode=mode, color=color, trail_window=trail_window, trail_speed=trail_speed)

def _message_trail_positions(
    *,
    snake: list[object],
    trail: list[object],
    width: int,
    height: int,
    tail_offset: int,
    needed: int,
):
    return _rso._message_trail_positions(snake=snake, trail=trail, width=width, height=height, tail_offset=tail_offset, needed=needed)

def _overlay_text(
    out: list[str],
    *,
    x: int,
    y: int,
    text: str,
    color: tuple[int, int, int],
):
    return _ru._overlay_text(out, x=x, y=y, text=text, color=color)

def _overlay_frame_preview(
    out: list[str],
    *,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    width: int,
    color: tuple[int, int, int],
):
    return _ru._overlay_frame_preview(out, x1=x1, y1=y1, x2=x2, y2=y2, width=width, color=color)

def _highlight_line_range(line: str, x1: int, x2: int, fg: tuple, bg: tuple):
    return _ru._highlight_line_range(line, x1, x2, fg, bg)

def _overlay_at_visible_col(line: str, col: int, replacement: str):
    return _ru._overlay_at_visible_col(line, col, replacement)

def _visible_char_at(line: str, col: int):
    return _ru._visible_char_at(line, col)

def _rule(width: int):
    return _ru._rule(width)

def _header_rule(width: int, focused: bool = False):
    return _rl._header_rule(width, focused)

def _clip(value: str, width: int):
    return _ru._clip(value, width)



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


