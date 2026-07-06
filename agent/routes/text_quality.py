from __future__ import annotations

import json

from flask import Blueprint, current_app, g, request

from agent.auth import admin_required, check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.repository_registry import get_repository_registry
from agent.services.text_quality.criteria_extractor_service import (
    CriteriaExtractorService,
)
from agent.services.text_quality.criteria_review_service import CriteriaReviewService
from agent.services.text_quality.config import normalize_text_quality_config
from agent.services.text_quality.models import ContentKind
from agent.services.text_quality.runtime_service import (
    get_text_quality_runtime_service,
)

text_quality_bp = Blueprint("text_quality", __name__)


def _cfg() -> dict:
    cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    return normalize_text_quality_config(cfg.get("text_quality"))


def _actor() -> str:
    user = getattr(g, "user", {}) or {}
    return str(user.get("sub") or user.get("username") or "system")


def _enabled():
    if not bool(_cfg().get("enabled", False)):
        return api_response(status="error", message="text_quality_disabled", code=403)
    return None


@text_quality_bp.route("/text-quality/evaluate", methods=["POST"])
@check_auth
def evaluate_text():
    if blocked := _enabled():
        return blocked
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text") or "")
    if not text.strip():
        return api_response(status="error", message="text_required", code=400)
    max_chars = int(_cfg().get("max_input_chars") or 12000)
    if len(text) > max_chars:
        return api_response(status="error", message="text_too_long", code=413)
    try:
        kind = ContentKind(str(payload.get("content_kind") or "freeform_prose"))
    except ValueError:
        return api_response(status="error", message="invalid_content_kind", code=400)
    result, _ = get_text_quality_runtime_service().evaluate(
        text=text,
        language=str(payload.get("language") or "de"),
        content_kind=kind,
        evidence_refs=list(payload.get("evidence_refs") or []),
    )
    log_audit(
        "text_quality_evaluated",
        {
            "evaluation_id": result.evaluation_id,
            "status": result.status.value,
            "criteria_version": result.criteria_version,
            "evaluator_version": result.evaluator_version,
        },
    )
    return api_response(data=result.model_dump(mode="json"))


@text_quality_bp.route("/text-quality/criteria", methods=["GET"])
@check_auth
def list_criteria():
    rows = get_repository_registry().text_quality_criteria_set_repo.list(limit=int(request.args.get("limit") or 100))
    return api_response(data={"items": [row.model_dump() for row in rows]})


@text_quality_bp.route("/text-quality/criteria/<criteria_id>", methods=["GET"])
@check_auth
def get_criteria(criteria_id: str):
    row = get_repository_registry().text_quality_criteria_set_repo.get_by_id(criteria_id)
    if row is None:
        return api_response(status="error", message="not_found", code=404)
    return api_response(data=row.model_dump())


@text_quality_bp.route("/text-quality/criteria/extract", methods=["POST"])
@check_auth
def extract_criteria():
    if blocked := _enabled():
        return blocked
    payload = request.get_json(silent=True) or {}
    examples = list(payload.get("examples") or [])
    if not examples:
        return api_response(status="error", message="examples_required", code=400)
    if sum(len(str(item)) for item in examples) > int(_cfg().get("max_input_chars") or 12000):
        return api_response(status="error", message="text_too_long", code=413)

    def invoke_json(**kwargs):
        from agent.services.model_invocation_service import ModelInvocationService

        result = ModelInvocationService.invoke_with_json_schema_result(
            prompt=kwargs["prompt"], json_schema=kwargs["schema"]
        )
        return json.loads(str(result.get("content") or "{}"))

    try:
        kind = ContentKind(str(payload.get("content_kind") or "freeform_prose"))
        row = CriteriaExtractorService(invoke_json).extract(
            examples=examples,
            language=str(payload.get("language") or "de"),
            content_kind=kind,
            actor=_actor(),
            comments=str(payload.get("comments") or ""),
        )
    except (ValueError, json.JSONDecodeError) as exc:
        return api_response(status="error", message=str(exc), code=422)
    log_audit(
        "criteria_extracted",
        {"criteria_id": row.id, "checksum": row.checksum, "actor": _actor()},
    )
    return api_response(data=row.model_dump(), code=201)


@text_quality_bp.route("/text-quality/criteria/<criteria_id>/<action>", methods=["POST"])
@check_auth
@admin_required
def review_criteria(criteria_id: str, action: str):
    status_by_action = {
        "activate": "enabled",
        "reject": "rejected",
        "archive": "archived",
    }
    status = status_by_action.get(action)
    if status is None:
        return api_response(status="error", message="invalid_action", code=400)
    review = CriteriaReviewService()
    row = getattr(review, action)(
        criteria_id,
        actor=_actor(),
        source="text_quality_api",
    )
    if row is None:
        return api_response(status="error", message="not_found", code=404)
    log_audit(
        f"criteria_{'activated' if action == 'activate' else action + 'ed'}",
        {"criteria_id": row.id, "version": row.version, "actor": _actor()},
    )
    return api_response(data=row.model_dump())
