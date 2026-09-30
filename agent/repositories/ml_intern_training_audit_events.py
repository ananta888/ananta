"""Content-free semantic-media audit events for ML-Intern training state transitions.

The training repository owns the transactions; this collaborator only derives
the scope, lease reference and idempotency key of each audit event and asks the
audit port to prepare it (SRP). The audit port is resolved through an injected
callable so the repository keeps its configured/default audit semantics (DIP).
"""

from __future__ import annotations

import hashlib
from typing import Callable

from agent.db_models import (
    MlInternDatasetDB,
    MlInternTrainingAttemptDB,
    MlInternTrainingCapacityLeaseDB,
    MlInternTrainingEventDB,
    MlInternTrainingExecutionLeaseDB,
    MlInternTrainingJobDB,
)
from agent.models.semantic_media_audit import SemanticMediaAuditEvent
from agent.ports.semantic_media_audit import SemanticMediaAuditPort


class MlInternTrainingAuditEvents:
    """Prepare audit events for datasets, jobs, leases, attempts and training events."""

    def __init__(self, *, audit_resolver: Callable[[], SemanticMediaAuditPort | None]) -> None:
        self._audit_resolver = audit_resolver

    def dataset(
        self,
        dataset: MlInternDatasetDB,
        *,
        transition: str,
        reason_code: str,
        epoch: int,
    ) -> SemanticMediaAuditEvent | None:
        return self.prepare(
            tenant_id=dataset.tenant_id,
            scope=f"ml-training-dataset:{dataset.owner_subject}:{dataset.id}",
            event_type="speech_dataset",
            transition=transition,
            reason_code=reason_code,
            epoch=epoch,
            job_ref=dataset.id,
            idempotency_key=f"ml-training:{transition}:{dataset.id}:{epoch}",
        )

    def job(
        self,
        job: MlInternTrainingJobDB,
        *,
        transition: str,
        reason_code: str,
        epoch: int,
    ) -> SemanticMediaAuditEvent | None:
        return self.prepare(
            tenant_id=job.tenant_id,
            scope=f"ml-training-job:{job.owner_subject}:{job.id}",
            event_type="speech_training",
            transition=transition,
            reason_code=reason_code,
            epoch=epoch,
            job_ref=job.id,
            idempotency_key=f"ml-training:{transition}:{job.id}:{epoch}",
        )

    def capacity(
        self,
        job: MlInternTrainingJobDB,
        lease: MlInternTrainingCapacityLeaseDB,
        *,
        transition: str,
        reason_code: str,
        epoch: int,
    ) -> SemanticMediaAuditEvent | None:
        return self.prepare(
            tenant_id=job.tenant_id,
            scope=f"ml-training-job:{job.owner_subject}:{job.id}",
            event_type="speech_training",
            transition=transition,
            reason_code=reason_code,
            epoch=epoch,
            job_ref=job.id,
            lease_ref=f"training-capacity:{lease.slot}:{job.id}",
            idempotency_key=f"ml-training:{transition}:{job.id}:{lease.slot}:{epoch}",
        )

    def execution(
        self,
        job: MlInternTrainingJobDB,
        lease: MlInternTrainingExecutionLeaseDB,
        *,
        transition: str,
        reason_code: str,
        epoch: int,
    ) -> SemanticMediaAuditEvent | None:
        # Execution leases are deliberately deleted and may later be acquired
        # again in the same numeric slot.  Version therefore is only an epoch
        # within one lease generation and cannot by itself identify the
        # authority transition.  Bind the audit idempotency key to the stable
        # creation value so replays of one lease collapse while reacquisitions
        # remain distinct.
        generation = hashlib.sha256(
            f"{lease.job_id}:{lease.slot}:{float(lease.created_at).hex()}".encode()
        ).hexdigest()[:24]
        return self.prepare(
            tenant_id=job.tenant_id,
            scope=f"ml-training-job:{job.owner_subject}:{job.id}",
            event_type="speech_training",
            transition=transition,
            reason_code=reason_code,
            epoch=epoch,
            job_ref=job.id,
            lease_ref=f"training-execution:{lease.slot}:{job.id}",
            idempotency_key=(
                f"ml-training:{transition}:{job.id}:{lease.slot}:{generation}:{epoch}"
            ),
        )

    def attempt(
        self,
        attempt: MlInternTrainingAttemptDB,
        *,
        transition: str,
        reason_code: str,
        epoch: int,
    ) -> SemanticMediaAuditEvent | None:
        return self.prepare(
            tenant_id=attempt.tenant_id,
            scope=f"ml-training-job:{attempt.owner_subject}:{attempt.job_id}",
            event_type="speech_training",
            transition=transition,
            reason_code=reason_code,
            epoch=epoch,
            job_ref=attempt.id,
            idempotency_key=f"ml-training:{transition}:{attempt.id}:{epoch}",
        )

    def training_event(
        self,
        job: MlInternTrainingJobDB,
        event: MlInternTrainingEventDB,
    ) -> SemanticMediaAuditEvent | None:
        dedupe_digest = hashlib.sha256(event.dedupe_key.encode("utf-8")).hexdigest()
        return self.prepare(
            tenant_id=job.tenant_id,
            scope=f"ml-training-job:{job.owner_subject}:{job.id}",
            event_type="speech_training",
            transition="event_appended",
            reason_code="training_event_appended",
            epoch=event.sequence,
            job_ref=event.id,
            idempotency_key=f"ml-training:event:{job.id}:{dedupe_digest}",
        )

    def prepare(
        self,
        *,
        tenant_id: str,
        scope: str,
        event_type: str,
        transition: str,
        reason_code: str,
        epoch: int,
        idempotency_key: str,
        job_ref: str | None = None,
        lease_ref: str | None = None,
    ) -> SemanticMediaAuditEvent | None:
        audit = self._audit_resolver()
        if audit is None:
            return None
        return audit.prepare_transition(
            idempotency_key=idempotency_key,
            tenant_id=tenant_id,
            scope=scope,
            event_type=event_type,
            transition=transition,
            reason_code=reason_code,
            epoch=max(1, int(epoch)),
            job_ref=job_ref,
            lease_ref=lease_ref,
        )


__all__ = ["MlInternTrainingAuditEvents"]
