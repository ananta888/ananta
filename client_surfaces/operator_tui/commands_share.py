"""Share command handler for the Ananta operator TUI.

Extracted from client_surfaces/operator_tui/commands.py (SPLIT-002).
"""
from __future__ import annotations

from client_surfaces.operator_tui.models import CommandResult, OperatorMode, OperatorState


def _handle_share_command(args: list[str], state: OperatorState) -> CommandResult:
    sub = args[0].lower() if args else "status"

    if sub == "status":
        return CommandResult(
            state.with_updates(mode=OperatorMode.NORMAL, command_line="", section_id="share", status_message="share status"),
            "share status",
        )

    if sub == "help":
        msg = (
            "share: status | list | create [title] | invite | join <code> | "
            "key generate | key show | key rotate | view on|off | stop | debug"
        )
        return CommandResult(state.with_updates(status_message=msg), "share help")

    if sub == "debug":
        import os as _os
        game = dict(state.header_logo_game or {})
        endpoint = str(state.endpoint or "")
        oidc_token = str(game.get("oidc_token") or "")
        active = dict(game.get("share_active_session") or {})
        status_msg = str(game.get("share_status_message") or "(keine)")
        # Hub-Token prüfen
        hub_raw = (
            _os.environ.get("ANANTA_AUTH_TOKEN")
            or _os.environ.get("ANANTA_PASSWORD")
            or _os.environ.get("INITIAL_ADMIN_PASSWORD")
            or ""
        )
        if not hub_raw:
            try:
                from client_surfaces.operator_tui.app import _load_env_file
                _env = _load_env_file()
                hub_raw = _env.get("ANANTA_AUTH_TOKEN") or _env.get("ANANTA_PASSWORD") or _env.get("INITIAL_ADMIN_PASSWORD") or ""
            except Exception:
                pass
        parts = [
            f"endpoint={endpoint or '(leer)'}",
            f"hub_raw={'ja (' + hub_raw[:4] + '…)' if hub_raw else 'FEHLT'}",
            f"oidc={'ja' if oidc_token else 'nein'}",
            f"active_session={'ja (' + str(active.get('id') or '')[:8] + ')' if active else 'nein'}",
            f"last_status={status_msg[:60]}",
        ]
        msg = " | ".join(parts)
        return CommandResult(state.with_updates(status_message=msg, section_id="share"), msg)

    if sub == "list":
        game = dict(state.header_logo_game or {})
        game["share_pending_action"] = {"action": "list"}
        game["share_status_message"] = "Sessions werden abgerufen…"
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                mode=OperatorMode.NORMAL,
                command_line="",
                section_id="share",
                status_message="Sessions werden abgerufen…",
            ),
            "share list",
        )

    if sub == "create":
        title = " ".join(args[1:]).strip() or "Shared Session"
        from client_surfaces.operator_tui.share_menu import share_section_lines
        game = dict(state.header_logo_game or {})
        game["share_pending_action"] = {"action": "create", "title": title}
        game["share_status_message"] = f"Session '{title}' wird erstellt…"
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                mode=OperatorMode.NORMAL,
                command_line="",
                section_id="share",
                status_message=f"share create: '{title}' – wird beim Hub erstellt",
            ),
            f"share create {title}",
        )

    if sub == "invite":
        game = dict(state.header_logo_game or {})
        session = dict(game.get("share_active_session") or {})
        invite_code = str(session.get("invite_code") or session.get("short_code") or "")
        invite_link = str(session.get("invite_link") or "")
        if invite_code:
            msg = f"Invite-Code: {invite_code}"
            if invite_link:
                msg = f"{msg}  Link: {invite_link}"
        else:
            msg = "Keine aktive Share-Session. :share create zuerst."
        return CommandResult(state.with_updates(status_message=msg, section_id="share"), msg)

    if sub == "join" and len(args) >= 2:
        invite_code = args[1].strip()
        game = dict(state.header_logo_game or {})
        game["share_pending_action"] = {"action": "join", "invite_code": invite_code}
        game["share_status_message"] = f"Beitritt mit Code '{invite_code[:16]}…' wird versucht…"
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                mode=OperatorMode.NORMAL,
                command_line="",
                section_id="share",
                status_message=f"share join: Code '{invite_code[:16]}…' wird versucht",
            ),
            f"share join {invite_code}",
        )

    if sub == "key":
        key_sub = args[1].lower() if len(args) >= 2 else "show"
        from client_surfaces.operator_tui.device_keys import get_device_key_manager, DeviceKeyError
        mgr = get_device_key_manager()
        if key_sub == "generate":
            if mgr.key_exists():
                msg = "Device-Key existiert bereits. :share key rotate für Rotation."
            else:
                try:
                    info = mgr.generate_key()
                    fp = str(info.get("fingerprint") or "")
                    msg = f"Device-Key erstellt. Fingerprint: {fp}"
                except DeviceKeyError as exc:
                    msg = f"Key-Generierung fehlgeschlagen: {exc}"
            return CommandResult(state.with_updates(status_message=msg, section_id="share"), msg)
        if key_sub == "show":
            try:
                info = mgr.get_public_info()
                fp = str(info.get("fingerprint") or "")
                msg = f"Fingerprint: {fp}"
            except DeviceKeyError as exc:
                msg = f"Kein Device-Key: {exc}"
            return CommandResult(state.with_updates(status_message=msg, section_id="share"), msg)
        if key_sub == "rotate":
            try:
                info = mgr.rotate_key()
                fp = str(info.get("fingerprint") or "")
                msg = f"Key rotiert. Neuer Fingerprint: {fp}"
            except DeviceKeyError as exc:
                msg = f"Key-Rotation fehlgeschlagen: {exc}"
            return CommandResult(state.with_updates(status_message=msg, section_id="share"), msg)

    if sub == "view":
        view_sub = args[1].lower() if len(args) >= 2 else ""
        if view_sub not in ("on", "off"):
            return CommandResult(state.with_updates(status_message=":share view on|off"), ":share view on|off", handled=False)
        enabled = view_sub == "on"
        game = dict(state.header_logo_game or {})
        session = dict(game.get("share_active_session") or {})
        if not session:
            return CommandResult(state.with_updates(status_message="Keine aktive Share-Session"), "no active session", handled=False)
        game["share_pending_action"] = {"action": "set_view", "view_tui": enabled}
        msg = "TUI-View-Share aktiviert" if enabled else "TUI-View-Share deaktiviert"
        game["share_status_message"] = f"{msg} (wird angewendet…)"
        return CommandResult(
            state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message=msg),
            msg,
        )

    if sub == "stop":
        game = dict(state.header_logo_game or {})
        game["share_pending_action"] = {"action": "stop"}
        game["share_status_message"] = "Share-Session wird beendet…"
        return CommandResult(
            state.with_updates(
                header_logo_game=game,
                mode=OperatorMode.NORMAL,
                command_line="",
                status_message="Share-Session wird beendet",
            ),
            "share stop",
        )

    msg = "share: status | list | create [title] | invite | join <code> | key generate|show|rotate | view on|off | stop"
    return CommandResult(state.with_updates(status_message=msg), msg, handled=False)

