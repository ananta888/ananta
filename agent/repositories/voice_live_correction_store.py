"""Multi-Hub correction claims and revision publication of live-run segments."""

from __future__ import annotations

from sqlmodel import update

from agent.db_models import VoiceLiveRunSegmentDB
from agent.models.voice_governance_domain import VoicePrincipal
from agent.repositories.voice_live_run_queries import (
    advance_timeline,
    find_run,
    find_segment,
)
from agent.repositories.voice_live_run_records import (
    CORRECTION_PROCESSING_LEASE_SECONDS,
    SessionFactory,
    VoiceLiveCorrectionClaim,
    VoiceLiveRunRepositoryConflict,
)


class VoiceLiveCorrectionStore:
    """CAS-owned correction lifecycle of completed live-run segments."""

    def __init__(self, *, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def claim_correction(
        self,
        principal: VoicePrincipal,
        run_id: str,
        sequence: int,
        *,
        provisional_result_ref: str,
        configuration_digest: str,
        now: float,
        lease_seconds: int = CORRECTION_PROCESSING_LEASE_SECONDS,
    ) -> VoiceLiveCorrectionClaim:
        """CAS-claim queued or stale correction work across multiple Hubs."""

        with self._session_factory() as session:
            run = find_run(session, principal, run_id)
            segment = find_segment(session, principal, run_id, sequence)
            if run is None or segment is None:
                raise LookupError("voice live segment not found")
            if run.status != "active":
                return VoiceLiveCorrectionClaim(run, segment, False)
            if (
                segment.status != "completed"
                or segment.provisional_result_ref != provisional_result_ref
                or segment.correction_configuration_digest != configuration_digest
            ):
                raise VoiceLiveRunRepositoryConflict("voice live correction identity changed")
            claimable = segment.correction_status == "queued" or (
                segment.correction_status == "processing"
                and segment.updated_at <= now - max(30, min(int(lease_seconds), 900))
            )
            if not claimable:
                return VoiceLiveCorrectionClaim(run, segment, False)
            previous_status = segment.correction_status
            previous_updated_at = segment.updated_at
            claimed = session.exec(
                update(VoiceLiveRunSegmentDB)
                .where(
                    VoiceLiveRunSegmentDB.id == segment.id,
                    VoiceLiveRunSegmentDB.run_id == run_id,
                    VoiceLiveRunSegmentDB.tenant_id == principal.tenant_id,
                    VoiceLiveRunSegmentDB.owner_subject == principal.subject,
                    VoiceLiveRunSegmentDB.provisional_result_ref == provisional_result_ref,
                    VoiceLiveRunSegmentDB.correction_configuration_digest == configuration_digest,
                    VoiceLiveRunSegmentDB.correction_status == previous_status,
                    VoiceLiveRunSegmentDB.updated_at == previous_updated_at,
                )
                .values(
                    correction_status="processing",
                    correction_task_id=None,
                    correction_attempt_count=VoiceLiveRunSegmentDB.correction_attempt_count + 1,
                    correction_failure_code=None,
                    correction_started_at=now,
                    correction_completed_at=None,
                    updated_at=now,
                )
            )
            if claimed.rowcount != 1:
                session.rollback()
                current = find_segment(session, principal, run_id, sequence)
                if current is None:
                    raise LookupError("voice live segment not found")
                return VoiceLiveCorrectionClaim(run, current, False)
            timeline_revision = advance_timeline(
                session,
                principal,
                run_id,
                now=now,
            )
            projected = session.exec(
                update(VoiceLiveRunSegmentDB)
                .where(
                    VoiceLiveRunSegmentDB.id == segment.id,
                    VoiceLiveRunSegmentDB.correction_status == "processing",
                    VoiceLiveRunSegmentDB.updated_at == now,
                )
                .values(timeline_revision=timeline_revision)
            )
            if projected.rowcount != 1:
                session.rollback()
                raise VoiceLiveRunRepositoryConflict("voice live correction projection changed")
            session.commit()
            current = find_segment(session, principal, run_id, sequence)
            current_run = find_run(session, principal, run_id)
            if current is None or current_run is None:
                raise LookupError("voice live segment not found")
            return VoiceLiveCorrectionClaim(current_run, current, True)

    def bind_correction_task(
        self,
        principal: VoicePrincipal,
        run_id: str,
        sequence: int,
        *,
        provisional_result_ref: str,
        attempt_count: int,
        task_id: str,
        now: float,
    ) -> VoiceLiveRunSegmentDB:
        with self._session_factory() as session:
            segment = find_segment(session, principal, run_id, sequence)
            if segment is None:
                raise LookupError("voice live segment not found")
            if (
                segment.status != "completed"
                or segment.provisional_result_ref != provisional_result_ref
                or segment.correction_status != "processing"
                or segment.correction_attempt_count != attempt_count
            ):
                raise VoiceLiveRunRepositoryConflict("voice live correction ownership changed")
            if segment.correction_task_id and segment.correction_task_id != task_id:
                raise VoiceLiveRunRepositoryConflict("voice live correction task changed")
            segment.correction_task_id = task_id
            segment.updated_at = now
            session.add(segment)
            session.commit()
            session.refresh(segment)
            return segment

    def complete_correction(
        self,
        principal: VoicePrincipal,
        run_id: str,
        sequence: int,
        *,
        provisional_result_ref: str,
        attempt_count: int,
        task_id: str,
        result_ref: str,
        applied: bool,
        reason_code: str,
        now: float,
    ) -> VoiceLiveRunSegmentDB:
        with self._session_factory() as session:
            run = find_run(session, principal, run_id)
            segment = find_segment(session, principal, run_id, sequence)
            if run is None or segment is None:
                raise LookupError("voice live segment not found")
            if run.status != "active":
                raise VoiceLiveRunRepositoryConflict("voice live run is not active")
            if (
                segment.status != "completed"
                or segment.provisional_result_ref != provisional_result_ref
                or segment.correction_status != "processing"
                or segment.correction_attempt_count != attempt_count
                or segment.correction_task_id != task_id
            ):
                raise VoiceLiveRunRepositoryConflict("voice live correction ownership changed")
            timeline_revision = advance_timeline(
                session,
                principal,
                run_id,
                now=now,
            )
            segment.result_ref = result_ref
            segment.correction_status = "completed" if applied else "skipped"
            segment.correction_failure_code = None if applied else str(reason_code or "correction_unchanged")[:120]
            segment.text_revision = 2
            segment.timeline_revision = timeline_revision
            segment.correction_completed_at = now
            segment.updated_at = now
            session.add(segment)
            session.commit()
            session.refresh(segment)
            return segment

    def fail_correction(
        self,
        principal: VoicePrincipal,
        run_id: str,
        sequence: int,
        *,
        provisional_result_ref: str,
        attempt_count: int,
        failure_code: str,
        task_id: str | None,
        now: float,
    ) -> VoiceLiveRunSegmentDB | None:
        with self._session_factory() as session:
            run = find_run(session, principal, run_id)
            segment = find_segment(session, principal, run_id, sequence)
            if run is None or segment is None:
                return None
            if (
                segment.provisional_result_ref != provisional_result_ref
                or segment.correction_attempt_count != attempt_count
                or segment.correction_status != "processing"
                or (task_id is not None and segment.correction_task_id not in {None, task_id})
            ):
                return segment
            if run.status != "active":
                return segment
            timeline_revision = advance_timeline(
                session,
                principal,
                run_id,
                now=now,
            )
            segment.result_ref = segment.provisional_result_ref
            segment.correction_status = "failed"
            segment.correction_failure_code = str(failure_code or "correction_failed")[:120]
            if task_id:
                segment.correction_task_id = task_id
            segment.text_revision = 2
            segment.timeline_revision = timeline_revision
            segment.correction_completed_at = now
            segment.updated_at = now
            session.add(segment)
            session.commit()
            session.refresh(segment)
            return segment


__all__ = ["VoiceLiveCorrectionStore"]
