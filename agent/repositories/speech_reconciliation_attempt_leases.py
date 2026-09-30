"""Fenced attempt lease lifecycle (claim, renew, pause, cancel, fence, expire)."""

from __future__ import annotations

from sqlalchemy import func
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_reconciliation import (
    SpeechReconciliationAttemptDB,
    SpeechReconciliationJobDB,
)
from agent.models.speech_reconciliation_state_machine import SpeechReconciliationStateMachine
from agent.repositories.speech_reconciliation_audit_stager import SpeechReconciliationAuditStager
from agent.repositories.speech_reconciliation_locking import locked_job
from agent.repositories.speech_reconciliation_records import (
    SpeechReconciliationAttemptRecord,
    SpeechReconciliationRepositoryError,
    attempt_record,
    resolve_now_ms,
)


class SpeechReconciliationAttemptLeases:
    """Owns attempt lease and fencing-epoch transitions under Hub authority."""

    def __init__(
        self,
        *,
        states: SpeechReconciliationStateMachine,
        audit: SpeechReconciliationAuditStager,
    ) -> None:
        self._states = states
        self._audit = audit


    def claim_attempt(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_id: str,
        expected_job_version: int,
        worker_id_digest: str,
        worker_capability_digest: str,
        location_digest: str,
        resource_profile_digest: str,
        fencing_token_digest: str,
        lease_expires_at_ms: int,
        now_ms: int | None = None,
    ) -> SpeechReconciliationAttemptRecord:
        now = resolve_now_ms(now_ms)
        with Session(engine) as session:
            job = locked_job(session, tenant_id, owner_subject, job_id)
            if job.version != expected_job_version or job.state not in {"queued", "running"}:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_claim_stale")
            active = None
            if job.active_attempt_id:
                active = session.get(SpeechReconciliationAttemptDB, job.active_attempt_id)
            if active is not None and active.state == "running" and active.lease_expires_at_ms > now:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_attempt_already_claimed")
            attempt_number = (
                int(
                    session.exec(
                        select(func.count(SpeechReconciliationAttemptDB.id)).where(
                            SpeechReconciliationAttemptDB.job_id == job.id
                        )
                    ).one()
                )
                + 1
            )
            epoch = job.fencing_epoch + 1
            attempt = SpeechReconciliationAttemptDB(
                job_id=job.id,
                tenant_id=tenant_id,
                owner_subject=owner_subject,
                attempt_number=attempt_number,
                worker_id_digest=worker_id_digest,
                worker_capability_digest=worker_capability_digest,
                location_digest=location_digest,
                resource_profile_digest=resource_profile_digest,
                fencing_token_digest=fencing_token_digest,
                fencing_epoch=epoch,
                lease_expires_at_ms=min(lease_expires_at_ms, job.deadline_at_ms),
                deadline_at_ms=job.deadline_at_ms,
                last_heartbeat_at_ms=now,
                created_at_ms=now,
                updated_at_ms=now,
            )
            if attempt.lease_expires_at_ms <= now:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_lease_invalid", status_code=422)
            if active is not None and active.state == "running":
                active.state = "fenced"
                active.finished_at_ms = now
                active.updated_at_ms = now
                active.version += 1
                session.add(active)
            session.add(attempt)
            session.flush()
            job.active_attempt_id = attempt.id
            job.fencing_epoch = epoch
            job.state = "running"
            job.stage = "staging"
            job.reason_code = "speech_reconciliation_attempt_claimed"
            job.version += 1
            job.updated_at_ms = now
            session.add(job)
            if active is not None and active.state == "fenced":
                self._audit.enqueue(
                    session,
                    self._audit.prepare(
                        tenant_id=tenant_id,
                        job_id=job_id,
                        idempotency_key=f"speech-reconciliation:replaced-fence:{active.id}:{active.version}",
                        event_type="semantic_lease",
                        transition="fenced",
                        reason_code="speech_reconciliation_stale_attempt_replaced",
                        epoch=active.fencing_epoch,
                        lease_ref=active.id,
                    ),
                )
            self._audit.enqueue(
                session,
                self._audit.prepare(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    idempotency_key=f"speech-reconciliation:claim:{attempt.id}:{epoch}",
                    event_type="semantic_lease",
                    transition="acquired",
                    reason_code="speech_reconciliation_attempt_claimed",
                    epoch=epoch,
                    lease_ref=attempt.id,
                ),
            )
            self._audit.enqueue(
                session,
                self._audit.prepare(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    idempotency_key=f"speech-reconciliation:running:{job_id}:{epoch}",
                    event_type="semantic_job",
                    transition="running",
                    reason_code="speech_reconciliation_attempt_claimed",
                    epoch=epoch,
                    lease_ref=attempt.id,
                ),
            )
            session.commit()
            session.refresh(attempt)
            return attempt_record(attempt)

    def heartbeat(
        self,
        *,
        job_id: str,
        attempt_id: str,
        fencing_epoch: int,
        fencing_token_digest: str,
        expected_version: int,
        lease_expires_at_ms: int,
        now_ms: int | None = None,
    ) -> SpeechReconciliationAttemptRecord:
        now = resolve_now_ms(now_ms)
        with Session(engine) as session:
            attempt = session.exec(
                select(SpeechReconciliationAttemptDB)
                .where(SpeechReconciliationAttemptDB.id == attempt_id, SpeechReconciliationAttemptDB.job_id == job_id)
                .with_for_update()
            ).first()
            if attempt is None:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_attempt_not_found", status_code=404)
            if (
                attempt.state != "running"
                or attempt.version != expected_version
                or attempt.fencing_epoch != fencing_epoch
                or attempt.fencing_token_digest != fencing_token_digest
                or attempt.lease_expires_at_ms <= now
            ):
                raise SpeechReconciliationRepositoryError("speech_reconciliation_fence_stale")
            attempt.last_heartbeat_at_ms = now
            attempt.lease_expires_at_ms = min(lease_expires_at_ms, attempt.deadline_at_ms)
            attempt.version += 1
            attempt.updated_at_ms = now
            session.add(attempt)
            self._audit.enqueue(
                session,
                self._audit.prepare(
                    tenant_id=attempt.tenant_id,
                    job_id=job_id,
                    idempotency_key=f"speech-reconciliation:heartbeat:{attempt_id}:{attempt.version}",
                    event_type="semantic_lease",
                    transition="renewed",
                    reason_code="speech_reconciliation_heartbeat_accepted",
                    epoch=fencing_epoch,
                    lease_ref=attempt_id,
                ),
            )
            session.commit()
            session.refresh(attempt)
            return attempt_record(attempt)

    def pause_active_attempt(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_id: str,
        attempt_id: str,
        fencing_epoch: int,
        reason_code: str,
        now_ms: int | None = None,
    ) -> bool:
        """Atomically fence one attempt and pause its job under Hub authority."""

        reason = str(reason_code or "").strip()
        if not reason.startswith("speech_reconciliation_") or len(reason) > 128:
            raise SpeechReconciliationRepositoryError(
                "speech_reconciliation_reason_invalid",
                status_code=422,
            )
        now = resolve_now_ms(now_ms)
        with Session(engine) as session:
            job = session.exec(
                select(SpeechReconciliationJobDB)
                .where(
                    SpeechReconciliationJobDB.id == job_id,
                    SpeechReconciliationJobDB.tenant_id == tenant_id,
                    SpeechReconciliationJobDB.owner_subject == owner_subject,
                )
                .with_for_update()
            ).first()
            attempt = session.exec(
                select(SpeechReconciliationAttemptDB)
                .where(
                    SpeechReconciliationAttemptDB.id == attempt_id,
                    SpeechReconciliationAttemptDB.job_id == job_id,
                    SpeechReconciliationAttemptDB.tenant_id == tenant_id,
                    SpeechReconciliationAttemptDB.owner_subject == owner_subject,
                )
                .with_for_update()
            ).first()
            if job is None or attempt is None:
                return False
            if job.state == "paused" and attempt.state == "fenced":
                return True
            if (
                job.state != "running"
                or attempt.state != "running"
                or attempt.fencing_epoch != fencing_epoch
                or job.active_attempt_id != attempt.id
            ):
                return False
            self._states.transition(
                job.state,
                "paused",
                stage=job.stage,
                reason_code=reason,
            )
            attempt.state = "fenced"
            attempt.version += 1
            attempt.updated_at_ms = now
            attempt.finished_at_ms = now
            job.active_attempt_id = None
            job.state = "paused"
            job.reason_code = reason
            job.version += 1
            job.updated_at_ms = now
            session.add(attempt)
            session.add(job)
            self._audit.enqueue(
                session,
                self._audit.prepare(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    idempotency_key=f"speech-reconciliation:pause:{job_id}:{job.version}",
                    event_type="semantic_job",
                    transition="paused",
                    reason_code=reason,
                    epoch=fencing_epoch,
                    lease_ref=attempt_id,
                ),
            )
            session.commit()
            return True

    def cancel_active_attempt(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_id: str,
        attempt_id: str,
        fencing_epoch: int,
        reason_code: str,
        now_ms: int | None = None,
    ) -> bool:
        """Atomically finish an explicitly cancelled attempt and job."""

        reason = str(reason_code or "").strip()
        if not reason.startswith("speech_reconciliation_") or len(reason) > 128:
            raise SpeechReconciliationRepositoryError(
                "speech_reconciliation_reason_invalid",
                status_code=422,
            )
        now = resolve_now_ms(now_ms)
        with Session(engine) as session:
            job = session.exec(
                select(SpeechReconciliationJobDB)
                .where(
                    SpeechReconciliationJobDB.id == job_id,
                    SpeechReconciliationJobDB.tenant_id == tenant_id,
                    SpeechReconciliationJobDB.owner_subject == owner_subject,
                )
                .with_for_update()
            ).first()
            attempt = session.exec(
                select(SpeechReconciliationAttemptDB)
                .where(
                    SpeechReconciliationAttemptDB.id == attempt_id,
                    SpeechReconciliationAttemptDB.job_id == job_id,
                    SpeechReconciliationAttemptDB.tenant_id == tenant_id,
                    SpeechReconciliationAttemptDB.owner_subject == owner_subject,
                )
                .with_for_update()
            ).first()
            if job is None or attempt is None:
                return False
            if job.state == "cancelled" and attempt.state == "cancelled":
                return True
            if (
                job.state != "cancel_requested"
                or attempt.state not in {"running", "cancel_requested"}
                or attempt.fencing_epoch != fencing_epoch
                or job.active_attempt_id not in {None, attempt.id}
            ):
                return False
            self._states.transition(
                job.state,
                "cancelled",
                stage=job.stage,
                reason_code=reason,
            )
            attempt.state = "cancelled"
            attempt.version += 1
            attempt.updated_at_ms = now
            attempt.finished_at_ms = now
            job.active_attempt_id = None
            job.state = "cancelled"
            job.reason_code = reason
            job.version += 1
            job.updated_at_ms = now
            job.finished_at_ms = now
            session.add(attempt)
            session.add(job)
            self._audit.enqueue(
                session,
                self._audit.prepare(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    idempotency_key=f"speech-reconciliation:cancel:{job_id}:{job.version}",
                    event_type="semantic_job",
                    transition="cancelled",
                    reason_code=reason,
                    epoch=fencing_epoch,
                    lease_ref=attempt_id,
                ),
            )
            session.commit()
            return True

    def expire_stale_attempts(self, *, now_ms: int | None = None, limit: int = 100) -> tuple[str, ...]:
        now = resolve_now_ms(now_ms)
        if not 1 <= limit <= 1000:
            raise ValueError("speech reconciliation recovery limit invalid")
        expired: list[str] = []
        with Session(engine) as session:
            attempts = session.exec(
                select(SpeechReconciliationAttemptDB)
                .where(
                    SpeechReconciliationAttemptDB.state == "running",
                    SpeechReconciliationAttemptDB.lease_expires_at_ms <= now,
                )
                .order_by(SpeechReconciliationAttemptDB.lease_expires_at_ms)
                .limit(limit)
                .with_for_update()
            ).all()
            for attempt in attempts:
                attempt.state = "fenced"
                attempt.version += 1
                attempt.updated_at_ms = now
                attempt.finished_at_ms = now
                session.add(attempt)
                job = session.get(SpeechReconciliationJobDB, attempt.job_id)
                transition = "fenced"
                reason_code = "speech_reconciliation_stale_attempt_fenced"
                if job is not None and job.active_attempt_id == attempt.id and job.state == "running":
                    job.state = "queued" if job.deadline_at_ms > now else "expired"
                    job.reason_code = (
                        "speech_reconciliation_stale_attempt_requeued"
                        if job.deadline_at_ms > now
                        else "speech_reconciliation_deadline_expired"
                    )
                    job.active_attempt_id = None
                    job.version += 1
                    job.updated_at_ms = now
                    if job.state == "expired":
                        job.finished_at_ms = now
                    session.add(job)
                    transition = job.state
                    reason_code = job.reason_code
                self._audit.enqueue(
                    session,
                    self._audit.prepare(
                        tenant_id=attempt.tenant_id,
                        job_id=attempt.job_id,
                        idempotency_key=f"speech-reconciliation:expired-attempt:{attempt.id}:{attempt.version}",
                        event_type="semantic_recovery",
                        transition=transition,
                        reason_code=reason_code,
                        epoch=attempt.fencing_epoch,
                        lease_ref=attempt.id,
                    ),
                )
                expired.append(attempt.id)
            session.commit()
        return tuple(expired)

    def fence_attempt(
        self,
        attempt_id: str,
        *,
        reason_code: str,
        authority: str = "hub",
        now_ms: int | None = None,
    ) -> bool:
        if authority != "hub":
            raise PermissionError("speech_reconciliation_hub_fence_authority_required")
        now = resolve_now_ms(now_ms)
        with Session(engine) as session:
            attempt = session.exec(
                select(SpeechReconciliationAttemptDB)
                .where(SpeechReconciliationAttemptDB.id == attempt_id)
                .with_for_update()
            ).first()
            if attempt is None:
                return False
            job = session.exec(
                select(SpeechReconciliationJobDB)
                .where(SpeechReconciliationJobDB.id == attempt.job_id)
                .with_for_update()
            ).first()
            if attempt.state in {"fenced", "cancelled", "completed", "failed"}:
                return True
            attempt.state = "fenced"
            attempt.version += 1
            attempt.updated_at_ms = now
            attempt.finished_at_ms = now
            session.add(attempt)
            if job is not None and job.active_attempt_id == attempt.id:
                job.active_attempt_id = None
                if job.state == "running":
                    job.state = "queued"
                job.reason_code = reason_code
                job.version += 1
                job.updated_at_ms = now
                session.add(job)
            self._audit.enqueue(
                session,
                self._audit.prepare(
                    tenant_id=attempt.tenant_id,
                    job_id=attempt.job_id,
                    idempotency_key=f"speech-reconciliation:fence:{attempt.id}:{attempt.version}",
                    event_type="semantic_lease",
                    transition="fenced",
                    reason_code=reason_code,
                    epoch=attempt.fencing_epoch,
                    lease_ref=attempt.id,
                ),
            )
            session.commit()
            return True


__all__ = ["SpeechReconciliationAttemptLeases"]
