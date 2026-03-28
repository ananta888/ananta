from __future__ import annotations

import uuid

from flask import Blueprint, request

from agent.auth import check_auth
from agent.common.api_envelope import unwrap_api_envelope
from agent.common.errors import api_response
from agent.config import settings
from agent.models import TaskDelegationRequest
from agent.routes.tasks.orchestration_policy import (
    DelegationPolicy,
    build_orchestration_read_model,
    compute_lease_expiry,
    evaluate_worker_routing_policy,
    extract_active_lease,
    persist_policy_decision,
)
from agent.routes.tasks.status import normalize_task_status
from agent.routes.tasks.utils import _forward_to_worker, _get_local_task_status, _update_local_task_status
from agent.services.result_memory_service import get_result_memory_service
from agent.services.verification_service import get_verification_service
from agent.services.worker_job_service import get_worker_job_service

orchestration_bp = Blueprint("tasks_orchestration", __name__)

_policy = DelegationPolicy(role_provider=settings, required_role="hub")


@orchestration_bp.route("/tasks/<tid>/delegate", methods=["POST"])
@check_auth
def delegate_task(tid):
    violation = _policy.check_delegation_allowed()
    if violation:
        return api_response(status="error", message=violation, code=403)

    payload = request.get_json(silent=True) or {}
    try:
        data = TaskDelegationRequest.model_validate(payload)
    except Exception:
        return api_response(status="error", message="validation_failed", code=400)
    parent_task = _get_local_task_status(tid)
    if not parent_task:
        return api_response(status="error", message="parent_task_not_found", code=404)

    agent_url = data.agent_url
    selected_by_policy = False
    selection = None
    if not agent_url:
        from agent.repository import agent_repo

        selection, _decision = evaluate_worker_routing_policy(
            task=parent_task,
            workers=[worker.model_dump() for worker in agent_repo.get_all()],
            decision_type="delegation",
            task_kind=data.task_kind,
            required_capabilities=data.required_capabilities,
            task_id=tid,
        )
        agent_url = selection.worker_url
        selected_by_policy = True
        if not agent_url:
            return api_response(status="error", message="no_worker_available", data={"reasons": selection.reasons}, code=409)

    subtask_id = f"sub-{uuid.uuid4()}"
    my_url = settings.agent_url or f"http://localhost:{settings.port}"
    callback_url = f"{my_url.rstrip('/')}/tasks/{tid}/subtask-callback"
    context_query = str(data.context_query or "").strip() or " ".join(
        item
        for item in [
            str(parent_task.get("title") or "").strip(),
            str(parent_task.get("description") or "").strip(),
            str(data.subtask_description or "").strip(),
        ]
        if item
    )
    context_bundle = get_worker_job_service().create_context_bundle(
        query=context_query,
        parent_task_id=tid,
        goal_id=parent_task.get("goal_id"),
    )
    expected_output_schema = dict(data.expected_output_schema or {})
    allowed_tools = list(data.allowed_tools or [])
    worker_execution_context = {
        "instructions": data.subtask_description,
        "context_bundle_id": context_bundle.id,
        "context": {
            "context_text": context_bundle.context_text,
            "chunks": context_bundle.chunks,
            "token_estimate": context_bundle.token_estimate,
            "bundle_metadata": context_bundle.bundle_metadata,
        },
        "allowed_tools": allowed_tools,
        "expected_output_schema": expected_output_schema,
    }

    delegation_payload = {
        "id": subtask_id,
        "title": data.subtask_description[:200],
        "description": data.subtask_description,
        "parent_task_id": tid,
        "priority": data.priority,
        "team_id": parent_task.get("team_id"),
        "goal_id": parent_task.get("goal_id"),
        "goal_trace_id": parent_task.get("goal_trace_id"),
        "task_kind": data.task_kind or parent_task.get("task_kind"),
        "required_capabilities": data.required_capabilities or parent_task.get("required_capabilities") or [],
        "context_bundle_id": context_bundle.id,
        "worker_execution_context": worker_execution_context,
        "callback_url": callback_url,
        "callback_token": settings.agent_token or "",
        "source": "agent",
        "created_by": settings.agent_name or "hub",
    }
    worker_job = get_worker_job_service().create_worker_job(
        parent_task_id=tid,
        subtask_id=subtask_id,
        worker_url=agent_url,
        context_bundle_id=context_bundle.id,
        allowed_tools=allowed_tools,
        expected_output_schema=expected_output_schema,
        metadata={
            "selected_by_policy": selected_by_policy,
            "task_kind": data.task_kind or parent_task.get("task_kind"),
            "required_capabilities": data.required_capabilities or parent_task.get("required_capabilities") or [],
        },
    )
    try:
        persist_policy_decision(
            decision_type="delegation",
            status="approved",
            policy_name="worker_capability_routing",
            policy_version="worker-routing-v1",
            reasons=(selection.reasons if selection else ["manual_override"]),
            details={
                "task_kind": data.task_kind,
                "required_capabilities": data.required_capabilities,
                "manual_override": not selected_by_policy,
            },
            task_id=tid,
            worker_url=agent_url,
        )
        res = _forward_to_worker(agent_url, "/tasks", delegation_payload, token=data.agent_token or "")
        res = unwrap_api_envelope(res)
    except Exception as exc:
        return api_response(status="error", message="delegation_failed", data={"details": str(exc)}, code=502)

    subtasks = parent_task.get("subtasks", [])
    subtasks.append(
        {
            "id": subtask_id,
            "agent_url": agent_url,
            "description": data.subtask_description,
            "status": "created",
        }
    )
    _update_local_task_status(
        tid,
        parent_task.get("status", "in_progress"),
        context_bundle_id=context_bundle.id,
        current_worker_job_id=worker_job.id,
        worker_execution_context=worker_execution_context,
        subtasks=subtasks,
        event_type="task_delegated",
        event_actor="hub",
        event_details={
            "delegated_to": agent_url,
            "subtask_id": subtask_id,
            "context_bundle_id": context_bundle.id,
            "worker_job_id": worker_job.id,
            "policy": "hub_central_queue",
            "selected_by_policy": selected_by_policy,
        },
    )
    return api_response(
        data={
            "status": "delegated",
            "subtask_id": subtask_id,
            "agent_url": agent_url,
            "response": res,
            "selected_by_policy": selected_by_policy,
            "selection_reasons": selection.reasons if selection else ["manual_override"],
            "context_bundle_id": context_bundle.id,
            "worker_job_id": worker_job.id,
        }
    )


@orchestration_bp.route("/tasks/orchestration/ingest", methods=["POST"])
@check_auth
def ingest_task():
    payload = request.get_json(silent=True) or {}
    description = str(payload.get("description") or "").strip()
    if not description:
        return api_response(status="error", message="description_required", code=400)
    tid = str(payload.get("id") or f"tsk-{uuid.uuid4()}")
    source = str(payload.get("source") or "ui").strip().lower()
    created_by = str(payload.get("created_by") or "unknown").strip()
    priority = str(payload.get("priority") or "medium")
    _update_local_task_status(
        tid,
        normalize_task_status(str(payload.get("status") or "todo"), default="todo"),
        title=str(payload.get("title") or "")[:200] or None,
        description=description,
        priority=priority,
        event_type="task_ingested",
        event_actor=created_by or "unknown",
        event_details={"source": source, "channel": "central_task_management"},
    )
    return api_response(data={"id": tid, "ingested": True, "source": source})


@orchestration_bp.route("/tasks/orchestration/claim", methods=["POST"])
@check_auth
def claim_task():
    payload = request.get_json(silent=True) or {}
    tid = str(payload.get("task_id") or "").strip()
    agent_url = str(payload.get("agent_url") or "").strip()
    idempotency_key = str(payload.get("idempotency_key") or "").strip()
    if not tid or not agent_url:
        return api_response(status="error", message="task_id_and_agent_url_required", code=400)
    task = _get_local_task_status(tid)
    if not task:
        return api_response(status="error", message="not_found", code=404)

    can_claim, error_msg = _policy.can_claim_task(task, agent_url)
    if not can_claim:
        lease_info = extract_active_lease(task)
        persist_policy_decision(
            decision_type="execution_claim",
            status="blocked",
            policy_name="task_claim_policy",
            policy_version="claim-v1",
            reasons=[error_msg or "claim_denied"],
            details={"agent_url": agent_url},
            task_id=tid,
            worker_url=agent_url,
        )
        return api_response(
            status="error",
            message=error_msg or "claim_denied",
            data={"lease": lease_info.__dict__ if lease_info else {}},
            code=409,
        )

    requested_lease = int(payload.get("lease_seconds") or 120)
    lease_seconds = _policy.validate_lease_duration(requested_lease)
    lease_until = compute_lease_expiry(lease_seconds)
    persist_policy_decision(
        decision_type="execution_claim",
        status="approved",
        policy_name="task_claim_policy",
        policy_version="claim-v1",
        reasons=["lease_granted"],
        details={"agent_url": agent_url, "lease_seconds": lease_seconds},
        task_id=tid,
        worker_url=agent_url,
    )

    _update_local_task_status(
        tid,
        "assigned",
        assigned_agent_url=agent_url,
        event_type="task_claimed",
        event_actor=agent_url,
        event_details={"agent_url": agent_url, "lease_until": lease_until, "idempotency_key": idempotency_key},
    )
    return api_response(data={"task_id": tid, "claimed": True, "lease_until": lease_until})


@orchestration_bp.route("/tasks/orchestration/complete", methods=["POST"])
@check_auth
def complete_task():
    payload = request.get_json(silent=True) or {}
    tid = str(payload.get("task_id") or "").strip()
    if not tid:
        return api_response(status="error", message="task_id_required", code=400)
    task = _get_local_task_status(tid)
    if not task:
        return api_response(status="error", message="not_found", code=404)
    gate = payload.get("gate_results") or {}
    all_passed = bool(gate.get("passed", False))
    final_status = "completed" if all_passed else "failed"
    record = get_verification_service().create_or_update_record(
        tid,
        trace_id=payload.get("trace_id"),
        output=str(payload.get("output") or ""),
        exit_code=0 if all_passed else 1,
        gate_results=gate,
    )
    worker_job_id = str(payload.get("worker_job_id") or task.get("current_worker_job_id") or "").strip() or None
    actor = str(payload.get("actor") or "system")
    if worker_job_id:
        get_worker_job_service().record_worker_result(
            worker_job_id=worker_job_id,
            task_id=tid,
            worker_url=actor,
            status=final_status,
            output=str(payload.get("output") or ""),
            metadata={"gate_results": gate, "trace_id": payload.get("trace_id")},
        )
    memory_entry = get_result_memory_service().record_worker_result_memory(
        task_id=tid,
        goal_id=task.get("goal_id"),
        trace_id=payload.get("trace_id") or task.get("goal_trace_id"),
        worker_job_id=worker_job_id,
        title=task.get("title"),
        output=str(payload.get("output") or ""),
        artifact_refs=[{"kind": "task_output", "task_id": tid, "worker_job_id": worker_job_id}],
        retrieval_tags=[
            value
            for value in [
                str(task.get("task_kind") or "").strip(),
                str(task.get("goal_id") or "").strip(),
                str(final_status).strip(),
            ]
            if value
        ],
        metadata={"gate_results": gate, "actor": actor},
    )
    verification_status = {
        "record_id": record.id if record else None,
        "status": record.status if record else ("passed" if all_passed else "failed"),
        "retry_count": record.retry_count if record else 0,
        "repair_attempts": record.repair_attempts if record else 0,
        "escalation_reason": record.escalation_reason if record else None,
        "memory_entry_id": memory_entry.id if memory_entry else None,
    }
    _update_local_task_status(
        tid,
        final_status,
        last_output=str(payload.get("output") or ""),
        last_exit_code=0 if all_passed else 1,
        verification_status=verification_status,
        event_type="task_completed_with_gates",
        event_actor=actor,
        event_details={"gate_results": gate, "trace_id": payload.get("trace_id"), "worker_job_id": worker_job_id},
    )
    return api_response(
        data={
            "task_id": tid,
            "status": final_status,
            "gates_passed": all_passed,
            "verification_status": verification_status,
        }
    )


@orchestration_bp.route("/tasks/orchestration/read-model", methods=["GET"])
@check_auth
def orchestration_read_model():
    from agent.repository import policy_decision_repo, task_repo

    tasks = [t.model_dump() for t in task_repo.get_all()]
    model = build_orchestration_read_model(tasks)
    model["recent_policy_decisions"] = [item.model_dump() for item in policy_decision_repo.get_all(limit=50)]
    return api_response(data=model)
