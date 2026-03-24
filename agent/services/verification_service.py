import time
from typing import Any

from agent.db_models import VerificationRecordDB
from agent.repository import goal_repo, policy_decision_repo, task_repo, verification_record_repo
from agent.routes.tasks.quality_gates import evaluate_quality_gates
from agent.routes.tasks.utils import _update_local_task_status


def default_verification_spec(task: dict | None) -> dict[str, Any]:
    task_data = task or {}
    return {
        "lint": bool(str(task_data.get("task_kind") or "").lower() in {"coding", "testing"}),
        "tests": bool(str(task_data.get("task_kind") or "").lower() in {"coding", "testing", "verification"}),
        "policy": True,
        "mode": "quality_gates",
    }


class VerificationService:
    def ensure_task_spec(self, task_id: str) -> dict[str, Any] | None:
        task = task_repo.get_by_id(task_id)
        if not task:
            return None
        spec = dict(task.verification_spec or {})
        if not spec:
            spec = default_verification_spec(task.model_dump())
            task.verification_spec = spec
            task_repo.save(task)
        return spec

    def create_or_update_record(
        self,
        task_id: str,
        *,
        trace_id: str | None,
        output: str | None,
        exit_code: int | None,
        gate_results: dict | None = None,
    ) -> VerificationRecordDB | None:
        task = task_repo.get_by_id(task_id)
        if not task:
            return None
        spec = self.ensure_task_spec(task_id) or {}
        existing = verification_record_repo.get_by_task_id(task_id)
        record = existing[0] if existing else VerificationRecordDB(task_id=task_id, goal_id=task.goal_id, trace_id=trace_id)

        passed, reason_code = evaluate_quality_gates(task, output, exit_code, policy=None)
        external_gate = dict(gate_results or {})
        external_passed = bool(external_gate.get("passed", passed))
        status = "passed" if (passed and external_passed) else "failed"

        record.trace_id = trace_id or record.trace_id
        record.goal_id = task.goal_id
        record.spec = spec
        record.status = status
        record.results = {
            "quality_gates_passed": passed,
            "quality_gates_reason": reason_code,
            "external_gate_results": external_gate,
            "final_passed": status == "passed",
        }
        if status == "failed":
            record.retry_count = int(record.retry_count or 0) + 1
            record.repair_attempts = min(record.retry_count, 3)
            if record.retry_count >= 3:
                record.status = "escalated"
                record.escalation_reason = "verification_retry_limit_reached"
        record.updated_at = time.time()
        saved = verification_record_repo.save(record)

        verification_status = {
            "status": saved.status,
            "record_id": saved.id,
            "retry_count": saved.retry_count,
            "repair_attempts": saved.repair_attempts,
            "escalation_reason": saved.escalation_reason,
            "results": saved.results,
        }
        _update_local_task_status(task_id, task.status, verification_spec=spec, verification_status=verification_status)
        return saved

    def governance_summary(self, goal_id: str) -> dict[str, Any] | None:
        goal = goal_repo.get_by_id(goal_id)
        if not goal:
            return None
        tasks = task_repo.get_by_goal_id(goal_id)
        policy_decisions = policy_decision_repo.get_all(limit=500)
        goal_policy = [item for item in policy_decisions if item.goal_id == goal_id or item.task_id in {task.id for task in tasks}]
        verification_records = verification_record_repo.get_by_goal_id(goal_id)
        if not verification_records:
            for task in tasks:
                verification_records.extend(verification_record_repo.get_by_task_id(task.id))

        return {
            "goal_id": goal_id,
            "trace_id": goal.trace_id,
            "policy": {
                "total": len(goal_policy),
                "approved": len([item for item in goal_policy if item.status == "approved"]),
                "blocked": len([item for item in goal_policy if item.status == "blocked"]),
                "latest": [item.model_dump() for item in goal_policy[:10]],
            },
            "verification": {
                "total": len(verification_records),
                "passed": len([item for item in verification_records if item.status == "passed"]),
                "failed": len([item for item in verification_records if item.status == "failed"]),
                "escalated": len([item for item in verification_records if item.status == "escalated"]),
                "latest": [item.model_dump() for item in verification_records[:10]],
            },
            "summary": {
                "goal_status": goal.status,
                "task_count": len(tasks),
                "governance_visible": True,
            },
        }


verification_service = VerificationService()


def get_verification_service() -> VerificationService:
    return verification_service
