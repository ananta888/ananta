"""Authenticated Hub control-plane routes for vector-store rollout and tasks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from agent.auth import check_strict_auth, get_request_auth_context
from agent.services.vector_index_task_service import (
    VectorIndexTrustedScope,
    get_vector_index_task_service,
)
from agent.services.vector_store_rollout_service import (
    get_vector_store_rollout_service,
)

vector_store_control_bp = Blueprint(
    "vector_store_control",
    __name__,
    url_prefix="/api/vector-store",
)
_MAX_BODY_BYTES = 2 * 1024 * 1024
_ADMIN_ROLES = frozenset({"admin", "superadmin", "system_admin"})
_GLOBAL_ADMIN_ROLES = frozenset({"superadmin", "system_admin"})


def _identity() -> dict[str, Any]:
    return dict(get_request_auth_context() or {})


def _roles(identity: Mapping[str, Any]) -> set[str]:
    raw = identity.get("roles")
    roles = (
        {str(value).strip().lower() for value in raw}
        if isinstance(raw, (list, tuple, set, frozenset))
        else set()
    )
    direct = str(identity.get("role") or "").strip().lower()
    if direct:
        roles.add(direct)
    return roles


def _require_admin(identity: Mapping[str, Any]) -> None:
    if not (_roles(identity) & _ADMIN_ROLES):
        raise PermissionError("vector_store_admin_required")


def _require_global_admin(identity: Mapping[str, Any]) -> None:
    if not (_roles(identity) & _GLOBAL_ADMIN_ROLES):
        raise PermissionError("vector_store_global_admin_required")


def _authorize_workspace(identity: Mapping[str, Any], workspace_id: str) -> None:
    if _roles(identity) & _GLOBAL_ADMIN_ROLES:
        return
    allowed = set()
    direct = str(identity.get("workspace_id") or "").strip()
    if direct:
        allowed.add(direct)
    raw = identity.get("workspace_ids")
    if isinstance(raw, (list, tuple, set, frozenset)):
        allowed.update(str(item).strip() for item in raw if str(item).strip())
    if str(workspace_id) not in allowed:
        raise PermissionError("vector_store_workspace_forbidden")


def _actor(identity: Mapping[str, Any]) -> str:
    return str(
        identity.get("sub")
        or identity.get("username")
        or "unknown"
    ).strip()


def _body() -> dict[str, Any]:
    if request.content_length is not None and request.content_length > _MAX_BODY_BYTES:
        raise RequestEntityTooLarge()
    request.max_content_length = _MAX_BODY_BYTES
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("vector_store_json_required")
    return value


def _reason(error: Exception) -> str:
    value = str(error).split(":", 1)[0].strip()
    if value.startswith("vector_") and value.replace("_", "").isalnum():
        return value
    return "vector_store_request_failed"


def _error_response(error: Exception):
    if isinstance(error, RequestEntityTooLarge):
        code = 413
    elif isinstance(error, PermissionError):
        code = 403
    elif isinstance(error, RuntimeError):
        code = 409
    else:
        code = 400
    return jsonify({"status": "error", "reason_code": _reason(error)}), code


@vector_store_control_bp.get("/resolved-config")
@check_strict_auth
def resolved_config():
    try:
        identity = _identity()
        _require_admin(identity)
        workspace_id = str(request.args.get("workspace_id") or "").strip()
        _authorize_workspace(identity, workspace_id)
        resolved = get_vector_store_rollout_service().resolve(
            domain=str(request.args.get("domain") or "codecompass"),
            workspace_id=workspace_id,
            profile_name=str(request.args.get("profile_name") or "default"),
        )
        return jsonify(
            {
                "status": "ok",
                "resolved_config": resolved.to_worker_payload(),
            }
        )
    except Exception as exc:
        return _error_response(exc)


@vector_store_control_bp.put("/profiles/<profile_name>/override")
@check_strict_auth
def set_profile_override(profile_name: str):
    try:
        identity = _identity()
        _require_global_admin(identity)
        body = _body()
        if set(body) - {"domain", "override", "expected_revision"}:
            raise ValueError("vector_store_override_fields_forbidden")
        record = get_vector_store_rollout_service().set_profile_override(
            domain=str(body.get("domain") or "codecompass"),
            profile_name=profile_name,
            override=dict(body.get("override") or {}),
            expected_revision=int(body.get("expected_revision") or 0),
            actor=_actor(identity),
        )
        return jsonify({"status": "ok", "override": record.to_dict()}), 201
    except Exception as exc:
        return _error_response(exc)


@vector_store_control_bp.delete("/profiles/<profile_name>/override")
@check_strict_auth
def rollback_profile_override(profile_name: str):
    try:
        identity = _identity()
        _require_global_admin(identity)
        body = _body()
        result = get_vector_store_rollout_service().rollback(
            layer="profile",
            domain=str(body.get("domain") or "codecompass"),
            scope_name=profile_name,
            expected_revision=int(body.get("expected_revision") or 0),
            actor=_actor(identity),
        )
        return jsonify({"status": "ok", "rollback": result})
    except Exception as exc:
        return _error_response(exc)


@vector_store_control_bp.put("/workspaces/<workspace_id>/override")
@check_strict_auth
def set_workspace_override(workspace_id: str):
    try:
        identity = _identity()
        _require_admin(identity)
        _authorize_workspace(identity, workspace_id)
        body = _body()
        if set(body) - {"domain", "override", "expected_revision"}:
            raise ValueError("vector_store_override_fields_forbidden")
        record = get_vector_store_rollout_service().set_workspace_override(
            domain=str(body.get("domain") or "codecompass"),
            workspace_id=workspace_id,
            override=dict(body.get("override") or {}),
            expected_revision=int(body.get("expected_revision") or 0),
            actor=_actor(identity),
        )
        return jsonify({"status": "ok", "override": record.to_dict()}), 201
    except Exception as exc:
        return _error_response(exc)


@vector_store_control_bp.delete("/workspaces/<workspace_id>/override")
@check_strict_auth
def rollback_workspace_override(workspace_id: str):
    try:
        identity = _identity()
        _require_admin(identity)
        _authorize_workspace(identity, workspace_id)
        body = _body()
        result = get_vector_store_rollout_service().rollback(
            layer="workspace",
            domain=str(body.get("domain") or "codecompass"),
            scope_name=workspace_id,
            expected_revision=int(body.get("expected_revision") or 0),
            actor=_actor(identity),
        )
        return jsonify({"status": "ok", "rollback": result})
    except Exception as exc:
        return _error_response(exc)


@vector_store_control_bp.post("/index-tasks")
@check_strict_auth
def submit_index_task():
    try:
        identity = _identity()
        _require_admin(identity)
        body = _body()
        allowed = {
            "operation",
            "workspace_id",
            "repository_id",
            "profile_name",
            "domain",
            "idempotency_key",
            "payload",
            "priority",
        }
        if set(body) - allowed:
            raise ValueError("vector_index_request_fields_forbidden")
        workspace_id = str(body.get("workspace_id") or "").strip()
        _authorize_workspace(identity, workspace_id)
        task = get_vector_index_task_service().submit(
            operation=str(body.get("operation") or ""),
            trusted_scope=VectorIndexTrustedScope(
                workspace_id=workspace_id,
                repository_id=str(body.get("repository_id") or ""),
                profile_name=str(body.get("profile_name") or "default"),
                domain=str(body.get("domain") or "codecompass"),
            ),
            idempotency_key=str(body.get("idempotency_key") or ""),
            payload=(
                dict(body.get("payload") or {})
                if isinstance(body.get("payload"), Mapping)
                else None
            ),
            actor=_actor(identity),
            priority=str(body.get("priority") or "medium"),
        )
        return jsonify({"status": "ok", "task": task}), 202
    except Exception as exc:
        return _error_response(exc)


@vector_store_control_bp.get("/index-tasks/<job_id>")
@check_strict_auth
def get_index_task(job_id: str):
    try:
        identity = _identity()
        _require_admin(identity)
        task = get_vector_index_task_service().get_task(job_id)
        if task is None:
            return jsonify(
                {"status": "error", "reason_code": "vector_index_task_not_found"}
            ), 404
        _authorize_workspace(identity, str(dict(task.get("scope") or {}).get("workspace_id") or ""))
        return jsonify({"status": "ok", "task": task})
    except Exception as exc:
        return _error_response(exc)


@vector_store_control_bp.post("/index-tasks/<job_id>/cancel")
@check_strict_auth
def cancel_index_task(job_id: str):
    try:
        identity = _identity()
        _require_admin(identity)
        task = get_vector_index_task_service().get_task(job_id)
        if task is None:
            raise ValueError("vector_index_task_not_found")
        _authorize_workspace(identity, str(dict(task.get("scope") or {}).get("workspace_id") or ""))
        result = get_vector_index_task_service().cancel(
            job_id=job_id,
            actor=_actor(identity),
        )
        return jsonify({"status": "ok", "task": result})
    except Exception as exc:
        return _error_response(exc)


@vector_store_control_bp.post("/index-tasks/<job_id>/retry")
@check_strict_auth
def retry_index_task(job_id: str):
    try:
        identity = _identity()
        _require_admin(identity)
        task = get_vector_index_task_service().get_task(job_id)
        if task is None:
            raise ValueError("vector_index_task_not_found")
        _authorize_workspace(identity, str(dict(task.get("scope") or {}).get("workspace_id") or ""))
        result = get_vector_index_task_service().retry(
            job_id=job_id,
            actor=_actor(identity),
        )
        return jsonify({"status": "ok", "task": result})
    except Exception as exc:
        return _error_response(exc)


__all__ = ["vector_store_control_bp"]
