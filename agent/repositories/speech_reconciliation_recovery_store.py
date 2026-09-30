"""Content-free collector and recovery projections plus Hub recovery actions."""

from __future__ import annotations

import time

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_reconciliation import (
    SpeechReconciliationAttemptDB,
    SpeechReconciliationJobDB,
)
from agent.repositories.speech_reconciliation_audit_stager import SpeechReconciliationAuditStager
from agent.repositories.speech_reconciliation_records import (
    SpeechReconciliationCollectibleAttempt,
    active_job_contract,
)


class SpeechReconciliationRecoveryStore:
    """Projects in-flight attempts for collectors and applies recovery decisions."""

    def __init__(self, *, audit: SpeechReconciliationAuditStager) -> None:
        self._audit = audit


    def list_collectible_attempts(
        self,
        *,
        now_ms: int,
        limit: int = 100,
    ) -> tuple[SpeechReconciliationCollectibleAttempt, ...]:
        """Return only currently fenced attempts that may need poll/cancel work."""

        if not 1 <= limit <= 1000:
            raise ValueError("speech_reconciliation_collector_batch_invalid")
        with Session(engine) as session:
            pairs = session.exec(
                select(SpeechReconciliationJobDB, SpeechReconciliationAttemptDB)
                .join(
                    SpeechReconciliationAttemptDB,
                    SpeechReconciliationAttemptDB.job_id == SpeechReconciliationJobDB.id,
                )
                .where(
                    SpeechReconciliationJobDB.state.in_(["running", "cancel_requested"]),
                    SpeechReconciliationAttemptDB.state.in_(["running", "cancel_requested"]),
                    SpeechReconciliationAttemptDB.deadline_at_ms > now_ms,
                )
                .order_by(
                    SpeechReconciliationAttemptDB.last_heartbeat_at_ms.asc(),
                    SpeechReconciliationAttemptDB.id.asc(),
                )
                .limit(limit)
            ).all()
        projected: list[SpeechReconciliationCollectibleAttempt] = []
        for job, attempt in pairs:
            if job.state == "running" and job.active_attempt_id != attempt.id:
                continue
            projected.append(
                SpeechReconciliationCollectibleAttempt(
                    tenant_id=job.tenant_id,
                    owner_subject=job.owner_subject,
                    job_state=job.state,
                    job_contract=active_job_contract(job, attempt),
                    attempt_version=attempt.version,
                    lease_expires_at_ms=attempt.lease_expires_at_ms,
                )
            )
        return tuple(projected)

    def attempt_audit_binding(self, attempt_id: str) -> tuple[str, str, int, str] | None:
        """Return the minimum content-free scope needed for audit repair."""

        with Session(engine) as session:
            pair = session.exec(
                select(SpeechReconciliationAttemptDB, SpeechReconciliationJobDB)
                .join(
                    SpeechReconciliationJobDB,
                    SpeechReconciliationJobDB.id == SpeechReconciliationAttemptDB.job_id,
                )
                .where(SpeechReconciliationAttemptDB.id == attempt_id)
            ).first()
            if pair is None:
                return None
            attempt, job = pair
            return job.tenant_id, job.id, attempt.fencing_epoch, job.reason_code

    def list_recovery_candidates(self, *, now_ms: int, limit: int):
        """Project persisted recovery facts without exposing payload content."""

        if not 1 <= limit <= 1000:
            raise ValueError("speech_reconciliation_recovery_batch_invalid")
        from agent.db_models.speech_evidence import SpeechEvidenceConsentDB
        from agent.ports.speech_reconciliation_recovery import SpeechReconciliationRecoveryCandidate

        with Session(engine) as session:
            pairs = session.exec(
                select(SpeechReconciliationAttemptDB, SpeechReconciliationJobDB)
                .join(
                    SpeechReconciliationJobDB,
                    SpeechReconciliationJobDB.id == SpeechReconciliationAttemptDB.job_id,
                )
                .where(
                    SpeechReconciliationAttemptDB.state.in_(["running", "cancel_requested"]),
                    SpeechReconciliationJobDB.state.in_(["running", "cancel_requested"]),
                )
                .order_by(SpeechReconciliationAttemptDB.updated_at_ms, SpeechReconciliationAttemptDB.id)
                .limit(limit)
            ).all()
            candidates = []
            for attempt, job in pairs:
                consent = session.exec(
                    select(SpeechEvidenceConsentDB).where(
                        SpeechEvidenceConsentDB.id == job.consent_id,
                        SpeechEvidenceConsentDB.tenant_id == job.tenant_id,
                        SpeechEvidenceConsentDB.owner_subject == job.owner_subject,
                    )
                ).first()
                if (
                    consent is None
                    or consent.state != "active"
                    or consent.expires_at_ms <= now_ms
                    or consent.consent_version != job.consent_version
                    or consent.revocation_epoch != job.revocation_epoch
                ):
                    condition = "consent_revoked"
                elif job.deadline_at_ms <= now_ms:
                    condition = "job_expired"
                elif attempt.state == "cancel_requested" and attempt.updated_at_ms + 30_000 <= now_ms:
                    condition = "cancel_grace_elapsed"
                elif attempt.lease_expires_at_ms <= now_ms or attempt.last_heartbeat_at_ms + 30_000 <= now_ms:
                    condition = "stale_heartbeat"
                else:
                    continue
                candidates.append(
                    SpeechReconciliationRecoveryCandidate(
                        job_id=job.id,
                        attempt_id=attempt.id,
                        state=job.state,
                        stage=job.stage,
                        expected_version=job.version,
                        fencing_epoch=attempt.fencing_epoch,
                        retry_count=max(0, attempt.attempt_number - 1),
                        max_retries=2,
                        checkpoint_ref=attempt.checkpoint_ref,
                        condition=condition,
                    )
                )
            return tuple(candidates)

    def apply_recovery(self, candidate, action, *, authority: str) -> bool:
        if authority != "hub":
            raise PermissionError("speech_reconciliation_hub_recovery_authority_required")
        now = time.time_ns() // 1_000_000
        with Session(engine) as session:
            job = session.exec(
                select(SpeechReconciliationJobDB)
                .where(SpeechReconciliationJobDB.id == candidate.job_id)
                .with_for_update()
            ).first()
            attempt = session.exec(
                select(SpeechReconciliationAttemptDB)
                .where(
                    SpeechReconciliationAttemptDB.id == candidate.attempt_id,
                    SpeechReconciliationAttemptDB.job_id == candidate.job_id,
                )
                .with_for_update()
            ).first()
            if job is None or attempt is None:
                return False
            if (
                job.version != candidate.expected_version
                or attempt.fencing_epoch != candidate.fencing_epoch
                or attempt.state not in {"running", "cancel_requested"}
                or (action.resume_checkpoint_ref is not None and action.resume_checkpoint_ref != attempt.checkpoint_ref)
            ):
                return bool(
                    job.state == action.target_state
                    and job.active_attempt_id != attempt.id
                    and attempt.state != "running"
                )
            attempt.state = "cancelled" if action.target_state in {"cancelled", "expired"} else "fenced"
            attempt.version += 1
            attempt.updated_at_ms = now
            attempt.finished_at_ms = now
            job.active_attempt_id = None
            job.state = action.target_state
            job.reason_code = action.reason_code
            job.version += 1
            job.updated_at_ms = now
            if action.target_state in {"cancelled", "expired", "failed"}:
                job.finished_at_ms = now
            session.add(attempt)
            session.add(job)
            self._audit.enqueue(
                session,
                self._audit.prepare(
                    tenant_id=job.tenant_id,
                    job_id=job.id,
                    idempotency_key=f"speech-reconciliation:recovery:{attempt.id}:{job.version}",
                    event_type="semantic_recovery",
                    transition=action.target_state,
                    reason_code=action.reason_code,
                    epoch=attempt.fencing_epoch,
                    lease_ref=attempt.id,
                ),
            )
            session.commit()
            return True


__all__ = ["SpeechReconciliationRecoveryStore"]
