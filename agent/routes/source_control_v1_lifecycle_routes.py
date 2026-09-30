"""Source operation, index lifecycle, bulk and event routes of the Source Control v1 API.
"""

from __future__ import annotations

from collections.abc import Callable

from flask import Blueprint, request

from agent.routes.source_control_v1_authorization import SourceControlV1RequestGuard
from agent.routes.source_control_v1_http import (
    SourceControlApiError,
    boundary,
    bounded_int,
    json_object,
    require_exact_fields,
    require_execution_contract,
    require_idempotency_key,
    success_response,
)
from agent.routes.source_control_v1_port import SourceControlV1Port
from agent.services.source_control_access_policy import SourceControlAction


def register_lifecycle_routes(
    blueprint: Blueprint,
    *,
    api: SourceControlV1Port,
    request_guard: SourceControlV1RequestGuard,
    check_auth: Callable,
) -> None:
    """Refresh/scan/run operations and index lifecycle mutations."""

    def lifecycle_response(
        *,
        operation: str,
        resource_kind: str,
        resource_id: str,
        action: SourceControlAction,
        allowed_fields: frozenset[str] = frozenset({"dry_run"}),
    ):
        payload = json_object()
        require_exact_fields(
            payload,
            allowed_fields,
            required=frozenset({"dry_run"}),
        )
        if_match, idempotency_key = require_execution_contract(payload)
        denied = request_guard.authorize(api,
            action=action,
            resource_kind=resource_kind,
            resource_id=resource_id,
        )
        if denied is not None:
            return denied
        return success_response(
            api.mutate(
                principal=request_guard.principal(),
                operation=operation,
                resource_id=resource_id,
                if_match=if_match,
                idempotency_key=idempotency_key,
                payload=payload,
            )
        )

    def operation_response(
        *,
        operation: str,
        connection_id: str,
        action: SourceControlAction,
        allowed_fields: frozenset[str],
        required_fields: frozenset[str],
    ):
        payload = json_object()
        require_exact_fields(
            payload,
            allowed_fields,
            required=required_fields,
        )
        if_match, idempotency_key = require_execution_contract(payload)
        denied = request_guard.authorize(api,
            action=action,
            resource_kind="source_connection",
            resource_id=connection_id,
        )
        if denied is not None:
            return denied
        return success_response(
            api.dispatch_operation(
                principal=request_guard.principal(),
                operation=operation,
                connection_id=connection_id,
                if_match=if_match,
                idempotency_key=idempotency_key,
                payload=payload,
            ),
            status=202,
        )

    @blueprint.post("/connections/<connection_id>/refresh")
    @check_auth
    @boundary
    def refresh_connection(connection_id: str):
        return operation_response(
            operation="refresh",
            connection_id=connection_id,
            action=SourceControlAction.REFRESH,
            allowed_fields=frozenset({"dry_run"}),
            required_fields=frozenset({"dry_run"}),
        )

    @blueprint.post("/connections/<connection_id>/scan")
    @check_auth
    @boundary
    def scan_connection(connection_id: str):
        return operation_response(
            operation="scan",
            connection_id=connection_id,
            action=SourceControlAction.SCAN,
            allowed_fields=frozenset({"dry_run"}),
            required_fields=frozenset({"dry_run"}),
        )

    @blueprint.post("/connections/<connection_id>/runs")
    @check_auth
    @boundary
    def start_index_run(connection_id: str):
        return operation_response(
            operation="run",
            connection_id=connection_id,
            action=SourceControlAction.INDEX,
            allowed_fields=frozenset({"dry_run", "index_profile_id"}),
            required_fields=frozenset({"dry_run", "index_profile_id"}),
        )

    @blueprint.post("/indices/<index_id>/activate")
    @check_auth
    @boundary
    def activate_index(index_id: str):
        return lifecycle_response(
            operation="activate",
            resource_kind="knowledge_index",
            resource_id=index_id,
            action=SourceControlAction.INDEX,
        )

    @blueprint.post("/indices/<index_id>/rollback")
    @check_auth
    @boundary
    def rollback_index(index_id: str):
        return lifecycle_response(
            operation="rollback",
            resource_kind="knowledge_index",
            resource_id=index_id,
            action=SourceControlAction.INDEX,
        )

    @blueprint.post("/connections/<connection_id>/disable")
    @check_auth
    @boundary
    def disable_connection(connection_id: str):
        return lifecycle_response(
            operation="disable",
            resource_kind="source_connection",
            resource_id=connection_id,
            action=SourceControlAction.DELETE,
        )

    @blueprint.post("/indices/<index_id>/tombstone")
    @check_auth
    @boundary
    def tombstone_index(index_id: str):
        return lifecycle_response(
            operation="tombstone",
            resource_kind="knowledge_index",
            resource_id=index_id,
            action=SourceControlAction.DELETE,
        )

    @blueprint.delete("/indices/<index_id>")
    @check_auth
    @boundary
    def purge_index(index_id: str):
        return lifecycle_response(
            operation="purge",
            resource_kind="knowledge_index",
            resource_id=index_id,
            action=SourceControlAction.DELETE,
            allowed_fields=frozenset({"dry_run", "approval_id"}),
        )


def register_bulk_and_event_routes(
    blueprint: Blueprint,
    *,
    api: SourceControlV1Port,
    request_guard: SourceControlV1RequestGuard,
    check_auth: Callable,
) -> None:
    """Bulk plan/execute and job event polling."""

    @blueprint.post("/bulk/plan")
    @check_auth
    @boundary
    def bulk_plan():
        denied = request_guard.authorize(api,
            action=SourceControlAction.LIST,
            resource_kind="source_connection",
            collection=True,
        )
        if denied is not None:
            return denied
        payload = json_object()
        if payload.get("dry_run") is not True:
            raise SourceControlApiError("dry_run_required")
        return success_response(api.bulk_plan(principal=request_guard.principal(), payload=payload))

    @blueprint.post("/bulk/execute")
    @check_auth
    @boundary
    def bulk_execute():
        denied = request_guard.authorize(api,
            action=SourceControlAction.DELETE,
            resource_kind="source_connection",
            collection=True,
        )
        if denied is not None:
            return denied
        payload = json_object()
        if payload.get("dry_run") is not False:
            raise SourceControlApiError("dry_run_false_required")
        return success_response(
            api.bulk_execute(
                principal=request_guard.principal(),
                payload=payload,
                idempotency_key=require_idempotency_key(),
            )
        )

    @blueprint.get("/events")
    @check_auth
    @boundary
    def poll_events():
        denied = request_guard.authorize(api,
            action=SourceControlAction.LIST,
            resource_kind="source_control_event",
            collection=True,
        )
        if denied is not None:
            return denied
        return success_response(
            api.poll_events(
                principal=request_guard.principal(),
                after_sequence=bounded_int(
                    request.args.get("after_sequence"),
                    field="after_sequence",
                    default=0,
                    minimum=0,
                    maximum=9_223_372_036_854_775_807,
                ),
                limit=bounded_int(
                    request.args.get("limit"),
                    field="limit",
                    default=100,
                    minimum=1,
                    maximum=500,
                ),
            )
        )
