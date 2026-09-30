"""SQL adapters for the Hub-owned speech-adaptation control plane."""

from __future__ import annotations

import time

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select, update

from agent.database import engine
from agent.db_models.speech_adaptation import SpeechAdaptationJobDB
from agent.models.semantic_media_audit import SemanticMediaAuditEvent
from agent.models.speech_adaptation_admission import (
    SpeechAdaptationDecisionConflict,
    SpeechAdmissionDecision,
    SpeechPrincipal,
    restore_speech_adaptation_job,
)
from agent.ports.semantic_media_audit import SemanticMediaAuditPort
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.repositories.speech_adaptation_artifacts import SqlSpeechAdaptationArtifactRepository
from agent.repositories.speech_adaptation_capacity import SqlSpeechAdaptationCapacityLeasePort
from agent.repositories.speech_adaptation_storage import SPEECH_ADAPTATION_WRITE_LOCK as _WRITE_LOCK
from ananta_contracts.speech_adaptation import SpeechAdaptationResult

_TERMINAL = frozenset({"completed", "dataset_only", "cancelled", "failed", "denied"})
_ACTIVE = frozenset({"queued", "dispatching", "submitted", "running", "cancel_requested"})


class SqlSpeechAdaptationDecisionStore:
    """Tenant-scoped, CAS-protected admission and worker state."""

    def __init__(self, *, audit: SemanticMediaAuditPort | None = None) -> None:
        self._audit = audit

    @property
    def transactional_audit(self) -> bool:
        return self._audit is not None

    def _audit_event(
        self,
        *,
        tenant_id: str,
        job_id: str,
        version: int,
        status: str,
        reason_code: str,
        contract_payload: dict | None,
    ) -> SemanticMediaAuditEvent | None:
        if self._audit is None:
            return None
        epoch = 1
        lease_ref = None
        if contract_payload:
            job = restore_speech_adaptation_job(contract_payload)
            epoch = max(1, job.fencing.epoch)
            lease_ref = job.fencing.lease_id
        return self._audit.prepare_transition(
            idempotency_key=f"speech-training:{job_id}:{version}:{status}:{reason_code}",
            tenant_id=tenant_id,
            scope=f"speech-job:{job_id}",
            event_type="speech_training",
            transition=status,
            reason_code=reason_code,
            epoch=epoch,
            lease_ref=lease_ref,
            job_ref=job_id,
        )

    def by_idempotency(
        self,
        principal: SpeechPrincipal,
        idempotency_digest: str,
    ) -> SpeechAdmissionDecision | None:
        with Session(engine) as session:
            row = session.exec(
                self._scope(principal).where(SpeechAdaptationJobDB.idempotency_digest == idempotency_digest)
            ).first()
            return _decision(row) if row is not None else None

    def create(
        self,
        principal: SpeechPrincipal,
        *,
        idempotency_digest: str,
        decision: SpeechAdmissionDecision,
    ) -> tuple[SpeechAdmissionDecision, bool]:
        row = SpeechAdaptationJobDB(
            id=decision.job_id,
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            task_id=decision.task_id,
            idempotency_digest=idempotency_digest,
            request_digest=decision.request_digest,
            status=decision.status,
            reason_code=decision.reason_code,
            admission_request_payload=dict(decision.admission_request or {}),
            contract_payload=decision.job.to_dict() if decision.job is not None else {},
            terminal_at_ms=(time.time_ns() // 1_000_000 if decision.status in _TERMINAL else None),
        )
        with _WRITE_LOCK:
            with Session(engine) as session:
                existing = session.exec(
                    self._scope(principal).where(SpeechAdaptationJobDB.idempotency_digest == idempotency_digest)
                ).first()
                if existing is not None:
                    return self._replay(existing, decision), True
                session.add(row)
                audit_event = self._audit_event(
                    tenant_id=principal.tenant_id,
                    job_id=row.id,
                    version=row.version,
                    status=row.status,
                    reason_code=row.reason_code,
                    contract_payload=dict(row.contract_payload or {}),
                )
                try:
                    if audit_event is not None:
                        SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
                    session.commit()
                    session.refresh(row)
                    return _decision(row), False
                except IntegrityError:
                    session.rollback()
                    existing = session.exec(
                        self._scope(principal).where(SpeechAdaptationJobDB.idempotency_digest == idempotency_digest)
                    ).first()
                    if existing is None:
                        raise
                    return self._replay(existing, decision), True

    def get(self, principal: SpeechPrincipal, job_id: str) -> SpeechAdmissionDecision | None:
        with Session(engine) as session:
            row = session.exec(self._scope(principal).where(SpeechAdaptationJobDB.id == job_id)).first()
            return _decision(row) if row is not None else None

    def waiting_admission(
        self,
        principal: SpeechPrincipal,
        job_id: str,
    ) -> tuple[str, dict] | None:
        with Session(engine) as session:
            row = session.exec(
                self._scope(principal).where(
                    SpeechAdaptationJobDB.id == job_id,
                    SpeechAdaptationJobDB.status == "queued",
                )
            ).first()
            if row is None or dict(row.contract_payload or {}) or not dict(row.admission_request_payload or {}):
                return None
            return row.idempotency_digest, dict(row.admission_request_payload)

    def get_row(self, job_id: str) -> SpeechAdaptationJobDB | None:
        with Session(engine) as session:
            row = session.get(SpeechAdaptationJobDB, job_id)
            if row is not None:
                session.expunge(row)
            return row

    def replace(
        self,
        principal: SpeechPrincipal,
        decision: SpeechAdmissionDecision,
        *,
        expected_statuses: frozenset[str],
        result: SpeechAdaptationResult | None = None,
    ) -> SpeechAdmissionDecision:
        with _WRITE_LOCK:
            current = self.get_row(decision.job_id)
            if (
                current is None
                or current.tenant_id != principal.tenant_id
                or current.owner_subject != principal.subject
            ):
                raise SpeechAdaptationDecisionConflict("speech_job_not_found")
            expected_contract = decision.job.to_dict() if decision.job is not None else {}
            expected_request = dict(decision.admission_request or {})
            expected_result = result.to_dict() if result is not None else None
            if (
                current.status == decision.status
                and current.reason_code == decision.reason_code
                and dict(current.contract_payload or {}) == expected_contract
                and dict(current.admission_request_payload or {}) == expected_request
                and (expected_result is None or dict(current.result_payload or {}) == expected_result)
            ):
                return _decision(current)
            if current.status not in expected_statuses:
                raise SpeechAdaptationDecisionConflict("speech_job_state_conflict")
            values: dict[str, object] = {
                "status": decision.status,
                "reason_code": decision.reason_code,
                "task_id": decision.task_id,
                "admission_request_payload": expected_request,
                "contract_payload": expected_contract,
                "version": current.version + 1,
                "updated_at_ms": time.time_ns() // 1_000_000,
            }
            if decision.status in _TERMINAL:
                values["terminal_at_ms"] = time.time_ns() // 1_000_000
            if result is not None:
                values["result_payload"] = expected_result
            with Session(engine) as session:
                changed = session.exec(
                    update(SpeechAdaptationJobDB)
                    .where(
                        SpeechAdaptationJobDB.id == current.id,
                        SpeechAdaptationJobDB.version == current.version,
                        SpeechAdaptationJobDB.status == current.status,
                    )
                    .values(**values)
                )
                if changed.rowcount != 1:
                    session.rollback()
                    raise SpeechAdaptationDecisionConflict("speech_job_state_conflict")
                audit_event = self._audit_event(
                    tenant_id=principal.tenant_id,
                    job_id=current.id,
                    version=current.version + 1,
                    status=decision.status,
                    reason_code=decision.reason_code,
                    contract_payload=expected_contract,
                )
                if audit_event is not None:
                    SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
                session.commit()
            saved = self.get(principal, decision.job_id)
            if saved is None:
                raise SpeechAdaptationDecisionConflict("speech_job_not_found")
            return saved

    def list_dispatchable(self, *, now_ms: int, limit: int) -> tuple[SpeechAdaptationJobDB, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("speech_adaptation_dispatch_limit_invalid")
        with Session(engine) as session:
            rows = session.exec(
                select(SpeechAdaptationJobDB)
                .where(
                    SpeechAdaptationJobDB.status.in_(_ACTIVE),
                    SpeechAdaptationJobDB.next_dispatch_at_ms <= now_ms,
                )
                .order_by(
                    SpeechAdaptationJobDB.created_at_ms.asc(),
                    SpeechAdaptationJobDB.id.asc(),
                )
                .limit(limit)
            ).all()
            for row in rows:
                session.expunge(row)
            return tuple(rows)

    def list_active(self, *, limit: int) -> tuple[SpeechAdaptationJobDB, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("speech_adaptation_active_limit_invalid")
        with Session(engine) as session:
            rows = session.exec(
                select(SpeechAdaptationJobDB)
                .where(SpeechAdaptationJobDB.status.in_(_ACTIVE))
                .order_by(
                    SpeechAdaptationJobDB.updated_at_ms.asc(),
                    SpeechAdaptationJobDB.id.asc(),
                )
                .limit(limit)
            ).all()
            for row in rows:
                session.expunge(row)
            return tuple(rows)

    def transition_worker_state(
        self,
        job_id: str,
        *,
        expected_statuses: frozenset[str],
        status: str,
        reason_code: str,
        worker_status: str | None = None,
        result: SpeechAdaptationResult | None = None,
        retry_delay_ms: int = 0,
        increment_dispatch_attempts: bool = False,
        now_ms: int | None = None,
    ) -> SpeechAdaptationJobDB:
        """``now_ms``: the caller's clock (the dispatcher lists due jobs with the same clock it schedules
        ``next_dispatch_at_ms`` with); default wall-clock time."""
        if status not in _ACTIVE | _TERMINAL:
            raise ValueError("speech_adaptation_status_invalid")
        with _WRITE_LOCK:
            current = self.get_row(job_id)
            if current is None:
                raise SpeechAdaptationDecisionConflict("speech_job_not_found")
            if current.status not in expected_statuses:
                if current.status == status:
                    return current
                raise SpeechAdaptationDecisionConflict("speech_job_state_conflict")
            now = int(now_ms) if now_ms is not None else time.time_ns() // 1_000_000
            values: dict[str, object] = {
                "status": status,
                "reason_code": reason_code,
                "worker_status": worker_status,
                "next_dispatch_at_ms": now + max(0, retry_delay_ms),
                "dispatch_attempts": current.dispatch_attempts + (1 if increment_dispatch_attempts else 0),
                "version": current.version + 1,
                "updated_at_ms": now,
            }
            if result is not None:
                values["result_payload"] = result.to_dict()
            if status in _TERMINAL:
                values["terminal_at_ms"] = now
            with Session(engine) as session:
                changed = session.exec(
                    update(SpeechAdaptationJobDB)
                    .where(
                        SpeechAdaptationJobDB.id == current.id,
                        SpeechAdaptationJobDB.version == current.version,
                        SpeechAdaptationJobDB.status == current.status,
                    )
                    .values(**values)
                )
                if changed.rowcount != 1:
                    session.rollback()
                    raise SpeechAdaptationDecisionConflict("speech_job_state_conflict")
                audit_event = self._audit_event(
                    tenant_id=current.tenant_id,
                    job_id=current.id,
                    version=current.version + 1,
                    status=status,
                    reason_code=reason_code,
                    contract_payload=dict(current.contract_payload or {}),
                )
                if audit_event is not None:
                    SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
                session.commit()
            saved = self.get_row(job_id)
            if saved is None:
                raise SpeechAdaptationDecisionConflict("speech_job_not_found")
            return saved

    @staticmethod
    def _scope(principal: SpeechPrincipal):
        return select(SpeechAdaptationJobDB).where(
            SpeechAdaptationJobDB.tenant_id == principal.tenant_id,
            SpeechAdaptationJobDB.owner_subject == principal.subject,
        )

    @staticmethod
    def _replay(
        existing: SpeechAdaptationJobDB,
        decision: SpeechAdmissionDecision,
    ) -> SpeechAdmissionDecision:
        if existing.request_digest != decision.request_digest:
            raise SpeechAdaptationDecisionConflict("speech_idempotency_conflict")
        expected_contract = decision.job.to_dict() if decision.job is not None else {}
        if dict(existing.contract_payload or {}) != expected_contract:
            raise SpeechAdaptationDecisionConflict("speech_idempotency_binding_conflict")
        if dict(existing.admission_request_payload or {}) != dict(decision.admission_request or {}):
            raise SpeechAdaptationDecisionConflict("speech_idempotency_request_binding_conflict")
        return _decision(existing)


def _decision(row: SpeechAdaptationJobDB) -> SpeechAdmissionDecision:
    payload = dict(row.contract_payload or {})
    result_payload = dict(row.result_payload or {})
    return SpeechAdmissionDecision(
        job_id=row.id,
        task_id=row.task_id,
        status=row.status,
        reason_code=row.reason_code,
        job=restore_speech_adaptation_job(payload) if payload else None,
        request_digest=row.request_digest,
        admission_request=dict(row.admission_request_payload or {}) or None,
        result=(SpeechAdaptationResult.from_mapping(result_payload) if result_payload else None),
    )


__all__ = [
    "SqlSpeechAdaptationArtifactRepository",
    "SqlSpeechAdaptationCapacityLeasePort",
    "SqlSpeechAdaptationDecisionStore",
]
