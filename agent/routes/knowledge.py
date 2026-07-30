from __future__ import annotations

import time
from pathlib import Path

from flask import Blueprint, g, request

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import BadRequestError, ConflictError, NotFoundError, api_response
from agent.config import settings
from agent.db_models import KnowledgeCollectionDB
from agent.models import (
    KnowledgeCollectionCreateRequest,
    KnowledgeCollectionIndexRequest,
    KnowledgeCollectionSearchRequest,
    KnowledgeSourceIndexRequest,
)
from agent.services.file_type_support_service import (
    FileTypeSupportFilter,
    FileTypeSupportFilterError,
    get_file_type_support_service,
    parse_optional_boolean,
)
from agent.services.repository_registry import get_repository_registry
from agent.services.retrieval_orchestration_contract import build_retrieval_orchestration_contract
from agent.services.retrieval_service import get_retrieval_service
from agent.services.retrieval_source_contract import source_scopes_for_types
from agent.services.service_registry import get_core_services
from agent.services.wiki_import_job_service import get_wiki_import_job_service
from agent.routes.source_control_access import (
    authorize_route_request,
    filter_visible_resources,
)
from agent.services.source_control_access_policy import SourceControlAction
from agent.sources.source_registry import SourceRegistry

knowledge_bp = Blueprint("knowledge", __name__)

_FILE_TYPE_SUPPORT_QUERY_FIELDS = frozenset(
    {
        "priority",
        "support_level",
        "level",
        "dimension",
        "pipeline",
        "missing_parser",
        "missing_runtime",
        "enabled",
    }
)

WIKI_IMPORT_PRESETS = [
    {
        "id": "wikipedia-de-multistream-latest",
        "label": "Wikipedia DE: Artikel Multistream (latest)",
        "description": "Empfohlen fuer ernsthaftes deutsches RAG: echter Wikimedia XML.BZ2 Multistream-Dump plus Index.",
        "corpus_url": "https://dumps.wikimedia.org/dewiki/latest/dewiki-latest-pages-articles-multistream.xml.bz2",
        "index_url": "https://dumps.wikimedia.org/dewiki/latest/dewiki-latest-pages-articles-multistream-index.txt.bz2",
        "source_id": "wikipedia-de-multistream-latest",
        "language": "de",
        "size_hint": "~8.1 GB dump + ~63 MB index",
        "recommended": True,
        "import_format": "mediawiki-multistream",
        "codecompass_prerender": True,
        "mobile_policy": {"network": "unknown", "charging": "unknown", "storage": "unknown"},
    },
    {
        "id": "wikipedia-de-pages-latest",
        "label": "Wikipedia DE: Artikel nicht-Multistream (latest)",
        "description": "Fallback ohne Multistream-Index; meist weniger praktisch fuer grosse lokale Verarbeitung.",
        "corpus_url": "https://dumps.wikimedia.org/dewiki/latest/dewiki-latest-pages-articles.xml.bz2",
        "source_id": "wikipedia-de-pages-latest",
        "language": "de",
        "size_hint": "~7.8 GB",
        "recommended": False,
        "import_format": "mediawiki-xml",
        "codecompass_prerender": True,
        "mobile_policy": {"network": "unknown", "charging": "unknown", "storage": "unknown"},
    },
    {
        "id": "wikipedia-de-zim-mini-2026-04",
        "label": "Wikipedia DE: ZIM mini 2026-04 (Prototyp)",
        "description": "Kleiner Kiwix/ZIM-Prototyp-Dump. Sichtbar fuer Download-Planung; Import benoetigt noch ZIM-Parser.",
        "corpus_url": "https://dumps.wikimedia.org/kiwix/zim/wikipedia/wikipedia_de_all_mini_2026-04.zim",
        "source_id": "wikipedia-de-zim-mini-2026-04",
        "language": "de",
        "size_hint": "~3.9 GiB",
        "recommended": False,
        "import_format": "zim",
        "supported": False,
        "codecompass_prerender": False,
        "mobile_policy": {"network": "unknown", "charging": "unknown", "storage": "unknown"},
    },
    {
        "id": "wikipedia-de-zim-nopic-2026-01",
        "label": "Wikipedia DE: ZIM ohne Bilder 2026-01 (Prototyp)",
        "description": "Groesserer Kiwix/ZIM-Dump ohne Bilder. Sichtbar fuer spaetere ZIM-Unterstuetzung.",
        "corpus_url": "https://dumps.wikimedia.org/kiwix/zim/wikipedia/wikipedia_de_all_nopic_2026-01.zim",
        "source_id": "wikipedia-de-zim-nopic-2026-01",
        "language": "de",
        "size_hint": "~13.6 GiB",
        "recommended": False,
        "import_format": "zim",
        "supported": False,
        "codecompass_prerender": False,
        "mobile_policy": {"network": "unknown", "charging": "unknown", "storage": "unknown"},
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
    index_path = str(payload.get("index_path") or "").strip() or None
    import_format = str(payload.get("import_format") or "").strip() or None
    profile_name = str(payload.get("profile_name") or "").strip() or None
    language = str(payload.get("language") or "en").strip().lower() or "en"
    strict = bool(payload.get("strict", False))
    async_mode = bool(payload.get("async", True))
    codecompass_prerender = bool(payload.get("codecompass_prerender", False))
    raw_source_metadata = payload.get("source_metadata") or {}
    if not isinstance(raw_source_metadata, dict):
        raise BadRequestError("invalid_source_metadata")
    source_metadata = dict(raw_source_metadata)
    return {
        "corpus_path": corpus_path,
        "source_id": source_id,
        "index_path": index_path,
        "import_format": import_format,
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
    index_url = str(payload.get("index_url") or "").strip()
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
    async_mode = bool(payload.get("async", True))
    codecompass_prerender = bool(payload.get("codecompass_prerender", selected_preset.get("codecompass_prerender", False) if selected_preset else False))
    raw_source_metadata = payload.get("source_metadata") or {}
    if not isinstance(raw_source_metadata, dict):
        raise BadRequestError("invalid_source_metadata")
    source_metadata = dict(raw_source_metadata)
    if selected_preset is not None:
        index_url = str(selected_preset.get("index_url") or index_url).strip()
        if selected_preset.get("supported") is False:
            raise BadRequestError("wiki_preset_not_supported")
        source_metadata.setdefault("preset_id", selected_preset["id"])
        source_metadata.setdefault("preset_label", selected_preset["label"])
        source_metadata.setdefault("import_format", selected_preset.get("import_format"))
    return {
        "corpus_url": effective_url,
        "index_url": index_url or None,
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


def _file_type_support_filter() -> FileTypeSupportFilter:
    unknown_fields = sorted(set(request.args) - _FILE_TYPE_SUPPORT_QUERY_FIELDS)
    if unknown_fields:
        raise BadRequestError(
            "invalid_file_type_support_filter",
            {"unknown_fields": unknown_fields},
        )
    support_levels = [
        *request.args.getlist("support_level"),
        *request.args.getlist("level"),
    ]
    try:
        return FileTypeSupportFilter.build(
            priorities=request.args.getlist("priority"),
            support_levels=support_levels,
            dimensions=request.args.getlist("dimension"),
            pipelines=request.args.getlist("pipeline"),
            missing_parser=parse_optional_boolean(
                request.args.get("missing_parser"),
                field_name="missing_parser",
            ),
            missing_runtime=parse_optional_boolean(
                request.args.get("missing_runtime"),
                field_name="missing_runtime",
            ),
            enabled=parse_optional_boolean(
                request.args.get("enabled"),
                field_name="enabled",
            ),
        )
    except FileTypeSupportFilterError as exc:
        raise BadRequestError("invalid_file_type_support_filter") from exc


def _normalize_security_metadata_patch(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise BadRequestError("invalid_security_metadata_patch")
    allowed_keys = {"classification", "source_origin", "sensitivity", "tenancy", "approval_class", "chunk_security_tags"}
    patch: dict = {}
    for key, value in raw.items():
        normalized_key = str(key or "").strip()
        if normalized_key not in allowed_keys:
            continue
        if normalized_key == "chunk_security_tags":
            if not isinstance(value, list):
                raise BadRequestError("invalid_chunk_security_tags")
            patch[normalized_key] = [str(item).strip().lower() for item in value if str(item).strip()]
        else:
            patch[normalized_key] = str(value or "").strip().lower() or None
    if not patch:
        raise BadRequestError("empty_security_metadata_patch")
    return patch


def _apply_security_metadata_patch(*, knowledge_index, patch: dict, actor: str):
    metadata = dict(getattr(knowledge_index, "index_metadata", None) or {})
    security_metadata = dict(metadata.get("security_metadata") or {})
    security_metadata.update({key: value for key, value in patch.items() if value is not None})
    metadata["security_metadata"] = security_metadata
    metadata["security_metadata_updated_by"] = actor
    metadata["security_metadata_updated_at"] = time.time()
    knowledge_index.index_metadata = metadata
    return knowledge_index


def _index_payload(item) -> dict:
    payload = item.model_dump()
    metadata = dict(payload.get("index_metadata") or {})
    payload["security_metadata"] = dict(metadata.get("security_metadata") or {})
    return payload


def _metadata_batch_candidates(payload: dict) -> list:
    ids = [str(item).strip() for item in list(payload.get("knowledge_index_ids") or []) if str(item).strip()]
    candidates = []
    if ids:
        for index_id in ids:
            row = _knowledge_index_repo().get_by_id(index_id)
            if row is not None:
                candidates.append(row)
        return candidates
    source_scope = str(payload.get("source_scope") or "").strip().lower() or None
    return list(_knowledge_index_repo().list_completed(source_scope=source_scope))


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
    indices = filter_visible_resources(
        indices,
        resource_kind="knowledge_index",
        object_id=lambda item: str(item.get("id") or ""),
    )
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


_KNOWLEDGE_ACTIONS = {
    "list_knowledge_collections": SourceControlAction.list,
    "create_knowledge_collection": SourceControlAction.index,
    "get_knowledge_collection": SourceControlAction.detail,
    "index_knowledge_collection": SourceControlAction.index,
    "list_knowledge_index_profiles": SourceControlAction.list,
    "get_knowledge_index_job": SourceControlAction.detail,
    "list_wiki_import_jobs": SourceControlAction.list,
    "get_wiki_import_job": SourceControlAction.detail,
    "pause_wiki_import_job": SourceControlAction.index,
    "resume_wiki_import_job": SourceControlAction.index,
    "cancel_wiki_import_job": SourceControlAction.delete,
    "retry_interrupted_wiki_import_job": SourceControlAction.index,
    "wiki_disk_state": SourceControlAction.artifact,
    "list_wiki_import_presets": SourceControlAction.list,
    "index_knowledge_source_records": SourceControlAction.index,
    "import_wiki_corpus": SourceControlAction.index,
    "import_wiki_corpus_from_url": SourceControlAction.download,
    "search_knowledge_collection": SourceControlAction.query,
    "search_wiki": SourceControlAction.query,
    "get_knowledge_retrieval_preflight": SourceControlAction.list,
    "list_knowledge_indices": SourceControlAction.list,
    "update_knowledge_index_security_metadata": SourceControlAction.policy,
    "batch_update_knowledge_index_security_metadata": SourceControlAction.policy,
    "get_knowledge_orchestration_contract": SourceControlAction.list,
    "get_file_type_support": SourceControlAction.list,
}
_KNOWLEDGE_COLLECTION_ENDPOINTS = {
    "list_knowledge_collections",
    "create_knowledge_collection",
    "list_knowledge_index_profiles",
    "list_wiki_import_jobs",
    "list_wiki_import_presets",
    "get_knowledge_retrieval_preflight",
    "list_knowledge_indices",
    "batch_update_knowledge_index_security_metadata",
    "get_knowledge_orchestration_contract",
    "get_file_type_support",
}
_KNOWLEDGE_GLOBAL_ENDPOINTS = {
    "wiki_disk_state",
    "import_wiki_corpus",
    "import_wiki_corpus_from_url",
    "search_wiki",
}


def _knowledge_job(job_id: str):
    job = get_wiki_import_job_service().get_job(job_id)
    return job or get_knowledge_index_job_service().get_job(job_id)


@knowledge_bp.before_request
@check_auth
def _authorize_knowledge_surface():
    endpoint = str(request.endpoint or "").rsplit(".", 1)[-1]
    action = _KNOWLEDGE_ACTIONS.get(endpoint)
    if action is None:
        return None
    if endpoint in _KNOWLEDGE_COLLECTION_ENDPOINTS:
        return authorize_route_request(
            action=action,
            resource_kind="knowledge",
            collection=True,
        )
    view_args = request.view_args or {}
    collection_id = str(view_args.get("collection_id") or "").strip()
    if collection_id:
        return authorize_route_request(
            action=action,
            resource_kind="knowledge_collection",
            resource=_collection_repo().get_by_id(collection_id),
            object_id=collection_id,
        )
    job_id = str(view_args.get("job_id") or "").strip()
    if job_id:
        return authorize_route_request(
            action=action,
            resource_kind="knowledge_job",
            resource=_knowledge_job(job_id),
            object_id=job_id,
        )
    knowledge_index_id = str(
        view_args.get("knowledge_index_id") or ""
    ).strip()
    if knowledge_index_id:
        return authorize_route_request(
            action=action,
            resource_kind="knowledge_index",
            resource=_knowledge_index_repo().get_by_id(knowledge_index_id),
            object_id=knowledge_index_id,
        )
    if endpoint == "index_knowledge_source_records":
        body = request.get_json(silent=True) or {}
        source_id = (
            str(body.get("source_id") or "").strip()
            if isinstance(body, dict)
            else ""
        )
        if source_id:
            return authorize_route_request(
                action=action,
                resource_kind="source",
                resource=SourceRegistry().get_source(source_id),
                object_id=source_id,
            )
    if endpoint in _KNOWLEDGE_GLOBAL_ENDPOINTS:
        return authorize_route_request(
            action=action,
            resource_kind="knowledge_global",
            resource={},
            object_id=endpoint,
        )
    return authorize_route_request(
        action=action,
        resource_kind="knowledge",
        collection=True,
    )


@knowledge_bp.route("/knowledge/collections", methods=["GET"])
@check_auth
def list_knowledge_collections():
    rows = filter_visible_resources(
        _collection_repo().get_all(),
        resource_kind="knowledge_collection",
        object_id=lambda item: str(getattr(item, "id", "")),
    )
    return api_response(data=[item.model_dump() for item in rows])


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
        KnowledgeCollectionDB(
            name=name,
            description=description,
            created_by=_current_username(),
            collection_metadata={
                "source_control_scope": {
                    "tenant_id": g.source_control_principal.tenant_id,
                    "project_id": g.source_control_principal.project_id,
                    "owner_id": g.source_control_principal.subject_id,
                }
            },
        )
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
    try:
        job = get_knowledge_index_job_service().submit_collection_job(
            collection_id=collection_id,
            artifact_ids=artifact_ids,
            created_by=_current_username(),
            profile_name=payload.profile_name,
            profile_overrides=payload.profile_overrides,
            graph_visual_metrics=(
                payload.graph_visual_metrics.model_dump(by_alias=True)
                if payload.graph_visual_metrics is not None
                else None
            ),
        )
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    return api_response(
        status="accepted",
        code=202,
        data={
            "collection": collection.model_dump(),
            "job": job,
            "execution_mode": "hub_delegated",
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


@knowledge_bp.route("/knowledge/wiki/import-jobs", methods=["GET"])
@check_auth
def list_wiki_import_jobs():
    jobs = filter_visible_resources(
        get_wiki_import_job_service().list_jobs(),
        resource_kind="knowledge_job",
        object_id=lambda item: str(
            item.get("job_id") or item.get("id") or ""
        ),
    )
    return api_response(data={"jobs": jobs})


@knowledge_bp.route("/knowledge/wiki/import-jobs/<job_id>", methods=["GET"])
@check_auth
def get_wiki_import_job(job_id: str):
    job = get_wiki_import_job_service().get_job(job_id)
    if job is None:
        job = get_knowledge_index_job_service().get_job(job_id)
    if job is None:
        raise NotFoundError("wiki_import_job_not_found")
    job_type = str(job.get("job_type") or "").strip().lower()
    scope = str(job.get("source_scope") or "").strip().lower()
    if not (job_type == "wiki_import" or (job_type == "source_records" and scope == "wiki")):
        raise NotFoundError("wiki_import_job_not_found")
    return api_response(data={"job": job})


@knowledge_bp.route("/knowledge/wiki/import-jobs/<job_id>/pause", methods=["POST"])
@check_auth
def pause_wiki_import_job(job_id: str):
    job = get_wiki_import_job_service().pause_job(job_id)
    if job is None:
        raise NotFoundError("wiki_import_job_not_found")
    return api_response(data={"job": job})


@knowledge_bp.route("/knowledge/wiki/import-jobs/<job_id>/resume", methods=["POST"])
@check_auth
def resume_wiki_import_job(job_id: str):
    job = get_wiki_import_job_service().resume_job(job_id)
    if job is None:
        raise NotFoundError("wiki_import_job_not_found")
    return api_response(data={"job": job})


@knowledge_bp.route("/knowledge/wiki/import-jobs/<job_id>/cancel", methods=["POST"])
@check_auth
def cancel_wiki_import_job(job_id: str):
    job = get_wiki_import_job_service().cancel_job(job_id)
    if job is None:
        raise NotFoundError("wiki_import_job_not_found")
    return api_response(data={"job": job})


@knowledge_bp.route("/knowledge/wiki/import-jobs/<job_id>/retry-interrupted", methods=["POST"])
@check_auth
def retry_interrupted_wiki_import_job(job_id: str):
    job = get_wiki_import_job_service().retry_interrupted_job(job_id)
    if job is None:
        raise NotFoundError("wiki_import_job_not_found_or_not_interrupted")
    return api_response(data={"job": job})


@knowledge_bp.route("/knowledge/wiki/disk-state", methods=["GET"])
@check_auth
def wiki_disk_state():
    """Returns the real on-disk state of wiki import files — independent of any job."""
    wiki_dir = Path(settings.data_dir) / "wiki_corpora"
    checkpoint_dir = Path(settings.data_dir) / "wiki_checkpoints"

    def _file_info(p: Path) -> dict | None:
        if not p.exists():
            return None
        stat = p.stat()
        return {"path": p.name, "size_bytes": stat.st_size, "mtime": stat.st_mtime}

    files = []
    if wiki_dir.exists():
        for f in sorted(wiki_dir.iterdir()):
            info = _file_info(f)
            if info is None:
                continue
            name = f.name
            if name.endswith(".partial.chunks_cache.json"):
                kind = "chunks_cache"
            elif name.endswith(".partial.links.jsonl"):
                kind = "partial_links_jsonl"
            elif name.endswith(".links.jsonl"):
                kind = "links_jsonl"
            elif name.endswith(".partial.jsonl"):
                kind = "partial_jsonl"
            elif name.endswith(".compact.jsonl"):
                kind = "compact_jsonl"
            elif name.endswith(".normalized.jsonl"):
                kind = "normalized_jsonl"
            elif name.endswith(".xml.bz2") or name.endswith(".xml.gz"):
                kind = "corpus_compressed"
            elif name.endswith(".xml"):
                kind = "corpus_xml"
            elif name.endswith(".txt.bz2"):
                kind = "index_compressed"
            elif name.endswith(".txt"):
                kind = "index_txt"
            else:
                kind = "other"
            files.append({**info, "kind": kind})

    # CodeCompass output files from knowledge_indices/wiki/
    cc_outputs: list[dict] = []
    ki_wiki_dir = Path(settings.data_dir) / "knowledge_indices" / "wiki"
    if ki_wiki_dir.exists():
        import json as _json_cc
        for index_dir in sorted(ki_wiki_dir.iterdir()):
            if not index_dir.is_dir():
                continue
            for run_dir in sorted(index_dir.iterdir(), reverse=True):
                if not run_dir.is_dir():
                    continue
                run_files = {}
                manifest = {}
                for rf in sorted(run_dir.iterdir()):
                    if rf.name == "manifest.json":
                        try:
                            manifest = _json_cc.loads(rf.read_text(encoding="utf-8"))
                        except Exception:
                            pass
                        continue
                    info = _file_info(rf)
                    if info:
                        run_files[rf.name] = info
                if run_files:
                    cc_outputs.append({
                        "index_id": index_dir.name,
                        "run_id": run_dir.name,
                        "files": run_files,
                        "manifest": {
                            "index_record_count": manifest.get("index_record_count"),
                            "node_count": manifest.get("node_count"),
                            "relation_record_count": manifest.get("relation_record_count"),
                            "link_edge_count": manifest.get("link_edge_count"),
                            "profile_name": manifest.get("profile_name"),
                            "generated_at": manifest.get("generated_at"),
                        } if manifest else None,
                    })

    checkpoints = []
    if checkpoint_dir.exists():
        import json as _json
        for f in sorted(checkpoint_dir.glob("*.json")):
            try:
                data = _json.loads(f.read_text(encoding="utf-8"))
                checkpoints.append({"file": f.name, **data})
            except Exception:
                pass

    return api_response(data={"files": files, "checkpoints": checkpoints, "codecompass_outputs": cc_outputs})


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
    try:
        job = get_knowledge_index_job_service().submit_source_records_job(
            source_scope=source_scope,
            source_id=source_id,
            records=list(payload.records or []),
            created_by=_current_username(),
            profile_name=payload.profile_name,
            source_metadata=dict(payload.source_metadata or {}),
            graph_visual_metrics=(
                payload.graph_visual_metrics.model_dump(by_alias=True)
                if payload.graph_visual_metrics is not None
                else None
            ),
        )
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    return api_response(
        status="accepted",
        code=202,
        data={"job": job, "execution_mode": "hub_delegated"},
    )


@knowledge_bp.route("/knowledge/wiki/import", methods=["POST"])
@check_auth
def import_wiki_corpus():
    payload = _wiki_import_request()
    try:
        report = get_ingestion_service().import_wiki_corpus(
            corpus_path=payload["corpus_path"],
            index_path=payload["index_path"],
            source_id=payload["source_id"],
            default_language=payload["language"],
            strict=payload["strict"],
            import_format=payload["import_format"],
        )
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc

    source_metadata = {
        **dict(payload.get("source_metadata") or {}),
        "corpus_path": report.get("corpus_path"),
        "index_path": report.get("index_path"),
        "import_format": payload.get("import_format") or report.get("format"),
        "issues": list(report.get("issues") or []),
        "import_stats": dict(report.get("stats") or {}),
    }

    try:
        job = get_knowledge_index_job_service().submit_source_records_job(
            source_scope="wiki",
            source_id=str(report.get("source_id") or ""),
            records=list(report.get("records") or []),
            created_by=_current_username(),
            profile_name=payload["profile_name"],
            source_metadata=source_metadata,
            codecompass_prerender=payload["codecompass_prerender"],
        )
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    return api_response(
        status="accepted",
        code=202,
        data={
            "import_report": {
                "source_scope": report.get("source_scope"),
                "source_id": report.get("source_id"),
                "corpus_path": report.get("corpus_path"),
                "index_path": report.get("index_path"),
                "jsonl_cache_path": report.get("jsonl_cache_path"),
                "format": report.get("format"),
                "stats": report.get("stats"),
                "issues": report.get("issues"),
            },
            "job": job,
            "execution_mode": "hub_delegated",
        },
    )


@knowledge_bp.route("/knowledge/wiki/import-url", methods=["POST"])
@check_auth
def import_wiki_corpus_from_url():
    payload = _wiki_import_url_request()
    return _import_wiki_corpus_from_url_legacy(payload)


def _import_wiki_corpus_from_url_legacy(payload: dict | None = None):
    """Compatibility entrypoint; indexing is always delegated to a worker task."""
    payload = payload or _wiki_import_url_request()
    try:
        report = get_ingestion_service().import_wiki_jsonl_from_url(
            corpus_url=payload["corpus_url"],
            index_url=payload["index_url"],
            source_id=payload["source_id"],
            default_language=payload["language"],
            strict=payload["strict"],
        )
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc

    source_metadata = {
        **dict(payload.get("source_metadata") or {}),
        "corpus_url": payload["corpus_url"],
        "index_url": payload["index_url"],
        "corpus_path": report.get("corpus_path"),
        "index_path": report.get("index_path"),
        "jsonl_cache_path": report.get("jsonl_cache_path"),
        "download": dict(report.get("download") or {}),
        "issues": list(report.get("issues") or []),
        "import_stats": dict(report.get("stats") or {}),
    }

    try:
        job = get_knowledge_index_job_service().submit_source_records_job(
            source_scope="wiki",
            source_id=str(report.get("source_id") or ""),
            records=list(report.get("records") or []),
            created_by=_current_username(),
            profile_name=payload["profile_name"],
            source_metadata=source_metadata,
            codecompass_prerender=payload["codecompass_prerender"],
        )
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    return api_response(
        status="accepted",
        code=202,
        data={
            "import_report": {
                "source_scope": report.get("source_scope"),
                "source_id": report.get("source_id"),
                "corpus_path": report.get("corpus_path"),
                "index_path": report.get("index_path"),
                "jsonl_cache_path": report.get("jsonl_cache_path"),
                "corpus_url": payload["corpus_url"],
                "index_url": payload["index_url"],
                "download": report.get("download"),
                "stats": report.get("stats"),
                "issues": report.get("issues"),
            },
            "job": job,
            "execution_mode": "hub_delegated",
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


@knowledge_bp.route("/knowledge/wiki/search", methods=["POST"])
@check_auth
def search_wiki():
    """Direct wiki search without requiring a collection.

    Body:
      query: str
      top_k: int  (default 10)
    """
    body = request.get_json(silent=True) or {}
    query = str(body.get("query") or "").strip()
    if not query:
        raise BadRequestError("query_required")
    top_k = max(1, min(int(body.get("top_k") or 10), 50))
    from agent.services.retrieval_source_contract import source_scopes_for_types
    source_scopes = source_scopes_for_types({"wiki"})
    chunks = get_knowledge_index_retrieval_service().search(
        query, top_k=top_k, source_scopes=source_scopes
    )
    return api_response(data={
        "query": query,
        "top_k": top_k,
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
    })


@knowledge_bp.route("/knowledge/retrieval-preflight", methods=["GET"])
@check_auth
def get_knowledge_retrieval_preflight():
    return api_response(data=get_retrieval_service().get_source_preflight())


@knowledge_bp.route("/knowledge/indices", methods=["GET"])
@knowledge_bp.route("/knowledge/indexes", methods=["GET"])
@check_auth
def list_knowledge_indices():
    source_scope = str(request.args.get("source_scope") or "").strip().lower() or None
    limit = max(1, min(int(request.args.get("limit") or 100), 500))
    rows = list(_knowledge_index_repo().list_completed(source_scope=source_scope))[:limit]
    rows = filter_visible_resources(
        rows,
        resource_kind="knowledge_index",
        object_id=lambda item: str(getattr(item, "id", "")),
    )
    return api_response(
        data={
            "items": [_index_payload(item) for item in rows],
            "count": len(rows),
            "source_scope": source_scope,
            "limit": limit,
        }
    )


@knowledge_bp.route("/knowledge/indices/<knowledge_index_id>/metadata/security", methods=["POST"])
@check_auth
def update_knowledge_index_security_metadata(knowledge_index_id: str):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise BadRequestError("invalid_payload")
    patch = _normalize_security_metadata_patch(dict(payload.get("security_metadata_patch") or {}))
    row = _knowledge_index_repo().get_by_id(knowledge_index_id)
    if row is None:
        raise NotFoundError("knowledge_index_not_found")
    actor = _current_username()
    saved = _knowledge_index_repo().save(_apply_security_metadata_patch(knowledge_index=row, patch=patch, actor=actor))
    log_audit(
        "knowledge_security_metadata_updated",
        {
            "knowledge_index_id": knowledge_index_id,
            "source_scope": saved.source_scope,
            "actor": actor,
            "patch_keys": sorted(patch.keys()),
        },
    )
    return api_response(data={"knowledge_index": _index_payload(saved)})


@knowledge_bp.route("/knowledge/indices/metadata/security/batch", methods=["POST"])
@check_auth
def batch_update_knowledge_index_security_metadata():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise BadRequestError("invalid_payload")
    patch = _normalize_security_metadata_patch(dict(payload.get("security_metadata_patch") or {}))
    dry_run = bool(payload.get("dry_run", False))
    limit = max(1, min(int(payload.get("limit") or 200), 1000))
    candidates = _metadata_batch_candidates(payload)[:limit]
    actor = _current_username()
    updated_ids: list[str] = []
    if not dry_run:
        for row in candidates:
            saved = _knowledge_index_repo().save(_apply_security_metadata_patch(knowledge_index=row, patch=patch, actor=actor))
            updated_ids.append(str(saved.id))
    else:
        updated_ids = [str(getattr(row, "id", "")) for row in candidates if str(getattr(row, "id", ""))]
    log_audit(
        "knowledge_security_metadata_batch_updated",
        {
            "actor": actor,
            "updated_count": len(updated_ids),
            "dry_run": dry_run,
            "patch_keys": sorted(patch.keys()),
            "source_scope": str(payload.get("source_scope") or "").strip().lower() or None,
        },
    )
    return api_response(
        data={
            "updated_count": len(updated_ids),
            "updated_ids": updated_ids,
            "dry_run": dry_run,
            "patch": patch,
            "limit": limit,
        }
    )


@knowledge_bp.route("/knowledge/orchestration-contract", methods=["GET"])
@check_auth
def get_knowledge_orchestration_contract():
    return api_response(data=build_retrieval_orchestration_contract(entrypoint_group="knowledge"))


@knowledge_bp.route("/knowledge/file-type-support", methods=["GET"])
@check_auth
def get_file_type_support():
    """Return the Hub-owned, read-only CodeCompass support projection."""

    filters = _file_type_support_filter()
    try:
        payload = get_file_type_support_service().support_matrix(filters)
    except FileTypeSupportFilterError as exc:
        raise BadRequestError("invalid_file_type_support_filter") from exc
    return api_response(data=payload)
