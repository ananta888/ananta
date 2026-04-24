from __future__ import annotations

import uuid

from flask import Blueprint, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.config import settings
from agent.models import TaskClaimRequest, TaskCreateRequest, TaskDelegationRequest
from agent.routes.tasks.orchestration_policy import (
    DelegationPolicy,
)
from agent.services.service_registry import get_core_services
from agent.services.task_execution_tracking_service import get_task_execution_tracking_service

orchestration_bp = Blueprint("tasks_orchestration", __name__)

_policy = DelegationPolicy(role_provider=settings, required_role="hub")


def _services():
    return get_core_services()


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return None


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
    task_request = TaskCreateRequest.model_validate(
        {
            "id": str(payload.get("id") or f"tsk-{uuid.uuid4()}"),
            "title": str(payload.get("title") or "") or None,
            "description": description,
            "status": str(payload.get("status") or "todo"),
            "priority": str(payload.get("priority") or "medium"),
            "team_id": payload.get("team_id"),
            "tags": payload.get("tags") if isinstance(payload.get("tags"), list) else None,
            "parent_task_id": payload.get("parent_task_id"),
            "source_task_id": payload.get("source_task_id"),
            "derivation_reason": payload.get("derivation_reason"),
            "derivation_depth": payload.get("derivation_depth"),
            "depends_on": payload.get("depends_on") if isinstance(payload.get("depends_on"), list) else None,
            "goal_id": payload.get("goal_id"),
            "goal_trace_id": payload.get("goal_trace_id"),
            "task_kind": payload.get("task_kind"),
            "retrieval_intent": payload.get("retrieval_intent"),
            "required_context_scope": payload.get("required_context_scope"),
            "preferred_bundle_mode": payload.get("preferred_bundle_mode"),
            "required_capabilities": payload.get("required_capabilities")
            if isinstance(payload.get("required_capabilities"), list)
            else None,
            "context_bundle_id": payload.get("context_bundle_id"),
            "worker_execution_context": payload.get("worker_execution_context")
            if isinstance(payload.get("worker_execution_context"), dict)
            else None,
        }
    )
    source = str(payload.get("source") or "ui").strip().lower()
    created_by = str(payload.get("created_by") or "unknown").strip()
    result = _services().task_management_service.create_task(
        data=task_request,
        source=source,
        created_by=created_by,
    )
    if result.get("error"):
        return api_response(status="error", message=result["error"], code=result.get("code", 400))
    return api_response(data={**result.get("data", {}), "ingested": True, "source": source})


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
        evolution_service=_services().evolution_service,
    )
    if result.get("error"):
        return api_response(status="error", message=result["error"], data=result.get("data"), code=result.get("code", 400))
    return api_response(data=result["data"])


@orchestration_bp.route("/tasks/orchestration/read-model", methods=["GET"])
@check_auth
def orchestration_read_model():
    payload = _services().task_claim_service.orchestration_read_model(task_queue_service=_services().task_queue_service)
    tracking_service = get_task_execution_tracking_service()
    payload["worker_execution_reconciliation"] = tracking_service.build_execution_reconciliation_snapshot()
    payload["control_layer_observability"] = tracking_service.build_control_layer_observability_snapshot()
    overrides = {}
    artifact_flow_enabled = _parse_bool(request.args.get("artifact_flow_enabled"))
    if artifact_flow_enabled is not None:
        overrides["enabled"] = artifact_flow_enabled
    rag_enabled = _parse_bool(request.args.get("artifact_flow_rag_enabled"))
    if rag_enabled is not None:
        overrides["rag_enabled"] = rag_enabled
    rag_include_content = _parse_bool(request.args.get("artifact_flow_rag_include_content"))
    if rag_include_content is not None:
        overrides["rag_include_content"] = rag_include_content
    for query_key, cfg_key in (
        ("artifact_flow_rag_top_k", "rag_top_k"),
        ("artifact_flow_max_tasks", "max_tasks"),
        ("artifact_flow_max_worker_jobs_per_task", "max_worker_jobs_per_task"),
    ):
        raw = request.args.get(query_key)
        if raw is None:
            continue
        try:
            overrides[cfg_key] = int(raw)
        except (TypeError, ValueError):
            continue
    payload["artifact_flow"] = tracking_service.build_artifact_flow_read_model(overrides=overrides)
    return api_response(data=payload)
