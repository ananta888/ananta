"""Authenticated multipart API for browser-folder workspace snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from typing import Callable

from flask import Blueprint, Response, g, jsonify, make_response, request
from werkzeug.exceptions import HTTPException

from agent.auth import check_auth, get_authenticated_source_control_principal
from agent.routes.source_control_access import (
    SourceControlProjectScopeError,
    authorize_route_request,
    bind_source_control_project_selector,
    record_source_control_route_denial,
)
from agent.services.source_control_access_policy import SourceControlAction
from agent.services.source_control_workspace_snapshot_contracts import (
    MAX_SNAPSHOT_FILES,
    MAX_SNAPSHOT_RELATIVE_PATH_BYTES,
    MAX_SNAPSHOT_TOTAL_BYTES,
)
from agent.services.source_control_workspace_snapshot_service import (
    WorkspaceSnapshotUploadError,
    WorkspaceSnapshotUploadService,
)


_ERROR_SCHEMA = "ananta.source-control.error.v1"
_MAX_MULTIPART_OVERHEAD = (
    MAX_SNAPSHOT_FILES * (MAX_SNAPSHOT_RELATIVE_PATH_BYTES + 1024)
    + 64 * 1024
)


@dataclass(frozen=True)
class WorkspaceSnapshotRouteContract:
    endpoint: str
    rule: str
    methods: tuple[str, ...]
    content_type: str
    mutation: bool


WORKSPACE_SNAPSHOT_ROUTE_MATRIX = (
    WorkspaceSnapshotRouteContract(
        endpoint="create_workspace_snapshot",
        rule="/workspace-snapshots",
        methods=("POST",),
        content_type="multipart/form-data",
        mutation=True,
    ),
)


def create_source_control_workspace_snapshots_blueprint(
    service: WorkspaceSnapshotUploadService,
) -> Blueprint:
    blueprint = Blueprint(
        "source_control_workspace_snapshots",
        __name__,
        url_prefix="/api/source-control/v1",
    )

    @blueprint.post("/workspace-snapshots")
    @check_auth
    @_access_guard
    @_boundary
    def create_workspace_snapshot():
        _require_bounded_multipart()
        result = service.upload(
            principal=_scoped_principal(),
            display_name=request.form.get("display_name"),
            files=tuple(request.files.getlist("files")),
            idempotency_key=request.headers.get("Idempotency-Key"),
        )
        return _success(result, status_code=201)

    return blueprint


def _require_bounded_multipart() -> None:
    content_type = str(request.content_type or "").lower()
    if not content_type.startswith("multipart/form-data;"):
        raise WorkspaceSnapshotUploadError(
            "workspace_snapshot_multipart_required",
            status_code=415,
        )
    if (
        request.content_length is not None
        and request.content_length
        > MAX_SNAPSHOT_TOTAL_BYTES + _MAX_MULTIPART_OVERHEAD
    ):
        raise WorkspaceSnapshotUploadError(
            "workspace_snapshot_request_too_large",
            status_code=413,
        )
    if (
        set(request.form) != {"display_name"}
        or len(request.form.getlist("display_name")) != 1
        or set(request.files) != {"files"}
    ):
        raise WorkspaceSnapshotUploadError(
            "workspace_snapshot_multipart_fields_invalid"
        )


def _access_guard(function: Callable):
    @wraps(function)
    def wrapper(*args, **kwargs):
        try:
            principal = _project_principal()
        except SourceControlProjectScopeError as exc:
            authenticated = get_authenticated_source_control_principal()
            record_source_control_route_denial(
                principal=authenticated,
                action=SourceControlAction.refresh,
                resource_kind="workspace_snapshot",
                object_id=str(request.args.get("project_id") or ""),
                status_code=exc.status_code,
                reason_code=exc.reason_code,
            )
            return _error(exc.reason_code, exc.status_code)
        denied = authorize_route_request(
            action=SourceControlAction.refresh,
            resource_kind="workspace_snapshot",
            collection=True,
            principal_override=principal,
            require_project_scope=True,
        )
        if denied is not None:
            return denied
        return function(*args, **kwargs)

    return wrapper


def _project_principal():
    if (
        set(request.args) != {"project_id"}
        or len(request.args.getlist("project_id")) != 1
    ):
        raise SourceControlProjectScopeError(
            "project_id_required"
            if "project_id" not in request.args
            else "query_fields_forbidden",
            status_code=400,
        )
    return bind_source_control_project_selector(
        str(request.args.get("project_id") or "")
    )


def _scoped_principal():
    principal = getattr(g, "source_control_principal", None)
    if principal is None:
        raise WorkspaceSnapshotUploadError(
            "source_control_principal_scope_required",
            status_code=403,
        )
    return principal


def _success(data: object, *, status_code: int) -> Response:
    if not isinstance(data, dict) and not hasattr(data, "items"):
        raise WorkspaceSnapshotUploadError(
            "workspace_snapshot_result_invalid",
            status_code=500,
        )
    response = make_response(
        jsonify(dict(data)),
        status_code,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _error(reason_code: str, status_code: int) -> Response:
    response = make_response(
        jsonify(
            {
                "schema": _ERROR_SCHEMA,
                "error": {"code": reason_code},
            }
        ),
        status_code,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _boundary(function: Callable):
    @wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except WorkspaceSnapshotUploadError as exc:
            return _error(exc.reason_code, exc.status_code)
        except HTTPException:
            raise
        except Exception:
            return _error("workspace_snapshot_internal_error", 500)

    return wrapper


__all__ = [
    "WORKSPACE_SNAPSHOT_ROUTE_MATRIX",
    "WorkspaceSnapshotRouteContract",
    "create_source_control_workspace_snapshots_blueprint",
]
