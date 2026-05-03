from __future__ import annotations

from flask import Blueprint, g, request

from agent.auth import check_auth
from agent.common.errors import BadRequestError, ConflictError, NotFoundError, api_response
from agent.db_models import KnowledgeCollectionDB
from agent.models import (
    KnowledgeCollectionCreateRequest,
    KnowledgeCollectionIndexRequest,
    KnowledgeCollectionSearchRequest,
    KnowledgeSourceIndexRequest,
)
from agent.services.retrieval_orchestration_contract import build_retrieval_orchestration_contract
from agent.services.retrieval_service import get_retrieval_service
from agent.services.retrieval_source_contract import source_scopes_for_types
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services

knowledge_bp = Blueprint("knowledge", __name__)

WIKI_IMPORT_PRESETS = [
    {
        "id": "wiki-ananta-core-en",
        "label": "Wikipedia Mini: Software Engineering (EN)",
        "description": "Kleines, schnelles Starter-Set fuer lokale RAG-Tests in der APK.",
        "corpus_url": "https://raw.githubusercontent.com/ananta888/ananta/refs/heads/main/data/wiki-presets/wiki-ananta-core-en.jsonl",
        "source_id": "wiki-ananta-core-en",
        "language": "en",
        "size_hint": "~1-3 MB",
        "recommended": True,
        "codecompass_prerender": True,
    },
    {
        "id": "wiki-ananta-core-de",
        "label": "Wikipedia Mini: Software Engineering (DE)",
        "description": "Deutschsprachiges Starter-Set fuer lokale Wissenssuche.",
        "corpus_url": "https://raw.githubusercontent.com/ananta888/ananta/refs/heads/main/data/wiki-presets/wiki-ananta-core-de.jsonl",
        "source_id": "wiki-ananta-core-de",
        "language": "de",
        "size_hint": "~1-3 MB",
        "recommended": False,
        "codecompass_prerender": True,
    },
]


def get_knowledge_index_job_service():
    return get_core_services().knowledge_index_job_service


def get_knowledge_index_retrieval_service():
    return get_core_services().knowledge_index_retrieval_service


def get_rag_helper_index_service():
    return get_core_services().rag_helper_index_service


def get_ingestion_service():
    return get_core_services().ingestion_service


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


def _source_index_request() -> KnowledgeSourceIndexRequest:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}
    return KnowledgeSourceIndexRequest.model_validate(payload)


def _wiki_import_request() -> dict:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise BadRequestError("invalid_payload")
    corpus_path = str(payload.get("corpus_path") or "").strip()
    if not corpus_path:
        raise BadRequestError("corpus_path_required")
    source_id = str(payload.get("source_id") or "").strip() or None
    profile_name = str(payload.get("profile_name") or "").strip() or None
    language = str(payload.get("language") or "en").strip().lower() or "en"
    strict = bool(payload.get("strict", False))
    async_mode = bool(payload.get("async", False))
    codecompass_prerender = bool(payload.get("codecompass_prerender", False))
    raw_source_metadata = payload.get("source_metadata") or {}
    if not isinstance(raw_source_metadata, dict):
        raise BadRequestError("invalid_source_metadata")
    source_metadata = dict(raw_source_metadata)
    return {
        "corpus_path": corpus_path,
        "source_id": source_id,
        "profile_name": profile_name,
        "language": language,
        "strict": strict,
        "async_mode": async_mode,
        "codecompass_prerender": codecompass_prerender,
        "source_metadata": source_metadata,
    }


def _wiki_import_url_request() -> dict:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise BadRequestError("invalid_payload")
    preset_id = str(payload.get("preset_id") or "").strip()
    corpus_url = str(payload.get("corpus_url") or "").strip()
    if not preset_id and not corpus_url:
        raise BadRequestError("wiki_corpus_url_required")
    selected_preset = next((item for item in WIKI_IMPORT_PRESETS if item["id"] == preset_id), None) if preset_id else None
    if preset_id and selected_preset is None:
        raise BadRequestError("invalid_wiki_preset")
    effective_url = str(selected_preset.get("corpus_url") if selected_preset else corpus_url).strip()
    if not effective_url:
        raise BadRequestError("wiki_corpus_url_required")
    source_id = str(payload.get("source_id") or "").strip() or (
        str(selected_preset.get("source_id") or "").strip() if selected_preset else None
    )
    profile_name = str(payload.get("profile_name") or "").strip() or None
    language = str(payload.get("language") or (selected_preset.get("language") if selected_preset else "en")).strip().lower() or "en"
    strict = bool(payload.get("strict", False))
    async_mode = bool(payload.get("async", False))
    codecompass_prerender = bool(payload.get("codecompass_prerender", selected_preset.get("codecompass_prerender", False) if selected_preset else False))
    raw_source_metadata = payload.get("source_metadata") or {}
    if not isinstance(raw_source_metadata, dict):
        raise BadRequestError("invalid_source_metadata")
    source_metadata = dict(raw_source_metadata)
    if selected_preset is not None:
        source_metadata.setdefault("preset_id", selected_preset["id"])
        source_metadata.setdefault("preset_label", selected_preset["label"])
    return {
        "corpus_url": effective_url,
        "source_id": source_id or None,
        "profile_name": profile_name,
        "language": language,
        "strict": strict,
        "async_mode": async_mode,
        "codecompass_prerender": codecompass_prerender,
        "source_metadata": source_metadata,
    }


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


@knowledge_bp.route("/knowledge/wiki/presets", methods=["GET"])
@check_auth
def list_wiki_import_presets():
    return api_response(data={"items": WIKI_IMPORT_PRESETS})


@knowledge_bp.route("/knowledge/sources/index-records", methods=["POST"])
@check_auth
def index_knowledge_source_records():
    payload = _source_index_request()
    source_scope = str(payload.source_scope or "").strip().lower()
    source_id = str(payload.source_id or "").strip()
    if not source_scope:
        raise BadRequestError("source_scope_required")
    if not source_id:
        raise BadRequestError("source_id_required")
    if payload.async_mode:
        job = get_knowledge_index_job_service().submit_source_records_job(
            source_scope=source_scope,
            source_id=source_id,
            records=list(payload.records or []),
            created_by=_current_username(),
            profile_name=payload.profile_name,
            source_metadata=dict(payload.source_metadata or {}),
        )
        return api_response(status="accepted", code=202, data={"job": job})
    try:
        knowledge_index, run = get_rag_helper_index_service().index_source_records(
            source_scope=source_scope,
            source_id=source_id,
            records=list(payload.records or []),
            created_by=_current_username(),
            profile_name=payload.profile_name,
            source_metadata=dict(payload.source_metadata or {}),
        )
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    run_status = _model_status(run)
    status = "success" if run_status == "completed" else "error"
    return api_response(
        status=status,
        code=200 if run_status == "completed" else 500,
        message=None if run_status == "completed" else "source_index_failed",
        data={
            "knowledge_index": knowledge_index.model_dump(),
            "run": run.model_dump(),
        },
    )


@knowledge_bp.route("/knowledge/wiki/import", methods=["POST"])
@check_auth
def import_wiki_corpus():
    payload = _wiki_import_request()
    try:
        report = get_ingestion_service().import_wiki_jsonl(
            corpus_path=payload["corpus_path"],
            source_id=payload["source_id"],
            default_language=payload["language"],
            strict=payload["strict"],
        )
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc

    source_metadata = {
        **dict(payload.get("source_metadata") or {}),
        "corpus_path": report.get("corpus_path"),
        "issues": list(report.get("issues") or []),
        "import_stats": dict(report.get("stats") or {}),
    }

    if payload["async_mode"]:
        job = get_knowledge_index_job_service().submit_source_records_job(
            source_scope="wiki",
            source_id=str(report.get("source_id") or ""),
            records=list(report.get("records") or []),
            created_by=_current_username(),
            profile_name=payload["profile_name"],
            source_metadata=source_metadata,
            codecompass_prerender=payload["codecompass_prerender"],
        )
        return api_response(
            status="accepted",
            code=202,
            data={
                "import_report": {
                    "source_scope": report.get("source_scope"),
                    "source_id": report.get("source_id"),
                    "corpus_path": report.get("corpus_path"),
                    "stats": report.get("stats"),
                    "issues": report.get("issues"),
                },
                "job": job,
            },
        )
    knowledge_index, run = get_rag_helper_index_service().index_source_records(
        source_scope="wiki",
        source_id=str(report.get("source_id") or ""),
        records=list(report.get("records") or []),
        created_by=_current_username(),
        profile_name=payload["profile_name"],
        source_metadata=source_metadata,
        codecompass_prerender=payload["codecompass_prerender"],
    )
    run_status = _model_status(run)
    return api_response(
        status="success" if run_status == "completed" else "error",
        code=200 if run_status == "completed" else 500,
        message=None if run_status == "completed" else "wiki_import_failed",
        data={
            "import_report": {
                "source_scope": report.get("source_scope"),
                "source_id": report.get("source_id"),
                "corpus_path": report.get("corpus_path"),
                "stats": report.get("stats"),
                "issues": report.get("issues"),
            },
            "knowledge_index": knowledge_index.model_dump(),
            "run": run.model_dump(),
        },
    )


@knowledge_bp.route("/knowledge/wiki/import-url", methods=["POST"])
@check_auth
def import_wiki_corpus_from_url():
    payload = _wiki_import_url_request()
    try:
        report = get_ingestion_service().import_wiki_jsonl_from_url(
            corpus_url=payload["corpus_url"],
            source_id=payload["source_id"],
            default_language=payload["language"],
            strict=payload["strict"],
        )
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc

    source_metadata = {
        **dict(payload.get("source_metadata") or {}),
        "corpus_url": payload["corpus_url"],
        "corpus_path": report.get("corpus_path"),
        "download": dict(report.get("download") or {}),
        "issues": list(report.get("issues") or []),
        "import_stats": dict(report.get("stats") or {}),
    }

    if payload["async_mode"]:
        job = get_knowledge_index_job_service().submit_source_records_job(
            source_scope="wiki",
            source_id=str(report.get("source_id") or ""),
            records=list(report.get("records") or []),
            created_by=_current_username(),
            profile_name=payload["profile_name"],
            source_metadata=source_metadata,
            codecompass_prerender=payload["codecompass_prerender"],
        )
        return api_response(
            status="accepted",
            code=202,
            data={
                "import_report": {
                    "source_scope": report.get("source_scope"),
                    "source_id": report.get("source_id"),
                    "corpus_path": report.get("corpus_path"),
                    "corpus_url": payload["corpus_url"],
                    "download": report.get("download"),
                    "stats": report.get("stats"),
                    "issues": report.get("issues"),
                },
                "job": job,
            },
        )
    knowledge_index, run = get_rag_helper_index_service().index_source_records(
        source_scope="wiki",
        source_id=str(report.get("source_id") or ""),
        records=list(report.get("records") or []),
        created_by=_current_username(),
        profile_name=payload["profile_name"],
        source_metadata=source_metadata,
        codecompass_prerender=payload["codecompass_prerender"],
    )
    run_status = _model_status(run)
    return api_response(
        status="success" if run_status == "completed" else "error",
        code=200 if run_status == "completed" else 500,
        message=None if run_status == "completed" else "wiki_import_failed",
        data={
            "import_report": {
                "source_scope": report.get("source_scope"),
                "source_id": report.get("source_id"),
                "corpus_path": report.get("corpus_path"),
                "corpus_url": payload["corpus_url"],
                "download": report.get("download"),
                "stats": report.get("stats"),
                "issues": report.get("issues"),
            },
            "knowledge_index": knowledge_index.model_dump(),
            "run": run.model_dump(),
        },
    )


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


@knowledge_bp.route("/knowledge/retrieval-preflight", methods=["GET"])
@check_auth
def get_knowledge_retrieval_preflight():
    return api_response(data=get_retrieval_service().get_source_preflight())


@knowledge_bp.route("/knowledge/orchestration-contract", methods=["GET"])
@check_auth
def get_knowledge_orchestration_contract():
    return api_response(data=build_retrieval_orchestration_contract(entrypoint_group="knowledge"))
