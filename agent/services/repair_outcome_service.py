"""Repair outcome persistence service.
DRR-T027: Bridge RepairExecutionResult to RepairExecutionRecordDB persistence.
"""
from __future__ import annotations

import logging
from typing import Any

from agent.db_models import RepairExecutionRecordDB
from agent.repositories.repair_execution_record import get_repair_execution_record_repo
from worker.core.execution_envelope import RepairExecutionResult

log = logging.getLogger(__name__)


def persist_repair_execution_result(
    result: RepairExecutionResult,
    *,
    goal_id: str = "",
    task_id: str = "",
    worker_job_id: str = "",
    platform_target: str = "",
    signature_id: str = "",
    environment_facts_hash: str = "",
) -> dict[str, Any]:
    """Persist a RepairExecutionResult to the database.

    Returns dict with persisted status and record id, or error.
    """
    try:
        db_entry = RepairExecutionRecordDB(
            goal_id=goal_id,
            task_id=task_id,
            worker_job_id=worker_job_id,
            plan_id=result.plan_id,
            procedure_id=result.procedure_id,
            signature_id=signature_id,
            problem_class=result.step_results[0].step_id if result.step_results else "",
            platform_target=platform_target,
            environment_facts_hash=environment_facts_hash,
            execution_status=result.status.value,
            outcome_label=result.outcome_label,
            verification_evidence_refs=[],
            artifact_refs=[a.get("artifact_id", "") for a in result.artifacts if isinstance(a, dict)],
            trace_ref=result.trace_bundle_ref,
            regression_flag=result.outcome_label == "regressed",
            extra_metadata={
                "completed_steps": result.completed_steps,
                "skipped_steps": result.skipped_steps,
                "failed_step_id": result.failed_step_id,
                "approval_required_step_id": result.approval_required_step_id,
            },
        )

        if result.selected_worker_runtime:
            db_entry.selected_worker_id = result.selected_worker_runtime.selected_worker_id
            db_entry.selected_worker_kind = (
                result.selected_worker_runtime.selected_worker_kind.value
                if result.selected_worker_runtime.selected_worker_kind
                else None
            )
            db_entry.selected_runtime_target_id = result.selected_worker_runtime.selected_runtime_target_id
            db_entry.selected_runtime_kind = (
                result.selected_worker_runtime.selected_runtime_kind.value
                if result.selected_worker_runtime.selected_runtime_kind
                else None
            )
            db_entry.selection_decision_ref = result.selected_worker_runtime.selection_decision_ref
            db_entry.selection_reason = result.selected_worker_runtime.selection_reason

        if result.actual_worker_runtime:
            db_entry.actual_worker_id = result.actual_worker_runtime.selected_worker_id
            db_entry.actual_worker_kind = (
                result.actual_worker_runtime.selected_worker_kind.value
                if result.actual_worker_runtime.selected_worker_kind
                else None
            )
            db_entry.actual_runtime_target_id = result.actual_worker_runtime.selected_runtime_target_id
            db_entry.actual_runtime_kind = (
                result.actual_worker_runtime.selected_runtime_kind.value
                if result.actual_worker_runtime.selected_runtime_kind
                else None
            )

        if result.final_verification:
            db_entry.verification_evidence_refs = list(
                result.final_verification.get("evidence_refs") or []
            )

        problem_class = (
            (result.step_results[0].evidence or {}).get("problem_class")
            if result.step_results
            else ""
        )
        if problem_class:
            db_entry.problem_class = str(problem_class)

        saved = get_repair_execution_record_repo().save(db_entry)
        return {"persisted": True, "id": saved.id}
    except Exception as exc:
        log.error("Failed to persist repair execution result: %s", exc)
        return {"persisted": False, "error": str(exc)}


def query_repair_outcomes(
    *,
    problem_class: str = "",
    procedure_id: str = "",
    signature_id: str = "",
) -> list[dict[str, Any]]:
    """Query repair outcome records by problem_class, procedure_id, or signature_id.

    Returns list of serialised outcome dicts.  At most one filter is applied;
    if none is given the 10 most recent records are returned.
    """
    repo = get_repair_execution_record_repo()

    if problem_class:
        records = repo.query_by_problem_class(problem_class, limit=20)
    elif procedure_id:
        records = repo.query_by_procedure_id(procedure_id, limit=20)
    elif signature_id:
        records = repo.query_by_signature_id(signature_id, limit=20)
    else:
        records = repo.recent_by_environment({}, limit=10)

    return [
        {
            "id": str(r.id or ""),
            "plan_id": r.plan_id,
            "procedure_id": r.procedure_id,
            "problem_class": r.problem_class,
            "execution_status": r.execution_status,
            "outcome_label": r.outcome_label,
            "regression_flag": r.regression_flag,
            "created_at": r.created_at,
        }
        for r in records
    ]
