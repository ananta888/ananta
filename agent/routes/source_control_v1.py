"""Canonical versioned Hub API for source-control governance.

This module owns the canonical ``source_control_v1`` blueprint (URL prefix
``/api/source-control/v1``) and the legacy redirect aliases. The route
handlers are registered by focused registrar modules that receive their
dependencies explicitly (SRP, DIP):

* ``source_control_v1_connection_routes`` -- intake, catalogs, connection reads
* ``source_control_v1_grant_routes`` -- grants and index-access preparation
* ``source_control_v1_lifecycle_routes`` -- operations, lifecycle, bulk, events
* ``source_control_v1_index_read_routes`` -- graph/query/artifacts, CodeHug
* ``source_control_v1_policy_routes`` -- effective access, Context Policy

Shared HTTP helpers live in ``source_control_v1_http``, the authorization
matrix and request guard in ``source_control_v1_authorization`` and the
runtime port in ``source_control_v1_port``; the public names stay
importable from here.
"""

from __future__ import annotations

from collections.abc import Callable

from flask import Blueprint, redirect, request

from agent.routes.source_control_access import authorize_route_request
from agent.routes.source_control_v1_authorization import (
    SOURCE_CONTROL_V1_AUTHORIZATION_BY_ENDPOINT,
    SOURCE_CONTROL_V1_AUTHORIZATION_MATRIX,
    SourceControlV1AuthorizationRule,
    SourceControlV1RequestGuard,
)
from agent.routes.source_control_v1_connection_routes import (
    register_connection_intake_routes,
    register_connection_read_routes,
)
from agent.routes.source_control_v1_grant_routes import register_grant_routes
from agent.routes.source_control_v1_http import (
    SourceControlApiError,
    boundary,
    check_auth,
    error_response,
)
from agent.routes.source_control_v1_index_read_routes import (
    register_artifact_routes,
    register_index_read_routes,
)
from agent.routes.source_control_v1_lifecycle_routes import (
    register_bulk_and_event_routes,
    register_lifecycle_routes,
)
from agent.routes.source_control_v1_policy_routes import (
    register_policy_routes,
)
from agent.routes.source_control_v1_port import SourceControlV1Port
from agent.services.source_control_access_policy import SourceControlAction
from agent.services.source_control_legacy_usage import (
    BoundedLegacySourceControlUsage,
)


def create_source_control_v1_blueprint(
    api: SourceControlV1Port,
    *,
    request_guard: SourceControlV1RequestGuard | None = None,
    auth_decorator: Callable | None = None,
) -> Blueprint:
    """Create the canonical API with an injected Hub composition root.

    ``request_guard`` and ``auth_decorator`` are keyword-only seams; the
    production defaults are ``SourceControlV1RequestGuard()`` and the audited
    ``check_auth`` wrapper.
    """

    blueprint = Blueprint(
        "source_control_v1",
        __name__,
        url_prefix="/api/source-control/v1",
    )
    guard = request_guard or SourceControlV1RequestGuard()
    authenticate = auth_decorator or check_auth

    @blueprint.before_request
    def require_authorization_matrix_entry():
        endpoint = str(request.endpoint or "").rsplit(".", 1)[-1]
        if endpoint not in SOURCE_CONTROL_V1_AUTHORIZATION_BY_ENDPOINT:
            return error_response(
                "source_control_authorization_matrix_missing",
                500,
            )
        return None

    # Registration order mirrors the historical single-function layout.
    for register in (
        register_connection_intake_routes,
        register_grant_routes,
        register_connection_read_routes,
        register_lifecycle_routes,
        register_index_read_routes,
        register_bulk_and_event_routes,
        register_policy_routes,
        register_artifact_routes,
    ):
        register(
            blueprint,
            api=api,
            request_guard=guard,
            check_auth=authenticate,
        )
    return blueprint


def create_source_control_legacy_alias_blueprint(
    usage: BoundedLegacySourceControlUsage,
) -> Blueprint:
    """Retain observable redirects without duplicating domain behavior."""

    blueprint = Blueprint(
        "source_control_legacy_aliases",
        __name__,
        url_prefix="/api/source-control",
    )

    def _alias(target: str, label: str):
        denied = authorize_route_request(
            action=SourceControlAction.LIST,
            resource_kind="source_connection",
            collection=True,
        )
        if denied is not None:
            return denied
        usage.record(label)
        return redirect(target, code=308)

    @blueprint.route("/connections", methods=["GET", "POST"])
    @check_auth
    @boundary
    def legacy_connections():
        return _alias(
            "/api/source-control/v1/connections",
            "connections",
        )

    @blueprint.route(
        "/connections/<path:tail>",
        methods=["GET", "POST", "DELETE"],
    )
    @check_auth
    @boundary
    def legacy_connection_detail(tail: str):
        return _alias(
            f"/api/source-control/v1/connections/{tail}",
            "connection_detail",
        )

    @blueprint.route(
        "/context-policies",
        methods=["GET", "POST"],
    )
    @check_auth
    @boundary
    def legacy_context_policies():
        return _alias(
            "/api/source-control/v1/context-policies",
            "context_policies",
        )

    return blueprint


__all__ = [
    "SOURCE_CONTROL_V1_AUTHORIZATION_MATRIX",
    "SourceControlApiError",
    "SourceControlV1AuthorizationRule",
    "SourceControlV1Port",
    "SourceControlV1RequestGuard",
    "create_source_control_legacy_alias_blueprint",
    "create_source_control_v1_blueprint",
]
