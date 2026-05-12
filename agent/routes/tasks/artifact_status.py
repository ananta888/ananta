"""Artifact-first task status endpoints. AFH-T021/T022."""
from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, jsonify, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.services.repository_registry import get_repository_registry
from agent.services.worker_output_collector_service import get_worker_output_collector_service
from agent.services.task_completion_policy_service import get_task_completion_policy_service

artifact_status_bp = Blueprint("artifact_status", __name__)


@artifact_status_bp.route("/api/tasks/<tid>/artifact-status", methods=["GET"])
@check_auth
def get_task_artifact_status(tid: str):
    """Return artifact-first completion status for a task.

    Includes: artifact_summary, completion_decision, reason_codes,
    manifest_status, verification_status, advisory_parse_status.
    """
    repos = get_repository_registry()
    task = repos.task_repo.get_by_id(tid)
    if not task:
        return api_response({"error": "task_not_found"}, status=404)

    task_dict = task.model_dump()
    verification_status = dict(task_dict.get("verification_status") or {})

    # Extract artifact-first metadata from last history event
    artifact_completion = None
    for evt in reversed(list(task_dict.get("history") or [])):
        if isinstance(evt, dict) and evt.get("event_type") in (
            "artifact_first_completion", "artifact_first_finalization", "artifact_reconciliation_applied"
        ):
            artifact_completion = dict(evt.get("details") or {})
            break

    # Extract propose strategy metadata from last_proposal or history
    last_proposal = dict(task_dict.get("last_proposal") or {})
    routing = dict(last_proposal.get("routing") or {})
    propose_strategy_meta = dict(routing.get("propose_strategy_meta") or {})

    # Also check most recent proposal_result history event for strategy meta
    if not propose_strategy_meta:
        for evt in reversed(list(task_dict.get("history") or [])):
            if isinstance(evt, dict) and evt.get("event_type") == "proposal_result":
                propose_strategy_meta = dict(evt.get("propose_strategy_meta") or {})
                if propose_strategy_meta:
                    break

    response = {
        "task_id": tid,
        "status": task_dict.get("status"),
        # Propose strategy observability (FA-T019)
        "attempted_strategies": propose_strategy_meta.get("attempted_strategies") or [],
        "selected_strategy": propose_strategy_meta.get("selected_strategy"),
        "proposal_status": propose_strategy_meta.get("proposal_status"),
        "proposal_reason": propose_strategy_meta.get("proposal_reason") or last_proposal.get("reason"),
        "normalization_format": propose_strategy_meta.get("normalization_format"),
        "effective_propose_policy": routing.get("task_kind"),
        # Artifact completion observability
        "artifact_summary": {
            "completion_decision": (artifact_completion or {}).get("completion_decision"),
            "reason_codes": (artifact_completion or {}).get("reason_codes") or [],
            "manifest_status": "valid" if (artifact_completion or {}).get("manifest_id") else "unknown",
            "artifact_ids": (artifact_completion or {}).get("artifact_ids") or [],
            "manifest_id": (artifact_completion or {}).get("manifest_id"),
            "verification_status": verification_status.get("status"),
        },
        "verification_status": verification_status.get("status"),
        "advisory_parse_status": (artifact_completion or {}).get("advisory_parse_status"),
        "artifact_first_completion": artifact_completion,
        "completion_decision": (artifact_completion or {}).get("completion_decision"),
    }
    return jsonify(response), 200


@artifact_status_bp.route("/api/tasks/<tid>/artifact-preview", methods=["GET"])
@check_auth
def get_task_artifact_preview(tid: str):
    """Preview expected artifacts and completion rules before execution. AFH-T022.

    Does NOT execute the worker.
    """
    repos = get_repository_registry()
    task = repos.task_repo.get_by_id(tid)
    if not task:
        return api_response({"error": "task_not_found"}, status=404)

    task_dict = task.model_dump()
    workspace_dir = str(task_dict.get("workspace_dir") or "")
    todo_contract = dict(task_dict.get("todo_contract") or {})
    todo_tasks = list((todo_contract.get("todo") or {}).get("tasks") or [])

    expected_artifacts: list[dict] = []
    for t in todo_tasks:
        for a in list(t.get("expected_artifacts") or []):
            expected_artifacts.append({
                "kind": str(a.get("kind") or ""),
                "relative_path": str(a.get("relative_path") or a.get("description") or ""),
                "required": bool(a.get("required", False)),
                "description": str(a.get("description") or ""),
            })

    from agent.services.worker_todo_planner_service import _DEFAULT_CONFIG as planner_defaults
    from flask import current_app, has_app_context
    agent_cfg = current_app.config.get("AGENT_CONFIG", {}) if has_app_context() else {}
    runtime_cfg = (agent_cfg or {}).get("worker_runtime") or {}
    todo_cfg = (runtime_cfg.get("todo_contract") or {}) if isinstance(runtime_cfg, dict) else {}
    planner_llm_mode = "disabled" if not todo_cfg.get("planner_llm_enabled", False) else "enabled"

    response = {
        "task_id": tid,
        "expected_artifacts": expected_artifacts,
        "manifest_output_path": f".ananta/handoff/<execution_id>/artifact_manifest.v1.json",
        "completion_policy_summary": {
            "completion_on_valid_manifest": True,
            "verification_required": bool(task_dict.get("verification_spec", {}) or {}),
            "allow_synthesized_manifest": False,
            "max_retries": 3,
        },
        "verification_requirements": {
            "required": bool(task_dict.get("verification_spec")),
            "spec": task_dict.get("verification_spec") or {},
        },
        "planner_llm_mode": planner_llm_mode,
        "workspace_output_boundary": workspace_dir or "<workspace_dir not set>",
        "note": "This preview does not execute the worker.",
    }
    return jsonify(response), 200


@artifact_status_bp.route("/api/tasks/<tid>/reconcile", methods=["POST"])
@check_auth
def reconcile_task_from_artifacts(tid: str):
    """Reconcile task state from artifacts. Requires actor and reason. AFH-T016."""
    from agent.services.artifact_reconciliation_service import get_artifact_reconciliation_service
    from flask import g

    body = request.get_json(force=True, silent=True) or {}
    actor = str(body.get("actor") or "") or str((getattr(g, "user", {}) or {}).get("sub") or "unknown")
    reason = str(body.get("reason") or "").strip()
    if not reason:
        return api_response({"error": "reason_required"}, status=400)

    repos = get_repository_registry()
    task = repos.task_repo.get_by_id(tid)
    if not task:
        return api_response({"error": "task_not_found"}, status=404)

    task_dict = task.model_dump()
    workspace_dir = str(task_dict.get("workspace_dir") or "").strip()
    if not workspace_dir:
        return api_response({"error": "workspace_dir_not_set"}, status=400)

    execution_id = str(body.get("execution_id") or task_dict.get("current_worker_job_id") or tid)
    manifest_path = str(body.get("manifest_relative_path") or f".ananta/handoff/{execution_id}/artifact_manifest.v1.json")

    dry_run = bool(body.get("dry_run", False))
    svc = get_artifact_reconciliation_service()

    kwargs = dict(
        task_id=tid,
        goal_id=str(task_dict.get("goal_id") or ""),
        execution_id=execution_id,
        trace_id=str(task_dict.get("trace_id") or tid),
        workspace_root=Path(workspace_dir),
        manifest_relative_path=manifest_path,
        allow_synthesized_manifest=bool(body.get("allow_synthesized_manifest", True)),
    )

    if dry_run:
        result = svc.dry_run(**kwargs)
    else:
        result = svc.apply(actor=actor, reason=reason, **kwargs)

    return jsonify(result), 200
