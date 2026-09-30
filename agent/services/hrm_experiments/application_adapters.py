"""Hub adapters of the HRM experiment application service.

Task-queue ingestion/cancellation, the WorkerJob/slot-lease execution binding
resolved from authoritative Hub state, and the owner-scoped admission
repository. Workers never see these adapters; the Hub owns the task queue.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Mapping

from agent.repositories.hrm_experiments import HrmExperimentRepository
from agent.repositories.worker_slot_lease import WorkerSlotLeaseRepository
from agent.services.hrm_experiments.admission import HrmAdmissionRepositoryPort
from agent.services.hrm_experiments.application_contracts import (
    HrmApplicationError,
    HrmExecutionBinding,
)
from agent.services.hrm_experiments.artifact_store import HrmArtifactStoreAdapter


class AnantaHrmTaskQueueAdapter:
    """Use the canonical Hub task queue without exposing it to Workers."""

    def create_run_task(self, *, run_id: str, profile_id: str, subject: str) -> str:
        from agent.services.task_queue_service import get_task_queue_service

        task_id = f"hrm-task-{uuid.uuid4()}"
        get_task_queue_service().ingest_task(
            task_id=task_id,
            status="todo",
            title=f"HRM experiment {run_id}",
            description="Execute one admitted HRM experiment through the isolated runner.",
            priority="medium",
            created_by=subject,
            source="hrm_experiments",
            tags=["hrm-experiment", "isolated-runner"],
            event_type="hrm_experiment_task_ingested",
            event_channel="hrm_experiments",
            extra_fields={
                "task_kind": "hrm_experiment",
                "required_capabilities": [
                    "hrm_experiment",
                    f"hrm_experiment.profile.{profile_id}",
                ],
                "worker_execution_context": {
                    "hrm_experiment": {"run_id": run_id}
                },
                "verification_spec": {
                    "schema": "ananta.hrm-experiments.worker-verification.v1",
                    "required_checks": [
                        "authority_binding",
                        "lease_fencing",
                        "run_result_contract",
                    ],
                },
            },
        )
        return task_id

    def cancel_run_task(self, task_id: str, *, reason_code: str) -> None:
        from agent.services.task_runtime_service import update_local_task_status

        update_local_task_status(
            task_id,
            "cancelled",
            event_type="hrm_experiment_task_cancelled",
            event_actor="hrm_experiments",
            event_details={"reason_code": reason_code},
            status_reason_code=reason_code,
        )


class AnantaHrmExecutionBindingAdapter:
    """Resolve current WorkerJob and slot lease from authoritative Hub state."""

    def resolve(
        self, *, task_id: str, worker_job_id: str, worker_url: str
    ) -> HrmExecutionBinding:
        from agent.services.repository_registry import get_repository_registry
        from agent.services.task_runtime_service import get_local_task_status

        task = get_local_task_status(task_id)
        job = get_repository_registry().worker_job_repo.get_by_id(worker_job_id)
        if task is None or job is None:
            raise HrmApplicationError("hrm.execution_binding_not_found", status_code=404)
        normalized_worker_url = worker_url.rstrip("/")
        if (
            str(task.get("current_worker_job_id") or "") != worker_job_id
            or str(job.parent_task_id or "") != task_id
            or str(job.worker_url or "").rstrip("/") != normalized_worker_url
            or str(task.get("assigned_agent_url") or "").rstrip("/")
            != normalized_worker_url
            or str(task.get("task_kind") or "") != "hrm_experiment"
        ):
            raise HrmApplicationError("hrm.execution_binding_mismatch")
        lease_id = str(job.slot_lease_id or "")
        lease = WorkerSlotLeaseRepository().get_by_id(lease_id) if lease_id else None
        if (
            lease is None
            or lease.status != "active"
            or str(lease.worker_job_id or "") != worker_job_id
            or str(lease.parent_task_id or "") != task_id
            or float(lease.deadline_at) <= time.time()
        ):
            raise HrmApplicationError("hrm.dispatch_lease_invalid")
        assignment_id = str(job.subtask_id or f"assignment:{worker_job_id}")
        return HrmExecutionBinding(
            task_id=task_id,
            worker_job_id=worker_job_id,
            assignment_id=assignment_id,
            dispatch_lease_id=lease_id,
            worker_url=normalized_worker_url,
            deadline_epoch_ms=int(float(lease.deadline_at) * 1000),
        )


class _ScopedAdmissionRepository(HrmAdmissionRepositoryPort):
    def __init__(
        self,
        *,
        repository: HrmExperimentRepository,
        artifacts: HrmArtifactStoreAdapter,
        owner_subject: str,
    ) -> None:
        self._repository = repository
        self._artifacts = artifacts
        self._owner_subject = owner_subject

    def save_dataset(self, manifest: Mapping[str, Any]) -> Mapping[str, Any]:
        records = self._artifacts.dataset_records(str(manifest["source"]["locator"]))
        return self._repository.save_dataset(
            manifest,
            records,
            owner_subject=self._owner_subject,
        ).manifest

    def save_checkpoint(self, manifest: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._repository.save_checkpoint(
            manifest,
            owner_subject=self._owner_subject,
        ).manifest


__all__ = [
    "AnantaHrmExecutionBindingAdapter",
    "AnantaHrmTaskQueueAdapter",
]
