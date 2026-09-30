"""Segment reservation, task binding and ASR completion ledger of live runs."""

from __future__ import annotations

import time

from sqlalchemy.exc import IntegrityError
from sqlmodel import update

from agent.db_models import VoiceLiveRunDB, VoiceLiveRunSegmentDB
from agent.models.voice_governance_domain import VoicePrincipal
from agent.repositories.voice_live_run_queries import (
    advance_timeline,
    assert_same_segment,
    find_run,
    find_segment,
    returned_int,
)
from agent.repositories.voice_live_run_records import (
    SEGMENT_PROCESSING_LEASE_SECONDS,
    SessionFactory,
    VoiceLiveRunRepositoryConflict,
    VoiceLiveRunRepositoryInProgress,
    VoiceLiveSegmentReservation,
)


class VoiceLiveSegmentStore:
    """Idempotent, attempt-fenced state transitions of live-run segments."""

    def __init__(self, *, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def reserve_segment(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        sequence: int,
        idempotency_key_digest: str,
        audio_binding: str | None,
        started_at_ms: int,
        ended_at_ms: int,
        duration_ms: int,
        overlap_milliseconds: int,
        now: float,
    ) -> VoiceLiveSegmentReservation:
        for attempt in range(2):
            with self._session_factory() as session:
                run = find_run(session, principal, run_id)
                if run is None:
                    raise LookupError("voice live run not found")
                if run.status != "active":
                    raise VoiceLiveRunRepositoryConflict("voice live run is not active")
                timeline_row = session.exec(
                    update(VoiceLiveRunDB)
                    .where(
                        VoiceLiveRunDB.id == run_id,
                        VoiceLiveRunDB.tenant_id == principal.tenant_id,
                        VoiceLiveRunDB.owner_subject == principal.subject,
                        VoiceLiveRunDB.status == "active",
                        VoiceLiveRunDB.expires_at >= now,
                    )
                    .values(
                        version=VoiceLiveRunDB.version + 1,
                        timeline_revision=VoiceLiveRunDB.timeline_revision + 1,
                        updated_at=now,
                    )
                    .returning(VoiceLiveRunDB.timeline_revision)
                ).first()
                if timeline_row is None:
                    session.rollback()
                    current = find_run(session, principal, run_id)
                    if current is None:
                        raise LookupError("voice live run not found")
                    raise VoiceLiveRunRepositoryConflict("voice live run is not active or has expired")
                existing = find_segment(session, principal, run_id, sequence)
                current_run = find_run(session, principal, run_id)
                if current_run is None:
                    session.rollback()
                    raise LookupError("voice live run not found")
                current_run.updated_at = now
                session.add(current_run)
                if existing is not None:
                    assert_same_segment(
                        existing,
                        idempotency_key_digest=idempotency_key_digest,
                        audio_binding=audio_binding,
                        started_at_ms=started_at_ms,
                        ended_at_ms=ended_at_ms,
                        duration_ms=duration_ms,
                        overlap_milliseconds=overlap_milliseconds,
                    )
                    if existing.status == "completed":
                        return VoiceLiveSegmentReservation(existing, True)
                    if existing.status == "processing":
                        if existing.updated_at > now - SEGMENT_PROCESSING_LEASE_SECONDS:
                            raise VoiceLiveRunRepositoryInProgress("voice live segment is already processing")
                        existing.attempt_count += 1
                        existing.task_id = None
                        existing.result_ref = None
                        existing.provisional_result_ref = None
                        existing.correction_task_id = None
                        existing.correction_status = "not_requested"
                        existing.correction_configuration_digest = None
                        existing.correction_attempt_count = 0
                        existing.correction_failure_code = None
                        existing.text_revision = 0
                        existing.timeline_revision = returned_int(timeline_row)
                        existing.completed_at = None
                        existing.correction_started_at = None
                        existing.correction_completed_at = None
                        existing.updated_at = now
                        session.add(existing)
                        session.commit()
                        session.refresh(existing)
                        return VoiceLiveSegmentReservation(existing, False)
                    if existing.status != "failed":
                        raise VoiceLiveRunRepositoryConflict(
                            "voice live segment cannot be retried from its current state"
                        )
                    existing.status = "processing"
                    existing.attempt_count += 1
                    existing.failure_code = None
                    existing.task_id = None
                    existing.result_ref = None
                    existing.provisional_result_ref = None
                    existing.correction_task_id = None
                    existing.correction_status = "not_requested"
                    existing.correction_configuration_digest = None
                    existing.correction_attempt_count = 0
                    existing.correction_failure_code = None
                    existing.text_revision = 0
                    existing.timeline_revision = returned_int(timeline_row)
                    existing.completed_at = None
                    existing.correction_started_at = None
                    existing.correction_completed_at = None
                    existing.updated_at = now
                    session.add(existing)
                    session.commit()
                    session.refresh(existing)
                    return VoiceLiveSegmentReservation(existing, False)

                # Segments captured inside the bounded timeline may be drained
                # from an offline client spool during the finalization grace.
                # The service validates ended_at_ms against max_duration; the
                # durable expiry remains the hard wall for first registration.
                segment = VoiceLiveRunSegmentDB(
                    run_id=run_id,
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    sequence=sequence,
                    idempotency_key_digest=idempotency_key_digest,
                    audio_binding=audio_binding,
                    started_at_ms=started_at_ms,
                    ended_at_ms=ended_at_ms,
                    duration_ms=duration_ms,
                    overlap_milliseconds=overlap_milliseconds,
                    timeline_revision=returned_int(timeline_row),
                    created_at=now,
                    updated_at=now,
                )
                session.add(segment)
                try:
                    session.commit()
                    session.refresh(segment)
                    return VoiceLiveSegmentReservation(segment, False)
                except IntegrityError:
                    session.rollback()
                    if attempt:
                        raise
        raise RuntimeError("voice live segment reservation failed")

    def complete_segment(
        self,
        principal: VoicePrincipal,
        run_id: str,
        sequence: int,
        *,
        idempotency_key_digest: str,
        attempt_count: int,
        task_id: str,
        result_ref: str,
    ) -> VoiceLiveRunSegmentDB:
        with self._session_factory() as session:
            run = find_run(session, principal, run_id)
            if run is None:
                raise LookupError("voice live run not found")
            timeline_row = session.exec(
                update(VoiceLiveRunDB)
                .where(
                    VoiceLiveRunDB.id == run_id,
                    VoiceLiveRunDB.tenant_id == principal.tenant_id,
                    VoiceLiveRunDB.owner_subject == principal.subject,
                    VoiceLiveRunDB.status == "active",
                )
                .values(
                    version=VoiceLiveRunDB.version + 1,
                    timeline_revision=VoiceLiveRunDB.timeline_revision + 1,
                    updated_at=time.time(),
                )
                .returning(VoiceLiveRunDB.timeline_revision)
            ).first()
            if timeline_row is None:
                session.rollback()
                current = find_run(session, principal, run_id)
                if current is None:
                    raise LookupError("voice live run not found")
                raise VoiceLiveRunRepositoryConflict("voice live run is not active")
            segment = find_segment(session, principal, run_id, sequence)
            if segment is None:
                raise LookupError("voice live segment not found")
            if segment.idempotency_key_digest != idempotency_key_digest:
                raise VoiceLiveRunRepositoryConflict("voice live segment idempotency conflict")
            if segment.attempt_count != attempt_count:
                raise VoiceLiveRunRepositoryConflict("voice live segment attempt was superseded")
            if segment.status == "completed":
                if segment.task_id != task_id or segment.result_ref != result_ref:
                    raise VoiceLiveRunRepositoryConflict("voice live segment result conflict")
                return segment
            if segment.status != "processing":
                raise VoiceLiveRunRepositoryConflict("voice live segment is not processing")
            if segment.task_id != task_id:
                raise VoiceLiveRunRepositoryConflict("voice live segment task ownership changed")
            now = time.time()
            run.updated_at = now
            session.add(run)
            segment.status = "completed"
            segment.task_id = task_id
            segment.result_ref = result_ref
            segment.provisional_result_ref = result_ref
            segment.correction_status = "not_requested"
            segment.text_revision = 2
            segment.timeline_revision = returned_int(timeline_row)
            segment.failure_code = None
            segment.completed_at = now
            segment.updated_at = now
            session.add(segment)
            session.commit()
            session.refresh(segment)
            return segment

    def publish_provisional(
        self,
        principal: VoicePrincipal,
        run_id: str,
        sequence: int,
        *,
        idempotency_key_digest: str,
        attempt_count: int,
        task_id: str,
        result_ref: str,
        correction_configuration_digest: str | None,
        correction_spec_ref: str | None,
        correction_requested: bool,
        now: float,
    ) -> VoiceLiveRunSegmentDB:
        """Atomically acknowledge ASR and publish its encrypted text revision."""

        with self._session_factory() as session:
            run = find_run(session, principal, run_id)
            segment = find_segment(session, principal, run_id, sequence)
            if run is None or segment is None:
                raise LookupError("voice live segment not found")
            if run.status != "active":
                raise VoiceLiveRunRepositoryConflict("voice live run is not active")
            if segment.idempotency_key_digest != idempotency_key_digest:
                raise VoiceLiveRunRepositoryConflict("voice live segment idempotency conflict")
            if segment.attempt_count != attempt_count:
                raise VoiceLiveRunRepositoryConflict("voice live segment attempt was superseded")
            if segment.status == "completed":
                if segment.task_id != task_id or segment.provisional_result_ref != result_ref:
                    raise VoiceLiveRunRepositoryConflict("voice live segment provisional result conflict")
                return segment
            if segment.status != "processing" or segment.task_id != task_id:
                raise VoiceLiveRunRepositoryConflict("voice live segment task ownership changed")
            timeline_revision = advance_timeline(
                session,
                principal,
                run_id,
                now=now,
            )
            segment.status = "completed"
            segment.task_id = task_id
            segment.result_ref = result_ref
            segment.provisional_result_ref = result_ref
            segment.correction_status = "queued" if correction_requested else "not_requested"
            segment.correction_configuration_digest = correction_configuration_digest
            segment.correction_spec_ref = correction_spec_ref
            segment.correction_failure_code = None
            segment.text_revision = 1 if correction_requested else 2
            segment.timeline_revision = timeline_revision
            segment.failure_code = None
            segment.completed_at = now
            segment.updated_at = now
            session.add(segment)
            session.commit()
            session.refresh(segment)
            return segment

    def bind_segment_task(
        self,
        principal: VoicePrincipal,
        run_id: str,
        sequence: int,
        *,
        idempotency_key_digest: str,
        attempt_count: int,
        task_id: str,
    ) -> VoiceLiveRunSegmentDB:
        with self._session_factory() as session:
            segment = find_segment(session, principal, run_id, sequence)
            if segment is None:
                raise LookupError("voice live segment not found")
            if segment.idempotency_key_digest != idempotency_key_digest:
                raise VoiceLiveRunRepositoryConflict("voice live segment idempotency conflict")
            if segment.attempt_count != attempt_count:
                raise VoiceLiveRunRepositoryConflict("voice live segment attempt was superseded")
            if segment.status != "processing":
                raise VoiceLiveRunRepositoryConflict("voice live segment is not processing")
            if segment.task_id and segment.task_id != task_id:
                raise VoiceLiveRunRepositoryConflict("voice live segment task conflict")
            segment.task_id = task_id
            segment.updated_at = time.time()
            session.add(segment)
            session.commit()
            session.refresh(segment)
            return segment

    def fail_segment(
        self,
        principal: VoicePrincipal,
        run_id: str,
        sequence: int,
        *,
        idempotency_key_digest: str,
        attempt_count: int | None = None,
        failure_code: str,
        task_id: str | None = None,
    ) -> VoiceLiveRunSegmentDB | None:
        with self._session_factory() as session:
            run = find_run(session, principal, run_id)
            segment = find_segment(session, principal, run_id, sequence)
            if (
                run is None
                or run.status != "active"
                or segment is None
                or segment.idempotency_key_digest != idempotency_key_digest
            ):
                return None
            if attempt_count is not None and segment.attempt_count != attempt_count:
                return segment
            if segment.status == "completed":
                return segment
            timeline_revision = advance_timeline(
                session,
                principal,
                run_id,
                now=time.time(),
            )
            segment.status = "failed"
            segment.failure_code = str(failure_code or "segment_processing_failed")[:120]
            if task_id:
                segment.task_id = task_id
            segment.timeline_revision = timeline_revision
            segment.updated_at = time.time()
            session.add(segment)
            session.commit()
            session.refresh(segment)
            return segment


__all__ = ["VoiceLiveSegmentStore"]
