from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from agent.db_models import WorkerSlotLeaseDB
from agent.repository import worker_slot_lease_repo
from agent.services.autopilot_wake_service import request_autopilot_wake
from agent.services.ollama_parallel_runtime_service import get_ollama_parallel_runtime_service


@dataclass(frozen=True)
class WorkerSlotDecision:
    status: str  # active|queued|rejected
    reason_code: str
    slot_lease_id: str | None = None
    queue_position: int | None = None
    selected_worker_id: str | None = None
    selected_runtime_target_id: str | None = None
    ollama_endpoint: str | None = None
    ollama_model: str | None = None


class WorkerPoolSchedulerService:
    """Central slot orchestration service for worker/runtime + optional ollama model slots."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ollama = get_ollama_parallel_runtime_service()

    @staticmethod
    def compute_effective_concurrency_cap(
        security_policy_cap: int | None,
        worker_capacity: int,
        runtime_capacity: int,
        ollama_model_capacity: int | None,
    ) -> int:
        caps = [max(1, int(worker_capacity)), max(1, int(runtime_capacity))]
        if security_policy_cap is not None:
            caps.append(max(1, int(security_policy_cap)))
        if ollama_model_capacity is not None:
            caps.append(max(1, int(ollama_model_capacity)))
        return min(caps)

    def acquire_for_job(self, *, request: dict[str, Any]) -> WorkerSlotDecision:
        with self._lock:
            worker_id = str(request.get("selected_worker_id") or "").strip() or None
            runtime_target_id = str(request.get("selected_runtime_target_id") or "").strip() or None
            worker_kind = str(request.get("selected_worker_kind") or "").strip() or None
            runtime_kind = str(request.get("selected_runtime_kind") or "").strip() or None
            parent_task_id = str(request.get("parent_task_id") or "").strip() or None
            worker_job_id = str(request.get("worker_job_id") or "").strip() or None
            policy_decision_ref = str(request.get("policy_decision_ref") or "").strip() or None
            policy_decision_hash = str(request.get("policy_decision_hash") or "").strip() or None
            worker_capacity = max(1, int(request.get("worker_capacity") or 1))
            runtime_capacity = max(1, int(request.get("runtime_capacity") or worker_capacity))
            security_cap = request.get("security_policy_cap")
            max_parallel = self.compute_effective_concurrency_cap(security_cap, worker_capacity, runtime_capacity, None)
            now = time.time()
            active_for_worker = [
                lease
                for lease in worker_slot_lease_repo.list_active()
                if lease.worker_id == worker_id and lease.lease_type in {"worker", "combined"}
            ]
            queue_for_worker = [
                lease
                for lease in worker_slot_lease_repo.list_queued()
                if lease.worker_id == worker_id and lease.lease_type in {"worker", "combined"}
            ]
            queue_limit = max(1, int(request.get("worker_queue_limit") or 32))

            if len(active_for_worker) >= max_parallel:
                if len(queue_for_worker) >= queue_limit:
                    lease = worker_slot_lease_repo.save(WorkerSlotLeaseDB(
                        lease_type="worker",
                        status="rejected",
                        worker_id=worker_id,
                        worker_kind=worker_kind,
                        runtime_target_id=runtime_target_id,
                        runtime_kind=runtime_kind,
                        parent_task_id=parent_task_id,
                        worker_job_id=worker_job_id,
                        reason_code="worker_queue_full",
                        released_at=now,
                        lease_metadata={
                            "policy_decision_ref": policy_decision_ref,
                            "policy_decision_hash": policy_decision_hash,
                        },
                    ))
                    return WorkerSlotDecision(status="rejected", reason_code="worker_queue_full", slot_lease_id=lease.id)

                queue_position = len(queue_for_worker) + 1
                lease = worker_slot_lease_repo.save(WorkerSlotLeaseDB(
                    lease_type="worker",
                    status="queued",
                    worker_id=worker_id,
                    worker_kind=worker_kind,
                    runtime_target_id=runtime_target_id,
                    runtime_kind=runtime_kind,
                    parent_task_id=parent_task_id,
                    worker_job_id=worker_job_id,
                    queue_position=queue_position,
                    reason_code="worker_parallel_capacity_exhausted",
                    deadline_at=now + max(1, int(request.get("slot_lease_seconds") or 600)),
                    lease_metadata={
                        "policy_decision_ref": policy_decision_ref,
                        "policy_decision_hash": policy_decision_hash,
                    },
                ))
                return WorkerSlotDecision(
                    status="queued",
                    reason_code="worker_parallel_capacity_exhausted",
                    slot_lease_id=lease.id,
                    queue_position=queue_position,
                    selected_worker_id=worker_id,
                    selected_runtime_target_id=runtime_target_id,
                )

            endpoint = str(request.get("ollama_endpoint") or "").strip()
            model = str(request.get("ollama_model") or "").strip()
            ollama_lease_id = None
            if endpoint and model:
                ollama_decision = self._ollama.acquire_slot(
                    endpoint=endpoint,
                    model=model,
                    max_parallel_requests=max(1, int(request.get("ollama_max_parallel_requests") or 4)),
                    queue_limit=max(1, int(request.get("ollama_queue_limit") or 64)),
                    lease_seconds=max(1, int(request.get("slot_lease_seconds") or 600)),
                    backpressure=str(request.get("ollama_backpressure") or "queue_then_reject"),
                )
                if ollama_decision.status == "rejected":
                    return WorkerSlotDecision(
                        status="rejected",
                        reason_code=ollama_decision.reason_code,
                        selected_worker_id=worker_id,
                        selected_runtime_target_id=runtime_target_id,
                        ollama_endpoint=endpoint,
                        ollama_model=model,
                    )
                if ollama_decision.status == "queued":
                    queue_position = ollama_decision.queue_position or 1
                    lease = worker_slot_lease_repo.save(WorkerSlotLeaseDB(
                        lease_type="combined",
                        status="queued",
                        worker_id=worker_id,
                        worker_kind=worker_kind,
                        runtime_target_id=runtime_target_id,
                        runtime_kind=runtime_kind,
                        ollama_endpoint=endpoint,
                        ollama_model=model,
                        parent_task_id=parent_task_id,
                        worker_job_id=worker_job_id,
                        queue_position=queue_position,
                        reason_code=ollama_decision.reason_code,
                        deadline_at=now + max(1, int(request.get("slot_lease_seconds") or 600)),
                        lease_metadata={
                            "ollama_lease_id": ollama_decision.lease_id,
                            "policy_decision_ref": policy_decision_ref,
                            "policy_decision_hash": policy_decision_hash,
                        },
                    ))
                    return WorkerSlotDecision(
                        status="queued",
                        reason_code=ollama_decision.reason_code,
                        slot_lease_id=lease.id,
                        queue_position=queue_position,
                        selected_worker_id=worker_id,
                        selected_runtime_target_id=runtime_target_id,
                        ollama_endpoint=endpoint,
                        ollama_model=model,
                    )
                ollama_lease_id = ollama_decision.lease_id

            lease = worker_slot_lease_repo.save(WorkerSlotLeaseDB(
                lease_type="combined" if (endpoint and model) else "worker",
                status="active",
                worker_id=worker_id,
                worker_kind=worker_kind,
                runtime_target_id=runtime_target_id,
                runtime_kind=runtime_kind,
                ollama_endpoint=endpoint or None,
                ollama_model=model or None,
                parent_task_id=parent_task_id,
                worker_job_id=worker_job_id,
                reason_code="slot_acquired",
                deadline_at=now + max(1, int(request.get("slot_lease_seconds") or 600)),
                lease_metadata={"ollama_lease_id": ollama_lease_id} if ollama_lease_id else {},
            ))
            md = dict(lease.lease_metadata or {})
            if policy_decision_ref:
                md["policy_decision_ref"] = policy_decision_ref
            if policy_decision_hash:
                md["policy_decision_hash"] = policy_decision_hash
            if md:
                lease.lease_metadata = md
                lease = worker_slot_lease_repo.save(lease)
            return WorkerSlotDecision(
                status="active",
                reason_code="slot_acquired",
                slot_lease_id=lease.id,
                selected_worker_id=worker_id,
                selected_runtime_target_id=runtime_target_id,
                ollama_endpoint=endpoint or None,
                ollama_model=model or None,
            )

    def release_for_job(self, slot_lease_id: str | None) -> None:
        if not slot_lease_id:
            return
        lease = worker_slot_lease_repo.get_by_id(slot_lease_id)
        if lease is None:
            return
        metadata = dict(lease.lease_metadata or {})
        ollama_lease_id = metadata.get("ollama_lease_id")
        if lease.ollama_endpoint and lease.ollama_model and ollama_lease_id:
            self._ollama.release_slot(endpoint=lease.ollama_endpoint, model=lease.ollama_model, lease_id=str(ollama_lease_id))
        worker_slot_lease_repo.release(slot_lease_id)
        try:
            request_autopilot_wake(
                "worker_capacity_released",
                worker_url=str(lease.worker_id or ""),
                runtime_target_id=str(lease.runtime_target_id or ""),
                slot_lease_id=str(slot_lease_id),
            )
        except Exception:
            pass

    def cleanup_stale_leases(self) -> int:
        stale = worker_slot_lease_repo.list_expired()
        cleaned = 0
        for lease in stale:
            if lease.ollama_endpoint and lease.ollama_model:
                metadata = dict(lease.lease_metadata or {})
                ollama_lease_id = metadata.get("ollama_lease_id")
                if ollama_lease_id:
                    self._ollama.release_slot(endpoint=lease.ollama_endpoint, model=lease.ollama_model, lease_id=str(ollama_lease_id))
            worker_slot_lease_repo.release(lease.id, status="stale_released")
            cleaned += 1
        return cleaned

    def revalidate_queued_job(
        self,
        *,
        slot_lease_id: str,
        policy_decision_ref: str | None,
        policy_decision_hash: str | None,
        worker_online: bool = True,
        policy_allowed: bool = True,
        capacity_available: bool = True,
    ) -> WorkerSlotDecision:
        lease = worker_slot_lease_repo.get_by_id(slot_lease_id)
        if lease is None:
            return WorkerSlotDecision(status="rejected", reason_code="unknown_slot_lease", slot_lease_id=slot_lease_id)
        if lease.status != "queued":
            return WorkerSlotDecision(status=lease.status, reason_code="lease_not_queued", slot_lease_id=slot_lease_id)

        old_ref = None
        old_hash = None
        md = dict(lease.lease_metadata or {})
        old_ref = md.get("policy_decision_ref")
        old_hash = md.get("policy_decision_hash")
        if old_hash and policy_decision_hash and str(old_hash) != str(policy_decision_hash):
            lease.reason_code = "stale_policy_decision"
            lease.status = "rejected"
            lease.released_at = time.time()
            lease.lease_metadata = {
                **md,
                "old_decision_ref": old_ref,
                "new_decision_ref": policy_decision_ref,
            }
            worker_slot_lease_repo.save(lease)
            return WorkerSlotDecision(status="rejected", reason_code="stale_policy_decision", slot_lease_id=slot_lease_id)
        if not policy_allowed:
            lease.status = "rejected"
            lease.reason_code = "policy_denied_on_revalidation"
            lease.released_at = time.time()
            worker_slot_lease_repo.save(lease)
            return WorkerSlotDecision(status="rejected", reason_code="policy_denied_on_revalidation", slot_lease_id=slot_lease_id)
        if not worker_online:
            return WorkerSlotDecision(status="queued", reason_code="worker_offline_requeue", slot_lease_id=slot_lease_id, queue_position=lease.queue_position)
        if not capacity_available:
            return WorkerSlotDecision(status="queued", reason_code="capacity_not_available_requeue", slot_lease_id=slot_lease_id, queue_position=lease.queue_position)

        lease.status = "active"
        lease.reason_code = "queued_revalidated_and_started"
        lease.queue_position = None
        lease.acquired_at = time.time()
        worker_slot_lease_repo.save(lease)
        return WorkerSlotDecision(
            status="active",
            reason_code="queued_revalidated_and_started",
            slot_lease_id=slot_lease_id,
            selected_worker_id=lease.worker_id,
            selected_runtime_target_id=lease.runtime_target_id,
            ollama_endpoint=lease.ollama_endpoint,
            ollama_model=lease.ollama_model,
        )

    def get_scheduler_status(self) -> dict[str, Any]:
        leases = worker_slot_lease_repo.list_all()
        active = [x for x in leases if x.status == "active"]
        queued = [x for x in leases if x.status == "queued"]
        rejected = [x for x in leases if x.status == "rejected"]
        stale = [x for x in leases if x.status == "stale_released"]

        capacity_by_worker: dict[str, int] = {}
        for lease in active:
            if lease.worker_id:
                capacity_by_worker[lease.worker_id] = capacity_by_worker.get(lease.worker_id, 0) + 1

        return {
            "active_slots": len(active),
            "queued_jobs": len(queued),
            "rejected_jobs": len(rejected),
            "stale_leases": len(stale),
            "capacity_by_worker": capacity_by_worker,
            "capacity_by_model": self._ollama.get_status(),
        }


_worker_pool_scheduler_service = WorkerPoolSchedulerService()


def get_worker_pool_scheduler_service() -> WorkerPoolSchedulerService:
    return _worker_pool_scheduler_service
