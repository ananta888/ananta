"""Share-session membership adapter for bilateral semantic relay checks."""

from __future__ import annotations

import time
from typing import Any, Callable, Protocol

from agent.services.semantic_relay_authorization import RelayMember


class ShareSessionReadPort(Protocol):
    def get_session(self, session_id: str) -> dict[str, Any] | None: ...

    def get_participants(self, session_id: str) -> list[dict[str, Any]]: ...


_PERMISSION_TRANSLATION: dict[str, frozenset[str]] = {
    "chat": frozenset({"semantic_control", "semantic_speech_receive"}),
    "view_tui": frozenset({"semantic_visual_receive"}),
    "artifact_share": frozenset({"peer_evidence_sync"}),
    "remote_control": frozenset({"semantic_diagnostics"}),
}


class ShareSessionRelayMembership:
    def __init__(
        self,
        sessions: ShareSessionReadPort,
        *,
        epoch_resolver: Callable[[str], int | None],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._sessions = sessions
        self._epoch_resolver = epoch_resolver
        self._clock = clock

    def member(self, *, tenant_id: str, session_id: str, member_id: str) -> RelayMember | None:
        session = self._sessions.get_session(session_id)
        if not isinstance(session, dict) or str(session.get("tenant_id") or "default") != tenant_id:
            return None
        if session.get("revoked_at") is not None:
            return None
        expires_at = session.get("expires_at")
        if isinstance(expires_at, (int, float)) and float(expires_at) <= self._clock():
            return None
        owner_id = str(session.get("owner_user_id") or "")
        participants = self._sessions.get_participants(session_id)
        active = [row for row in participants if row.get("revoked_at") is None]
        if member_id == owner_id:
            raw_permissions = dict(session.get("permissions") or {})
        else:
            matching = [row for row in active if str(row.get("user_id") or "") == member_id]
            if not matching:
                return None
            raw_permissions = dict(matching[0].get("permissions") or {})
        permissions = frozenset(
            permission
            for source, translated in _PERMISSION_TRANSLATION.items()
            if raw_permissions.get(source) is True
            for permission in translated
        )
        audiences = {owner_id, *(str(row.get("user_id") or "") for row in active)}
        audiences.discard("")
        audiences.discard(member_id)
        epoch = self._epoch_resolver(session_id)
        if epoch is None:
            return None
        return RelayMember(
            tenant_id=tenant_id,
            session_id=session_id,
            member_id=member_id,
            epoch=epoch,
            active=True,
            permissions=permissions,
            send_audiences=frozenset(audiences),
        )


__all__ = ["ShareSessionReadPort", "ShareSessionRelayMembership"]
