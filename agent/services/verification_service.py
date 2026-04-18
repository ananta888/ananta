import time
from typing import Any

from sqlmodel import Session, select

from agent.common.audit import log_audit
from agent.common.governance_codes import GovernanceReasonCode
from agent.database import engine
from agent.db_models import TaskDB, VerificationRecordDB
from agent.services.cost_aggregation_service import get_cost_aggregation_service
from agent.services.hub_event_service import (
    build_hub_event_catalog,
    build_policy_governance_event,
    build_task_history_event,
    build_verification_governance_event,
)
from agent.services.repository_registry import get_repository_registry
from agent.services.task_runtime_service import notify_task_update
from agent.services.verification_policy_service import default_verification_spec, evaluate_quality_gates


class VerificationService:
    def _classify_failure(
        self,
        *,
        quality_gates_passed: bool,
        quality_gates_reason: str,
        external_gate_results: dict[str, Any],
        exit_code: int | None,
    ) -> str:
        external_passed = bool(external_gate_results.get("passed", quality_gates_passed))
        if exit_code not in (None, 0) or quality_gates_reason == "non_zero_exit_code":
            return "execution_failure"
        if not external_passed:
            return "external_gate_failure"
        if quality_gates_reason in {"insufficient_output_evidence", "missing_coding_quality_markers"}:
            return "quality_evidence_missing"
        if not quality_gates_passed:
            return "quality_gate_failure"
        return "unknown_failure"

    def _build_repair_workflow(self, failure_classification: str, retry_count: int) -> dict[str, Any]:
        attempts = max(0, int(retry_count))
        exhausted = attempts >= 3
        action_map = {
            "execution_failure": "rerun_with_debug",
            "external_gate_failure": "fix_external_checks",
            "quality_evidence_missing": "add_verification_evidence",
            "quality_gate_failure": "fix_quality_issues",
            "unknown_failure": "manual_review",
        }
        return {
            "failure_classification": failure_classification,
            "next_action": "escalate_to_human" if exhausted else action_map.get(failure_classification, "manual_review"),
            "retry_budget_remaining": max(0, 3 - attempts),
            "repair_required": True,
        }

    def ensure_task_spec(self, task_id: str) -> dict[str, Any] | None:
        repos = get_repository_registry()
        task = repos.task_repo.get_by_id(task_id)
        if not task:
            return None
        spec = dict(task.verification_spec or {})
        if not spec:
            spec = default_verification_spec(task.model_dump())
            task.verification_spec = spec
            repos.task_repo.save(task)
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
        with Session(engine) as session:
            task = session.get(TaskDB, task_id)
            if not task:
                return None

            spec = dict(task.verification_spec or {})
            if not spec:
                spec = default_verification_spec(task.model_dump())
                task.verification_spec = spec

            existing = session.exec(
                select(VerificationRecordDB).where(VerificationRecordDB.task_id == task_id).order_by(VerificationRecordDB.created_at.desc())
            ).first()
            record = existing or VerificationRecordDB(task_id=task_id, goal_id=task.goal_id, trace_id=trace_id)

            passed, reason_code = evaluate_quality_gates(task, output, exit_code, policy=None)
            external_gate = dict(gate_results or {})
            external_passed = bool(external_gate.get("passed", passed))
            status = "passed" if (passed and external_passed) else "failed"

            record.trace_id = trace_id or record.trace_id
            record.goal_id = task.goal_id
            record.spec = spec
            record.status = status
            failure_classification = None
            repair_workflow = {}
            if status != "passed":
                failure_classification = self._classify_failure(
                    quality_gates_passed=passed,
                    quality_gates_reason=reason_code,
                    external_gate_results=external_gate,
                    exit_code=exit_code,
                )
                record.escalation_code = failure_classification
                repair_workflow = self._build_repair_workflow(failure_classification, int(record.retry_count or 0) + 1)
            record.results = {
                "quality_gates_passed": passed,
                "quality_gates_reason": reason_code,
                "external_gate_results": external_gate,
                "final_passed": status == "passed",
                "failure_classification": failure_classification,
                "repair_workflow": repair_workflow,
            }
            record.results = get_cost_aggregation_service().attach_cost_to_verification_results(task=task, results=record.results)
            if status == "failed":
                record.retry_count = int(record.retry_count or 0) + 1
                record.repair_attempts = min(record.retry_count, 3)
                if record.retry_count >= 3:
                    record.status = "escalated"
                    record.escalation_code = GovernanceReasonCode.RETRY_EXHAUSTED.value
                    record.escalation_reason = "verification_retry_limit_reached"
            record.updated_at = time.time()

            merged_record = session.merge(record)
            session.flush()
            verification_status = {
                "status": merged_record.status,
                "record_id": merged_record.id,
                "retry_count": merged_record.retry_count,
                "repair_attempts": merged_record.repair_attempts,
                "escalation_code": merged_record.escalation_code,
                "escalation_reason": merged_record.escalation_reason,
                "failure_classification": (merged_record.results or {}).get("failure_classification"),
                "repair_workflow": (merged_record.results or {}).get("repair_workflow"),
                "results": merged_record.results,
            }
            task.verification_spec = spec
            task.verification_status = verification_status
            task.updated_at = time.time()
            history = list(task.history or [])
            history.append(
                build_task_history_event(
                    task,
                    "task_verification_updated",
                    actor="verification_service",
                    details={
                        "verification_status": verification_status.get("status"),
                        "record_id": verification_status.get("record_id"),
                    },
                    timestamp=time.time(),
                )
            )
            task.history = history[-200:]
            session.add(task)
            session.commit()
            session.refresh(merged_record)
            saved = merged_record

        notify_task_update(task_id)
        log_audit(
            "verification_record_updated",
            {
                "task_id": task_id,
                "goal_id": saved.goal_id,
                "trace_id": trace_id or saved.trace_id,
                "verification_record_id": saved.id,
                "status": saved.status,
                "retry_count": saved.retry_count,
                "repair_attempts": saved.repair_attempts,
                "escalation_code": saved.escalation_code,
                "escalation_reason": saved.escalation_reason,
                "failure_classification": (saved.results or {}).get("failure_classification"),
            },
        )
        return saved

    def governance_summary(self, goal_id: str, *, include_sensitive: bool = False) -> dict[str, Any] | None:
        repos = get_repository_registry()
        goal = repos.goal_repo.get_by_id(goal_id)
        if not goal:
            return None
        tasks = repos.task_repo.get_by_goal_id(goal_id)
        task_ids = [task.id for task in tasks]
        goal_policy = repos.policy_decision_repo.get_by_goal_or_task_ids(goal_id=goal_id, task_ids=task_ids, limit=500)
        verification_records = repos.verification_record_repo.get_by_goal_or_task_ids(
            goal_id=goal_id, task_ids=task_ids, limit=500
        )

        return {
            "goal_id": goal_id,
            "trace_id": goal.trace_id,
            "event_contract": build_hub_event_catalog().model_dump(),
            "policy": {
                "total": len(goal_policy),
                "approved": len([item for item in goal_policy if item.status == "approved"]),
                "blocked": len([item for item in goal_policy if item.status == "blocked"]),
                **(
                    {
                        "latest": [item.model_dump() for item in goal_policy[:10]],
                        "latest_events": [build_policy_governance_event(item) for item in goal_policy[:10]],
                    }
                    if include_sensitive
                    else {}
                ),
            },
            "verification": {
                "total": len(verification_records),
                "passed": len([item for item in verification_records if item.status == "passed"]),
                "failed": len([item for item in verification_records if item.status == "failed"]),
                "escalated": len([item for item in verification_records if item.status == "escalated"]),
                **(
                    {
                        "latest": [item.model_dump() for item in verification_records[:10]],
                        "latest_events": [build_verification_governance_event(item) for item in verification_records[:10]],
                    }
                    if include_sensitive
                    else {}
                ),
            },
            "cost_summary": get_cost_aggregation_service().aggregate_goal_costs(goal_id),
            "summary": {
                "goal_status": goal.status,
                "task_count": len(tasks),
                "governance_visible": bool(include_sensitive),
            },
        }


verification_service = VerificationService()


def get_verification_service() -> VerificationService:
    return verification_service
