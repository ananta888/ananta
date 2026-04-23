from __future__ import annotations

from flask import Blueprint, g, request

from agent.auth import check_auth
from agent.common.errors import BadRequestError, ConflictError, NotFoundError, api_response
from agent.db_models import KnowledgeCollectionDB
from agent.models import (
    KnowledgeCollectionCreateRequest,
    KnowledgeCollectionIndexRequest,
    KnowledgeCollectionSearchRequest,
)
from agent.services.retrieval_source_contract import source_scopes_for_types
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services

knowledge_bp = Blueprint("knowledge", __name__)


def get_knowledge_index_job_service():
    return get_core_services().knowledge_index_job_service


def get_knowledge_index_retrieval_service():
    return get_core_services().knowledge_index_retrieval_service


def get_rag_helper_index_service():
    return get_core_services().rag_helper_index_service


def _collection_repo():
    return get_repository_registry().knowledge_collection_repo


def _knowledge_index_repo():
    return get_repository_registry().knowledge_index_repo


def _knowledge_link_repo():
    return get_repository_registry().knowledge_link_repo


def _collection_create_request() -> KnowledgeCollectionCreateRequest:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}
    return KnowledgeCollectionCreateRequest.model_validate(payload)


def _collection_index_request() -> KnowledgeCollectionIndexRequest:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}
    return KnowledgeCollectionIndexRequest.model_validate(payload)


def _collection_search_request() -> KnowledgeCollectionSearchRequest:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}
    return KnowledgeCollectionSearchRequest.model_validate(payload)


def _current_username() -> str:
    user = getattr(g, "user", {}) or {}
    return str(user.get("sub") or user.get("username") or "anonymous")


def _collection_payload(collection_id: str) -> dict | None:
    collection = _collection_repo().get_by_id(collection_id)
    if collection is None:
        return None
    links = _knowledge_link_repo().get_by_collection(collection_id)
    artifact_ids = {str(link.artifact_id) for link in links if getattr(link, "artifact_id", None)}
    indices = []
    for artifact_id in sorted(artifact_ids):
        knowledge_index = _knowledge_index_repo().get_by_artifact(artifact_id)
        if knowledge_index is not None:
            indices.append(knowledge_index.model_dump())
    return {
        "collection": collection.model_dump(),
        "knowledge_links": [link.model_dump() for link in links],
        "knowledge_indices": indices,
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


@knowledge_bp.route("/knowledge/collections", methods=["GET"])
@check_auth
def list_knowledge_collections():
    return api_response(data=[item.model_dump() for item in _collection_repo().get_all()])


@knowledge_bp.route("/knowledge/collections", methods=["POST"])
@check_auth
def create_knowledge_collection():
    payload = _collection_create_request()
    name = str(payload.name or "").strip()
    description = str(payload.description or "").strip() or None
    if not name:
        raise BadRequestError("name_required")
    existing = _collection_repo().get_by_name(name)
    if existing is not None:
        raise ConflictError("collection_exists")
    collection = _collection_repo().save(
        KnowledgeCollectionDB(name=name, description=description, created_by=_current_username())
    )
    return api_response(data=collection.model_dump(), code=201)


@knowledge_bp.route("/knowledge/collections/<collection_id>", methods=["GET"])
@check_auth
def get_knowledge_collection(collection_id: str):
    payload = _collection_payload(collection_id)
    if payload is None:
        raise NotFoundError()
    return api_response(data=payload)


@knowledge_bp.route("/knowledge/collections/<collection_id>/index", methods=["POST"])
@check_auth
def index_knowledge_collection(collection_id: str):
    collection = _collection_repo().get_by_id(collection_id)
    if collection is None:
        raise NotFoundError()
    links = _knowledge_link_repo().get_by_collection(collection_id)
    artifact_ids = [str(link.artifact_id) for link in links if getattr(link, "artifact_id", None)]
    if not artifact_ids:
        raise NotFoundError("collection_has_no_artifacts")

    payload = _collection_index_request()
    if payload.async_mode:
        job = get_knowledge_index_job_service().submit_collection_job(
            collection_id=collection_id,
            artifact_ids=artifact_ids,
            created_by=_current_username(),
            profile_name=payload.profile_name,
            profile_overrides=payload.profile_overrides,
        )
        return api_response(status="accepted", code=202, data={"collection": collection.model_dump(), "job": job})
    results = []
    failed = False
    index_service = get_rag_helper_index_service()
    for artifact_id in artifact_ids:
        knowledge_index, run = index_service.index_artifact(
            artifact_id,
            created_by=_current_username(),
            profile_name=payload.profile_name,
            profile_overrides=payload.profile_overrides,
        )
        results.append(
            {
                "artifact_id": artifact_id,
                "knowledge_index": knowledge_index.model_dump(),
                "run": run.model_dump(),
            }
        )
        if _model_status(run) != "completed":
            failed = True

    return api_response(
        status="error" if failed else "success",
        message="collection_index_failed" if failed else None,
        code=500 if failed else 200,
        data={
            "collection": collection.model_dump(),
            "results": results,
        },
    )


@knowledge_bp.route("/knowledge/index-profiles", methods=["GET"])
@check_auth
def list_knowledge_index_profiles():
    return api_response(data={"items": get_rag_helper_index_service().list_profiles()})


@knowledge_bp.route("/knowledge/index-jobs/<job_id>", methods=["GET"])
@check_auth
def get_knowledge_index_job(job_id: str):
    job = get_knowledge_index_job_service().get_job(job_id)
    if job is None:
        raise NotFoundError("rag_job_not_found")
    return api_response(data={"job": job})


@knowledge_bp.route("/knowledge/collections/<collection_id>/search", methods=["POST"])
@check_auth
def search_knowledge_collection(collection_id: str):
    collection = _collection_repo().get_by_id(collection_id)
    if collection is None:
        raise NotFoundError()
    payload = _collection_search_request()
    query = str(payload.query or "").strip()
    if not query:
        raise BadRequestError("query_required")
    top_k = max(1, int(payload.top_k or 5))
    requested_source_types = [str(item).strip().lower() for item in list(payload.source_types or []) if str(item).strip()]
    invalid_source_types = sorted({item for item in requested_source_types if item not in {"artifact", "wiki"}})
    if invalid_source_types:
        raise BadRequestError("invalid_source_types")
    source_scopes = source_scopes_for_types(set(requested_source_types)) if requested_source_types else set()
    artifact_ids = {
        str(link.artifact_id)
        for link in _knowledge_link_repo().get_by_collection(collection_id)
        if getattr(link, "artifact_id", None)
    }
    search_kwargs = {"top_k": top_k, "artifact_ids": artifact_ids}
    if source_scopes:
        search_kwargs["source_scopes"] = source_scopes
    chunks = get_knowledge_index_retrieval_service().search(query, **search_kwargs)
    return api_response(
        data={
            "collection": collection.model_dump(),
            "query": query,
            "source_policy": {
                "requested": requested_source_types,
                "effective_scopes": sorted(source_scopes) if source_scopes else ["artifact"],
            },
            "chunks": [
                {
                    "engine": chunk.engine,
                    "source": chunk.source,
                    "content": chunk.content,
                    "score": round(chunk.score, 3),
                    "metadata": chunk.metadata,
                }
                for chunk in chunks
            ],
        }
    )
