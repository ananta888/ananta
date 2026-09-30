"""Effective access and Context Policy lifecycle routes of the Source Control v1 API.
"""

from __future__ import annotations

from collections.abc import Callable

from flask import Blueprint, request

from agent.routes.source_control_v1_authorization import SourceControlV1RequestGuard
from agent.routes.source_control_v1_http import (
    MATRIX_FIELDS,
    PREVIEW_FIELDS,
    SourceControlApiError,
    boundary,
    bounded_int,
    json_object,
    require_exact_fields,
    require_execution_contract,
    require_idempotency_key,
    required_string,
    success_response,
)
from agent.routes.source_control_v1_port import SourceControlV1Port
from agent.services.source_control_access_policy import SourceControlAction


def register_policy_routes(
    blueprint: Blueprint,
    *,
    api: SourceControlV1Port,
    request_guard: SourceControlV1RequestGuard,
    check_auth: Callable,
) -> None:
    """Access preview/matrix and Context Policy lifecycle routes."""

    @blueprint.post("/access/preview")
    @check_auth
    @boundary
    def access_preview():
        payload = json_object()
        require_exact_fields(
            payload,
            PREVIEW_FIELDS,
            required=PREVIEW_FIELDS,
        )
        source_revision_id = required_string(payload, "source_revision_id")
        denied = request_guard.authorize(api,
            action=SourceControlAction.POLICY,
            resource_kind="source_revision",
            resource_id=source_revision_id,
        )
        if denied is not None:
            return denied
        return success_response(
            api.access_preview(principal=request_guard.principal(), payload=payload)
        )

    @blueprint.post("/access/matrix")
    @check_auth
    @boundary
    def access_matrix():
        payload = json_object()
        require_exact_fields(
            payload,
            MATRIX_FIELDS,
            required=frozenset(
                {"operation", "transformation", "purpose"}
            ),
        )
        denied = request_guard.authorize(api,
            action=SourceControlAction.POLICY,
            resource_kind="source_revision",
            collection=True,
        )
        if denied is not None:
            return denied
        return success_response(
            api.access_matrix(principal=request_guard.principal(), payload=payload)
        )

    @blueprint.get("/context-policies")
    @check_auth
    @boundary
    def context_policy_list():
        denied = request_guard.authorize(api,
            action=SourceControlAction.POLICY,
            resource_kind="context_policy",
            collection=True,
        )
        if denied is not None:
            return denied
        return success_response(
            api.context_policy_list(
                principal=request_guard.principal(),
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

    @blueprint.get("/context-policies/<policy_id>/versions")
    @check_auth
    @boundary
    def context_policy_versions(policy_id: str):
        denied = request_guard.authorize(api,
            action=SourceControlAction.POLICY,
            resource_kind="context_policy",
            resource_id=policy_id,
        )
        if denied is not None:
            return denied
        return success_response(
            api.context_policy_versions(
                principal=request_guard.principal(),
                policy_id=policy_id,
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

    @blueprint.get(
        "/context-policies/<policy_id>/versions/<int:version>"
    )
    @check_auth
    @boundary
    def context_policy_detail(policy_id: str, version: int):
        denied = request_guard.authorize(api,
            action=SourceControlAction.POLICY,
            resource_kind="context_policy",
            resource_id=policy_id,
        )
        if denied is not None:
            return denied
        data, etag = api.context_policy_detail(
            principal=request_guard.principal(),
            policy_id=policy_id,
            version=version,
        )
        response = success_response(data)
        response.headers["ETag"] = etag
        return response

    @blueprint.get("/context-policies/<policy_id>/active")
    @check_auth
    @boundary
    def context_policy_active(policy_id: str):
        denied = request_guard.authorize(api,
            action=SourceControlAction.POLICY,
            resource_kind="context_policy",
            resource_id=policy_id,
        )
        if denied is not None:
            return denied
        data, etag = api.context_policy_active(
            principal=request_guard.principal(),
            policy_id=policy_id,
        )
        response = success_response(data)
        response.headers["ETag"] = etag
        return response

    @blueprint.post("/context-policies/<policy_id>/drafts")
    @check_auth
    @boundary
    def context_policy_draft(policy_id: str):
        denied = request_guard.authorize(api,
            action=SourceControlAction.POLICY,
            resource_kind="context_policy",
            collection=True,
        )
        if denied is not None:
            return denied
        payload = json_object()
        require_exact_fields(
            payload,
            frozenset(
                {"document", "expected_latest_version", "dry_run"}
            ),
            required=frozenset(
                {"document", "expected_latest_version", "dry_run"}
            ),
        )
        if (
            not isinstance(payload.get("document"), dict)
            or payload.get("dry_run") is not False
            or (
                payload.get("expected_latest_version") is not None
                and not isinstance(
                    payload.get("expected_latest_version"), int
                )
            )
        ):
            raise SourceControlApiError("policy_draft_invalid")
        return success_response(
            api.context_policy_draft(
                principal=request_guard.principal(),
                policy_id=policy_id,
                payload=payload,
                idempotency_key=require_idempotency_key(),
            ),
            status=201,
        )

    @blueprint.post("/context-policies/lint")
    @check_auth
    @boundary
    def context_policy_lint():
        payload = json_object()
        require_exact_fields(
            payload,
            frozenset({"policy_id", "version"}),
            required=frozenset({"policy_id", "version"}),
        )
        policy_id = required_string(payload, "policy_id")
        version = bounded_int(
            payload.get("version"),
            field="version",
            default=0,
            minimum=1,
            maximum=2_147_483_647,
        )
        denied = request_guard.authorize(api,
            action=SourceControlAction.POLICY,
            resource_kind="context_policy",
            resource_id=policy_id,
        )
        if denied is not None:
            return denied
        return success_response(
            api.context_policy_lint(
                principal=request_guard.principal(),
                policy_id=policy_id,
                version=version,
            )
        )

    @blueprint.post("/context-policies/<policy_id>/preview")
    @check_auth
    @boundary
    def context_policy_preview(policy_id: str):
        payload = json_object()
        allowed = frozenset(
            {
                "version",
                "source_revision_id",
                "destination_id",
                "operation",
                "transformation",
            }
        )
        require_exact_fields(payload, allowed, required=allowed)
        denied = request_guard.authorize(api,
            action=SourceControlAction.POLICY,
            resource_kind="context_policy",
            resource_id=policy_id,
        )
        if denied is not None:
            return denied
        return success_response(
            api.context_policy_preview(
                principal=request_guard.principal(),
                policy_id=policy_id,
                payload=payload,
            )
        )

    def policy_transition_response(
        *,
        operation: str,
        policy_id: str,
        version: int,
    ):
        payload = json_object()
        require_exact_fields(
            payload,
            frozenset({"dry_run"}),
            required=frozenset({"dry_run"}),
        )
        if_match, idempotency_key = require_execution_contract(payload)
        denied = request_guard.authorize(api,
            action=SourceControlAction.POLICY,
            resource_kind="context_policy",
            resource_id=policy_id,
        )
        if denied is not None:
            return denied
        return success_response(
            api.context_policy_transition(
                principal=request_guard.principal(),
                operation=operation,
                policy_id=policy_id,
                version=version,
                if_match=if_match,
                idempotency_key=idempotency_key,
            )
        )

    @blueprint.post(
        "/context-policies/<policy_id>/versions/<int:version>/activate"
    )
    @check_auth
    @boundary
    def context_policy_activate(policy_id: str, version: int):
        return policy_transition_response(
            operation="activate",
            policy_id=policy_id,
            version=version,
        )

    @blueprint.post(
        "/context-policies/<policy_id>/versions/<int:version>/revoke"
    )
    @check_auth
    @boundary
    def context_policy_revoke(policy_id: str, version: int):
        return policy_transition_response(
            operation="revoke",
            policy_id=policy_id,
            version=version,
        )

    @blueprint.post("/context-policies/<policy_id>/rollback")
    @check_auth
    @boundary
    def context_policy_rollback(policy_id: str):
        payload = json_object()
        allowed = frozenset(
            {"target_version", "expected_latest_version", "dry_run"}
        )
        require_exact_fields(payload, allowed, required=allowed)
        if not isinstance(payload.get("target_version"), int) or not isinstance(
            payload.get("expected_latest_version"), int
        ):
            raise SourceControlApiError("policy_rollback_invalid")
        if_match, idempotency_key = require_execution_contract(payload)
        denied = request_guard.authorize(api,
            action=SourceControlAction.POLICY,
            resource_kind="context_policy",
            resource_id=policy_id,
        )
        if denied is not None:
            return denied
        return success_response(
            api.context_policy_rollback(
                principal=request_guard.principal(),
                policy_id=policy_id,
                payload=payload,
                if_match=if_match,
                idempotency_key=idempotency_key,
            ),
            status=201,
        )
