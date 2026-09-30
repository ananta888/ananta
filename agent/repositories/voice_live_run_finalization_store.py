"""Versioned finalization ownership of live runs (begin, complete, abort)."""

from __future__ import annotations

from sqlmodel import select, update

from agent.db_models import VoiceLiveRunDB, VoiceLiveRunSegmentDB
from agent.models.voice_governance_domain import VoicePrincipal
from agent.repositories.voice_live_run_queries import advance_timeline, find_run
from agent.repositories.voice_live_run_records import (
    CORRECTION_PROCESSING_LEASE_SECONDS,
    SEGMENT_PROCESSING_LEASE_SECONDS,
    SessionFactory,
    VoiceLiveRunRepositoryConflict,
    VoiceLiveRunRepositoryInProgress,
)


class VoiceLiveRunFinalizationStore:
    """Fence live-run finalization with lease reclaim and version CAS."""

    def __init__(self, *, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def begin_finalize(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        expected_last_sequence: int | None,
        now: float,
    ) -> tuple[VoiceLiveRunDB, bool]:
        with self._session_factory() as session:
            run = find_run(session, principal, run_id)
            if run is None:
                raise LookupError("voice live run not found")
            if run.status in {"completed", "completed_with_gaps", "stopped", "expired"}:
                return run, True
            if run.status == "active":
                processing_segments = list(
                    session.exec(
                        select(VoiceLiveRunSegmentDB).where(
                            VoiceLiveRunSegmentDB.run_id == run_id,
                            VoiceLiveRunSegmentDB.tenant_id == principal.tenant_id,
                            VoiceLiveRunSegmentDB.owner_subject == principal.subject,
                            VoiceLiveRunSegmentDB.status == "processing",
                        )
                    ).all()
                )
                for segment in processing_segments:
                    if segment.updated_at > now - SEGMENT_PROCESSING_LEASE_SECONDS:
                        continue
                    timeline_revision = advance_timeline(
                        session,
                        principal,
                        run_id,
                        now=now,
                    )
                    segment.status = "failed"
                    segment.failure_code = "processing_lease_expired"
                    segment.timeline_revision = timeline_revision
                    segment.updated_at = now
                    session.add(segment)
                pending_corrections = list(
                    session.exec(
                        select(VoiceLiveRunSegmentDB).where(
                            VoiceLiveRunSegmentDB.run_id == run_id,
                            VoiceLiveRunSegmentDB.tenant_id == principal.tenant_id,
                            VoiceLiveRunSegmentDB.owner_subject == principal.subject,
                            VoiceLiveRunSegmentDB.correction_status.in_(["queued", "processing"]),
                        )
                    ).all()
                )
                for segment in pending_corrections:
                    if segment.updated_at > now - CORRECTION_PROCESSING_LEASE_SECONDS:
                        continue
                    timeline_revision = advance_timeline(
                        session,
                        principal,
                        run_id,
                        now=now,
                    )
                    segment.result_ref = segment.provisional_result_ref
                    segment.correction_status = "failed"
                    segment.correction_failure_code = "correction_lease_expired"
                    segment.text_revision = 2 if segment.provisional_result_ref else segment.text_revision
                    segment.timeline_revision = timeline_revision
                    segment.correction_completed_at = now
                    segment.updated_at = now
                    session.add(segment)
            if run.status == "active":
                claimed = session.exec(
                    update(VoiceLiveRunDB)
                    .where(
                        VoiceLiveRunDB.id == run_id,
                        VoiceLiveRunDB.tenant_id == principal.tenant_id,
                        VoiceLiveRunDB.owner_subject == principal.subject,
                        VoiceLiveRunDB.status == "active",
                    )
                    .values(
                        status="finalizing",
                        version=VoiceLiveRunDB.version + 1,
                        updated_at=now,
                    )
                )
                if claimed.rowcount != 1:
                    session.rollback()
                    raise VoiceLiveRunRepositoryInProgress("voice live run state changed during finalization")
            elif run.status == "finalizing":
                if run.updated_at > now - 600:
                    raise VoiceLiveRunRepositoryInProgress("voice live run finalization is already in progress")
                reclaimed = session.exec(
                    update(VoiceLiveRunDB)
                    .where(
                        VoiceLiveRunDB.id == run_id,
                        VoiceLiveRunDB.tenant_id == principal.tenant_id,
                        VoiceLiveRunDB.owner_subject == principal.subject,
                        VoiceLiveRunDB.status == "finalizing",
                        VoiceLiveRunDB.updated_at <= now - 600,
                    )
                    .values(version=VoiceLiveRunDB.version + 1, updated_at=now)
                )
                if reclaimed.rowcount != 1:
                    session.rollback()
                    raise VoiceLiveRunRepositoryInProgress("voice live run finalization ownership changed")
            else:
                raise VoiceLiveRunRepositoryConflict("voice live run cannot be finalized")
            run = find_run(session, principal, run_id)
            if run is None:
                session.rollback()
                raise LookupError("voice live run not found")
            correction_in_flight = list(
                session.exec(
                    select(VoiceLiveRunSegmentDB).where(
                        VoiceLiveRunSegmentDB.run_id == run_id,
                        VoiceLiveRunSegmentDB.tenant_id == principal.tenant_id,
                        VoiceLiveRunSegmentDB.owner_subject == principal.subject,
                        VoiceLiveRunSegmentDB.correction_status.in_(["queued", "processing"]),
                    )
                ).all()
            )
            if correction_in_flight:
                session.rollback()
                raise VoiceLiveRunRepositoryInProgress(
                    "voice live run still has in-flight corrections"
                )
            processing = list(
                session.exec(
                    select(VoiceLiveRunSegmentDB).where(
                        VoiceLiveRunSegmentDB.run_id == run_id,
                        VoiceLiveRunSegmentDB.tenant_id == principal.tenant_id,
                        VoiceLiveRunSegmentDB.owner_subject == principal.subject,
                        VoiceLiveRunSegmentDB.status == "processing",
                    )
                ).all()
            )
            if processing:
                session.rollback()
                raise VoiceLiveRunRepositoryInProgress("voice live run still has in-flight segments")
            if expected_last_sequence is not None:
                run.expected_last_sequence = max(
                    int(expected_last_sequence),
                    int(run.expected_last_sequence if run.expected_last_sequence is not None else -1),
                )
                run.last_local_sequence = max(
                    int(expected_last_sequence),
                    int(run.last_local_sequence if run.last_local_sequence is not None else -1),
                )
            run.updated_at = now
            session.add(run)
            session.commit()
            session.refresh(run)
            return run, False

    def complete_finalize(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        expected_version: int,
        result_ref: str,
        has_gaps: bool,
        stop_reason: str,
        now: float,
    ) -> VoiceLiveRunDB:
        with self._session_factory() as session:
            run = find_run(session, principal, run_id)
            if run is None:
                raise LookupError("voice live run not found")
            if run.status in {"completed", "completed_with_gaps"}:
                if run.final_result_ref != result_ref:
                    raise VoiceLiveRunRepositoryConflict("voice live run result conflict")
                return run
            completed = session.exec(
                update(VoiceLiveRunDB)
                .where(
                    VoiceLiveRunDB.id == run_id,
                    VoiceLiveRunDB.tenant_id == principal.tenant_id,
                    VoiceLiveRunDB.owner_subject == principal.subject,
                    VoiceLiveRunDB.status == "finalizing",
                    VoiceLiveRunDB.version == expected_version,
                )
                .values(
                    status="completed_with_gaps" if has_gaps else "completed",
                    final_result_ref=result_ref,
                    stop_reason=str(stop_reason or "user_stop")[:120],
                    stopped_at=now,
                    updated_at=now,
                    version=VoiceLiveRunDB.version + 1,
                )
            )
            if completed.rowcount != 1:
                session.rollback()
                current = find_run(session, principal, run_id)
                if (
                    current is not None
                    and current.status in {"completed", "completed_with_gaps"}
                    and current.final_result_ref == result_ref
                ):
                    return current
                raise VoiceLiveRunRepositoryConflict("voice live run finalization ownership changed")
            session.commit()
            current = find_run(session, principal, run_id)
            if current is None:
                raise LookupError("voice live run not found")
            return current

    def abort_finalize(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        expected_version: int,
        now: float,
    ) -> bool:
        with self._session_factory() as session:
            aborted = session.exec(
                update(VoiceLiveRunDB)
                .where(
                    VoiceLiveRunDB.id == run_id,
                    VoiceLiveRunDB.tenant_id == principal.tenant_id,
                    VoiceLiveRunDB.owner_subject == principal.subject,
                    VoiceLiveRunDB.status == "finalizing",
                    VoiceLiveRunDB.version == expected_version,
                )
                .values(
                    status="active",
                    updated_at=now,
                    version=VoiceLiveRunDB.version + 1,
                )
            )
            session.commit()
            return aborted.rowcount == 1


__all__ = ["VoiceLiveRunFinalizationStore"]
