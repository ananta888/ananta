"""Fail-closed verification of already materialized planning tasks and committed receipts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.db_models import (
    ArchivedTaskDB,
    PlanningOperationReceiptDB,
    PlanningTaskMappingDB,
    TaskDB,
)
from agent.services.planning_artifact_transition_service import (
    PlanningTransitionError,
)
from agent.services.planning_control_unit_of_work import (
    PlanningControlUnitOfWork,
)


def verify_existing_task(
    task: TaskDB | ArchivedTaskDB,
    mapping: PlanningTaskMappingDB,
    *,
    runtime_contract: Mapping[str, Any],
) -> None:
    if not runtime_task_binding_matches(
        task,
        mapping,
    ):
        raise PlanningTransitionError("planning_materialized_task_binding_conflict")
    expected_workflow_binding = runtime_contract.get("workflow_binding")
    actual_workflow_binding = dict(task.worker_execution_context or {}).get("organization_workflow_step_binding")
    if (
        (expected_workflow_binding is None and actual_workflow_binding is not None)
        or (
            expected_workflow_binding is not None
            and (
                not isinstance(actual_workflow_binding, Mapping)
                or dict(actual_workflow_binding) != dict(expected_workflow_binding)
            )
        )
        or dict(task.verification_spec or {}) != dict(runtime_contract.get("verification_spec") or {})
    ):
        raise PlanningTransitionError("planning_materialized_task_runtime_contract_conflict")


def runtime_task_binding_matches(
    task: TaskDB | ArchivedTaskDB,
    mapping: PlanningTaskMappingDB,
) -> bool:
    return not (
        str(task.tenant_id or "") != mapping.tenant_id
        or str(task.project_id or "") != mapping.project_id
        or str(task.organization_id or "") != mapping.organization_id
        or str(task.goal_id or "") != mapping.execution_goal_id
        or str(task.unit_id or "") != str(mapping.unit_id or "")
        or str(task.team_id or "") != str(mapping.team_id or "")
        or str(task.role_slot_id or "") != str(mapping.role_slot_id or "")
    )


def verify_committed_materialization(
    *,
    uow: PlanningControlUnitOfWork,
    receipt: PlanningOperationReceiptDB,
    mappings: list[PlanningTaskMappingDB],
    runtime_contracts: Mapping[str, Mapping[str, Any]],
    expected_plan_task_ids: set[str],
) -> None:
    assert uow.session is not None
    if (
        receipt.status != "committed"
        or {row.plan_task_id for row in mappings} != expected_plan_task_ids
        or any(row.materialization_receipt_id != receipt.id for row in mappings)
    ):
        raise PlanningTransitionError("planning_materialization_receipt_incomplete")
    for mapping in mappings:
        task = uow.session.get(TaskDB, mapping.internal_task_id)
        if task is None:
            task = uow.session.get(ArchivedTaskDB, mapping.internal_task_id)
        runtime_contract = runtime_contracts.get(mapping.plan_task_id)
        if task is None or runtime_contract is None:
            raise PlanningTransitionError("planning_materialization_receipt_incomplete")
        try:
            verify_existing_task(
                task,
                mapping,
                runtime_contract=runtime_contract,
            )
        except PlanningTransitionError as exc:
            raise PlanningTransitionError("planning_materialization_receipt_incomplete") from exc


__all__ = [
    "runtime_task_binding_matches",
    "verify_committed_materialization",
    "verify_existing_task",
]
