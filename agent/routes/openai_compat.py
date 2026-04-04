from __future__ import annotations

from flask import Blueprint, current_app, g, request

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import BadRequestError, NotFoundError, api_response
from agent.services.repository_registry import get_repository_registry
from agent.services.exposure_policy_service import get_exposure_policy_service
from agent.services.service_registry import get_core_services

openai_compat_bp = Blueprint("openai_compat", __name__)


def get_ingestion_service():
    return get_core_services().ingestion_service


def get_openai_compat_service():
    return get_core_services().openai_compat_service


def _artifact_repo():
    return get_repository_registry().artifact_repo


def _enforce_openai_compat_policy(endpoint_group: str = "core"):
    is_agent_auth = bool(getattr(g, "auth_payload", None))
    is_user_auth = bool(getattr(g, "user", None))
    caller_instance_id = str(request.headers.get("X-Ananta-Instance-ID") or "").strip() or None
    hop_header = request.headers.get("X-Ananta-Hop-Count")
    try:
        hop_count = int(hop_header) if hop_header is not None else None
    except (TypeError, ValueError):
        hop_count = None
    local_instance_id = str(current_app.config.get("AGENT_NAME") or "").strip() or None
    decision = get_exposure_policy_service().evaluate_openai_compat_access(
        cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
        is_agent_auth=is_agent_auth,
        is_user_auth=is_user_auth,
        is_admin=bool(getattr(g, "is_admin", False)),
        endpoint_group=endpoint_group,
        caller_instance_id=caller_instance_id,
        local_instance_id=local_instance_id,
        hop_count=hop_count,
    )
    if not decision.allowed:
        if decision.policy.get("emit_audit_events", True):
            log_audit(
                "openai_compat_access_blocked",
                {"reason": decision.reason, "auth_source": decision.auth_source, "endpoint_group": endpoint_group},
            )
        return api_response(
            status="error",
            message="forbidden",
            data={"details": decision.reason, "auth_source": decision.auth_source, "endpoint_group": endpoint_group},
            code=403,
        )
    return None


@openai_compat_bp.route("/v1/ananta/capabilities", methods=["GET"])
@check_auth
def capabilities():
    blocked = _enforce_openai_compat_policy()
    if blocked:
        return blocked
    policy = get_exposure_policy_service().resolve_openai_compat_policy(current_app.config.get("AGENT_CONFIG", {}) or {})
    payload = {
        "object": "ananta.openai_compat.capabilities",
        "exposure_mode": "openai_compat",
        "policy": policy,
        "features": {
            "models": True,
            "chat_completions": True,
            "responses": True,
            "files": bool(policy.get("allow_files_api")),
            "session_metadata": True,
        },
    }
    if policy.get("emit_audit_events", True):
        log_audit("openai_compat_capabilities_read", {"auth_source": get_exposure_policy_service().resolve_auth_source(
            is_agent_auth=bool(getattr(g, "auth_payload", None)),
            is_user_auth=bool(getattr(g, "user", None)),
        )})
    return payload


@openai_compat_bp.route("/v1/models", methods=["GET"])
@check_auth
def list_models():
    blocked = _enforce_openai_compat_policy()
    if blocked:
        return blocked
    return {"object": "list", "data": get_openai_compat_service().list_models()}


@openai_compat_bp.route("/v1/chat/completions", methods=["POST"])
@check_auth
def chat_completions():
    blocked = _enforce_openai_compat_policy()
    if blocked:
        return blocked
    payload = request.get_json(silent=True) or {}
    try:
        response = get_openai_compat_service().chat_completion(payload=payload)
        conversation = response.get("conversation") if isinstance(response, dict) else None
        if isinstance(conversation, dict):
            log_audit("openai_compat_session_turn", {"endpoint": "chat.completions", "conversation": conversation})
        return response
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@openai_compat_bp.route("/v1/responses", methods=["POST"])
@check_auth
def responses():
    blocked = _enforce_openai_compat_policy()
    if blocked:
        return blocked
    payload = request.get_json(silent=True) or {}
    try:
        response = get_openai_compat_service().response_api(payload=payload)
        conversation = response.get("conversation") if isinstance(response, dict) else None
        if isinstance(conversation, dict):
            log_audit("openai_compat_session_turn", {"endpoint": "responses", "conversation": conversation})
        return response
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@openai_compat_bp.route("/v1/files", methods=["POST"])
@check_auth
def upload_file():
    blocked = _enforce_openai_compat_policy(endpoint_group="files")
    if blocked:
        return blocked
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        raise BadRequestError("file_required")
    content = uploaded.read()
    if not content:
        raise BadRequestError("file_empty")
    artifact, _version, _collection = get_ingestion_service().upload_artifact(
        filename=uploaded.filename,
        content=content,
        created_by="openai_compat",
        media_type=uploaded.mimetype,
        collection_name=None,
    )
    return get_openai_compat_service()._serialize_file(artifact.id), 201


@openai_compat_bp.route("/v1/files", methods=["GET"])
@check_auth
def list_files():
    blocked = _enforce_openai_compat_policy(endpoint_group="files")
    if blocked:
        return blocked
    return {"object": "list", "data": get_openai_compat_service().list_files()}


@openai_compat_bp.route("/v1/files/<file_id>", methods=["GET"])
@check_auth
def get_file(file_id: str):
    blocked = _enforce_openai_compat_policy(endpoint_group="files")
    if blocked:
        return blocked
    if _artifact_repo().get_by_id(file_id) is None:
        raise NotFoundError()
    return get_openai_compat_service()._serialize_file(file_id)
