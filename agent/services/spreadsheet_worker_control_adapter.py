"""Adapters from Spreadsheet Studio to Ananta's central Worker control plane."""

from __future__ import annotations

from agent.repository import worker_job_repo
from agent.services.spreadsheet_execution_queue_ports import (
    SpreadsheetLeaseDecision,
    SpreadsheetWorkerJobBinding,
)
from agent.services.worker_job_service import get_worker_job_service
from agent.services.worker_pool_scheduler_service import get_worker_pool_scheduler_service


class HubSpreadsheetWorkerJobLedger:
    def create(
        self,
        *,
        queue_job_id: str,
        proposal_id: str,
        assignment_digest: str,
        worker_id: str,
    ) -> SpreadsheetWorkerJobBinding:
        record = get_worker_job_service().create_worker_job(
            parent_task_id=queue_job_id,
            subtask_id=proposal_id,
            worker_url=worker_id,
            context_bundle_id=None,
            allowed_tools=["libreoffice.calc.headless"],
            expected_output_schema={
                "type": "object",
                "required": ["assignment_digest", "result"],
                "additionalProperties": False,
                "properties": {
                    "assignment_digest": {"type": "string", "const": assignment_digest},
                    "result": {"type": "object"},
                },
            },
            metadata={
                "domain": "spreadsheet_studio",
                "queue_job_id": queue_job_id,
                "proposal_id": proposal_id,
                "assignment_digest": assignment_digest,
            },
            selection_decision={
                "selected_worker_id": worker_id,
                "selected_worker_kind": "spreadsheet_libreoffice",
                "selected_runtime_target_id": worker_id,
                "selected_runtime_kind": "libreoffice_calc",
                "selection_mode": "capability_matched",
                "policy_decision_ref": assignment_digest,
            },
        )
        return SpreadsheetWorkerJobBinding(worker_job_id=record.id)

    def bind_lease(
        self,
        *,
        worker_job_id: str,
        slot_lease_id: str,
        status: str,
        queue_position: int | None,
        reason_code: str,
    ) -> None:
        record = worker_job_repo.get_by_id(worker_job_id)
        if record is None:
            raise RuntimeError("spreadsheet_worker_job_missing")
        record.slot_lease_id = slot_lease_id
        record.queue_position = queue_position
        record.scheduling_reason_code = reason_code
        record.status = "delegated" if status in {"active", "queued"} else "rejected"
        worker_job_repo.save(record)


class HubSpreadsheetWorkerLeaseScheduler:
    def acquire(
        self,
        *,
        worker_job_id: str,
        queue_job_id: str,
        worker_id: str,
        assignment_digest: str,
    ) -> SpreadsheetLeaseDecision:
        decision = get_worker_pool_scheduler_service().acquire_for_job(
            request={
                "selected_worker_id": worker_id,
                "selected_worker_kind": "spreadsheet_libreoffice",
                "selected_runtime_target_id": worker_id,
                "selected_runtime_kind": "libreoffice_calc",
                "parent_task_id": queue_job_id,
                "worker_job_id": worker_job_id,
                "policy_decision_ref": assignment_digest,
                "policy_decision_hash": assignment_digest,
                "worker_capacity": 1,
                "runtime_capacity": 1,
                "security_policy_cap": 1,
                "slot_lease_seconds": 600,
            }
        )
        return SpreadsheetLeaseDecision(
            status=decision.status,
            reason_code=decision.reason_code,
            slot_lease_id=decision.slot_lease_id,
            queue_position=decision.queue_position,
        )


__all__ = ["HubSpreadsheetWorkerJobLedger", "HubSpreadsheetWorkerLeaseScheduler"]
