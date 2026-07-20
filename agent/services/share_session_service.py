from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from agent.config import settings
from agent.database import engine
from agent.db_models import ShareParticipantDB, ShareSessionDB
from agent.services.share_session_permissions import (
    PERMISSION_CONTRACT_VERSION,
    get_share_session_permission_service,
)
from agent.services.webrtc_epoch_service import get_webrtc_epoch_service
from agent.services.webrtc_peer_identity_service import PeerIdentityError, spki_fingerprint

_FALLBACK_SESSIONS: dict[str, dict[str, Any]] = {}
_FALLBACK_PARTICIPANTS: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True)
class JoinResult:
    ok: bool
    reason: str = ""
    participant: dict[str, Any] | None = None


def _now() -> float:
    return time.time()


def _hub_id() -> str:
    return str(settings.agent_name or "hub")[:128]


def _attach_security_epoch(item: dict[str, Any]) -> dict[str, Any]:
    item["security_epoch"] = get_webrtc_epoch_service().current_epoch("session", str(item.get("id") or ""))
    return item


def _normalize_permissions(raw: Any) -> dict[str, bool]:
    return get_share_session_permission_service().normalize(raw).values


def _session_to_dict(row: ShareSessionDB) -> dict[str, Any]:
    metadata = dict(row.session_metadata or {})
    return _attach_security_epoch(
        {
            "id": str(row.id),
            "owner_user_id": str(row.owner_user_id),
            "owner_device_id": str(row.owner_device_id),
            "title": str(row.title or "Shared Session"),
            "mode": str(row.mode or "relay"),
            "transport": str(row.transport or "hub_relay"),
            "permissions": _normalize_permissions(row.permissions),
            "permissions_version": PERMISSION_CONTRACT_VERSION,
            "invite_code": str(row.invite_code or ""),
            "expires_at": row.expires_at,
            "created_at": float(row.created_at),
            "revoked_at": row.revoked_at,
            "security_contract_version": int(metadata.get("security_contract_version") or 0),
            "security_mode": str(metadata.get("security_mode") or "legacy"),
            "tenant_id": str(metadata.get("tenant_id") or "default"),
        }
    )


def _participant_to_dict(row: ShareParticipantDB) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "session_id": str(row.session_id),
        "user_id": str(row.user_id),
        "device_id": str(row.device_id),
        "public_key_fingerprint": str(row.public_key_fingerprint or ""),
        "role": str(row.role or "participant"),
        "permissions": _normalize_permissions(row.permissions),
        "permissions_version": PERMISSION_CONTRACT_VERSION,
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
        security_contract_version: int = 0,
        security_mode: str = "legacy",
        owner_public_key_spki_b64: str = "",
        owner_public_key_fingerprint: str = "",
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        normalized = _normalize_permissions(permissions or {})
        if (
            not isinstance(security_contract_version, int)
            or isinstance(security_contract_version, bool)
            or security_contract_version not in {0, 1}
        ):
            raise PeerIdentityError("security_contract_version_invalid")
        if security_mode not in {"legacy", "strict_e2ee"}:
            raise PeerIdentityError("security_mode_invalid")
        strict_security = security_contract_version == 1 and security_mode == "strict_e2ee"
        if (security_contract_version == 1) != (security_mode == "strict_e2ee"):
            raise PeerIdentityError("security_downgrade_rejected")
        if strict_security:
            if not owner_public_key_spki_b64 or not owner_public_key_fingerprint:
                raise PeerIdentityError("device_key_required")
            if spki_fingerprint(owner_public_key_spki_b64) != owner_public_key_fingerprint:
                raise PeerIdentityError("device_key_substitution")
        payload = {
            "id": str(uuid.uuid4()),
            "owner_user_id": owner_user_id,
            "owner_device_id": owner_device_id,
            "title": title or "Shared Session",
            "mode": mode or "relay",
            "transport": transport or "hub_relay",
            "permissions": normalized,
            "permissions_version": PERMISSION_CONTRACT_VERSION,
            "invite_code": secrets.token_urlsafe(12),
            "expires_at": expires_at,
            "created_at": _now(),
            "revoked_at": None,
            "session_metadata": {
                "security_contract_version": 1 if security_contract_version == 1 else 0,
                "security_mode": "strict_e2ee" if security_mode == "strict_e2ee" else "legacy",
                "owner_public_key_spki_b64": owner_public_key_spki_b64 if strict_security else "",
                "owner_key_fingerprint": owner_public_key_fingerprint if strict_security else "",
                "owner_membership_version": 1,
                "tenant_id": tenant_id,
            },
            "security_contract_version": 1 if strict_security else 0,
            "security_mode": "strict_e2ee" if strict_security else "legacy",
        }
        try:
            with Session(engine) as session:
                row = ShareSessionDB(**payload)
                session.add(row)
                session.commit()
                session.refresh(row)
                result = get_webrtc_epoch_service().claim_epoch(
                    scope_kind="session", scope_id=payload["id"], hub_id=_hub_id()
                )
                item = _session_to_dict(row)
                item["security_epoch"] = result.epoch if result.ok else None
                return item
        except SQLAlchemyError:
            _FALLBACK_SESSIONS[payload["id"]] = dict(payload)
            payload["security_epoch"] = None
            return dict(payload)

    def list_sessions_for_owner(self, owner_user_id: str) -> list[dict[str, Any]]:
        now = _now()
        try:
            with Session(engine) as session:
                rows = session.exec(select(ShareSessionDB).where(ShareSessionDB.owner_user_id == owner_user_id)).all()
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
        public_key_spki_b64: str = "",
        tenant_id: str = "default",
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
        strict_security = (
            int(
                session_item.get("security_contract_version")
                or dict(session_item.get("session_metadata") or {}).get("security_contract_version")
                or 0
            )
            == 1
            and str(session_item.get("security_mode") or "legacy") == "strict_e2ee"
        )
        if strict_security:
            if str(session_item.get("tenant_id") or "default") != tenant_id:
                return JoinResult(ok=False, reason="cross_tenant_denied")
            if not public_key_spki_b64 or not public_key_fingerprint:
                return JoinResult(ok=False, reason="device_key_required")
            try:
                if spki_fingerprint(public_key_spki_b64) != public_key_fingerprint:
                    return JoinResult(ok=False, reason="device_key_substitution")
            except PeerIdentityError as exc:
                return JoinResult(ok=False, reason=exc.reason_code)
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
                        if strict_security and str(row.public_key_fingerprint or "") != public_key_fingerprint:
                            return JoinResult(ok=False, reason="device_key_substitution")
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
                    participant_metadata={
                        "public_key_spki_b64": public_key_spki_b64 if strict_security else "",
                        "membership_version": 1,
                    },
                )
                session.add(participant)
                session.commit()
                session.refresh(participant)
                get_webrtc_epoch_service().claim_epoch(
                    scope_kind="session", scope_id=session_id, hub_id=_hub_id(), advance=True
                )
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
                "permissions_version": PERMISSION_CONTRACT_VERSION,
                "joined_at": _now(),
                "revoked_at": None,
                "participant_metadata": {
                    "public_key_spki_b64": public_key_spki_b64 if strict_security else "",
                    "membership_version": 1,
                },
            }
            _FALLBACK_PARTICIPANTS[participant["id"]] = dict(participant)
            return JoinResult(ok=True, participant=participant)

    def get_security_memberships(self, session_id: str) -> list[dict[str, Any]]:
        """Return active and revoked identity bindings without content keys."""
        try:
            with Session(engine) as session:
                share = session.get(ShareSessionDB, session_id)
                if share is None:
                    return []
                metadata = dict(share.session_metadata or {})
                rows = session.exec(select(ShareParticipantDB).where(ShareParticipantDB.session_id == session_id)).all()
                memberships = [
                    {
                        "membership_id": f"owner-{session_id}",
                        "peer_id": str(share.owner_user_id),
                        "device_id": str(share.owner_device_id),
                        "fingerprint": str(metadata.get("owner_key_fingerprint") or ""),
                        "public_key_spki_b64": str(metadata.get("owner_public_key_spki_b64") or ""),
                        "membership_version": int(metadata.get("owner_membership_version") or 1),
                        "active": share.revoked_at is None,
                    }
                ]
                for row in rows:
                    participant_metadata = dict(row.participant_metadata or {})
                    memberships.append(
                        {
                            "membership_id": str(row.id),
                            "peer_id": str(row.user_id),
                            "device_id": str(row.device_id),
                            "fingerprint": str(row.public_key_fingerprint or ""),
                            "public_key_spki_b64": str(participant_metadata.get("public_key_spki_b64") or ""),
                            "membership_version": int(participant_metadata.get("membership_version") or 1),
                            "active": row.revoked_at is None,
                        }
                    )
                return memberships
        except SQLAlchemyError:
            share = _FALLBACK_SESSIONS.get(session_id)
            if not share:
                return []
            metadata = dict(share.get("session_metadata") or {})
            memberships = [
                {
                    "membership_id": f"owner-{session_id}",
                    "peer_id": str(share.get("owner_user_id") or ""),
                    "device_id": str(share.get("owner_device_id") or ""),
                    "fingerprint": str(metadata.get("owner_key_fingerprint") or ""),
                    "public_key_spki_b64": str(metadata.get("owner_public_key_spki_b64") or ""),
                    "membership_version": int(metadata.get("owner_membership_version") or 1),
                    "active": share.get("revoked_at") is None,
                }
            ]
            for row in _FALLBACK_PARTICIPANTS.values():
                if str(row.get("session_id") or "") != session_id:
                    continue
                participant_metadata = dict(row.get("participant_metadata") or {})
                memberships.append(
                    {
                        "membership_id": str(row.get("id") or ""),
                        "peer_id": str(row.get("user_id") or ""),
                        "device_id": str(row.get("device_id") or ""),
                        "fingerprint": str(row.get("public_key_fingerprint") or ""),
                        "public_key_spki_b64": str(participant_metadata.get("public_key_spki_b64") or ""),
                        "membership_version": int(participant_metadata.get("membership_version") or 1),
                        "active": row.get("revoked_at") is None,
                    }
                )
            return memberships

    def get_participants(self, session_id: str) -> list[dict[str, Any]]:
        try:
            with Session(engine) as session:
                rows = session.exec(select(ShareParticipantDB).where(ShareParticipantDB.session_id == session_id)).all()
                return [_participant_to_dict(r) for r in rows]
        except SQLAlchemyError:
            return [dict(p) for p in _FALLBACK_PARTICIPANTS.values() if str(p.get("session_id") or "") == session_id]

    def get_session_by_invite_code(self, invite_code: str) -> dict[str, Any] | None:
        try:
            with Session(engine) as session:
                row = session.exec(select(ShareSessionDB).where(ShareSessionDB.invite_code == invite_code)).first()
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
                get_share_session_permission_service().invalidate(session_id)
                get_webrtc_epoch_service().close_scope("session", session_id)
                return True
        except SQLAlchemyError:
            cached = _FALLBACK_SESSIONS.get(session_id)
            if isinstance(cached, dict) and str(cached.get("owner_user_id") or "") == actor_user_id:
                cached["revoked_at"] = _now()
                get_share_session_permission_service().invalidate(session_id)
                get_webrtc_epoch_service().close_scope("session", session_id)
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
                get_share_session_permission_service().invalidate(session_id)
                get_webrtc_epoch_service().claim_epoch(
                    scope_kind="session", scope_id=session_id, hub_id=_hub_id(), advance=True
                )
                return True, "", _session_to_dict(row)
        except SQLAlchemyError:
            cached = _FALLBACK_SESSIONS.get(session_id)
            if not isinstance(cached, dict):
                return False, "session_not_found", None
            cached["permissions"] = normalized
            get_share_session_permission_service().invalidate(session_id)
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
                get_share_session_permission_service().invalidate(session_id)
                get_webrtc_epoch_service().claim_epoch(
                    scope_kind="session", scope_id=session_id, hub_id=_hub_id(), advance=True
                )
                return True, "", _participant_to_dict(participant)
        except SQLAlchemyError:
            participant = _FALLBACK_PARTICIPANTS.get(participant_id)
            if not isinstance(participant, dict) or str(participant.get("session_id") or "") != session_id:
                return False, "participant_not_found", None
            participant["revoked_at"] = _now()
            get_share_session_permission_service().invalidate(session_id)
            return True, "", dict(participant)


_SERVICE = ShareSessionService()


def get_share_session_service() -> ShareSessionService:
    return _SERVICE
