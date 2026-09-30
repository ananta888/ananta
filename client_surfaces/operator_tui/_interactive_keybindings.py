"""Key binding table of the interactive operator TUI.

Every binding is one row of :data:`KEY_BINDING_TABLE`: the keys it listens on
and a small module-level handler ``handler(tui, event)`` with one
responsibility. :func:`build_keybindings` only registers the rows, in table
order, on a prompt_toolkit :class:`KeyBindings` instance. The editing and
navigation keys that are shared by all text inputs delegate to the route
tables in :mod:`client_surfaces.operator_tui._interactive_key_routes`.

The table order is significant: prompt_toolkit lets the most recently added
binding win when several bindings match the same key, so rows (and the keys
inside a row) are registered exactly in the order of the former decorator
stack.
"""

from __future__ import annotations

import asyncio  # noqa: F401 - kept importable for existing callers
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, Callable, Union

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys

from client_surfaces.operator_tui._interactive_key_routes import (
    BACKSPACE_ROUTES,
    DELETE_ROUTES,
    DOWN_ROUTES,
    LEFT_ROUTES,
    PRINTABLE_INPUT_ROUTES,
    RIGHT_ROUTES,
    UP_ROUTES,
    config_panel_selection_active,
    current_game,
    dispatch_first_route,
    move_ai_snake_config_selection,
)
from client_surfaces.operator_tui.chat_long_message import (  # noqa: F401 - kept importable
    configure_middle_view_for_history_entry,
    is_showing_chat_long_message,
    long_message_history_rows,
    refresh_rendered_view,
)
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.keybindings_config import key_for_action
from client_surfaces.operator_tui.models import FocusPane, OperatorMode
from client_surfaces.operator_tui.sections import SECTIONS, get_section  # noqa: F401 - kept importable

if TYPE_CHECKING:
    from client_surfaces.operator_tui.interactive import InteractiveOperatorTui

KeyHandler = Callable[["InteractiveOperatorTui", Any], None]


@dataclass(frozen=True)
class ConfiguredKey:
    """A key that the keybinding config file may override for ``action``."""

    action: str
    default_key: str

    def resolve(self) -> str:
        return key_for_action(self.action, self.default_key)


KeySpec = Union[str, Keys, ConfiguredKey]


@dataclass(frozen=True)
class KeyBindingRow:
    """One binding: its keys in declaration (top-to-bottom decorator) order and its handler."""

    keys: tuple[KeySpec, ...]
    handler: KeyHandler


def _resolve_key(spec: KeySpec) -> str | Keys:
    return spec.resolve() if isinstance(spec, ConfiguredKey) else spec


# --- mode and command-line keys -------------------------------------------------


def _on_quit(tui: InteractiveOperatorTui, event: Any) -> None:
    tui._handle_quit_key(event)


def _on_colon(tui: InteractiveOperatorTui, event: Any) -> None:
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


def _on_slash(tui: InteractiveOperatorTui, event: Any) -> None:
    if tui._snake_message_mode_active():
        tui._snake_message_append("/")
        return
    if tui.state.mode is OperatorMode.COMMAND:
        tui._append_command("/")
        return
    tui._enter_command_mode_from_anywhere()


def _on_enter(tui: InteractiveOperatorTui, event: Any) -> None:
    tui._handle_enter_key()


def _on_escape(tui: InteractiveOperatorTui, event: Any) -> None:
    tui._escape_to_start_state()


def _on_backspace(tui: InteractiveOperatorTui, event: Any) -> None:
    dispatch_first_route(BACKSPACE_ROUTES, tui, event, current_game(tui))


def _on_delete(tui: InteractiveOperatorTui, event: Any) -> None:
    dispatch_first_route(DELETE_ROUTES, tui, event, current_game(tui))


# --- selection and inspection ---------------------------------------------------


def _on_selection_down(tui: InteractiveOperatorTui, event: Any) -> None:
    game = current_game(tui)
    if config_panel_selection_active(tui, game):
        move_ai_snake_config_selection(tui, game, 1)
        return
    tui._normal_or_text("j", lambda: tui._set_selected_index(tui._clamp_down()))


def _on_selection_up(tui: InteractiveOperatorTui, event: Any) -> None:
    game = current_game(tui)
    if config_panel_selection_active(tui, game):
        move_ai_snake_config_selection(tui, game, -1)
        return
    tui._normal_or_text("k", lambda: tui._set_selected_index(max(0, tui.state.selected_index - 1)))


def _open_selected_in_content_viewer(tui: InteractiveOperatorTui) -> bool:
    if tui.state.focus is not FocusPane.CONTENT:
        return False
    if tui.state.section_id == "templates" and tui._open_template_editor_for_selected():
        return True
    return tui.state.section_id == "audit" and bool(tui._open_audit_viewer_for_selected())


def _inspect_selected(tui: InteractiveOperatorTui, event: Any) -> None:
    if _open_selected_in_content_viewer(tui):
        return
    if tui._open_selected_item_inline():
        return
    section = get_section(tui.state.section_id)
    payload = (tui.state.section_payloads or {}).get(section.id, {})
    plugin = tui._plugins.launcher_for(payload, tui.state.selected_index)
    if plugin is None:
        return

    async def _run():
        await event.app.run_in_terminal(lambda: plugin.launch(payload, tui.state.selected_index))

    event.app.create_background_task(_run())


def _on_inspect(tui: InteractiveOperatorTui, event: Any) -> None:
    tui._normal_or_text("e", lambda: _inspect_selected(tui, event))


def _on_focus_left(tui: InteractiveOperatorTui, event: Any) -> None:
    tui._normal_or_text("h", lambda: tui._move_focus(-1))


def _on_focus_right(tui: InteractiveOperatorTui, event: Any) -> None:
    tui._normal_or_text("l", lambda: tui._move_focus(1))


def _on_refresh(tui: InteractiveOperatorTui, event: Any) -> None:
    game = dict(tui.state.header_logo_game or {})
    if is_showing_chat_long_message(game):
        refresh_rendered_view(game)
        tui._set_state(
            tui.state.with_updates(header_logo_game=game, status_message="Chat-Ansicht: Render aktualisiert")
        )
        return
    tui._normal_or_text("r", lambda: tui._run_command(":refresh"))


def _on_help(tui: InteractiveOperatorTui, event: Any) -> None:
    tui._normal_or_text("?", lambda: tui._run_command(":help"))


def _on_next_section(tui: InteractiveOperatorTui, event: Any) -> None:
    tui._normal_or_text("n", lambda: tui._run_command(":next"))


# --- focus, tabs, snake and chat toggles ---------------------------------------


def _on_cycle_focus_or_channel(tui: InteractiveOperatorTui, event: Any) -> None:
    if tui._chat_focus_active() or tui._artifact_chat_focus_active() or tui._snake_mode_active():
        tui._chat_cycle_channel()
        return
    if tui.state.open_tabs and tui.state.mode is OperatorMode.NORMAL:
        tui._tab_close_active()
        return
    tui._exit_command_mode_for_global_shortcut()
    tui._move_focus(1)


def _tab_cycler(step: int) -> KeyHandler:
    def handler(tui: InteractiveOperatorTui, event: Any) -> None:
        if tui.state.mode is OperatorMode.COMMAND:
            return
        tui._tab_cycle(step)

    return handler


def _on_snake_pause(tui: InteractiveOperatorTui, event: Any) -> None:
    if tui.state.mode is OperatorMode.COMMAND:
        return
    if not tui._snake_mode_active():
        return
    tui._toggle_snake_pause()


def _on_toggle_snake_mode(tui: InteractiveOperatorTui, event: Any) -> None:
    if tui._template_editor_active():
        tui._template_editor_save()
        return
    tui._exit_command_mode_for_global_shortcut()
    tui._toggle_snake_mode()


def _global_shortcut(action: Callable[[InteractiveOperatorTui], None]) -> KeyHandler:
    """A shortcut that first leaves command mode and then runs ``action``."""

    def handler(tui: InteractiveOperatorTui, event: Any) -> None:
        tui._exit_command_mode_for_global_shortcut()
        action(tui)

    return handler


def _global_command(command: str) -> KeyHandler:
    return _global_shortcut(lambda tui: tui._run_command(command))


def _on_copy_chat_panel(tui: InteractiveOperatorTui, event: Any) -> None:
    if tui._snake_mode_active():
        tui._snake_copy_selection()
        return
    tui._copy_chat_panel_snapshot()


def _on_clear_chat_input(tui: InteractiveOperatorTui, event: Any) -> None:
    if tui._chat_focus_active():
        tui._chat_clear_input()
        return
    if tui._artifact_chat_focus_active():
        tui._artifact_chat_clear_input()


def _on_center_browser_toggle(tui: InteractiveOperatorTui, event: Any) -> None:
    if tui.state.mode is OperatorMode.COMMAND:
        return
    result = execute_command("center.browser.toggle", tui.state)
    tui._set_state(result.state)


def _plain(action: Callable[[InteractiveOperatorTui], None]) -> KeyHandler:
    def handler(tui: InteractiveOperatorTui, event: Any) -> None:
        action(tui)

    return handler


# --- arrow keys and printable input --------------------------------------------


def _on_left(tui: InteractiveOperatorTui, event: Any) -> None:
    if dispatch_first_route(LEFT_ROUTES, tui, event, current_game(tui)):
        return
    tui._set_state(tui.state.with_updates(selected_index=max(0, tui.state.selected_index - 1)))


def _on_right(tui: InteractiveOperatorTui, event: Any) -> None:
    if dispatch_first_route(RIGHT_ROUTES, tui, event, current_game(tui)):
        return
    tui._set_selected_index(tui._clamp_down())


def _on_up(tui: InteractiveOperatorTui, event: Any) -> None:
    if dispatch_first_route(UP_ROUTES, tui, event, current_game(tui)):
        return
    tui._set_selected_index(max(0, tui.state.selected_index - 1))


def _on_down(tui: InteractiveOperatorTui, event: Any) -> None:
    if dispatch_first_route(DOWN_ROUTES, tui, event, current_game(tui)):
        return
    tui._set_selected_index(tui._clamp_down())


def _on_any_key(tui: InteractiveOperatorTui, event: Any) -> None:
    dispatch_first_route(PRINTABLE_INPUT_ROUTES, tui, event, current_game(tui))


# --- scrolling and mouse ---------------------------------------------------------


def _panel_scroller(direction: str) -> KeyHandler:
    return _plain(lambda tui: tui._scroll_active_panel(direction=direction))


def _center_h_scroller(delta: int) -> KeyHandler:
    return _plain(lambda tui: tui._h_scroll_center(delta=delta))


def _on_mouse_event(tui: InteractiveOperatorTui, event: Any) -> None:
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


def _row(*keys: KeySpec, handler: KeyHandler) -> KeyBindingRow:
    return KeyBindingRow(keys=keys, handler=handler)


_K = ConfiguredKey

KEY_BINDING_TABLE: tuple[KeyBindingRow, ...] = (
    _row(_K("quit", "c-q"), handler=_on_quit),
    _row(":", handler=_on_colon),
    _row("/", handler=_on_slash),
    _row("enter", "c-m", "c-j", handler=_on_enter),
    _row("escape", handler=_on_escape),
    _row("backspace", "c-h", handler=_on_backspace),
    _row("delete", handler=_on_delete),
    _row(_K("selection_down", "c-j"), handler=_on_selection_down),
    _row(_K("selection_up", "c-k"), handler=_on_selection_up),
    _row(_K("inspect", "c-f"), handler=_on_inspect),
    _row(_K("focus_left", "c-a"), handler=_on_focus_left),
    _row(_K("focus_right", "c-d"), handler=_on_focus_right),
    _row(_K("refresh", "c-r"), handler=_on_refresh),
    _row(_K("help", "c-y"), handler=_on_help),
    _row(_K("cycle_focus_or_channel", "c-w"), handler=_on_cycle_focus_or_channel),
    _row(_K("tab_next", "c-right"), handler=_tab_cycler(1)),
    _row(_K("tab_prev", "c-left"), handler=_tab_cycler(-1)),
    _row(_K("snake_pause", "c-p"), handler=_on_snake_pause),
    _row(_K("toggle_snake_mode", "c-s"), handler=_on_toggle_snake_mode),
    _row(_K("chat_focus", "c-e"), handler=_global_shortcut(lambda tui: tui._toggle_chat_focus())),
    _row(_K("toggle_chat_panel", "c-g"), handler=_global_shortcut(lambda tui: tui._toggle_chat_panel_open())),
    _row(_K("copy_chat_panel", "c-c"), handler=_on_copy_chat_panel),
    _row(_K("copy_tui_snapshot", "c-\\"), handler=_global_shortcut(lambda tui: tui._copy_tui_snapshot())),
    _row(_K("save_tui_snapshot", "c-_"), handler=_global_shortcut(lambda tui: tui._save_tui_snapshot())),
    _row(_K("clear_chat_input", "c-l"), handler=_on_clear_chat_input),
    _row(
        _K("open_long_chat_message", "c-space"),
        handler=_global_shortcut(lambda tui: tui._open_latest_long_chat_message()),
    ),
    _row(
        _K("toggle_visual_view_switcher_overlay", "f8"),
        handler=_plain(lambda tui: tui._toggle_visual_view_switcher_overlay()),
    ),
    _row(_K("center_browser_toggle", "f5"), handler=_on_center_browser_toggle),
    _row(_K("open_center_webview", "c-0"), handler=_global_command(":center.webview.open")),
    _row(_K("open_center_window", "c-9"), handler=_global_command(":center.window.open")),
    _row(_K("switch_center_to_doc_view", "f6"), handler=_global_command(":doc switch")),
    _row(_K("next_visual_view", "f9"), handler=_plain(lambda tui: tui._next_visual_view())),
    _row(_K("previous_visual_view", "f10"), handler=_plain(lambda tui: tui._previous_visual_view())),
    _row(_K("toggle_ai_snake_config", "f6"), handler=_plain(lambda tui: tui._toggle_ai_snake_config_panel())),
    _row("left", handler=_on_left),
    _row("right", handler=_on_right),
    _row("up", handler=_on_up),
    _row("down", handler=_on_down),
    _row(_K("next_section", "c-n"), handler=_on_next_section),
    _row("<any>", handler=_on_any_key),
    _row(_K("scroll_page_up", "pageup"), handler=_panel_scroller("page_up")),
    _row(_K("scroll_page_down", "pagedown"), handler=_panel_scroller("page_down")),
    _row(_K("scroll_line_up", "s-up"), "c-up", handler=_panel_scroller("line_up")),
    _row(_K("scroll_line_down", "s-down"), "c-down", handler=_panel_scroller("line_down")),
    _row(_K("scroll_home", "s-home"), handler=_panel_scroller("home")),
    _row(_K("scroll_end", "s-end"), handler=_panel_scroller("end")),
    _row(_K("scroll_left", "s-left"), "c-left", handler=_center_h_scroller(-4)),
    _row(_K("scroll_right", "s-right"), "c-right", handler=_center_h_scroller(4)),
    _row(_K("scroll_left_page", "s-pageup"), "c-pageup", handler=_center_h_scroller(-20)),
    _row(_K("scroll_right_page", "s-pagedown"), "c-pagedown", handler=_center_h_scroller(20)),
    _row(Keys.Vt100MouseEvent, handler=_on_mouse_event),
)


def build_keybindings(tui: InteractiveOperatorTui) -> KeyBindings:
    bindings = KeyBindings()
    for row in KEY_BINDING_TABLE:
        handler = partial(row.handler, tui)
        # Stacked decorators registered the bottom key first; keep that order.
        for spec in reversed(row.keys):
            bindings.add(_resolve_key(spec))(handler)
    return bindings
