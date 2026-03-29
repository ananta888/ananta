from __future__ import annotations

from flask import Blueprint, g, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.db_models import KnowledgeCollectionDB
from agent.repository import knowledge_collection_repo, knowledge_index_repo, knowledge_link_repo
from agent.services.knowledge_index_job_service import get_knowledge_index_job_service
from agent.services.knowledge_index_retrieval_service import get_knowledge_index_retrieval_service
from agent.services.rag_helper_index_service import get_rag_helper_index_service

knowledge_bp = Blueprint("knowledge", __name__)


def _current_username() -> str:
    user = getattr(g, "user", {}) or {}
    return str(user.get("sub") or user.get("username") or "anonymous")


def _collection_payload(collection_id: str) -> dict | None:
    collection = knowledge_collection_repo.get_by_id(collection_id)
    if collection is None:
        return None
    links = knowledge_link_repo.get_by_collection(collection_id)
    artifact_ids = {str(link.artifact_id) for link in links if getattr(link, "artifact_id", None)}
    indices = []
    for artifact_id in sorted(artifact_ids):
        knowledge_index = knowledge_index_repo.get_by_artifact(artifact_id)
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
    return api_response(data=[item.model_dump() for item in knowledge_collection_repo.get_all()])


@knowledge_bp.route("/knowledge/collections", methods=["POST"])
@check_auth
def create_knowledge_collection():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    description = str(payload.get("description") or "").strip() or None
    if not name:
        return api_response(status="error", message="name_required", code=400)
    existing = knowledge_collection_repo.get_by_name(name)
    if existing is not None:
        return api_response(status="error", message="collection_exists", code=409)
    collection = knowledge_collection_repo.save(
        KnowledgeCollectionDB(name=name, description=description, created_by=_current_username())
    )
    return api_response(data=collection.model_dump(), code=201)


@knowledge_bp.route("/knowledge/collections/<collection_id>", methods=["GET"])
@check_auth
def get_knowledge_collection(collection_id: str):
    payload = _collection_payload(collection_id)
    if payload is None:
        return api_response(status="error", message="not_found", code=404)
    return api_response(data=payload)


@knowledge_bp.route("/knowledge/collections/<collection_id>/index", methods=["POST"])
@check_auth
def index_knowledge_collection(collection_id: str):
    collection = knowledge_collection_repo.get_by_id(collection_id)
    if collection is None:
        return api_response(status="error", message="not_found", code=404)
    links = knowledge_link_repo.get_by_collection(collection_id)
    artifact_ids = [str(link.artifact_id) for link in links if getattr(link, "artifact_id", None)]
    if not artifact_ids:
        return api_response(status="error", message="collection_has_no_artifacts", code=404)

    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}
    async_mode = bool(payload.get("async"))
    if async_mode:
        job = get_knowledge_index_job_service().submit_collection_job(
            collection_id=collection_id,
            artifact_ids=artifact_ids,
            created_by=_current_username(),
            profile_name=payload.get("profile_name"),
            profile_overrides=payload.get("profile_overrides"),
        )
        return api_response(status="accepted", code=202, data={"collection": collection.model_dump(), "job": job})
    results = []
    failed = False
    index_service = get_rag_helper_index_service()
    for artifact_id in artifact_ids:
        knowledge_index, run = index_service.index_artifact(
            artifact_id,
            created_by=_current_username(),
            profile_name=payload.get("profile_name"),
            profile_overrides=payload.get("profile_overrides"),
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
        return api_response(status="error", message="rag_job_not_found", code=404)
    return api_response(data={"job": job})


@knowledge_bp.route("/knowledge/collections/<collection_id>/search", methods=["POST"])
@check_auth
def search_knowledge_collection(collection_id: str):
    collection = knowledge_collection_repo.get_by_id(collection_id)
    if collection is None:
        return api_response(status="error", message="not_found", code=404)
    payload = request.get_json(silent=True) or {}
    query = str(payload.get("query") or "").strip()
    if not query:
        return api_response(status="error", message="query_required", code=400)
    top_k = int(payload.get("top_k") or 5)
    artifact_ids = {
        str(link.artifact_id)
        for link in knowledge_link_repo.get_by_collection(collection_id)
        if getattr(link, "artifact_id", None)
    }
    chunks = get_knowledge_index_retrieval_service().search(query, top_k=top_k, artifact_ids=artifact_ids)
    return api_response(
        data={
            "collection": collection.model_dump(),
            "query": query,
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
