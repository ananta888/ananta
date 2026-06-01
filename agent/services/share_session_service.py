from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import ShareParticipantDB, ShareSessionDB

_DEFAULT_PERMISSIONS: dict[str, bool] = {
    "chat": True,
    "view_tui": False,
    "remote_cursor": False,
    "artifact_share": False,
    "remote_control": False,
}

_FALLBACK_SESSIONS: dict[str, dict[str, Any]] = {}
_FALLBACK_PARTICIPANTS: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True)
class JoinResult:
    ok: bool
    reason: str = ""
    participant: dict[str, Any] | None = None


def _now() -> float:
    return time.time()


def _normalize_permissions(raw: Any) -> dict[str, bool]:
    base = dict(_DEFAULT_PERMISSIONS)
    if not isinstance(raw, dict):
        return base
    for key in base:
        if key in raw:
            base[key] = bool(raw[key])
    return base


def _session_to_dict(row: ShareSessionDB) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "owner_user_id": str(row.owner_user_id),
        "owner_device_id": str(row.owner_device_id),
        "title": str(row.title or "Shared Session"),
        "mode": str(row.mode or "relay"),
        "transport": str(row.transport or "hub_relay"),
        "permissions": _normalize_permissions(row.permissions),
        "invite_code": str(row.invite_code or ""),
        "expires_at": row.expires_at,
        "created_at": float(row.created_at),
        "revoked_at": row.revoked_at,
    }


def _participant_to_dict(row: ShareParticipantDB) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "session_id": str(row.session_id),
        "user_id": str(row.user_id),
        "device_id": str(row.device_id),
        "public_key_fingerprint": str(row.public_key_fingerprint or ""),
        "role": str(row.role or "participant"),
        "permissions": _normalize_permissions(row.permissions),
        "joined_at": float(row.joined_at),
        "revoked_at": row.revoked_at,
    }


class ShareSessionService:
    def create_session(
        self,
        *,
        owner_user_id: str,
        owner_device_id: str,
        title: str,
        mode: str,
        transport: str,
        permissions: dict[str, Any] | None,
        expires_at: float | None,
    ) -> dict[str, Any]:
        normalized = _normalize_permissions(permissions or {})
        payload = {
            "id": str(uuid.uuid4()),
            "owner_user_id": owner_user_id,
            "owner_device_id": owner_device_id,
            "title": title or "Shared Session",
            "mode": mode or "relay",
            "transport": transport or "hub_relay",
            "permissions": normalized,
            "invite_code": secrets.token_urlsafe(12),
            "expires_at": expires_at,
            "created_at": _now(),
            "revoked_at": None,
        }
        try:
            with Session(engine) as session:
                row = ShareSessionDB(**payload)
                session.add(row)
                session.commit()
                session.refresh(row)
                return _session_to_dict(row)
        except SQLAlchemyError:
            _FALLBACK_SESSIONS[payload["id"]] = dict(payload)
            return dict(payload)

    def list_sessions_for_owner(self, owner_user_id: str) -> list[dict[str, Any]]:
        now = _now()
        try:
            with Session(engine) as session:
                rows = session.exec(
                    select(ShareSessionDB).where(ShareSessionDB.owner_user_id == owner_user_id)
                ).all()
                out: list[dict[str, Any]] = []
                for row in rows:
                    if row.revoked_at is not None:
                        continue
                    if isinstance(row.expires_at, (int, float)) and float(row.expires_at) <= now:
                        continue
                    out.append(_session_to_dict(row))
                return out
        except SQLAlchemyError:
            out = []
            for item in _FALLBACK_SESSIONS.values():
                if str(item.get("owner_user_id") or "") != owner_user_id:
                    continue
                if item.get("revoked_at") is not None:
                    continue
                exp = item.get("expires_at")
                if isinstance(exp, (int, float)) and float(exp) <= now:
                    continue
                out.append(dict(item))
            return out

    def list_sessions_as_participant(self, user_id: str) -> list[dict[str, Any]]:
        """Sessions wo der User Teilnehmer ist (aber nicht Owner)."""
        now = _now()
        try:
            with Session(engine) as session:
                rows = session.exec(
                    select(ShareParticipantDB).where(
                        ShareParticipantDB.user_id == user_id,
                        ShareParticipantDB.revoked_at.is_(None),  # type: ignore[attr-defined]
                    )
                ).all()
                out: list[dict[str, Any]] = []
                seen: set[str] = set()
                for p in rows:
                    sid = str(p.session_id)
                    if sid in seen:
                        continue
                    sess_row = session.get(ShareSessionDB, sid)
                    if sess_row is None or sess_row.revoked_at is not None:
                        continue
                    if isinstance(sess_row.expires_at, (int, float)) and float(sess_row.expires_at) <= now:
                        continue
                    if str(sess_row.owner_user_id) == user_id:
                        continue  # Owner-Sessions kommen von list_sessions_for_owner
                    seen.add(sid)
                    out.append(_session_to_dict(sess_row))
                return out
        except SQLAlchemyError:
            out = []
            seen = set()
            for item in _FALLBACK_PARTICIPANTS.values():
                if str(item.get("user_id") or "") != user_id:
                    continue
                if item.get("revoked_at") is not None:
                    continue
                sid = str(item.get("session_id") or "")
                if sid in seen:
                    continue
                sess = _FALLBACK_SESSIONS.get(sid)
                if not sess or sess.get("revoked_at") is not None:
                    continue
                if str(sess.get("owner_user_id") or "") == user_id:
                    continue
                seen.add(sid)
                out.append(dict(sess))
            return out

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        try:
            with Session(engine) as session:
                row = session.get(ShareSessionDB, session_id)
                if row is None:
                    return None
                return _session_to_dict(row)
        except SQLAlchemyError:
            raw = _FALLBACK_SESSIONS.get(session_id)
            return dict(raw) if isinstance(raw, dict) else None

    def join_session(
        self,
        *,
        session_id: str,
        user_id: str,
        device_id: str,
        public_key_fingerprint: str,
        invite_code: str,
    ) -> JoinResult:
        session_item = self.get_session(session_id)
        if not isinstance(session_item, dict):
            return JoinResult(ok=False, reason="session_not_found")
        if session_item.get("revoked_at") is not None:
            return JoinResult(ok=False, reason="session_revoked")
        exp = session_item.get("expires_at")
        if isinstance(exp, (int, float)) and float(exp) <= _now():
            return JoinResult(ok=False, reason="session_expired")
        if str(session_item.get("invite_code") or "") != str(invite_code or ""):
            return JoinResult(ok=False, reason="invalid_invite")
        permissions = _normalize_permissions(session_item.get("permissions") or {})
        try:
            with Session(engine) as session:
                existing_rows = session.exec(
                    select(ShareParticipantDB).where(
                        ShareParticipantDB.session_id == session_id,
                        ShareParticipantDB.user_id == user_id,
                        ShareParticipantDB.device_id == device_id,
                    )
                ).all()
                for row in existing_rows:
                    if row.revoked_at is None:
                        return JoinResult(ok=True, participant=_participant_to_dict(row))
                participant = ShareParticipantDB(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    user_id=user_id,
                    device_id=device_id,
                    public_key_fingerprint=public_key_fingerprint or None,
                    role="participant",
                    permissions=permissions,
                    joined_at=_now(),
                    revoked_at=None,
                )
                session.add(participant)
                session.commit()
                session.refresh(participant)
                return JoinResult(ok=True, participant=_participant_to_dict(participant))
        except SQLAlchemyError:
            for item in _FALLBACK_PARTICIPANTS.values():
                if (
                    str(item.get("session_id") or "") == session_id
                    and str(item.get("user_id") or "") == user_id
                    and str(item.get("device_id") or "") == device_id
                    and item.get("revoked_at") is None
                ):
                    return JoinResult(ok=True, participant=dict(item))
            participant = {
                "id": str(uuid.uuid4()),
                "session_id": session_id,
                "user_id": user_id,
                "device_id": device_id,
                "public_key_fingerprint": public_key_fingerprint or "",
                "role": "participant",
                "permissions": permissions,
                "joined_at": _now(),
                "revoked_at": None,
            }
            _FALLBACK_PARTICIPANTS[participant["id"]] = dict(participant)
            return JoinResult(ok=True, participant=participant)

    def get_participants(self, session_id: str) -> list[dict[str, Any]]:
        try:
            with Session(engine) as session:
                rows = session.exec(
                    select(ShareParticipantDB).where(ShareParticipantDB.session_id == session_id)
                ).all()
                return [_participant_to_dict(r) for r in rows]
        except SQLAlchemyError:
            return [
                dict(p) for p in _FALLBACK_PARTICIPANTS.values()
                if str(p.get("session_id") or "") == session_id
            ]

    def get_session_by_invite_code(self, invite_code: str) -> dict[str, Any] | None:
        try:
            with Session(engine) as session:
                row = session.exec(
                    select(ShareSessionDB).where(ShareSessionDB.invite_code == invite_code)
                ).first()
                return _session_to_dict(row) if row else None
        except SQLAlchemyError:
            for item in _FALLBACK_SESSIONS.values():
                if str(item.get("invite_code") or "") == invite_code:
                    return dict(item)
            return None

    def revoke_session(self, *, session_id: str, actor_user_id: str) -> bool:
        try:
            with Session(engine) as session:
                row = session.get(ShareSessionDB, session_id)
                if row is None or str(row.owner_user_id or "") != actor_user_id:
                    return False
                row.revoked_at = _now()
                session.add(row)
                session.commit()
                return True
        except SQLAlchemyError:
            cached = _FALLBACK_SESSIONS.get(session_id)
            if isinstance(cached, dict) and str(cached.get("owner_user_id") or "") == actor_user_id:
                cached["revoked_at"] = _now()
                return True
            return False

    def update_session_permissions(
        self, *, session_id: str, actor_user_id: str, permissions: dict[str, Any]
    ) -> tuple[bool, str, dict[str, Any] | None]:
        session_item = self.get_session(session_id)
        if not isinstance(session_item, dict):
            return False, "session_not_found", None
        if str(session_item.get("owner_user_id") or "") != actor_user_id:
            return False, "forbidden", None
        normalized = _normalize_permissions(permissions)
        try:
            with Session(engine) as session:
                row = session.get(ShareSessionDB, session_id)
                if row is None:
                    return False, "session_not_found", None
                row.permissions = normalized
                session.add(row)
                session.commit()
                session.refresh(row)
                return True, "", _session_to_dict(row)
        except SQLAlchemyError:
            cached = _FALLBACK_SESSIONS.get(session_id)
            if not isinstance(cached, dict):
                return False, "session_not_found", None
            cached["permissions"] = normalized
            return True, "", dict(cached)

    def revoke_participant(
        self, *, session_id: str, participant_id: str, actor_user_id: str
    ) -> tuple[bool, str, dict[str, Any] | None]:
        session_item = self.get_session(session_id)
        if not isinstance(session_item, dict):
            return False, "session_not_found", None
        if str(session_item.get("owner_user_id") or "") != actor_user_id:
            return False, "forbidden", None
        try:
            with Session(engine) as session:
                participant = session.get(ShareParticipantDB, participant_id)
                if participant is None or str(participant.session_id or "") != session_id:
                    return False, "participant_not_found", None
                participant.revoked_at = _now()
                session.add(participant)
                session.commit()
                session.refresh(participant)
                return True, "", _participant_to_dict(participant)
        except SQLAlchemyError:
            participant = _FALLBACK_PARTICIPANTS.get(participant_id)
            if not isinstance(participant, dict) or str(participant.get("session_id") or "") != session_id:
                return False, "participant_not_found", None
            participant["revoked_at"] = _now()
            return True, "", dict(participant)


_SERVICE = ShareSessionService()


def get_share_session_service() -> ShareSessionService:
    return _SERVICE
