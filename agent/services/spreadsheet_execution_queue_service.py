"""Hub application service for queue-backed spreadsheet execution."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from agent.services.spreadsheet_execution_queue_ports import (
    SpreadsheetExecutionQueuePort,
    SpreadsheetWorkerJobLedgerPort,
    SpreadsheetWorkerLeasePort,
)
from agent.services.spreadsheet_observability_service import (
    SpreadsheetCorrelation,
    SpreadsheetObservabilityService,
)
from agent.services.spreadsheet_saga_service import SpreadsheetSagaService


class SpreadsheetExecutionQueueService:
    """Validates in the Hub, then delegates one immutable assignment to a Worker."""

    def __init__(
        self,
        *,
        saga: SpreadsheetSagaService,
        queue: SpreadsheetExecutionQueuePort,
        worker_jobs: SpreadsheetWorkerJobLedgerPort,
        leases: SpreadsheetWorkerLeasePort,
        worker_id: str,
        observability: SpreadsheetObservabilityService | None = None,
    ) -> None:
        normalized_worker_id = str(worker_id or "").strip()
        if not normalized_worker_id:
            raise ValueError("spreadsheet_worker_id_invalid")
        self._saga = saga
        self._queue = queue
        self._worker_jobs = worker_jobs
        self._leases = leases
        self._worker_id = normalized_worker_id
        self._observability = observability

    def execute_proposal(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        proposal: Mapping[str, Any],
    ) -> dict[str, Any]:
        started = time.monotonic()
        assignment = self._saga.prepare_proposal_execution(
            tenant_id=tenant_id,
            principal_id=principal_id,
            proposal=proposal,
        )
        completed = assignment.get("completed_result")
        if isinstance(completed, Mapping):
            return dict(completed)
        queued, created = self._queue.enqueue(assignment)
        if not created:
            self._record(
                assignment=assignment,
                job=queued,
                outcome="replayed",
                reason_code="spreadsheet_proposal_replayed",
                duration_seconds=time.monotonic() - started,
            )
            return {**queued, "replayed": True}
        binding = self._worker_jobs.create(
            queue_job_id=queued["job_id"],
            proposal_id=queued["proposal_id"],
            assignment_digest=queued["assignment_digest"],
            worker_id=self._worker_id,
        )
        decision = self._leases.acquire(
            worker_job_id=binding.worker_job_id,
            queue_job_id=queued["job_id"],
            worker_id=self._worker_id,
            assignment_digest=queued["assignment_digest"],
        )
        if decision.status not in {"active", "queued"} or not decision.slot_lease_id:
            self._worker_jobs.bind_lease(
                worker_job_id=binding.worker_job_id,
                slot_lease_id=decision.slot_lease_id or "lease-rejected",
                status="rejected",
                queue_position=None,
                reason_code=decision.reason_code,
            )
            failed = self._queue.fail_dispatch(
                tenant_id=tenant_id,
                job_id=queued["job_id"],
                reason_code=decision.reason_code,
            )
            self._record(
                assignment=assignment,
                job={**failed, "worker_job_id": binding.worker_job_id},
                outcome="rejected",
                reason_code=decision.reason_code,
                duration_seconds=time.monotonic() - started,
            )
            return failed
        queue_status = "leased" if decision.status == "active" else "queued"
        self._worker_jobs.bind_lease(
            worker_job_id=binding.worker_job_id,
            slot_lease_id=decision.slot_lease_id,
            status=decision.status,
            queue_position=decision.queue_position,
            reason_code=decision.reason_code,
        )
        bound = self._queue.bind_dispatch(
            tenant_id=tenant_id,
            job_id=queued["job_id"],
            worker_job_id=binding.worker_job_id,
            slot_lease_id=decision.slot_lease_id,
            worker_id=self._worker_id,
            status=queue_status,
            queue_position=decision.queue_position,
        )
        self._record(
            assignment=assignment,
            job=bound,
            outcome="accepted",
            reason_code=decision.reason_code,
            duration_seconds=time.monotonic() - started,
        )
        return {**bound, "replayed": False}

    def get_job(self, *, tenant_id: str, principal_id: str, job_id: str) -> dict[str, Any]:
        """Return an owner-authorized Hub projection of one delegated execution job."""

        job = self._queue.get(tenant_id=tenant_id, job_id=job_id)
        self._saga.get_document(
            tenant_id=tenant_id,
            document_id=str(job["document_id"]),
            principal_id=principal_id,
        )
        return job

    def _record(
        self,
        *,
        assignment: Mapping[str, Any],
        job: Mapping[str, Any],
        outcome: str,
        reason_code: str,
        duration_seconds: float,
    ) -> None:
        if self._observability is None:
            return
        proposal = dict(assignment.get("proposal") or {})
        try:
            self._observability.record(
                operation="proposal",
                outcome=outcome,
                reason_code=reason_code,
                correlation=SpreadsheetCorrelation(
                    task_id=str(job["job_id"]),
                    worker_job_id=str(job.get("worker_job_id") or job["job_id"]),
                    document_id=str(proposal["document_id"]),
                    candidate_id=str(proposal["proposal_id"]),
                ),
                duration_seconds=duration_seconds,
            )
        except (RuntimeError, ValueError):
            # Telemetry is deliberately non-authoritative and cannot alter queue semantics.
            return


__all__ = ["SpreadsheetExecutionQueueService"]
