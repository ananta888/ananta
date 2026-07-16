"""Project LoRA attempts into Ananta's generic Hub-owned WorkerJob ledger."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class MlInternTrainingWorkerJobProjectionPort(Protocol):
    def claim(
        self,
        *,
        task_id: str,
        job_id: str,
        attempt_id: str,
        worker_id: str,
        worker_ref: str,
        backend: str,
        gpu_profile: str,
        tenant_scope_digest: str,
    ) -> str: ...

    def finish(
        self,
        *,
        worker_job_id: str,
        task_id: str,
        worker_ref: str,
        status: str,
        reason_code: str | None,
    ) -> None: ...


@dataclass(frozen=True)
class HubWorkerJobProjection:
    """Small adapter around the existing generic worker-job service."""

    def claim(
        self,
        *,
        task_id: str,
        job_id: str,
        attempt_id: str,
        worker_id: str,
        worker_ref: str,
        backend: str,
        gpu_profile: str,
        tenant_scope_digest: str,
    ) -> str:
        from agent.services.worker_job_service import get_worker_job_service

        service = get_worker_job_service()
        worker_job = service.create_worker_job(
            parent_task_id=task_id,
            subtask_id=job_id,
            worker_url=worker_ref,
            context_bundle_id=None,
            allowed_tools=[],
            expected_output_schema={
                "type": "object",
                "required": ["status", "artifacts"],
                "properties": {
                    "status": {"type": "string"},
                    "artifacts": {"type": "array"},
                },
            },
            metadata={
                "domain": "ml_intern_training",
                "job_id": job_id,
                "attempt_id": attempt_id,
                "backend": backend,
                "gpu_profile": gpu_profile,
                "tenant_scope_digest": tenant_scope_digest,
            },
            selection_decision={
                "selected_worker_id": worker_id,
                "selected_worker_kind": "lora_training",
                "selected_runtime_target_id": worker_id,
                "selected_runtime_kind": backend,
                "selection_mode": "capability_matched",
                "policy_decision_ref": f"ml-intern-training:{attempt_id}",
            },
            scheduling_decision={
                "status": "active",
                "reason_code": "lora_training_capacity_admitted",
            },
        )
        service.record_worker_result(
            worker_job_id=worker_job.id,
            task_id=task_id,
            worker_url=worker_ref,
            status="running",
            output=None,
            metadata={"reason_code": "worker_attempt_claimed", "attempt_id": attempt_id},
        )
        return worker_job.id

    def finish(
        self,
        *,
        worker_job_id: str,
        task_id: str,
        worker_ref: str,
        status: str,
        reason_code: str | None,
    ) -> None:
        from agent.services.worker_job_service import get_worker_job_service

        projected_status = status if status in {"completed", "failed", "cancelled"} else "failed"
        get_worker_job_service().record_worker_result(
            worker_job_id=worker_job_id,
            task_id=task_id,
            worker_url=worker_ref,
            status=projected_status,
            output=None,
            metadata={"domain_status": status, "reason_code": reason_code},
        )


_projection = HubWorkerJobProjection()


def get_ml_intern_training_worker_job_projection() -> MlInternTrainingWorkerJobProjectionPort:
    return _projection


__all__ = [
    "HubWorkerJobProjection",
    "MlInternTrainingWorkerJobProjectionPort",
    "get_ml_intern_training_worker_job_projection",
]
