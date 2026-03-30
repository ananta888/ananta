from __future__ import annotations

import uuid

from flask import Blueprint, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.config import settings
from agent.models import TaskClaimRequest, TaskDelegationRequest
from agent.routes.tasks.orchestration_policy import (
    DelegationPolicy,
    compute_lease_expiry,
    extract_active_lease,
    persist_policy_decision,
)
from agent.services.task_execution_tracking_service import get_task_execution_tracking_service
from agent.services.service_registry import get_core_services

orchestration_bp = Blueprint("tasks_orchestration", __name__)

_policy = DelegationPolicy(role_provider=settings, required_role="hub")


def _services():
    return get_core_services()


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
    parent_task = _services().task_runtime_service.get_local_task_status(tid)
    if not parent_task:
        return api_response(status="error", message="parent_task_not_found", code=404)
    result = _services().task_orchestration_service.delegate_task(
        task_id=tid,
        data=data,
        worker_job_service=_services().worker_job_service,
        worker_contract_service=_services().worker_contract_service,
        agent_registry_service=_services().agent_registry_service,
        result_memory_service=_services().result_memory_service,
        verification_service=_services().verification_service,
    )
    if result.get("error"):
        return api_response(status="error", message=result["error"], data=result.get("data"), code=result.get("code", 400))
    return api_response(data=result["data"])


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
    _services().task_queue_service.ingest_task(
        task_id=tid,
        status=str(payload.get("status") or "todo"),
        title=str(payload.get("title") or ""),
        description=description,
        priority=priority,
        created_by=created_by,
        source=source,
    )
    return api_response(data={"id": tid, "ingested": True, "source": source})


@orchestration_bp.route("/tasks/orchestration/claim", methods=["POST"])
@check_auth
def claim_task():
    payload = request.get_json(silent=True) or {}
    try:
        data = TaskClaimRequest.model_validate(payload)
    except Exception:
        return api_response(status="error", message="task_id_and_agent_url_required", code=400)
    result = _services().task_claim_service.claim_task(
        task_id=data.task_id,
        agent_url=data.agent_url,
        requested_lease=int(data.lease_seconds or 120),
        idempotency_key=str(data.idempotency_key or "").strip(),
        policy=_policy,
        task_queue_service=_services().task_queue_service,
    )
    if result.get("error"):
        return api_response(status="error", message=result["error"], data=result.get("data"), code=result.get("code", 400))
    return api_response(data=result["data"])


@orchestration_bp.route("/tasks/orchestration/complete", methods=["POST"])
@check_auth
def complete_task():
    payload = request.get_json(silent=True) or {}
    tid = str(payload.get("task_id") or "").strip()
    if not tid:
        return api_response(status="error", message="task_id_required", code=400)
    task = _services().task_runtime_service.get_local_task_status(tid)
    if not task:
        return api_response(status="error", message="not_found", code=404)
    result = _services().task_orchestration_service.complete_task(
        task_id=tid,
        payload=payload,
        verification_service=_services().verification_service,
        worker_job_service=_services().worker_job_service,
        result_memory_service=_services().result_memory_service,
    )
    if result.get("error"):
        return api_response(status="error", message=result["error"], data=result.get("data"), code=result.get("code", 400))
    return api_response(data=result["data"])


@orchestration_bp.route("/tasks/orchestration/read-model", methods=["GET"])
@check_auth
def orchestration_read_model():
    payload = _services().task_claim_service.orchestration_read_model(task_queue_service=_services().task_queue_service)
    payload["worker_execution_reconciliation"] = get_task_execution_tracking_service().build_execution_reconciliation_snapshot()
    return api_response(data=payload)
