"""Shared persistence for opaque peer key-confirmation messages."""

from __future__ import annotations

import hashlib
import time

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import WebrtcKeyConfirmationDB
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.services.semantic_media_audit_service import SemanticMediaAuditEvent


class WebrtcPeerKeyRepositoryError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class WebrtcPeerKeyRepository:
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
        """Atomically upsert one opaque confirmation and its audit outbox row."""

        row_id = hashlib.sha256(f"{scope_id}\0{epoch}\0{sender_peer_id}\0{recipient_peer_id}".encode()).hexdigest()
        timestamp = float(time.time() if now is None else now)
        for attempt in range(2):
            try:
                with Session(engine) as session:
                    row = session.exec(
                        select(WebrtcKeyConfirmationDB)
                        .where(WebrtcKeyConfirmationDB.id == row_id)
                        .with_for_update()
                    ).first()
                    created = row is None
                    if row is None:
                        row = WebrtcKeyConfirmationDB(
                            id=row_id,
                            scope_id=scope_id,
                            epoch=epoch,
                            sender_peer_id=sender_peer_id,
                            recipient_peer_id=recipient_peer_id,
                            package_id=package_id,
                            confirmation_tag=confirmation_tag,
                            created_at=timestamp,
                            expires_at=expires_at,
                        )
                    else:
                        if row.expires_at > timestamp and (
                            row.package_id != package_id or row.confirmation_tag != confirmation_tag
                        ):
                            raise WebrtcPeerKeyRepositoryError("key_confirmation_conflict")
                        row.package_id = package_id
                        row.confirmation_tag = confirmation_tag
                        row.created_at = timestamp
                        row.expires_at = expires_at
                    session.add(row)
                    if audit_event is not None:
                        SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
                    session.commit()
                    return created
            except IntegrityError as exc:
                if attempt:
                    raise WebrtcPeerKeyRepositoryError("key_confirmation_race") from exc
        raise WebrtcPeerKeyRepositoryError("key_confirmation_race")

    def get_confirmation(
        self,
        *,
        scope_id: str,
        epoch: int,
        sender_peer_id: str,
        recipient_peer_id: str,
        now: float,
    ) -> WebrtcKeyConfirmationDB | None:
        with Session(engine) as session:
            return session.exec(
                select(WebrtcKeyConfirmationDB).where(
                    WebrtcKeyConfirmationDB.scope_id == scope_id,
                    WebrtcKeyConfirmationDB.epoch == epoch,
                    WebrtcKeyConfirmationDB.sender_peer_id == sender_peer_id,
                    WebrtcKeyConfirmationDB.recipient_peer_id == recipient_peer_id,
                    WebrtcKeyConfirmationDB.expires_at > now,
                )
            ).first()

    def delete_scope(self, scope_id: str) -> None:
        with Session(engine) as session:
            session.exec(delete(WebrtcKeyConfirmationDB).where(WebrtcKeyConfirmationDB.scope_id == scope_id))
            session.commit()


__all__ = ["WebrtcPeerKeyRepository", "WebrtcPeerKeyRepositoryError"]
