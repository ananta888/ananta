"""Admin/project-owner API for server-resolved Git authorizations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import wraps
from typing import Callable, Mapping

from flask import Blueprint, Response, jsonify, make_response, request

from agent.auth import check_auth, get_authenticated_source_control_principal
from agent.repositories.hub_git_authorization_repository import (
    HubGitAuthorizationPersistenceError,
)
from agent.routes.source_control_access import authorize_route_request
from agent.services.hub_git_authorization_provisioning import (
    HubGitAuthorizationProvisioningError,
    HubGitAuthorizationProvisioningService,
)
from agent.services.source_control_access_policy import SourceControlAction

_SUCCESS_SCHEMA = "ananta.source-control.api-response.v1"
_ERROR_SCHEMA = "ananta.source-control.error.v1"
_IDEMPOTENCY_KEY = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$"
)
_ETAG = re.compile(r'^"git-auth-v1:(?P<revision>[1-9][0-9]*)"$')
_TRANSITION_FIELDS = frozenset({"repository"})


@dataclass(frozen=True)
class GitAuthorizationRouteContract:
    endpoint: str
    rule: str
    methods: tuple[str, ...]
    mutation: bool


GIT_AUTHORIZATION_ROUTE_MATRIX = (
    GitAuthorizationRouteContract(
        "validate_authorization",
        "/git-authorizations/validate",
        ("POST",),
        True,
    ),
    GitAuthorizationRouteContract(
        "provision_authorization",
        "/git-authorizations",
        ("POST",),
        True,
    ),
    GitAuthorizationRouteContract(
        "list_authorizations",
        "/git-authorizations",
        ("GET",),
        False,
    ),
    GitAuthorizationRouteContract(
        "authorization_health",
        "/git-authorizations/health",
        ("GET",),
        False,
    ),
    GitAuthorizationRouteContract(
        "authorization_detail",
        "/git-authorizations/<authorization_ref>",
        ("GET",),
        False,
    ),
    GitAuthorizationRouteContract(
        "revoke_authorization",
        "/git-authorizations/<authorization_ref>/actions/revoke",
        ("POST",),
        True,
    ),
    GitAuthorizationRouteContract(
        "record_scope_loss",
        "/git-authorizations/<authorization_ref>/actions/scope-loss",
        ("POST",),
        True,
    ),
)


def create_source_control_git_authorizations_blueprint(
    service: HubGitAuthorizationProvisioningService,
) -> Blueprint:
    blueprint = Blueprint(
        "source_control_git_authorizations",
        __name__,
        url_prefix="/api/source-control/v1",
    )

    @blueprint.post("/git-authorizations/validate")
    @check_auth
    @_access_guard
    @_boundary
    def validate_authorization():
        result = service.validate(
            principal=get_authenticated_source_control_principal(),
            payload=_json_object(),
        )
        return _success(result)

    @blueprint.post("/git-authorizations")
    @check_auth
    @_access_guard
    @_boundary
    def provision_authorization():
        result = service.provision(
            principal=get_authenticated_source_control_principal(),
            payload=_json_object(),
            idempotency_key=_idempotency_key(),
        )
        return _success(result, etag=str(result.get("etag") or ""))

    @blueprint.get("/git-authorizations")
    @check_auth
    @_access_guard
    @_boundary
    def list_authorizations():
        unknown = set(request.args) - {
            "cursor",
            "limit",
            "kind",
            "state",
        }
        if unknown:
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_query_fields_invalid"
            )
        try:
            limit = int(request.args.get("limit", "50"))
        except ValueError:
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_limit_invalid"
            ) from None
        result = service.list_authorizations(
            principal=get_authenticated_source_control_principal(),
            cursor=request.args.get("cursor"),
            limit=limit,
            authorization_kind=request.args.get("kind"),
            authorization_state=request.args.get("state"),
        )
        return _success(result)

    @blueprint.get("/git-authorizations/health")
    @check_auth
    @_access_guard
    @_boundary
    def authorization_health():
        result = service.health(
            principal=get_authenticated_source_control_principal()
        )
        status_code = 200 if result.get("status") == "healthy" else 503
        return _success(result, status_code=status_code)

    @blueprint.get("/git-authorizations/<authorization_ref>")
    @check_auth
    @_access_guard
    @_boundary
    def authorization_detail(authorization_ref: str):
        unknown = set(request.args) - {"repository"}
        if unknown:
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_query_fields_invalid"
            )
        result = service.detail(
            principal=get_authenticated_source_control_principal(),
            authorization_ref=authorization_ref,
            repository=request.args.get("repository"),
        )
        return _success(result, etag=str(result.get("etag") or ""))

    @blueprint.post(
        "/git-authorizations/<authorization_ref>/actions/revoke"
    )
    @check_auth
    @_access_guard
    @_boundary
    def revoke_authorization(authorization_ref: str):
        result = service.revoke(
            principal=get_authenticated_source_control_principal(),
            authorization_ref=authorization_ref,
            repository=_transition_repository(),
            expected_revision=_expected_revision(),
            idempotency_key=_idempotency_key(),
        )
        return _success(result, etag=str(result.get("etag") or ""))

    @blueprint.post(
        "/git-authorizations/<authorization_ref>/actions/scope-loss"
    )
    @check_auth
    @_access_guard
    @_boundary
    def record_scope_loss(authorization_ref: str):
        result = service.record_scope_loss(
            principal=get_authenticated_source_control_principal(),
            authorization_ref=authorization_ref,
            repository=_transition_repository(),
            expected_revision=_expected_revision(),
            idempotency_key=_idempotency_key(),
        )
        return _success(result, etag=str(result.get("etag") or ""))

    return blueprint


def _access_guard(function: Callable):
    """Apply the common Hub policy before entering provisioning code."""

    @wraps(function)
    def wrapper(*args, **kwargs):
        denied = authorize_route_request(
            action=SourceControlAction.refresh,
            resource_kind="git_authorization",
            collection=True,
        )
        if denied is not None:
            return denied
        return function(*args, **kwargs)

    return wrapper


def _json_object() -> Mapping[str, object]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, Mapping):
        raise HubGitAuthorizationProvisioningError(
            "git_authorization_payload_invalid"
        )
    return dict(payload)


def _transition_repository() -> str | None:
    payload = _json_object()
    if set(payload) != _TRANSITION_FIELDS:
        raise HubGitAuthorizationProvisioningError(
            "git_authorization_transition_fields_invalid"
        )
    repository = payload.get("repository")
    return str(repository) if repository is not None else None


def _idempotency_key() -> str:
    value = str(request.headers.get("Idempotency-Key") or "").strip()
    if _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise HubGitAuthorizationProvisioningError(
            "idempotency_key_required"
        )
    return value


def _expected_revision() -> int:
    match = _ETAG.fullmatch(str(request.headers.get("If-Match") or ""))
    if match is None:
        raise HubGitAuthorizationProvisioningError(
            "if_match_required",
            status_code=428,
        )
    return int(match.group("revision"))


def _success(
    data: Mapping[str, object],
    *,
    status_code: int = 200,
    etag: str = "",
) -> Response:
    response = make_response(
        jsonify({"schema": _SUCCESS_SCHEMA, "data": dict(data)}),
        status_code,
    )
    if etag:
        response.headers["ETag"] = etag
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
        except HubGitAuthorizationProvisioningError as exc:
            return _error(exc.reason_code, exc.status_code)
        except HubGitAuthorizationPersistenceError as exc:
            reason_code = exc.reason_code
            status_code = (
                404
                if reason_code == "git_authorization_not_found"
                else 409
                if reason_code.endswith("_conflict")
                else 400
            )
            return _error(reason_code, status_code)
        except Exception as exc:
            reason_code = str(
                getattr(exc, "reason_code", "")
                or "git_authorization_internal_error"
            )
            status_code = int(getattr(exc, "status_code", 500) or 500)
            if not 400 <= status_code <= 599:
                status_code = 500
            return _error(reason_code, status_code)

    return wrapper


__all__ = [
    "GIT_AUTHORIZATION_ROUTE_MATRIX",
    "GitAuthorizationRouteContract",
    "create_source_control_git_authorizations_blueprint",
]
