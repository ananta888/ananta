"""Endpoint authorization matrix and request guard of the Source Control v1 API.

Every v1 endpoint must have exactly one matrix entry; the request guard
resolves the principal, binds the selected project and delegates the
decision to the shared Source Control route authorization.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from flask import g, request

from agent.auth import (
    get_authenticated_source_control_principal,
)
from agent.routes.source_control_access import (
    SourceControlProjectScopeError,
    authorize_route_request,
    bind_source_control_project_selector,
    record_source_control_route_denial,
)
from agent.routes.source_control_v1_http import (
    SourceControlApiError,
    bounded_int,
    error_response,
)
from agent.routes.source_control_v1_port import SourceControlV1Port
from agent.services.source_control_access_policy import SourceControlAction


AUTHORITATIVE_PROJECT_ENDPOINTS = frozenset(
    {
        "validate_connection",
        "create_connection",
        "validate_content_admission",
        "create_content_admission",
        "list_connections",
        "get_connection",
        "run_history",
        "list_workspaces",
        "list_registered_remotes",
        "list_index_profiles",
        "list_grant_presets",
        "list_grants",
        "create_grant",
        "revoke_grant",
        "prepare_index_access_options",
        "prepare_index_access",
        "refresh_connection",
        "scan_connection",
        "start_index_run",
        "compare_indices",
        "activate_index",
        "rollback_index",
        "disable_connection",
        "tombstone_index",
        "purge_index",
        "graph",
        "query",
        "artifact_status",
        "artifact_download",
        "bulk_plan",
        "bulk_execute",
        "poll_events",
        "access_preview",
        "access_matrix",
        "codehug_mutation",
        "context_policy_list",
        "context_policy_versions",
        "context_policy_detail",
        "context_policy_active",
        "context_policy_draft",
        "context_policy_lint",
        "context_policy_preview",
        "context_policy_activate",
        "context_policy_revoke",
        "context_policy_rollback",
    }
)


@dataclass(frozen=True)
class SourceControlV1AuthorizationRule:
    endpoint: str
    rule: str
    methods: tuple[str, ...]
    action: SourceControlAction
    resource_kind: str
    collection: bool


SOURCE_CONTROL_V1_AUTHORIZATION_MATRIX = (
    SourceControlV1AuthorizationRule("validate_connection", "/connections/validate", ("POST",), SourceControlAction.index, "source_connection", True),
    SourceControlV1AuthorizationRule("create_connection", "/connections", ("POST",), SourceControlAction.index, "source_connection", True),
    SourceControlV1AuthorizationRule("validate_content_admission", "/content-admissions/validate", ("POST",), SourceControlAction.index, "source_connection", True),
    SourceControlV1AuthorizationRule("create_content_admission", "/content-admissions", ("POST",), SourceControlAction.index, "source_connection", True),
    SourceControlV1AuthorizationRule("list_workspaces", "/workspaces", ("GET",), SourceControlAction.list, "source_connection", True),
    SourceControlV1AuthorizationRule("list_registered_remotes", "/registered-remotes", ("GET",), SourceControlAction.list, "source_connection", True),
    SourceControlV1AuthorizationRule("list_index_profiles", "/index-profiles", ("GET",), SourceControlAction.list, "source_connection", True),
    SourceControlV1AuthorizationRule("list_grant_presets", "/grant-presets", ("GET",), SourceControlAction.list, "source_access_grant", True),
    SourceControlV1AuthorizationRule("list_grants", "/grants", ("GET",), SourceControlAction.list, "source_access_grant", True),
    SourceControlV1AuthorizationRule("create_grant", "/grants", ("POST",), SourceControlAction.index, "source_access_grant", True),
    SourceControlV1AuthorizationRule("revoke_grant", "/grants/<grant_id>/actions/revoke", ("POST",), SourceControlAction.delete, "source_access_grant", False),
    SourceControlV1AuthorizationRule("prepare_index_access_options", "/connections/<connection_id>/actions/prepare-index-access", ("GET",), SourceControlAction.index, "source_connection", False),
    SourceControlV1AuthorizationRule("prepare_index_access", "/connections/<connection_id>/actions/prepare-index-access", ("POST",), SourceControlAction.index, "source_connection", False),
    SourceControlV1AuthorizationRule("list_connections", "/connections", ("GET",), SourceControlAction.list, "source_connection", True),
    SourceControlV1AuthorizationRule("get_connection", "/connections/<connection_id>", ("GET",), SourceControlAction.detail, "source_connection", False),
    SourceControlV1AuthorizationRule("run_history", "/connections/<connection_id>/runs", ("GET",), SourceControlAction.detail, "source_connection", False),
    SourceControlV1AuthorizationRule("compare_indices", "/indices/compare", ("POST",), SourceControlAction.detail, "knowledge_index", True),
    SourceControlV1AuthorizationRule("refresh_connection", "/connections/<connection_id>/refresh", ("POST",), SourceControlAction.refresh, "source_connection", False),
    SourceControlV1AuthorizationRule("scan_connection", "/connections/<connection_id>/scan", ("POST",), SourceControlAction.scan, "source_connection", False),
    SourceControlV1AuthorizationRule("start_index_run", "/connections/<connection_id>/runs", ("POST",), SourceControlAction.index, "source_connection", False),
    SourceControlV1AuthorizationRule("activate_index", "/indices/<index_id>/activate", ("POST",), SourceControlAction.index, "knowledge_index", False),
    SourceControlV1AuthorizationRule("rollback_index", "/indices/<index_id>/rollback", ("POST",), SourceControlAction.index, "knowledge_index", False),
    SourceControlV1AuthorizationRule("disable_connection", "/connections/<connection_id>/disable", ("POST",), SourceControlAction.delete, "source_connection", False),
    SourceControlV1AuthorizationRule("tombstone_index", "/indices/<index_id>/tombstone", ("POST",), SourceControlAction.delete, "knowledge_index", False),
    SourceControlV1AuthorizationRule("purge_index", "/indices/<index_id>", ("DELETE",), SourceControlAction.delete, "knowledge_index", False),
    SourceControlV1AuthorizationRule("graph", "/connections/<connection_id>/graph", ("GET",), SourceControlAction.graph, "source_connection", False),
    SourceControlV1AuthorizationRule("query", "/connections/<connection_id>/query", ("POST",), SourceControlAction.query, "source_connection", False),
    SourceControlV1AuthorizationRule("artifact_status", "/connections/<connection_id>/artifacts/<artifact_id>/status", ("GET",), SourceControlAction.artifact, "source_connection", False),
    SourceControlV1AuthorizationRule("artifact_download", "/connections/<connection_id>/artifacts/<artifact_id>/download", ("GET",), SourceControlAction.download, "source_connection", False),
    SourceControlV1AuthorizationRule("bulk_plan", "/bulk/plan", ("POST",), SourceControlAction.scan, "source_connection", True),
    SourceControlV1AuthorizationRule("bulk_execute", "/bulk/execute", ("POST",), SourceControlAction.index, "source_connection", True),
    SourceControlV1AuthorizationRule("poll_events", "/events", ("GET",), SourceControlAction.list, "source_connection", True),
    SourceControlV1AuthorizationRule("access_preview", "/access/preview", ("POST",), SourceControlAction.detail, "source_connection", True),
    SourceControlV1AuthorizationRule("access_matrix", "/access/matrix", ("POST",), SourceControlAction.list, "source_connection", True),
    SourceControlV1AuthorizationRule("codehug_mutation", "/codehug/mutations", ("POST",), SourceControlAction.index, "source_connection", True),
    SourceControlV1AuthorizationRule("context_policy_list", "/context-policies", ("GET",), SourceControlAction.policy, "context_policy", True),
    SourceControlV1AuthorizationRule("context_policy_versions", "/context-policies/<policy_id>/versions", ("GET",), SourceControlAction.policy, "context_policy", False),
    SourceControlV1AuthorizationRule("context_policy_detail", "/context-policies/<policy_id>/versions/<int:version>", ("GET",), SourceControlAction.policy, "context_policy", False),
    SourceControlV1AuthorizationRule("context_policy_active", "/context-policies/<policy_id>/active", ("GET",), SourceControlAction.policy, "context_policy", False),
    SourceControlV1AuthorizationRule("context_policy_draft", "/context-policies/<policy_id>/drafts", ("POST",), SourceControlAction.policy, "context_policy", False),
    SourceControlV1AuthorizationRule("context_policy_lint", "/context-policies/lint", ("POST",), SourceControlAction.policy, "context_policy", True),
    SourceControlV1AuthorizationRule("context_policy_preview", "/context-policies/<policy_id>/preview", ("POST",), SourceControlAction.policy, "context_policy", False),
    SourceControlV1AuthorizationRule("context_policy_activate", "/context-policies/<policy_id>/versions/<int:version>/activate", ("POST",), SourceControlAction.policy, "context_policy", False),
    SourceControlV1AuthorizationRule("context_policy_revoke", "/context-policies/<policy_id>/versions/<int:version>/revoke", ("POST",), SourceControlAction.policy, "context_policy", False),
    SourceControlV1AuthorizationRule("context_policy_rollback", "/context-policies/<policy_id>/rollback", ("POST",), SourceControlAction.policy, "context_policy", False),
)
SOURCE_CONTROL_V1_AUTHORIZATION_BY_ENDPOINT = {
    item.endpoint: item for item in SOURCE_CONTROL_V1_AUTHORIZATION_MATRIX
}


def authenticated_source_control_principal() -> object:
    """Return the request-bound principal, else the authenticated one."""

    return getattr(
        g,
        "source_control_principal",
        None,
    ) or get_authenticated_source_control_principal()


class SourceControlV1RequestGuard:
    """Per-request principal resolution and route authorization.

    The authorization matrix decides the action for the current endpoint;
    project-scoped endpoints bind the selected project to the principal
    before the shared route authorization runs. Collaborators are
    keyword-only seams with the production Hub defaults.
    """

    def __init__(
        self,
        *,
        route_authorizer: Callable[..., object | None] = authorize_route_request,
        principal_resolver: Callable[[], object] = (
            authenticated_source_control_principal
        ),
        denial_recorder: Callable[..., None] = record_source_control_route_denial,
        project_selector_binder: Callable[..., object] = (
            bind_source_control_project_selector
        ),
    ) -> None:
        self._route_authorizer = route_authorizer
        self._principal_resolver = principal_resolver
        self._record_denial = denial_recorder
        self._bind_project_selector = project_selector_binder

    def principal(self) -> object:
        return self._principal_resolver()

    def catalog_request(
        self, *, allowed: frozenset[str]
    ) -> tuple[object, str, str | None, int, dict[str, str]]:
        if set(request.args) - allowed:
            raise SourceControlApiError("query_fields_forbidden")
        project_id = str(request.args.get("project_id") or "").strip()
        if not project_id:
            raise SourceControlApiError("project_id_required")
        principal = self.principal()
        try:
            scoped_principal = self._bind_project_selector(
                project_id,
                principal=principal,
            )
        except SourceControlProjectScopeError as exc:
            raise SourceControlApiError(
                exc.reason_code,
                status_code=exc.status_code,
            ) from None
        principal = scoped_principal
        filters = {
            key: str(value)
            for key, value in request.args.items()
            if key not in {"project_id", "cursor", "limit"}
        }
        return (
            principal,
            project_id,
            request.args.get("cursor"),
            bounded_int(
                request.args.get("limit"),
                field="limit",
                default=50,
                minimum=1,
                maximum=200,
            ),
            filters,
        )

    def authorize(
        self,
        api: SourceControlV1Port,
        *,
        action: SourceControlAction,
        resource_kind: str,
        resource_id: str | None = None,
        collection: bool = False,
        principal_override: object | None = None,
    ) -> object | None:
        endpoint = str(request.endpoint or "").rsplit(".", 1)[-1]
        expected = SOURCE_CONTROL_V1_AUTHORIZATION_BY_ENDPOINT.get(endpoint)
        if expected is None:
            raise SourceControlApiError(
                "source_control_authorization_matrix_missing",
                status_code=500,
            )
        action = expected.action
        require_project_scope = endpoint in AUTHORITATIVE_PROJECT_ENDPOINTS
        scoped_principal = principal_override
        if require_project_scope and scoped_principal is None:
            authenticated = self.principal()
            selected_project = str(
                request.args.get("project_id")
                or getattr(authenticated, "project_id", None)
                or ""
            )
            scoped_principal = self._bind_project_selector(
                selected_project,
                principal=authenticated,
            )
        if require_project_scope and scoped_principal is not None:
            # Authorization and the subsequent domain operation must observe the
            # same project-bound principal. Keeping the binding request-local also
            # prevents one project's selector from leaking into another request.
            g.source_control_principal = scoped_principal
        resource = None
        if resource_id is not None:
            resource = api.binding(
                resource_kind=resource_kind,
                resource_id=resource_id,
            )
            if resource is None:
                self._record_denial(
                    principal=self.principal(),
                    action=action,
                    resource_kind=resource_kind,
                    object_id=resource_id,
                    status_code=404,
                    reason_code="source_control_not_found",
                )
                return error_response("source_control_not_found", 404)
        denied = self._route_authorizer(
            action=action,
            resource_kind=resource_kind,
            resource=resource,
            object_id=resource_id or "",
            collection=collection,
            principal_override=scoped_principal,
            require_project_scope=require_project_scope,
        )
        return denied


__all__ = [
    "AUTHORITATIVE_PROJECT_ENDPOINTS",
    "SOURCE_CONTROL_V1_AUTHORIZATION_BY_ENDPOINT",
    "SOURCE_CONTROL_V1_AUTHORIZATION_MATRIX",
    "SourceControlV1AuthorizationRule",
    "SourceControlV1RequestGuard",
    "authenticated_source_control_principal",
]
