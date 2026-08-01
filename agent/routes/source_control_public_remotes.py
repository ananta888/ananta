"""Narrow authenticated routes for secret-free public Git remotes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import wraps
from typing import Callable, Mapping

from flask import Blueprint, Response, g, jsonify, make_response, request

from agent.auth import check_auth, get_authenticated_source_control_principal
from agent.repositories.source_control_public_remote_repository import (
    SourceControlPublicRemotePersistenceError,
)
from agent.routes.source_control_access import (
    SourceControlProjectScopeError,
    authorize_route_request,
    bind_source_control_project_selector,
    record_source_control_route_denial,
)
from agent.services.source_control_access_policy import SourceControlAction
from agent.services.source_control_public_remote_service import (
    SourceControlPublicRemoteError,
    SourceControlPublicRemoteService,
)

_SUCCESS_SCHEMA = "ananta.source-control.api-response.v1"
_ERROR_SCHEMA = "ananta.source-control.error.v1"
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")


@dataclass(frozen=True)
class PublicRemoteRouteContract:
    endpoint: str
    rule: str
    methods: tuple[str, ...]
    mutation: bool


PUBLIC_REMOTE_ROUTE_MATRIX = (
    PublicRemoteRouteContract(
        endpoint="validate_public_remote",
        rule="/public-remotes/validate",
        methods=("POST",),
        mutation=True,
    ),
    PublicRemoteRouteContract(
        endpoint="create_public_remote",
        rule="/public-remotes",
        methods=("POST",),
        mutation=True,
    ),
)


def create_source_control_public_remotes_blueprint(
    service: SourceControlPublicRemoteService,
) -> Blueprint:
    blueprint = Blueprint(
        "source_control_public_remotes",
        __name__,
        url_prefix="/api/source-control/v1",
    )

    @blueprint.post("/public-remotes/validate")
    @check_auth
    @_access_guard
    @_boundary
    def validate_public_remote():
        result = service.validate(
            principal=_scoped_principal(),
            payload=_json_object(),
        )
        return _success(result, status_code=200)

    @blueprint.post("/public-remotes")
    @check_auth
    @_access_guard
    @_boundary
    def create_public_remote():
        result = service.create(
            principal=_scoped_principal(),
            payload=_json_object(),
            idempotency_key=_idempotency_key(),
        )
        return _success(result, status_code=201)

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
                resource_kind="public_remote",
                object_id=str(request.args.get("project_id") or ""),
                status_code=exc.status_code,
                reason_code=exc.reason_code,
            )
            return _error(exc.reason_code, exc.status_code)
        denied = authorize_route_request(
            action=SourceControlAction.refresh,
            resource_kind="public_remote",
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
        raise SourceControlPublicRemoteError(
            "source_control_principal_scope_required",
            status_code=403,
        )
    return principal


def _json_object() -> Mapping[str, object]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, Mapping):
        raise SourceControlPublicRemoteError(
            "public_remote_payload_invalid"
        )
    return dict(payload)


def _idempotency_key() -> str:
    value = str(request.headers.get("Idempotency-Key") or "").strip()
    if _IDEMPOTENCY.fullmatch(value) is None:
        raise SourceControlPublicRemoteError("idempotency_key_required")
    return value


def _success(
    data: Mapping[str, object],
    *,
    status_code: int,
) -> Response:
    response = make_response(
        jsonify({"schema": _SUCCESS_SCHEMA, "data": dict(data)}),
        status_code,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _error(reason_code: str, status_code: int) -> Response:
    response = make_response(
        jsonify(
            {
                "schema": _ERROR_SCHEMA,
                "error": {"code": str(reason_code)},
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
            SourceControlPublicRemoteError,
            SourceControlPublicRemotePersistenceError,
        ) as exc:
            return _error(exc.reason_code, exc.status_code)
        except Exception:
            return _error("public_remote_internal_error", 500)

    return wrapper


__all__ = [
    "PUBLIC_REMOTE_ROUTE_MATRIX",
    "PublicRemoteRouteContract",
    "create_source_control_public_remotes_blueprint",
]
