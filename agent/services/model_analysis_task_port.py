"""Hub-owned task projection for model-analysis jobs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

from ananta_contracts.model_intelligence import AnalysisJob


@dataclass(frozen=True, slots=True)
class ModelAnalysisTaskReference:
    parent_task_id: str
    execution_task_id: str
    status: str


class ModelAnalysisTaskQueuePort(Protocol):
    def ingest_task(self, **values: Any) -> None: ...


class ModelAnalysisTaskStatusPort(Protocol):
    def set_status(
        self,
        task_id: str,
        *,
        status: str,
        reason_code: str,
        event_type: str,
        details: dict[str, Any],
    ) -> None: ...


class ModelAnalysisTaskSubmissionPort(Protocol):
    def submit(self, job: AnalysisJob) -> ModelAnalysisTaskReference: ...

    def mark_running(self, job: AnalysisJob, *, worker_id: str) -> None: ...

    def mark_cancel_requested(self, job: AnalysisJob, *, reason_code: str) -> None: ...

    def finish(self, job: AnalysisJob, *, status: str, reason_code: str) -> None: ...


class HubModelAnalysisTaskSubmissionPort:
    """Materializes one deterministic child task; only the Hub uses this port."""

    def __init__(
        self,
        *,
        queue: ModelAnalysisTaskQueuePort | None = None,
        statuses: ModelAnalysisTaskStatusPort | None = None,
    ) -> None:
        self._queue = queue
        self._statuses = statuses

    def submit(self, job: AnalysisJob) -> ModelAnalysisTaskReference:
        execution_task_id = self.execution_task_id(job.job_id)
        queue = self._queue
        if queue is None:
            from agent.services.task_queue_service import get_task_queue_service

            queue = get_task_queue_service()
        queue.ingest_task(
            task_id=execution_task_id,
            status="assigned",
            title=f"Model analysis: {job.analysis_kind}",
            description="Execute exactly one Hub-issued bounded model-analysis job.",
            priority="medium",
            created_by="hub",
            source="model_intelligence",
            tags=["model_intelligence", "hub_child", "bounded"],
            event_type="model_analysis_delegated",
            event_details={
                "analysis_kind": job.analysis_kind,
                "job_id": job.job_id,
            },
            extra_fields={
                "task_kind": "model_analysis",
                "parent_task_id": job.hub_task_id,
                "required_capabilities": [
                    "model_analysis",
                    job.analysis_kind,
                ],
                "worker_execution_context": {
                    "model_intelligence": {
                        "job": job.to_wire(),
                        "tenant_scope_hash": hashlib.sha256(
                            job.tenant_id.encode()
                        ).hexdigest(),
                        "persistence_owner": "hub",
                        "followup_task_creation_allowed": False,
                        "peer_transfer_allowed": False,
                    }
                },
            },
        )
        return ModelAnalysisTaskReference(
            parent_task_id=job.hub_task_id,
            execution_task_id=execution_task_id,
            status="assigned",
        )

    def mark_running(self, job: AnalysisJob, *, worker_id: str) -> None:
        self._set_status(
            job,
            status="in_progress",
            reason_code="model_analysis_worker_claimed",
            event_type="model_analysis_started",
            details={
                "worker_id_hash": hashlib.sha256(worker_id.encode()).hexdigest(),
            },
        )

    def mark_cancel_requested(self, job: AnalysisJob, *, reason_code: str) -> None:
        self._set_status(
            job,
            status="in_progress",
            reason_code=reason_code,
            event_type="model_analysis_cancel_requested",
            details={},
        )

    def finish(self, job: AnalysisJob, *, status: str, reason_code: str) -> None:
        task_status = {
            "succeeded": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
        }.get(status)
        if task_status is None:
            raise ValueError("model_analysis_task_terminal_status_invalid")
        self._set_status(
            job,
            status=task_status,
            reason_code=reason_code,
            event_type=f"model_analysis_{status}",
            details={},
        )

    @staticmethod
    def execution_task_id(job_id: str) -> str:
        digest = hashlib.sha256(job_id.encode()).hexdigest()
        return f"model-analysis-{digest[:32]}"

    def _set_status(
        self,
        job: AnalysisJob,
        *,
        status: str,
        reason_code: str,
        event_type: str,
        details: dict[str, Any],
    ) -> None:
        task_id = self.execution_task_id(job.job_id)
        if self._statuses is not None:
            self._statuses.set_status(
                task_id,
                status=status,
                reason_code=reason_code,
                event_type=event_type,
                details=dict(details),
            )
            return
        from agent.services.task_runtime_service import update_local_task_status

        update_local_task_status(
            task_id,
            status,
            status_reason_code=reason_code,
            status_reason_details={},
            event_type=event_type,
            event_actor="hub",
            event_details=dict(details),
        )


__all__ = [
    "HubModelAnalysisTaskSubmissionPort",
    "ModelAnalysisTaskQueuePort",
    "ModelAnalysisTaskReference",
    "ModelAnalysisTaskStatusPort",
    "ModelAnalysisTaskSubmissionPort",
]
