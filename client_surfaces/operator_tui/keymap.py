from __future__ import annotations

from client_surfaces.operator_tui.models import KeyBinding, OperatorMode
from client_surfaces.operator_tui.keybindings_config import display_for_action


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

_NORMAL_HINTS = (
    f"[{display_for_action('cycle_focus_or_channel', 'Ctrl+W')}] Focus/Kanal  "
    f"[{display_for_action('selection_down', 'Ctrl+J')}/{display_for_action('selection_up', 'Ctrl+K')}] Move  "
    f"[{display_for_action('refresh', 'Ctrl+R')}] Refresh  "
    f"[{display_for_action('next_section', 'Ctrl+N')}] Section  "
    f"[{display_for_action('toggle_ai_snake_config', 'F6')}] AI-Config  "
    f"[{display_for_action('toggle_visual_view_switcher_overlay', 'F8')}] {display_for_action('toggle_visual_view_switcher_overlay', 'View-Leiste')}  "
    f"[{display_for_action('open_long_chat_message', 'Ctrl+Space')}] Chat-Rest  "
    f"[{display_for_action('scroll_page_up', 'PgUp')}/{display_for_action('scroll_page_down', 'PgDn')}] Scroll  "
    f"[{display_for_action('inspect', 'Ctrl+F')}] Inspect  "
    "[:config] Fallback  "
    f"[{display_for_action('help', 'Ctrl+Y')}] Help  "
    f"[{display_for_action('quit', 'Ctrl+Q')}] Quit"
)
_COMMAND_HINTS = "[Enter] Execute  [Esc] Cancel  — commands: :section <id>  :refresh  :focus <pane>  :mouse <on|off|toggle>  :help  :action <name> <risk>"
_INSPECT_HINTS = (
    f"[{display_for_action('selection_down', 'Ctrl+J')}/"
    f"{display_for_action('selection_up', 'Ctrl+K')}] Move  "
    f"[Esc] Normal  [{display_for_action('help', 'Ctrl+Y')}] Help  "
    f"[{display_for_action('quit', 'Ctrl+Q')}] Quit  — confirm actions with :confirm"
)


def bindings_for_mode(mode: OperatorMode) -> tuple[KeyBinding, ...]:
    return tuple(binding for binding in KEYBINDINGS if mode in binding.modes)


def hints_for_mode(mode: OperatorMode) -> str:
    if mode is OperatorMode.COMMAND:
        return _COMMAND_HINTS
    if mode is OperatorMode.INSPECT:
        return _INSPECT_HINTS
    return _NORMAL_HINTS
