"""Grant preset, grant administration and index-access routes of the Source Control v1 API.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from flask import Blueprint

from agent.routes.source_control_v1_authorization import SourceControlV1RequestGuard
from agent.routes.source_control_v1_http import (
    SourceControlApiError,
    boundary,
    json_object,
    require_exact_fields,
    require_grant_mutation_headers,
    success_response,
)
from agent.routes.source_control_v1_port import SourceControlV1Port
from agent.services.source_control_access_policy import SourceControlAction


def register_grant_routes(
    blueprint: Blueprint,
    *,
    api: SourceControlV1Port,
    request_guard: SourceControlV1RequestGuard,
    check_auth: Callable,
) -> None:
    """Grant presets, grants and prepare-index-access."""

    @blueprint.get("/grant-presets")
    @check_auth
    @boundary
    def list_grant_presets():
        denied = request_guard.authorize(api,
            action=SourceControlAction.LIST,
            resource_kind="source_access_grant",
            collection=True,
        )
        if denied is not None:
            return denied
        principal, project_id, cursor, limit, filters = request_guard.catalog_request(
            allowed=frozenset(
                {
                    "project_id",
                    "cursor",
                    "limit",
                    "q",
                    "operation",
                    "transformation",
                }
            )
        )
        return success_response(
            api.list_grant_presets(
                principal=principal,
                project_id=project_id,
                cursor=cursor,
                limit=limit,
                filters=filters,
            )
        )

    @blueprint.get("/grants")
    @check_auth
    @boundary
    def list_grants():
        denied = request_guard.authorize(api,
            action=SourceControlAction.LIST,
            resource_kind="source_access_grant",
            collection=True,
        )
        if denied is not None:
            return denied
        principal, project_id, cursor, limit, filters = request_guard.catalog_request(
            allowed=frozenset(
                {
                    "project_id",
                    "cursor",
                    "limit",
                    "state",
                    "source_revision_id",
                    "destination_id",
                }
            )
        )
        return success_response(
            api.list_grants(
                principal=principal,
                project_id=project_id,
                cursor=cursor,
                limit=limit,
                filters=filters,
            )
        )

    @blueprint.post("/grants")
    @check_auth
    @boundary
    def create_grant():
        denied = request_guard.authorize(api,
            action=SourceControlAction.POLICY,
            resource_kind="source_access_grant",
            collection=True,
        )
        if denied is not None:
            return denied
        principal, project_id, _, _, _ = request_guard.catalog_request(
            allowed=frozenset({"project_id"})
        )
        payload = json_object()
        fields = frozenset(
            {
                "source_revision_id",
                "destination_id",
                "policy_id",
                "preset_id",
                "duration_seconds",
            }
        )
        require_exact_fields(payload, fields, required=fields)
        if_match, idempotency_key = require_grant_mutation_headers()
        data = api.create_grant(
            principal=principal,
            project_id=project_id,
            payload=payload,
            if_match=if_match,
            idempotency_key=idempotency_key,
        )
        response = success_response(data, status=201)
        grant = data.get("grant")
        if isinstance(grant, Mapping) and grant.get("etag"):
            response.headers["ETag"] = f'"{grant["etag"]}"'
        return response

    @blueprint.post("/grants/<grant_id>/actions/revoke")
    @check_auth
    @boundary
    def revoke_grant(grant_id: str):
        denied = request_guard.authorize(api,
            action=SourceControlAction.POLICY,
            resource_kind="source_access_grant",
            collection=True,
        )
        if denied is not None:
            return denied
        principal, project_id, _, _, _ = request_guard.catalog_request(
            allowed=frozenset({"project_id"})
        )
        payload = json_object()
        fields = frozenset({"reason_code"})
        require_exact_fields(payload, fields, required=fields)
        if_match, idempotency_key = require_grant_mutation_headers()
        data = api.revoke_grant(
            principal=principal,
            project_id=project_id,
            grant_id=grant_id,
            payload=payload,
            if_match=if_match,
            idempotency_key=idempotency_key,
        )
        response = success_response(data)
        grant = data.get("grant")
        if isinstance(grant, Mapping) and grant.get("etag"):
            response.headers["ETag"] = f'"{grant["etag"]}"'
        return response

    @blueprint.get(
        "/connections/<connection_id>/actions/prepare-index-access"
    )
    @check_auth
    @boundary
    def prepare_index_access_options(connection_id: str):
        principal, project_id, _, _, _ = request_guard.catalog_request(
            allowed=frozenset({"project_id"})
        )
        denied = request_guard.authorize(api,
            action=SourceControlAction.INDEX,
            resource_kind="source_connection",
            resource_id=connection_id,
            principal_override=principal,
        )
        if denied is not None:
            return denied
        data = api.prepare_index_access_options(
            principal=principal,
            project_id=project_id,
            connection_id=connection_id,
        )
        response = success_response(data)
        etag = str(data.get("etag") or "").strip().strip('"')
        if etag:
            response.headers["ETag"] = f'"{etag}"'
        return response

    @blueprint.post(
        "/connections/<connection_id>/actions/prepare-index-access"
    )
    @check_auth
    @boundary
    def prepare_index_access(connection_id: str):
        principal, project_id, _, _, _ = request_guard.catalog_request(
            allowed=frozenset({"project_id"})
        )
        denied = request_guard.authorize(api,
            action=SourceControlAction.INDEX,
            resource_kind="source_connection",
            resource_id=connection_id,
            principal_override=principal,
        )
        if denied is not None:
            return denied
        payload = json_object()
        fields = frozenset(
            {
                "source_revision_id",
                "destination_id",
                "option_id",
                "duration_seconds",
                "confirmed",
            }
        )
        require_exact_fields(payload, fields, required=fields)
        if payload.get("confirmed") is not True:
            raise SourceControlApiError(
                "index_access_confirmation_required"
            )
        if_match, idempotency_key = require_grant_mutation_headers()
        data = api.prepare_index_access(
            principal=principal,
            project_id=project_id,
            connection_id=connection_id,
            payload=payload,
            if_match=if_match,
            idempotency_key=idempotency_key,
        )
        response = success_response(data, status=201)
        grant = data.get("grant")
        if isinstance(grant, Mapping) and grant.get("etag"):
            etag = str(grant["etag"]).strip().removeprefix("W/")
            etag = etag.strip().strip('"')
            response.headers["ETag"] = f'"{etag}"'
        return response
