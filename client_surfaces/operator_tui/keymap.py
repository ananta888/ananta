from __future__ import annotations

from client_surfaces.operator_tui.models import KeyBinding, OperatorMode


KEYBINDINGS: tuple[KeyBinding, ...] = (
    KeyBinding("j", "selection_down", "Move selection down", (OperatorMode.NORMAL, OperatorMode.INSPECT)),
    KeyBinding("k", "selection_up", "Move selection up", (OperatorMode.NORMAL, OperatorMode.INSPECT)),
    KeyBinding("h", "focus_left", "Move focus left", (OperatorMode.NORMAL,)),
    KeyBinding("l", "focus_right", "Move focus right", (OperatorMode.NORMAL,)),
    KeyBinding("gg", "selection_first", "Move to first item", (OperatorMode.NORMAL, OperatorMode.INSPECT)),
    KeyBinding("G", "selection_last", "Move to last item", (OperatorMode.NORMAL, OperatorMode.INSPECT)),
    KeyBinding("/", "search", "Open search", (OperatorMode.NORMAL,)),
    KeyBinding(":", "command_mode", "Open command line", (OperatorMode.NORMAL,)),
    KeyBinding("r", "refresh", "Refresh active section", (OperatorMode.NORMAL,)),
    KeyBinding("?", "help", "Toggle help overlay", (OperatorMode.NORMAL, OperatorMode.INSPECT)),
    KeyBinding("enter", "inspect", "Inspect selected item", (OperatorMode.NORMAL,)),
    KeyBinding("esc", "normal_mode", "Close overlay or return to normal mode", (OperatorMode.COMMAND, OperatorMode.INSPECT, OperatorMode.EDIT)),
    KeyBinding("q", "quit", "Quit operator TUI", (OperatorMode.NORMAL,)),
)

_NORMAL_HINTS = "[Tab/←→] Focus  [j/k/↑↓] Move  [r] Refresh  [n/p] Section  [Enter] Inspect  [:] Command  [?] Help  [q] Quit"
_COMMAND_HINTS = "[Enter] Execute  [Esc] Cancel  — commands: :section <id>  :refresh  :focus <pane>  :help  :action <name> <risk>"
_INSPECT_HINTS = "[j/k/↑↓] Move  [Esc] Normal  [?] Help  [q] Quit  — confirm actions with :confirm"


def bindings_for_mode(mode: OperatorMode) -> tuple[KeyBinding, ...]:
    return tuple(binding for binding in KEYBINDINGS if mode in binding.modes)


def hints_for_mode(mode: OperatorMode) -> str:
    if mode is OperatorMode.COMMAND:
        return _COMMAND_HINTS
    if mode is OperatorMode.INSPECT:
        return _INSPECT_HINTS
    return _NORMAL_HINTS
