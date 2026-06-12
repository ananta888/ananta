"""Share/OIDC tick helpers extracted from SnakeTickMixin (SPLIT-119).

Module-level functions take the mixin instance as first argument ``tui``.
The mixin keeps thin delegating method wrappers so the public method
contract (incl. monkeypatching on the class/instance) stays unchanged.
"""
from __future__ import annotations

import os
from concurrent.futures import Future
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from client_surfaces.operator_tui.snake_tick_mixin import SnakeTickMixin


def tick_oidc_device_flow(tui, game: dict, *, now: float) -> None:
    # Poller kommt aus dem globalen Sidecar (gesetzt von :oidc login)
    import client_surfaces.operator_tui.oidc_device_flow as _odf
    poller = getattr(_odf, "_active_poller", None)
    if poller is None:
        return
    state = poller.tick(now)
    if state is None:
        return
    game["oidc_device_flow"] = {
        "status": state.status,
        "user_code": state.user_code,
        "verification_uri": state.verification_uri,
        "error": state.error,
    }
    if state.status == "done" and state.access_token:
        game["oidc_token"] = state.access_token
        from client_surfaces.operator_tui.hub_loader import set_share_oidc_token
        from client_surfaces.operator_tui.network_profile import rendezvous_base_url
        rdv_url = rendezvous_base_url()
        set_share_oidc_token(state.access_token, rdv_url)
        from client_surfaces.operator_tui.snake_persistence import save_oidc_token
        save_oidc_token(state.access_token, issuer=state.issuer)
        game["oidc_device_flow"] = {"status": "done", "user_code": "", "verification_uri": "", "error": ""}
        poller.clear()
        _odf._active_poller = None
    elif state.status in ("error", "expired"):
        _odf._active_poller = None

# ── Share Action Executor ─────────────────────────────────────────────────

def tick_e2e_share_autorun(tui, game: dict, *, now: float) -> None:
    enabled = str(os.environ.get("ANANTA_TUI_E2E_SHARE_AUTORUN") or "").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return
    state = dict(game.get("_e2e_share_autorun") or {})
    stage = str(state.get("stage") or "init")
    title = str(os.environ.get("ANANTA_TUI_E2E_SHARE_TITLE") or "e2e-share").strip() or "e2e-share"
    since = float(state.get("since") or now)

    if stage == "init":
        from client_surfaces.operator_tui.device_keys import get_device_key_manager
        try:
            mgr = get_device_key_manager()
            if not mgr.key_exists():
                mgr.generate_key()
                game["share_status_message"] = "e2e autorun: :share key generate"
        except Exception as exc:
            game["share_status_message"] = f"e2e autorun keygen failed: {exc}"
            state = {"stage": "done", "since": now}
            game["_e2e_share_autorun"] = state
            return
        game["share_pending_action"] = {"action": "create", "title": title}
        game["share_status_message"] = f"e2e autorun: :share create {title}"
        state = {"stage": "create_sent", "since": now}
    elif stage == "create_sent":
        active = dict(game.get("share_active_session") or {})
        if str(active.get("id") or "").strip():
            game["share_pending_action"] = {"action": "list"}
            game["share_status_message"] = "e2e autorun: :share list"
            state = {"stage": "list_sent", "since": now}
        elif (now - since) >= 8.0:
            state = {"stage": "create_retry", "since": now}
    elif stage == "create_retry":
        game["share_pending_action"] = {"action": "create", "title": title}
        game["share_status_message"] = f"e2e autorun: :share create {title}"
        state = {"stage": "create_sent", "since": now}
    elif stage == "list_sent":
        status = str(game.get("share_status_message") or "")
        if "Session(s):" in status:
            state = {"stage": "done", "since": now}
        elif (now - since) >= 5.0:
            game["share_pending_action"] = {"action": "list"}
            game["share_status_message"] = "e2e autorun: :share list"
            state = {"stage": "list_sent", "since": now}

    game["_e2e_share_autorun"] = state

def get_share_action_futures(tui) -> list[Future]:
    futures: list[Future] | None = getattr(tui, "_share_action_futures", None)
    if futures is None:
        futures = []
        tui._share_action_futures = futures
    return futures

def collect_share_action_results(tui, game: dict) -> None:
    futures = tui._get_share_action_futures()
    if not futures:
        return
    pending: list[Future] = []
    for future in futures:
        if not future.done():
            pending.append(future)
            continue
        try:
            result = future.result()
        except Exception as exc:
            game["share_status_message"] = f"Share-Aktion fehlgeschlagen: {exc}"
            continue
        if not isinstance(result, dict):
            continue
        for key in ("share_status_message", "share_active_session", "share_joined_as", "share_audit_items"):
            if key in result:
                game[key] = result[key]
    tui._share_action_futures = pending

def run_share_action(
    tui,
    action: Any,
    oidc_token: str,
    hub_raw: str,
    endpoint: str,
    *args: Any,
) -> dict[str, Any]:
    result_game: dict[str, Any] = {}
    action(result_game, oidc_token, hub_raw, endpoint, *args)
    return result_game

def tick_share_pending_action(tui, game: dict, *, now: float) -> None:
    tui._collect_share_action_results(game)
    action_info = game.get("share_pending_action")
    if not action_info or not isinstance(action_info, dict):
        return
    # Nur einmal ausführen — sofort löschen, dann im Background verarbeiten
    game.pop("share_pending_action", None)
    action = str(action_info.get("action") or "")
    _bg = tui._get_snake_bg_executor()
    oidc_token = str(game.get("oidc_token") or "").strip()
    if not oidc_token:
        oidc_token = str(
            os.environ.get("ANANTA_TUI_E2E_OIDC_TOKEN")
            or os.environ.get("ANANTA_TUI_OIDC_TOKEN")
            or ""
        ).strip()
        if oidc_token:
            game["oidc_token"] = oidc_token
            try:
                from client_surfaces.operator_tui.hub_loader import set_share_oidc_token
                from client_surfaces.operator_tui.network_profile import rendezvous_base_url
                set_share_oidc_token(oidc_token, rendezvous_base_url())
            except Exception:
                pass
    endpoint = str(tui.state.endpoint or "")
    # Hub-Passwort aus Env oder .env lesen — JWT-Auflösung erfolgt im bg-Thread
    import os as _os
    _hub_raw = (
        _os.environ.get("ANANTA_AUTH_TOKEN")
        or _os.environ.get("ANANTA_PASSWORD")
        or _os.environ.get("INITIAL_ADMIN_PASSWORD")
        or ""
    )
    if not _hub_raw:
        from client_surfaces.operator_tui.app import _load_env_file as _lef
        _dotenv = _lef()
        _hub_raw = (
            _dotenv.get("ANANTA_AUTH_TOKEN")
            or _dotenv.get("ANANTA_PASSWORD")
            or _dotenv.get("INITIAL_ADMIN_PASSWORD")
            or ""
        )
    hub_raw = _hub_raw  # wird im bg-Thread zu JWT aufgelöst

    if action == "create":
        title = str(action_info.get("title") or "Shared Session")
        tui._get_share_action_futures().append(
            _bg.submit(tui._run_share_action, tui._share_action_create, oidc_token, hub_raw, endpoint, title)
        )
    elif action == "join":
        code = str(action_info.get("invite_code") or "")
        tui._get_share_action_futures().append(
            _bg.submit(tui._run_share_action, tui._share_action_join, oidc_token, hub_raw, endpoint, code)
        )
    elif action == "set_view":
        session_id = str((game.get("share_active_session") or {}).get("id") or "")
        view_enabled = bool(action_info.get("view_tui"))
        if session_id:
            tui._get_share_action_futures().append(
                _bg.submit(
                    tui._run_share_action,
                    tui._share_action_set_view,
                    oidc_token,
                    hub_raw,
                    endpoint,
                    session_id,
                    view_enabled,
                )
            )
    elif action == "list":
        tui._get_share_action_futures().append(
            _bg.submit(tui._run_share_action, tui._share_action_list, oidc_token, hub_raw, endpoint)
        )
    elif action == "stop":
        session_id = str((game.get("share_active_session") or {}).get("id") or "")
        if session_id:
            tui._get_share_action_futures().append(
                _bg.submit(tui._run_share_action, tui._share_action_stop, oidc_token, hub_raw, endpoint, session_id)
            )

def resolve_hub_jwt(hub_raw: str, endpoint: str) -> str:
    """Löst Passwort/Token zu Hub-JWT auf. Leerer String wenn nicht möglich."""
    if not hub_raw or not endpoint:
        return ""
    from client_surfaces.operator_tui.hub_loader import resolve_token
    try:
        import os
        username = os.environ.get("ANANTA_USER") or os.environ.get("INITIAL_ADMIN_USER") or "admin"
        if not username or username == "admin":
            from client_surfaces.operator_tui.app import _load_env_file
            _env = _load_env_file()
            username = _env.get("ANANTA_USER") or _env.get("INITIAL_ADMIN_USER") or "admin"
        return resolve_token(endpoint, hub_raw)
    except Exception as exc:
        return f"__error__:{exc}"

def append_share_audit(game: dict, text: str) -> None:
    items = list(game.get("share_audit_items") or [])
    import time as _time
    items.append({"ts": _time.strftime("%Y-%m-%dT%H:%M:%S"), "text": str(text or "")})
    game["share_audit_items"] = items[-20:]

def share_action_create(tui, game: dict, oidc_token: str, hub_raw: str, endpoint: str, title: str) -> None:
    from client_surfaces.operator_tui.device_keys import get_device_key_manager
    from client_surfaces.operator_tui.network_profile import is_public_profile_active, oidc_issuer, rendezvous_base_url
    from client_surfaces.operator_tui.share_client import create_session, create_hub_session
    from client_surfaces.operator_tui.share_invite import build_invite
    mgr = get_device_key_manager()
    fp = mgr.get_fingerprint() if mgr.key_exists() else ""
    if not fp:
        game["share_status_message"] = "Kein Device-Key. :share key generate zuerst."
        return
    try:
        if is_public_profile_active() and oidc_token:
            rdv_url = rendezvous_base_url()
            result = create_session(token=oidc_token, device_fingerprint=fp, title=title, base_url=rdv_url)
        else:
            hub_jwt = tui._resolve_hub_jwt(hub_raw, endpoint)
            if hub_jwt.startswith("__error__:"):
                game["share_status_message"] = f"Hub-Login fehlgeschlagen: {hub_jwt[10:]}"
                return
            if not hub_jwt:
                game["share_status_message"] = "Kein Hub-Token (ANANTA_PASSWORD fehlt)."
                return
            result = create_hub_session(hub_token=hub_jwt, hub_url=endpoint, device_id=fp, title=title)
        if result.get("ok") or result.get("id"):
            session = dict(result.get("data") or result)
            invite_code = str(session.get("invite_code") or "")
            if is_public_profile_active() and invite_code:
                invite = build_invite(
                    session_id=str(session.get("id") or ""),
                    rendezvous_url=rendezvous_base_url(),
                    oidc_issuer=oidc_issuer(),
                    owner_device_fingerprint=fp,
                    allowed_permissions=dict(session.get("allowed_permissions") or session.get("permissions") or {}),
                    expires_at=float(session.get("expires_at") or 0) or None,
                    short_code=invite_code,
                )
                session["invite_link"] = str(invite.get("invite_link") or "")
                session["short_code"] = invite_code
            game["share_active_session"] = session
            invite_label = session.get("invite_link") or session.get("invite_code") or ""
            game["share_status_message"] = f"Session '{title}' erstellt. Invite: {invite_label}"
            tui._append_share_audit(game, f"session_created title={title}")
        else:
            game["share_status_message"] = f"Session-Erstellung fehlgeschlagen: {result.get('error', result)}"
    except Exception as exc:
        game["share_status_message"] = f"Fehler beim Erstellen: {exc}"

def share_action_list(tui, game: dict, oidc_token: str, hub_raw: str, endpoint: str) -> None:
    from client_surfaces.operator_tui.network_profile import is_public_profile_active, rendezvous_base_url
    from client_surfaces.operator_tui.share_client import list_sessions, list_hub_sessions
    try:
        if is_public_profile_active() and oidc_token:
            sessions = list_sessions(token=oidc_token, base_url=rendezvous_base_url())
        else:
            hub_jwt = tui._resolve_hub_jwt(hub_raw, endpoint)
            if hub_jwt.startswith("__error__:"):
                game["share_status_message"] = f"Hub-Login fehlgeschlagen: {hub_jwt[10:]}"
                return
            if not hub_jwt:
                game["share_status_message"] = "Kein Hub-Token (ANANTA_PASSWORD fehlt)."
                return
            sessions = list_hub_sessions(token=hub_jwt, hub_url=endpoint)
        if not sessions:
            game["share_status_message"] = "Keine aktiven Sessions."
        else:
            parts = []
            for s in sessions[:5]:
                title = str(s.get("title") or "Session")[:20]
                sid = str(s.get("id") or "")[:8]
                pcount = len(s.get("participants") or [])
                parts.append(f"'{title}'[{sid}] {pcount}P")
            suffix = f" (+{len(sessions) - 5} weitere)" if len(sessions) > 5 else ""
            game["share_status_message"] = f"{len(sessions)} Session(s): {', '.join(parts)}{suffix}"
            tui._append_share_audit(game, f"sessions_listed count={len(sessions)}")
    except Exception as exc:
        game["share_status_message"] = f"Fehler beim Laden der Sessions: {exc}"

def share_action_join(tui, game: dict, oidc_token: str, hub_raw: str, endpoint: str, invite_code: str) -> None:
    from client_surfaces.operator_tui.device_keys import get_device_key_manager
    from client_surfaces.operator_tui.network_profile import rendezvous_base_url, is_public_profile_active
    from client_surfaces.operator_tui.share_client import join_session, join_hub_session
    from client_surfaces.operator_tui.share_invite import parse_invite
    mgr = get_device_key_manager()
    fp = mgr.get_fingerprint() if mgr.key_exists() else ""
    # Invite-Link parsen falls ananta://-Format
    parsed = parse_invite(invite_code)
    if parsed:
        code = str(parsed.get("short_code") or invite_code)
        session_id = str(parsed.get("session_id") or "")
        rdv_url = str(parsed.get("rendezvous_url") or rendezvous_base_url())
    else:
        code = invite_code
        session_id = ""
        rdv_url = rendezvous_base_url()
    try:
        if is_public_profile_active() and oidc_token:
            result = join_session(
                token=oidc_token,
                invite_code=code,
                session_id=session_id,
                device_id=fp,
                device_fingerprint=fp,
                base_url=rdv_url,
            )
        else:
            hub_jwt = tui._resolve_hub_jwt(hub_raw, endpoint)
            if hub_jwt.startswith("__error__:"):
                game["share_status_message"] = f"Hub-Login fehlgeschlagen: {hub_jwt[10:]}"
                return
            if not hub_jwt:
                game["share_status_message"] = "Kein Hub-Token (ANANTA_PASSWORD fehlt)."
                return
            session_id = str((game.get("share_active_session") or {}).get("id") or "")
            result = join_hub_session(
                hub_token=hub_jwt,
                hub_url=endpoint,
                session_id=session_id,
                invite_code=code,
                device_id=fp,
                device_fingerprint=fp,
            )
        if result.get("ok") or result.get("data"):
            participant = dict(result.get("data") or {})
            game["share_joined_as"] = participant
            game["share_status_message"] = f"Session beigetreten. Fingerprint: {fp[:17]}…"
            tui._append_share_audit(game, f"participant_joined device={fp[:17]}")
        else:
            game["share_status_message"] = f"Beitritt fehlgeschlagen: {result.get('error', result)}"
    except Exception as exc:
        game["share_status_message"] = f"Fehler beim Beitreten: {exc}"

def share_action_set_view(tui, game: dict, oidc_token: str, hub_raw: str, endpoint: str, session_id: str, enabled: bool) -> None:
    try:
        from client_surfaces.operator_tui.network_profile import is_public_profile_active, rendezvous_base_url
        if is_public_profile_active() and oidc_token:
            from client_surfaces.operator_tui.share_client import update_session_permissions
            result = update_session_permissions(
                token=oidc_token,
                session_id=session_id,
                permissions={"view_tui": enabled},
                base_url=rendezvous_base_url(),
            )
            if not result.get("ok"):
                game["share_status_message"] = f"View-Share Fehler: {result.get('error', result)}"
                return
            session = dict(result.get("data") or {})
            if session:
                game["share_active_session"] = session
        else:
            hub_jwt = tui._resolve_hub_jwt(hub_raw, endpoint)
            if not hub_jwt or hub_jwt.startswith("__error__:"):
                game["share_status_message"] = f"Kein Hub-Token: {hub_jwt[10:] if hub_jwt.startswith('__error__:') else 'fehlt'}"
                return
            import json as _json
            import urllib.request
            url = f"{endpoint.rstrip('/')}/share-sessions/{session_id}/permissions"
            body = _json.dumps({"permissions": {"view_tui": enabled}}).encode()
            req = urllib.request.Request(
                url, data=body,
                headers={"Authorization": f"Bearer {hub_jwt}", "Content-Type": "application/json"},
                method="PATCH",
            )
            with urllib.request.urlopen(req, timeout=5):
                pass
        label = "aktiviert" if enabled else "deaktiviert"
        game["share_status_message"] = f"TUI-View-Share {label}"
        tui._append_share_audit(game, f"view_tui_{'on' if enabled else 'off'}")
    except Exception as exc:
        game["share_status_message"] = f"View-Share Fehler: {exc}"

def share_action_stop(tui, game: dict, oidc_token: str, hub_raw: str, endpoint: str, session_id: str) -> None:
    from client_surfaces.operator_tui.network_profile import is_public_profile_active, rendezvous_base_url
    from client_surfaces.operator_tui.share_client import revoke_session
    try:
        if is_public_profile_active() and oidc_token:
            revoke_session(token=oidc_token, session_id=session_id, base_url=rendezvous_base_url())
        else:
            hub_jwt = tui._resolve_hub_jwt(hub_raw, endpoint)
            if not hub_jwt or hub_jwt.startswith("__error__:"):
                game["share_status_message"] = "Stop fehlgeschlagen: kein Hub-Token."
                return
            import urllib.request
            url = f"{endpoint.rstrip('/')}/share-sessions/{session_id}"
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {hub_jwt}"}, method="DELETE"
            )
            with urllib.request.urlopen(req, timeout=5):
                pass
        game.pop("share_active_session", None)
        game["share_status_message"] = "Share-Session beendet"
        tui._append_share_audit(game, "session_stopped")
    except Exception as exc:
        game["share_status_message"] = f"Stop fehlgeschlagen: {exc}"
