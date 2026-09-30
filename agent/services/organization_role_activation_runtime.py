"""Live runtime observation projection for Organization role activation.

Runtime states are derived only from an exact persisted workflow-step binding
and strictly scoped Task, WorkerJob and lease rows; absent or conflicting
evidence stays unknown.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from agent.db_models import TaskDB, WorkerJobDB, WorkerSlotLeaseDB
from agent.db_models.organizations import (
    OrganizationInstanceDB,
)

WORKFLOW_BINDING_SCHEMA = "organization_workflow_step_binding.v1"


def apply_runtime_observations(
    *,
    teams: Sequence[dict[str, Any]],
    tasks: Sequence[TaskDB],
    jobs: Sequence[WorkerJobDB],
    leases: Sequence[WorkerSlotLeaseDB],
    organization: OrganizationInstanceDB,
    now: float,
) -> dict[str, Any]:
    tasks_by_id = {row.id: row for row in tasks}
    jobs_by_id = {row.id: row for row in jobs if str(row.parent_task_id or "") in tasks_by_id}
    leases_by_id = {
        row.id: row
        for row in leases
        if str(row.parent_task_id or "") in tasks_by_id and str(row.worker_job_id or "") in jobs_by_id
    }
    bound_step_count = 0
    task_ready_step_count = 0
    hub_routed_step_count = 0
    worker_executing_step_count = 0
    workflow_step_count = 0
    for team in teams:
        workflow = dict(team["workflow"])
        workflow_ref = str(workflow["workflow_ref"])
        workflow_hash = str(team["revision_binding"]["workflow_content_hash"])
        for step in workflow["steps"]:
            workflow_step_count += 1
            matching = sorted(
                (
                    task
                    for task in tasks
                    if _task_has_exact_workflow_binding(
                        task=task,
                        organization=organization,
                        team_unit_id=str(team["team_unit_id"]),
                        workflow_ref=workflow_ref,
                        workflow_content_hash=workflow_hash,
                        step=step,
                    )
                ),
                key=lambda row: row.id,
            )
            runtime = _step_runtime_projection(
                matching_tasks=matching,
                tasks_by_id=tasks_by_id,
                jobs_by_id=jobs_by_id,
                leases_by_id=leases_by_id,
                now=now,
            )
            step["activation"]["runtime"] = runtime
            if runtime["binding"]["state"] == "exact":
                bound_step_count += 1
            if runtime["task_ready"]["state"] == "observed_true":
                task_ready_step_count += 1
            if runtime["hub_routed"]["state"] == "observed_true":
                hub_routed_step_count += 1
            if runtime["worker_executing"]["state"] == "observed_true":
                worker_executing_step_count += 1

    if bound_step_count == 0:
        observation = {
            "state": "not_observed",
            "reason_code": "organization_role_activation_exact_task_binding_missing",
            "task_state_included": False,
        }
    elif bound_step_count < workflow_step_count:
        observation = {
            "state": "partial",
            "reason_code": "organization_role_activation_runtime_partially_observed",
            "task_state_included": True,
        }
    else:
        observation = {
            "state": "observed",
            "reason_code": "organization_role_activation_runtime_observed",
            "task_state_included": True,
        }
    return {
        "observation": observation,
        "bound_step_count": bound_step_count,
        "task_ready_step_count": task_ready_step_count,
        "hub_routed_step_count": hub_routed_step_count,
        "worker_executing_step_count": worker_executing_step_count,
    }


def _task_has_exact_workflow_binding(
    *,
    task: TaskDB,
    organization: OrganizationInstanceDB,
    team_unit_id: str,
    workflow_ref: str,
    workflow_content_hash: str,
    step: Mapping[str, Any],
) -> bool:
    raw = dict(task.worker_execution_context or {}).get("organization_workflow_step_binding")
    if not isinstance(raw, Mapping):
        return False
    binding = dict(raw)
    expected_fields = {
        "schema",
        "organization_id",
        "definition_revision",
        "workflow_ref",
        "workflow_content_hash",
        "step_id",
        "team_unit_id",
        "team_id",
        "role_slot_id",
        "gate",
        "handoff_ref",
        "failure_policy",
    }
    if set(binding) != expected_fields:
        return False
    expected_gate = dict(step["gate"])
    verification_spec = dict(task.verification_spec or {})
    verification_checks = verification_spec.get("acceptance_checks")
    verification_independence = verification_spec.get("independent_principal_required")
    if not isinstance(verification_checks, list) or not isinstance(verification_independence, bool):
        return False
    return (
        binding.get("schema") == WORKFLOW_BINDING_SCHEMA
        and binding.get("organization_id") == organization.organization_id == task.organization_id
        and binding.get("definition_revision") == organization.definition_revision
        and binding.get("workflow_ref") == workflow_ref
        and binding.get("workflow_content_hash") == workflow_content_hash
        and binding.get("step_id") == step["step_id"]
        and binding.get("team_unit_id") == team_unit_id == task.unit_id
        and binding.get("team_id") == task.team_id
        and binding.get("role_slot_id") == task.role_slot_id
        and task.role_slot_id in set(step["role_binding"]["bound_role_slot_ids"])
        and binding.get("gate") == expected_gate
        and binding.get("handoff_ref") == step["handoff_ref"]
        and binding.get("failure_policy") == step["failure_policy"]
        and verification_checks == expected_gate["acceptance_checks"]
        and verification_spec.get("approval_role_ref") == expected_gate["approval_role_ref"]
        and verification_independence == expected_gate["independent_principal_required"]
        and str(verification_spec.get("failure_policy") or "") == step["failure_policy"]
    )


def _step_runtime_projection(
    *,
    matching_tasks: Sequence[TaskDB],
    tasks_by_id: Mapping[str, TaskDB],
    jobs_by_id: Mapping[str, WorkerJobDB],
    leases_by_id: Mapping[str, WorkerSlotLeaseDB],
    now: float,
) -> dict[str, Any]:
    if not matching_tasks:
        unknown = _runtime_fact((), reason_prefix="organization_role_activation")
        return {
            "binding": {
                "state": "unknown",
                "reason_code": "organization_role_activation_exact_task_binding_missing",
                "task_ids": [],
            },
            "task_ready": unknown,
            "hub_routed": unknown,
            "worker_executing": unknown,
            "worker_job_count": 0,
            "active_lease_count": 0,
        }

    readiness = [task_ready_fact(task, tasks_by_id=tasks_by_id) for task in matching_tasks]
    routed = [hub_routed_fact(task) for task in matching_tasks]
    executing = [
        _worker_executing_fact(
            task,
            jobs_by_id=jobs_by_id,
            leases_by_id=leases_by_id,
            now=now,
        )
        for task in matching_tasks
    ]
    current_jobs = [
        jobs_by_id[job_id]
        for task in matching_tasks
        if (job_id := str(task.current_worker_job_id or "")) in jobs_by_id
        and str(jobs_by_id[job_id].parent_task_id or "") == task.id
    ]
    active_leases = [
        leases_by_id[lease_id]
        for job in current_jobs
        if (lease_id := str(job.slot_lease_id or "")) in leases_by_id
        and _lease_is_active(
            leases_by_id[lease_id],
            task_id=str(job.parent_task_id or ""),
            worker_job_id=job.id,
            now=now,
        )
    ]
    return {
        "binding": {
            "state": "exact",
            "reason_code": "organization_role_activation_exact_task_binding_observed",
            "task_ids": [task.id for task in matching_tasks],
        },
        "task_ready": _runtime_fact(
            readiness,
            reason_prefix="organization_role_activation_task_ready",
        ),
        "hub_routed": _runtime_fact(
            routed,
            reason_prefix="organization_role_activation_hub_routed",
        ),
        "worker_executing": _runtime_fact(
            executing,
            reason_prefix="organization_role_activation_worker_executing",
        ),
        "worker_job_count": len({row.id for row in current_jobs}),
        "active_lease_count": len({row.id for row in active_leases}),
    }


def _runtime_fact(
    values: Sequence[str],
    *,
    reason_prefix: str,
) -> dict[str, Any]:
    counts = Counter(values)
    if counts["observed_true"]:
        state = "observed_true"
    elif not values or counts["unknown"]:
        state = "unknown"
    else:
        state = "observed_false"
    return {
        "state": state,
        "reason_code": f"{reason_prefix}_{state}",
        "observed_true_count": counts["observed_true"],
        "observed_false_count": counts["observed_false"],
        "unknown_count": counts["unknown"] if values else 1,
    }


def task_ready_fact(task: TaskDB, *, tasks_by_id: Mapping[str, TaskDB]) -> str:
    dependencies: list[TaskDB] = []
    for dependency_id in list(task.depends_on or []):
        dependency = tasks_by_id.get(str(dependency_id))
        if dependency is None:
            return "unknown"
        dependencies.append(dependency)
    status = str(task.status or "").strip().lower()
    if status in {"todo", "created", "blocked_by_dependency"} and all(
        str(dependency.status or "").strip().lower() == "completed" for dependency in dependencies
    ):
        return "observed_true"
    return "observed_false"


def hub_routed_fact(task: TaskDB) -> str:
    context = dict(task.worker_execution_context or {})
    raw_dispatch = context.get("planning_dispatch")
    status = str(task.status or "").strip().lower()
    if not isinstance(raw_dispatch, Mapping):
        return "unknown" if status in {"assigned", "in_progress", "delegated"} else "observed_false"
    dispatch = dict(raw_dispatch)
    dispatch_status = str(dispatch.get("status") or "")
    common_string_fields = (
        "dispatch_intent_id",
        "lease_id",
        "track_revision_id",
        "plan_task_id",
    )
    attempt = dispatch.get("attempt")
    if (
        dispatch.get("schema") != "organization_planning_dispatch.v1"
        or dispatch_status not in {"pending_dispatch", "dispatched"}
        or any(not str(dispatch.get(field) or "").strip() for field in common_string_fields)
        or isinstance(attempt, bool)
        or not isinstance(attempt, int)
        or attempt < 1
        or not str(task.assigned_agent_url or "")
    ):
        return "unknown"
    if dispatch_status == "pending_dispatch":
        if task.current_worker_job_id:
            return "unknown"
        return "observed_true" if status == "assigned" else "observed_false"
    if (
        not str(dispatch.get("assignment_id") or "").strip()
        or str(dispatch.get("worker_job_id") or "") != str(task.current_worker_job_id or "")
        or str(dispatch.get("worker_id") or "") != str(task.assigned_agent_url or "")
    ):
        return "unknown"
    return "observed_true" if status in {"in_progress", "delegated"} else "observed_false"


def _worker_executing_fact(
    task: TaskDB,
    *,
    jobs_by_id: Mapping[str, WorkerJobDB],
    leases_by_id: Mapping[str, WorkerSlotLeaseDB],
    now: float,
) -> str:
    task_status = str(task.status or "").strip().lower()
    worker_job_id = str(task.current_worker_job_id or "")
    if not worker_job_id:
        return "unknown" if task_status in {"in_progress", "delegated"} else "observed_false"
    job = jobs_by_id.get(worker_job_id)
    if job is None or str(job.parent_task_id or "") != task.id:
        return "unknown"
    job_status = str(job.status or "").strip().lower()
    if job_status in {"completed", "failed", "cancelled", "timeout", "rejected"}:
        return "observed_false"
    if job_status != "running" or job.started_at is None or job.finished_at is not None:
        return "observed_false" if job_status in {"created", "delegated", "queued"} else "unknown"
    if task_status not in {"in_progress", "delegated"} or str(job.worker_url or "") != str(
        task.assigned_agent_url or ""
    ):
        return "unknown"
    lease_id = str(job.slot_lease_id or "")
    lease = leases_by_id.get(lease_id)
    if lease is None:
        return "unknown"
    return (
        "observed_true"
        if _lease_is_active(
            lease,
            task_id=task.id,
            worker_job_id=job.id,
            now=now,
        )
        else "observed_false"
    )


def _lease_is_active(
    lease: WorkerSlotLeaseDB,
    *,
    task_id: str,
    worker_job_id: str,
    now: float,
) -> bool:
    return (
        str(lease.status or "") == "active"
        and str(lease.parent_task_id or "") == task_id
        and str(lease.worker_job_id or "") == worker_job_id
        and lease.released_at is None
        and float(lease.deadline_at) > now
    )


__all__ = [
    "WORKFLOW_BINDING_SCHEMA",
    "apply_runtime_observations",
    "hub_routed_fact",
    "task_ready_fact",
]
