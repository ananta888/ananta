from __future__ import annotations

from agent.common.audit import log_audit
from agent.db_models import PolicyDecisionDB
from agent.repository import agent_repo, policy_decision_repo, task_repo

from .models import WorkerSelection
from .routing import choose_worker_for_task, derive_required_capabilities, normalize_capabilities


def persist_policy_decision(
    decision_type: str,
    status: str,
    policy_name: str,
    policy_version: str,
    reasons: list[str] | None = None,
    details: dict | None = None,
    task_id: str | None = None,
    goal_id: str | None = None,
    trace_id: str | None = None,
    worker_url: str | None = None,
) -> PolicyDecisionDB:
    if task_id and (not trace_id or not goal_id):
        task = task_repo.get_by_id(task_id)
        if task:
            trace_id = trace_id or task.goal_trace_id
            goal_id = goal_id or task.goal_id
    decision = PolicyDecisionDB(
        task_id=task_id,
        goal_id=goal_id,
        trace_id=trace_id,
        decision_type=decision_type,
        status=status,
        worker_url=worker_url,
        policy_name=policy_name,
        policy_version=policy_version,
        reasons=list(reasons or []),
        details=dict(details or {}),
    )
    saved = policy_decision_repo.save(decision)
    log_audit(
        "policy_decision_recorded",
        {
            "task_id": task_id,
            "goal_id": goal_id,
            "trace_id": trace_id,
            "decision_type": decision_type,
            "status": status,
            "worker_url": worker_url,
            "policy_name": policy_name,
            "policy_version": policy_version,
            "policy_decision_id": saved.id,
            "reasons": list(reasons or []),
        },
    )
    return saved


def evaluate_worker_routing_policy(
    *,
    task: dict | None,
    workers: list[dict],
    decision_type: str,
    task_kind: str | None = None,
    required_capabilities: list[str] | None = None,
    task_id: str | None = None,
    goal_id: str | None = None,
    trace_id: str | None = None,
    policy_name: str = "worker_capability_routing",
    policy_version: str = "worker-routing-v2",
    extra_details: dict | None = None,
) -> tuple[WorkerSelection, PolicyDecisionDB]:
    selection = choose_worker_for_task(
        task,
        workers,
        task_kind=task_kind,
        required_capabilities=required_capabilities,
    )
    decision = persist_policy_decision(
        decision_type=decision_type,
        status="approved" if selection.worker_url else "blocked",
        policy_name=policy_name,
        policy_version=policy_version,
        reasons=selection.reasons,
        details={
            "task_kind": task_kind,
            "required_capabilities": required_capabilities,
            "selection_strategy": selection.strategy,
            **dict(extra_details or {}),
        },
        task_id=task_id,
        goal_id=goal_id,
        trace_id=trace_id,
        worker_url=selection.worker_url,
    )
    return selection, decision


def enforce_assignment_policy(
    task: dict,
    worker_url: str,
    *,
    task_kind: str | None = None,
    required_capabilities: list[str] | None = None,
) -> tuple[bool, list[str], dict]:
    worker = next((item.model_dump() for item in agent_repo.get_all() if item.url == worker_url), None)
    if not worker:
        return False, ["worker_not_found"], {}
    normalized_required = normalize_capabilities(required_capabilities) or derive_required_capabilities(task, task_kind)
    selection = choose_worker_for_task(task, [worker], task_kind=task_kind, required_capabilities=required_capabilities)
    if normalized_required and selection.strategy == "fallback":
        return False, selection.reasons or ["capability_mismatch"], worker
    if selection.worker_url != worker_url:
        return False, selection.reasons or ["capability_mismatch"], worker
    return True, selection.reasons or ["manual_override_allowed"], worker
