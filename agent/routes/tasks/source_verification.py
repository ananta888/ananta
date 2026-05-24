from __future__ import annotations

from flask import Blueprint

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.services.repository_registry import get_repository_registry

source_verification_bp = Blueprint("tasks_source_verification", __name__)


def _get_task_payload(task_id: str) -> dict | None:
    task = get_repository_registry().task_repo.get_by_id(task_id)
    if task is None:
        return None
    return task.model_dump()


@source_verification_bp.route("/tasks/<task_id>/sources", methods=["GET"])
@check_auth
def get_task_sources(task_id: str):
    task = _get_task_payload(task_id)
    if task is None:
        return api_response(status="error", message="not_found", code=404)

    verification_status = dict(task.get("verification_status") or {})
    source_catalog = dict(verification_status.get("source_catalog") or {})
    sources = []
    for source in list(source_catalog.get("sources") or []):
        if not isinstance(source, dict):
            continue
        entry = dict(source)
        # Never expose content bodies on this endpoint.
        entry.pop("content", None)
        entry["content_exposed"] = False
        if not bool(entry.get("allowed_for_llm_scope", True)):
            entry["redaction_reason"] = "blocked_by_policy_scope"
        sources.append(entry)

    return api_response(
        data={
            "task_id": task_id,
            "source_catalog_id": source_catalog.get("source_catalog_id"),
            "catalog_hash": source_catalog.get("source_catalog_hash"),
            "source_count": len(sources),
            "sources": sources,
        }
    )


@source_verification_bp.route("/tasks/<task_id>/answer-verification", methods=["GET"])
@check_auth
def get_task_answer_verification(task_id: str):
    task = _get_task_payload(task_id)
    if task is None:
        return api_response(status="error", message="not_found", code=404)

    verification_status = dict(task.get("verification_status") or {})
    answer_verification = dict(verification_status.get("answer_verification") or {})
    return api_response(
        data={
            "task_id": task_id,
            "status": answer_verification.get("citation_verification_status"),
            "answer_schema": answer_verification.get("answer_schema"),
            "verified_claim_count": int(answer_verification.get("verified_claim_count") or 0),
            "unverified_claim_count": int(answer_verification.get("unverified_claim_count") or 0),
            "failed_claims": list(answer_verification.get("failed_claims") or []),
        }
    )
