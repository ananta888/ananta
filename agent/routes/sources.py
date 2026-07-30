from __future__ import annotations

from typing import Any

from flask import Blueprint, g, request

from agent.auth import admin_required, check_auth
from agent.common.errors import BadRequestError, NotFoundError, api_response
from agent.sources.builtin_sources import load_builtin_source_descriptors
from agent.sources.citation_formatter import format_citation
from agent.sources.source_cache import SourceCache
from agent.sources.source_refresh_service import SourceRefreshService
from agent.sources.source_registry import SourceRegistry
from agent.sources.source_pack_service import SourcePackService
from agent.sources.source_snapshot_store import SourceSnapshotStore
from agent.routes.source_control_access import (
    authorize_route_request,
    filter_visible_resources,
)
from agent.services.source_control_access_policy import SourceControlAction

sources_bp = Blueprint("sources", __name__)


def _registry() -> SourceRegistry:
    return SourceRegistry()


def _snapshots() -> SourceSnapshotStore:
    return SourceSnapshotStore()


def _refresh_service() -> SourceRefreshService:
    return SourceRefreshService(registry=_registry(), snapshots=_snapshots())


def _cache() -> SourceCache:
    return SourceCache()


def _packs() -> SourcePackService:
    return SourcePackService(registry=_registry(), snapshots=_snapshots())


def _sync_builtin_descriptors() -> int:
    registry = _registry()
    created = 0
    for descriptor in load_builtin_source_descriptors():
        source_id = str(descriptor.get("source_id") or "").strip()
        if not source_id:
            continue
        current = registry.get_source(source_id)
        if current is None:
            registry.create_source(descriptor)
            created += 1
    return created


def _source_payload(source: dict[str, Any]) -> dict[str, Any]:
    latest = _snapshots().latest_indexed_snapshot(source_id=str(source.get("source_id") or ""))
    return {
        "source_id": str(source.get("source_id") or ""),
        "source_type": str(source.get("source_type") or ""),
        "display_name": str(source.get("display_name") or ""),
        "enabled": bool(source.get("enabled", True)),
        "trust_level": str(source.get("trust_level") or ""),
        "fetch_source": dict(source.get("fetch_source") or {}),
        "citation_source": dict(source.get("citation_source") or {}),
        "latest_snapshot": latest,
    }


_SOURCE_ACTIONS = {
    "list_sources": SourceControlAction.list,
    "list_source_packs": SourceControlAction.list,
    "get_source_pack": SourceControlAction.detail,
    "bootstrap_source_pack": SourceControlAction.index,
    "source_pack_query_preview": SourceControlAction.query,
    "source_pack_doctor": SourceControlAction.scan,
    "import_open_notebook": SourceControlAction.index,
    "source_chat": SourceControlAction.query,
    "get_source": SourceControlAction.detail,
    "list_source_snapshots": SourceControlAction.artifact,
    "get_source_citation": SourceControlAction.artifact,
    "refresh_due_sources": SourceControlAction.refresh,
    "refresh_source": SourceControlAction.refresh,
    "source_cache_status": SourceControlAction.artifact,
    "source_cache_clear": SourceControlAction.delete,
    "sync_builtin_sources": SourceControlAction.refresh,
}
_SOURCE_COLLECTION_ENDPOINTS = {"list_sources", "refresh_due_sources"}
_SOURCE_GLOBAL_ENDPOINTS = {
    "list_source_packs",
    "source_pack_doctor",
    "import_open_notebook",
    "sync_builtin_sources",
}


@sources_bp.before_request
@check_auth
def _authorize_sources_surface():
    endpoint = str(request.endpoint or "").rsplit(".", 1)[-1]
    action = _SOURCE_ACTIONS.get(endpoint)
    if action is None:
        return None
    if endpoint in _SOURCE_COLLECTION_ENDPOINTS:
        return authorize_route_request(
            action=action,
            resource_kind="source",
            collection=True,
        )
    view_args = request.view_args or {}
    source_id = str(view_args.get("source_id") or "").strip()
    if source_id:
        return authorize_route_request(
            action=action,
            resource_kind="source",
            resource=_registry().get_source(source_id),
            object_id=source_id,
        )
    source_pack_id = str(
        view_args.get("source_pack_id")
        or request.args.get("source_pack_id")
        or ""
    ).strip()
    if source_pack_id:
        try:
            pack = _packs().get_pack(source_pack_id)
        except ValueError:
            pack = None
        return authorize_route_request(
            action=action,
            resource_kind="source_pack",
            resource=pack,
            object_id=source_pack_id,
        )
    if endpoint in _SOURCE_GLOBAL_ENDPOINTS:
        return authorize_route_request(
            action=action,
            resource_kind="source_global",
            resource={},
            object_id=endpoint,
        )
    return authorize_route_request(
        action=action,
        resource_kind="source",
        collection=True,
    )


@sources_bp.route("/sources", methods=["GET"])
@check_auth
def list_sources():
    visible = filter_visible_resources(
        _registry().list_sources(include_disabled=True),
        resource_kind="source",
        object_id=lambda item: str(item.get("source_id") or ""),
    )
    payload = [_source_payload(item) for item in visible]
    return api_response(data=payload)


@sources_bp.route("/sources/packs", methods=["GET"])
@check_auth
def list_source_packs():
    return api_response(data=_packs().list_packs())


@sources_bp.route("/sources/packs/<source_pack_id>", methods=["GET"])
@check_auth
def get_source_pack(source_pack_id: str):
    try:
        pack = _packs().get_pack(source_pack_id)
    except ValueError:
        raise NotFoundError("source_pack_not_found")
    return api_response(data=pack)


@sources_bp.route("/sources/packs/<source_pack_id>/bootstrap", methods=["POST"])
@check_auth
@admin_required
def bootstrap_source_pack(source_pack_id: str):
    _sync_builtin_descriptors()
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise BadRequestError("invalid_payload")
    result = _packs().bootstrap(
        source_pack_id=source_pack_id,
        dry_run=bool(payload.get("dry_run", False)),
        skip_source_ids=[str(item) for item in list(payload.get("skip_source_ids") or []) if str(item).strip()],
        include_optional=bool(payload.get("include_optional_sources", False)),
    )
    return api_response(data=result)


@sources_bp.route("/sources/packs/<source_pack_id>/query-preview", methods=["POST"])
@check_auth
def source_pack_query_preview(source_pack_id: str):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise BadRequestError("invalid_payload")
    query = str(payload.get("query") or "").strip()
    if not query:
        raise BadRequestError("query_required")
    result = _packs().answer_preview(
        source_pack_id=source_pack_id,
        query=query,
        include_wikipedia=bool(payload.get("include_wikipedia", True)),
        include_local_project=bool(payload.get("include_local_project", True)),
    )
    return api_response(data=result)


@sources_bp.route("/sources/doctor", methods=["GET"])
@check_auth
def source_pack_doctor():
    source_pack_id = str(request.args.get("source_pack_id") or "ananta-dev-default").strip() or "ananta-dev-default"
    try:
        report = _packs().doctor(source_pack_id=source_pack_id)
    except ValueError:
        raise NotFoundError("source_pack_not_found")
    return api_response(data=report)


@sources_bp.route("/sources/import/open-notebook", methods=["POST"])
@check_auth
@admin_required
def import_open_notebook():
    payload: dict[str, Any] | None = None
    uploaded = request.files.get("file")
    if uploaded is not None:
        import json as _json

        try:
            payload = _json.loads(uploaded.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise BadRequestError("invalid_open_notebook_export_file")
    else:
        payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise BadRequestError("invalid_payload")

    options = {}
    for key in ("include_notes", "include_insights"):
        raw = request.args.get(key)
        if raw is not None:
            options[key] = str(raw).strip().lower() in {"1", "true", "yes"}

    from agent.sources.open_notebook_importer import get_open_notebook_importer

    result = get_open_notebook_importer().import_export(payload, created_by="api", **options)
    if str(result.get("status") or "") == "failed":
        return api_response(data=result, status="error", message=str(result.get("reason_code") or "import_failed"), code=400)
    return api_response(data=result)


@sources_bp.route("/sources/<source_id>/chat", methods=["POST"])
@check_auth
def source_chat(source_id: str):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise BadRequestError("invalid_payload")
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise BadRequestError("prompt_required")

    from agent.services.source_chat_service import get_source_chat_service

    try:
        result = get_source_chat_service().answer(
            prompt=prompt,
            source_ref=source_id,
            include_insights=bool(payload.get("include_insights", True)),
            include_notes=bool(payload.get("include_notes", False)),
            max_chunks=payload.get("max_chunks"),
            provenance_visibility=str(payload.get("provenance_visibility") or "") or None,
            requested_llm_scope=str(payload.get("llm_scope") or "") or None,
        )
    except ValueError as exc:
        reason = str(exc)
        if reason == "source_not_found":
            raise NotFoundError("source_not_found")
        if reason == "no_retrieval_source_enabled":
            raise BadRequestError("open_notebook_source_disabled")
        raise BadRequestError(reason)

    return api_response(data=result)


@sources_bp.route("/sources/<source_id>", methods=["GET"])
@check_auth
def get_source(source_id: str):
    source = _registry().get_source(source_id)
    if source is None:
        raise NotFoundError("source_not_found")
    return api_response(data=_source_payload(source))


@sources_bp.route("/sources/<source_id>/snapshots", methods=["GET"])
@check_auth
def list_source_snapshots(source_id: str):
    source = _registry().get_source(source_id)
    if source is None:
        raise NotFoundError("source_not_found")
    return api_response(data=_snapshots().list_snapshots(source_id=source_id))


@sources_bp.route("/sources/<source_id>/citation", methods=["GET"])
@check_auth
def get_source_citation(source_id: str):
    source = _registry().get_source(source_id)
    if source is None:
        raise NotFoundError("source_not_found")
    latest = _snapshots().latest_indexed_snapshot(source_id=source_id)
    citation = format_citation(descriptor=source, snapshot=latest, output_format=str(request.args.get("format") or "long"))
    return api_response(data={"source_id": source_id, "latest_snapshot": latest, **citation})


@sources_bp.route("/sources/refresh", methods=["POST"])
@check_auth
def refresh_due_sources():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise BadRequestError("invalid_payload")
    dry_run = bool(payload.get("dry_run", False))
    service = _refresh_service()
    principal = g.source_control_principal
    if principal.is_admin:
        results = service.refresh_due_sources(dry_run=dry_run)
    else:
        visible_sources = filter_visible_resources(
            _registry().list_sources(include_disabled=True),
            resource_kind="source",
            object_id=lambda item: str(item.get("source_id") or ""),
        )
        visible_ids = {
            str(item.get("source_id") or "") for item in visible_sources
        }
        results = []
        for item in service.plan_due_sources():
            source_id = str(item.get("source_id") or "")
            if source_id not in visible_ids:
                continue
            if str(item.get("action") or "") != "refresh":
                results.append(
                    {
                        "source_id": source_id,
                        "status": "skipped",
                        "reason_code": str(item.get("reason_code") or ""),
                    }
                )
                continue
            results.append(
                service.refresh_source(
                    source_id=source_id,
                    dry_run=dry_run,
                )
            )
    return api_response(data={"status": "ok", "dry_run": dry_run, "results": results})


@sources_bp.route("/sources/<source_id>/refresh", methods=["POST"])
@check_auth
def refresh_source(source_id: str):
    source = _registry().get_source(source_id)
    if source is None:
        raise NotFoundError("source_not_found")
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise BadRequestError("invalid_payload")
    report = _refresh_service().refresh_source(
        source_id=source_id,
        dry_run=bool(payload.get("dry_run", False)),
        corpus_url=str(payload.get("corpus_url") or "").strip() or None,
        destination_name=str(payload.get("destination_name") or "").strip() or None,
    )
    return api_response(data=report)


@sources_bp.route("/sources/<source_id>/cache", methods=["GET"])
@check_auth
def source_cache_status(source_id: str):
    source = _registry().get_source(source_id)
    if source is None:
        raise NotFoundError("source_not_found")
    stats = _cache().stats_for_source(source_id=source_id)
    return api_response(data={"source_id": source_id, **stats})


@sources_bp.route("/sources/<source_id>/cache/clear", methods=["POST"])
@check_auth
def source_cache_clear(source_id: str):
    source = _registry().get_source(source_id)
    if source is None:
        raise NotFoundError("source_not_found")
    removed = _cache().clear_source(source_id=source_id)
    stats = _cache().stats_for_source(source_id=source_id)
    return api_response(data={"source_id": source_id, "removed_files": removed, **stats})


@sources_bp.route("/sources/actions/sync-builtins", methods=["POST"])
@check_auth
@admin_required
def sync_builtin_sources():
    created = _sync_builtin_descriptors()
    return api_response(data={"status": "ok", "created": created})
