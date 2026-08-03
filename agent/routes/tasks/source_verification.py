from __future__ import annotations

from collections.abc import Mapping

from flask import Blueprint

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.routes.tasks.task_source_access import (
    authorized_task_source_payload,
)

source_verification_bp = Blueprint("tasks_source_verification", __name__)


@source_verification_bp.route("/tasks/<task_id>/sources", methods=["GET"])
@check_auth
def get_task_sources(task_id: str):
    task, error = authorized_task_source_payload(task_id)
    if error is not None:
        return error
    assert task is not None

    verification_status = dict(task.get("verification_status") or {})
    source_catalog = dict(verification_status.get("source_catalog") or {})
    sources = []
    public_fields = (
        "source_ref",
        "source_id",
        "source_version",
        "tenant_id",
        "scope",
        "provenance_digest",
        "source_type",
        "path",
        "record_id",
        "line_start",
        "line_end",
        "content_hash",
        "manifest_hash",
        "sensitivity",
        "allowed_for_llm_scope",
        "task_id",
    )
    for source in list(source_catalog.get("sources") or []):
        if not isinstance(source, dict):
            continue
        # Project an explicit metadata allowlist.  Legacy or malformed catalog
        # rows may contain arbitrary body-like fields (for example ``text`` or
        # ``excerpt``); copying then deleting one known key is not fail-closed.
        entry = {
            field: source.get(field)
            for field in public_fields
            if field in source and field != "source_ref"
        }
        raw_ref = source.get("source_ref")
        if isinstance(raw_ref, Mapping):
            entry["source_ref"] = {
                field: raw_ref.get(field)
                for field in (
                    "schema",
                    "source_id",
                    "source_version",
                    "tenant_id",
                    "scope",
                    "provenance_digest",
                )
                if field in raw_ref
            }
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
    task, error = authorized_task_source_payload(task_id)
    if error is not None:
        return error
    assert task is not None

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
