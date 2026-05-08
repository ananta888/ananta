from __future__ import annotations

from client_surfaces.operator_tui.actions import dispatch_action, parse_action
from client_surfaces.operator_tui.browser import browser_fallback_url
from client_surfaces.operator_tui.models import CommandResult, FocusPane, OperatorMode, OperatorState
from client_surfaces.operator_tui.sections import move_section, normalize_section_id, section_ids


def execute_command(raw_command: str, state: OperatorState) -> CommandResult:
    text = str(raw_command or "").strip()
    if text.startswith(":"):
        text = text[1:].strip()
    if not text:
        return CommandResult(state.with_updates(mode=OperatorMode.NORMAL, command_line=""), "empty command ignored")

    parts = text.split()
    command = parts[0].lower()
    args = parts[1:]

    if command in {"refresh", "r"}:
        return CommandResult(
            state.with_updates(
                mode=OperatorMode.NORMAL,
                command_line="",
                refresh_count=state.refresh_count + 1,
                status_message="refresh requested",
            ),
            "refresh requested",
        )
    if command in {"section", "open", "goto"}:
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
    if command == "next":
        section_id = move_section(state.section_id, 1)
        return CommandResult(state.with_updates(section_id=section_id, selected_index=0), f"opened section {section_id}")
    if command == "prev":
        section_id = move_section(state.section_id, -1)
        return CommandResult(state.with_updates(section_id=section_id, selected_index=0), f"opened section {section_id}")
    if command == "focus":
        if not args:
            return CommandResult(state, "focus command requires navigation, content, or detail")
        requested = args[0].lower()
        try:
            focus = FocusPane(requested)
        except ValueError:
            return CommandResult(state, f"unknown focus pane: {requested}", handled=False)
        return CommandResult(state.with_updates(focus=focus, status_message=f"focus {focus.value}"), f"focus {focus.value}")
    if command == "mode":
        if not args:
            return CommandResult(state, "mode command requires normal, command, inspect, or edit")
        requested = args[0].lower()
        try:
            mode = OperatorMode(requested)
        except ValueError:
            return CommandResult(state, f"unknown mode: {requested}", handled=False)
        return CommandResult(state.with_updates(mode=mode, status_message=f"mode {mode.value}"), f"mode {mode.value}")
    if command in {"help", "?"}:
        return CommandResult(state.with_updates(show_help=not state.show_help, status_message="help toggled"), "help toggled")
    if command == "inspect":
        return CommandResult(state.with_updates(mode=OperatorMode.INSPECT, status_message="inspect current selection"), "inspect current selection")
    if command == "browser":
        target = args[0] if args else ""
        url = browser_fallback_url(state.endpoint, state.section_id, target)
        return CommandResult(state.with_updates(browser_fallback_url=url, status_message=f"browser fallback {url}"), f"browser fallback {url}")
    if command == "action":
        if not args:
            return CommandResult(state, "action command requires an action name", handled=False)
        risk = args[1] if len(args) > 1 else "read_only"
        action = parse_action(args[0], risk=risk)
        result = dispatch_action(action)
        pending = (
            {
                "name": result.pending_action.name,
                "target": result.pending_action.target,
                "risk": result.pending_action.risk.value,
                "payload": dict(result.pending_action.payload),
                "requires_confirmation": result.pending_action.requires_confirmation,
            }
            if result.pending_action
            else None
        )
        return CommandResult(
            state.with_updates(
                pending_action=pending,
                audit_context=result.audit_context,
                status_message=result.message,
            ),
            result.message,
            handled=result.accepted or result.pending_action is not None,
        )
    if command == "confirm":
        pending = state.pending_action or {}
        if not pending:
            return CommandResult(state, "no pending action to confirm", handled=False)
        action = parse_action(str(pending.get("name") or ""), str(pending.get("target") or ""), str(pending.get("risk") or "high"))
        result = dispatch_action(action, confirmed=True)
        return CommandResult(
            state.with_updates(pending_action=None, audit_context=result.audit_context, status_message=result.message),
            result.message,
            handled=result.accepted,
        )
    if command in {"cancel", "esc"}:
        return CommandResult(
            state.with_updates(mode=OperatorMode.NORMAL, pending_action=None, command_line="", status_message="cancelled"),
            "cancelled",
        )
    if command == "sections":
        return CommandResult(state.with_updates(status_message="sections: " + ",".join(section_ids())), "sections listed")

    return CommandResult(state.with_updates(status_message=f"unknown command: {command}"), f"unknown command: {command}", handled=False)
