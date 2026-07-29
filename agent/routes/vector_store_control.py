"""Authenticated Hub control-plane routes for vector-store rollout and tasks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from flask import Blueprint, g, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from agent.auth import check_strict_auth, get_request_auth_context
from agent.services.vector_index_task_attestation_service import (
    VectorIndexTaskSigningConfigurationError,
)
from agent.services.vector_index_task_service import (
    VectorIndexTrustedScope,
    get_vector_index_task_service,
)
from agent.services.vector_store_authorization_policy import (
    VectorAdminAuthorizationContext,
    get_vector_store_authorization_policy,
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


def _identity() -> dict[str, Any]:
    return dict(get_request_auth_context() or {})


def _authorization() -> VectorAdminAuthorizationContext:
    return get_vector_store_authorization_policy().from_identity(
        _identity(),
        authenticated_admin=bool(
            getattr(g, "is_admin", False)
            and not (getattr(g, "user", {}) or {})
        ),
        source="vector_store_control_route",
    )


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
    elif isinstance(error, VectorIndexTaskSigningConfigurationError):
        code = 503
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
        authorization = _authorization()
        workspace_id = str(request.args.get("workspace_id") or "").strip()
        get_vector_store_authorization_policy().require_workspace_admin(
            authorization,
            workspace_id,
        )
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
        authorization = _authorization()
        get_vector_store_authorization_policy().require_global_admin(
            authorization
        )
        body = _body()
        if set(body) - {"domain", "override", "expected_revision"}:
            raise ValueError("vector_store_override_fields_forbidden")
        record = get_vector_store_rollout_service().set_profile_override(
            domain=str(body.get("domain") or "codecompass"),
            profile_name=profile_name,
            override=dict(body.get("override") or {}),
            expected_revision=int(body.get("expected_revision") or 0),
            actor=authorization.actor,
        )
        return jsonify({"status": "ok", "override": record.to_dict()}), 201
    except Exception as exc:
        return _error_response(exc)


@vector_store_control_bp.delete("/profiles/<profile_name>/override")
@check_strict_auth
def rollback_profile_override(profile_name: str):
    try:
        authorization = _authorization()
        get_vector_store_authorization_policy().require_global_admin(
            authorization
        )
        body = _body()
        result = get_vector_store_rollout_service().rollback(
            layer="profile",
            domain=str(body.get("domain") or "codecompass"),
            scope_name=profile_name,
            expected_revision=int(body.get("expected_revision") or 0),
            actor=authorization.actor,
        )
        return jsonify({"status": "ok", "rollback": result})
    except Exception as exc:
        return _error_response(exc)


@vector_store_control_bp.put("/workspaces/<workspace_id>/override")
@check_strict_auth
def set_workspace_override(workspace_id: str):
    try:
        authorization = _authorization()
        get_vector_store_authorization_policy().require_workspace_admin(
            authorization,
            workspace_id,
        )
        body = _body()
        if set(body) - {"domain", "override", "expected_revision"}:
            raise ValueError("vector_store_override_fields_forbidden")
        record = get_vector_store_rollout_service().set_workspace_override(
            domain=str(body.get("domain") or "codecompass"),
            workspace_id=workspace_id,
            override=dict(body.get("override") or {}),
            expected_revision=int(body.get("expected_revision") or 0),
            actor=authorization.actor,
        )
        return jsonify({"status": "ok", "override": record.to_dict()}), 201
    except Exception as exc:
        return _error_response(exc)


@vector_store_control_bp.delete("/workspaces/<workspace_id>/override")
@check_strict_auth
def rollback_workspace_override(workspace_id: str):
    try:
        authorization = _authorization()
        get_vector_store_authorization_policy().require_workspace_admin(
            authorization,
            workspace_id,
        )
        body = _body()
        result = get_vector_store_rollout_service().rollback(
            layer="workspace",
            domain=str(body.get("domain") or "codecompass"),
            scope_name=workspace_id,
            expected_revision=int(body.get("expected_revision") or 0),
            actor=authorization.actor,
        )
        return jsonify({"status": "ok", "rollback": result})
    except Exception as exc:
        return _error_response(exc)


@vector_store_control_bp.post("/index-tasks")
@check_strict_auth
def submit_index_task():
    try:
        authorization = _authorization()
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
        get_vector_store_authorization_policy().require_workspace_admin(
            authorization,
            workspace_id,
        )
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
            actor=authorization.actor,
            priority=str(body.get("priority") or "medium"),
        )
        return jsonify({"status": "ok", "task": task}), 202
    except Exception as exc:
        return _error_response(exc)


@vector_store_control_bp.get("/index-tasks/<job_id>")
@check_strict_auth
def get_index_task(job_id: str):
    try:
        authorization = _authorization()
        task = get_vector_index_task_service().get_task(job_id)
        if task is None:
            return jsonify(
                {"status": "error", "reason_code": "vector_index_task_not_found"}
            ), 404
        get_vector_store_authorization_policy().require_workspace_admin(
            authorization,
            str(
                dict(task.get("scope") or {}).get(
                    "workspace_id"
                )
                or ""
            ),
        )
        return jsonify({"status": "ok", "task": task})
    except Exception as exc:
        return _error_response(exc)


@vector_store_control_bp.post("/index-tasks/<job_id>/cancel")
@check_strict_auth
def cancel_index_task(job_id: str):
    try:
        authorization = _authorization()
        task = get_vector_index_task_service().get_task(job_id)
        if task is None:
            raise ValueError("vector_index_task_not_found")
        get_vector_store_authorization_policy().require_workspace_admin(
            authorization,
            str(
                dict(task.get("scope") or {}).get(
                    "workspace_id"
                )
                or ""
            ),
        )
        result = get_vector_index_task_service().cancel(
            job_id=job_id,
            actor=authorization.actor,
        )
        return jsonify({"status": "ok", "task": result})
    except Exception as exc:
        return _error_response(exc)


@vector_store_control_bp.post("/index-tasks/<job_id>/retry")
@check_strict_auth
def retry_index_task(job_id: str):
    try:
        authorization = _authorization()
        task = get_vector_index_task_service().get_task(job_id)
        if task is None:
            raise ValueError("vector_index_task_not_found")
        get_vector_store_authorization_policy().require_workspace_admin(
            authorization,
            str(
                dict(task.get("scope") or {}).get(
                    "workspace_id"
                )
                or ""
            ),
        )
        result = get_vector_index_task_service().retry(
            job_id=job_id,
            actor=authorization.actor,
        )
        return jsonify({"status": "ok", "task": result})
    except Exception as exc:
        return _error_response(exc)


__all__ = ["vector_store_control_bp"]
