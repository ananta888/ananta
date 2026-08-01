"""Authenticated API for safe, path-free workspace registration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import wraps
from typing import Callable, Mapping

from flask import Blueprint, Response, g, jsonify, make_response, request

from agent.auth import check_auth, get_authenticated_source_control_principal
from agent.repositories.source_control_workspace_registration_repository import (
    SourceControlWorkspacePersistenceError,
)
from agent.routes.source_control_access import (
    SourceControlProjectScopeError,
    authorize_route_request,
    bind_source_control_project_selector,
    record_source_control_route_denial,
)
from agent.services.source_control_access_policy import SourceControlAction
from agent.services.source_control_workspace_catalog import (
    SourceControlWorkspaceCatalogError,
)
from agent.services.source_control_workspace_registration_service import (
    SourceControlWorkspaceRegistrationError,
    SourceControlWorkspaceRegistrationService,
)

_SUCCESS_SCHEMA = "ananta.source-control.api-response.v1"
_ERROR_SCHEMA = "ananta.source-control.error.v1"
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
_ETAG = re.compile(r'^"workspace-v1:(?P<revision>[1-9][0-9]*)"$')


@dataclass(frozen=True)
class WorkspaceRegistrationRouteContract:
    endpoint: str
    rule: str
    methods: tuple[str, ...]
    mutation: bool


WORKSPACE_REGISTRATION_ROUTE_MATRIX = (
    WorkspaceRegistrationRouteContract(
        endpoint="list_workspace_folders",
        rule="/workspace-folders",
        methods=("GET",),
        mutation=False,
    ),
    WorkspaceRegistrationRouteContract(
        endpoint="validate_workspace_folder",
        rule="/workspace-folders/validate",
        methods=("POST",),
        mutation=False,
    ),
    WorkspaceRegistrationRouteContract(
        endpoint="create_workspace",
        rule="/workspaces",
        methods=("POST",),
        mutation=True,
    ),
    WorkspaceRegistrationRouteContract(
        endpoint="workspace_detail",
        rule="/workspaces/<workspace_id>",
        methods=("GET",),
        mutation=False,
    ),
    WorkspaceRegistrationRouteContract(
        endpoint="disable_workspace",
        rule="/workspaces/<workspace_id>/actions/disable",
        methods=("POST",),
        mutation=True,
    ),
)


def create_source_control_workspace_registrations_blueprint(
    service: SourceControlWorkspaceRegistrationService,
) -> Blueprint:
    blueprint = Blueprint(
        "source_control_workspace_registrations",
        __name__,
        url_prefix="/api/source-control/v1",
    )

    @blueprint.get("/workspace-folders")
    @check_auth
    @_access_guard
    @_boundary
    def list_workspace_folders():
        return _success(
            service.list_folders(
                principal=_scoped_principal()
            )
        )

    @blueprint.post("/workspace-folders/validate")
    @check_auth
    @_access_guard
    @_boundary
    def validate_workspace_folder():
        return _success(
            service.validate(
                principal=_scoped_principal(),
                payload=_json_object(),
            )
        )

    @blueprint.post("/workspaces")
    @check_auth
    @_access_guard
    @_boundary
    def create_workspace():
        result = service.create(
            principal=_scoped_principal(),
            payload=_json_object(),
            idempotency_key=_idempotency_key(),
        )
        return _success(result, status_code=201)

    @blueprint.get("/workspaces/<workspace_id>")
    @check_auth
    @_access_guard
    @_boundary
    def workspace_detail(workspace_id: str):
        return _success(
            service.detail(
                principal=_scoped_principal(),
                workspace_id=workspace_id,
            )
        )

    @blueprint.post("/workspaces/<workspace_id>/actions/disable")
    @check_auth
    @_access_guard
    @_boundary
    def disable_workspace(workspace_id: str):
        if _json_object():
            raise SourceControlWorkspaceRegistrationError(
                "workspace_disable_payload_invalid"
            )
        result = service.disable(
            principal=_scoped_principal(),
            workspace_id=workspace_id,
            expected_revision=_expected_revision(),
            idempotency_key=_idempotency_key(),
        )
        return _success(result)

    return blueprint


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
                resource_kind="registered_workspace",
                object_id=str(request.args.get("project_id") or ""),
                status_code=exc.status_code,
                reason_code=exc.reason_code,
            )
            return _error(exc.reason_code, exc.status_code)
        action = (
            SourceControlAction.list
            if request.method == "GET"
            else SourceControlAction.refresh
        )
        denied = authorize_route_request(
            action=action,
            resource_kind="registered_workspace",
            collection=True,
            principal_override=principal,
            require_project_scope=True,
        )
        if denied is not None:
            return denied
        return function(*args, **kwargs)

    return wrapper


def _project_principal():
    if set(request.args) - {"project_id"}:
        raise SourceControlProjectScopeError(
            "query_fields_forbidden",
            status_code=400,
        )
    return bind_source_control_project_selector(
        str(request.args.get("project_id") or "")
    )


def _scoped_principal():
    principal = getattr(g, "source_control_principal", None)
    if principal is None:
        raise SourceControlWorkspaceRegistrationError(
            "source_control_principal_scope_required",
            status_code=403,
        )
    return principal


def _json_object() -> Mapping[str, object]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, Mapping):
        raise SourceControlWorkspaceRegistrationError(
            "workspace_registration_payload_invalid"
        )
    return dict(payload)


def _idempotency_key() -> str:
    value = str(request.headers.get("Idempotency-Key") or "").strip()
    if _IDEMPOTENCY.fullmatch(value) is None:
        raise SourceControlWorkspaceRegistrationError(
            "idempotency_key_required"
        )
    return value


def _expected_revision() -> int:
    match = _ETAG.fullmatch(str(request.headers.get("If-Match") or ""))
    if match is None:
        raise SourceControlWorkspaceRegistrationError(
            "if_match_required",
            status_code=428,
        )
    return int(match.group("revision"))


def _success(
    data: Mapping[str, object],
    *,
    status_code: int = 200,
) -> Response:
    response = make_response(
        jsonify({"schema": _SUCCESS_SCHEMA, "data": dict(data)}),
        status_code,
    )
    etag = str(data.get("etag") or "")
    if etag:
        response.headers["ETag"] = etag
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
        except (
            SourceControlWorkspaceCatalogError,
            SourceControlWorkspacePersistenceError,
            SourceControlWorkspaceRegistrationError,
        ) as exc:
            return _error(exc.reason_code, exc.status_code)
        except Exception:
            return _error("workspace_registration_internal_error", 500)

    return wrapper


__all__ = [
    "WORKSPACE_REGISTRATION_ROUTE_MATRIX",
    "WorkspaceRegistrationRouteContract",
    "create_source_control_workspace_registrations_blueprint",
]
