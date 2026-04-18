from __future__ import annotations

from flask import Blueprint, current_app, g, request

from agent.auth import check_auth
from agent.common.errors import BadRequestError, NotFoundError, api_response
from agent.models import ArtifactRagIndexRequest, ArtifactUploadRequest
from agent.services.repository_registry import get_repository_registry
from agent.services.remote_federation_policy_service import get_remote_federation_policy_service
from agent.services.service_registry import get_core_services

artifacts_bp = Blueprint("artifacts", __name__)


def get_ingestion_service():
    return get_core_services().ingestion_service


def get_knowledge_index_job_service():
    return get_core_services().knowledge_index_job_service


def get_rag_helper_index_service():
    return get_core_services().rag_helper_index_service


def _artifact_repo():
    return get_repository_registry().artifact_repo


def _artifact_version_repo():
    return get_repository_registry().artifact_version_repo


def _extracted_document_repo():
    return get_repository_registry().extracted_document_repo


def _knowledge_index_repo():
    return get_repository_registry().knowledge_index_repo


def _knowledge_index_run_repo():
    return get_repository_registry().knowledge_index_run_repo


def _knowledge_link_repo():
    return get_repository_registry().knowledge_link_repo


def _artifact_upload_request() -> ArtifactUploadRequest:
    return ArtifactUploadRequest(collection_name=str(request.form.get("collection_name") or "").strip() or None)


def _artifact_rag_index_request() -> ArtifactRagIndexRequest:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}
    return ArtifactRagIndexRequest.model_validate(payload)


def _current_username() -> str:
    user = getattr(g, "user", {}) or {}
    return str(user.get("sub") or user.get("username") or "anonymous")


def _serialize_artifact_detail(artifact_id: str) -> dict | None:
    artifact = _artifact_repo().get_by_id(artifact_id)
    if artifact is None:
        return None
    versions = _artifact_version_repo().get_by_artifact(artifact_id)
    documents = _extracted_document_repo().get_by_artifact(artifact_id)
    links = _knowledge_link_repo().get_by_artifact(artifact_id)
    knowledge_index = _knowledge_index_repo().get_by_artifact(artifact_id)
    index_runs = _knowledge_index_run_repo().get_by_knowledge_index(knowledge_index.id) if knowledge_index else []
    return {
        "artifact": artifact.model_dump(),
        "versions": [item.model_dump() for item in versions],
        "extracted_documents": [item.model_dump() for item in documents],
        "knowledge_links": [item.model_dump() for item in links],
        "knowledge_index": knowledge_index.model_dump() if knowledge_index else None,
        "knowledge_index_runs": [item.model_dump() for item in index_runs],
    }


def _enforce_remote_artifact_access(operation: str):
    caller_instance_id = str(request.headers.get("X-Ananta-Instance-ID") or "").strip()
    if not caller_instance_id:
        return None
    cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    policy = get_remote_federation_policy_service().normalize_backend(
        {
            "allowed_operations": ["models", "chat", operation],
        },
        cfg=cfg,
    )
    decision = get_remote_federation_policy_service().evaluate(
        backend_policy=policy,
        operation=operation,
        hop_count=request.headers.get("X-Ananta-Hop-Count", type=int),
        provenance={"caller_instance_id": caller_instance_id},
    )
    if not decision.allowed:
        return api_response(
            status="error",
            message="forbidden",
            data={"details": decision.reason, "caller_instance_id": caller_instance_id},
            code=403,
        )
    return None


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
        raise BadRequestError("file_required")

    content = uploaded.read()
    if not content:
        raise BadRequestError("file_empty")

    upload_request = _artifact_upload_request()
    artifact, version, collection = get_ingestion_service().upload_artifact(
        filename=uploaded.filename,
        content=content,
        created_by=_current_username(),
        media_type=uploaded.mimetype,
        collection_name=upload_request.collection_name,
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
    blocked = _enforce_remote_artifact_access("artifact")
    if blocked:
        return blocked
    return api_response(data=[item.model_dump() for item in _artifact_repo().get_all()])


@artifacts_bp.route("/artifacts/<artifact_id>", methods=["GET"])
@check_auth
def get_artifact(artifact_id: str):
    blocked = _enforce_remote_artifact_access("artifact")
    if blocked:
        return blocked
    payload = _serialize_artifact_detail(artifact_id)
    if payload is None:
        raise NotFoundError()
    return api_response(data=payload)


@artifacts_bp.route("/artifacts/<artifact_id>/extract", methods=["POST"])
@check_auth
def extract_artifact(artifact_id: str):
    artifact, version, document = get_ingestion_service().extract_artifact(artifact_id)
    if artifact is None:
        raise NotFoundError()
    if version is None or document is None:
        raise NotFoundError("artifact_version_not_found")
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
    if _artifact_repo().get_by_id(artifact_id) is None:
        raise NotFoundError()
    payload = _artifact_rag_index_request()
    if payload.async_mode:
        job = get_knowledge_index_job_service().submit_artifact_job(
            artifact_id=artifact_id,
            created_by=_current_username(),
            profile_name=payload.profile_name,
            profile_overrides=payload.profile_overrides,
        )
        return api_response(status="accepted", code=202, data={"job": job})
    knowledge_index, run = get_rag_helper_index_service().index_artifact(
        artifact_id,
        created_by=_current_username(),
        profile_name=payload.profile_name,
        profile_overrides=payload.profile_overrides,
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
    if _artifact_repo().get_by_id(artifact_id) is None:
        raise NotFoundError()
    knowledge_index, runs = get_rag_helper_index_service().get_artifact_status(artifact_id)
    if knowledge_index is None:
        raise NotFoundError("rag_index_not_found")
    return api_response(
        data={
            "knowledge_index": knowledge_index.model_dump(),
            "runs": [item.model_dump() for item in runs],
        }
    )


@artifacts_bp.route("/artifacts/<artifact_id>/rag-preview", methods=["GET"])
@check_auth
def get_artifact_rag_preview(artifact_id: str):
    if _artifact_repo().get_by_id(artifact_id) is None:
        raise NotFoundError()
    limit = request.args.get("limit", default=5, type=int) or 5
    preview = get_rag_helper_index_service().get_artifact_preview(artifact_id, limit=max(1, min(limit, 25)))
    if preview is None:
        raise NotFoundError("rag_index_not_found")
    return api_response(data=preview)


@artifacts_bp.route("/artifacts/<artifact_id>/rag-jobs/<job_id>", methods=["GET"])
@check_auth
def get_artifact_rag_job(artifact_id: str, job_id: str):
    if _artifact_repo().get_by_id(artifact_id) is None:
        raise NotFoundError()
    job = get_knowledge_index_job_service().get_job(job_id)
    if job is None or str(job.get("scope_id")) != artifact_id:
        raise NotFoundError("rag_job_not_found")
    return api_response(data={"job": job})
