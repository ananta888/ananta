from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete, select, update

from agent.database import engine
from agent.db_models import VoiceLiveRunDB, VoiceLiveRunSegmentDB
from agent.models.voice_governance_domain import VoicePrincipal
from agent.repositories.voice_live_correction_store import VoiceLiveCorrectionStore
from agent.repositories.voice_live_run_expiry_store import VoiceLiveRunExpiryStore
from agent.repositories.voice_live_run_finalization_store import VoiceLiveRunFinalizationStore
from agent.repositories.voice_live_run_queries import find_by_idempotency, find_run, find_segment
from agent.repositories.voice_live_run_records import (
    CORRECTION_PROCESSING_LEASE_SECONDS,
    SessionFactory,
    VoiceLiveCorrectionClaim,
    VoiceLiveRunRepositoryConflict,
    VoiceLiveRunRepositoryInProgress,
    VoiceLiveSegmentReservation,
)
from agent.repositories.voice_live_segment_store import VoiceLiveSegmentStore


def _default_session() -> Session:
    """Production session factory on the hub database engine.

    Other databases (for example isolated race databases in tests) are
    injected through ``VoiceLiveRunRepository(session_factory=...)``.
    """
    return Session(engine)


class VoiceLiveRunRepository:
    """Tenant-scoped persistence port for Hub-owned long-run metadata.

    The port composes focused stores (segments, corrections, finalization,
    expiry) that share one session factory; run identity, snapshots,
    heartbeats and deletion stay here.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory | None = None,
        segments: VoiceLiveSegmentStore | None = None,
        corrections: VoiceLiveCorrectionStore | None = None,
        finalization: VoiceLiveRunFinalizationStore | None = None,
        expiry: VoiceLiveRunExpiryStore | None = None,
    ) -> None:
        self._session_factory = session_factory or _default_session
        self._segments = segments or VoiceLiveSegmentStore(session_factory=self._session_factory)
        self._corrections = corrections or VoiceLiveCorrectionStore(session_factory=self._session_factory)
        self._finalization = finalization or VoiceLiveRunFinalizationStore(
            session_factory=self._session_factory
        )
        self._expiry = expiry or VoiceLiveRunExpiryStore(session_factory=self._session_factory)

    def create(self, run: VoiceLiveRunDB) -> tuple[VoiceLiveRunDB, bool]:
        with self._session_factory() as session:
            existing = find_by_idempotency(
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
                existing = find_by_idempotency(
                    session,
                    VoicePrincipal(tenant_id=run.tenant_id, subject=run.owner_subject),
                    run.idempotency_key_digest,
                )
                if existing is None:
                    raise
                return existing, True

    def get(self, principal: VoicePrincipal, run_id: str) -> VoiceLiveRunDB | None:
        with self._session_factory() as session:
            return find_run(session, principal, run_id)

    def list_segments(
        self,
        principal: VoicePrincipal,
        run_id: str,
    ) -> tuple[VoiceLiveRunSegmentDB, ...]:
        with self._session_factory() as session:
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
        with self._session_factory() as session:
            return find_segment(session, principal, run_id, sequence)

    def heartbeat(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        last_local_sequence: int | None,
        reported_gap_sequences: tuple[int, ...],
        now: float,
    ) -> VoiceLiveRunDB | None:
        with self._session_factory() as session:
            run = find_run(session, principal, run_id)
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
                return find_run(session, principal, run_id)
            run = find_run(session, principal, run_id)
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

    # -- expiry ---------------------------------------------------------

    def mark_expired(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        now: float,
    ) -> VoiceLiveRunDB | None:
        return self._expiry.mark_expired(principal, run_id, now=now)

    def claim_expired_runs(
        self,
        *,
        now: float,
        limit: int = 500,
        lease_seconds: int = 300,
    ) -> tuple[VoiceLiveRunDB, ...]:
        """CAS-claim a bounded batch of abandoned runs for Hub maintenance."""

        return self._expiry.claim_expired_runs(now=now, limit=limit, lease_seconds=lease_seconds)

    def complete_expiry_reconciliation(
        self,
        run_id: str,
        *,
        lease_token: str,
        now: float,
    ) -> bool:
        return self._expiry.complete_expiry_reconciliation(run_id, lease_token=lease_token, now=now)

    def release_expiry_reconciliation(
        self,
        run_id: str,
        *,
        lease_token: str,
    ) -> bool:
        return self._expiry.release_expiry_reconciliation(run_id, lease_token=lease_token)

    # -- segments -------------------------------------------------------

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
        return self._segments.reserve_segment(
            principal,
            run_id,
            sequence=sequence,
            idempotency_key_digest=idempotency_key_digest,
            audio_binding=audio_binding,
            started_at_ms=started_at_ms,
            ended_at_ms=ended_at_ms,
            duration_ms=duration_ms,
            overlap_milliseconds=overlap_milliseconds,
            now=now,
        )

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
        return self._segments.complete_segment(
            principal,
            run_id,
            sequence,
            idempotency_key_digest=idempotency_key_digest,
            attempt_count=attempt_count,
            task_id=task_id,
            result_ref=result_ref,
        )

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

        return self._segments.publish_provisional(
            principal,
            run_id,
            sequence,
            idempotency_key_digest=idempotency_key_digest,
            attempt_count=attempt_count,
            task_id=task_id,
            result_ref=result_ref,
            correction_configuration_digest=correction_configuration_digest,
            correction_spec_ref=correction_spec_ref,
            correction_requested=correction_requested,
            now=now,
        )

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
        return self._segments.bind_segment_task(
            principal,
            run_id,
            sequence,
            idempotency_key_digest=idempotency_key_digest,
            attempt_count=attempt_count,
            task_id=task_id,
        )

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
        return self._segments.fail_segment(
            principal,
            run_id,
            sequence,
            idempotency_key_digest=idempotency_key_digest,
            attempt_count=attempt_count,
            failure_code=failure_code,
            task_id=task_id,
        )

    # -- corrections ----------------------------------------------------

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

        return self._corrections.claim_correction(
            principal,
            run_id,
            sequence,
            provisional_result_ref=provisional_result_ref,
            configuration_digest=configuration_digest,
            now=now,
            lease_seconds=lease_seconds,
        )

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
        return self._corrections.bind_correction_task(
            principal,
            run_id,
            sequence,
            provisional_result_ref=provisional_result_ref,
            attempt_count=attempt_count,
            task_id=task_id,
            now=now,
        )

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
        return self._corrections.complete_correction(
            principal,
            run_id,
            sequence,
            provisional_result_ref=provisional_result_ref,
            attempt_count=attempt_count,
            task_id=task_id,
            result_ref=result_ref,
            applied=applied,
            reason_code=reason_code,
            now=now,
        )

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
        return self._corrections.fail_correction(
            principal,
            run_id,
            sequence,
            provisional_result_ref=provisional_result_ref,
            attempt_count=attempt_count,
            failure_code=failure_code,
            task_id=task_id,
            now=now,
        )

    # -- finalization ---------------------------------------------------

    def begin_finalize(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        expected_last_sequence: int | None,
        now: float,
    ) -> tuple[VoiceLiveRunDB, bool]:
        return self._finalization.begin_finalize(
            principal,
            run_id,
            expected_last_sequence=expected_last_sequence,
            now=now,
        )

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
        return self._finalization.complete_finalize(
            principal,
            run_id,
            expected_version=expected_version,
            result_ref=result_ref,
            has_gaps=has_gaps,
            stop_reason=stop_reason,
            now=now,
        )

    def abort_finalize(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        expected_version: int,
        now: float,
    ) -> bool:
        return self._finalization.abort_finalize(
            principal,
            run_id,
            expected_version=expected_version,
            now=now,
        )

    # -- deletion -------------------------------------------------------

    def delete_profile(self, principal: VoicePrincipal, profile_id: str) -> dict[str, int]:
        with self._session_factory() as session:
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

        with self._session_factory() as session:
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


__all__ = [
    "SessionFactory",
    "VoiceLiveCorrectionClaim",
    "VoiceLiveRunRepository",
    "VoiceLiveRunRepositoryConflict",
    "VoiceLiveRunRepositoryInProgress",
    "VoiceLiveSegmentReservation",
]
