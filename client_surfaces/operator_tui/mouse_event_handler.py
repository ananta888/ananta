"""Mouse event handling — coordinate mapping, click zones, event processing.

Extracted from ``mouse_artifact_mixin.py`` so that the mixin stays focused on
artifact intents, inline-open, and navigation.  All functions accept a duck-
typed *self* — call them with the ``MouseArtifactMixin`` instance as first
argument.
"""
from __future__ import annotations

import re
import shutil
import time
from typing import TYPE_CHECKING, Any, cast

from client_surfaces.operator_tui.ai_snake_config_view import ai_snake_config_items
from client_surfaces.operator_tui.app import load_active_section
from client_surfaces.operator_tui.chat_long_message import (
    configure_middle_view_for_history_entry,
    is_showing_chat_long_message,
    long_message_history_rows,
    refresh_rendered_view,
)
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.keybindings_config import display_for_action
from client_surfaces.operator_tui.models import FocusPane, OperatorMode
from client_surfaces.operator_tui.mouse import (
    MouseEventType as NormalizedMouseEventType,
    normalize_mouse_state,
)
from client_surfaces.operator_tui.region_index import RegionTarget, build_region_index
from client_surfaces.operator_tui.sections import SECTIONS, get_section
from client_surfaces.operator_tui.audit_nav import audit_nav_items
from client_surfaces.operator_tui.template_nav import template_nav_items

if TYPE_CHECKING:
    from client_surfaces.operator_tui.chat_state import ChatState


# ── SGR mouse protocol ───────────────────────────────────────────────────────

def parse_sgr_mouse_event(raw: str) -> tuple[int, int, str, int, int, bool] | None:
    text = str(raw or "")
    match = re.search(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])", text)
    if not match:
        return None
    cb = int(match.group(1))
    cx = max(0, int(match.group(2)) - 1)
    cy = max(0, int(match.group(3)) - 1)
    release = match.group(4) == "m"
    ctrl_held = bool(cb & 16)
    event_type = "move"
    buttons = 0
    scroll_delta = 0
    if cb & 64:
        event_type = "scroll_down" if (cb & 1) else "scroll_up"
        scroll_delta = 1 if event_type == "scroll_down" else -1
    elif release:
        event_type = "up"
    elif cb & 32:
        event_type = "move"
        button_code = cb & 3
        buttons = {0: 1, 1: 2, 2: 3}.get(button_code, 0)
    else:
        event_type = "down"
        button_code = cb & 3
        buttons = {0: 1, 1: 2, 2: 3}.get(button_code, 0)
    return cx, cy, event_type, buttons, scroll_delta, ctrl_held


# ── Shortcut actions (click on rendered key hints) ───────────────────────────

def shortcut_action_at(self, x: int, y: int) -> str | None:
    rendered = str(getattr(self, "_rendered_text", "") or "")
    if not rendered.strip():
        try:
            rendered = self._render()
        except Exception:
            rendered = ""
    lines = rendered.splitlines()
    if not (0 <= int(y) < len(lines)):
        return None
    line = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", lines[int(y)])
    if not line:
        return None
    candidates = shortcut_action_display_map(self)
    for match in re.finditer(r"\[([^\]]+)\]", line):
        inner = match.group(1)
        inner_start = match.start(1)
        for token_match in re.finditer(r"[^/\s]+(?:\+[^/\s]+)*", inner):
            token_start = inner_start + token_match.start()
            token_end = inner_start + token_match.end() - 1
            if token_start <= int(x) <= token_end:
                action = candidates.get(token_match.group(0))
                if action:
                    return action
    return None


def shortcut_action_display_map(self) -> dict[str, str]:
    actions = {
        "cycle_focus_or_channel": "Ctrl+W",
        "selection_down": "Ctrl+J",
        "selection_up": "Ctrl+K",
        "refresh": "Ctrl+R",
        "next_section": "Ctrl+N",
        "toggle_ai_snake_config": "Ctrl+A",
        "toggle_visual_view_switcher_overlay": "Ctrl+4",
        "copy_tui_snapshot": "Ctrl+\\",
        "save_tui_snapshot": "Ctrl+_",
        "open_long_chat_message": "Ctrl+Space",
        "scroll_page_up": "Ctrl+7",
        "scroll_page_down": "Ctrl+8",
        "inspect": "Ctrl+F",
        "help": "Ctrl+Y",
        "quit": "Ctrl+Q",
        "toggle_snake_mode": "Ctrl+S",
        "toggle_chat_panel": "Ctrl+G",
        "chat_focus": "Ctrl+E",
        "snake_pause": "Ctrl+P",
        "toggle_tutorial_ai": "Ctrl+U",
        "toggle_mouse_follow": "Ctrl+O",
        "snake_toggle_frame": "Ctrl+B",
        "snake_toggle_selection": "Ctrl+X",
        "snake_replace_selection": "Ctrl+V",
        "snake_clear_marks": "Ctrl+Z",
        "copy_chat_panel": "Ctrl+C",
        "copy_ai_status": "Ctrl+I",
        "clear_chat_input": "Ctrl+L",
    }
    result: dict[str, str] = {"Esc": "escape", "Enter": "enter"}
    for action, default in actions.items():
        result[display_for_action(action, default)] = action
    return result


def trigger_shortcut_action(self, action: str) -> None:
    if action == "cycle_focus_or_channel":
        if self._chat_focus_active() or self._artifact_chat_focus_active() or self._snake_mode_active():
            self._chat_cycle_channel()
        else:
            self._exit_command_mode_for_global_shortcut()
            move_focus(self, 1)
        return
    if action == "selection_down":
        self._set_selected_index(clamp_down(self))
        return
    if action == "selection_up":
        self._set_selected_index(max(0, self.state.selected_index - 1))
        return
    if action == "refresh":
        game = dict(self.state.header_logo_game or {})
        if is_showing_chat_long_message(game):
            refresh_rendered_view(game)
            self._set_state(self.state.with_updates(header_logo_game=game, status_message="Chat-Ansicht: Render aktualisiert"))
        else:
            self._run_command(":refresh")
        return
    if action == "next_section":
        self._run_command(":next")
        return
    if action == "toggle_ai_snake_config":
        self._toggle_ai_snake_config_panel()
        return
    if action == "toggle_visual_view_switcher_overlay":
        self._toggle_visual_view_switcher_overlay()
        return
    if action == "copy_tui_snapshot":
        self._exit_command_mode_for_global_shortcut()
        self._copy_tui_snapshot()
        return
    if action == "save_tui_snapshot":
        self._exit_command_mode_for_global_shortcut()
        self._save_tui_snapshot()
        return
    if action == "open_long_chat_message":
        self._exit_command_mode_for_global_shortcut()
        self._open_latest_long_chat_message()
        return
    if action == "scroll_page_up":
        self._scroll_active_panel(direction="page_up")
        return
    if action == "scroll_page_down":
        self._scroll_active_panel(direction="page_down")
        return
    if action == "inspect":
        if self._open_selected_item_inline():
            return
        self._run_command(":inspect")
        return
    if action == "help":
        self._run_command(":help")
        return
    if action == "quit":
        try:
            self._flush_config_on_exit()
            self._app.exit()
        except Exception:
            self._set_state(self.state.with_updates(status_message="quit"))
        return
    if action == "toggle_snake_mode":
        self._exit_command_mode_for_global_shortcut()
        self._toggle_snake_mode()
        return
    if action == "toggle_chat_panel":
        self._exit_command_mode_for_global_shortcut()
        self._toggle_chat_panel_open()
        return
    if action == "chat_focus":
        self._exit_command_mode_for_global_shortcut()
        self._toggle_chat_focus()
        return
    if action == "snake_pause":
        self._toggle_snake_pause()
        return
    if action == "toggle_tutorial_ai":
        self._toggle_tutorial_ai_mode()
        return
    if action == "toggle_mouse_follow":
        self._toggle_snake_mouse_follow()
        return
    if action == "snake_toggle_frame":
        self._snake_toggle_frame_mode()
        return
    if action == "snake_toggle_selection":
        self._snake_toggle_selection()
        return
    if action == "snake_replace_selection":
        self._snake_replace_selection()
        return
    if action == "snake_clear_marks":
        self._snake_clear_visual_marks()
        return
    if action == "copy_chat_panel":
        self._copy_chat_panel_snapshot()
        return
    if action == "copy_ai_status":
        self._exit_command_mode_for_global_shortcut()
        self._copy_ai_status_snapshot()
        return
    if action == "clear_chat_input":
        if self._chat_focus_active():
            self._chat_clear_input()
        elif self._artifact_chat_focus_active():
            self._artifact_chat_clear_input()


# ── Mouse event ingestion ────────────────────────────────────────────────────

def ingest_mouse_event(
    self,
    *,
    x: int,
    y: int,
    event_type: str,
    buttons: int = 0,
    scroll_delta: int = 0,
    ctrl_held: bool = False,
    now: float | None = None,
) -> None:
    game = dict(self.state.header_logo_game or self._default_header_snake())
    size = shutil.get_terminal_size((120, 32))
    width = max(72, int(size.columns))
    height = max(18, int(size.lines - 1))
    ts = float(now if now is not None else time.monotonic())
    self._mouse_state = normalize_mouse_state(
        self._mouse_state,
        x=x,
        y=y,
        width=width,
        height=height,
        event_type=cast(NormalizedMouseEventType, str(event_type)),
        buttons=buttons,
        scroll_delta=scroll_delta,
        now=ts,
    )
    game["mouse_state"] = {
        "x": self._mouse_state.x,
        "y": self._mouse_state.y,
        "event": self._mouse_state.last_event_type,
        "buttons": self._mouse_state.buttons,
        "scroll_delta": self._mouse_state.scroll_delta,
        "last_seen_at": self._mouse_state.last_seen_at,
        "active": self._mouse_state.active,
        "hover_started_at": self._mouse_state.hover_started_at,
    }

    _is_drag_move = (
        event_type == "move"
        and buttons == 1
        and bool(game.get("mouse_selection_active"))
    )

    if not _is_drag_move:
        region_index = build_region_index(self.state, width=width, height=height)
        target = region_index.get_target_at(self._mouse_state.x, self._mouse_state.y)
        if target is not None:
            game["mouse_target"] = {
                "kind": target.kind,
                "section_id": target.section_id,
                "pane": target.pane,
                "label": target.label,
                "payload": dict(target.payload),
            }
        else:
            game["mouse_target"] = None
    else:
        target = None

    if event_type == "down" and buttons == 1:
        shortcut_action = shortcut_action_at(self, self._mouse_state.x, self._mouse_state.y)
        if shortcut_action:
            trigger_shortcut_action(self, shortcut_action)
            return

    scrollbar_handled = handle_visual_viewport_scrollbar_mouse(
        self,
        game,
        x=self._mouse_state.x,
        y=self._mouse_state.y,
        width=width,
        height=height,
        event_type=event_type,
        buttons=buttons,
    )

    if not _is_drag_move:
        intent = self._intent_detector.evaluate(
            now=ts,
            mouse=self._mouse_state,
            target=target,
            selected_index=self.state.selected_index,
            current_section_id=self.state.section_id,
            user_feed=str(game.get("tutorial_user_feed") or ""),
        )
        self._apply_artifact_intent(game, intent=intent, now=ts, width=width, height=height)

    if scroll_delta != 0 and event_type in ("scroll_up", "scroll_down"):
        route_wheel_scroll(self, game, x=self._mouse_state.x, y=self._mouse_state.y, delta=scroll_delta)

    mouse_selection_handled = handle_mouse_selection_event(
        self,
        game,
        target=target,
        x=self._mouse_state.x,
        y=self._mouse_state.y,
        event_type=event_type,
        buttons=buttons,
        ctrl_held=ctrl_held,
    )

    if _is_drag_move:
        _new_range = game.get("mouse_selection_range")
        _old_range = (self.state.header_logo_game or {}).get("mouse_selection_range") or {}
        if (
            isinstance(_new_range, dict)
            and int(_new_range.get("end_x", -1)) == int(_old_range.get("end_x", -2))
            and int(_new_range.get("end_y", -1)) == int(_old_range.get("end_y", -2))
        ):
            return

    delayed_click = bool(game.pop("_mouse_selection_click", False))
    if delayed_click and target is not None:
        _section_before_click = self.state.section_id
        _tab_before_click = self.state.active_tab_id
        handle_left_click(self, game, target=target, now=ts, width=width, height=height)
        latest_game = dict(self.state.header_logo_game or {})
        game_out = dict(game)
        game_out.update(latest_game)
        game = game_out
        if self.state.section_id != _section_before_click:
            game["visual_viewport_enabled"] = False
            game["visual_viewport"] = {"enabled": False}
        if self.state.active_tab_id != _tab_before_click:
            game["visual_viewport_enabled"] = bool((dict(self.state.header_logo_game or {})).get("visual_viewport_enabled", False))
            game["visual_viewport"] = dict((dict(self.state.header_logo_game or {})).get("visual_viewport") or {"enabled": False})

    if not delayed_click and not scrollbar_handled and not mouse_selection_handled and event_type == "down" and buttons == 1 and target is not None:
        _section_before_click = self.state.section_id
        _tab_before_click = self.state.active_tab_id
        handle_left_click(self, game, target=target, now=ts, width=width, height=height)
        latest_game = dict(self.state.header_logo_game or {})
        game_out = dict(game)
        game_out.update(latest_game)
        game = game_out
        if self.state.section_id != _section_before_click:
            game["visual_viewport_enabled"] = False
            game["visual_viewport"] = {"enabled": False}
        if self.state.active_tab_id != _tab_before_click:
            game["visual_viewport_enabled"] = bool((dict(self.state.header_logo_game or {})).get("visual_viewport_enabled", False))
            game["visual_viewport"] = dict((dict(self.state.header_logo_game or {})).get("visual_viewport") or {"enabled": False})

    status = str(game.pop("_copy_status_message", "") or f"mouse {self._mouse_state.x},{self._mouse_state.y}")
    self._set_state(self.state.with_updates(header_logo_game=game, status_message=status))


# ── Mouse selection (text / snake) ───────────────────────────────────────────

def handle_mouse_selection_event(
    self,
    game: dict[str, object],
    *,
    target: RegionTarget | None,
    x: int,
    y: int,
    event_type: str,
    buttons: int,
    ctrl_held: bool = False,
) -> bool:
    selection_enabled = (
        self._snake_mode_active(game)
        or bool(game.get("mouse_selection_active"))
        or (target is not None and target.pane == "content")
    )
    if not selection_enabled:
        return False
    if event_type == "down" and buttons == 3:
        self._snake_copy_selection_to_game(game)
        return True
    if event_type == "down" and buttons == 1:
        game["mouse_selection_active"] = True
        game["mouse_selection_anchor"] = (int(x), int(y))
        game["mouse_selection_dragged"] = False
        game["mouse_selection_ctrl"] = ctrl_held
        mode = "block" if ctrl_held else "linear"
        if ctrl_held:
            active = game.get("mouse_selection_range")
            if active and isinstance(active, dict):
                committed = list(game.get("mouse_selection_committed_ranges") or [])
                committed.append(active)
                game["mouse_selection_committed_ranges"] = committed
        else:
            game["mouse_selection_committed_ranges"] = []
            game["mouse_selection_range"] = None
        game["mouse_selection_range"] = {
            "start_x": int(x), "start_y": int(y),
            "end_x": int(x), "end_y": int(y),
            "mode": mode,
        }
        if self._snake_mode_active(game):
            return True
        if (
            target is not None
            and target.pane == "content"
            and self.state.focus is FocusPane.CONTENT
            and not self._template_editor_active()
            and not self._audit_viewer_active()
        ):
            return True
        return False
    if event_type == "move" and buttons == 1 and bool(game.get("mouse_selection_active")):
        anchor_raw = game.get("mouse_selection_anchor")
        if isinstance(anchor_raw, (list, tuple)) and len(anchor_raw) == 2:
            ax, ay = int(anchor_raw[0]), int(anchor_raw[1])
            game["mouse_selection_dragged"] = True
            mode = "block" if bool(game.get("mouse_selection_ctrl")) else "linear"
            game["mouse_selection_range"] = {
                "start_x": ax, "start_y": ay,
                "end_x": int(x), "end_y": int(y),
                "mode": mode,
            }
            return True
    if event_type == "up" and bool(game.get("mouse_selection_active")):
        dragged = bool(game.get("mouse_selection_dragged"))
        game["mouse_selection_active"] = False
        if self._snake_mode_active(game) and not dragged:
            game["mouse_selection_range"] = None
            game["mouse_selection_committed_ranges"] = []
            game["selection_anchor"] = None
            game["selection_cells"] = []
            game["selection_regions"] = []
            game["_mouse_selection_click"] = True
            return True
        if dragged:
            active_range = game.get("mouse_selection_range")
            if isinstance(active_range, dict):
                sx = int(active_range.get("start_x", 0))
                sy = int(active_range.get("start_y", 0))
                ex = int(active_range.get("end_x", sx))
                ey = int(active_range.get("end_y", sy))
                x1, x2 = sorted((sx, ex))
                y1, y2 = sorted((sy, ey))
                cells: list[tuple[int, int]] = []
                for row in range(y1, y2 + 1):
                    for col in range(x1, x2 + 1):
                        cells.append((col, row))
                game["selection_anchor"] = (sx, sy)
                game["selection_cells"] = cells
                game["selection_regions"] = [
                    (x1, y1, x2, y2)
                ]
        return dragged
    return False


# ── Wheel scroll routing ─────────────────────────────────────────────────────

def route_wheel_scroll(self, game: dict, *, x: int, y: int, delta: int) -> None:
    """Route mouse wheel events to the appropriate ScrollContext via MouseRouter."""
    tab_y = 10 if self.state.open_tabs else 9
    if self.state.open_tabs and int(y) == tab_y - 1:
        max_offset = max(0, len(self.state.open_tabs) - 1)
        new_offset = max(0, min(max_offset, self.state.tab_scroll_offset + (1 if delta > 0 else -1)))
        if new_offset != self.state.tab_scroll_offset:
            self._set_state(self.state.with_updates(tab_scroll_offset=new_offset))
        return
    try:
        if (
            self.state.section_id == "templates"
            and hasattr(self, "_template_editor_active")
            and self._template_editor_active()
        ):
            size = shutil.get_terminal_size((120, 32))
            width = max(72, int(size.columns))
            height = max(18, int(size.lines - 1))
            left_width = 22
            detail_width = 34
            middle_width = max(18, width - left_width - detail_width - 6)
            body_height = max(3, height - 5 - 8)
            content_x1 = left_width + 2
            content_x2 = content_x1 + middle_width - 1
            body_y1 = 8
            body_y2 = body_y1 + body_height - 1
            if content_x1 <= int(x) <= content_x2 and body_y1 <= int(y) <= body_y2:
                scroll_delta = 2 if delta > 0 else -2
                if hasattr(self, "_template_editor_scroll_vertical"):
                    self._template_editor_scroll_vertical(scroll_delta)
                    game.update(dict(self.state.header_logo_game or {}))
                    game["_copy_status_message"] = "template editor: scroll"
                    return
        if (
            self.state.section_id == "audit"
            and hasattr(self, "_audit_viewer_active")
            and self._audit_viewer_active()
        ):
            size = shutil.get_terminal_size((120, 32))
            width = max(72, int(size.columns))
            height = max(18, int(size.lines - 1))
            left_width = 22
            detail_width = 34
            middle_width = max(18, width - left_width - detail_width - 6)
            body_height = max(3, height - 5 - 8)
            content_x1 = left_width + 2
            content_x2 = content_x1 + middle_width - 1
            body_y1 = 8
            body_y2 = body_y1 + body_height - 1
            if content_x1 <= int(x) <= content_x2 and body_y1 <= int(y) <= body_y2:
                scroll_delta = 2 if delta > 0 else -2
                if hasattr(self, "_audit_viewer_scroll_vertical"):
                    self._audit_viewer_scroll_vertical(scroll_delta)
                    game.update(dict(self.state.header_logo_game or {}))
                    game["_copy_status_message"] = "audit viewer: scroll"
                    return
        from client_surfaces.operator_tui.input.mouse_router import MouseRouter, PanelRect
        from client_surfaces.operator_tui.focus.focus_manager import FocusManager
        fm: FocusManager = self._get_focus_manager()
        mr = getattr(self, "_mouse_router_instance", None)
        if mr is None:
            self._mouse_router_instance = MouseRouter()
            mr = self._mouse_router_instance
        ctx_id = mr.route_wheel_event(x, y, delta, fm)
        if ctx_id == "chat_panel" or (ctx_id is None and self._chat_focus_active()):
            self._chat_scroll(delta * 2)
            return
        if ctx_id is not None:
            sm = self._get_scroll_manager()
            ctx = sm.get(ctx_id)
            if ctx:
                if delta < 0:
                    ctx.scroll_line_up(abs(delta) * 2)
                else:
                    ctx.scroll_line_down(delta * 2)
                game[f"scroll_offset_{ctx_id}"] = ctx.offset
                if ctx_id == "center_viewport":
                    game["visual_viewport_force_render"] = True
    except Exception:
        pass


# ── Visual viewport scrollbar ────────────────────────────────────────────────

def handle_visual_viewport_scrollbar_mouse(
    self,
    game: dict[str, object],
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    event_type: str,
    buttons: int,
) -> bool:
    """Handle clicks/drags on the visual viewport scrollbars."""
    if not bool(game.get("visual_viewport_enabled")):
        return False
    if event_type not in {"down", "move"} or buttons not in {0, 1}:
        return False

    left_width = 22
    detail_width = 34
    middle_width = max(18, int(width) - left_width - detail_width - 6)
    body_height = max(3, int(height) - 5 - 8)
    content_x1 = left_width + 2
    content_x2 = content_x1 + middle_width - 1
    body_y1 = 8
    body_y2 = body_y1 + body_height - 1
    if not (content_x1 <= int(x) <= content_x2 and body_y1 <= int(y) <= body_y2):
        return False

    meta = dict(game.get("visual_viewport_scene_meta") or {})
    content_lines = max(1, int(meta.get("content_lines") or body_height))
    max_line_width = max(1, int(meta.get("max_line_width") or middle_width))
    h_scrollable = max_line_width > middle_width
    v_scrollable = content_lines > body_height
    hbar_y = body_y2 if h_scrollable else -1

    if v_scrollable and int(x) == content_x2 and int(y) <= body_y2:
        usable_rows = max(1, body_height - (1 if h_scrollable else 0))
        rel_y = max(0, min(usable_rows - 1, int(y) - body_y1))
        max_scroll = max(0, content_lines - usable_rows)
        new_offset = round(max_scroll * rel_y / max(1, usable_rows - 1))
        try:
            sm = self._get_scroll_manager()
            ctx = sm.get("center_viewport")
            if ctx is not None:
                ctx.update_dimensions(content_height=content_lines, viewport_height=usable_rows)
                ctx.offset = max(0, min(ctx.max_scroll, new_offset))
                game["scroll_offset_center_viewport"] = ctx.offset
        except Exception:
            game["scroll_offset_center_viewport"] = max(0, new_offset)
        game["visual_viewport_force_render"] = True
        game["_copy_status_message"] = f"scroll: Visual Viewport {game.get('scroll_offset_center_viewport', new_offset)}/{max_scroll}"
        return True

    if h_scrollable and int(y) == hbar_y:
        rel_x = max(0, min(middle_width - 1, int(x) - content_x1))
        max_offset = max(0, max_line_width - middle_width)
        new_offset = round(max_offset * rel_x / max(1, middle_width - 1))
        game["center_h_scroll_offset"] = max(0, min(max_offset, new_offset))
        game["visual_viewport_force_render"] = True
        game["_copy_status_message"] = f"h-scroll: {game['center_h_scroll_offset']}/{max_offset}"
        return True

    return False


# ── Left click handler ───────────────────────────────────────────────────────

def handle_left_click(
    self,
    game: dict[str, object],
    *,
    target: RegionTarget,
    now: float,
    width: int,
    height: int,
) -> None:
    """On left click: select the item, direct AI snake there, open chat, trigger explanation."""
    if target.kind == "tab":
        from client_surfaces.operator_tui.tab_manager import activate_tab
        tab_id = str(target.payload.get("tab_id") or "")
        if tab_id:
            new_state, new_game = activate_tab(self.state, tab_id, game=dict(self.state.header_logo_game or {}))
            self._set_state(new_state.with_updates(header_logo_game=new_game))
        return

    if target.kind == "tab_close":
        from client_surfaces.operator_tui.tab_manager import close_tab
        tab_id = str(target.payload.get("tab_id") or "")
        if tab_id:
            new_state = close_tab(self.state, tab_id)
            game_out = dict(new_state.header_logo_game or {})
            game_out["visual_viewport_enabled"] = False
            game_out["visual_viewport"] = {"enabled": False}
            self._set_state(new_state.with_updates(header_logo_game=game_out))
        return

    if target.kind == "tab_scroll_left":
        new_offset = max(0, self.state.tab_scroll_offset - 1)
        self._set_state(self.state.with_updates(tab_scroll_offset=new_offset))
        return

    if target.kind == "tab_scroll_right":
        max_offset = max(0, len(self.state.open_tabs) - 1)
        new_offset = min(max_offset, self.state.tab_scroll_offset + 1)
        self._set_state(self.state.with_updates(tab_scroll_offset=new_offset))
        return

    if target.kind == "chat_history":
        rows = long_message_history_rows(game)
        idx_raw = target.payload.get("history_index")
        idx = int(idx_raw) if isinstance(idx_raw, int) else -1
        if 0 <= idx < len(rows) and configure_middle_view_for_history_entry(game, rows[idx]):
            from client_surfaces.operator_tui.tab_manager import open_or_activate_tab, tab_label_for_chat_preview
            entry = rows[idx]
            preview = str(entry.get("preview") or entry.get("text") or "Chat")
            label = tab_label_for_chat_preview(preview)
            vp_state = {"scroll_offset": 0, "preview": preview[:80]}
            next_state = open_or_activate_tab(
                self.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT, selected_index=0),
                section_id=self.state.section_id,
                kind="chat_viewport",
                label=label,
                viewport_state=vp_state,
            )
            game_out = dict(next_state.header_logo_game or game)
            game_out["visual_viewport_enabled"] = True
            game_out["visual_viewport"] = {"enabled": True}
            game["_copy_status_message"] = "Chat-History: Originalausgabe"
            self._set_state(next_state.with_updates(header_logo_game=game_out))
        return

    if target.kind == "template_nav_item":
        item_index_raw = target.payload.get("template_item_index")
        item_index = int(item_index_raw) if isinstance(item_index_raw, int) else -1
        if item_index < 0:
            return
        self._clear_chat_input_focus(game)
        next_state = self.state.with_updates(
            header_logo_game=game,
            section_id="templates",
            focus=FocusPane.CONTENT,
            selected_index=item_index,
        )
        if self.state.section_id != "templates":
            next_state = load_active_section(next_state, self._registry)
        self._set_state(next_state)
        payload = dict((self.state.section_payloads or {}).get("templates") or {})
        items = payload.get("items")
        entry = items[item_index] if isinstance(items, list) and 0 <= item_index < len(items) else {}
        if isinstance(entry, dict) and hasattr(self, "_open_template_editor_for_selected"):
            self._open_template_editor_for_selected()
        else:
            self._run_command(":inspect")
        game["_copy_status_message"] = str(self.state.status_message or "template ausgewählt")
        return

    if target.kind == "audit_nav_item":
        item_index_raw = target.payload.get("audit_item_index")
        item_index = int(item_index_raw) if isinstance(item_index_raw, int) else -1
        if item_index < 0:
            return
        self._clear_chat_input_focus(game)
        next_state = self.state.with_updates(
            header_logo_game=game,
            section_id="audit",
            focus=FocusPane.CONTENT,
            selected_index=item_index,
            mode=OperatorMode.NORMAL,
        )
        if self.state.section_id != "audit":
            next_state = load_active_section(next_state, self._registry)
        self._set_state(next_state)
        if hasattr(self, "_open_audit_viewer_for_selected"):
            self._open_audit_viewer_for_selected()
        game["_copy_status_message"] = str(self.state.status_message or "audit ausgewählt")
        return

    if (
        self.state.section_id == "templates"
        and target.pane == "content"
        and hasattr(self, "_template_editor_set_cursor_from_content_click")
    ):
        if self._template_editor_set_cursor_from_content_click(
            x=int(self._mouse_state.x),
            y=int(self._mouse_state.y),
            width=int(width),
            height=int(height),
        ):
            game["_copy_status_message"] = "template editor: cursor"
            return
    if self.state.section_id == "share" and target.pane == "content":
        if handle_share_content_click(self, x=int(self._mouse_state.x), y=int(self._mouse_state.y), game=game):
            return

    if (
        self.state.section_id == "audit"
        and target.pane == "content"
        and hasattr(self, "_open_audit_viewer_for_selected")
    ):
        if hasattr(self, "_audit_cleanup_handle_mouse_click") and self._audit_cleanup_handle_mouse_click(
            x=int(self._mouse_state.x),
            y=int(self._mouse_state.y),
            width=int(width),
            height=int(height),
        ):
            game["_copy_status_message"] = str(self.state.status_message or "cleanup")
            return
        self._open_audit_viewer_for_selected()
        game["_copy_status_message"] = str(self.state.status_message or "audit viewer")
        return

    if bool(game.get("ai_snake_config_open")) and target.pane == "content":
        combo_value = str(target.payload.get("ai_snake_combo_option_value") or "")
        if combo_value:
            self._ai_snake_config_combo_select_value(value=combo_value)
            return
        cfg_key = str(target.payload.get("ai_snake_config_key") or "")
        idx = int(target.payload.get("selected_index") or 0)
        if not cfg_key:
            items = ai_snake_config_items(game)
            if 0 <= idx < len(items):
                cfg_key = str(items[idx].get("key") or "")
        if cfg_key:
            self.state = self.state.with_updates(selected_index=max(0, idx), focus=FocusPane.CONTENT)
            self._open_ai_snake_config_combo(game, key=cfg_key, idx=max(0, idx))
            return

    if target.kind in {"pane", "section"}:
        self._clear_chat_input_focus(game)
        self._select_region_target(target)
        return

    self._select_region_target(target)

    game["artifact_target_cell"] = (self._mouse_state.x, self._mouse_state.y)
    game["tutorial_ai_target_mode"] = "fast_target"
    game["tutorial_ai_target_hint"] = target.pane or "content"
    game["artifact_intent_confidence"] = "confirmed"
    game["artifact_intent_target"] = {
        "kind": target.kind,
        "section_id": target.section_id,
        "pane": target.pane,
        "label": target.label,
        "payload": dict(target.payload),
    }

    self._activate_artifact_chat(game, target=target, now=now)

    if not bool(game.get("active")):
        return

    label = str(target.label or target.section_id or "diesen Bereich")
    section = str(target.section_id or self.state.section_id or "")
    game["tutorial_user_feed"] = f"Erkläre {label} im Abschnitt {section}."
    game["tutorial_ai_local_contact"] = True
    game["tutorial_ai_contact_zone"] = target.pane or "content"

    self._tutorial_async_next_refresh_at = 0.0
    self._tutorial_async_tip_future = None


# ── Share section mouse click ────────────────────────────────────────────────

def handle_share_content_click(self, *, x: int, y: int, game: dict) -> bool:
    rendered = str(getattr(self, "_rendered_text", "") or "")
    if not rendered.strip():
        try:
            rendered = self._render()
        except Exception:
            return False
    lines = rendered.splitlines()
    if not (0 <= y < len(lines)):
        return False

    from client_surfaces.operator_tui.share_menu import extract_click_command
    cmd = extract_click_command(lines[y], x=x)
    if not cmd:
        return False

    result = execute_command(cmd, self.state)
    self._set_state(result.state)
    game["_copy_status_message"] = f"share: {cmd}"
    return True


# ── Navigation helpers ───────────────────────────────────────────────────────

def clamp_down(self) -> int:
    cur = self.state.selected_index
    if self.state.focus is FocusPane.NAVIGATION:
        game = self.state.header_logo_game if isinstance(self.state.header_logo_game, dict) else {}
        history = game.get("chat_long_message_history")
        history_count = len(history) if isinstance(history, list) else 0
        template_count = self._template_nav_selectable_count()
        audit_count = self._audit_nav_selectable_count()
        return min(cur + 1, max(0, len(SECTIONS) + template_count + audit_count + history_count - 1))
    if self.state.focus is FocusPane.HEADER:
        from client_surfaces.operator_tui.header_config import CONFIG_ITEMS
        return min(cur + 1, len(CONFIG_ITEMS) - 1)
    return cur + 1


def move_focus(self, delta: int) -> None:
    panes = (FocusPane.HEADER, FocusPane.NAVIGATION, FocusPane.CONTENT, FocusPane.DETAIL)
    cur = panes.index(self.state.focus)
    new_focus = panes[(cur + delta) % len(panes)]
    if new_focus is FocusPane.NAVIGATION:
        section_ids = [s.id for s in SECTIONS]
        try:
            new_selected = section_ids.index(self.state.section_id)
        except ValueError:
            new_selected = 0
    elif new_focus is FocusPane.HEADER or self.state.focus in (FocusPane.NAVIGATION, FocusPane.HEADER):
        new_selected = 0
    else:
        new_selected = self.state.selected_index
    next_state = self.state.with_updates(focus=new_focus, selected_index=new_selected)
    self._set_state(next_state)
