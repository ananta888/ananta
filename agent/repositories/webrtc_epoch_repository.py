"""Persistent repository for authoritative WebRTC epochs and replay windows."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine as default_engine
from agent.db_models import WebrtcEpochDB, WebrtcReplayStateDB
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.services.semantic_media_audit_service import SemanticMediaAuditEvent


@dataclass(frozen=True)
class EpochClaimResult:
    ok: bool
    reason: str
    epoch: int | None = None
    generation: int | None = None
    ownership_changed: bool = False


class WebrtcEpochRepository:
    """Uses database row locks; no process-local authority is assumed."""

    def __init__(self, *, db_engine=default_engine) -> None:
        self._engine = db_engine

    def claim(
        self,
        *,
        scope_kind: str,
        scope_id: str,
        hub_id: str,
        now: float,
        lease_seconds: int,
        advance: bool,
        audit_event_factory: Callable[[int], SemanticMediaAuditEvent] | None = None,
        takeover_audit_event_factory: Callable[[int], SemanticMediaAuditEvent] | None = None,
    ) -> EpochClaimResult:
        scope_key = f"{scope_kind}:{scope_id}"
        for attempt in range(2):
            try:
                with Session(self._engine) as session:
                    row = session.exec(
                        select(WebrtcEpochDB).where(WebrtcEpochDB.scope_key == scope_key).with_for_update()
                    ).first()
                    if row is None:
                        row = WebrtcEpochDB(
                            scope_key=scope_key,
                            scope_kind=scope_kind,
                            scope_id=scope_id,
                            epoch=1,
                            generation=1,
                            owner_hub_id=hub_id,
                            lease_expires_at=now + lease_seconds,
                            updated_at=now,
                        )
                        session.add(row)
                        if audit_event_factory is not None:
                            SqlSemanticMediaAuditOutbox.enqueue_in_session(
                                session,
                                audit_event_factory(row.epoch),
                            )
                        session.commit()
                        return EpochClaimResult(True, "ok", row.epoch, row.generation)
                    if row.closed_at is not None:
                        return EpochClaimResult(False, "scope_closed")
                    ownership_change = row.owner_hub_id != hub_id
                    if ownership_change and row.lease_expires_at > now:
                        return EpochClaimResult(False, "epoch_split_brain")
                    if advance or ownership_change:
                        row.epoch += 1
                        row.generation += 1
                    row.owner_hub_id = hub_id
                    row.lease_expires_at = now + lease_seconds
                    row.updated_at = now
                    session.add(row)
                    selected_audit_factory = (
                        takeover_audit_event_factory
                        if ownership_change and takeover_audit_event_factory is not None
                        else audit_event_factory
                    )
                    if selected_audit_factory is not None:
                        SqlSemanticMediaAuditOutbox.enqueue_in_session(
                            session,
                            selected_audit_factory(row.epoch),
                        )
                    session.commit()
                    session.refresh(row)
                    return EpochClaimResult(
                        True,
                        "ok",
                        row.epoch,
                        row.generation,
                        ownership_changed=ownership_change,
                    )
            except IntegrityError:
                if attempt:
                    raise
        return EpochClaimResult(False, "epoch_conflict")

    def get(self, scope_kind: str, scope_id: str) -> WebrtcEpochDB | None:
        with Session(self._engine) as session:
            return session.get(WebrtcEpochDB, f"{scope_kind}:{scope_id}")

    def close(self, scope_kind: str, scope_id: str, *, now: float) -> None:
        scope_key = f"{scope_kind}:{scope_id}"
        with Session(self._engine) as session:
            row = session.get(WebrtcEpochDB, scope_key)
            if row is not None and row.closed_at is None:
                row.closed_at = now
                row.lease_expires_at = now
                row.updated_at = now
                session.add(row)
            session.exec(delete(WebrtcReplayStateDB).where(WebrtcReplayStateDB.scope_key == scope_key))
            session.commit()

    def count_replay_senders(self, scope_key: str, *, now: float) -> int:
        with Session(self._engine) as session:
            rows = session.exec(
                select(WebrtcReplayStateDB).where(
                    WebrtcReplayStateDB.scope_key == scope_key,
                    WebrtcReplayStateDB.expires_at > now,
                )
            ).all()
            return len({row.sender_id for row in rows})

    def has_replay_state(
        self,
        *,
        scope_key: str,
        epoch: int,
        sender_id: str,
        traffic_class: str,
    ) -> bool:
        state_id = hashlib.sha256(f"{scope_key}\0{epoch}\0{sender_id}\0{traffic_class}".encode("utf-8")).hexdigest()
        with Session(self._engine) as session:
            return session.get(WebrtcReplayStateDB, state_id) is not None

    def update_replay(
        self,
        *,
        scope_key: str,
        epoch: int,
        sender_id: str,
        traffic_class: str,
        sequence: int,
        nonce_digest: str | None,
        window_size: int,
        expires_at: float,
        now: float,
    ) -> tuple[bool, str]:
        state_id = hashlib.sha256(f"{scope_key}\0{epoch}\0{sender_id}\0{traffic_class}".encode("utf-8")).hexdigest()
        with Session(self._engine) as session:
            row = session.exec(
                select(WebrtcReplayStateDB).where(WebrtcReplayStateDB.id == state_id).with_for_update()
            ).first()
            if row is None or row.expires_at <= now:
                row = WebrtcReplayStateDB(
                    id=state_id,
                    scope_key=scope_key,
                    epoch=epoch,
                    sender_id=sender_id,
                    traffic_class=traffic_class,
                    highest_sequence=0,
                    accepted_sequences=[],
                    accepted_nonce_digests={},
                    expires_at=expires_at,
                    updated_at=now,
                )
            accepted = {int(value) for value in row.accepted_sequences if isinstance(value, int)}
            if sequence in accepted:
                return False, "sequence_duplicate"
            nonce_sequences = {
                str(digest): int(accepted_sequence)
                for digest, accepted_sequence in dict(row.accepted_nonce_digests or {}).items()
                if isinstance(digest, str) and isinstance(accepted_sequence, int)
            }
            if nonce_digest is not None and nonce_digest in nonce_sequences:
                return False, "nonce_reuse"
            if row.highest_sequence and sequence <= row.highest_sequence - window_size:
                return False, "sequence_too_old"
            if row.highest_sequence and sequence > row.highest_sequence + 4096:
                return False, "sequence_too_far_ahead"
            highest = max(row.highest_sequence, sequence)
            floor = max(1, highest - window_size + 1)
            accepted.add(sequence)
            if nonce_digest is not None:
                nonce_sequences[nonce_digest] = sequence
            row.highest_sequence = highest
            row.accepted_sequences = sorted(value for value in accepted if value >= floor)
            row.accepted_nonce_digests = {
                digest: accepted_sequence
                for digest, accepted_sequence in sorted(nonce_sequences.items())
                if accepted_sequence >= floor
            }
            row.expires_at = expires_at
            row.updated_at = now
            session.add(row)
            session.commit()
            return True, "ok"

    def prune_expired_replay(self, *, now: float) -> int:
        with Session(self._engine) as session:
            result = session.exec(delete(WebrtcReplayStateDB).where(WebrtcReplayStateDB.expires_at <= now))
            session.commit()
            return int(getattr(result, "rowcount", 0) or 0)
