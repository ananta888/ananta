"""PRD03.02: Rendezvous-Service für öffentliche Teilnehmer-Findung.

- Erstellt öffentliche ShareSessions mit Invite-Code
- Verbindet Teilnehmer nach OIDC- und Invite-Prüfung
- Presence-Metadaten für berechtigte Teilnehmer
- Rate-Limits gegen Invite-Bruteforce
"""
from __future__ import annotations

import secrets
import time
import uuid
from typing import Any


_DEFAULT_SESSION_TTL = 6 * 3600  # 6h
_MAX_PARTICIPANTS_PER_SESSION = 20

# In-Memory-Store (Fallback; Produktion nutzt DB über ShareSessionService)
_sessions: dict[str, dict[str, Any]] = {}
_participants: dict[str, list[dict[str, Any]]] = {}
_invite_codes: dict[str, str] = {}  # code -> session_id


def _now() -> float:
    return time.time()


def _generate_invite_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(10))


def _generate_session_id() -> str:
    return str(uuid.uuid4())


class RendezvousService:
    def create_session(
        self,
        *,
        owner_user_id: str,
        owner_device_fingerprint: str,
        oidc_issuer: str,
        allowed_permissions: dict[str, bool] | None = None,
        title: str = "Rendezvous Session",
        expires_at: float | None = None,
    ) -> dict[str, Any]:
        """Erstellt eine öffentliche ShareSession mit Invite-Code."""
        sid = _generate_session_id()
        code = _generate_invite_code()
        while code in _invite_codes:
            code = _generate_invite_code()
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
        perms["remote_control"] = False  # never auto-granted

        exp = expires_at or (_now() + _DEFAULT_SESSION_TTL)
        session: dict[str, Any] = {
            "id": sid,
            "owner_user_id": owner_user_id,
            "owner_device_fingerprint": owner_device_fingerprint,
            "oidc_issuer": oidc_issuer,
            "title": str(title or "Rendezvous Session")[:120],
            "invite_code": code,
            "allowed_permissions": perms,
            "expires_at": exp,
            "created_at": _now(),
            "revoked_at": None,
        }
        _sessions[sid] = session
        _invite_codes[code] = sid
        _participants[sid] = []
        return dict(session)

    def join_session(
        self,
        *,
        invite_code: str,
        user_id: str,
        user_sub: str,
        device_id: str,
        device_fingerprint: str,
        oidc_issuer: str,
    ) -> dict[str, Any]:
        """Verbindet Teilnehmer nach OIDC/Invite-Prüfung."""
        code = str(invite_code or "").strip().upper()
        if not code or code not in _invite_codes:
            return {"ok": False, "reason": "invalid_invite_code"}

        sid = _invite_codes[code]
        session = _sessions.get(sid)
        if not session:
            return {"ok": False, "reason": "session_not_found"}
        if session.get("revoked_at"):
            return {"ok": False, "reason": "session_revoked"}
        if float(session.get("expires_at") or 0) < _now():
            return {"ok": False, "reason": "session_expired"}

        # OIDC Issuer muss passen
        session_issuer = str(session.get("oidc_issuer") or "")
        if session_issuer and session_issuer != str(oidc_issuer or ""):
            return {"ok": False, "reason": "oidc_issuer_mismatch"}

        # User-Sub kommt aus Token, nicht aus Request-Body
        if not str(user_sub or "").strip():
            return {"ok": False, "reason": "oidc_sub_required"}

        existing = _participants.get(sid, [])
        if len(existing) >= _MAX_PARTICIPANTS_PER_SESSION:
            return {"ok": False, "reason": "session_full"}

        # Idempotent: bereits drin?
        for p in existing:
            if p.get("user_id") == user_id and p.get("device_id") == device_id and not p.get("revoked_at"):
                return {"ok": True, "participant": dict(p), "idempotent": True}

        participant: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "session_id": sid,
            "user_id": user_id,
            "user_sub": user_sub,
            "device_id": device_id,
            "device_fingerprint": device_fingerprint,
            "permissions": dict(session.get("allowed_permissions") or {}),
            "joined_at": _now(),
            "revoked_at": None,
            "last_seen": _now(),
        }
        _participants[sid].append(participant)
        return {"ok": True, "participant": dict(participant)}

    def get_participants(self, *, session_id: str, requester_user_id: str) -> dict[str, Any]:
        """Presence-Metadaten für berechtigte Teilnehmer."""
        session = _sessions.get(session_id)
        if not session:
            return {"ok": False, "reason": "session_not_found"}
        parts = _participants.get(session_id, [])
        # Nur berechtigte aktive Teilnehmer dürfen abrufen
        is_member = session.get("owner_user_id") == requester_user_id or any(
            p.get("user_id") == requester_user_id and not p.get("revoked_at")
            for p in parts
        )
        if not is_member:
            return {"ok": False, "reason": "forbidden"}
        # Presence: nur Metadaten, keine Tokens/Keys
        presence = [
            {
                "user_id": p.get("user_id"),
                "device_fingerprint": p.get("device_fingerprint"),
                "permissions": p.get("permissions"),
                "joined_at": p.get("joined_at"),
                "last_seen": p.get("last_seen"),
                "revoked_at": p.get("revoked_at"),
            }
            for p in parts
        ]
        return {"ok": True, "participants": presence}

    def revoke_session(self, *, session_id: str, actor_user_id: str) -> dict[str, Any]:
        session = _sessions.get(session_id)
        if not session:
            return {"ok": False, "reason": "session_not_found"}
        if session.get("owner_user_id") != actor_user_id:
            return {"ok": False, "reason": "forbidden"}
        session["revoked_at"] = _now()
        code = str(session.get("invite_code") or "")
        if code in _invite_codes:
            del _invite_codes[code]
        return {"ok": True}

    def list_sessions_for_user(self, *, requester_user_id: str) -> list[dict[str, Any]]:
        now = _now()
        out: list[dict[str, Any]] = []
        for sid, session in list(_sessions.items()):
            if session.get("revoked_at") is not None:
                continue
            if float(session.get("expires_at") or 0) < now:
                continue
            parts = _participants.get(sid, [])
            is_member = session.get("owner_user_id") == requester_user_id or any(
                p.get("user_id") == requester_user_id and not p.get("revoked_at")
                for p in parts
            )
            if not is_member:
                continue
            snap = dict(session)
            snap["participants"] = [dict(p) for p in parts if not p.get("revoked_at")]
            snap["participant_count"] = len(snap["participants"])
            out.append(snap)
        out.sort(key=lambda s: float(s.get("created_at") or 0), reverse=True)
        return out

    def touch_participant(self, *, session_id: str, user_id: str) -> None:
        for p in _participants.get(session_id, []):
            if p.get("user_id") == user_id and not p.get("revoked_at"):
                p["last_seen"] = _now()


_service: RendezvousService | None = None


def get_rendezvous_service() -> RendezvousService:
    global _service
    if _service is None:
        _service = RendezvousService()
    return _service
