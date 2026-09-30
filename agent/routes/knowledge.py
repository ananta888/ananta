"""Knowledge collection, index-job, wiki import and retrieval routes.

Route handlers stay here with their service lookups. Collaborators:

* ``knowledge_route_requests`` -- request parsing and payload shaping
* ``knowledge_route_access`` -- endpoint to source-control action map
* ``knowledge_wiki_presets`` -- curated wiki corpus presets
* ``knowledge_wiki_disk_state`` -- on-disk wiki import inventory
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, g, request

from agent.auth import admin_required, check_auth
from agent.common.audit import log_audit
from agent.common.errors import BadRequestError, ConflictError, NotFoundError, api_response
from agent.config import settings
from agent.db_models import KnowledgeCollectionDB
from agent.routes.knowledge_route_access import (
    KNOWLEDGE_ACTIONS as _KNOWLEDGE_ACTIONS,
)
from agent.routes.knowledge_route_access import (
    KNOWLEDGE_COLLECTION_ENDPOINTS as _KNOWLEDGE_COLLECTION_ENDPOINTS,
)
from agent.routes.knowledge_route_access import (
    KNOWLEDGE_GLOBAL_ENDPOINTS as _KNOWLEDGE_GLOBAL_ENDPOINTS,
)
from agent.routes.knowledge_route_requests import (
    FILE_TYPE_SUPPORT_QUERY_FIELDS as _FILE_TYPE_SUPPORT_QUERY_FIELDS,  # noqa: F401 - compatibility re-export
)
from agent.routes.knowledge_route_requests import (
    apply_security_metadata_patch as _apply_security_metadata_patch,
)
from agent.routes.knowledge_route_requests import (
    collection_create_request as _collection_create_request,
)
from agent.routes.knowledge_route_requests import (
    collection_index_request as _collection_index_request,
)
from agent.routes.knowledge_route_requests import (
    collection_search_request as _collection_search_request,
)
from agent.routes.knowledge_route_requests import (
    current_username as _current_username,
)
from agent.routes.knowledge_route_requests import (
    file_type_support_filter as _file_type_support_filter,
)
from agent.routes.knowledge_route_requests import (
    knowledge_index_payload as _index_payload,
)
from agent.routes.knowledge_route_requests import (
    model_status as _model_status,  # noqa: F401 - compatibility re-export
)
from agent.routes.knowledge_route_requests import (
    normalize_security_metadata_patch as _normalize_security_metadata_patch,
)
from agent.routes.knowledge_route_requests import (
    source_index_request as _source_index_request,
)
from agent.routes.knowledge_route_requests import (
    strict_if_match_version,
)
from agent.routes.knowledge_route_requests import (
    wiki_import_request as _wiki_import_request,
)
from agent.routes.knowledge_route_requests import (
    wiki_import_url_request as _wiki_import_url_request,
)
from agent.routes.knowledge_wiki_disk_state import collect_wiki_disk_state
from agent.routes.knowledge_wiki_presets import WIKI_IMPORT_PRESETS as WIKI_IMPORT_PRESETS
from agent.routes.source_control_access import (
    authorize_route_request,
    filter_visible_resources,
)
from agent.services.file_type_support_service import (
    FileTypeSupportFilterError,
    get_file_type_support_service,
)
from agent.services.knowledge_index_execution_binding_service import (
    KnowledgeIndexExecutionBindingError,
)
from agent.services.knowledge_index_job_service import (
    KnowledgeIndexCompletionProjectionPending,
)
from agent.services.repository_registry import get_repository_registry
from agent.services.retrieval_orchestration_contract import build_retrieval_orchestration_contract
from agent.services.retrieval_service import get_retrieval_service
from agent.services.retrieval_source_contract import source_scopes_for_types
from agent.services.service_registry import get_core_services
from agent.services.wiki_import_job_service import get_wiki_import_job_service
from agent.sources.source_registry import SourceRegistry

knowledge_bp = Blueprint("knowledge", __name__)


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
    response, status_code = api_response(data={"job": job})
    lock_version = job.get("execution_lock_version")
    if isinstance(lock_version, int) and not isinstance(lock_version, bool):
        response.headers["ETag"] = f'"{lock_version}"'
    projection_lock_version = job.get(
        "completion_projection_lock_version"
    )
    if isinstance(projection_lock_version, int) and not isinstance(
        projection_lock_version,
        bool,
    ):
        response.headers["X-Knowledge-Index-Completion-ETag"] = (
            f'"{projection_lock_version}"'
        )
    return response, status_code


@knowledge_bp.route(
    "/knowledge/index-jobs/<job_id>/reconcile-completion",
    methods=["POST"],
)
@check_auth
@admin_required
def reconcile_knowledge_index_completion(job_id: str):
    """Replay a durable Hub completion projection without Worker transport."""

    payload = request.get_json(silent=True)
    if payload not in (None, {}):
        raise BadRequestError("request_fields_forbidden")
    job_service = get_knowledge_index_job_service()
    if job_service.get_job(job_id) is None:
        # Resolve the object before parsing the caller-controlled OCC token or
        # invoking a mutation.  Unknown/cross-project identifiers therefore
        # converge on the same non-mutating 404 boundary.
        raise NotFoundError("rag_job_not_found")
    raw_if_match = str(request.headers.get("If-Match") or "").strip()
    if not raw_if_match:
        return api_response(
            status="error",
            message="if_match_required",
            data={"reason_code": "if_match_required"},
            code=428,
        )
    expected_projection_lock_version = strict_if_match_version(raw_if_match)

    try:
        result = job_service.reconcile_completion_projection(
            job_id=job_id,
            expected_projection_lock_version=(
                expected_projection_lock_version
            ),
        )
    except KnowledgeIndexCompletionProjectionPending:
        pending_job = job_service.get_job(job_id) or {
            "job_id": job_id,
            "status": "running",
        }
        log_audit(
            "knowledge_index_completion_reconciliation_pending",
            {
                "job_id": job_id,
                "actor": _current_username(),
                "completion_projection_state": pending_job.get(
                    "completion_projection_state"
                ),
            },
        )
        response, status_code = api_response(
            status="pending",
            message=(
                "Worker result accepted; Hub completion projection "
                "pending"
            ),
            data={
                "job": pending_job,
                "reason_code": (
                    "knowledge_index_source_projection_pending"
                ),
                "worker_result_accepted": True,
                "worker_dispatch_retry_allowed": False,
                "reconciliation_required": True,
            },
            code=202,
        )
        lock_version = pending_job.get(
            "completion_projection_lock_version"
        )
        if isinstance(lock_version, int) and not isinstance(
            lock_version,
            bool,
        ):
            completion_etag = f'"{lock_version}"'
            response.headers["ETag"] = completion_etag
            response.headers[
                "X-Knowledge-Index-Completion-ETag"
            ] = completion_etag
        return response, status_code
    except KnowledgeIndexExecutionBindingError as exc:
        status_code = {
            "knowledge_index_execution_not_found": 404,
            "knowledge_index_completion_projection_not_found": 404,
            "knowledge_index_completion_projection_conflict": 412,
            "knowledge_index_completion_projection_not_ready": 409,
        }.get(exc.reason_code, 409)
        return api_response(
            status="error",
            message=exc.reason_code,
            data={"reason_code": exc.reason_code},
            code=status_code,
        )

    log_audit(
        "knowledge_index_completion_reconciled",
        {
            "job_id": job_id,
            "actor": _current_username(),
            "completion_projection_state": result.get(
                "completion_projection_state"
            ),
        },
    )
    response, status_code = api_response(data={"job": result})
    projection_lock_version = result.get(
        "completion_projection_lock_version"
    )
    if isinstance(projection_lock_version, int):
        completion_etag = f'"{projection_lock_version}"'
        response.headers["ETag"] = completion_etag
        response.headers[
            "X-Knowledge-Index-Completion-ETag"
        ] = completion_etag
    return response, status_code


@knowledge_bp.route(
    "/knowledge/index-jobs/<job_id>/reconcile-expired-dispatch",
    methods=["POST"],
)
@check_auth
def reconcile_expired_knowledge_index_dispatch(job_id: str):
    """Explicitly close one expired governed dispatch using strict OCC."""

    payload = request.get_json(silent=True)
    if payload not in (None, {}):
        raise BadRequestError("request_fields_forbidden")
    raw_if_match = str(request.headers.get("If-Match") or "").strip()
    if not raw_if_match:
        return api_response(
            status="error",
            message="if_match_required",
            data={"reason_code": "if_match_required"},
            code=428,
        )
    expected_lock_version = strict_if_match_version(raw_if_match)

    try:
        result = (
            get_knowledge_index_job_service()
            .reconcile_expired_bound_dispatch(
                job_id=job_id,
                expected_lock_version=expected_lock_version,
            )
        )
    except KnowledgeIndexExecutionBindingError as exc:
        status_code = {
            "knowledge_index_execution_not_found": 404,
            "knowledge_index_execution_reconcile_conflict": 412,
            "knowledge_index_execution_dispatch_lease_active": 409,
            "knowledge_index_execution_not_reconcilable": 409,
        }.get(exc.reason_code, 409)
        return api_response(
            status="error",
            message=exc.reason_code,
            data={"reason_code": exc.reason_code},
            code=status_code,
        )
    except ValueError as exc:
        reason_code = str(exc)
        status_code = 404 if reason_code.endswith("not_found") else 409
        return api_response(
            status="error",
            message=reason_code,
            data={"reason_code": reason_code},
            code=status_code,
        )

    log_audit(
        "knowledge_index_expired_dispatch_reconciled",
        {
            "job_id": job_id,
            "actor": _current_username(),
            "execution_lock_version": result[
                "execution_lock_version"
            ],
            "reason_code": result["reason_code"],
        },
    )
    response, status_code = api_response(data={"job": result})
    response.headers["ETag"] = (
        f'"{result["execution_lock_version"]}"'
    )
    return response, status_code


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
    return api_response(data=collect_wiki_disk_state(Path(settings.data_dir)))


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
    effective_source_scopes = source_scopes or {"artifact"}
    search_kwargs = {
        "top_k": top_k,
        "artifact_ids": artifact_ids,
        "source_scopes": effective_source_scopes,
    }
    chunks = get_knowledge_index_retrieval_service().search(query, **search_kwargs)
    return api_response(
        data={
            "collection": collection.model_dump(),
            "query": query,
            "source_policy": {
                "requested": requested_source_types,
                "effective_scopes": sorted(effective_source_scopes),
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
