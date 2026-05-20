"""Debug API: GET /debug/llm-requests, GET /debug/llm-requests/{trace_id}, GET /goals/{id}/prompt-traces. PTI-015, PTI-016, PTI-017."""
from __future__ import annotations

import logging

from flask import Blueprint, g, request

from agent.auth import check_auth
from agent.common.errors import api_response

logger = logging.getLogger(__name__)

prompt_traces_bp = Blueprint("debug_prompt_traces", __name__)


def _is_admin() -> bool:
    """Heuristic: admin if auth passed and role is admin or running locally."""
    try:
        remote = request.remote_addr or ""
        if remote in ("127.0.0.1", "::1", "localhost"):
            return True
        return bool(getattr(g, "auth_role", None) in ("admin", "hub"))
    except Exception:
        return False


def _is_local() -> bool:
    try:
        remote = request.remote_addr or ""
        return remote in ("127.0.0.1", "::1", "localhost")
    except Exception:
        return False


def _trace_to_list_item(trace) -> dict:
    prompt_preview = ""
    if trace.final_prompt_redacted:
        prompt_preview = trace.final_prompt_redacted[:200]
    elif trace.messages_redacted:
        last = trace.messages_redacted[-1] if trace.messages_redacted else {}
        prompt_preview = str(last.get("content") or "")[:200]

    return {
        "trace_id": trace.trace_id,
        "request_id": trace.request_id,
        "goal_id": trace.goal_id,
        "task_id": trace.task_id,
        "provider": trace.provider,
        "model": trace.model,
        "request_kind": trace.request_kind,
        "source_component": trace.source_component,
        "success": trace.success,
        "latency_ms": trace.latency_ms,
        "created_at": trace.created_at,
        "prompt_preview_redacted": prompt_preview,
        "prompt_hash_sha256": trace.prompt_hash_sha256,
        "secrets_detected": trace.secrets_detected,
        "redaction_applied": trace.redaction_applied,
    }


def _trace_to_detail(trace, *, include_raw: bool = False) -> dict:
    from agent.services.prompt_trace_access_policy import get_trace_access_policy
    policy = get_trace_access_policy()

    detail: dict = {
        "trace_id": trace.trace_id,
        "request_id": trace.request_id,
        "idempotency_key": trace.idempotency_key,
        "goal_id": trace.goal_id,
        "task_id": trace.task_id,
        "worker_id": trace.worker_id,
        "source_component": trace.source_component,
        "provider": trace.provider,
        "transport_provider": trace.transport_provider,
        "model": trace.model,
        "endpoint_kind": trace.endpoint_kind,
        "request_kind": trace.request_kind,
        "final_prompt_redacted": trace.final_prompt_redacted,
        "messages_redacted": trace.messages_redacted,
        "prompt_hash_sha256": trace.prompt_hash_sha256,
        "raw_available": trace.raw_available,
        "template_chain": trace.template_chain,
        "overlay_chain": trace.overlay_chain,
        "model_profile": trace.model_profile,
        "optimizer_steps": trace.optimizer_steps,
        "context_sources": trace.context_sources,
        "tool_definitions_hash": trace.tool_definitions_hash,
        "selected_tools": trace.selected_tools,
        "created_at": trace.created_at,
        "started_at": trace.started_at,
        "ended_at": trace.ended_at,
        "latency_ms": trace.latency_ms,
        "success": trace.success,
        "error_type": trace.error_type,
        "error_message": trace.error_message,
        "usage": trace.usage,
        "response_hash_sha256": trace.response_hash_sha256,
        "redaction_policy_id": trace.redaction_policy_id,
        "redaction_applied": trace.redaction_applied,
        "secrets_detected": trace.secrets_detected,
        "raw_access_policy": trace.raw_access_policy,
        "sensitivity_level": trace.sensitivity_level,
        "llm_scope": trace.llm_scope,
    }

    if include_raw:
        decision = policy.check_raw_access(
            is_admin=_is_admin(),
            is_local=_is_local(),
            raw_available=trace.raw_available,
        )
        if decision.allowed:
            policy.audit_raw_access(trace.trace_id)
            detail["raw_access_granted"] = True
        else:
            detail["raw_access_denied"] = True
            detail["raw_access_denied_reason"] = decision.reason

    return detail


@prompt_traces_bp.route("/debug/llm-requests", methods=["GET"])
@check_auth
def list_llm_requests():
    """List recent PromptTraces with optional filters."""
    from agent.services.prompt_trace_service import get_prompt_trace_service

    try:
        limit = min(int(request.args.get("limit") or 50), 500)
    except (ValueError, TypeError):
        limit = 50

    filters = {}
    for key in ("provider", "model", "goal_id", "task_id", "worker_id"):
        val = request.args.get(key)
        if val:
            filters[key] = val

    success_param = request.args.get("success")
    if success_param is not None:
        filters["success"] = success_param.lower() in ("1", "true", "yes")

    since_param = request.args.get("since")
    if since_param:
        try:
            filters["since"] = float(since_param)
        except (ValueError, TypeError):
            pass

    svc = get_prompt_trace_service()
    traces = svc.list_traces(limit=limit, **filters)
    return api_response(
        data={"traces": [_trace_to_list_item(t) for t in traces], "count": len(traces)},
        status=200,
    )


@prompt_traces_bp.route("/debug/llm-requests/<trace_id>", methods=["GET"])
@check_auth
def get_llm_request(trace_id: str):
    """Get a single PromptTrace by trace_id."""
    from agent.services.prompt_trace_service import get_prompt_trace_service

    include_raw = request.args.get("include_raw", "").lower() in ("1", "true", "yes")

    svc = get_prompt_trace_service()
    trace = svc.get_trace(trace_id)
    if trace is None:
        return api_response(data={"error": "trace_not_found", "trace_id": trace_id}, status=404)

    return api_response(data=_trace_to_detail(trace, include_raw=include_raw), status=200)


@prompt_traces_bp.route("/goals/<goal_id>/prompt-traces", methods=["GET"])
@check_auth
def get_goal_prompt_traces(goal_id: str):
    """List all PromptTraces for a goal, grouped by request_kind."""
    from agent.services.prompt_trace_service import get_prompt_trace_service

    limit = min(int(request.args.get("limit") or 100), 500)
    svc = get_prompt_trace_service()
    traces = svc.find_by_goal_id(goal_id, limit=limit)

    grouped: dict[str, list] = {}
    for t in traces:
        kind = t.request_kind or "unknown"
        grouped.setdefault(kind, []).append(_trace_to_list_item(t))

    return api_response(
        data={"goal_id": goal_id, "traces": grouped, "total": len(traces)},
        status=200,
    )
