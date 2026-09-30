"""Hub service facade for opaque WebRTC peer key confirmations.

Routes use this facade instead of importing the SQL repository, keeping the
route -> service -> repository layering. The store is injected through
``WebrtcPeerKeyConfirmationStorePort`` (DIP); the default composition binds the
shared SQL repository. Each call delegates unchanged, so transactions and the
in-transaction audit outbox staging stay owned by the repository.
"""

from __future__ import annotations

import threading

from agent.models.semantic_media_audit import SemanticMediaAuditEvent
from agent.ports.webrtc_peer_key_confirmation import (
    WebrtcKeyConfirmationRecord,
    WebrtcPeerKeyConfirmationStorePort,
)
from agent.repositories.webrtc_peer_key_repository import WebrtcPeerKeyRepository


class WebrtcPeerKeyConfirmationService:
    def __init__(self, store: WebrtcPeerKeyConfirmationStorePort) -> None:
        self._store = store

    def put_confirmation(
        self,
        *,
        scope_id: str,
        epoch: int,
        sender_peer_id: str,
        recipient_peer_id: str,
        package_id: str,
        confirmation_tag: str,
        expires_at: float,
        now: float | None = None,
        audit_event: SemanticMediaAuditEvent | None = None,
    ) -> bool:
        return self._store.put_confirmation(
            scope_id=scope_id,
            epoch=epoch,
            sender_peer_id=sender_peer_id,
            recipient_peer_id=recipient_peer_id,
            package_id=package_id,
            confirmation_tag=confirmation_tag,
            expires_at=expires_at,
            now=now,
            audit_event=audit_event,
        )

    def get_confirmation(
        self,
        *,
        scope_id: str,
        epoch: int,
        sender_peer_id: str,
        recipient_peer_id: str,
        now: float,
    ) -> WebrtcKeyConfirmationRecord | None:
        return self._store.get_confirmation(
            scope_id=scope_id,
            epoch=epoch,
            sender_peer_id=sender_peer_id,
            recipient_peer_id=recipient_peer_id,
            now=now,
        )

    def delete_scope(self, scope_id: str) -> None:
        self._store.delete_scope(scope_id)


_SERVICE: WebrtcPeerKeyConfirmationService | None = None
_LOCK = threading.Lock()


def get_webrtc_peer_key_confirmation_service() -> WebrtcPeerKeyConfirmationService:
    global _SERVICE
    if _SERVICE is None:
        with _LOCK:
            if _SERVICE is None:
                _SERVICE = WebrtcPeerKeyConfirmationService(WebrtcPeerKeyRepository())
    return _SERVICE


__all__ = ["WebrtcPeerKeyConfirmationService", "get_webrtc_peer_key_confirmation_service"]
