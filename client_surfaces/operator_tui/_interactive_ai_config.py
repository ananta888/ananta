from __future__ import annotations

from typing import TYPE_CHECKING

from client_surfaces.operator_tui.ai_snake_config_view import (
    ai_snake_config_filter_options,
    ai_snake_config_items,
    ai_snake_config_options,
    apply_ai_snake_config_value,
    refresh_chat_backend_models,
)
from client_surfaces.operator_tui.models import FocusPane, OperatorMode

if TYPE_CHECKING:
    from client_surfaces.operator_tui.interactive import InteractiveOperatorTui


def toggle_ai_snake_config_panel(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    opened = not bool(game.get("ai_snake_config_open"))
    game["ai_snake_config_open"] = opened
    if opened:
        game["artifact_chat_focus"] = False
        from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
        chat = get_chat_state(game)
        chat["chat_focus"] = False
        set_chat_state(game, chat)
        tui._command_buffer = ""
        tui._command_cursor = 0
        tui._command_history_index = None
        tui._command_saved_draft = ""
        game["ai_snake_config_combo"] = {
            "open": False,
            "key": "",
            "filter": "",
            "filter_cursor": 0,
            "selected_option": 0,
        }
        tui._set_state(tui.state.with_updates(
            header_logo_game=game,
            focus=FocusPane.CONTENT,
            mode=OperatorMode.NORMAL,
            command_line="",
            selected_index=0,
            status_message="ai config: offen",
        ))
        return
    game["ai_snake_config_combo"] = {"open": False}
    tui._set_state(tui.state.with_updates(header_logo_game=game, status_message="ai config: geschlossen"))


def toggle_ai_snake_config_selected(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    items = ai_snake_config_items(game)
    if not items:
        tui._set_state(tui.state.with_updates(header_logo_game=game, status_message="ai config: keine felder"))
        return
    idx = max(0, min(len(items) - 1, int(tui.state.selected_index)))
    key = str(items[idx].get("key") or "")
    tui._open_ai_snake_config_combo(game, key=key, idx=idx)


def ai_snake_config_combo_active(tui: InteractiveOperatorTui, game=None) -> bool:
    snapshot = game if isinstance(game, dict) else dict(tui.state.header_logo_game or {})
    combo = snapshot.get("ai_snake_config_combo")
    return isinstance(combo, dict) and bool(combo.get("open"))


def ai_snake_config_next_index(tui: InteractiveOperatorTui, delta: int, game=None) -> int:
    snapshot = game if isinstance(game, dict) else dict(tui.state.header_logo_game or {})
    items = ai_snake_config_items(snapshot)
    if not items:
        return 0
    cur = max(0, min(len(items) - 1, int(tui.state.selected_index)))
    return max(0, min(len(items) - 1, cur + int(delta)))


def open_ai_snake_config_combo(tui: InteractiveOperatorTui, game, *, key: str, idx: int) -> None:
    if key == "chat_model":
        _, fetch_error = refresh_chat_backend_models(game, force=True)
    else:
        fetch_error = ""
    options = ai_snake_config_options(game, key=key)
    if not options:
        status = "ai config: keine optionen"
        if key == "chat_model" and fetch_error:
            status = f"ai config: chat model fetch fehlgeschlagen ({fetch_error})"
        tui._set_state(tui.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT, selected_index=idx, status_message=status))
        return
    game["ai_snake_config_combo"] = {
        "open": True,
        "key": key,
        "filter": "",
        "filter_cursor": 0,
        "selected_option": 0,
    }
    tui._set_state(tui.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT, selected_index=idx, status_message=f"ai config: auswahl für {key}"))


def ai_snake_config_combo_close(tui: InteractiveOperatorTui, *, status: str = "ai config: auswahl geschlossen") -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    game["ai_snake_config_combo"] = {"open": False}
    tui._set_state(tui.state.with_updates(header_logo_game=game, status_message=status))


def ai_snake_config_combo_filter_text(tui: InteractiveOperatorTui, combo) -> str:
    return str(combo.get("filter") or "")


def ai_snake_config_combo_apply(tui: InteractiveOperatorTui, game, *, value: str) -> None:
    combo = dict(game.get("ai_snake_config_combo") or {})
    key = str(combo.get("key") or "")
    idx = max(0, int(tui.state.selected_index))
    status = apply_ai_snake_config_value(game, key=key, value=value)
    game["ai_snake_config_combo"] = {"open": False}
    if key == "visual_enabled" and not bool(game.get("tutorial_mode")):
        tui._disable_visual_ai_snake_runtime(game)
        game["ai_snake_config_open"] = True
    tui._set_state(tui.state.with_updates(header_logo_game=game, focus=FocusPane.CONTENT, selected_index=idx, status_message=status))


def ai_snake_config_combo_commit(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    combo = dict(game.get("ai_snake_config_combo") or {})
    key = str(combo.get("key") or "")
    filter_text = tui._ai_snake_config_combo_filter_text(combo)
    options, _ = ai_snake_config_filter_options(game, key=key, regex_filter=filter_text)
    if filter_text.strip():
        tui._ai_snake_config_combo_apply(game, value=filter_text.strip())
        return
    if not options:
        tui._set_state(tui.state.with_updates(header_logo_game=game, status_message="ai config: keine treffer"))
        return
    selected = max(0, min(len(options) - 1, int(combo.get("selected_option") or 0)))
    tui._ai_snake_config_combo_apply(game, value=options[selected])


def ai_snake_config_combo_move(tui: InteractiveOperatorTui, delta: int) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    combo = dict(game.get("ai_snake_config_combo") or {})
    if not bool(combo.get("open")):
        return
    key = str(combo.get("key") or "")
    options, _ = ai_snake_config_filter_options(game, key=key, regex_filter=tui._ai_snake_config_combo_filter_text(combo))
    if not options:
        combo["selected_option"] = 0
    else:
        cur = max(0, min(len(options) - 1, int(combo.get("selected_option") or 0)))
        combo["selected_option"] = max(0, min(len(options) - 1, cur + int(delta)))
    game["ai_snake_config_combo"] = combo
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def ai_snake_config_combo_append_filter(tui: InteractiveOperatorTui, ch: str) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    combo = dict(game.get("ai_snake_config_combo") or {})
    if not bool(combo.get("open")):
        return
    buf = str(combo.get("filter") or "")
    cursor = max(0, min(len(buf), int(combo.get("filter_cursor") or len(buf))))
    next_buf = buf[:cursor] + ch + buf[cursor:]
    combo["filter"] = next_buf
    combo["filter_cursor"] = min(len(next_buf), cursor + len(ch))
    combo["selected_option"] = 0
    game["ai_snake_config_combo"] = combo
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def ai_snake_config_combo_backspace(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    combo = dict(game.get("ai_snake_config_combo") or {})
    if not bool(combo.get("open")):
        return
    buf = str(combo.get("filter") or "")
    cursor = max(0, min(len(buf), int(combo.get("filter_cursor") or len(buf))))
    if cursor <= 0:
        tui._set_state(tui.state.with_updates(header_logo_game=game))
        return
    combo["filter"] = buf[:cursor - 1] + buf[cursor:]
    combo["filter_cursor"] = cursor - 1
    combo["selected_option"] = 0
    game["ai_snake_config_combo"] = combo
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def ai_snake_config_combo_delete(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    combo = dict(game.get("ai_snake_config_combo") or {})
    if not bool(combo.get("open")):
        return
    buf = str(combo.get("filter") or "")
    cursor = max(0, min(len(buf), int(combo.get("filter_cursor") or len(buf))))
    if cursor >= len(buf):
        tui._set_state(tui.state.with_updates(header_logo_game=game))
        return
    combo["filter"] = buf[:cursor] + buf[cursor + 1:]
    combo["filter_cursor"] = cursor
    combo["selected_option"] = 0
    game["ai_snake_config_combo"] = combo
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def ai_snake_config_combo_move_cursor(tui: InteractiveOperatorTui, delta: int) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    combo = dict(game.get("ai_snake_config_combo") or {})
    if not bool(combo.get("open")):
        return
    buf = str(combo.get("filter") or "")
    cursor = max(0, min(len(buf), int(combo.get("filter_cursor") or len(buf))))
    combo["filter_cursor"] = max(0, min(len(buf), cursor + int(delta)))
    game["ai_snake_config_combo"] = combo
    tui._set_state(tui.state.with_updates(header_logo_game=game))


def ai_snake_config_combo_select_value(tui: InteractiveOperatorTui, *, value: str) -> None:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    tui._ai_snake_config_combo_apply(game, value=value)
