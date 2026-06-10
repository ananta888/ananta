from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys

from client_surfaces.operator_tui.chat_long_message import (
    configure_middle_view_for_history_entry,
    is_showing_chat_long_message,
    long_message_history_rows,
    refresh_rendered_view,
)
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.keybindings_config import key_for_action
from client_surfaces.operator_tui.models import FocusPane, OperatorMode
from client_surfaces.operator_tui.sections import SECTIONS, get_section

if TYPE_CHECKING:
    from client_surfaces.operator_tui.interactive import InteractiveOperatorTui


def build_keybindings(tui: InteractiveOperatorTui) -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add(key_for_action("quit", "c-q"))
    def _(event) -> None:
        tui._handle_quit_key(event)

    @bindings.add(":")
    def _(event) -> None:
        if tui.state.mode is OperatorMode.COMMAND:
            tui._append_command(":")
            return
        if tui._snake_message_mode_active():
            tui._snake_message_append(":")
            return
        if tui._snake_mode_active():
            tui._enter_command_mode_from_anywhere()
            return
        if tui._chat_focus_active():
            tui._chat_append(":")
            return
        tui._open_command_mode()

    @bindings.add("/")
    def _(event) -> None:
        if tui._snake_message_mode_active():
            tui._snake_message_append("/")
            return
        if tui.state.mode is OperatorMode.COMMAND:
            tui._append_command("/")
            return
        tui._enter_command_mode_from_anywhere()

    @bindings.add("enter")
    @bindings.add("c-m")
    @bindings.add("c-j")
    def _(event) -> None:
        tui._handle_enter_key()

    @bindings.add("escape")
    def _(event) -> None:
        tui._escape_to_start_state()

    @bindings.add("backspace")
    @bindings.add("c-h")
    def _(event) -> None:
        game = tui.state.header_logo_game if isinstance(tui.state.header_logo_game, dict) else {}
        if tui.state.mode is OperatorMode.COMMAND:
            tui._command_backspace()
            return
        if tui._artifact_chat_focus_active():
            tui._artifact_chat_backspace()
            return
        if tui._chat_focus_active():
            tui._chat_backspace()
            return
        if tui._audit_viewer_active():
            return
        if tui._template_editor_active():
            tui._template_editor_backspace()
            return
        if tui._ai_snake_config_combo_active(game):
            tui._ai_snake_config_combo_backspace()
            return
        if tui._snake_message_mode_active():
            tui._snake_message_backspace()
            return
        if tui._snake_mode_active():
            return

    @bindings.add("delete")
    def _(event) -> None:
        game = tui.state.header_logo_game if isinstance(tui.state.header_logo_game, dict) else {}
        if tui.state.mode is OperatorMode.COMMAND:
            tui._command_delete()
            return
        if tui._artifact_chat_focus_active():
            tui._artifact_chat_delete()
            return
        if tui._chat_focus_active():
            tui._chat_delete()
            return
        if tui._audit_viewer_active():
            return
        if tui._template_editor_active():
            tui._template_editor_delete()
            return
        if tui._ai_snake_config_combo_active(game):
            tui._ai_snake_config_combo_delete()
            return
        if tui._snake_message_mode_active():
            return
        if tui._snake_mode_active():
            return

    @bindings.add(key_for_action("selection_down", "c-j"))
    def _(event) -> None:
        game = tui.state.header_logo_game if isinstance(tui.state.header_logo_game, dict) else {}
        if bool(game.get("ai_snake_config_open")) and tui.state.focus is FocusPane.CONTENT:
            if tui._ai_snake_config_combo_active(game):
                tui._ai_snake_config_combo_move(1)
            else:
                tui._set_state(tui.state.with_updates(selected_index=tui._ai_snake_config_next_index(1, game)))
            return
        def _j():
            tui._set_selected_index(tui._clamp_down())
        tui._normal_or_text("j", _j)

    @bindings.add(key_for_action("selection_up", "c-k"))
    def _(event) -> None:
        game = tui.state.header_logo_game if isinstance(tui.state.header_logo_game, dict) else {}
        if bool(game.get("ai_snake_config_open")) and tui.state.focus is FocusPane.CONTENT:
            if tui._ai_snake_config_combo_active(game):
                tui._ai_snake_config_combo_move(-1)
            else:
                tui._set_state(tui.state.with_updates(selected_index=tui._ai_snake_config_next_index(-1, game)))
            return
        tui._normal_or_text("k", lambda: tui._set_selected_index(max(0, tui.state.selected_index - 1)))

    @bindings.add(key_for_action("inspect", "c-f"))
    def _(event) -> None:
        def _e():
            if tui.state.section_id == "templates" and tui.state.focus is FocusPane.CONTENT:
                if tui._open_template_editor_for_selected():
                    return
            if tui.state.section_id == "audit" and tui.state.focus is FocusPane.CONTENT:
                if tui._open_audit_viewer_for_selected():
                    return
            if tui._open_selected_item_inline():
                return
            section = get_section(tui.state.section_id)
            payload = (tui.state.section_payloads or {}).get(section.id, {})
            plugin = tui._plugins.launcher_for(payload, tui.state.selected_index)
            if plugin is None:
                return
            async def _run():
                await event.app.run_in_terminal(
                    lambda: plugin.launch(payload, tui.state.selected_index)
                )
            event.app.create_background_task(_run())
        tui._normal_or_text("e", _e)

    @bindings.add(key_for_action("focus_left", "c-a"))
    def _(event) -> None:
        tui._normal_or_text("h", lambda: tui._move_focus(-1))

    @bindings.add(key_for_action("focus_right", "c-d"))
    def _(event) -> None:
        tui._normal_or_text("l", lambda: tui._move_focus(1))

    @bindings.add(key_for_action("refresh", "c-r"))
    def _(event) -> None:
        game = dict(tui.state.header_logo_game or {})
        if is_showing_chat_long_message(game):
            refresh_rendered_view(game)
            tui._set_state(tui.state.with_updates(header_logo_game=game, status_message="Chat-Ansicht: Render aktualisiert"))
            return
        tui._normal_or_text("r", lambda: tui._run_command(":refresh"))

    @bindings.add(key_for_action("help", "c-y"))
    def _(event) -> None:
        tui._normal_or_text("?", lambda: tui._run_command(":help"))

    @bindings.add(key_for_action("cycle_focus_or_channel", "c-w"))
    def _(event) -> None:
        if tui._chat_focus_active() or tui._artifact_chat_focus_active() or tui._snake_mode_active():
            tui._chat_cycle_channel()
            return
        if tui.state.open_tabs and tui.state.mode is OperatorMode.NORMAL:
            tui._tab_close_active()
            return
        tui._exit_command_mode_for_global_shortcut()
        tui._move_focus(1)

    @bindings.add(key_for_action("tab_next", "c-right"))
    def _(event) -> None:
        if tui.state.mode is OperatorMode.COMMAND:
            return
        tui._tab_cycle(1)

    @bindings.add(key_for_action("tab_prev", "c-left"))
    def _(event) -> None:
        if tui.state.mode is OperatorMode.COMMAND:
            return
        tui._tab_cycle(-1)

    @bindings.add(key_for_action("snake_pause", "c-p"))
    def _(event) -> None:
        if tui.state.mode is OperatorMode.COMMAND:
            return
        if not tui._snake_mode_active():
            return
        tui._toggle_snake_pause()

    @bindings.add(key_for_action("toggle_snake_mode", "c-s"))
    def _(event) -> None:
        if tui._template_editor_active():
            tui._template_editor_save()
            return
        tui._exit_command_mode_for_global_shortcut()
        tui._toggle_snake_mode()

    @bindings.add(key_for_action("chat_focus", "c-e"))
    def _(event) -> None:
        tui._exit_command_mode_for_global_shortcut()
        tui._toggle_chat_focus()

    @bindings.add(key_for_action("toggle_chat_panel", "c-g"))
    def _(event) -> None:
        tui._exit_command_mode_for_global_shortcut()
        tui._toggle_chat_panel_open()

    @bindings.add(key_for_action("copy_chat_panel", "c-c"))
    def _(event) -> None:
        if tui._snake_mode_active():
            tui._snake_copy_selection()
            return
        tui._copy_chat_panel_snapshot()

    @bindings.add(key_for_action("copy_tui_snapshot", "c-\\"))
    def _(event) -> None:
        tui._exit_command_mode_for_global_shortcut()
        tui._copy_tui_snapshot()

    @bindings.add(key_for_action("save_tui_snapshot", "c-_"))
    def _(event) -> None:
        tui._exit_command_mode_for_global_shortcut()
        tui._save_tui_snapshot()

    @bindings.add(key_for_action("clear_chat_input", "c-l"))
    def _(event) -> None:
        if tui._chat_focus_active():
            tui._chat_clear_input()
            return
        if tui._artifact_chat_focus_active():
            tui._artifact_chat_clear_input()

    @bindings.add(key_for_action("open_long_chat_message", "c-space"))
    def _(event) -> None:
        tui._exit_command_mode_for_global_shortcut()
        tui._open_latest_long_chat_message()

    @bindings.add(key_for_action("toggle_visual_view_switcher_overlay", "f8"))
    def _(event) -> None:
        tui._toggle_visual_view_switcher_overlay()

    @bindings.add(key_for_action("center_browser_toggle", "f5"))
    def _(event) -> None:
        if tui.state.mode is OperatorMode.COMMAND:
            return
        result = execute_command("center.browser.toggle", tui.state)
        tui._set_state(result.state)

    @bindings.add(key_for_action("open_center_webview", "c-0"))
    def _(event) -> None:
        tui._exit_command_mode_for_global_shortcut()
        tui._run_command(":center.webview.open")

    @bindings.add(key_for_action("open_center_window", "c-9"))
    def _(event) -> None:
        tui._exit_command_mode_for_global_shortcut()
        tui._run_command(":center.window.open")

    @bindings.add(key_for_action("switch_center_to_doc_view", "f6"))
    def _(event) -> None:
        tui._exit_command_mode_for_global_shortcut()
        tui._run_command(":doc switch")

    @bindings.add(key_for_action("next_visual_view", "f9"))
    def _(event) -> None:
        tui._next_visual_view()

    @bindings.add(key_for_action("previous_visual_view", "f10"))
    def _(event) -> None:
        tui._previous_visual_view()

    @bindings.add(key_for_action("toggle_ai_snake_config", "f6"))
    def _(event) -> None:
        tui._toggle_ai_snake_config_panel()

    @bindings.add("left")
    def _(event) -> None:
        game = tui.state.header_logo_game if isinstance(tui.state.header_logo_game, dict) else {}
        if tui.state.mode is OperatorMode.COMMAND:
            tui._command_move_cursor(-1)
            return
        if tui._artifact_chat_focus_active():
            tui._artifact_chat_move_cursor(-1)
            return
        if tui._chat_focus_active():
            tui._chat_move_cursor(-1)
            return
        if tui._audit_viewer_active():
            if tui._audit_cleanup_confirm_mode_active():
                tui._audit_cleanup_set_choice("delete")
                return
            tui._audit_viewer_scroll_horizontal(-4)
            return
        if tui._template_editor_active():
            tui._template_editor_move_cursor(-1)
            return
        if tui._ai_snake_config_combo_active(game):
            tui._ai_snake_config_combo_move_cursor(-1)
            return
        if tui._try_header_snake_direction((-1, 0)):
            return
        tui._set_state(tui.state.with_updates(selected_index=max(0, tui.state.selected_index - 1)))

    @bindings.add("right")
    def _(event) -> None:
        game = tui.state.header_logo_game if isinstance(tui.state.header_logo_game, dict) else {}
        if tui.state.mode is OperatorMode.COMMAND:
            tui._command_move_cursor(1)
            return
        if tui._artifact_chat_focus_active():
            tui._artifact_chat_move_cursor(1)
            return
        if tui._chat_focus_active():
            tui._chat_move_cursor(1)
            return
        if tui._audit_viewer_active():
            if tui._audit_cleanup_confirm_mode_active():
                tui._audit_cleanup_set_choice("cancel")
                return
            tui._audit_viewer_scroll_horizontal(4)
            return
        if tui._template_editor_active():
            tui._template_editor_move_cursor(1)
            return
        if tui._ai_snake_config_combo_active(game):
            tui._ai_snake_config_combo_move_cursor(1)
            return
        if tui._try_header_snake_direction((1, 0)):
            return
        tui._set_selected_index(tui._clamp_down())

    @bindings.add("up")
    def _(event) -> None:
        game = tui.state.header_logo_game if isinstance(tui.state.header_logo_game, dict) else {}
        if tui.state.mode is OperatorMode.COMMAND:
            tui._command_history_move(-1)
            return
        if tui._artifact_chat_focus_active():
            tui._artifact_chat_history_move(-1)
            return
        if tui._chat_focus_active():
            tui._chat_history_move(-1)
            return
        if tui._audit_viewer_active():
            tui._audit_viewer_scroll_vertical(-1)
            return
        if tui._template_editor_active():
            tui._template_editor_move_cursor_vertical(-1)
            return
        if bool(game.get("ai_snake_config_open")) and tui.state.focus is FocusPane.CONTENT:
            if tui._ai_snake_config_combo_active(game):
                tui._ai_snake_config_combo_move(-1)
            else:
                tui._set_state(tui.state.with_updates(selected_index=tui._ai_snake_config_next_index(-1, game)))
            return
        if tui._try_header_snake_direction((0, -1)):
            return
        tui._set_selected_index(max(0, tui.state.selected_index - 1))

    @bindings.add("down")
    def _(event) -> None:
        game = tui.state.header_logo_game if isinstance(tui.state.header_logo_game, dict) else {}
        if tui.state.mode is OperatorMode.COMMAND:
            tui._command_history_move(1)
            return
        if tui._artifact_chat_focus_active():
            tui._artifact_chat_history_move(1)
            return
        if tui._chat_focus_active():
            tui._chat_history_move(1)
            return
        if tui._audit_viewer_active():
            tui._audit_viewer_scroll_vertical(1)
            return
        if tui._template_editor_active():
            tui._template_editor_move_cursor_vertical(1)
            return
        if bool(game.get("ai_snake_config_open")) and tui.state.focus is FocusPane.CONTENT:
            if tui._ai_snake_config_combo_active(game):
                tui._ai_snake_config_combo_move(1)
            else:
                tui._set_state(tui.state.with_updates(selected_index=tui._ai_snake_config_next_index(1, game)))
            return
        if tui._try_header_snake_direction((0, 1)):
            return
        tui._set_selected_index(tui._clamp_down())

    @bindings.add(key_for_action("next_section", "c-n"))
    def _(event) -> None:
        tui._normal_or_text("n", lambda: tui._run_command(":next"))

    @bindings.add("<any>")
    def _(event) -> None:
        game = tui.state.header_logo_game if isinstance(tui.state.header_logo_game, dict) else {}
        if tui.state.mode is OperatorMode.COMMAND:
            data = event.key_sequence[0].data
            if data == "\x7f":
                tui._command_backspace()
                return
            if data and data.isprintable():
                tui._append_command(data)
            return
        if tui._artifact_chat_focus_active():
            data = event.key_sequence[0].data
            if data and data.isprintable():
                tui._artifact_chat_append(data)
            return
        if tui._chat_focus_active():
            data = event.key_sequence[0].data
            if data and data.isprintable():
                tui._chat_append(data)
            return
        if tui._audit_viewer_active():
            return
        if tui._template_editor_active():
            data = event.key_sequence[0].data
            if data and data.isprintable():
                tui._template_editor_insert_text(data)
            return
        if tui._ai_snake_config_combo_active(game):
            data = event.key_sequence[0].data
            if data and data.isprintable():
                tui._ai_snake_config_combo_append_filter(data)
            return
        if tui._snake_message_mode_active():
            data = event.key_sequence[0].data
            if data and data.isprintable():
                tui._snake_message_append(data)
            return

    @bindings.add(key_for_action("scroll_page_up", "pageup"))
    def _(event) -> None:
        tui._scroll_active_panel(direction="page_up")

    @bindings.add(key_for_action("scroll_page_down", "pagedown"))
    def _(event) -> None:
        tui._scroll_active_panel(direction="page_down")

    @bindings.add(key_for_action("scroll_line_up", "s-up"))
    @bindings.add("c-up")
    def _(event) -> None:
        tui._scroll_active_panel(direction="line_up")

    @bindings.add(key_for_action("scroll_line_down", "s-down"))
    @bindings.add("c-down")
    def _(event) -> None:
        tui._scroll_active_panel(direction="line_down")

    @bindings.add(key_for_action("scroll_home", "s-home"))
    def _(event) -> None:
        tui._scroll_active_panel(direction="home")

    @bindings.add(key_for_action("scroll_end", "s-end"))
    def _(event) -> None:
        tui._scroll_active_panel(direction="end")

    @bindings.add(key_for_action("scroll_left", "s-left"))
    @bindings.add("c-left")
    def _(event) -> None:
        tui._h_scroll_center(delta=-4)

    @bindings.add(key_for_action("scroll_right", "s-right"))
    @bindings.add("c-right")
    def _(event) -> None:
        tui._h_scroll_center(delta=4)

    @bindings.add(key_for_action("scroll_left_page", "s-pageup"))
    @bindings.add("c-pageup")
    def _(event) -> None:
        tui._h_scroll_center(delta=-20)

    @bindings.add(key_for_action("scroll_right_page", "s-pagedown"))
    @bindings.add("c-pagedown")
    def _(event) -> None:
        tui._h_scroll_center(delta=20)

    @bindings.add(Keys.Vt100MouseEvent)
    def _(event) -> None:
        data = event.key_sequence[0].data or ""
        parsed = tui._parse_sgr_mouse_event(data)
        if parsed is None:
            return
        tui._ingest_mouse_event(
            x=parsed[0],
            y=parsed[1],
            event_type=parsed[2],
            buttons=parsed[3],
            scroll_delta=parsed[4],
            ctrl_held=parsed[5] if len(parsed) > 5 else False,
        )

    return bindings
