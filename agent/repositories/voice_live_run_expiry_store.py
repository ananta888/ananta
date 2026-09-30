"""Expiry of abandoned live runs and the Hub maintenance reconciliation lease."""

from __future__ import annotations

import uuid

from sqlalchemy import or_
from sqlmodel import select, update

from agent.db_models import VoiceLiveRunDB, VoiceLiveRunSegmentDB
from agent.models.voice_governance_domain import VoicePrincipal
from agent.repositories.voice_live_run_queries import advance_timeline, find_run
from agent.repositories.voice_live_run_records import SessionFactory


class VoiceLiveRunExpiryStore:
    """Expire runs lazily or via CAS-claimed maintenance batches."""

    def __init__(self, *, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def mark_expired(
        self,
        principal: VoicePrincipal,
        run_id: str,
        *,
        now: float,
    ) -> VoiceLiveRunDB | None:
        with self._session_factory() as session:
            run = find_run(session, principal, run_id)
            if run is None:
                return None
            if run.status in {"active", "finalizing"} and run.expires_at <= now:
                expiring_segments = list(
                    session.exec(
                        select(VoiceLiveRunSegmentDB).where(
                            VoiceLiveRunSegmentDB.run_id == run_id,
                            VoiceLiveRunSegmentDB.tenant_id == principal.tenant_id,
                            VoiceLiveRunSegmentDB.owner_subject == principal.subject,
                            or_(
                                VoiceLiveRunSegmentDB.status == "processing",
                                VoiceLiveRunSegmentDB.correction_status.in_(["queued", "processing"]),
                            ),
                        )
                    ).all()
                )
                for segment in expiring_segments:
                    timeline_revision = advance_timeline(
                        session,
                        principal,
                        run_id,
                        now=now,
                        statuses=("active", "finalizing"),
                    )
                    if segment.status == "processing":
                        segment.status = "failed"
                        segment.failure_code = "run_expired"
                        segment.completed_at = now
                    if segment.correction_status in {"queued", "processing"}:
                        segment.result_ref = segment.provisional_result_ref
                        segment.correction_status = "failed"
                        segment.correction_failure_code = "run_expired"
                        segment.text_revision = 2
                        segment.correction_completed_at = now
                    segment.timeline_revision = timeline_revision
                    segment.updated_at = now
                    session.add(segment)
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
                        timeline_revision=VoiceLiveRunDB.timeline_revision + 1,
                    )
                )
                session.commit()
                if expired.rowcount != 1:
                    return find_run(session, principal, run_id)
                return find_run(session, principal, run_id)
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
        with self._session_factory() as session:
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
                candidate = session.get(VoiceLiveRunDB, run_id)
                if candidate is None:
                    continue
                principal = VoicePrincipal(
                    tenant_id=candidate.tenant_id,
                    subject=candidate.owner_subject,
                )
                expiring_segments = list(
                    session.exec(
                        select(VoiceLiveRunSegmentDB).where(
                            VoiceLiveRunSegmentDB.run_id == run_id,
                            or_(
                                VoiceLiveRunSegmentDB.status == "processing",
                                VoiceLiveRunSegmentDB.correction_status.in_(["queued", "processing"]),
                            ),
                        )
                    ).all()
                )
                for segment in expiring_segments:
                    timeline_revision = advance_timeline(
                        session,
                        principal,
                        str(run_id),
                        now=now,
                        statuses=("active", "finalizing", "expired"),
                    )
                    if segment.status == "processing":
                        segment.status = "failed"
                        segment.failure_code = "run_expired"
                        segment.completed_at = now
                    if segment.correction_status in {"queued", "processing"}:
                        segment.result_ref = segment.provisional_result_ref
                        segment.correction_status = "failed"
                        segment.correction_failure_code = "run_expired"
                        segment.text_revision = 2
                        segment.correction_completed_at = now
                    segment.timeline_revision = timeline_revision
                    segment.updated_at = now
                    session.add(segment)
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
                        timeline_revision=VoiceLiveRunDB.timeline_revision + 1,
                    )
                )
                if claimed.rowcount == 1:
                    claimed_ids.append(str(run_id))
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
        with self._session_factory() as session:
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
        with self._session_factory() as session:
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


__all__ = ["VoiceLiveRunExpiryStore"]
