from __future__ import annotations

from flask import Blueprint, request

from agent.auth import check_auth
from agent.common.errors import BadRequestError, NotFoundError, api_response
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services

openai_compat_bp = Blueprint("openai_compat", __name__)


def get_ingestion_service():
    return get_core_services().ingestion_service


def get_openai_compat_service():
    return get_core_services().openai_compat_service


def _artifact_repo():
    return get_repository_registry().artifact_repo


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
        raise BadRequestError(str(exc)) from exc


@openai_compat_bp.route("/v1/responses", methods=["POST"])
@check_auth
def responses():
    payload = request.get_json(silent=True) or {}
    try:
        return get_openai_compat_service().response_api(payload=payload)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


@openai_compat_bp.route("/v1/files", methods=["POST"])
@check_auth
def upload_file():
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
    return {"object": "list", "data": get_openai_compat_service().list_files()}


@openai_compat_bp.route("/v1/files/<file_id>", methods=["GET"])
@check_auth
def get_file(file_id: str):
    if _artifact_repo().get_by_id(file_id) is None:
        raise NotFoundError()
    return get_openai_compat_service()._serialize_file(file_id)
