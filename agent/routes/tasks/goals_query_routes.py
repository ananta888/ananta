from typing import Any

from flask import g, request

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.models import GoalPlanNodePatchRequest
from agent.routes.tasks.goals import goals_bp
from agent.routes.tasks.goals_helpers import (
    _can_access_goal,
    _current_username,
    _goal_service,
    _is_admin_request,
    _maybe_recover_stalled_planning_goal,
    _repos,
    _services,
)
from agent.utils import validate_request


@goals_bp.route("/goals/readiness", methods=["GET"])
@check_auth
def goals_readiness():
    return api_response(data=_goal_service().goal_readiness())


@goals_bp.route("/goals/modes", methods=["GET"])
@check_auth
def list_goal_modes():
    return api_response(data=_goal_service().get_guided_modes())


@goals_bp.route("/goals", methods=["GET"])
@check_auth
def list_goals():
    return api_response(data=[_goal_service().serialize_goal(goal) for goal in _repos().goal_repo.get_all() if _can_access_goal(goal)])


@goals_bp.route("/goals/<goal_id>", methods=["GET"])
@check_auth
def get_goal(goal_id: str):
    goal = _repos().goal_repo.get_by_id(goal_id)
    if not goal or not _can_access_goal(goal):
        return api_response(status="error", message="not_found", code=404)
    return api_response(data=_goal_service().serialize_goal(goal))


@goals_bp.route("/goals/<goal_id>/detail", methods=["GET"])
@check_auth
def get_goal_detail(goal_id: str):
    goal = _repos().goal_repo.get_by_id(goal_id)
    if not goal or not _can_access_goal(goal):
        return api_response(status="error", message="not_found", code=404)
    goal = _maybe_recover_stalled_planning_goal(goal)
    return api_response(data=_goal_service().goal_detail(goal, is_admin=_is_admin_request()))


@goals_bp.route("/goals/<goal_id>/effective-config", methods=["GET"])
@check_auth
def get_goal_effective_config(goal_id: str):
    goal = _repos().goal_repo.get_by_id(goal_id)
    if not goal or not _can_access_goal(goal):
        return api_response(status="error", message="not_found", code=404)
    execution_preferences = dict(getattr(goal, "execution_preferences", None) or {})
    snapshot = dict(execution_preferences.get("config_snapshot") or {})
    return api_response(
        data={
            "goal_id": str(getattr(goal, "id", "") or ""),
            "config_snapshot": snapshot,
            "provenance": dict(execution_preferences.get("config_snapshot_provenance") or snapshot.get("provenance") or {}),
            "config_checksum": str(execution_preferences.get("config_snapshot_checksum") or "").strip() or None,
            "config_snapshot_hash": str(execution_preferences.get("config_snapshot_hash") or "").strip() or None,
            "redaction_summary": dict(execution_preferences.get("config_redaction_summary") or {}),
            "goal_config_source": "snapshot" if snapshot else "global_fallback",
        }
    )


@goals_bp.route("/goals/<goal_id>/plan", methods=["GET"])
@check_auth
def get_goal_plan(goal_id: str):
    goal = _repos().goal_repo.get_by_id(goal_id)
    if not goal or not _can_access_goal(goal):
        return api_response(status="error", message="not_found", code=404)
    plan, nodes = _services().planning_service.get_latest_plan_for_goal(goal_id)
    if not plan:
        return api_response(status="error", message="plan_not_found", code=404)
    return api_response(
        data={
            "goal_id": goal_id,
            "plan": plan.model_dump(),
            "nodes": [node.model_dump() for node in nodes],
        }
    )


@goals_bp.route("/goals/<goal_id>/plan/nodes/<node_id>", methods=["PATCH"])
@check_auth
@validate_request(GoalPlanNodePatchRequest)
def patch_goal_plan_node(goal_id: str, node_id: str):
    goal = _repos().goal_repo.get_by_id(goal_id)
    if not goal or not _can_access_goal(goal):
        return api_response(status="error", message="not_found", code=404)
    if not _is_admin_request():
        return api_response(status="error", message="forbidden", code=403)
    payload = g.validated_data.model_dump(exclude_unset=True)
    data, error = _services().planning_service.patch_plan_node(goal_id, node_id, payload)
    if error:
        code = 404 if error in {"plan_not_found", "node_not_found"} else 400
        return api_response(status="error", message=error, code=code)
    plan, _ = _services().planning_service.get_latest_plan_for_goal(goal_id)
    log_audit(
        "plan_node_updated",
        {
            "goal_id": goal_id,
            "plan_id": plan.id if plan else None,
            "trace_id": goal.trace_id,
            "node_id": node_id,
            "changes": payload,
        },
    )
    return api_response(data=data)


@goals_bp.route("/goals/<goal_id>/governance-summary", methods=["GET"])
@check_auth
def goal_governance_summary(goal_id: str):
    goal = _repos().goal_repo.get_by_id(goal_id)
    if not goal or not _can_access_goal(goal):
        return api_response(status="error", message="not_found", code=404)
    summary = _services().verification_service.governance_summary(goal_id, include_sensitive=_is_admin_request())
    if not summary:
        return api_response(status="error", message="not_found", code=404)
    return api_response(data=_goal_service().sanitize_governance_summary(summary, _is_admin_request()))


@goals_bp.route("/goals/<goal_id>/gates/<gate_task_id>/human-decision", methods=["POST"])
@check_auth
def goal_gate_human_decision(goal_id: str, gate_task_id: str):
    goal = _repos().goal_repo.get_by_id(goal_id)
    if not goal or not _can_access_goal(goal):
        return api_response(status="error", message="not_found", code=404)
    body = request.get_json(silent=True) or {}
    outcome = str(body.get("outcome") or "").strip()
    operator = str(body.get("operator") or _current_username() or "").strip()
    reason = str(body.get("reason") or "").strip()
    from agent.services.human_approval_service import (
        HumanApprovalError,
        submit_human_decision_via_repo,
    )
    try:
        block = submit_human_decision_via_repo(
            goal_id=goal_id,
            gate_task_id=gate_task_id,
            operator=operator,
            outcome=outcome,
            reason=reason,
        )
    except HumanApprovalError as exc:
        return api_response(status="error", message=str(exc), code=400)
    return api_response(data={"decision": block})


@goals_bp.route("/goals/<goal_id>/workflow-status", methods=["GET"])
@check_auth
def goal_workflow_status(goal_id: str):
    goal = _repos().goal_repo.get_by_id(goal_id)
    if not goal or not _can_access_goal(goal):
        return api_response(status="error", message="not_found", code=404)
    from agent.services.workflow_status_service import (
        build_workflow_status, debug_workflow_status,
    )
    from agent.repository import task_repo
    try:
        tasks = list(task_repo.list_by_goal(goal_id) or [])
    except Exception:
        tasks = []
    plan_id = str(getattr(goal, "plan_id", "") or "")
    blueprint_id = str(getattr(goal, "blueprint_id", "") or "")
    blueprint_version = str(getattr(goal, "blueprint_version", "") or "")
    steps: list = []
    produced_artifact_keys: list = []
    try:
        from agent.artifacts.goal_artifact_service import GoalArtifactService
        graph = GoalArtifactService().get_goal_graph(goal_id)
        for output in list(graph.get("output_artifacts") or []):
            if isinstance(output, dict):
                if not plan_id:
                    plan_id = str(output.get("output_artifact_id") or "")
                payload = dict((output.get("extensions") or {}).get("payload") or {})
                for t in list(payload.get("tasks") or []):
                    if isinstance(t, dict):
                        steps.append(t)
                        for prod in list(t.get("produces") or []):
                            if isinstance(prod, str):
                                produced_artifact_keys.append(prod)
    except Exception:
        pass
    if str(request.args.get("debug") or "").strip() in {"1", "true", "yes"}:
        text = debug_workflow_status(
            goal_id=goal_id,
            steps=steps,
            tasks=tasks,
            produced_artifact_keys=produced_artifact_keys,
            plan_id=plan_id,
            blueprint_id=blueprint_id,
            blueprint_version=blueprint_version,
        )
        return api_response(data={"text": text, "goal_id": goal_id})
    payload = build_workflow_status(
        goal_id=goal_id,
        steps=steps,
        tasks=tasks,
        produced_artifact_keys=produced_artifact_keys,
        plan_id=plan_id,
        blueprint_id=blueprint_id,
        blueprint_version=blueprint_version,
    )
    return api_response(data=payload)


@goals_bp.route("/goals/<goal_id>/purge", methods=["DELETE"])
@check_auth
def purge_goal(goal_id: str):
    from agent.services.goal_purge_service import get_goal_purge_service
    goal = _repos().goal_repo.get_by_id(goal_id)
    if not goal:
        return api_response(data={"goal_id": goal_id, "already_deleted": True, "deleted_total": 0})
    if not _can_access_goal(goal):
        return api_response(status="error", message="not_found", code=404)
    if not _is_admin_request():
        return api_response(status="error", message="forbidden", code=403)
    include_prompt_traces = str(request.args.get("include_prompt_traces", "1")).strip().lower() not in {"0", "false", "no"}
    result = get_goal_purge_service().purge_goal(goal_id, include_prompt_traces=include_prompt_traces)
    if result is None:
        return api_response(data={"goal_id": goal_id, "already_deleted": True, "deleted_total": 0})
    log_audit(
        "goal_purged",
        {
            "goal_id": goal_id,
            "trace_id": getattr(goal, "trace_id", None),
            "include_prompt_traces": include_prompt_traces,
            "deleted": result.to_dict(),
        },
    )
    return api_response(data=result.to_dict())


@goals_bp.route("/goals/<goal_id>/kill-requests", methods=["POST"])
@check_auth
def kill_goal_requests(goal_id: str):
    from agent.services.request_cancellation_service import get_request_cancellation_service
    if not _is_admin_request():
        return api_response(status="error", message="forbidden", code=403)
    goal_id_norm = str(goal_id or "").strip()
    if not goal_id_norm:
        return api_response(status="error", message="goal_id required", code=400)
    result = get_request_cancellation_service().cancel_goal_requests(goal_id=goal_id_norm, include_workers=True)
    return api_response(data=result)


@goals_bp.route("/internal/goals/<goal_id>/kill-requests", methods=["POST"])
@check_auth
def kill_goal_requests_internal(goal_id: str):
    from agent.services.request_cancellation_service import get_request_cancellation_service
    goal_id_norm = str(goal_id or "").strip()
    if not goal_id_norm:
        return api_response(status="error", message="goal_id required", code=400)
    result = get_request_cancellation_service().cancel_goal_requests(goal_id=goal_id_norm, include_workers=False)
    return api_response(data=result)


@goals_bp.route("/goals/kill-all-requests", methods=["POST"])
@check_auth
def kill_all_requests():
    from agent.services.request_cancellation_service import get_request_cancellation_service
    if not _is_admin_request():
        return api_response(status="error", message="forbidden", code=403)
    result = get_request_cancellation_service().cancel_all_requests(include_workers=True)
    return api_response(data=result)


@goals_bp.route("/internal/goals/kill-all-requests", methods=["POST"])
@check_auth
def kill_all_requests_internal():
    from agent.services.request_cancellation_service import get_request_cancellation_service
    result = get_request_cancellation_service().cancel_all_requests(include_workers=False)
    return api_response(data=result)
