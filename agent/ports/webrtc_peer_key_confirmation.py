"""Port of the opaque WebRTC peer key-confirmation store.

Routes and services depend on this Protocol instead of the SQL repository
(DIP); the record Protocol names only the fields consumers read (ISP).
"""

from __future__ import annotations

from typing import Protocol

from agent.models.semantic_media_audit import SemanticMediaAuditEvent


class WebrtcKeyConfirmationRecord(Protocol):
    @property
    def scope_id(self) -> str: ...

    @property
    def epoch(self) -> int: ...

    @property
    def sender_peer_id(self) -> str: ...

    @property
    def recipient_peer_id(self) -> str: ...

    @property
    def package_id(self) -> str: ...

    @property
    def confirmation_tag(self) -> str: ...

    @property
    def expires_at(self) -> float: ...


class WebrtcPeerKeyConfirmationStorePort(Protocol):
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
    ) -> bool: ...

    def get_confirmation(
        self,
        *,
        scope_id: str,
        epoch: int,
        sender_peer_id: str,
        recipient_peer_id: str,
        now: float,
    ) -> WebrtcKeyConfirmationRecord | None: ...

    def delete_scope(self, scope_id: str) -> None: ...


__all__ = ["WebrtcKeyConfirmationRecord", "WebrtcPeerKeyConfirmationStorePort"]
