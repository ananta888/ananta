from __future__ import annotations

from flask import Blueprint, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.repository import artifact_repo
from agent.services.ingestion_service import get_ingestion_service
from agent.services.openai_compat_service import get_openai_compat_service

openai_compat_bp = Blueprint("openai_compat", __name__)


@openai_compat_bp.route("/v1/models", methods=["GET"])
@check_auth
def list_models():
    return {"object": "list", "data": get_openai_compat_service().list_models()}


@openai_compat_bp.route("/v1/chat/completions", methods=["POST"])
@check_auth
def chat_completions():
    payload = request.get_json(silent=True) or {}
    try:
        return get_openai_compat_service().chat_completion(payload=payload)
    except ValueError as exc:
        return api_response(status="error", message=str(exc), code=400)


@openai_compat_bp.route("/v1/responses", methods=["POST"])
@check_auth
def responses():
    payload = request.get_json(silent=True) or {}
    try:
        return get_openai_compat_service().response_api(payload=payload)
    except ValueError as exc:
        return api_response(status="error", message=str(exc), code=400)


@openai_compat_bp.route("/v1/files", methods=["POST"])
@check_auth
def upload_file():
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return api_response(status="error", message="file_required", code=400)
    content = uploaded.read()
    if not content:
        return api_response(status="error", message="file_empty", code=400)
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
    return {"object": "list", "data": get_openai_compat_service().list_files()}


@openai_compat_bp.route("/v1/files/<file_id>", methods=["GET"])
@check_auth
def get_file(file_id: str):
    if artifact_repo.get_by_id(file_id) is None:
        return api_response(status="error", message="not_found", code=404)
    return get_openai_compat_service()._serialize_file(file_id)
