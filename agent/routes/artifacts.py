from __future__ import annotations

from flask import Blueprint, g, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.repository import (
    artifact_repo,
    artifact_version_repo,
    extracted_document_repo,
    knowledge_index_repo,
    knowledge_index_run_repo,
    knowledge_link_repo,
)
from agent.services.ingestion_service import get_ingestion_service
from agent.services.rag_helper_index_service import get_rag_helper_index_service

artifacts_bp = Blueprint("artifacts", __name__)


def _current_username() -> str:
    user = getattr(g, "user", {}) or {}
    return str(user.get("sub") or user.get("username") or "anonymous")


def _serialize_artifact_detail(artifact_id: str) -> dict | None:
    artifact = artifact_repo.get_by_id(artifact_id)
    if artifact is None:
        return None
    versions = artifact_version_repo.get_by_artifact(artifact_id)
    documents = extracted_document_repo.get_by_artifact(artifact_id)
    links = knowledge_link_repo.get_by_artifact(artifact_id)
    knowledge_index = knowledge_index_repo.get_by_artifact(artifact_id)
    index_runs = knowledge_index_run_repo.get_by_knowledge_index(knowledge_index.id) if knowledge_index else []
    return {
        "artifact": artifact.model_dump(),
        "versions": [item.model_dump() for item in versions],
        "extracted_documents": [item.model_dump() for item in documents],
        "knowledge_links": [item.model_dump() for item in links],
        "knowledge_index": knowledge_index.model_dump() if knowledge_index else None,
        "knowledge_index_runs": [item.model_dump() for item in index_runs],
    }


def _model_status(item) -> str:
    direct = getattr(item, "status", None)
    if isinstance(direct, str) and direct.strip():
        return direct
    if hasattr(item, "model_dump"):
        payload = item.model_dump()
        if isinstance(payload, dict):
            value = payload.get("status")
            if isinstance(value, str):
                return value
    return ""


@artifacts_bp.route("/artifacts/upload", methods=["POST"])
@check_auth
def upload_artifact():
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return api_response(status="error", message="file_required", code=400)

    content = uploaded.read()
    if not content:
        return api_response(status="error", message="file_empty", code=400)

    collection_name = str(request.form.get("collection_name") or "").strip() or None
    artifact, version, collection = get_ingestion_service().upload_artifact(
        filename=uploaded.filename,
        content=content,
        created_by=_current_username(),
        media_type=uploaded.mimetype,
        collection_name=collection_name,
    )
    return api_response(
        data={
            "artifact": artifact.model_dump(),
            "version": version.model_dump(),
            "collection": collection.model_dump() if collection else None,
        },
        code=201,
    )


@artifacts_bp.route("/artifacts", methods=["GET"])
@check_auth
def list_artifacts():
    return api_response(data=[item.model_dump() for item in artifact_repo.get_all()])


@artifacts_bp.route("/artifacts/<artifact_id>", methods=["GET"])
@check_auth
def get_artifact(artifact_id: str):
    payload = _serialize_artifact_detail(artifact_id)
    if payload is None:
        return api_response(status="error", message="not_found", code=404)
    return api_response(data=payload)


@artifacts_bp.route("/artifacts/<artifact_id>/extract", methods=["POST"])
@check_auth
def extract_artifact(artifact_id: str):
    artifact, version, document = get_ingestion_service().extract_artifact(artifact_id)
    if artifact is None:
        return api_response(status="error", message="not_found", code=404)
    if version is None or document is None:
        return api_response(status="error", message="artifact_version_not_found", code=404)
    return api_response(
        data={
            "artifact": artifact.model_dump(),
            "version": version.model_dump(),
            "document": document.model_dump(),
        }
    )


@artifacts_bp.route("/artifacts/<artifact_id>/rag-index", methods=["POST"])
@check_auth
def index_artifact_for_rag(artifact_id: str):
    if artifact_repo.get_by_id(artifact_id) is None:
        return api_response(status="error", message="not_found", code=404)
    knowledge_index, run = get_rag_helper_index_service().index_artifact(
        artifact_id,
        created_by=_current_username(),
    )
    run_status = _model_status(run)
    status = "success" if run_status == "completed" else "error"
    code = 200 if run_status == "completed" else 500
    return api_response(
        status=status,
        code=code,
        data={
            "knowledge_index": knowledge_index.model_dump(),
            "run": run.model_dump(),
        },
        message=None if run_status == "completed" else "rag_index_failed",
    )


@artifacts_bp.route("/artifacts/<artifact_id>/rag-status", methods=["GET"])
@check_auth
def get_artifact_rag_status(artifact_id: str):
    if artifact_repo.get_by_id(artifact_id) is None:
        return api_response(status="error", message="not_found", code=404)
    knowledge_index, runs = get_rag_helper_index_service().get_artifact_status(artifact_id)
    if knowledge_index is None:
        return api_response(status="error", message="rag_index_not_found", code=404)
    return api_response(
        data={
            "knowledge_index": knowledge_index.model_dump(),
            "runs": [item.model_dump() for item in runs],
        }
    )


@artifacts_bp.route("/artifacts/<artifact_id>/rag-preview", methods=["GET"])
@check_auth
def get_artifact_rag_preview(artifact_id: str):
    if artifact_repo.get_by_id(artifact_id) is None:
        return api_response(status="error", message="not_found", code=404)
    limit = request.args.get("limit", default=5, type=int) or 5
    preview = get_rag_helper_index_service().get_artifact_preview(artifact_id, limit=max(1, min(limit, 25)))
    if preview is None:
        return api_response(status="error", message="rag_index_not_found", code=404)
    return api_response(data=preview)
