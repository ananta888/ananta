"""Connection intake, catalog and connection read routes of the Source Control v1 API.
"""

from __future__ import annotations

from collections.abc import Callable

from flask import Blueprint, request

from agent.routes.source_control_v1_authorization import SourceControlV1RequestGuard
from agent.routes.source_control_v1_http import (
    CONNECTION_FILTERS,
    SourceControlApiError,
    boundary,
    bounded_int,
    connection_intent_payload,
    json_object,
    require_exact_fields,
    require_idempotency_key,
    required_string,
    success_response,
)
from agent.routes.source_control_v1_port import SourceControlV1Port
from agent.services.source_control_access_policy import SourceControlAction


def register_connection_intake_routes(
    blueprint: Blueprint,
    *,
    api: SourceControlV1Port,
    request_guard: SourceControlV1RequestGuard,
    check_auth: Callable,
) -> None:
    """Connections, content admissions and scoped catalogs."""

    @blueprint.post("/connections/validate")
    @check_auth
    @boundary
    def validate_connection():
        denied = request_guard.authorize(api,
            action=SourceControlAction.POLICY,
            resource_kind="source_connection",
            collection=True,
        )
        if denied is not None:
            return denied
        payload = connection_intent_payload()
        if payload.get("dry_run") is not True:
            raise SourceControlApiError("dry_run_required")
        return success_response(
            api.validate_connection(
                principal=request_guard.principal(),
                payload=payload,
            )
        )

    @blueprint.post("/connections")
    @check_auth
    @boundary
    def create_connection():
        denied = request_guard.authorize(api,
            action=SourceControlAction.POLICY,
            resource_kind="source_connection",
            collection=True,
        )
        if denied is not None:
            return denied
        payload = connection_intent_payload()
        if payload.get("dry_run") is not False:
            raise SourceControlApiError("dry_run_false_required")
        return success_response(
            api.create_connection(
                principal=request_guard.principal(),
                payload=payload,
                idempotency_key=require_idempotency_key(),
            ),
            status=201,
        )

    @blueprint.post("/content-admissions/validate")
    @check_auth
    @boundary
    def validate_content_admission():
        denied = request_guard.authorize(api,
            action=SourceControlAction.POLICY,
            resource_kind="source_connection",
            collection=True,
        )
        if denied is not None:
            return denied
        return success_response(
            api.validate_content_admission(
                principal=request_guard.principal(),
                payload=json_object(),
            )
        )

    @blueprint.post("/content-admissions")
    @check_auth
    @boundary
    def create_content_admission():
        denied = request_guard.authorize(api,
            action=SourceControlAction.INDEX,
            resource_kind="source_connection",
            collection=True,
        )
        if denied is not None:
            return denied
        return success_response(
            api.create_content_admission(
                principal=request_guard.principal(),
                payload=json_object(),
                idempotency_key=require_idempotency_key(),
            ),
            status=201,
        )

    def catalog_response(
        *,
        catalog: str,
        allowed: frozenset[str],
    ):
        principal, project_id, cursor, limit, filters = request_guard.catalog_request(
            allowed=allowed
        )
        denied = request_guard.authorize(api,
            action=SourceControlAction.LIST,
            resource_kind="source_connection",
            collection=True,
            principal_override=principal,
        )
        if denied is not None:
            return denied
        return success_response(
            api.list_source_control_catalog(
                principal=principal,
                catalog=catalog,
                project_id=project_id,
                cursor=cursor,
                limit=limit,
                filters=filters,
            )
        )

    @blueprint.get("/workspaces")
    @check_auth
    @boundary
    def list_workspaces():
        return catalog_response(
            catalog="workspaces",
            allowed=frozenset(
                {"project_id", "cursor", "limit", "q", "enabled"}
            ),
        )

    @blueprint.get("/registered-remotes")
    @check_auth
    @boundary
    def list_registered_remotes():
        return catalog_response(
            catalog="registered_remotes",
            allowed=frozenset(
                {
                    "project_id",
                    "cursor",
                    "limit",
                    "q",
                    "kind",
                    "state",
                }
            ),
        )

    @blueprint.get("/index-profiles")
    @check_auth
    @boundary
    def list_index_profiles():
        return catalog_response(
            catalog="index_profiles",
            allowed=frozenset(
                {"project_id", "cursor", "limit", "q", "source"}
            ),
        )


def register_connection_read_routes(
    blueprint: Blueprint,
    *,
    api: SourceControlV1Port,
    request_guard: SourceControlV1RequestGuard,
    check_auth: Callable,
) -> None:
    """Connection list/detail, run history and index comparison."""

    @blueprint.get("/connections")
    @check_auth
    @boundary
    def list_connections():
        denied = request_guard.authorize(api,
            action=SourceControlAction.LIST,
            resource_kind="source_connection",
            collection=True,
        )
        if denied is not None:
            return denied
        unknown = set(request.args) - CONNECTION_FILTERS
        if unknown:
            raise SourceControlApiError("connection_filter_invalid")
        limit = bounded_int(
            request.args.get("limit"),
            field="limit",
            default=50,
            minimum=1,
            maximum=200,
        )
        filters = {
            key: value
            for key in ("state", "connector_type", "owner_id", "sensitivity")
            if (value := request.args.get(key))
        }
        return success_response(
            api.list_connections(
                principal=request_guard.principal(),
                cursor=request.args.get("cursor"),
                limit=limit,
                filters=filters,
            )
        )

    @blueprint.get("/connections/<connection_id>")
    @check_auth
    @boundary
    def get_connection(connection_id: str):
        denied = request_guard.authorize(api,
            action=SourceControlAction.DETAIL,
            resource_kind="source_connection",
            resource_id=connection_id,
        )
        if denied is not None:
            return denied
        data, etag = api.get_connection(
            principal=request_guard.principal(),
            connection_id=connection_id,
        )
        response = success_response(data)
        response.headers["ETag"] = etag
        return response

    @blueprint.get("/connections/<connection_id>/runs")
    @check_auth
    @boundary
    def run_history(connection_id: str):
        denied = request_guard.authorize(api,
            action=SourceControlAction.DETAIL,
            resource_kind="source_connection",
            resource_id=connection_id,
        )
        if denied is not None:
            return denied
        return success_response(
            api.run_history(
                principal=request_guard.principal(),
                connection_id=connection_id,
                cursor=request.args.get("cursor"),
                limit=bounded_int(
                    request.args.get("limit"),
                    field="limit",
                    default=50,
                    minimum=1,
                    maximum=200,
                ),
            )
        )

    @blueprint.post("/indices/compare")
    @check_auth
    @boundary
    def compare_indices():
        payload = json_object()
        require_exact_fields(
            payload,
            frozenset({"left_index_id", "right_index_id"}),
            required=frozenset({"left_index_id", "right_index_id"}),
        )
        left = required_string(payload, "left_index_id")
        right = required_string(payload, "right_index_id")
        denied = request_guard.authorize(api,
            action=SourceControlAction.DETAIL,
            resource_kind="knowledge_index",
            resource_id=left,
        )
        if denied is not None:
            return denied
        denied = request_guard.authorize(api,
            action=SourceControlAction.DETAIL,
            resource_kind="knowledge_index",
            resource_id=right,
        )
        if denied is not None:
            return denied
        return success_response(
            api.compare_indices(
                principal=request_guard.principal(),
                left_index_id=left,
                right_index_id=right,
            )
        )
