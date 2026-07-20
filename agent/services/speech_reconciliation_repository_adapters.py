"""Concrete Hub adapters from scheduler leases to persistent repository CAS."""

from __future__ import annotations

import secrets
import time
from dataclasses import replace
from typing import Callable

from agent.repositories.speech_reconciliation import (
    SpeechReconciliationRepository,
    SqlSpeechReconciliationBudgetRepository,
)
from agent.services.semantic_media_audit_service import SemanticMediaAuditPort
from agent.services.speech_reconciliation_budget_ledger_service import (
    SpeechReconciliationBudgetLedgerService,
)
from agent.services.speech_reconciliation_scheduler import (
    QueuedSpeechReconciliation,
    SpeechReconciliationLease,
    SpeechReconciliationWorkerCandidate,
)
from ananta_contracts.speech_reconciliation import SpeechResourceVector, canonical_sha256


class RepositorySpeechReconciliationLeasePort:
    def __init__(
        self,
        repository: SpeechReconciliationRepository,
        *,
        clock_ms: Callable[[], int] | None = None,
        audit: SemanticMediaAuditPort | None = None,
    ) -> None:
        self._repository = repository
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._audit = audit

    def acquire(
        self,
        queued: QueuedSpeechReconciliation,
        candidate: SpeechReconciliationWorkerCandidate,
        *,
        ttl_ms: int,
    ) -> SpeechReconciliationLease:
        now = self._clock_ms()
        if not 5_000 <= ttl_ms <= 300_000:
            raise ValueError("speech_reconciliation_lease_ttl_invalid")
        current = self._repository.get_job(
            tenant_id=queued.tenant_id,
            owner_subject=queued.owner_subject,
            job_id=queued.job.job_id,
        )
        if current is None:
            raise ValueError("speech_reconciliation_job_not_found")
        token = secrets.token_bytes(32)
        token_digest = canonical_sha256({"token": token.hex(), "job_id": current.id})
        attempt = self._repository.claim_attempt(
            tenant_id=queued.tenant_id,
            owner_subject=queued.owner_subject,
            job_id=current.id,
            expected_job_version=current.version,
            worker_id_digest=canonical_sha256({"worker_id": candidate.worker_id}),
            worker_capability_digest=canonical_sha256(sorted(candidate.capabilities)),
            location_digest=canonical_sha256({"location": candidate.location}),
            resource_profile_digest=canonical_sha256(queued.requested_resources.to_dict()),
            fencing_token_digest=token_digest,
            lease_expires_at_ms=min(now + ttl_ms, current.deadline_at_ms),
            now_ms=now,
        )
        budget_repository = SqlSpeechReconciliationBudgetRepository(
            tenant_id=queued.tenant_id,
            owner_subject=queued.owner_subject,
            audit=(self._audit if bool(getattr(self._repository, "transactional_audit", False)) else None),
        )
        budget_service = SpeechReconciliationBudgetLedgerService(
            budget_repository,
            tenant_id=queued.tenant_id,
            audit=self._audit,
        )
        try:
            ledger = budget_repository.get(job_id=current.id)
            if ledger is None:
                plan = dict(current.budget_plan or {})
                allocated = SpeechResourceVector.from_mapping(plan.get("allocated"), "budget_plan.allocated")
                ledger = budget_service.create(
                    job_id=current.id,
                    attempt_id=attempt.id,
                    fencing_epoch=attempt.fencing_epoch,
                    stage="staging",
                    source_duration_ms=current.source_duration_ms,
                    compute_factor=current.max_compute_factor,
                    allocated=allocated,
                )
            elif ledger.attempt_id != attempt.id or ledger.fencing_epoch != attempt.fencing_epoch:
                ledger = budget_service.rebind_attempt(
                    job_id=current.id,
                    expected_sequence=ledger.sequence,
                    attempt_id=attempt.id,
                    fencing_epoch=attempt.fencing_epoch,
                    stage="staging",
                )
        except Exception:
            self._repository.fence_attempt(
                attempt.id,
                reason_code="speech_reconciliation_budget_binding_failed",
                authority="hub",
                now_ms=now,
            )
            raise
        job = replace(
            queued.job,
            attempt_id=attempt.id,
            fencing_token_digest=attempt.fencing_token_digest,
            fencing_epoch=attempt.fencing_epoch,
            deadline_at_ms=current.deadline_at_ms,
            ledger_sequence=ledger.sequence,
            stage="staging",
        )
        if self._audit is not None and not bool(getattr(self._repository, "transactional_audit", False)):
            try:
                lease_event = self._audit.prepare_transition(
                    idempotency_key=f"speech-reconciliation-lease:{attempt.id}:{attempt.fencing_epoch}",
                    tenant_id=queued.tenant_id,
                    scope=f"speech-job:{current.id}",
                    event_type="semantic_lease",
                    transition="acquired",
                    reason_code="speech_reconciliation_worker_assigned",
                    epoch=attempt.fencing_epoch,
                    lease_ref=attempt.id,
                    job_ref=current.id,
                )
                job_event = self._audit.prepare_transition(
                    idempotency_key=f"speech-reconciliation-job:running:{current.id}:{attempt.fencing_epoch}",
                    tenant_id=queued.tenant_id,
                    scope=f"speech-job:{current.id}",
                    event_type="semantic_job",
                    transition="running",
                    reason_code="speech_reconciliation_attempt_claimed",
                    epoch=attempt.fencing_epoch,
                    lease_ref=attempt.id,
                    job_ref=current.id,
                )
                self._audit.append_prepared(lease_event)
                self._audit.append_prepared(job_event)
            except Exception as exc:
                self._repository.fence_attempt(
                    attempt.id,
                    reason_code="semantic_audit_unavailable",
                    authority="hub",
                    now_ms=now,
                )
                raise ValueError("semantic_audit_unavailable") from exc
        return SpeechReconciliationLease(attempt.id, job, candidate.worker_id, attempt.lease_expires_at_ms)

    def revoke(self, lease_id: str, *, reason_code: str) -> None:
        fenced = self._repository.fence_attempt(
            lease_id,
            reason_code=reason_code,
            authority="hub",
            now_ms=self._clock_ms(),
        )
        binding = self._repository.attempt_audit_binding(lease_id) if fenced else None
        if (
            self._audit is None
            or binding is None
            or bool(getattr(self._repository, "transactional_audit", False))
        ):
            return
        tenant_id, job_id, epoch, persisted_reason = binding
        try:
            lease_event = self._audit.prepare_transition(
                idempotency_key=f"speech-reconciliation-lease:fenced:{lease_id}:{epoch}",
                tenant_id=tenant_id,
                scope=f"speech-job:{job_id}",
                event_type="semantic_lease",
                transition="fenced",
                reason_code=persisted_reason,
                epoch=epoch,
                lease_ref=lease_id,
                job_ref=job_id,
            )
            job_event = self._audit.prepare_transition(
                idempotency_key=f"speech-reconciliation-job:queued:{job_id}:{epoch}:{persisted_reason}",
                tenant_id=tenant_id,
                scope=f"speech-job:{job_id}",
                event_type="semantic_job",
                transition="queued",
                reason_code=persisted_reason,
                epoch=epoch,
                lease_ref=lease_id,
                job_ref=job_id,
            )
            self._audit.append_prepared(lease_event)
            self._audit.append_prepared(job_event)
        except Exception as exc:
            raise ValueError("semantic_audit_unavailable") from exc


__all__ = ["RepositorySpeechReconciliationLeasePort"]
