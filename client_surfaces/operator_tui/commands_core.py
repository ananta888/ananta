"""Built-in operator TUI commands: navigation, modes, help, actions, tutor and ask.

Every command is a small handler with the uniform signature
``handler(command, args, state) -> CommandResult`` so that
``commands.execute_command`` can dispatch them from one registry together with
the subsystem command modules.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from client_surfaces.operator_tui.actions import dispatch_action, parse_action
from client_surfaces.operator_tui.browser import browser_fallback_url
from client_surfaces.operator_tui.commands_ai import _resolve_chat_ask_timeout_seconds
from client_surfaces.operator_tui.models import CommandResult, FocusPane, OperatorMode, OperatorState
from client_surfaces.operator_tui.sections import move_section, normalize_section_id, section_ids

CommandHandler = Callable[[str, list[str], OperatorState], CommandResult]

# --- navigation and modes ------------------------------------------------------


def refresh_command(command: str, args: list[str], state: OperatorState) -> CommandResult:
    return CommandResult(
        state.with_updates(
            mode=OperatorMode.NORMAL,
            command_line="",
            refresh_count=state.refresh_count + 1,
            status_message="refresh requested",
        ),
        "refresh requested",
    )


def section_command(command: str, args: list[str], state: OperatorState) -> CommandResult:
    if not args:
        return CommandResult(state.with_updates(mode=OperatorMode.COMMAND), "section command requires a section id")
    section_id = normalize_section_id(args[0])
    return CommandResult(
        state.with_updates(
            mode=OperatorMode.NORMAL,
            command_line="",
            section_id=section_id,
            selected_index=0,
            status_message=f"section {section_id}",
        ),
        f"opened section {section_id}",
    )


def _section_stepper(step: int) -> CommandHandler:
    def handler(command: str, args: list[str], state: OperatorState) -> CommandResult:
        section_id = move_section(state.section_id, step)
        return CommandResult(
            state.with_updates(section_id=section_id, selected_index=0), f"opened section {section_id}"
        )

    return handler


next_section_command = _section_stepper(1)
previous_section_command = _section_stepper(-1)


def focus_command(command: str, args: list[str], state: OperatorState) -> CommandResult:
    if not args:
        return CommandResult(state, "focus command requires navigation, content, or detail")
    requested = args[0].lower()
    try:
        focus = FocusPane(requested)
    except ValueError:
        return CommandResult(state, f"unknown focus pane: {requested}", handled=False)
    return CommandResult(state.with_updates(focus=focus, status_message=f"focus {focus.value}"), f"focus {focus.value}")


def mode_command(command: str, args: list[str], state: OperatorState) -> CommandResult:
    if not args:
        return CommandResult(state, "mode command requires normal, command, inspect, or edit")
    requested = args[0].lower()
    try:
        mode = OperatorMode(requested)
    except ValueError:
        return CommandResult(state, f"unknown mode: {requested}", handled=False)
    return CommandResult(state.with_updates(mode=mode, status_message=f"mode {mode.value}"), f"mode {mode.value}")


HELP_TOPICS: dict[str, str] = {
    "chat": (
        "chat: [c] focus | Esc game | :chat room|ai|@id|retry | :chat backend list|use|status | :chat model list|use"
    ),
    "notes": "notes: :notes | :notes find <t> | :notes pin/unpin/delete <id> | LOCAL ONLY",
    "rag": "rag: :rag why <frage> — zeigt Retrieval-Trace ohne LLM | :rag why --json <frage> — JSON-Output",
    "te": "te: :te status — Task-Engine-Status | :te classify <kind> — Klassifizierung testen",
    "sim": "sim: :sim list — Szenarien | :sim run <name> [--ticks N] — Simulation starten",
}


def help_command(command: str, args: list[str], state: OperatorState) -> CommandResult:
    if args:
        sub = args[0].lower()
        message = HELP_TOPICS.get(sub)
        if message is not None:
            return CommandResult(state.with_updates(status_message=message), f"help {sub}")
    return CommandResult(
        state.with_updates(show_help=not state.show_help, status_message="help toggled"), "help toggled"
    )


def _open_ai_config(state: OperatorState, game: dict[str, Any]) -> CommandResult:
    game["artifact_chat_focus"] = False
    from client_surfaces.operator_tui.chat_state import get_chat_state, set_chat_state

    chat = get_chat_state(game)
    chat["chat_focus"] = False
    set_chat_state(game, chat)
    game["ai_snake_config_combo"] = {
        "open": False,
        "key": "",
        "filter": "",
        "filter_cursor": 0,
        "selected_option": 0,
    }
    return CommandResult(
        state.with_updates(
            header_logo_game=game,
            mode=OperatorMode.NORMAL,
            command_line="",
            focus=FocusPane.CONTENT,
            selected_index=0,
            status_message="ai config: offen",
        ),
        "ai config opened",
    )


def ai_config_command(command: str, args: list[str], state: OperatorState) -> CommandResult:
    game = dict(state.header_logo_game or {})
    opened = not bool(game.get("ai_snake_config_open"))
    game["ai_snake_config_open"] = opened
    if opened:
        return _open_ai_config(state, game)
    game["ai_snake_config_combo"] = {"open": False}
    return CommandResult(
        state.with_updates(
            header_logo_game=game,
            mode=OperatorMode.NORMAL,
            command_line="",
            status_message="ai config: geschlossen",
        ),
        "ai config closed",
    )


def mouse_command(command: str, args: list[str], state: OperatorState) -> CommandResult:
    mode = args[0].strip().lower() if args else "toggle"
    if mode not in {"on", "off", "toggle"}:
        return CommandResult(state, "mouse command requires on, off, or toggle", handled=False)
    game = dict(state.header_logo_game or {})
    current = bool(game.get("mouse_follow_enabled"))
    next_value = (not current) if mode == "toggle" else mode == "on"
    game["mouse_follow_enabled"] = next_value
    game["movement_mode"] = "mouse_follow" if next_value else "keyboard"
    label = "on" if next_value else "off"
    return CommandResult(
        state.with_updates(header_logo_game=game, status_message=f"mouse-follow {label}"),
        f"mouse-follow {label}",
    )


def inspect_command(command: str, args: list[str], state: OperatorState) -> CommandResult:
    return CommandResult(
        state.with_updates(mode=OperatorMode.INSPECT, status_message="inspect current selection"),
        "inspect current selection",
    )


def browser_command(command: str, args: list[str], state: OperatorState) -> CommandResult:
    target = args[0] if args else ""
    url = browser_fallback_url(state.endpoint, state.section_id, target)
    return CommandResult(
        state.with_updates(browser_fallback_url=url, status_message=f"browser fallback {url}"),
        f"browser fallback {url}",
    )


def cancel_command(command: str, args: list[str], state: OperatorState) -> CommandResult:
    return CommandResult(
        state.with_updates(mode=OperatorMode.NORMAL, pending_action=None, command_line="", status_message="cancelled"),
        "cancelled",
    )


def sections_command(command: str, args: list[str], state: OperatorState) -> CommandResult:
    return CommandResult(state.with_updates(status_message="sections: " + ",".join(section_ids())), "sections listed")


# --- operator actions ------------------------------------------------------------


def _pending_action_payload(pending_action: Any) -> dict[str, Any] | None:
    if not pending_action:
        return None
    return {
        "name": pending_action.name,
        "target": pending_action.target,
        "risk": pending_action.risk.value,
        "payload": dict(pending_action.payload),
        "requires_confirmation": pending_action.requires_confirmation,
    }


def action_command(command: str, args: list[str], state: OperatorState) -> CommandResult:
    if not args:
        return CommandResult(state, "action command requires an action name", handled=False)
    risk = args[1] if len(args) > 1 else "read_only"
    result = dispatch_action(parse_action(args[0], risk=risk))
    return CommandResult(
        state.with_updates(
            pending_action=_pending_action_payload(result.pending_action),
            audit_context=result.audit_context,
            status_message=result.message,
        ),
        result.message,
        handled=result.accepted or result.pending_action is not None,
    )


def confirm_command(command: str, args: list[str], state: OperatorState) -> CommandResult:
    pending = state.pending_action or {}
    if not pending:
        return CommandResult(state, "no pending action to confirm", handled=False)
    action = parse_action(
        str(pending.get("name") or ""), str(pending.get("target") or ""), str(pending.get("risk") or "high")
    )
    result = dispatch_action(action, confirmed=True)
    return CommandResult(
        state.with_updates(pending_action=None, audit_context=result.audit_context, status_message=result.message),
        result.message,
        handled=result.accepted,
    )


# --- snake speed, tutor and ask ----------------------------------------------------

_SPEED_LEVEL_TPS = {1: 3, 2: 6, 3: 12, 4: 24, 5: 60}


def speed_command(command: str, args: list[str], state: OperatorState) -> CommandResult:
    if not args:
        return CommandResult(state, "speed requires a level 1-5", handled=False)
    try:
        level = int(args[0])
    except ValueError:
        return CommandResult(
            state.with_updates(status_message="speed: ungültiger Wert (1-5)"), "speed: invalid", handled=False
        )
    if level < 1 or level > 5:
        return CommandResult(
            state.with_updates(status_message="speed: Wert muss 1-5 sein"), "speed: out of range", handled=False
        )
    tps = _SPEED_LEVEL_TPS[level]
    game = dict(state.header_logo_game or {})
    game["tps_override"] = tps
    game["speed_level"] = level
    return CommandResult(
        state.with_updates(header_logo_game=game, status_message=f"speed: {level}/5 ({tps} tps)"),
        f"speed {level}/5",
    )


def _tutor_mode(args: list[str], state: OperatorState) -> CommandResult:
    mode_arg = args[1].lower() if len(args) > 1 else ""
    if mode_arg not in {"overview", "deep", "expert"}:
        return CommandResult(state, "tutor mode erwartet: overview | deep | expert", handled=False)
    try:
        from client_surfaces.operator_tui.snake_persistence import set_tutor_mode

        set_tutor_mode(mode_arg)
    except Exception:
        pass
    game = dict(state.header_logo_game or {})
    game["tutor_depth_mode"] = mode_arg
    return CommandResult(
        state.with_updates(header_logo_game=game, status_message=f"tutor mode: {mode_arg}"),
        f"tutor mode {mode_arg}",
    )


def _tutor_silence(silent: bool, state: OperatorState) -> CommandResult:
    try:
        from client_surfaces.operator_tui.snake_persistence import set_tutor_silent

        set_tutor_silent(silent)
    except Exception:
        pass
    game = dict(state.header_logo_game or {})
    game["tutor_silent"] = silent
    status = "tutor: idle-Kommentare deaktiviert" if silent else "tutor: idle-Kommentare aktiv"
    return CommandResult(
        state.with_updates(header_logo_game=game, status_message=status),
        "tutor silent" if silent else "tutor active",
    )


def _tutor_replay(args: list[str], state: OperatorState) -> CommandResult:
    section_arg = args[1].lower() if len(args) > 1 else ""
    try:
        from client_surfaces.operator_tui.snake_persistence import load_tutor_config, save_tutor_config

        cfg = load_tutor_config()
        visited = list(cfg.get("visited_sections") or [])
        if section_arg in visited:
            visited.remove(section_arg)
            cfg["visited_sections"] = visited
            save_tutor_config(cfg)
    except Exception:
        pass
    return CommandResult(
        state.with_updates(status_message=f"tutor replay: {section_arg or '(alle)'} zurückgesetzt"),
        f"tutor replay {section_arg}",
    )


_TUTOR_SUBCOMMANDS: dict[str, Callable[[list[str], OperatorState], CommandResult]] = {
    "mode": _tutor_mode,
    "silent": lambda args, state: _tutor_silence(True, state),
    "active": lambda args, state: _tutor_silence(False, state),
    "replay": _tutor_replay,
}


def tutor_command(command: str, args: list[str], state: OperatorState) -> CommandResult:
    sub = args[0].lower() if args else ""
    handler = _TUTOR_SUBCOMMANDS.get(sub)
    if handler is None:
        return CommandResult(
            state, "tutor: mode <overview|deep|expert> | silent | active | replay <section>", handled=False
        )
    return handler(args, state)


def ask_command(command: str, args: list[str], state: OperatorState) -> CommandResult:
    question = " ".join(args).strip()
    if not question:
        return CommandResult(state.with_updates(status_message="ask: Bitte Frage angeben"), "ask: leer", handled=False)
    game = dict(state.header_logo_game or {})
    game["tutor_ask_question"] = question
    game["tutor_ask_at"] = time.monotonic()
    timeout_s = _resolve_chat_ask_timeout_seconds(game)
    game["tutor_ask_timeout_s"] = timeout_s
    game["tutor_ask_deadline_at"] = float(game["tutor_ask_at"]) + timeout_s
    game["tutor_ask_answered"] = False
    game["_ask_submitted"] = False
    game["paused"] = True
    game["active"] = True
    game["alive"] = True
    return CommandResult(
        state.with_updates(
            header_logo_game=game,
            mode=OperatorMode.NORMAL,
            command_line="",
            status_message=f"ask: {question[:40]}...",
        ),
        f"ask: {question[:40]}",
    )
