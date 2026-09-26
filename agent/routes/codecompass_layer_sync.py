"""Post-commit sync API for incremental CodeCompass layers (Hub admin only).

``snapshots`` stores a manifest and names the contents the Hub still needs,
``content`` receives redacted texts by sha256, ``commits`` indexes a
snapshot for a profile (queued, deferred behind a running build, or noop).
Every call is audited; the Hub decides what gets built.
"""

from __future__ import annotations

from flask import Blueprint, current_app, g, request

from agent.auth import admin_required, check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response

codecompass_layer_sync_bp = Blueprint("codecompass_layer_sync", __name__)
PREFIX = "/api/codecompass/layer-sync"


def _service():
    return current_app.extensions.get("codecompass_layer_sync_service")


def _error(code: str, status: int):
    return api_response(status="error", message=code, data={"error": code}, code=status)


def _body() -> dict | None:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else None


def _audit(action: str, **details) -> None:
    log_audit("codecompass_layer_sync", {"action": action, "actor": str(getattr(g, "user", "") or ""), **details})


@codecompass_layer_sync_bp.route(f"{PREFIX}/snapshots", methods=["POST"])
@check_auth
@admin_required
def sync_snapshot():
    service, body = _service(), _body()
    if service is None:
        return _error("codecompass_layers_disabled", 404)
    if body is None:
        return _error("invalid_json_object", 400)
    try:
        result = service.accept_snapshot(body.get("manifest"))
    except ValueError as error:
        return _error(str(error), 400)
    _audit("snapshot", snapshot_ref=result["snapshot_ref"], missing=len(result["missing_content_sha256"]))
    return api_response(result)


@codecompass_layer_sync_bp.route(f"{PREFIX}/content", methods=["POST"])
@check_auth
@admin_required
def sync_content():
    service, body = _service(), _body()
    if service is None:
        return _error("codecompass_layers_disabled", 404)
    if body is None:
        return _error("invalid_json_object", 400)
    try:
        return api_response(service.accept_content(body.get("texts")))
    except ValueError as error:
        return _error(str(error), 400)


@codecompass_layer_sync_bp.route(f"{PREFIX}/commits", methods=["POST"])
@check_auth
@admin_required
def sync_commit():
    from agent.services.codecompass_layer_sync_service import LayerSyncConflict

    service, body = _service(), _body()
    if service is None:
        return _error("codecompass_layers_disabled", 404)
    if body is None:
        return _error("invalid_json_object", 400)
    try:
        result = service.commit(profile_id=str(body.get("profile_id") or ""),
                                snapshot_ref=str(body.get("snapshot_ref") or ""),
                                commit_sha=str(body.get("commit_sha") or ""))
    except KeyError as error:
        return _error(str(error).strip("'"), 404)
    except LayerSyncConflict as error:
        return _error(str(error), 409)
    except (ValueError, RuntimeError) as error:
        return _error(str(error), 400)
    _audit("commit", profile_id=str(body.get("profile_id") or ""), commit_sha=str(body.get("commit_sha") or ""),
           status=str(result.get("status") or ""), task_id=str(result.get("task_id") or ""))
    return api_response(result)
