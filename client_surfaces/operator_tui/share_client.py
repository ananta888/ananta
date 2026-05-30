"""HTTP-Client für den Ananta Rendezvous Service.

Alle Calls sind synchron mit kurzem Timeout — für Background-Thread-Ausführung gedacht.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from client_surfaces.operator_tui.network_profile import rendezvous_base_url


def _post(url: str, body: dict[str, Any], token: str, timeout: float = 6.0) -> dict[str, Any]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except Exception:
            return {"ok": False, "error": f"http_{exc.code}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _get(url: str, token: str, timeout: float = 5.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except Exception:
            return {"ok": False, "error": f"http_{exc.code}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _delete(url: str, token: str, timeout: float = 5.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except Exception:
            return {"ok": False, "error": f"http_{exc.code}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _patch(url: str, body: dict[str, Any], token: str, timeout: float = 5.0) -> dict[str, Any]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except Exception:
            return {"ok": False, "error": f"http_{exc.code}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _base(base_url: str | None = None) -> str:
    return (base_url or rendezvous_base_url()).rstrip("/")


# --- Session API ---

def create_session(
    *,
    token: str,
    device_fingerprint: str,
    title: str = "Shared Session",
    allowed_permissions: dict[str, bool] | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    url = f"{_base(base_url)}/rendezvous/sessions"
    body: dict[str, Any] = {
        "owner_device_fingerprint": device_fingerprint,
        "title": title,
    }
    if allowed_permissions:
        body["allowed_permissions"] = allowed_permissions
    return _post(url, body, token)


def join_session(
    *,
    token: str,
    invite_code: str,
    device_id: str = "",
    device_fingerprint: str = "",
    session_id: str = "",
    base_url: str | None = None,
) -> dict[str, Any]:
    if session_id:
        url = f"{_base(base_url)}/rendezvous/sessions/{session_id}/join"
    else:
        url = f"{_base(base_url)}/rendezvous/sessions/join"
    return _post(url, {
        "invite_code": invite_code,
        "device_id": device_id,
        "device_fingerprint": device_fingerprint,
    }, token)


def list_sessions(*, token: str, base_url: str | None = None) -> list[dict[str, Any]]:
    url = f"{_base(base_url)}/rendezvous/sessions"
    result = _get(url, token)
    return list((result.get("data") or {}).get("items") or result.get("items") or [])


def get_participants(*, token: str, session_id: str, base_url: str | None = None) -> list[dict[str, Any]]:
    url = f"{_base(base_url)}/rendezvous/sessions/{session_id}/participants"
    result = _get(url, token)
    return list((result.get("data") or {}).get("participants") or [])


def revoke_session(*, token: str, session_id: str, base_url: str | None = None) -> dict[str, Any]:
    url = f"{_base(base_url)}/rendezvous/sessions/{session_id}"
    return _delete(url, token)


def update_session_permissions(
    *,
    token: str,
    session_id: str,
    permissions: dict[str, bool],
    base_url: str | None = None,
) -> dict[str, Any]:
    url = f"{_base(base_url)}/rendezvous/sessions/{session_id}/permissions"
    return _patch(url, {"permissions": permissions}, token)


def get_turn_credentials(*, token: str, base_url: str | None = None) -> dict[str, Any] | None:
    url = f"{_base(base_url)}/rendezvous/turn-credentials"
    result = _get(url, token)
    return dict(result.get("data") or {}) if result.get("ok") else None


def list_hub_sessions(*, token: str, hub_url: str) -> list[dict[str, Any]]:
    """Listet eigene Hub-Relay-Sessions (GET /share-sessions)."""
    url = f"{hub_url.rstrip('/')}/share-sessions"
    result = _get(url, token)
    return list((result.get("data") or {}).get("items") or result.get("items") or [])


def list_joined_hub_sessions(*, token: str, hub_url: str) -> list[dict[str, Any]]:
    """Listet Sessions als Teilnehmer (GET /share-sessions/joined)."""
    url = f"{hub_url.rstrip('/')}/share-sessions/joined"
    result = _get(url, token)
    return list((result.get("data") or {}).get("items") or result.get("items") or [])


# --- Share session (Hub relay) API ---

def create_hub_session(
    *,
    hub_token: str,
    hub_url: str,
    device_id: str,
    title: str = "Shared Session",
    allowed_permissions: dict[str, bool] | None = None,
) -> dict[str, Any]:
    url = f"{hub_url.rstrip('/')}/share-sessions"
    body: dict[str, Any] = {"owner_device_id": device_id, "title": title}
    if allowed_permissions:
        body["permissions"] = allowed_permissions
    return _post(url, body, hub_token)


def join_hub_session(
    *,
    hub_token: str,
    hub_url: str,
    session_id: str,
    invite_code: str,
    device_id: str = "",
    device_fingerprint: str = "",
) -> dict[str, Any]:
    url = f"{hub_url.rstrip('/')}/share-sessions/{session_id}/join"
    return _post(url, {
        "invite_code": invite_code,
        "device_id": device_id,
        "public_key_fingerprint": device_fingerprint,
    }, hub_token)
