from __future__ import annotations

from typing import TYPE_CHECKING, Any

from client_surfaces.operator_tui.chat_long_message import (
    configure_middle_view_for_history_entry,
    long_message_history_rows,
)
from client_surfaces.operator_tui.models import FocusPane, OperatorMode
from client_surfaces.operator_tui.sections import SECTIONS

if TYPE_CHECKING:
    from client_surfaces.operator_tui.interactive import InteractiveOperatorTui


def normal_or_text(tui: InteractiveOperatorTui, text: str, normal_action) -> None:
    if tui._snake_message_mode_active():
        tui._snake_message_append(text)
        return
    if tui._artifact_chat_focus_active():
        tui._artifact_chat_append(text)
        return
    if tui.state.mode is OperatorMode.COMMAND:
        tui._append_command(text)
        return
    if tui._chat_focus_active():
        tui._chat_append(text)
        return
    if tui._snake_mode_active():
        return
    normal_action()


def handle_enter_key(tui: InteractiveOperatorTui) -> None:
    game = tui.state.header_logo_game if isinstance(tui.state.header_logo_game, dict) else {}
    if tui.state.mode is OperatorMode.COMMAND:
        tui._command_commit_history()
        tui._run_command(tui._command_buffer)
        return
    if tui._artifact_chat_focus_active():
        tui._artifact_chat_send_message()
        return
    if tui._chat_focus_active():
        tui._chat_send_message()
        return
    if tui._audit_viewer_active():
        if tui._audit_cleanup_result_mode_active():
            tui._audit_cleanup_close_viewer(status_message="cleanup viewer geschlossen")
            return
        if tui._confirm_audit_cleanup_action():
            return
        return
    if tui._template_editor_active():
        tui._template_editor_insert_text("\n")
        return
    if bool(game.get("ai_snake_config_open")):
        if tui.state.focus is not FocusPane.CONTENT:
            tui._set_state(tui.state.with_updates(focus=FocusPane.CONTENT))
            game = tui.state.header_logo_game if isinstance(tui.state.header_logo_game, dict) else {}
        if tui._ai_snake_config_combo_active(game):
            tui._ai_snake_config_combo_commit()
        else:
            tui._toggle_ai_snake_config_selected()
        return
    if tui._snake_message_mode_active():
        tui._snake_commit_message()
        return
    if tui.state.focus is FocusPane.NAVIGATION:
        if 0 <= tui.state.selected_index < len(SECTIONS):
            section = SECTIONS[tui.state.selected_index]
            tui._run_command(f":section {section.id}")
            tui._set_state(tui.state.with_updates(focus=FocusPane.CONTENT, selected_index=0))
            return
        template_selection = tui._template_nav_item_for_nav_index(tui.state.selected_index)
        if template_selection is not None:
            item_index, item = template_selection
            next_state = tui.state.with_updates(focus=FocusPane.CONTENT, selected_index=item_index, section_id="templates")
            tui._set_state(next_state)
            if not tui._open_template_editor_for_selected():
                tui._run_command(":inspect")
            return
        audit_selection = tui._audit_nav_item_for_nav_index(tui.state.selected_index)
        if audit_selection is not None:
            item_index, _ = audit_selection
            next_state = tui.state.with_updates(focus=FocusPane.CONTENT, selected_index=item_index, section_id="audit")
            tui._set_state(next_state)
            tui._open_audit_viewer_for_selected()
            return
        history_idx = (
            tui.state.selected_index
            - len(SECTIONS)
            - tui._template_nav_selectable_count()
            - tui._audit_nav_selectable_count()
        )
        game = dict(tui.state.header_logo_game or tui._default_header_snake())
        rows = long_message_history_rows(game)
        if 0 <= history_idx < len(rows) and configure_middle_view_for_history_entry(game, rows[history_idx]):
            tui._set_state(
                tui.state.with_updates(
                    header_logo_game=game,
                    focus=FocusPane.CONTENT,
                    selected_index=0,
                    status_message="Chat-History: Originalausgabe",
                )
            )
        return
    if tui.state.focus is FocusPane.CONTENT and tui.state.section_id == "templates":
        if tui._open_template_editor_for_selected():
            return
    if tui.state.focus is FocusPane.CONTENT and tui.state.section_id == "audit":
        if tui._open_audit_viewer_for_selected():
            return
    if tui._snake_mode_active():
        game = tui.state.header_logo_game or {}
        ts_raw = game.get("tutorial_state")
        if isinstance(ts_raw, dict) and ts_raw.get("guided"):
            tui._advance_guided_tour_now()
        return
    if tui.state.focus is FocusPane.HEADER:
        from client_surfaces.operator_tui.header_config import CONFIG_ITEMS, cycle_value

        if 0 <= tui.state.selected_index < len(CONFIG_ITEMS):
            tui._set_state(cycle_value(tui.state, CONFIG_ITEMS[tui.state.selected_index]))
        return
    tui._run_command(":inspect")


def cancel_active_input_mode(tui: InteractiveOperatorTui) -> bool:
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    if tui._ai_snake_config_combo_active(game):
        tui._ai_snake_config_combo_close(status="input: config-auswahl beendet")
        return True
    if tui.state.mode is OperatorMode.COMMAND:
        tui._command_reset()
        tui._set_state(tui.state.with_updates(mode=OperatorMode.NORMAL, status_message="input: command beendet"))
        return True
    if tui._snake_message_mode_active():
        tui._snake_cancel_message()
        return True
    return False


def append_command(tui: InteractiveOperatorTui, text: str) -> None:
    cursor = max(0, min(len(tui._command_buffer), int(tui._command_cursor)))
    tui._command_buffer = tui._command_buffer[:cursor] + text + tui._command_buffer[cursor:]
    tui._command_cursor = min(len(tui._command_buffer), cursor + len(text))
    tui._command_history_index = None
    tui._sync_command_line_state()


def command_backspace(tui: InteractiveOperatorTui) -> None:
    if not tui._command_buffer:
        game = dict(tui.state.header_logo_game or {})
        game["command_input_cursor"] = 0
        tui._command_cursor = 0
        tui._command_history_index = None
        tui._command_saved_draft = ""
        tui._set_state(
            tui.state.with_updates(
                header_logo_game=game,
                mode=OperatorMode.NORMAL,
                command_line="",
                status_message="command: beendet",
            )
        )
        return
    cursor = max(0, min(len(tui._command_buffer), int(tui._command_cursor)))
    if cursor <= 0:
        tui._sync_command_line_state()
        return
    tui._command_buffer = tui._command_buffer[:cursor - 1] + tui._command_buffer[cursor:]
    tui._command_cursor = max(0, cursor - 1)
    tui._command_history_index = None
    tui._sync_command_line_state()


def command_delete(tui: InteractiveOperatorTui) -> None:
    cursor = max(0, min(len(tui._command_buffer), int(tui._command_cursor)))
    if cursor >= len(tui._command_buffer):
        tui._sync_command_line_state()
        return
    tui._command_buffer = tui._command_buffer[:cursor] + tui._command_buffer[cursor + 1:]
    tui._command_history_index = None
    tui._sync_command_line_state()


def command_move_cursor(tui: InteractiveOperatorTui, delta: int) -> None:
    cursor = max(0, min(len(tui._command_buffer), int(tui._command_cursor)))
    tui._command_cursor = max(0, min(len(tui._command_buffer), cursor + int(delta)))
    tui._sync_command_line_state()


def command_history_move(tui: InteractiveOperatorTui, delta: int) -> None:
    history = [str(item) for item in tui._command_history if str(item).strip()]
    if not history:
        return
    idx_raw = tui._command_history_index
    if idx_raw is None:
        tui._command_saved_draft = tui._command_buffer
        idx = len(history)
    else:
        idx = max(0, min(len(history), int(idx_raw)))
    next_idx = idx + int(delta)
    if next_idx < 0:
        next_idx = 0
    if next_idx >= len(history):
        tui._command_history_index = None
        tui._command_buffer = tui._command_saved_draft
        tui._command_cursor = len(tui._command_buffer)
        tui._sync_command_line_state()
        return
    tui._command_history_index = next_idx
    tui._command_buffer = history[next_idx]
    tui._command_cursor = len(tui._command_buffer)
    tui._sync_command_line_state()


def input_history_config(tui: InteractiveOperatorTui) -> dict[str, Any]:
    try:
        from client_surfaces.operator_tui.config.user_config_manager import load_user_config
        return load_user_config()
    except Exception:
        return {}


def load_input_histories(tui: InteractiveOperatorTui) -> None:
    try:
        cfg = tui._input_history_config()
        if cfg.get("input_history_command_enabled", True):
            saved = cfg.get("command_input_history", [])
            if isinstance(saved, list):
                tui._command_history = [str(e) for e in saved if str(e).strip()]
    except Exception:
        pass


def apply_input_history_to_game(tui: InteractiveOperatorTui, game: dict[str, Any]) -> None:
    try:
        cfg = tui._input_history_config()
        if cfg.get("input_history_chat_enabled", True):
            saved = cfg.get("chat_input_history", [])
            if isinstance(saved, list) and saved:
                from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state
                chat = get_chat_state(game)
                existing = list(chat.get("chat_input_history") or [])
                for entry in reversed(saved):
                    if entry not in existing:
                        existing.insert(0, entry)
                max_entries = int(cfg.get("input_history_max_entries", 100))
                chat["chat_input_history"] = existing[-max_entries:]
                set_chat_state(game, chat)
    except Exception:
        pass


def save_command_to_history(tui: InteractiveOperatorTui, text: str) -> None:
    try:
        cfg = tui._input_history_config()
        if not cfg.get("input_history_command_enabled", True):
            return
        max_entries = int(cfg.get("input_history_max_entries", 100))
        history = list(tui._command_history)[-max_entries:]
        from client_surfaces.operator_tui.config.user_config_manager import save_user_config
        save_user_config({"command_input_history": history})
    except Exception:
        pass


def save_chat_to_history(tui: InteractiveOperatorTui, text: str) -> None:
    try:
        cfg = tui._input_history_config()
        if not cfg.get("input_history_chat_enabled", True):
            return
        max_entries = int(cfg.get("input_history_max_entries", 100))
        current = cfg.get("chat_input_history", [])
        if not isinstance(current, list):
            current = []
        if not current or current[-1] != text:
            current = current + [text]
        current = current[-max_entries:]
        from client_surfaces.operator_tui.config.user_config_manager import save_user_config
        save_user_config({"chat_input_history": current})
    except Exception:
        pass


def command_commit_history(tui: InteractiveOperatorTui) -> None:
    text = str(tui._command_buffer).strip()
    if not text:
        return
    if not tui._command_history or tui._command_history[-1] != text:
        tui._command_history.append(text)
    tui._command_history = tui._command_history[-100:]
    tui._command_history_index = None
    tui._command_saved_draft = ""
    tui._save_command_to_history(text)


def command_reset(tui: InteractiveOperatorTui) -> None:
    tui._command_buffer = ""
    tui._command_cursor = 0
    tui._command_history_index = None
    tui._command_saved_draft = ""
    tui._sync_command_line_state()


def open_command_mode(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or {})
    tui._command_buffer = ""
    tui._command_cursor = 0
    tui._command_history_index = None
    tui._command_saved_draft = ""
    game["command_input_cursor"] = tui._command_cursor
    tui._set_state(tui.state.with_updates(header_logo_game=game, mode=OperatorMode.COMMAND, command_line=tui._command_buffer))


def exit_command_mode_for_global_shortcut(tui: InteractiveOperatorTui) -> None:
    if tui.state.mode is not OperatorMode.COMMAND:
        return
    game = dict(tui.state.header_logo_game or {})
    tui._command_buffer = ""
    tui._command_cursor = 0
    tui._command_history_index = None
    tui._command_saved_draft = ""
    game["command_input_cursor"] = 0
    tui._set_state(
        tui.state.with_updates(
            header_logo_game=game,
            mode=OperatorMode.NORMAL,
            command_line="",
        )
    )


def enter_command_mode_from_anywhere(tui: InteractiveOperatorTui) -> None:
    if tui._chat_focus_active():
        tui._chat_focus_leave()
    if tui._artifact_chat_focus_active():
        tui._artifact_chat_focus_leave(clear=False)
    if tui._snake_message_mode_active():
        tui._snake_cancel_message()
    game = dict(tui.state.header_logo_game or tui._default_header_snake())
    if bool(game.get("ai_snake_config_open")):
        game["ai_snake_config_open"] = False
        game["ai_snake_config_combo"] = {"open": False}
        tui._set_state(
            tui.state.with_updates(
                header_logo_game=game,
                mode=OperatorMode.NORMAL,
                command_line="",
            )
        )
    tui._open_command_mode()


def sync_command_line_state(tui: InteractiveOperatorTui) -> None:
    game = dict(tui.state.header_logo_game or {})
    game["command_input_cursor"] = max(0, min(len(tui._command_buffer), int(tui._command_cursor)))
    n = len(tui._command_history)
    game["_command_history_count"] = n if n > 0 else None
    tui._set_state(tui.state.with_updates(header_logo_game=game, command_line=tui._command_buffer))
