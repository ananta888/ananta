from __future__ import annotations

from typing import Any

from flask import Blueprint, request

from agent.auth import check_auth
from agent.common.errors import BadRequestError, NotFoundError, api_response
from agent.sources.builtin_sources import load_builtin_source_descriptors
from agent.sources.citation_formatter import format_citation
from agent.sources.source_cache import SourceCache
from agent.sources.source_refresh_service import SourceRefreshService
from agent.sources.source_registry import SourceRegistry
from agent.sources.source_snapshot_store import SourceSnapshotStore

sources_bp = Blueprint("sources", __name__)


def _registry() -> SourceRegistry:
    return SourceRegistry()


def _snapshots() -> SourceSnapshotStore:
    return SourceSnapshotStore()


def _refresh_service() -> SourceRefreshService:
    return SourceRefreshService(registry=_registry(), snapshots=_snapshots())


def _cache() -> SourceCache:
    return SourceCache()


def _sync_builtin_descriptors() -> None:
    registry = _registry()
    for descriptor in load_builtin_source_descriptors():
        source_id = str(descriptor.get("source_id") or "").strip()
        if not source_id:
            continue
        current = registry.get_source(source_id)
        if current is None:
            registry.create_source(descriptor)


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


@sources_bp.route("/sources", methods=["GET"])
@check_auth
def list_sources():
    _sync_builtin_descriptors()
    payload = [_source_payload(item) for item in _registry().list_sources(include_disabled=True)]
    return api_response(data=payload)


@sources_bp.route("/sources/<source_id>", methods=["GET"])
@check_auth
def get_source(source_id: str):
    _sync_builtin_descriptors()
    source = _registry().get_source(source_id)
    if source is None:
        raise NotFoundError("source_not_found")
    return api_response(data=_source_payload(source))


@sources_bp.route("/sources/<source_id>/snapshots", methods=["GET"])
@check_auth
def list_source_snapshots(source_id: str):
    _sync_builtin_descriptors()
    source = _registry().get_source(source_id)
    if source is None:
        raise NotFoundError("source_not_found")
    return api_response(data=_snapshots().list_snapshots(source_id=source_id))


@sources_bp.route("/sources/<source_id>/citation", methods=["GET"])
@check_auth
def get_source_citation(source_id: str):
    _sync_builtin_descriptors()
    source = _registry().get_source(source_id)
    if source is None:
        raise NotFoundError("source_not_found")
    latest = _snapshots().latest_indexed_snapshot(source_id=source_id)
    citation = format_citation(descriptor=source, snapshot=latest, output_format=str(request.args.get("format") or "long"))
    return api_response(data={"source_id": source_id, "latest_snapshot": latest, **citation})


@sources_bp.route("/sources/refresh", methods=["POST"])
@check_auth
def refresh_due_sources():
    _sync_builtin_descriptors()
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise BadRequestError("invalid_payload")
    dry_run = bool(payload.get("dry_run", False))
    results = _refresh_service().refresh_due_sources(dry_run=dry_run)
    return api_response(data={"status": "ok", "dry_run": dry_run, "results": results})


@sources_bp.route("/sources/<source_id>/refresh", methods=["POST"])
@check_auth
def refresh_source(source_id: str):
    _sync_builtin_descriptors()
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
    _sync_builtin_descriptors()
    source = _registry().get_source(source_id)
    if source is None:
        raise NotFoundError("source_not_found")
    stats = _cache().stats_for_source(source_id=source_id)
    return api_response(data={"source_id": source_id, **stats})


@sources_bp.route("/sources/<source_id>/cache/clear", methods=["POST"])
@check_auth
def source_cache_clear(source_id: str):
    _sync_builtin_descriptors()
    source = _registry().get_source(source_id)
    if source is None:
        raise NotFoundError("source_not_found")
    removed = _cache().clear_source(source_id=source_id)
    stats = _cache().stats_for_source(source_id=source_id)
    return api_response(data={"source_id": source_id, "removed_files": removed, **stats})
