from __future__ import annotations

import re
import shutil
import time
from typing import TYPE_CHECKING, Any

from client_surfaces.operator_tui.chat_long_message import (
    configure_middle_view_for_message,
    is_showing_chat_long_message,
    latest_long_message_for_channel,
    toggle_render_mode,
)
from client_surfaces.operator_tui.models import FocusPane, OperatorMode
from client_surfaces.operator_tui.tui_snapshot import rendered_tui_snapshot_text, write_tui_snapshot

if TYPE_CHECKING:
    from client_surfaces.operator_tui.interactive import InteractiveOperatorTui


# ── Chat focus helpers ───────────────────────────────────────────────────

def chat_focus_active(tui: InteractiveOperatorTui) -> bool:
    game = tui.state.header_logo_game or {}
    chat_raw = game.get("chat_state")
    return isinstance(chat_raw, dict) and bool(chat_raw.get("chat_focus")) and (
        tui._snake_mode_active() or bool(game.get("chat_panel_open"))
    )


def chat_panel_available(tui: InteractiveOperatorTui) -> bool:
    game = tui.state.header_logo_game or {}
    artifact_chat = game.get("artifact_chat_state")
    return bool(game.get("chat_panel_open")) or (
        isinstance(artifact_chat, dict) and isinstance(artifact_chat.get("active_target"), dict)
    )


def artifact_chat_focus_active(tui: InteractiveOperatorTui) -> bool:
    game = tui.state.header_logo_game or {}
    return bool(game.get("artifact_chat_focus")) and not tui._snake_mode_active()


# ── Scroll / Focus managers ──────────────────────────────────────────────

def get_scroll_manager(tui: InteractiveOperatorTui):
    from client_surfaces.operator_tui.scroll.scroll_manager import ScrollManager
    from client_surfaces.operator_tui.scroll.scroll_context import ScrollContext
    if not hasattr(tui, "_scroll_manager_instance"):
        tui._scroll_manager_instance = ScrollManager()
        tui._scroll_manager_instance.register(
            ScrollContext(id="chat_panel", label="Chat", content_height=100, viewport_height=20)
        )
        tui._scroll_manager_instance.register(
            ScrollContext(id="main_content", label="Content", content_height=100, viewport_height=20)
        )
        tui._scroll_manager_instance.register(
            ScrollContext(id="center_viewport", label="Visual Viewport", content_height=1, viewport_height=1)
        )
    return tui._scroll_manager_instance


def get_focus_manager(tui: InteractiveOperatorTui):
    from client_surfaces.operator_tui.focus.focus_manager import FocusManager
    if not hasattr(tui, "_focus_manager_instance"):
        tui._focus_manager_instance = FocusManager()
        tui._focus_manager_instance.register_scroll_context("chat_panel", "chat_panel")
        tui._focus_manager_instance.register_scroll_context("main_content", "main_content")
        tui._focus_manager_instance.register_scroll_context("artifact_panel", "artifact_panel")
        tui._focus_manager_instance.register_scroll_context("center_viewport", "center_viewport")
    return tui._focus_manager_instance


def sync_scroll_focus_and_mouse_regions(
    tui: InteractiveOperatorTui,
    *,
    width: int,
    height: int,
    content_width: int,
    body_start: int,
    body_height: int,
) -> None:
    sm = get_scroll_manager(tui)
    fm = get_focus_manager(tui)
    game = tui.state.header_logo_game if isinstance(tui.state.header_logo_game, dict) else {}

    active_focus = {
        FocusPane.NAVIGATION: "nav_panel",
        FocusPane.CONTENT: "center_viewport" if bool(game.get("visual_viewport_enabled")) else "main_content",
        FocusPane.DETAIL: "detail_panel",
        FocusPane.HEADER: "main_content",
    }.get(tui.state.focus, "main_content")
    if chat_focus_active(tui):
        active_focus = "chat_panel"
    fm.set_active(active_focus)

    meta = dict(game.get("visual_viewport_scene_meta") or {})
    content_lines = max(1, int(meta.get("content_lines") or body_height))
    sm.update("center_viewport", content_height=content_lines, viewport_height=max(1, body_height))

    try:
        from client_surfaces.operator_tui.input.mouse_router import MouseRouter, PanelRect
        mr = getattr(tui, "_mouse_router_instance", None)
        if mr is None:
            tui._mouse_router_instance = MouseRouter()
            mr = tui._mouse_router_instance
        mr.clear_panels()
        left_width = 22
        detail_width = 34
        content_x1 = left_width + 2
        content_x2 = min(max(0, int(width) - detail_width - 5), content_x1 + max(1, content_width) - 1)
        detail_x1 = content_x2 + 3
        detail_x2 = min(max(0, int(width) - 1), detail_x1 + detail_width - 1)
        body_y1 = max(0, int(body_start))
        body_y2 = min(max(0, int(height) - 4), body_y1 + max(1, body_height) - 1)
        mr.register_panel(PanelRect(0, body_y1, left_width - 1, body_y2, "nav_panel", "main_content"))
        mr.register_panel(PanelRect(content_x1, body_y1, content_x2, body_y2, "center_viewport", "center_viewport"))
        mr.register_panel(PanelRect(detail_x1, body_y1, detail_x2, body_y2, "detail_panel", "chat_panel"))
    except Exception:
        pass


# ── Scrolling ────────────────────────────────────────────────────────────

def scroll_active_panel(tui: InteractiveOperatorTui, direction: str) -> None:
    if chat_focus_active(tui):
        delta_map = {"page_up": -10, "page_down": 10, "line_up": -1, "line_down": 1, "home": -9999, "end": 9999}
        chat_scroll(tui, delta_map.get(direction, 0))
        return
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    if (
        str(game.get("tutor_ask_question") or "").strip()
        and tui.state.focus is FocusPane.CONTENT
    ):
        delta_map = {"page_up": -10, "page_down": 10, "line_up": -1, "line_down": 1, "home": -9999, "end": 9999}
        delta = delta_map.get(direction, 0)
        if delta:
            raw_offset = game.get("chat_long_message_scroll_offset") or 0
            try:
                cur = int(str(raw_offset))
            except (TypeError, ValueError):
                cur = 0
            new_offset = max(0, cur + delta)
            game["chat_long_message_scroll_offset"] = new_offset
            tui._set_state(tui.state.with_updates(header_logo_game=game))
            return
    sm = get_scroll_manager(tui)
    fm = get_focus_manager(tui)
    ctx_id = fm.active_scroll_context_id()
    if ctx_id is None:
        tui._set_state(tui.state.with_updates(status_message="kein scrollbarer Bereich fokussiert"))
        return
    ctx = sm.get(ctx_id)
    if ctx is None:
        return
    moved = False
    if direction == "page_up":
        moved = ctx.scroll_page_up()
    elif direction == "page_down":
        moved = ctx.scroll_page_down()
    elif direction == "line_up":
        moved = ctx.scroll_line_up()
    elif direction == "line_down":
        moved = ctx.scroll_line_down()
    elif direction == "home":
        moved = ctx.scroll_home()
    elif direction == "end":
        moved = ctx.scroll_end()
    if moved:
        game = dict(tui.state.header_logo_game or tui._default_header_snake())
        game[f"scroll_offset_{ctx_id}"] = ctx.offset
        if ctx_id == "center_viewport":
            game["visual_viewport_force_render"] = True
        tui._set_state(tui.state.with_updates(header_logo_game=game, status_message=f"scroll: {ctx.label} {ctx.offset}/{ctx.max_scroll}"))


def h_scroll_center(tui: InteractiveOperatorTui, delta: int) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    meta = dict(game.get("visual_viewport_scene_meta") or {})
    max_line_width = int(meta.get("max_line_width") or 0)
    viewport_width = int(meta.get("viewport_width") or 0)
    if viewport_width <= 0:
        viewport_width = max(1, shutil.get_terminal_size((120, 32)).columns - 22 - 34 - 6)
    max_offset = max(0, max_line_width - viewport_width)
    current = int(game.get("center_h_scroll_offset") or 0)
    new_offset = max(0, min(max_offset, current + int(delta)))
    game["center_h_scroll_offset"] = new_offset
    game["visual_viewport_force_render"] = True
    try:
        runtime = tui._ensure_visual_runtime()
        view = runtime.get_view_instance("markdown_mermaid_document")
        if view is not None and hasattr(view, "apply_h_scroll_offset"):
            view.apply_h_scroll_offset(new_offset)
    except Exception:
        pass
    tui._set_state(tui.state.with_updates(header_logo_game=game, status_message=f"h-scroll: {new_offset}/{max_offset}"))


# ── Visual view toggles ──────────────────────────────────────────────────

def toggle_visual_view_switcher_overlay(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    current = bool(game.get("visual_view_switcher_overlay_visible", False))
    game["visual_view_switcher_overlay_visible"] = not current
    tui._set_state(
        tui.state.with_updates(
            header_logo_game=game,
            status_message="View-Leiste: an" if game["visual_view_switcher_overlay_visible"] else "View-Leiste: aus",
        )
    )


def next_visual_view(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    game["visual_viewport_cycle_next"] = True
    tui._set_state(tui.state.with_updates(header_logo_game=game, status_message="view: nächste"))


def previous_visual_view(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    game["visual_viewport_cycle_previous"] = True
    tui._set_state(tui.state.with_updates(header_logo_game=game, status_message="view: vorherige"))


# ── Chat panel / context help toggles ────────────────────────────────────

def toggle_chat_panel_open(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    game["chat_panel_open"] = not bool(game.get("chat_panel_open"))
    tui._append_ai_monitor_log(
        game,
        event="chat_panel_toggled",
        label="AI-Chat aktiviert" if bool(game["chat_panel_open"]) else "AI-Chat deaktiviert",
    )
    if not game["chat_panel_open"]:
        game["artifact_chat_focus"] = False
    try:
        from client_surfaces.operator_tui.snake_persistence import save_tui_chat_settings
        save_tui_chat_settings({"chat_panel_open": bool(game.get("chat_panel_open"))})
    except Exception:
        pass
    tui._set_state(
        tui.state.with_updates(
            header_logo_game=game,
            status_message="chat panel: an" if game["chat_panel_open"] else "chat panel: aus",
        )
    )


def toggle_context_help(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    game["shortcut_help_open"] = not bool(game.get("shortcut_help_open"))
    tui._set_state(
        tui.state.with_updates(
            header_logo_game=game,
            status_message="shortcuts: an" if game["shortcut_help_open"] else "shortcuts: aus",
        )
    )


def send_terminal_context_to_ai(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(tui._rendered_text or ""))
    snapshot = "\n".join(plain.splitlines()[-120:])[:8000]
    if not snapshot.strip():
        tui._set_state(tui.state.with_updates(status_message="AI-Kontext: kein Terminalinhalt"))
        return
    game["ai_terminal_context"] = snapshot
    artifact_chat = dict(game.get("artifact_chat_state") or {})
    artifact_chat["active_target"] = {
        "kind": "terminal_snapshot",
        "label": "Terminal Snapshot",
        "path": "",
        "id": "terminal-current",
        "section_id": str(tui.state.section_id or ""),
    }
    messages = [dict(m) for m in (artifact_chat.get("messages") or []) if isinstance(m, dict)]
    messages.append({"at": time.time(), "source": "system", "text": "Terminalinhalt als AI-Kontext übernommen."})
    artifact_chat["messages"] = messages[-12:]
    game["artifact_chat_state"] = artifact_chat
    game["chat_panel_open"] = True
    game["artifact_chat_focus"] = False

    from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state, switch_channel
    chat = get_chat_state(game)
    switch_channel(chat, "ai:tutor", preserve_input=True)
    chat["chat_focus"] = True
    chat["chat_input_cursor"] = len(str(chat.get("chat_input_buffer") or ""))
    chat["chat_input_history_index"] = None
    set_chat_state(game, chat)
    tui._set_state(
        tui.state.with_updates(
            header_logo_game=game,
            status_message="AI-Kontext: Terminalinhalt bereit; Frage im AI-Chat eingeben",
        )
    )


# ── Chat channel operations ──────────────────────────────────────────────

def chat_cycle_channel(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    from client_surfaces.operator_tui.chat_state import (
        get_chat_state, set_chat_state, switch_channel, get_sessions,
    )
    chat = get_chat_state(game)
    channels_dict = chat.get("channels") or {}
    session_ids = [str(s.get("id") or "") for s in get_sessions(chat) if isinstance(s, dict)]
    preferred = [ch for ch in ["room:main", "notes:self", "system"] if ch in channels_dict]
    session_channels = [f"ai:{sid}" for sid in session_ids if f"ai:{sid}" in channels_dict]
    ordered = preferred + session_channels
    if not ordered:
        return
    current = str(chat.get("active_channel") or ordered[0])
    try:
        idx = ordered.index(current)
    except ValueError:
        idx = -1
    next_id = ordered[(idx + 1) % len(ordered)]
    switch_channel(chat, next_id, preserve_input=True)
    set_chat_state(game, chat)
    tui._set_state(tui.state.with_updates(header_logo_game=game, status_message=f"kanal: {next_id}"))


def chat_switch_channel(tui: InteractiveOperatorTui, channel_id: str) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state, switch_channel
    chat = get_chat_state(game)
    if switch_channel(chat, channel_id, preserve_input=True):
        set_chat_state(game, chat)
        tui._set_state(tui.state.with_updates(header_logo_game=game, status_message=f"kanal: {channel_id}"))


# ── Chat focus (enter / leave / toggle) ──────────────────────────────────

def chat_focus_enter(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or {})
    from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
    chat = get_chat_state(game)
    chat["chat_focus"] = True
    chat["chat_input_buffer"] = ""
    chat["chat_input_cursor"] = 0
    chat["chat_input_history_index"] = None
    chat["chat_input_saved_draft"] = ""
    set_chat_state(game, chat)
    tui._set_state(tui.state.with_updates(header_logo_game=game, status_message="chat: focus"))


def chat_focus_leave(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or {})
    from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
    chat = get_chat_state(game)
    chat["chat_focus"] = False
    chat["chat_input_buffer"] = ""
    chat["chat_input_cursor"] = 0
    chat["chat_input_history_index"] = None
    chat["chat_input_saved_draft"] = ""
    set_chat_state(game, chat)
    tui._set_state(tui.state.with_updates(header_logo_game=game, status_message="chat: game focus"))


def toggle_chat_focus(tui: InteractiveOperatorTui) -> None:
    if chat_focus_active(tui):
        chat_focus_leave(tui)
        return
    if artifact_chat_focus_active(tui):
        artifact_chat_focus_leave(tui, clear=False)
        return
    if tui._snake_mode_active() or bool((tui.state.header_logo_game or {}).get("chat_panel_open")):
        chat_focus_enter(tui)
        return
    artifact_chat_focus_enter(tui)


# ── Chat input operations ────────────────────────────────────────────────

def chat_append(tui: InteractiveOperatorTui, ch: str) -> None:
    game = dict(tui.state.header_logo_game or {})
    from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
    chat = get_chat_state(game)
    buf = str(chat.get("chat_input_buffer") or "")
    cursor = max(0, min(len(buf), int(chat.get("chat_input_cursor") or len(buf))))
    if len(buf) >= 200:
        set_chat_state(game, chat)
        tui._set_state(tui.state.with_updates(header_logo_game=game))
        return
    new_buf = (buf[:cursor] + ch + buf[cursor:])[:200]
    new_cursor = min(len(new_buf), cursor + len(ch))
    chat["chat_input_buffer"] = new_buf
    chat["chat_input_cursor"] = new_cursor
    chat["chat_input_history_index"] = None
    set_chat_state(game, chat)
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def chat_backspace(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or {})
    from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
    chat = get_chat_state(game)
    buf = str(chat.get("chat_input_buffer") or "")
    cursor = max(0, min(len(buf), int(chat.get("chat_input_cursor") or len(buf))))
    if cursor <= 0:
        set_chat_state(game, chat)
        tui._set_state(tui.state.with_updates(header_logo_game=game))
        return
    chat["chat_input_buffer"] = buf[:cursor - 1] + buf[cursor:]
    chat["chat_input_cursor"] = cursor - 1
    chat["chat_input_history_index"] = None
    set_chat_state(game, chat)
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def chat_delete(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or {})
    from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
    chat = get_chat_state(game)
    buf = str(chat.get("chat_input_buffer") or "")
    cursor = max(0, min(len(buf), int(chat.get("chat_input_cursor") or len(buf))))
    if cursor >= len(buf):
        set_chat_state(game, chat)
        tui._set_state(tui.state.with_updates(header_logo_game=game))
        return
    chat["chat_input_buffer"] = buf[:cursor] + buf[cursor + 1:]
    chat["chat_input_cursor"] = cursor
    chat["chat_input_history_index"] = None
    set_chat_state(game, chat)
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def chat_move_cursor(tui: InteractiveOperatorTui, delta: int) -> None:
    game = dict(tui.state.header_logo_game or {})
    from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
    chat = get_chat_state(game)
    buf = str(chat.get("chat_input_buffer") or "")
    cursor = max(0, min(len(buf), int(chat.get("chat_input_cursor") or len(buf))))
    chat["chat_input_cursor"] = max(0, min(len(buf), cursor + int(delta)))
    set_chat_state(game, chat)
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def chat_history_move(tui: InteractiveOperatorTui, step: int) -> None:
    game = dict(tui.state.header_logo_game or {})
    from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
    chat = get_chat_state(game)
    history = [str(item) for item in (chat.get("chat_input_history") or []) if str(item).strip()]
    if not history:
        return
    buf = str(chat.get("chat_input_buffer") or "")
    idx_raw = chat.get("chat_input_history_index")
    idx = int(idx_raw) if isinstance(idx_raw, int) else None

    if int(step) < 0:
        if idx is None:
            chat["chat_input_saved_draft"] = buf
            idx = len(history) - 1
        else:
            idx = max(0, idx - 1)
        selected = history[idx]
        chat["chat_input_buffer"] = selected
        chat["chat_input_cursor"] = len(selected)
        chat["chat_input_history_index"] = idx
    else:
        if idx is None:
            set_chat_state(game, chat)
            tui._set_state(tui.state.with_updates(header_logo_game=game))
            return
        if idx < len(history) - 1:
            idx += 1
            selected = history[idx]
            chat["chat_input_buffer"] = selected
            chat["chat_input_cursor"] = len(selected)
            chat["chat_input_history_index"] = idx
        else:
            draft = str(chat.get("chat_input_saved_draft") or "")
            chat["chat_input_buffer"] = draft
            chat["chat_input_cursor"] = len(draft)
            chat["chat_input_history_index"] = None

    set_chat_state(game, chat)
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def chat_clear_input(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or {})
    from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
    chat = get_chat_state(game)
    chat["chat_input_buffer"] = ""
    chat["chat_input_cursor"] = 0
    chat["chat_input_history_index"] = None
    set_chat_state(game, chat)
    tui._set_state(tui.state.with_updates(header_logo_game=game, status_message="chat: input cleared"))


# ── Artifact chat operations ─────────────────────────────────────────────

def artifact_chat_focus_enter(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    if not chat_panel_available(tui):
        game["chat_panel_open"] = True
    game["artifact_chat_focus"] = True
    game.setdefault("artifact_chat_input", "")
    game["artifact_chat_cursor"] = max(0, min(len(str(game.get("artifact_chat_input") or "")), int(game.get("artifact_chat_cursor") or len(str(game.get("artifact_chat_input") or "")))))
    game["artifact_chat_history_index"] = None
    game.setdefault("artifact_chat_history", [])
    game.setdefault("artifact_chat_saved_draft", "")
    game["chat_panel_open"] = True
    tui._set_state(tui.state.with_updates(header_logo_game=game, status_message="artifact chat: focus"))


def artifact_chat_focus_leave(tui: InteractiveOperatorTui, *, clear: bool = False) -> None:
    game = dict(tui.state.header_logo_game or {})
    game["artifact_chat_focus"] = False
    if clear:
        game["artifact_chat_input"] = ""
        game["artifact_chat_cursor"] = 0
        game["artifact_chat_history_index"] = None
        game["artifact_chat_saved_draft"] = ""
    tui._set_state(tui.state.with_updates(header_logo_game=game, status_message="artifact chat: closed"))


def artifact_chat_append(tui: InteractiveOperatorTui, ch: str) -> None:
    game = dict(tui.state.header_logo_game or {})
    buf = str(game.get("artifact_chat_input") or "")
    cursor = max(0, min(len(buf), int(game.get("artifact_chat_cursor") or len(buf))))
    if len(buf) >= 500:
        tui._set_state(tui.state.with_updates(header_logo_game=game))
        return
    new_buf = (buf[:cursor] + ch + buf[cursor:])[:500]
    game["artifact_chat_input"] = new_buf
    game["artifact_chat_cursor"] = min(len(new_buf), cursor + len(ch))
    game["artifact_chat_history_index"] = None
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def artifact_chat_backspace(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or {})
    buf = str(game.get("artifact_chat_input") or "")
    cursor = max(0, min(len(buf), int(game.get("artifact_chat_cursor") or len(buf))))
    if cursor <= 0:
        tui._set_state(tui.state.with_updates(header_logo_game=game))
        return
    game["artifact_chat_input"] = buf[:cursor - 1] + buf[cursor:]
    game["artifact_chat_cursor"] = cursor - 1
    game["artifact_chat_history_index"] = None
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def artifact_chat_delete(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or {})
    buf = str(game.get("artifact_chat_input") or "")
    cursor = max(0, min(len(buf), int(game.get("artifact_chat_cursor") or len(buf))))
    if cursor >= len(buf):
        tui._set_state(tui.state.with_updates(header_logo_game=game))
        return
    game["artifact_chat_input"] = buf[:cursor] + buf[cursor + 1:]
    game["artifact_chat_cursor"] = cursor
    game["artifact_chat_history_index"] = None
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def artifact_chat_move_cursor(tui: InteractiveOperatorTui, delta: int) -> None:
    game = dict(tui.state.header_logo_game or {})
    buf = str(game.get("artifact_chat_input") or "")
    cursor = max(0, min(len(buf), int(game.get("artifact_chat_cursor") or len(buf))))
    game["artifact_chat_cursor"] = max(0, min(len(buf), cursor + int(delta)))
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def artifact_chat_history_move(tui: InteractiveOperatorTui, step: int) -> None:
    game = dict(tui.state.header_logo_game or {})
    history = [str(item) for item in (game.get("artifact_chat_history") or []) if str(item).strip()]
    if not history:
        return
    buf = str(game.get("artifact_chat_input") or "")
    idx_raw = game.get("artifact_chat_history_index")
    idx = int(idx_raw) if isinstance(idx_raw, int) else None
    if int(step) < 0:
        if idx is None:
            game["artifact_chat_saved_draft"] = buf
            idx = len(history) - 1
        else:
            idx = max(0, idx - 1)
        selected = history[idx]
        game["artifact_chat_input"] = selected
        game["artifact_chat_cursor"] = len(selected)
        game["artifact_chat_history_index"] = idx
    else:
        if idx is None:
            tui._set_state(tui.state.with_updates(header_logo_game=game))
            return
        if idx < len(history) - 1:
            idx += 1
            selected = history[idx]
            game["artifact_chat_input"] = selected
            game["artifact_chat_cursor"] = len(selected)
            game["artifact_chat_history_index"] = idx
        else:
            draft = str(game.get("artifact_chat_saved_draft") or "")
            game["artifact_chat_input"] = draft
            game["artifact_chat_cursor"] = len(draft)
            game["artifact_chat_history_index"] = None
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def artifact_chat_clear_input(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or {})
    game["artifact_chat_input"] = ""
    game["artifact_chat_cursor"] = 0
    game["artifact_chat_history_index"] = None
    tui._set_state(tui.state.with_updates(header_logo_game=game, status_message="artifact chat: input cleared"))


def artifact_chat_send_message(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or {})
    text = str(game.get("artifact_chat_input") or "").strip()
    if not text:
        return
    game["artifact_chat_input"] = ""
    game["artifact_chat_cursor"] = 0
    history = [str(item) for item in (game.get("artifact_chat_history") or []) if str(item).strip()]
    if not history or history[-1] != text:
        history.append(text)
    game["artifact_chat_history"] = history[-50:]
    game["artifact_chat_history_index"] = None
    game["artifact_chat_saved_draft"] = ""
    artifact_chat = dict(game.get("artifact_chat_state") or {})
    messages = [dict(m) for m in (artifact_chat.get("messages") or []) if isinstance(m, dict)]
    messages.append({"at": time.time(), "source": "user", "text": text})
    artifact_chat["messages"] = messages[-12:]
    game["artifact_chat_state"] = artifact_chat
    from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state, switch_channel, append_message, make_message
    chat = get_chat_state(game)
    switch_channel(chat, "ai:tutor", preserve_input=True)
    msg = make_message(
        channel_id="ai:tutor",
        channel_type="ai",
        sender_id=str(game.get("local_snake_id") or "s1"),
        sender_kind="user",
        text=text,
        visibility="ai_context",
        delivery_state="sent",
    )
    append_message(chat, msg)
    set_chat_state(game, chat)
    game["tutor_ask_question"] = text
    game["tutor_ask_at"] = time.monotonic()
    game["tutor_ask_section_id"] = tui.state.section_id
    timeout_s = tui._chat_ask_timeout_seconds()
    game["tutor_ask_timeout_s"] = timeout_s
    game["tutor_ask_deadline_at"] = float(game["tutor_ask_at"]) + timeout_s
    game["tutor_ask_answered"] = False
    game["_ask_submitted"] = False
    game["active"] = True
    game["alive"] = True
    game["ui_steering"] = False
    game["free_mode"] = False
    chat["ai_typing"] = True
    set_chat_state(game, chat)
    tui._set_state(tui.state.with_updates(header_logo_game=game, status_message=f"ask: {text[:40]}"))


# ── Chat scroll / snapshot / copy ────────────────────────────────────────

def chat_scroll(tui: InteractiveOperatorTui, delta: int) -> None:
    game = dict(tui.state.header_logo_game or {})
    from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
    chat = get_chat_state(game)
    current = int(chat.get("scroll_offset") or 0)
    chat["scroll_offset"] = max(0, current + delta)
    set_chat_state(game, chat)
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def copy_chat_panel_snapshot(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or {})
    from client_surfaces.operator_tui.chat_state import get_chat_state, sanitize_text
    chat = get_chat_state(game)
    active_ch_id = str(chat.get("active_channel") or "room:main")
    channels = chat.get("channels") if isinstance(chat.get("channels"), dict) else {}
    ch = channels.get(active_ch_id) if isinstance(channels, dict) else {}
    if not isinstance(ch, dict):
        ch = {}
    display_name = str(ch.get("display_name") or active_ch_id)
    lines = [f"CHAT {display_name} ({active_ch_id})"]
    msgs = [m for m in (ch.get("messages") or []) if isinstance(m, dict)]
    for msg in msgs[-80:]:
        sender_kind = str(msg.get("sender_kind") or "user")
        sender_id = str(msg.get("sender_id") or "?")
        if sender_kind == "ai" or sender_id == "s-ai":
            sender = "AI-snake"
        elif sender_kind == "system":
            sender = "system"
        else:
            sender = "user"
        created_at = msg.get("created_at")
        if isinstance(created_at, (int, float)):
            ts = time.strftime("%H:%M", time.localtime(float(created_at)))
        else:
            ts = "--:--"
        text = sanitize_text(str(msg.get("text") or ""), max_len=6000)
        if text:
            lines.append(f"[{ts}] {sender}: {text}")
    copied = "\n".join(lines).strip()
    game["clipboard"] = copied
    ok = tui._copy_to_system_clipboard(copied) if copied else False
    status = "chat copy: intern + System-Zwischenablage" if ok else "chat copy: intern"
    tui._set_state(tui.state.with_updates(header_logo_game=game, status_message=status))


def copy_ai_status_snapshot(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or {})
    lines = ["AI-SNAKE STATUS"]
    lines.append(f"tutorial_mode={bool(game.get('tutorial_mode'))}")
    lines.append(f"chat_panel_open={bool(game.get('chat_panel_open'))}")
    lines.append(f"ai_snake_mode={str(game.get('ai_snake_mode') or 'lurking_follow')}")
    lines.append(f"runtime_status={str(game.get('ai_snake_runtime_status') or 'idle')}")
    lines.append(f"provider={str(game.get('ai_snake_provider_preference') or 'lmstudio')}")
    lines.append(f"model={str(game.get('ai_snake_provider_model') or 'ananta-smoke')}")
    monitor = game.get("ai_snake_monitor_log")
    rows = [dict(item) for item in monitor if isinstance(item, dict)] if isinstance(monitor, list) else []
    if rows:
        lines.append("events:")
        for item in rows[-20:]:
            created_at = item.get("created_at")
            ts = time.strftime("%H:%M", time.localtime(float(created_at))) if isinstance(created_at, (int, float)) else "--:--"
            label = str(item.get("label") or item.get("event") or "event")
            lines.append(f"- {ts} {label}")
    copied = "\n".join(lines).strip()
    game["clipboard"] = copied
    ok = tui._copy_to_system_clipboard(copied) if copied else False
    status = "ai status copy: intern + System-Zwischenablage" if ok else "ai status copy: intern"
    tui._set_state(tui.state.with_updates(header_logo_game=game, status_message=status))


def current_rendered_text(tui: InteractiveOperatorTui) -> str:
    rendered = str(tui._rendered_text or "")
    if rendered.strip():
        return rendered
    return tui._render()


def copy_tui_snapshot(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or {})
    copied = rendered_tui_snapshot_text(current_rendered_text(tui))
    game["clipboard"] = copied
    ok = tui._copy_to_system_clipboard(copied) if copied.strip() else False
    status = "tui snapshot: intern + System-Zwischenablage" if ok else "tui snapshot: intern"
    tui._set_state(tui.state.with_updates(header_logo_game=game, status_message=status))


def save_tui_snapshot(tui: InteractiveOperatorTui) -> None:
    try:
        target = write_tui_snapshot(current_rendered_text(tui))
    except OSError as exc:
        tui._set_state(tui.state.with_updates(status_message=f"tui snapshot speichern fehlgeschlagen: {exc}"))
        return
    game = dict(tui.state.header_logo_game or {})
    game["last_tui_snapshot_path"] = str(target)
    tui._set_state(
        tui.state.with_updates(
            header_logo_game=game,
            status_message=f"tui snapshot gespeichert: {target}",
        )
    )


# ── Long chat message ────────────────────────────────────────────────────

def open_latest_long_chat_message(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    active_view = str(game.get("visual_viewport_active_view") or game.get("visual_runtime_status", {}).get("active_view") or "")

    if is_showing_chat_long_message(game):
        new_mode = toggle_render_mode(game)
        mode_label = "Plain-Text" if new_mode == "plain" else "Markdown/Mermaid gerendert"
        tui._set_state(
            tui.state.with_updates(
                header_logo_game=game,
                status_message=f"Chat-Ansicht: {mode_label}",
            )
        )
        return

    if bool(game.get("visual_viewport_enabled")) and active_view == "markdown_mermaid_document":
        plain = bool(game.get("markdown_stream_plain"))
        mermaid_on = bool(game.get("markdown_mermaid_render_requested"))
        if plain:
            game["markdown_stream_plain"] = False
            game["markdown_mermaid_render_requested"] = False
            mode_label = "Markdown gerendert"
        elif not mermaid_on:
            game["markdown_stream_plain"] = False
            game["markdown_mermaid_render_requested"] = True
            mode_label = "Markdown+Mermaid"
        else:
            game["markdown_stream_plain"] = True
            game["markdown_mermaid_render_requested"] = False
            mode_label = "Simple"
        game["visual_viewport_force_render"] = True
        tui._set_state(
            tui.state.with_updates(
                header_logo_game=game,
                status_message=f"Doc-Ansicht: {mode_label}",
            )
        )
        return

    from client_surfaces.operator_tui.chat_state import get_chat_state
    chat = get_chat_state(game)
    channels = chat.get("channels") if isinstance(chat.get("channels"), dict) else {}
    active_ch_id = str(chat.get("active_channel") or "room:main")
    channel = channels.get(active_ch_id) if isinstance(channels, dict) else {}
    if not isinstance(channel, dict):
        channel = {}
    message = latest_long_message_for_channel(channel)
    if message is None:
        tui._set_state(tui.state.with_updates(status_message="keine lange Chatnachricht im aktiven Kanal"))
        return

    configure_middle_view_for_message(
        game,
        message,
        channel_id=active_ch_id,
        streaming=False,
        plain_text=True,
    )
    from client_surfaces.operator_tui.tab_manager import open_or_activate_tab, tab_label_for_chat_preview
    preview = str(message.get("text") or message.get("preview") or "Chat")
    label = tab_label_for_chat_preview(preview)
    vp_state = {"scroll_offset": 0, "preview": preview[:80]}
    next_state = open_or_activate_tab(
        tui.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT),
        section_id=tui.state.section_id,
        kind="chat_viewport",
        label=label,
        viewport_state=vp_state,
    )
    game_out = dict(next_state.header_logo_game or game)
    game_out["visual_viewport_enabled"] = True
    tui._set_state(next_state.with_updates(
        header_logo_game=game_out,
        status_message="lange Chatnachricht: Originalausgabe",
    ))
