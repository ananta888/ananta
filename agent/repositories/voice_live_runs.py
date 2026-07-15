from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete, select, update

from agent.database import engine
from agent.db_models import VoiceLiveRunDB, VoiceLiveRunSegmentDB
from agent.services.voice_governance_domain import VoicePrincipal


class VoiceLiveRunRepositoryConflict(RuntimeError):
    pass


class VoiceLiveRunRepositoryInProgress(RuntimeError):
    pass


@dataclass(frozen=True)
class VoiceLiveSegmentReservation:
    segment: VoiceLiveRunSegmentDB
    replayed: bool


class VoiceLiveRunRepository:
    """Tenant-scoped persistence port for Hub-owned long-run metadata."""

    def create(self, run: VoiceLiveRunDB) -> tuple[VoiceLiveRunDB, bool]:
        with Session(engine) as session:
            existing = self._find_by_idempotency(
                session,
                VoicePrincipal(tenant_id=run.tenant_id, subject=run.owner_subject),
                run.idempotency_key_digest,
            )
            if existing is not None:
                return existing, True
            session.add(run)
            try:
                session.commit()
                session.refresh(run)
                return run, False
            except IntegrityError:
                session.rollback()
                existing = self._find_by_idempotency(
                    session,
                    VoicePrincipal(tenant_id=run.tenant_id, subject=run.owner_subject),
                    run.idempotency_key_digest,
                )
                if existing is None:
                    raise
                return existing, True

    def get(self, principal: VoicePrincipal, run_id: str) -> VoiceLiveRunDB | None:
        with Session(engine) as session:
            return self._find_run(session, principal, run_id)

    def list_segments(
        self,
        principal: VoicePrincipal,
        run_id: str,
    ) -> tuple[VoiceLiveRunSegmentDB, ...]:
        with Session(engine) as session:
            rows = session.exec(
                select(VoiceLiveRunSegmentDB)
                .where(
                    VoiceLiveRunSegmentDB.run_id == run_id,
                    VoiceLiveRunSegmentDB.tenant_id == principal.tenant_id,
                    VoiceLiveRunSegmentDB.owner_subject == principal.subject,
                )
                .order_by(VoiceLiveRunSegmentDB.sequence.asc())
            ).all()
            return tuple(rows)

    def get_segment(
        self,
        principal: VoicePrincipal,
        run_id: str,
        sequence: int,
    ) -> VoiceLiveRunSegmentDB | None:
        with Session(engine) as session:
            return self._find_segment(session, principal, run_id, sequence)

    def heartbeat(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        last_local_sequence: int | None,
        reported_gap_sequences: tuple[int, ...],
        now: float,
    ) -> VoiceLiveRunDB | None:
        with Session(engine) as session:
            run = self._find_run(session, principal, run_id)
            if run is None:
                return None
            if run.status != "active":
                return run
            claimed = session.exec(
                update(VoiceLiveRunDB)
                .where(
                    VoiceLiveRunDB.id == run_id,
                    VoiceLiveRunDB.tenant_id == principal.tenant_id,
                    VoiceLiveRunDB.owner_subject == principal.subject,
                    VoiceLiveRunDB.status == "active",
                )
                .values(version=VoiceLiveRunDB.version + 1, updated_at=now)
            )
            if claimed.rowcount != 1:
                session.rollback()
                return self._find_run(session, principal, run_id)
            run = self._find_run(session, principal, run_id)
            if run is None:
                session.rollback()
                return None
            if last_local_sequence is not None:
                run.last_local_sequence = max(
                    int(last_local_sequence),
                    int(run.last_local_sequence if run.last_local_sequence is not None else -1),
                )
            if reported_gap_sequences:
                run.reported_gap_sequences = sorted(
                    {
                        *[int(item) for item in (run.reported_gap_sequences or [])],
                        *[int(item) for item in reported_gap_sequences],
                    }
                )
            run.last_heartbeat_at = now
            run.updated_at = now
            session.add(run)
            session.commit()
            session.refresh(run)
            return run

    def mark_expired(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        now: float,
    ) -> VoiceLiveRunDB | None:
        with Session(engine) as session:
            run = self._find_run(session, principal, run_id)
            if run is None:
                return None
            if run.status in {"active", "finalizing"} and run.expires_at <= now:
                expired = session.exec(
                    update(VoiceLiveRunDB)
                    .where(
                        VoiceLiveRunDB.id == run_id,
                        VoiceLiveRunDB.tenant_id == principal.tenant_id,
                        VoiceLiveRunDB.owner_subject == principal.subject,
                        VoiceLiveRunDB.status.in_(["active", "finalizing"]),
                        VoiceLiveRunDB.expires_at <= now,
                    )
                    .values(
                        status="expired",
                        stop_reason="run_expired",
                        stopped_at=now,
                        updated_at=now,
                        version=VoiceLiveRunDB.version + 1,
                    )
                )
                if expired.rowcount == 1:
                    session.exec(
                        update(VoiceLiveRunSegmentDB)
                        .where(
                            VoiceLiveRunSegmentDB.run_id == run_id,
                            VoiceLiveRunSegmentDB.tenant_id == principal.tenant_id,
                            VoiceLiveRunSegmentDB.owner_subject == principal.subject,
                            VoiceLiveRunSegmentDB.status == "processing",
                        )
                        .values(
                            status="failed",
                            failure_code="run_expired",
                            completed_at=now,
                            updated_at=now,
                        )
                    )
                session.commit()
                if expired.rowcount != 1:
                    return self._find_run(session, principal, run_id)
                return self._find_run(session, principal, run_id)
            return run

    def claim_expired_runs(
        self,
        *,
        now: float,
        limit: int = 500,
        lease_seconds: int = 300,
    ) -> tuple[VoiceLiveRunDB, ...]:
        """CAS-claim a bounded batch of abandoned runs for Hub maintenance."""

        bounded_limit = max(1, min(int(limit), 2_000))
        bounded_lease = max(30, min(int(lease_seconds), 900))
        claimed_ids: list[str] = []
        with Session(engine) as session:
            claimable = or_(
                (VoiceLiveRunDB.status.in_(["active", "finalizing"]) & (VoiceLiveRunDB.expires_at <= now)),
                (
                    (VoiceLiveRunDB.status == "expired")
                    & VoiceLiveRunDB.maintenance_reconciled_at.is_(None)
                    & (
                        VoiceLiveRunDB.maintenance_lease_expires_at.is_(None)
                        | (VoiceLiveRunDB.maintenance_lease_expires_at <= now)
                    )
                ),
            )
            candidate_ids = list(
                session.exec(
                    select(VoiceLiveRunDB.id)
                    .where(claimable)
                    .order_by(VoiceLiveRunDB.expires_at.asc(), VoiceLiveRunDB.id.asc())
                    .limit(bounded_limit)
                ).all()
            )
            for run_id in candidate_ids:
                lease_token = f"voice-live-maintenance-{uuid.uuid4()}"
                claimed = session.exec(
                    update(VoiceLiveRunDB)
                    .where(
                        VoiceLiveRunDB.id == run_id,
                        claimable,
                    )
                    .values(
                        status="expired",
                        stop_reason="run_expired",
                        stopped_at=now,
                        updated_at=now,
                        maintenance_lease_token=lease_token,
                        maintenance_lease_expires_at=now + bounded_lease,
                        version=VoiceLiveRunDB.version + 1,
                    )
                )
                if claimed.rowcount == 1:
                    claimed_ids.append(str(run_id))
            if claimed_ids:
                session.exec(
                    update(VoiceLiveRunSegmentDB)
                    .where(
                        VoiceLiveRunSegmentDB.run_id.in_(claimed_ids),
                        VoiceLiveRunSegmentDB.status == "processing",
                    )
                    .values(
                        status="failed",
                        failure_code="run_expired",
                        completed_at=now,
                        updated_at=now,
                    )
                )
            session.commit()
            if not claimed_ids:
                return ()
            rows = list(
                session.exec(
                    select(VoiceLiveRunDB)
                    .where(VoiceLiveRunDB.id.in_(claimed_ids))
                    .order_by(VoiceLiveRunDB.expires_at.asc(), VoiceLiveRunDB.id.asc())
                ).all()
            )
            return tuple(rows)

    def complete_expiry_reconciliation(
        self,
        run_id: str,
        *,
        lease_token: str,
        now: float,
    ) -> bool:
        with Session(engine) as session:
            completed = session.exec(
                update(VoiceLiveRunDB)
                .where(
                    VoiceLiveRunDB.id == run_id,
                    VoiceLiveRunDB.status == "expired",
                    VoiceLiveRunDB.maintenance_reconciled_at.is_(None),
                    VoiceLiveRunDB.maintenance_lease_token == lease_token,
                )
                .values(
                    maintenance_reconciled_at=now,
                    maintenance_lease_token=None,
                    maintenance_lease_expires_at=None,
                    updated_at=now,
                    version=VoiceLiveRunDB.version + 1,
                )
            )
            session.commit()
            return completed.rowcount == 1

    def release_expiry_reconciliation(
        self,
        run_id: str,
        *,
        lease_token: str,
    ) -> bool:
        with Session(engine) as session:
            released = session.exec(
                update(VoiceLiveRunDB)
                .where(
                    VoiceLiveRunDB.id == run_id,
                    VoiceLiveRunDB.status == "expired",
                    VoiceLiveRunDB.maintenance_reconciled_at.is_(None),
                    VoiceLiveRunDB.maintenance_lease_token == lease_token,
                )
                .values(
                    maintenance_lease_token=None,
                    maintenance_lease_expires_at=None,
                )
            )
            session.commit()
            return released.rowcount == 1

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
            with Session(engine) as session:
                run = self._find_run(session, principal, run_id)
                if run is None:
                    raise LookupError("voice live run not found")
                if run.status != "active":
                    raise VoiceLiveRunRepositoryConflict("voice live run is not active")
                claimed = session.exec(
                    update(VoiceLiveRunDB)
                    .where(
                        VoiceLiveRunDB.id == run_id,
                        VoiceLiveRunDB.tenant_id == principal.tenant_id,
                        VoiceLiveRunDB.owner_subject == principal.subject,
                        VoiceLiveRunDB.status == "active",
                        VoiceLiveRunDB.expires_at >= now,
                    )
                    .values(version=VoiceLiveRunDB.version + 1, updated_at=now)
                )
                if claimed.rowcount != 1:
                    session.rollback()
                    current = self._find_run(session, principal, run_id)
                    if current is None:
                        raise LookupError("voice live run not found")
                    raise VoiceLiveRunRepositoryConflict("voice live run is not active or has expired")
                existing = self._find_segment(session, principal, run_id, sequence)
                if existing is not None:
                    self._assert_same_segment(
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
                        if existing.updated_at > now - 600:
                            raise VoiceLiveRunRepositoryInProgress("voice live segment is already processing")
                        existing.attempt_count += 1
                        existing.task_id = None
                        existing.result_ref = None
                        existing.completed_at = None
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
                    existing.completed_at = None
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
        with Session(engine) as session:
            run = self._find_run(session, principal, run_id)
            if run is None:
                raise LookupError("voice live run not found")
            claimed = session.exec(
                update(VoiceLiveRunDB)
                .where(
                    VoiceLiveRunDB.id == run_id,
                    VoiceLiveRunDB.tenant_id == principal.tenant_id,
                    VoiceLiveRunDB.owner_subject == principal.subject,
                    VoiceLiveRunDB.status == "active",
                )
                .values(version=VoiceLiveRunDB.version + 1, updated_at=time.time())
            )
            if claimed.rowcount != 1:
                session.rollback()
                current = self._find_run(session, principal, run_id)
                if current is None:
                    raise LookupError("voice live run not found")
                raise VoiceLiveRunRepositoryConflict("voice live run is not active")
            segment = self._find_segment(session, principal, run_id, sequence)
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
            segment.status = "completed"
            segment.task_id = task_id
            segment.result_ref = result_ref
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
        with Session(engine) as session:
            segment = self._find_segment(session, principal, run_id, sequence)
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
        with Session(engine) as session:
            segment = self._find_segment(session, principal, run_id, sequence)
            if segment is None or segment.idempotency_key_digest != idempotency_key_digest:
                return None
            if attempt_count is not None and segment.attempt_count != attempt_count:
                return segment
            if segment.status == "completed":
                return segment
            segment.status = "failed"
            segment.failure_code = str(failure_code or "segment_processing_failed")[:120]
            if task_id:
                segment.task_id = task_id
            segment.updated_at = time.time()
            session.add(segment)
            session.commit()
            session.refresh(segment)
            return segment

    def begin_finalize(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        expected_last_sequence: int | None,
        now: float,
    ) -> tuple[VoiceLiveRunDB, bool]:
        with Session(engine) as session:
            run = self._find_run(session, principal, run_id)
            if run is None:
                raise LookupError("voice live run not found")
            if run.status in {"completed", "completed_with_gaps", "stopped", "expired"}:
                return run, True
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
            run = self._find_run(session, principal, run_id)
            if run is None:
                session.rollback()
                raise LookupError("voice live run not found")
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
            fresh_processing: list[VoiceLiveRunSegmentDB] = []
            for segment in processing:
                if segment.updated_at <= now - 600:
                    segment.status = "failed"
                    segment.failure_code = "processing_lease_expired"
                    segment.updated_at = now
                    session.add(segment)
                else:
                    fresh_processing.append(segment)
            if fresh_processing:
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
        with Session(engine) as session:
            run = self._find_run(session, principal, run_id)
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
                current = self._find_run(session, principal, run_id)
                if (
                    current is not None
                    and current.status in {"completed", "completed_with_gaps"}
                    and current.final_result_ref == result_ref
                ):
                    return current
                raise VoiceLiveRunRepositoryConflict("voice live run finalization ownership changed")
            session.commit()
            current = self._find_run(session, principal, run_id)
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
        with Session(engine) as session:
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

    def delete_profile(self, principal: VoicePrincipal, profile_id: str) -> dict[str, int]:
        with Session(engine) as session:
            runs = list(
                session.exec(
                    select(VoiceLiveRunDB).where(
                        VoiceLiveRunDB.tenant_id == principal.tenant_id,
                        VoiceLiveRunDB.owner_subject == principal.subject,
                        VoiceLiveRunDB.profile_id == profile_id,
                    )
                ).all()
            )
            run_ids = [run.id for run in runs]
            segment_count = 0
            if run_ids:
                segment_ids = session.exec(
                    select(VoiceLiveRunSegmentDB.id).where(
                        VoiceLiveRunSegmentDB.run_id.in_(run_ids),
                        VoiceLiveRunSegmentDB.tenant_id == principal.tenant_id,
                        VoiceLiveRunSegmentDB.owner_subject == principal.subject,
                    )
                ).all()
                segment_count = len(segment_ids)
                session.exec(
                    delete(VoiceLiveRunSegmentDB).where(
                        VoiceLiveRunSegmentDB.run_id.in_(run_ids),
                        VoiceLiveRunSegmentDB.tenant_id == principal.tenant_id,
                        VoiceLiveRunSegmentDB.owner_subject == principal.subject,
                    )
                )
                session.exec(
                    delete(VoiceLiveRunDB).where(
                        VoiceLiveRunDB.id.in_(run_ids),
                        VoiceLiveRunDB.tenant_id == principal.tenant_id,
                        VoiceLiveRunDB.owner_subject == principal.subject,
                    )
                )
            session.commit()
        return {
            VoiceLiveRunDB.__tablename__: len(runs),
            VoiceLiveRunSegmentDB.__tablename__: segment_count,
        }

    def delete_run_identity(
        self,
        principal: VoicePrincipal,
        *,
        run_id: str,
        profile_id: str,
        parent_task_id: str,
        created_at: float,
    ) -> bool:
        """Delete only the exact run instance rejected by a completion fence."""

        with Session(engine) as session:
            run = session.exec(
                select(VoiceLiveRunDB).where(
                    VoiceLiveRunDB.id == run_id,
                    VoiceLiveRunDB.tenant_id == principal.tenant_id,
                    VoiceLiveRunDB.owner_subject == principal.subject,
                    VoiceLiveRunDB.profile_id == profile_id,
                    VoiceLiveRunDB.parent_task_id == parent_task_id,
                    VoiceLiveRunDB.created_at == created_at,
                )
            ).first()
            if run is None:
                return False
            session.exec(
                delete(VoiceLiveRunSegmentDB).where(
                    VoiceLiveRunSegmentDB.run_id == run_id,
                    VoiceLiveRunSegmentDB.tenant_id == principal.tenant_id,
                    VoiceLiveRunSegmentDB.owner_subject == principal.subject,
                )
            )
            removed = session.exec(
                delete(VoiceLiveRunDB).where(
                    VoiceLiveRunDB.id == run_id,
                    VoiceLiveRunDB.tenant_id == principal.tenant_id,
                    VoiceLiveRunDB.owner_subject == principal.subject,
                    VoiceLiveRunDB.profile_id == profile_id,
                    VoiceLiveRunDB.parent_task_id == parent_task_id,
                    VoiceLiveRunDB.created_at == created_at,
                )
            )
            session.commit()
            return removed.rowcount == 1

    @staticmethod
    def _find_run(
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

    @staticmethod
    def _find_by_idempotency(
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

    @staticmethod
    def _find_segment(
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

    @staticmethod
    def _assert_same_segment(
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
