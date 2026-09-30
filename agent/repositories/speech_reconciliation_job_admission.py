"""Job admission and owner-scoped job reads for speech reconciliation."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_reconciliation import SpeechReconciliationJobDB
from agent.repositories.speech_evidence_lineage import (
    SpeechEvidenceLineageRepository,
    SpeechLineageEdge,
    SpeechLineageNode,
)
from agent.repositories.speech_reconciliation_audit_stager import SpeechReconciliationAuditStager
from agent.repositories.speech_reconciliation_budget_plan import validate_budget_plan
from agent.repositories.speech_reconciliation_locking import sqlite_write_guard
from agent.repositories.speech_reconciliation_records import (
    SpeechReconciliationJobCreate,
    SpeechReconciliationJobRecord,
    SpeechReconciliationRepositoryError,
    job_record,
    resolve_now_ms,
)


class SpeechReconciliationJobAdmission:
    """Admits idempotent jobs with lineage and audit in one transaction."""

    def __init__(
        self,
        *,
        lineage: SpeechEvidenceLineageRepository,
        audit: SpeechReconciliationAuditStager,
    ) -> None:
        self._lineage = lineage
        self._audit = audit


    def create_job(
        self, spec: SpeechReconciliationJobCreate, *, now_ms: int | None = None
    ) -> tuple[SpeechReconciliationJobRecord, bool]:
        # See sqlite_write_guard for the single-connection SQLite rationale;
        # database uniqueness constraints remain the multi-Hub authority.
        guard = sqlite_write_guard()
        with guard:
            return self._create_job(spec, now_ms=now_ms)

    def _create_job(
        self, spec: SpeechReconciliationJobCreate, *, now_ms: int | None = None
    ) -> tuple[SpeechReconciliationJobRecord, bool]:
        now = resolve_now_ms(now_ms)
        audit_event = self._audit.prepare(
            tenant_id=spec.tenant_id,
            job_id=spec.job_id,
            idempotency_key=f"speech-reconciliation:create:{spec.job_id}",
            event_type="semantic_job",
            transition="created",
            reason_code="speech_reconciliation_admitted",
            epoch=max(1, spec.revocation_epoch + 1),
        )
        row = SpeechReconciliationJobDB(
            id=spec.job_id,
            tenant_id=spec.tenant_id,
            owner_subject=spec.owner_subject,
            pair_scope_digest=spec.pair_scope_digest,
            idempotency_key_digest=spec.idempotency_key_digest,
            request_digest=spec.request_digest,
            consent_id=spec.consent_id,
            consent_version=spec.consent_version,
            revocation_epoch=spec.revocation_epoch,
            input_manifest_digest=spec.input_manifest_digest,
            input_lineage_digest=spec.input_lineage_digest,
            input_artifact_ref=spec.input_artifact_ref,
            policy_digest=spec.policy_digest,
            research_policy_ref=spec.research_policy_ref,
            budget_plan=validate_budget_plan(spec.budget_plan, factor=spec.max_compute_factor),
            source_duration_ms=spec.source_duration_ms,
            max_compute_factor=spec.max_compute_factor,
            current_compute_factor=(
                spec.current_compute_factor
                if spec.current_compute_factor is not None
                else min(10, spec.max_compute_factor)
            ),
            training_budget=(dict(spec.training_budget) if spec.training_budget is not None else None),
            key_epoch=spec.key_epoch,
            deadline_at_ms=spec.deadline_at_ms,
            created_at_ms=now,
            updated_at_ms=now,
        )
        with Session(engine) as session:
            existing = session.exec(
                select(SpeechReconciliationJobDB).where(
                    SpeechReconciliationJobDB.tenant_id == spec.tenant_id,
                    SpeechReconciliationJobDB.owner_subject == spec.owner_subject,
                    SpeechReconciliationJobDB.idempotency_key_digest == spec.idempotency_key_digest,
                )
            ).first()
            if existing is not None:
                if existing.request_digest != spec.request_digest:
                    raise SpeechReconciliationRepositoryError("speech_reconciliation_idempotency_conflict")
                return job_record(existing), False
            session.add(row)
            try:
                outbox = self._lineage.stage(
                    session,
                    tenant_id=spec.tenant_id,
                    owner_subject=spec.owner_subject,
                    nodes=(
                        SpeechLineageNode(
                            "manifest",
                            spec.input_manifest_digest,
                            consent_id=spec.consent_id,
                            revocation_epoch=spec.revocation_epoch,
                        ),
                        SpeechLineageNode(
                            "reconciliation",
                            spec.request_digest,
                            consent_id=spec.consent_id,
                            revocation_epoch=spec.revocation_epoch,
                        ),
                    ),
                    edges=(
                        SpeechLineageEdge(
                            "manifest",
                            spec.input_manifest_digest,
                            "reconciliation",
                            spec.request_digest,
                            "input_to",
                        ),
                    ),
                    now_ms=now,
                )
                self._audit.enqueue(session, audit_event)
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                winner = session.exec(
                    select(SpeechReconciliationJobDB).where(
                        SpeechReconciliationJobDB.tenant_id == spec.tenant_id,
                        SpeechReconciliationJobDB.owner_subject == spec.owner_subject,
                        SpeechReconciliationJobDB.idempotency_key_digest == spec.idempotency_key_digest,
                    )
                ).first()
                if winner is not None and winner.request_digest == spec.request_digest:
                    return job_record(winner), False
                raise SpeechReconciliationRepositoryError("speech_reconciliation_write_conflict") from exc
            session.refresh(row)
        self._lineage.process_outbox(event_digest=outbox, tenant_id=spec.tenant_id, owner_subject=spec.owner_subject)
        return job_record(row), True

    def get_job(self, *, tenant_id: str, owner_subject: str, job_id: str) -> SpeechReconciliationJobRecord | None:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechReconciliationJobDB).where(
                    SpeechReconciliationJobDB.id == job_id,
                    SpeechReconciliationJobDB.tenant_id == tenant_id,
                    SpeechReconciliationJobDB.owner_subject == owner_subject,
                )
            ).first()
            return job_record(row) if row is not None else None

    def list_jobs(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[SpeechReconciliationJobRecord, ...]:
        if not 0 <= offset <= 1_000_000 or not 1 <= limit <= 200:
            raise SpeechReconciliationRepositoryError("speech_reconciliation_pagination_invalid", status_code=422)
        with Session(engine) as session:
            rows = session.exec(
                select(SpeechReconciliationJobDB)
                .where(
                    SpeechReconciliationJobDB.tenant_id == tenant_id,
                    SpeechReconciliationJobDB.owner_subject == owner_subject,
                )
                .order_by(SpeechReconciliationJobDB.created_at_ms.desc(), SpeechReconciliationJobDB.id)
                .offset(offset)
                .limit(limit)
            ).all()
            return tuple(job_record(row) for row in rows)


__all__ = ["SpeechReconciliationJobAdmission"]
