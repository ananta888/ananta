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
    choose_worker_for_task,
    compute_lease_expiry,
    extract_active_lease,
    persist_policy_decision,
)
from agent.routes.tasks.status import normalize_task_status
from agent.routes.tasks.utils import _forward_to_worker, _get_local_task_status, _update_local_task_status
from agent.services.verification_service import get_verification_service

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

        selection = choose_worker_for_task(
            parent_task,
            [worker.model_dump() for worker in agent_repo.get_all()],
            task_kind=data.task_kind,
            required_capabilities=data.required_capabilities,
        )
        agent_url = selection.worker_url
        selected_by_policy = True
        if not agent_url:
            persist_policy_decision(
                decision_type="delegation",
                status="blocked",
                policy_name="worker_capability_routing",
                policy_version="worker-routing-v1",
                reasons=selection.reasons,
                details={"task_kind": data.task_kind, "required_capabilities": data.required_capabilities},
                task_id=tid,
            )
            return api_response(status="error", message="no_worker_available", data={"reasons": selection.reasons}, code=409)

    subtask_id = f"sub-{uuid.uuid4()}"
    my_url = settings.agent_url or f"http://localhost:{settings.port}"
    callback_url = f"{my_url.rstrip('/')}/tasks/{tid}/subtask-callback"

    delegation_payload = {
        "id": subtask_id,
        "description": data.subtask_description,
        "parent_task_id": tid,
        "priority": data.priority,
        "callback_url": callback_url,
        "callback_token": settings.agent_token or "",
        "source": "agent",
        "created_by": settings.agent_name or "hub",
    }
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
        subtasks=subtasks,
        event_type="task_delegated",
        event_actor="hub",
        event_details={
            "delegated_to": agent_url,
            "subtask_id": subtask_id,
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
    verification_status = {
        "record_id": record.id if record else None,
        "status": record.status if record else ("passed" if all_passed else "failed"),
        "retry_count": record.retry_count if record else 0,
        "repair_attempts": record.repair_attempts if record else 0,
        "escalation_reason": record.escalation_reason if record else None,
    }
    _update_local_task_status(
        tid,
        final_status,
        last_output=str(payload.get("output") or ""),
        last_exit_code=0 if all_passed else 1,
        verification_status=verification_status,
        event_type="task_completed_with_gates",
        event_actor=str(payload.get("actor") or "system"),
        event_details={"gate_results": gate, "trace_id": payload.get("trace_id")},
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
