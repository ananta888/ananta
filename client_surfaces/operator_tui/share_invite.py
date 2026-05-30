"""PRD03.01: Invite-Code und ananta:// Invite-Link für ShareSessions.

Invite enthält: session_id, rendezvous_url, oidc_issuer, owner_device_fingerprint,
                expires_at, allowed_permissions.
Keine privaten Schlüssel, keine Bearer-Tokens.
"""
from __future__ import annotations

import base64
import json
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlparse


_DEFAULT_INVITE_TTL = 24 * 3600  # 24h


def build_invite(
    *,
    session_id: str,
    rendezvous_url: str,
    oidc_issuer: str,
    owner_device_fingerprint: str,
    allowed_permissions: dict[str, bool] | None = None,
    expires_at: float | None = None,
    short_code: str = "",
) -> dict[str, Any]:
    perms = {
        "chat": True,
        "view_tui": False,
        "remote_cursor": False,
        "artifact_share": False,
        "remote_control": False,
    }
    if isinstance(allowed_permissions, dict):
        for k in perms:
            if k in allowed_permissions:
                perms[k] = bool(allowed_permissions[k])
    # remote_control ist immer false – kein MVP-Recht
    perms["remote_control"] = False

    exp = float(expires_at) if expires_at else time.time() + _DEFAULT_INVITE_TTL
    code = str(short_code or "").strip().upper() or _generate_short_code()
    invite: dict[str, Any] = {
        "version": "1",
        "session_id": str(session_id),
        "rendezvous_url": str(rendezvous_url),
        "oidc_issuer": str(oidc_issuer),
        "owner_device_fingerprint": str(owner_device_fingerprint),
        "expires_at": exp,
        "allowed_permissions": perms,
        "short_code": code,
    }
    invite["invite_link"] = _to_ananta_link(invite)
    return invite


def _generate_short_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


def _to_ananta_link(invite: dict[str, Any]) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(invite, separators=(",", ":")).encode()).decode().rstrip("=")
    return f"ananta://invite?v=1&p={payload}"


def parse_invite(raw: str) -> dict[str, Any] | None:
    """Parst einen Invite aus JSON-String, ananta://-Link oder Short-Code-artiger Struktur."""
    raw = str(raw or "").strip()
    if not raw:
        return None
    # ananta:// Link
    if raw.startswith("ananta://"):
        try:
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(raw)
            qs = parse_qs(parsed.query)
            p = (qs.get("p") or [""])[0]
            padding = "=" * (-len(p) % 4)
            invite = json.loads(base64.urlsafe_b64decode(p + padding))
            return _validate_invite(invite)
        except Exception:
            return None
    # JSON
    if raw.startswith("{"):
        try:
            return _validate_invite(json.loads(raw))
        except Exception:
            return None
    return None


def _validate_invite(invite: dict[str, Any]) -> dict[str, Any] | None:
    required = {"version", "session_id", "rendezvous_url", "oidc_issuer", "owner_device_fingerprint", "expires_at", "allowed_permissions"}
    if not all(k in invite for k in required):
        return None
    if float(invite.get("expires_at", 0)) < time.time():
        return None  # abgelaufen
    return invite


def is_valid(invite: dict[str, Any] | None) -> bool:
    if not invite:
        return False
    exp = float(invite.get("expires_at") or 0)
    return exp > time.time()
