"""Deterministic repair API endpoints.

DRR-T035: analyze, preview, execute, and outcome/history endpoints.
DRR-T038: Operator view for deterministic repair path.
"""
from __future__ import annotations

from flask import Blueprint, g, request

from agent.auth import admin_required, check_auth
from agent.common.errors import api_response

repair_bp = Blueprint("repair", __name__, url_prefix="/repair")


def _check_auth_or_abort() -> bool:
    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    return bool(check_auth(token))


# ── DRR-T035: Repair analysis endpoint ────────────────────────────────────────

@repair_bp.route("/analyze", methods=["POST"])
def analyze_repair():
    """Analyze repair evidence and return signature/outcome response. DRR-T035."""
    if not _check_auth_or_abort():
        return api_response({"error": "unauthorized"}, 401)

    body = request.get_json(silent=True) or {}
    evidence = body.get("evidence") or {}
    environment_facts = body.get("environment_facts") or {}
    issue_symptom = str(body.get("issue_symptom") or "")

    if not evidence and not issue_symptom:
        return api_response(
            {"error": "invalid_evidence_payload", "message": "evidence or issue_symptom required"},
            400,
        )

    try:
        from agent.services.deterministic_repair_path_service import (
            build_initial_failure_signature_catalog,
            match_failure_signatures,
            classify_signature_matching_outcome,
            normalize_evidence_bundle,
            ingest_structured_logs,
        )

        evidence_items = []
        if issue_symptom:
            evidence_items.extend(
                ingest_structured_logs([{"message": issue_symptom, "severity": "error"}], source="api")
            )
        if evidence:
            if isinstance(evidence, list):
                evidence_items.extend(evidence)
            elif isinstance(evidence, dict):
                evidence_items.append(evidence)

        normalized = normalize_evidence_bundle(
            evidence_items=evidence_items,
            environment_facts=environment_facts,
        )
        catalog = build_initial_failure_signature_catalog()
        matching = match_failure_signatures(
            normalized_evidence=normalized,
            environment_facts=environment_facts,
            signature_catalog=catalog,
        )
        outcome = classify_signature_matching_outcome(matching_result=matching)

        return api_response({
            "schema": "repair_analysis_response_v1",
            "outcome": outcome.get("outcome"),
            "best_problem_class": outcome.get("best_problem_class"),
            "best_score": outcome.get("best_score"),
            "top_matches": (matching.get("matches") or [])[:3],
            "signature_count_checked": len(catalog),
        })
    except Exception as exc:
        return api_response({"error": "analysis_failed", "detail": str(exc)}, 500)


# ── DRR-T035: Repair preview endpoint ─────────────────────────────────────────

@repair_bp.route("/preview", methods=["POST"])
def preview_repair():
    """Create a repair preview plan without execution. DRR-T035."""
    if not _check_auth_or_abort():
        return api_response({"error": "unauthorized"}, 401)

    body = request.get_json(silent=True) or {}
    matching_outcome = body.get("matching_outcome") or {}
    environment_facts = body.get("environment_facts") or {}
    task_id = str(body.get("task_id") or "")
    goal_id = str(body.get("goal_id") or "")

    if not matching_outcome:
        return api_response(
            {"error": "invalid_input", "message": "matching_outcome required"}, 400
        )

    try:
        from agent.services.repair_execution_plan_service import generate_repair_execution_plan

        plan = generate_repair_execution_plan(
            matching_outcome=matching_outcome,
            environment_facts=environment_facts,
            task_id=task_id,
            goal_id=goal_id,
        )
        return api_response({
            "schema": "repair_preview_response_v1",
            "plan_id": plan.plan_id,
            "procedure_id": plan.procedure_id,
            "safety_class": plan.safety_class,
            "approval_requirement": plan.approval_requirement,
            "step_count": len(plan.steps),
            "steps": [
                {
                    "step_id": s.step_id,
                    "title": s.title,
                    "mutation_candidate": s.mutation_candidate,
                    "action_safety_class": s.action_safety_class,
                    "requires_approval": s.requires_approval,
                    "verification_after_step": s.verification_after_step,
                }
                for s in plan.steps
            ],
            "verification_plan": plan.verification_plan,
            "rollback_hints": plan.rollback_hints,
        })
    except Exception as exc:
        return api_response({"error": "preview_failed", "detail": str(exc)}, 500)


# ── DRR-T035: Repair execution endpoint (approval-gated) ─────────────────────

@repair_bp.route("/execute", methods=["POST"])
@admin_required
def execute_repair():
    """Execute an approved repair plan. Requires admin permission. DRR-T035."""
    body = request.get_json(silent=True) or {}
    procedure_dict = body.get("repair_procedure") or {}
    procedure_id = str(body.get("procedure_id") or procedure_dict.get("procedure_id") or "")
    task_id = str(body.get("task_id") or "")
    approval_ref = body.get("approval_ref")
    dry_run = bool(body.get("dry_run", False))

    if not procedure_id:
        return api_response({"error": "procedure_id required"}, 400)
    if not procedure_dict:
        return api_response({"error": "repair_procedure required"}, 400)

    try:
        from agent.services.native_worker_runtime_service import execute_repair_procedure_plan

        result = execute_repair_procedure_plan(
            task_id=task_id or f"repair-api-{procedure_id}",
            procedure_id=procedure_id,
            repair_procedure_dict=procedure_dict,
            approval_ref=approval_ref,
            dry_run=dry_run,
        )
        return api_response(result)
    except Exception as exc:
        return api_response({"error": "execution_failed", "detail": str(exc)}, 500)


# ── DRR-T035/T038: Repair outcome history endpoint ────────────────────────────

@repair_bp.route("/outcomes", methods=["GET"])
def get_repair_outcomes():
    """Fetch repair outcome/history for task/goal/procedure. DRR-T035."""
    if not _check_auth_or_abort():
        return api_response({"error": "unauthorized"}, 401)

    problem_class = request.args.get("problem_class") or ""
    procedure_id = request.args.get("procedure_id") or ""
    signature_id = request.args.get("signature_id") or ""

    try:
        from agent.repositories.repair_execution_record import get_repair_execution_record_repo

        repo = get_repair_execution_record_repo()
        records: list = []

        if problem_class:
            records = repo.query_by_problem_class(problem_class, limit=20)
        elif procedure_id:
            records = repo.query_by_procedure_id(procedure_id, limit=20)
        elif signature_id:
            records = repo.query_by_signature_id(signature_id, limit=20)
        else:
            records = repo.recent_by_environment({}, limit=10)

        return api_response({
            "schema": "repair_outcomes_response_v1",
            "count": len(records),
            "outcomes": [
                {
                    "id": str(r.id or ""),
                    "plan_id": r.plan_id,
                    "procedure_id": r.procedure_id,
                    "problem_class": r.problem_class,
                    "execution_status": r.execution_status,
                    "outcome_label": r.outcome_label,
                    "regression_flag": r.regression_flag,
                    "created_at": r.created_at,
                }
                for r in records
            ],
        })
    except Exception as exc:
        return api_response({"error": "query_failed", "detail": str(exc)}, 500)


# ── DRR-T038: Operator view endpoint ─────────────────────────────────────────

@repair_bp.route("/operator/view", methods=["GET"])
def operator_repair_view():
    """Operator view for deterministic repair path. DRR-T038."""
    if not _check_auth_or_abort():
        return api_response({"error": "unauthorized"}, 401)

    task_id = request.args.get("task_id") or ""

    try:
        from agent.services.deterministic_repair_path_service import (
            build_operator_session_summary,
            build_path_visibility,
            build_operator_proposal_preview,
            build_repair_history_inspection_view,
        )
        from agent.services.repair_diagnostics_service import build_repair_diagnostics_read_model

        diagnostics = build_repair_diagnostics_read_model()
        session_summary = build_operator_session_summary(
            diagnosis_run={},
            matching_outcome={"outcome": "no_data", "best_score": 0.0},
            repair_execution_result={},
            final_verification={"outcome_label": None},
        )
        path_visibility = build_path_visibility(
            llm_escalation_decision={"should_escalate": False, "reasons": []},
            matching_outcome={"outcome": "no_data"},
        )
        history_view = build_repair_history_inspection_view(memory_entries=[])

        return api_response({
            "schema": "repair_operator_view_v1",
            "task_id": task_id,
            "diagnostics": diagnostics.as_dict(),
            "session_summary": session_summary,
            "path_visibility": path_visibility,
            "history": history_view,
        })
    except Exception as exc:
        return api_response({"error": "operator_view_failed", "detail": str(exc)}, 500)


# ── DRR-T034: Repair diagnostics endpoint ─────────────────────────────────────

@repair_bp.route("/diagnostics", methods=["GET"])
def repair_diagnostics():
    """Return repair engine diagnostics read-model. DRR-T034."""
    if not _check_auth_or_abort():
        return api_response({"error": "unauthorized"}, 401)

    try:
        from agent.services.repair_diagnostics_service import build_repair_diagnostics_read_model

        model = build_repair_diagnostics_read_model()
        return api_response(model.as_dict())
    except Exception as exc:
        return api_response({"error": "diagnostics_failed", "detail": str(exc)}, 500)
