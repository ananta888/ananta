"""Tenant-scoped row lookups and timeline CAS shared by live-run stores."""

from __future__ import annotations

from sqlmodel import Session, select, update

from agent.db_models import VoiceLiveRunDB, VoiceLiveRunSegmentDB
from agent.models.voice_governance_domain import VoicePrincipal
from agent.repositories.voice_live_run_records import VoiceLiveRunRepositoryConflict


def advance_timeline(
    session: Session,
    principal: VoicePrincipal,
    run_id: str,
    *,
    now: float,
    statuses: tuple[str, ...] = ("active",),
) -> int:
    """Atomically reserve one globally monotone visible timeline revision."""

    row = session.exec(
        update(VoiceLiveRunDB)
        .where(
            VoiceLiveRunDB.id == run_id,
            VoiceLiveRunDB.tenant_id == principal.tenant_id,
            VoiceLiveRunDB.owner_subject == principal.subject,
            VoiceLiveRunDB.status.in_(statuses),
        )
        .values(
            timeline_revision=VoiceLiveRunDB.timeline_revision + 1,
            version=VoiceLiveRunDB.version + 1,
            updated_at=now,
        )
        .returning(VoiceLiveRunDB.timeline_revision)
    ).first()
    if row is None:
        raise VoiceLiveRunRepositoryConflict("voice live run is not active")
    return returned_int(row)


def returned_int(value: object) -> int:
    if isinstance(value, int):
        return value
    try:
        return int(value[0])  # type: ignore[index]
    except (IndexError, TypeError, ValueError) as exc:
        raise RuntimeError("database did not return a timeline revision") from exc


def find_run(
    session: Session,
    principal: VoicePrincipal,
    run_id: str,
) -> VoiceLiveRunDB | None:
    statement = select(VoiceLiveRunDB).where(
        VoiceLiveRunDB.id == run_id,
        VoiceLiveRunDB.tenant_id == principal.tenant_id,
        VoiceLiveRunDB.owner_subject == principal.subject,
    )
    return session.exec(statement).first()


def find_by_idempotency(
    session: Session,
    principal: VoicePrincipal,
    idempotency_key_digest: str,
) -> VoiceLiveRunDB | None:
    return session.exec(
        select(VoiceLiveRunDB).where(
            VoiceLiveRunDB.tenant_id == principal.tenant_id,
            VoiceLiveRunDB.owner_subject == principal.subject,
            VoiceLiveRunDB.idempotency_key_digest == idempotency_key_digest,
        )
    ).first()


def find_segment(
    session: Session,
    principal: VoicePrincipal,
    run_id: str,
    sequence: int,
) -> VoiceLiveRunSegmentDB | None:
    return session.exec(
        select(VoiceLiveRunSegmentDB).where(
            VoiceLiveRunSegmentDB.run_id == run_id,
            VoiceLiveRunSegmentDB.sequence == sequence,
            VoiceLiveRunSegmentDB.tenant_id == principal.tenant_id,
            VoiceLiveRunSegmentDB.owner_subject == principal.subject,
        )
    ).first()


def assert_same_segment(
    segment: VoiceLiveRunSegmentDB,
    *,
    idempotency_key_digest: str,
    audio_binding: str | None,
    started_at_ms: int,
    ended_at_ms: int,
    duration_ms: int,
    overlap_milliseconds: int,
) -> None:
    if (
        segment.idempotency_key_digest != idempotency_key_digest
        or segment.audio_binding != audio_binding
        or segment.started_at_ms != started_at_ms
        or segment.ended_at_ms != ended_at_ms
        or segment.duration_ms != duration_ms
        or segment.overlap_milliseconds != overlap_milliseconds
    ):
        raise VoiceLiveRunRepositoryConflict("voice live segment sequence was already used with different input")


__all__ = [
    "advance_timeline",
    "assert_same_segment",
    "find_by_idempotency",
    "find_run",
    "find_segment",
    "returned_int",
]
