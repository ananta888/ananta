"""Input-target routing for the editing and navigation keys of the operator TUI.

Several keys (backspace, delete, the arrow keys and plain printable input) are
shared by every text-input surface of the TUI: the command line, the chat
inputs, the audit viewer, the template editor, the AI-Snake config combo box
and the snake message line. Which surface receives the key depends on the
current mode, and the priority order is the same for all of them.

Instead of one long ``if``/``return`` chain per key, each key declares an
ordered route table of ``(applies, act)`` pairs. :func:`dispatch_first_route`
walks the table and runs the first route whose predicate matches, which keeps
each key handler a flat data declaration (OCP: a new input surface is one more
row, not another branch in every handler).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Sequence

from client_surfaces.operator_tui.models import FocusPane, OperatorMode

if TYPE_CHECKING:
    from client_surfaces.operator_tui.interactive import InteractiveOperatorTui

Game = dict[str, Any]
RoutePredicate = Callable[["InteractiveOperatorTui", Game], bool]
RouteAction = Callable[["InteractiveOperatorTui", Any, Game], None]
Route = tuple[RoutePredicate, RouteAction]


def current_game(tui: InteractiveOperatorTui) -> Game:
    game = tui.state.header_logo_game
    return game if isinstance(game, dict) else {}


def dispatch_first_route(routes: Sequence[Route], tui: InteractiveOperatorTui, event: Any, game: Game) -> bool:
    """Run the first route whose predicate matches; report whether one matched."""
    for applies, act in routes:
        if applies(tui, game):
            act(tui, event, game)
            return True
    return False


def _ignore(tui: InteractiveOperatorTui, event: Any, game: Game) -> None:
    return None


def _command_mode(tui: InteractiveOperatorTui, game: Game) -> bool:
    return tui.state.mode is OperatorMode.COMMAND


def _artifact_chat(tui: InteractiveOperatorTui, game: Game) -> bool:
    return bool(tui._artifact_chat_focus_active())


def _chat(tui: InteractiveOperatorTui, game: Game) -> bool:
    return bool(tui._chat_focus_active())


def _audit_viewer(tui: InteractiveOperatorTui, game: Game) -> bool:
    return bool(tui._audit_viewer_active())


def _template_editor(tui: InteractiveOperatorTui, game: Game) -> bool:
    return bool(tui._template_editor_active())


def _config_combo(tui: InteractiveOperatorTui, game: Game) -> bool:
    return bool(tui._ai_snake_config_combo_active(game))


def _snake_message(tui: InteractiveOperatorTui, game: Game) -> bool:
    return bool(tui._snake_message_mode_active())


def _snake_mode(tui: InteractiveOperatorTui, game: Game) -> bool:
    return bool(tui._snake_mode_active())


def config_panel_selection_active(tui: InteractiveOperatorTui, game: Game) -> bool:
    return bool(game.get("ai_snake_config_open")) and tui.state.focus is FocusPane.CONTENT


def move_ai_snake_config_selection(tui: InteractiveOperatorTui, game: Game, step: int) -> None:
    """Move inside the open AI-Snake config panel: the combo list or the field cursor."""
    if tui._ai_snake_config_combo_active(game):
        tui._ai_snake_config_combo_move(step)
    else:
        tui._set_state(tui.state.with_updates(selected_index=tui._ai_snake_config_next_index(step, game)))


def _printable_data(event: Any) -> str:
    data = event.key_sequence[0].data
    return data if data and data.isprintable() else ""


def _feed_printable(append: Callable[[InteractiveOperatorTui, str], None]) -> RouteAction:
    def act(tui: InteractiveOperatorTui, event: Any, game: Game) -> None:
        data = _printable_data(event)
        if data:
            append(tui, data)

    return act


def _command_input(tui: InteractiveOperatorTui, event: Any, game: Game) -> None:
    data = event.key_sequence[0].data
    if data == "\x7f":
        tui._command_backspace()
        return
    if data and data.isprintable():
        tui._append_command(data)


BACKSPACE_ROUTES: tuple[Route, ...] = (
    (_command_mode, lambda tui, event, game: tui._command_backspace()),
    (_artifact_chat, lambda tui, event, game: tui._artifact_chat_backspace()),
    (_chat, lambda tui, event, game: tui._chat_backspace()),
    (_audit_viewer, _ignore),
    (_template_editor, lambda tui, event, game: tui._template_editor_backspace()),
    (_config_combo, lambda tui, event, game: tui._ai_snake_config_combo_backspace()),
    (_snake_message, lambda tui, event, game: tui._snake_message_backspace()),
    (_snake_mode, _ignore),
)

DELETE_ROUTES: tuple[Route, ...] = (
    (_command_mode, lambda tui, event, game: tui._command_delete()),
    (_artifact_chat, lambda tui, event, game: tui._artifact_chat_delete()),
    (_chat, lambda tui, event, game: tui._chat_delete()),
    (_audit_viewer, _ignore),
    (_template_editor, lambda tui, event, game: tui._template_editor_delete()),
    (_config_combo, lambda tui, event, game: tui._ai_snake_config_combo_delete()),
    (_snake_message, _ignore),
    (_snake_mode, _ignore),
)

PRINTABLE_INPUT_ROUTES: tuple[Route, ...] = (
    (_command_mode, _command_input),
    (_artifact_chat, _feed_printable(lambda tui, data: tui._artifact_chat_append(data))),
    (_chat, _feed_printable(lambda tui, data: tui._chat_append(data))),
    (_audit_viewer, _ignore),
    (_template_editor, _feed_printable(lambda tui, data: tui._template_editor_insert_text(data))),
    (_config_combo, _feed_printable(lambda tui, data: tui._ai_snake_config_combo_append_filter(data))),
    (_snake_message, _feed_printable(lambda tui, data: tui._snake_message_append(data))),
)


def _horizontal_audit_action(choice: str, scroll: int) -> RouteAction:
    def act(tui: InteractiveOperatorTui, event: Any, game: Game) -> None:
        if tui._audit_cleanup_confirm_mode_active():
            tui._audit_cleanup_set_choice(choice)
            return
        tui._audit_viewer_scroll_horizontal(scroll)

    return act


def horizontal_routes(step: int, audit_choice: str) -> tuple[Route, ...]:
    """Routes of the left (step -1) and right (step +1) arrow keys."""
    return (
        (_command_mode, lambda tui, event, game: tui._command_move_cursor(step)),
        (_artifact_chat, lambda tui, event, game: tui._artifact_chat_move_cursor(step)),
        (_chat, lambda tui, event, game: tui._chat_move_cursor(step)),
        (_audit_viewer, _horizontal_audit_action(audit_choice, 4 * step)),
        (_template_editor, lambda tui, event, game: tui._template_editor_move_cursor(step)),
        (_config_combo, lambda tui, event, game: tui._ai_snake_config_combo_move_cursor(step)),
        (lambda tui, game: bool(tui._try_header_snake_direction((step, 0))), _ignore),
    )


def vertical_routes(step: int) -> tuple[Route, ...]:
    """Routes of the up (step -1) and down (step +1) arrow keys."""
    return (
        (_command_mode, lambda tui, event, game: tui._command_history_move(step)),
        (_artifact_chat, lambda tui, event, game: tui._artifact_chat_history_move(step)),
        (_chat, lambda tui, event, game: tui._chat_history_move(step)),
        (_audit_viewer, lambda tui, event, game: tui._audit_viewer_scroll_vertical(step)),
        (_template_editor, lambda tui, event, game: tui._template_editor_move_cursor_vertical(step)),
        (config_panel_selection_active, lambda tui, event, game: move_ai_snake_config_selection(tui, game, step)),
        (lambda tui, game: bool(tui._try_header_snake_direction((0, step))), _ignore),
    )


LEFT_ROUTES = horizontal_routes(-1, "delete")
RIGHT_ROUTES = horizontal_routes(1, "cancel")
UP_ROUTES = vertical_routes(-1)
DOWN_ROUTES = vertical_routes(1)
