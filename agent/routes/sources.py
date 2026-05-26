from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from flask import Blueprint, request

from agent.auth import check_auth
from agent.common.errors import BadRequestError, NotFoundError, api_response
from agent.sources.builtin_sources import load_builtin_source_descriptors
from agent.sources.keycloak_fetcher import KeycloakDocsFetcher
from agent.sources.source_registry import SourceRegistry
from agent.sources.source_snapshot_store import SourceSnapshotStore
from agent.sources.wikimedia_downloader import WikimediaDownloader

sources_bp = Blueprint("sources", __name__)


def _registry() -> SourceRegistry:
    return SourceRegistry()


def _snapshots() -> SourceSnapshotStore:
    return SourceSnapshotStore()


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
    citation = dict(source.get("citation_source") or {})
    latest = _snapshots().latest_indexed_snapshot(source_id=source_id)
    human = (
        f"{citation.get('title', '')} — {citation.get('publisher', '')}. "
        f"{citation.get('canonical_url', '')} (retrieved {citation.get('retrieved_at', '')}). "
        f"snapshot={latest.get('snapshot_id') if isinstance(latest, dict) else 'none'}; "
        f"license={citation.get('license_ref', '')}"
    )
    return api_response(data={"source_id": source_id, "citation": citation, "latest_snapshot": latest, "human_readable": human.strip()})


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
    source_type = str(source.get("source_type") or "")
    if source_type == "keycloak_docs":
        report = KeycloakDocsFetcher(snapshot_store=_snapshots()).fetch(descriptor=source, dry_run=bool(payload.get("dry_run", False)))
        if not bool(payload.get("dry_run", False)):
            _snapshots().mark_superseded(source_id=source_id, keep_snapshot_id=str(report["snapshot"]["snapshot_id"]))
        return api_response(data={"status": "ok", "report": report})
    if source_type == "wikimedia_dump":
        corpus_url = str(payload.get("corpus_url") or "").strip()
        destination_name = str(payload.get("destination_name") or "").strip()
        if not corpus_url or not destination_name:
            queued = _snapshots().build_snapshot(
                source_id=source_id,
                descriptor_hash=str((source.get("extensions") or {}).get("descriptor_hash") or "0" * 64),
                content_payload={"source_id": source_id},
                metadata_payload={"hint": "provide corpus_url and destination_name for large dump refresh"},
                status="queued",
                reason_code="download_parameters_required",
                human_message="For Wikimedia dump refresh provide corpus_url and destination_name.",
            )
            _snapshots().save_snapshot(queued)
            return api_response(data={"status": "queued", "report": {"snapshot": queued}})
        destination = Path("data/wiki_corpora") / destination_name
        report = WikimediaDownloader(snapshot_store=_snapshots()).download(
            source_id=source_id,
            descriptor_hash=str((source.get("extensions") or {}).get("descriptor_hash") or "0" * 64),
            url=corpus_url,
            destination=destination,
            max_parallel=1,
        )
        _snapshots().mark_superseded(source_id=source_id, keep_snapshot_id=str(report["snapshot"]["snapshot_id"]))
        return api_response(data={"status": "ok", "report": report})
    raise BadRequestError("unsupported_source_type")

