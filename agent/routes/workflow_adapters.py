"""Hub API Blueprint für Workflow Adapter Management (LCG-034, LCG-053)."""
from __future__ import annotations

import json
from typing import Any

from flask import Blueprint, request, jsonify

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.common.audit import log_audit

workflow_adapters_bp = Blueprint(
    "workflow_adapters", __name__, url_prefix="/api/workflow_adapters"
)


def _get_registry() -> dict[str, Any]:
    from worker.adapters.workflow_adapter_registry import get_registry
    return get_registry()


# ── List adapters ──────────────────────────────────────────────────────────────

@workflow_adapters_bp.route("/", methods=["GET"])
def list_adapters():
    """List all registered workflow adapters with their status."""
    registry = _get_registry()
    result = [adapter.descriptor().as_dict() for adapter in registry.values()]
    return jsonify({"adapters": result, "count": len(result)})


# ── Adapter detail ─────────────────────────────────────────────────────────────

@workflow_adapters_bp.route("/<kind>/", methods=["GET"])
def get_adapter(kind: str):
    """Get descriptor for a specific adapter kind."""
    registry = _get_registry()
    adapter = registry.get(kind)
    if adapter is None:
        return api_response(status="error", message=f"unknown_adapter_kind:{kind}", code=404)
    return jsonify(adapter.descriptor().as_dict())


# ── Dry run ────────────────────────────────────────────────────────────────────

@workflow_adapters_bp.route("/<kind>/dry_run", methods=["POST"])
@check_auth
def dry_run(kind: str):
    """Run a dry-run for the given adapter kind."""
    registry = _get_registry()
    adapter = registry.get(kind)
    if adapter is None:
        return api_response(status="error", message=f"unknown_adapter_kind:{kind}", code=404)

    body = request.get_json(silent=True) or {}
    task_id = str(body.get("task_id") or "")
    task_type = str(body.get("task_type") or "")
    payload = body.get("payload") or {}

    if not task_id or not task_type:
        return api_response(status="error", message="task_id and task_type are required", code=400)

    result = adapter.dry_run(task_id=task_id, task_type=task_type, payload=payload)
    return jsonify(result.as_dict())


# ── Execute ────────────────────────────────────────────────────────────────────

@workflow_adapters_bp.route("/<kind>/execute", methods=["POST"])
@check_auth
def execute(kind: str):
    """Execute a workflow via the given adapter."""
    registry = _get_registry()
    adapter = registry.get(kind)
    if adapter is None:
        return api_response(status="error", message=f"unknown_adapter_kind:{kind}", code=404)

    body = request.get_json(silent=True) or {}
    task_id = str(body.get("task_id") or "")
    task_type = str(body.get("task_type") or "")
    payload = body.get("payload") or {}
    resume_token = body.get("resume_token")

    if not task_id or not task_type:
        return api_response(status="error", message="task_id and task_type are required", code=400)

    log_audit("workflow_adapter_execute", {
        "kind": kind,
        "task_id": task_id,
        "task_type": task_type,
    })

    # Support resume_token for LangGraph adapters
    if resume_token is not None and hasattr(adapter, "execute"):
        import inspect
        sig = inspect.signature(adapter.execute)
        if "resume_token" in sig.parameters:
            result = adapter.execute(
                task_id=task_id, task_type=task_type,
                payload=payload, resume_token=resume_token,
            )
        else:
            result = adapter.execute(task_id=task_id, task_type=task_type, payload=payload)
    else:
        result = adapter.execute(task_id=task_id, task_type=task_type, payload=payload)

    return jsonify(result.as_dict())


# ── Stream (SSE) ───────────────────────────────────────────────────────────────

@workflow_adapters_bp.route("/<kind>/stream", methods=["GET"])
@check_auth
def stream(kind: str):
    """SSE stream for workflow adapter execution (LCG-053)."""
    registry = _get_registry()
    adapter = registry.get(kind)
    if adapter is None:
        return api_response(status="error", message=f"unknown_adapter_kind:{kind}", code=404)

    task_id = request.args.get("task_id", "")
    task_type = request.args.get("task_type", "")
    payload_raw = request.args.get("payload", "{}")
    try:
        payload = json.loads(payload_raw)
    except (ValueError, TypeError):
        return api_response(status="error", message="invalid payload JSON", code=400)

    if not task_id or not task_type:
        return api_response(status="error", message="task_id and task_type are required", code=400)

    # Pre-check dry_run before opening the stream
    dry = adapter.dry_run(task_id=task_id, task_type=task_type, payload=payload)
    if dry.blocked:
        return api_response(status="error", message=f"blocked:{dry.block_reason}", code=403)

    def generate():
        import json as _json
        # Use stream() if adapter supports it
        if hasattr(adapter, "stream"):
            try:
                for event in adapter.stream(
                    task_id=task_id, task_type=task_type, payload=payload
                ):
                    yield f"data: {_json.dumps(event)}\n\n"
            except Exception as exc:
                yield f"data: {_json.dumps({'event_type': 'error', 'error': str(exc)})}\n\n"
        else:
            # Fallback: batch execute and emit single event
            try:
                result = adapter.execute(task_id=task_id, task_type=task_type, payload=payload)
                yield f"data: {_json.dumps({'event_type': 'stream_end', **result.as_dict()})}\n\n"
            except Exception as exc:
                yield f"data: {_json.dumps({'event_type': 'error', 'error': str(exc)})}\n\n"

    from flask import Response
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
