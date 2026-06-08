"""Task Engine read-model API (te-013).

GET  /api/task-engine/status   — current classification status (polling / SSE-ready)
POST /api/task-engine/classify — classify a task dict without executing anything
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent.services.task_class_resolver import TaskClassResolver
from agent.services.task_engine_policy_gate import TaskEnginePolicyGate
from agent.services.task_engine_status_service import get_task_engine_status_service
from agent.services.task_engine_trace import extract_te_summary
from agent.services.tool_scope_contract import ToolScopeContract

task_engine_bp = Blueprint("task_engine", __name__, url_prefix="/api/task-engine")


@task_engine_bp.get("/status")
def status():
    """Return the most recently processed task's classification."""
    svc = get_task_engine_status_service()
    return jsonify(svc.as_dict()), 200


@task_engine_bp.post("/classify")
def classify():
    """Dry-run classification: route a task dict through the policy gate without executing.

    Body: task dict (same shape as used in propose/execute flows)
    Returns: GateDecision fields + ToolScopeContract summary
    """
    body = request.get_json(silent=True) or {}
    task = body.get("task") or body  # accept task nested or flat

    gate = TaskEnginePolicyGate.from_settings()
    decision = gate.evaluate(task)

    resolver = TaskClassResolver()
    cr = resolver.resolve(task)

    scope = ToolScopeContract.from_task(task)

    return jsonify({
        "allow": decision.allow,
        "bypass_llm": decision.bypass_llm,
        "blocked": decision.blocked,
        "handler_id": decision.handler_id,
        "task_class": decision.task_class,
        "intent": decision.intent,
        "llm_required": decision.llm_required,
        "reason": decision.reason,
        "unknown_tools": decision.unknown_tools,
        "resolver": {
            "task_class": cr.task_class,
            "intent": cr.intent,
            "reason": cr.reason,
            "deterministic_handler_id": cr.deterministic_handler_id,
        },
        "tool_scope": scope.as_dict(),
    }), 200
