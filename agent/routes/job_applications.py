"""Job Application REST API — domain-specific endpoints on top of CaseFlow.

GET  /api/caseflow/jobs                         — list job applications
GET  /api/caseflow/jobs/<case_id>/fit-score     — get fit score
PUT  /api/caseflow/jobs/<case_id>/fit-score     — set manual fit score
GET  /api/caseflow/jobs/<case_id>/document-bundle — get document bundle
POST /api/caseflow/jobs/<case_id>/posting       — add/normalize job posting
"""
from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

from agent.caseflow.models import CaseFlowCase
from agent.job_module.document_bundle import ApplicationDocumentBundle, DocumentStatus
from agent.job_module.fit_scoring import JobFitScore, SubScore
from agent.job_module.models import JobApplicationPayload
from agent.job_module.posting_normalizer import normalize_posting

job_app_bp = Blueprint("job_applications", __name__, url_prefix="/api/caseflow/jobs")

# In-memory fit scores: case_id -> JobFitScore
_fit_scores: dict[str, JobFitScore] = {}


def _get_job_cases() -> list[CaseFlowCase]:
    from agent.routes.caseflow import _cases
    return [c for c in _cases.values() if c.case_type == "job_application" and not c.is_deleted]


def _case_with_payload(case: CaseFlowCase) -> dict[str, Any]:
    data = case.model_dump()
    data["created_at"] = case.created_at.isoformat()
    data["updated_at"] = case.updated_at.isoformat()
    if case.closed_at:
        data["closed_at"] = case.closed_at.isoformat()
    # Try to parse domain_payload as JobApplicationPayload
    try:
        payload = JobApplicationPayload.model_validate(case.domain_payload)
        data["payload"] = payload.model_dump()
    except Exception:
        data["payload"] = case.domain_payload
    fit_score = _fit_scores.get(case.id)
    if fit_score:
        data["fit_score"] = fit_score.model_dump()
    return data


@job_app_bp.route("", methods=["GET"])
def list_job_applications():
    status = request.args.get("status")
    cases = _get_job_cases()
    if status:
        cases = [c for c in cases if c.status == status]
    return jsonify([_case_with_payload(c) for c in cases]), 200


@job_app_bp.route("/<case_id>", methods=["GET"])
def get_job_application(case_id: str):
    from agent.routes.caseflow import _cases
    case = _cases.get(case_id)
    if not case or case.case_type != "job_application" or case.is_deleted:
        return jsonify({"error": "not_found"}), 404
    return jsonify(_case_with_payload(case)), 200


@job_app_bp.route("/<case_id>/fit-score", methods=["GET"])
def get_fit_score(case_id: str):
    from agent.routes.caseflow import _cases
    case = _cases.get(case_id)
    if not case or case.case_type != "job_application":
        return jsonify({"error": "not_found"}), 404
    score = _fit_scores.get(case_id)
    if not score:
        return jsonify({"case_id": case_id, "final_score": None, "note": "No score yet"}), 200
    data = score.model_dump()
    return jsonify(data), 200


@job_app_bp.route("/<case_id>/fit-score", methods=["PUT"])
def set_manual_fit_score(case_id: str):
    from agent.routes.caseflow import _cases
    case = _cases.get(case_id)
    if not case or case.case_type != "job_application":
        return jsonify({"error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    score_val = body.get("score")
    reason = body.get("reason", "")
    if score_val is None:
        return jsonify({"error": "score is required"}), 400
    existing = _fit_scores.get(case_id) or JobFitScore(case_id=case_id, source="manual")
    existing.manual_override = float(score_val)
    existing.manual_override_reason = reason
    existing.final_score = existing.compute_final_score()
    _fit_scores[case_id] = existing
    return jsonify(existing.model_dump()), 200


@job_app_bp.route("/<case_id>/document-bundle", methods=["GET"])
def document_bundle(case_id: str):
    from agent.routes.caseflow import _artifacts, _cases
    case = _cases.get(case_id)
    if not case or case.case_type != "job_application":
        return jsonify({"error": "not_found"}), 404
    artifacts = _artifacts.get(case_id, [])
    bundle = ApplicationDocumentBundle(case_id=case_id)
    for artifact in artifacts:
        doc_type = artifact.artifact_type
        if doc_type not in bundle.documents:
            bundle.documents[doc_type] = DocumentStatus(doc_type=doc_type)
        ds = bundle.documents[doc_type]
        ds.artifact_ids.append(artifact.id)
        ds.latest_artifact_id = artifact.id
        ds.status = artifact.status.value
    bundle.completion_percent = bundle.compute_completion()
    data = bundle.model_dump()
    data["missing_required"] = bundle.missing_required_docs()
    data["can_send"] = bundle.can_send()
    return jsonify(data), 200


@job_app_bp.route("/<case_id>/posting", methods=["POST"])
def add_job_posting(case_id: str):
    from agent.routes.caseflow import _artifacts, _cases
    from agent.caseflow.artifacts import CaseArtifact
    case = _cases.get(case_id)
    if not case or case.case_type != "job_application":
        return jsonify({"error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    raw_text = body.get("raw_text", "")
    source_url = body.get("source_url")
    source_name = body.get("source_name", "manual")
    if not raw_text:
        return jsonify({"error": "raw_text is required"}), 400
    posting = normalize_posting(raw_text, source_url=source_url, source_name=source_name)
    artifact = CaseArtifact(
        case_id=case_id,
        artifact_type="job_posting",
        title=posting.title or "Stellenanzeige",
        source="manual",
        content_text=raw_text,
        metadata=posting.model_dump(exclude={"raw_text"}),
    )
    _artifacts.setdefault(case_id, []).append(artifact)
    from agent.caseflow.artifacts import ArtifactStatus
    from agent.caseflow.timeline import CaseEvent, append_event
    evt = CaseEvent(
        case_id=case_id,
        event_type="artifact_added",
        title=f"Stellenanzeige hinzugefügt: {artifact.title}",
        payload={"artifact_id": artifact.id, "artifact_type": "job_posting"},
        artifact_id=artifact.id,
    )
    append_event(case_id, evt)
    from agent.routes.caseflow import _artifact_to_dict
    return jsonify(_artifact_to_dict(artifact)), 201


def reset_stores() -> None:
    """Reset in-memory stores. For tests only."""
    _fit_scores.clear()
