"""OIDC device-flow command handler for the Ananta operator TUI.

Extracted from client_surfaces/operator_tui/commands.py (SPLIT-002).
"""
from __future__ import annotations

from client_surfaces.operator_tui.models import CommandResult, OperatorMode, OperatorState


def _handle_oidc_command(args: list[str], state: OperatorState) -> CommandResult:
    sub = args[0].lower() if args else "status"

    if sub == "status":
        game = dict(state.header_logo_game or {})
        token = str(game.get("oidc_token") or "")
        flow = dict(game.get("oidc_device_flow") or {})
        if flow.get("status") in ("waiting", "polling"):
            code = str(flow.get("user_code") or "")
            uri = str(flow.get("verification_uri") or "")
            msg = f"OIDC Device Flow aktiv – Code: {code}  URL: {uri}"
        elif token:
            try:
                import base64 as _b64
                parts = token.split(".")
                pad = parts[1] + "=" * (-len(parts[1]) % 4)
                import json as _json
                claims = _json.loads(_b64.b64decode(pad))
                username = str(claims.get("preferred_username") or claims.get("email") or claims.get("sub") or "")
                msg = f"OIDC: eingeloggt als {username}"
            except Exception:
                msg = "OIDC: Token vorhanden"
        else:
            msg = "OIDC: nicht eingeloggt. :oidc login starten."
        return CommandResult(state.with_updates(status_message=msg, section_id="share"), msg)

    if sub == "login":
        from client_surfaces.operator_tui.network_profile import oidc_issuer, get_active_profile
        from client_surfaces.operator_tui.oidc_device_flow import DeviceFlowPoller
        issuer = oidc_issuer()
        if not issuer:
            msg = "Kein OIDC Issuer konfiguriert. ANANTA_NETWORK_PROFILE=public-ananta setzen."
            game = dict(state.header_logo_game or {})
            game["oidc_device_flow"] = {"status": "error", "user_code": "", "verification_uri": "", "error": msg}
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    mode=OperatorMode.NORMAL,
                    command_line="",
                    status_message=msg,
                    section_id="share",
                ),
                msg,
                handled=False,
            )
        profile = get_active_profile()
        client_id = str(profile.get("oidc", {}).get("client_id") or "ananta-tui")
        try:
            poller = DeviceFlowPoller()
            flow_state = poller.start(issuer, client_id)
            # Poller in interaktivem Shell-Objekt speichern via game state
            game = dict(state.header_logo_game or {})
            game["_oidc_poller_ref"] = id(poller)
            game["oidc_device_flow"] = {
                "status": flow_state.status,
                "user_code": flow_state.user_code,
                "verification_uri": flow_state.verification_uri,
                "error": "",
            }
            # Poller als Attribut auf dem Shell-Objekt setzen (via Sidecar-Dict im game)
            game["_oidc_device_flow_active"] = True
            import client_surfaces.operator_tui.oidc_device_flow as _odf
            _odf._active_poller = poller
            msg = f"OIDC Device Flow gestartet. Browser öffnen: {flow_state.verification_uri}  Code: {flow_state.user_code}"
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    mode=OperatorMode.NORMAL,
                    command_line="",
                    section_id="share",
                    status_message=msg,
                ),
                msg,
            )
        except Exception as exc:
            msg = f"OIDC Login fehlgeschlagen: {exc}"
            game = dict(state.header_logo_game or {})
            game["oidc_device_flow"] = {
                "status": "error",
                "user_code": "",
                "verification_uri": "",
                "error": str(exc),
            }
            return CommandResult(
                state.with_updates(
                    header_logo_game=game,
                    mode=OperatorMode.NORMAL,
                    command_line="",
                    status_message=msg,
                    section_id="share",
                ),
                msg,
                handled=False,
            )

    if sub == "logout":
        game = dict(state.header_logo_game or {})
        game.pop("oidc_token", None)
        game.pop("oidc_device_flow", None)
        game.pop("_oidc_device_flow_active", None)
        from client_surfaces.operator_tui.hub_loader import set_share_oidc_token
        set_share_oidc_token("")
        import client_surfaces.operator_tui.oidc_device_flow as _odf
        _odf._active_poller = None
        from client_surfaces.operator_tui.snake_persistence import clear_oidc_token
        clear_oidc_token()
        msg = "OIDC: ausgeloggt"
        return CommandResult(
            state.with_updates(header_logo_game=game, mode=OperatorMode.NORMAL, command_line="", status_message=msg),
            msg,
        )

    msg = "oidc: status | login | logout"
    return CommandResult(state.with_updates(status_message=msg), msg, handled=False)
