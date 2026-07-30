"""Canonical versioned Hub API for source-control governance."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import wraps
from typing import Any, Protocol

from flask import Blueprint, Response, jsonify, make_response, redirect, request

from agent.auth import (
    check_auth as _base_check_auth,
    get_authenticated_source_control_principal,
)
from agent.routes.source_control_access import (
    authorize_route_request,
    record_source_control_route_denial,
)
from agent.services.source_control_access_policy import SourceControlAction
from agent.services.source_control_legacy_usage import (
    BoundedLegacySourceControlUsage,
)
from agent.services.source_control_artifact_download import (
    SourceControlArtifactStream,
)


_SUCCESS_SCHEMA = "ananta.source-control.api-response.v1"
_ERROR_SCHEMA = "ananta.source-control.error.v1"
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
_CONNECTION_FILTERS = frozenset(
    {"cursor", "limit", "state", "connector_type", "owner_id", "sensitivity"}
)
_PREVIEW_FIELDS = frozenset(
    {
        "source_revision_id",
        "destination_id",
        "operation",
        "transformation",
        "purpose",
    }
)
_MATRIX_FIELDS = frozenset(
    {
        "operation",
        "transformation",
        "purpose",
        "source_cursor",
        "destination_cursor",
        "source_limit",
        "destination_limit",
        "source_filters",
        "destination_filters",
    }
)


class SourceControlApiError(ValueError):
    """Stable route-boundary error without implementation details."""

    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class SourceControlV1Port(Protocol):
    def binding(
        self, *, resource_kind: str, resource_id: str
    ) -> Mapping[str, object] | None: ...

    def validate_connection(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]: ...

    def create_connection(
        self,
        *,
        principal: object,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]: ...

    def validate_content_admission(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]: ...

    def create_content_admission(
        self,
        *,
        principal: object,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]: ...

    def list_source_control_catalog(
        self,
        *,
        principal: object,
        catalog: str,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> Mapping[str, object]: ...

    def list_grant_presets(
        self,
        *,
        principal: object,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> Mapping[str, object]: ...

    def list_grants(
        self,
        *,
        principal: object,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> Mapping[str, object]: ...

    def create_grant(
        self,
        *,
        principal: object,
        project_id: str,
        payload: Mapping[str, object],
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]: ...

    def revoke_grant(
        self,
        *,
        principal: object,
        project_id: str,
        grant_id: str,
        payload: Mapping[str, object],
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]: ...

    def list_connections(
        self,
        *,
        principal: object,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> Mapping[str, object]: ...

    def get_connection(
        self, *, principal: object, connection_id: str
    ) -> tuple[Mapping[str, object], str]: ...

    def run_history(
        self,
        *,
        principal: object,
        connection_id: str,
        cursor: str | None,
        limit: int,
    ) -> Mapping[str, object]: ...

    def compare_indices(
        self,
        *,
        principal: object,
        left_index_id: str,
        right_index_id: str,
    ) -> Mapping[str, object]: ...

    def mutate(
        self,
        *,
        principal: object,
        operation: str,
        resource_id: str,
        if_match: str,
        idempotency_key: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]: ...

    def dispatch_operation(
        self,
        *,
        principal: object,
        operation: str,
        connection_id: str,
        if_match: str,
        idempotency_key: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]: ...

    def graph(
        self,
        *,
        principal: object,
        connection_id: str,
        parameters: Mapping[str, object],
    ) -> Mapping[str, object]: ...

    def query(
        self,
        *,
        principal: object,
        connection_id: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]: ...

    def artifact_status(
        self,
        *,
        principal: object,
        connection_id: str,
        artifact_id: str,
    ) -> Mapping[str, object]: ...

    def artifact_download(
        self,
        *,
        principal: object,
        connection_id: str,
        artifact_id: str,
        range_header: str | None,
    ) -> SourceControlArtifactStream: ...

    def codehug_mutation(
        self,
        *,
        principal: object,
        mutation_intent_id: str,
        idempotency_key: str,
    ) -> Mapping[str, object]: ...

    def bulk_plan(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]: ...

    def bulk_execute(
        self,
        *,
        principal: object,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]: ...

    def poll_events(
        self,
        *,
        principal: object,
        after_sequence: int,
        limit: int,
    ) -> Mapping[str, object]: ...

    def access_preview(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]: ...

    def access_matrix(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]: ...

    def context_policy_list(
        self, *, principal: object, cursor: str | None, limit: int
    ) -> Mapping[str, object]: ...

    def context_policy_versions(
        self,
        *,
        principal: object,
        policy_id: str,
        cursor: str | None,
        limit: int,
    ) -> Mapping[str, object]: ...

    def context_policy_detail(
        self, *, principal: object, policy_id: str, version: int
    ) -> tuple[Mapping[str, object], str]: ...

    def context_policy_active(
        self, *, principal: object, policy_id: str
    ) -> tuple[Mapping[str, object], str]: ...

    def context_policy_draft(
        self,
        *,
        principal: object,
        policy_id: str,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]: ...

    def context_policy_lint(
        self, *, principal: object, policy_id: str, version: int
    ) -> Mapping[str, object]: ...

    def context_policy_preview(
        self,
        *,
        principal: object,
        policy_id: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]: ...

    def context_policy_transition(
        self,
        *,
        principal: object,
        operation: str,
        policy_id: str,
        version: int,
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]: ...

    def context_policy_rollback(
        self,
        *,
        principal: object,
        policy_id: str,
        payload: Mapping[str, object],
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]: ...


def _success(data: Mapping[str, object], *, status: int = 200) -> Response:
    response = jsonify({"schema": _SUCCESS_SCHEMA, "data": dict(data)})
    response.status_code = status
    return response


def _error(reason_code: str, status_code: int) -> tuple[Response, int]:
    return (
        jsonify(
            {
                "schema": _ERROR_SCHEMA,
                "error": {"code": reason_code},
            }
        ),
        status_code,
    )


def _json_object() -> dict[str, object]:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise SourceControlApiError("request_body_invalid")
    return value


def _bounded_int(
    value: object,
    *,
    field: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SourceControlApiError(f"{field}_invalid") from exc
    if parsed < minimum or parsed > maximum:
        raise SourceControlApiError(f"{field}_invalid")
    return parsed


def _required_string(
    payload: Mapping[str, object], field: str, *, maximum: int = 256
) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise SourceControlApiError(f"{field}_invalid")
    return value.strip()


def _require_exact_fields(
    payload: Mapping[str, object],
    allowed: frozenset[str],
    *,
    required: frozenset[str],
) -> None:
    if not required.issubset(payload):
        raise SourceControlApiError("request_fields_missing")
    if set(payload) - allowed:
        raise SourceControlApiError("request_fields_forbidden")


def _require_execution_contract(payload: Mapping[str, object]) -> tuple[str, str]:
    if payload.get("dry_run") is not False:
        raise SourceControlApiError("dry_run_false_required")
    if_match = request.headers.get("If-Match", "").strip()
    if not if_match:
        raise SourceControlApiError(
            "if_match_required",
            status_code=428,
        )
    idempotency_key = request.headers.get("Idempotency-Key", "").strip()
    if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        raise SourceControlApiError(
            "idempotency_key_required",
            status_code=428,
        )
    return if_match, idempotency_key


def _require_idempotency_key() -> str:
    value = request.headers.get("Idempotency-Key", "").strip()
    if not _IDEMPOTENCY_KEY.fullmatch(value):
        raise SourceControlApiError(
            "idempotency_key_required",
            status_code=428,
        )
    return value


def _require_grant_mutation_headers() -> tuple[str, str]:
    if_match = request.headers.get("If-Match", "").strip()
    if not if_match:
        raise SourceControlApiError(
            "if_match_required",
            status_code=428,
        )
    return if_match, _require_idempotency_key()


def _principal() -> object:
    return get_authenticated_source_control_principal()


def _catalog_request(
    *, allowed: frozenset[str]
) -> tuple[object, str, str | None, int, dict[str, str]]:
    if set(request.args) - allowed:
        raise SourceControlApiError("query_fields_forbidden")
    project_id = str(request.args.get("project_id") or "").strip()
    principal = _principal()
    if not project_id:
        raise SourceControlApiError("project_id_required")
    if project_id != str(getattr(principal, "project_id", "")):
        raise SourceControlApiError(
            "source_control_not_found", status_code=404
        )
    filters = {
        key: str(value)
        for key, value in request.args.items()
        if key not in {"project_id", "cursor", "limit"}
    }
    return (
        principal,
        project_id,
        request.args.get("cursor"),
        _bounded_int(
            request.args.get("limit"),
            field="limit",
            default=50,
            minimum=1,
            maximum=200,
        ),
        filters,
    )


def _authorize(
    api: SourceControlV1Port,
    *,
    action: SourceControlAction,
    resource_kind: str,
    resource_id: str | None = None,
    collection: bool = False,
) -> object | None:
    endpoint = str(request.endpoint or "").rsplit(".", 1)[-1]
    expected = SOURCE_CONTROL_V1_AUTHORIZATION_BY_ENDPOINT.get(endpoint)
    if expected is None:
        raise SourceControlApiError(
            "source_control_authorization_matrix_missing",
            status_code=500,
        )
    action = expected.action
    resource = None
    if resource_id is not None:
        resource = api.binding(
            resource_kind=resource_kind,
            resource_id=resource_id,
        )
        if resource is None:
            record_source_control_route_denial(
                principal=_principal(),
                action=action,
                resource_kind=resource_kind,
                object_id=resource_id,
                status_code=404,
                reason_code="source_control_not_found",
            )
            return _error("source_control_not_found", 404)
    denied = authorize_route_request(
        action=action,
        resource_kind=resource_kind,
        resource=resource,
        object_id=resource_id or "",
        collection=collection,
    )
    return denied


def check_auth(view):
    """Audit v1 authentication failures before domain runtime invocation."""

    protected = _base_check_auth(view)

    @wraps(view)
    def audited(*args, **kwargs):
        result = protected(*args, **kwargs)
        if make_response(result).status_code == 401:
            record_source_control_route_denial(
                principal=None,
                action="authenticate",
                resource_kind="source_control_route",
                object_id="",
                status_code=401,
                reason_code="authentication_required",
            )
        return result

    return audited


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


def _connection_intent_payload() -> dict[str, object]:
    payload = _json_object()
    connector_type = _required_string(payload, "connector_type")
    common = frozenset(
        {"connector_type", "display_name", "sensitivity", "dry_run"}
    )
    if connector_type in {"registered_workspace", "local_directory"}:
        allowed = common | {"workspace_id"}
        _required_string(payload, "workspace_id")
    elif connector_type in {"git", "github"}:
        allowed = common | {"remote_id"}
        _required_string(payload, "remote_id")
    else:
        raise SourceControlApiError("connector_type_not_registered")
    _require_exact_fields(
        payload,
        frozenset(allowed),
        required=frozenset(allowed),
    )
    _required_string(payload, "display_name")
    _required_string(payload, "sensitivity")
    return payload


def _boundary(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except SourceControlApiError as exc:
            return _error(exc.reason_code, exc.status_code)
        except Exception as exc:
            reason_code = getattr(exc, "reason_code", None)
            if isinstance(reason_code, str) and reason_code:
                status_code = int(
                    getattr(
                        exc,
                        "status_code",
                        _status_for_reason(reason_code),
                    )
                )
                return _error(reason_code, status_code)
            return _error("source_control_internal_error", 500)

    return wrapped


def _status_for_reason(reason_code: str) -> int:
    if "not_found" in reason_code or reason_code.endswith("_missing"):
        return 404
    if "role_required" in reason_code or "policy_denied" in reason_code:
        return 403
    if "version_conflict" in reason_code or "etag" in reason_code:
        return 412
    if (
        "idempotency" in reason_code
        or "already_" in reason_code
        or reason_code.startswith("purge_blocked")
    ):
        return 409
    if "unavailable" in reason_code:
        return 503
    return 400


def create_source_control_v1_blueprint(
    api: SourceControlV1Port,
) -> Blueprint:
    """Create the canonical API with an injected Hub composition root."""

    blueprint = Blueprint(
        "source_control_v1",
        __name__,
        url_prefix="/api/source-control/v1",
    )

    @blueprint.before_request
    def require_authorization_matrix_entry():
        endpoint = str(request.endpoint or "").rsplit(".", 1)[-1]
        if endpoint not in SOURCE_CONTROL_V1_AUTHORIZATION_BY_ENDPOINT:
            return _error(
                "source_control_authorization_matrix_missing",
                500,
            )
        return None

    @blueprint.post("/connections/validate")
    @check_auth
    @_boundary
    def validate_connection():
        denied = _authorize(
            api,
            action=SourceControlAction.POLICY,
            resource_kind="source_connection",
            collection=True,
        )
        if denied is not None:
            return denied
        payload = _connection_intent_payload()
        if payload.get("dry_run") is not True:
            raise SourceControlApiError("dry_run_required")
        return _success(
            api.validate_connection(
                principal=_principal(),
                payload=payload,
            )
        )

    @blueprint.post("/connections")
    @check_auth
    @_boundary
    def create_connection():
        denied = _authorize(
            api,
            action=SourceControlAction.POLICY,
            resource_kind="source_connection",
            collection=True,
        )
        if denied is not None:
            return denied
        payload = _connection_intent_payload()
        if payload.get("dry_run") is not False:
            raise SourceControlApiError("dry_run_false_required")
        return _success(
            api.create_connection(
                principal=_principal(),
                payload=payload,
                idempotency_key=_require_idempotency_key(),
            ),
            status=201,
        )

    @blueprint.post("/content-admissions/validate")
    @check_auth
    @_boundary
    def validate_content_admission():
        denied = _authorize(
            api,
            action=SourceControlAction.POLICY,
            resource_kind="source_connection",
            collection=True,
        )
        if denied is not None:
            return denied
        return _success(
            api.validate_content_admission(
                principal=_principal(),
                payload=_json_object(),
            )
        )

    @blueprint.post("/content-admissions")
    @check_auth
    @_boundary
    def create_content_admission():
        denied = _authorize(
            api,
            action=SourceControlAction.INDEX,
            resource_kind="source_connection",
            collection=True,
        )
        if denied is not None:
            return denied
        return _success(
            api.create_content_admission(
                principal=_principal(),
                payload=_json_object(),
                idempotency_key=_require_idempotency_key(),
            ),
            status=201,
        )

    def catalog_response(
        *,
        catalog: str,
        allowed: frozenset[str],
    ):
        denied = _authorize(
            api,
            action=SourceControlAction.LIST,
            resource_kind="source_connection",
            collection=True,
        )
        if denied is not None:
            return denied
        principal, project_id, cursor, limit, filters = _catalog_request(
            allowed=allowed
        )
        return _success(
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
    @_boundary
    def list_workspaces():
        return catalog_response(
            catalog="workspaces",
            allowed=frozenset(
                {"project_id", "cursor", "limit", "q", "enabled"}
            ),
        )

    @blueprint.get("/registered-remotes")
    @check_auth
    @_boundary
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
    @_boundary
    def list_index_profiles():
        return catalog_response(
            catalog="index_profiles",
            allowed=frozenset(
                {"project_id", "cursor", "limit", "q", "source"}
            ),
        )

    @blueprint.get("/grant-presets")
    @check_auth
    @_boundary
    def list_grant_presets():
        denied = _authorize(
            api,
            action=SourceControlAction.LIST,
            resource_kind="source_access_grant",
            collection=True,
        )
        if denied is not None:
            return denied
        principal, project_id, cursor, limit, filters = _catalog_request(
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
        return _success(
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
    @_boundary
    def list_grants():
        denied = _authorize(
            api,
            action=SourceControlAction.LIST,
            resource_kind="source_access_grant",
            collection=True,
        )
        if denied is not None:
            return denied
        principal, project_id, cursor, limit, filters = _catalog_request(
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
        return _success(
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
    @_boundary
    def create_grant():
        denied = _authorize(
            api,
            action=SourceControlAction.POLICY,
            resource_kind="source_access_grant",
            collection=True,
        )
        if denied is not None:
            return denied
        principal, project_id, _, _, _ = _catalog_request(
            allowed=frozenset({"project_id"})
        )
        payload = _json_object()
        fields = frozenset(
            {
                "source_revision_id",
                "destination_id",
                "policy_id",
                "preset_id",
                "duration_seconds",
            }
        )
        _require_exact_fields(payload, fields, required=fields)
        if_match, idempotency_key = _require_grant_mutation_headers()
        data = api.create_grant(
            principal=principal,
            project_id=project_id,
            payload=payload,
            if_match=if_match,
            idempotency_key=idempotency_key,
        )
        response = _success(data, status=201)
        grant = data.get("grant")
        if isinstance(grant, Mapping) and grant.get("etag"):
            response.headers["ETag"] = f'"{grant["etag"]}"'
        return response

    @blueprint.post("/grants/<grant_id>/actions/revoke")
    @check_auth
    @_boundary
    def revoke_grant(grant_id: str):
        denied = _authorize(
            api,
            action=SourceControlAction.POLICY,
            resource_kind="source_access_grant",
            collection=True,
        )
        if denied is not None:
            return denied
        principal, project_id, _, _, _ = _catalog_request(
            allowed=frozenset({"project_id"})
        )
        payload = _json_object()
        fields = frozenset({"reason_code"})
        _require_exact_fields(payload, fields, required=fields)
        if_match, idempotency_key = _require_grant_mutation_headers()
        data = api.revoke_grant(
            principal=principal,
            project_id=project_id,
            grant_id=grant_id,
            payload=payload,
            if_match=if_match,
            idempotency_key=idempotency_key,
        )
        response = _success(data)
        grant = data.get("grant")
        if isinstance(grant, Mapping) and grant.get("etag"):
            response.headers["ETag"] = f'"{grant["etag"]}"'
        return response

    @blueprint.get("/connections")
    @check_auth
    @_boundary
    def list_connections():
        denied = _authorize(
            api,
            action=SourceControlAction.LIST,
            resource_kind="source_connection",
            collection=True,
        )
        if denied is not None:
            return denied
        unknown = set(request.args) - _CONNECTION_FILTERS
        if unknown:
            raise SourceControlApiError("connection_filter_invalid")
        limit = _bounded_int(
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
        return _success(
            api.list_connections(
                principal=_principal(),
                cursor=request.args.get("cursor"),
                limit=limit,
                filters=filters,
            )
        )

    @blueprint.get("/connections/<connection_id>")
    @check_auth
    @_boundary
    def get_connection(connection_id: str):
        denied = _authorize(
            api,
            action=SourceControlAction.DETAIL,
            resource_kind="source_connection",
            resource_id=connection_id,
        )
        if denied is not None:
            return denied
        data, etag = api.get_connection(
            principal=_principal(),
            connection_id=connection_id,
        )
        response = _success(data)
        response.headers["ETag"] = etag
        return response

    @blueprint.get("/connections/<connection_id>/runs")
    @check_auth
    @_boundary
    def run_history(connection_id: str):
        denied = _authorize(
            api,
            action=SourceControlAction.DETAIL,
            resource_kind="source_connection",
            resource_id=connection_id,
        )
        if denied is not None:
            return denied
        return _success(
            api.run_history(
                principal=_principal(),
                connection_id=connection_id,
                cursor=request.args.get("cursor"),
                limit=_bounded_int(
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
    @_boundary
    def compare_indices():
        payload = _json_object()
        _require_exact_fields(
            payload,
            frozenset({"left_index_id", "right_index_id"}),
            required=frozenset({"left_index_id", "right_index_id"}),
        )
        left = _required_string(payload, "left_index_id")
        right = _required_string(payload, "right_index_id")
        denied = _authorize(
            api,
            action=SourceControlAction.DETAIL,
            resource_kind="knowledge_index",
            resource_id=left,
        )
        if denied is not None:
            return denied
        denied = _authorize(
            api,
            action=SourceControlAction.DETAIL,
            resource_kind="knowledge_index",
            resource_id=right,
        )
        if denied is not None:
            return denied
        return _success(
            api.compare_indices(
                principal=_principal(),
                left_index_id=left,
                right_index_id=right,
            )
        )

    def lifecycle_response(
        *,
        operation: str,
        resource_kind: str,
        resource_id: str,
        action: SourceControlAction,
        allowed_fields: frozenset[str] = frozenset({"dry_run"}),
    ):
        payload = _json_object()
        _require_exact_fields(
            payload,
            allowed_fields,
            required=frozenset({"dry_run"}),
        )
        if_match, idempotency_key = _require_execution_contract(payload)
        denied = _authorize(
            api,
            action=action,
            resource_kind=resource_kind,
            resource_id=resource_id,
        )
        if denied is not None:
            return denied
        return _success(
            api.mutate(
                principal=_principal(),
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
        payload = _json_object()
        _require_exact_fields(
            payload,
            allowed_fields,
            required=required_fields,
        )
        if_match, idempotency_key = _require_execution_contract(payload)
        denied = _authorize(
            api,
            action=action,
            resource_kind="source_connection",
            resource_id=connection_id,
        )
        if denied is not None:
            return denied
        return _success(
            api.dispatch_operation(
                principal=_principal(),
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
    @_boundary
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
    @_boundary
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
    @_boundary
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
    @_boundary
    def activate_index(index_id: str):
        return lifecycle_response(
            operation="activate",
            resource_kind="knowledge_index",
            resource_id=index_id,
            action=SourceControlAction.INDEX,
        )

    @blueprint.post("/indices/<index_id>/rollback")
    @check_auth
    @_boundary
    def rollback_index(index_id: str):
        return lifecycle_response(
            operation="rollback",
            resource_kind="knowledge_index",
            resource_id=index_id,
            action=SourceControlAction.INDEX,
        )

    @blueprint.post("/connections/<connection_id>/disable")
    @check_auth
    @_boundary
    def disable_connection(connection_id: str):
        return lifecycle_response(
            operation="disable",
            resource_kind="source_connection",
            resource_id=connection_id,
            action=SourceControlAction.DELETE,
        )

    @blueprint.post("/indices/<index_id>/tombstone")
    @check_auth
    @_boundary
    def tombstone_index(index_id: str):
        return lifecycle_response(
            operation="tombstone",
            resource_kind="knowledge_index",
            resource_id=index_id,
            action=SourceControlAction.DELETE,
        )

    @blueprint.delete("/indices/<index_id>")
    @check_auth
    @_boundary
    def purge_index(index_id: str):
        return lifecycle_response(
            operation="purge",
            resource_kind="knowledge_index",
            resource_id=index_id,
            action=SourceControlAction.DELETE,
            allowed_fields=frozenset({"dry_run", "approval_id"}),
        )

    @blueprint.get("/connections/<connection_id>/graph")
    @check_auth
    @_boundary
    def graph(connection_id: str):
        denied = _authorize(
            api,
            action=SourceControlAction.GRAPH,
            resource_kind="source_connection",
            resource_id=connection_id,
        )
        if denied is not None:
            return denied
        if set(request.args) - {"cursor", "limit", "view"}:
            raise SourceControlApiError("graph_parameters_invalid")
        parameters = {
            "cursor": request.args.get("cursor"),
            "limit": _bounded_int(
                request.args.get("limit"),
                field="limit",
                default=100,
                minimum=1,
                maximum=500,
            ),
            "view": request.args.get("view", "default"),
        }
        return _success(
            api.graph(
                principal=_principal(),
                connection_id=connection_id,
                parameters=parameters,
            )
        )

    @blueprint.post("/connections/<connection_id>/query")
    @check_auth
    @_boundary
    def query(connection_id: str):
        payload = _json_object()
        _require_exact_fields(
            payload,
            frozenset({"query", "limit"}),
            required=frozenset({"query"}),
        )
        _required_string(payload, "query", maximum=4000)
        denied = _authorize(
            api,
            action=SourceControlAction.QUERY,
            resource_kind="source_connection",
            resource_id=connection_id,
        )
        if denied is not None:
            return denied
        return _success(
            api.query(
                principal=_principal(),
                connection_id=connection_id,
                payload={
                    "query": payload["query"],
                    "limit": _bounded_int(
                        payload.get("limit"),
                        field="limit",
                        default=20,
                        minimum=1,
                        maximum=100,
                    ),
                },
            )
        )

    @blueprint.get(
        "/connections/<connection_id>/artifacts/<artifact_id>/status"
    )
    @check_auth
    @_boundary
    def artifact_status(connection_id: str, artifact_id: str):
        denied = _authorize(
            api,
            action=SourceControlAction.ARTIFACT,
            resource_kind="source_connection",
            resource_id=connection_id,
        )
        if denied is not None:
            return denied
        return _success(
            api.artifact_status(
                principal=_principal(),
                connection_id=connection_id,
                artifact_id=artifact_id,
            )
        )

    @blueprint.post("/bulk/plan")
    @check_auth
    @_boundary
    def bulk_plan():
        denied = _authorize(
            api,
            action=SourceControlAction.LIST,
            resource_kind="source_connection",
            collection=True,
        )
        if denied is not None:
            return denied
        payload = _json_object()
        if payload.get("dry_run") is not True:
            raise SourceControlApiError("dry_run_required")
        return _success(api.bulk_plan(principal=_principal(), payload=payload))

    @blueprint.post("/bulk/execute")
    @check_auth
    @_boundary
    def bulk_execute():
        denied = _authorize(
            api,
            action=SourceControlAction.DELETE,
            resource_kind="source_connection",
            collection=True,
        )
        if denied is not None:
            return denied
        payload = _json_object()
        if payload.get("dry_run") is not False:
            raise SourceControlApiError("dry_run_false_required")
        return _success(
            api.bulk_execute(
                principal=_principal(),
                payload=payload,
                idempotency_key=_require_idempotency_key(),
            )
        )

    @blueprint.get("/events")
    @check_auth
    @_boundary
    def poll_events():
        denied = _authorize(
            api,
            action=SourceControlAction.LIST,
            resource_kind="source_control_event",
            collection=True,
        )
        if denied is not None:
            return denied
        return _success(
            api.poll_events(
                principal=_principal(),
                after_sequence=_bounded_int(
                    request.args.get("after_sequence"),
                    field="after_sequence",
                    default=0,
                    minimum=0,
                    maximum=9_223_372_036_854_775_807,
                ),
                limit=_bounded_int(
                    request.args.get("limit"),
                    field="limit",
                    default=100,
                    minimum=1,
                    maximum=500,
                ),
            )
        )

    @blueprint.post("/access/preview")
    @check_auth
    @_boundary
    def access_preview():
        payload = _json_object()
        _require_exact_fields(
            payload,
            _PREVIEW_FIELDS,
            required=_PREVIEW_FIELDS,
        )
        source_revision_id = _required_string(payload, "source_revision_id")
        denied = _authorize(
            api,
            action=SourceControlAction.POLICY,
            resource_kind="source_revision",
            resource_id=source_revision_id,
        )
        if denied is not None:
            return denied
        return _success(
            api.access_preview(principal=_principal(), payload=payload)
        )

    @blueprint.post("/access/matrix")
    @check_auth
    @_boundary
    def access_matrix():
        payload = _json_object()
        _require_exact_fields(
            payload,
            _MATRIX_FIELDS,
            required=frozenset(
                {"operation", "transformation", "purpose"}
            ),
        )
        denied = _authorize(
            api,
            action=SourceControlAction.POLICY,
            resource_kind="source_revision",
            collection=True,
        )
        if denied is not None:
            return denied
        return _success(
            api.access_matrix(principal=_principal(), payload=payload)
        )

    @blueprint.get("/context-policies")
    @check_auth
    @_boundary
    def context_policy_list():
        denied = _authorize(
            api,
            action=SourceControlAction.POLICY,
            resource_kind="context_policy",
            collection=True,
        )
        if denied is not None:
            return denied
        return _success(
            api.context_policy_list(
                principal=_principal(),
                cursor=request.args.get("cursor"),
                limit=_bounded_int(
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
    @_boundary
    def context_policy_versions(policy_id: str):
        denied = _authorize(
            api,
            action=SourceControlAction.POLICY,
            resource_kind="context_policy",
            resource_id=policy_id,
        )
        if denied is not None:
            return denied
        return _success(
            api.context_policy_versions(
                principal=_principal(),
                policy_id=policy_id,
                cursor=request.args.get("cursor"),
                limit=_bounded_int(
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
    @_boundary
    def context_policy_detail(policy_id: str, version: int):
        denied = _authorize(
            api,
            action=SourceControlAction.POLICY,
            resource_kind="context_policy",
            resource_id=policy_id,
        )
        if denied is not None:
            return denied
        data, etag = api.context_policy_detail(
            principal=_principal(),
            policy_id=policy_id,
            version=version,
        )
        response = _success(data)
        response.headers["ETag"] = etag
        return response

    @blueprint.get("/context-policies/<policy_id>/active")
    @check_auth
    @_boundary
    def context_policy_active(policy_id: str):
        denied = _authorize(
            api,
            action=SourceControlAction.POLICY,
            resource_kind="context_policy",
            resource_id=policy_id,
        )
        if denied is not None:
            return denied
        data, etag = api.context_policy_active(
            principal=_principal(),
            policy_id=policy_id,
        )
        response = _success(data)
        response.headers["ETag"] = etag
        return response

    @blueprint.post("/context-policies/<policy_id>/drafts")
    @check_auth
    @_boundary
    def context_policy_draft(policy_id: str):
        denied = _authorize(
            api,
            action=SourceControlAction.POLICY,
            resource_kind="context_policy",
            collection=True,
        )
        if denied is not None:
            return denied
        payload = _json_object()
        _require_exact_fields(
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
        return _success(
            api.context_policy_draft(
                principal=_principal(),
                policy_id=policy_id,
                payload=payload,
                idempotency_key=_require_idempotency_key(),
            ),
            status=201,
        )

    @blueprint.post("/context-policies/lint")
    @check_auth
    @_boundary
    def context_policy_lint():
        payload = _json_object()
        _require_exact_fields(
            payload,
            frozenset({"policy_id", "version"}),
            required=frozenset({"policy_id", "version"}),
        )
        policy_id = _required_string(payload, "policy_id")
        version = _bounded_int(
            payload.get("version"),
            field="version",
            default=0,
            minimum=1,
            maximum=2_147_483_647,
        )
        denied = _authorize(
            api,
            action=SourceControlAction.POLICY,
            resource_kind="context_policy",
            resource_id=policy_id,
        )
        if denied is not None:
            return denied
        return _success(
            api.context_policy_lint(
                principal=_principal(),
                policy_id=policy_id,
                version=version,
            )
        )

    @blueprint.post("/context-policies/<policy_id>/preview")
    @check_auth
    @_boundary
    def context_policy_preview(policy_id: str):
        payload = _json_object()
        allowed = frozenset(
            {
                "version",
                "source_revision_id",
                "destination_id",
                "operation",
                "transformation",
            }
        )
        _require_exact_fields(payload, allowed, required=allowed)
        denied = _authorize(
            api,
            action=SourceControlAction.POLICY,
            resource_kind="context_policy",
            resource_id=policy_id,
        )
        if denied is not None:
            return denied
        return _success(
            api.context_policy_preview(
                principal=_principal(),
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
        payload = _json_object()
        _require_exact_fields(
            payload,
            frozenset({"dry_run"}),
            required=frozenset({"dry_run"}),
        )
        if_match, idempotency_key = _require_execution_contract(payload)
        denied = _authorize(
            api,
            action=SourceControlAction.POLICY,
            resource_kind="context_policy",
            resource_id=policy_id,
        )
        if denied is not None:
            return denied
        return _success(
            api.context_policy_transition(
                principal=_principal(),
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
    @_boundary
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
    @_boundary
    def context_policy_revoke(policy_id: str, version: int):
        return policy_transition_response(
            operation="revoke",
            policy_id=policy_id,
            version=version,
        )

    @blueprint.post("/context-policies/<policy_id>/rollback")
    @check_auth
    @_boundary
    def context_policy_rollback(policy_id: str):
        payload = _json_object()
        allowed = frozenset(
            {"target_version", "expected_latest_version", "dry_run"}
        )
        _require_exact_fields(payload, allowed, required=allowed)
        if not isinstance(payload.get("target_version"), int) or not isinstance(
            payload.get("expected_latest_version"), int
        ):
            raise SourceControlApiError("policy_rollback_invalid")
        if_match, idempotency_key = _require_execution_contract(payload)
        denied = _authorize(
            api,
            action=SourceControlAction.POLICY,
            resource_kind="context_policy",
            resource_id=policy_id,
        )
        if denied is not None:
            return denied
        return _success(
            api.context_policy_rollback(
                principal=_principal(),
                policy_id=policy_id,
                payload=payload,
                if_match=if_match,
                idempotency_key=idempotency_key,
            ),
            status=201,
        )

    @blueprint.get(
        "/connections/<connection_id>/artifacts/<artifact_id>/download"
    )
    @check_auth
    @_boundary
    def artifact_download(connection_id: str, artifact_id: str):
        denied = _authorize(
            api,
            action=SourceControlAction.DOWNLOAD,
            resource_kind="source_connection",
            resource_id=connection_id,
        )
        if denied is not None:
            return denied
        stream = api.artifact_download(
            principal=_principal(),
            connection_id=connection_id,
            artifact_id=artifact_id,
            range_header=request.headers.get("Range"),
        )
        if not isinstance(stream, SourceControlArtifactStream):
            raise SourceControlApiError(
                "artifact_download_result_invalid", status_code=502
            )
        response = Response(
            stream.body,
            status=stream.status_code,
            content_type=stream.media_type,
        )
        response.headers["Content-Length"] = str(stream.content_length)
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{stream.filename}"'
        )
        response.headers["Accept-Ranges"] = "bytes"
        response.headers["ETag"] = stream.etag
        response.headers["X-Content-SHA256"] = stream.sha256
        response.headers["X-Content-Type-Options"] = "nosniff"
        if stream.content_range is not None:
            response.headers["Content-Range"] = stream.content_range
        response.call_on_close(stream.close)
        return response

    @blueprint.post("/codehug/mutations")
    @check_auth
    @_boundary
    def codehug_mutation():
        denied = _authorize(
            api,
            action=SourceControlAction.INDEX,
            resource_kind="source_connection",
            collection=True,
        )
        if denied is not None:
            return denied
        payload = _json_object()
        _require_exact_fields(
            payload,
            frozenset({"mutation_intent_id", "dry_run"}),
            required=frozenset({"mutation_intent_id", "dry_run"}),
        )
        if payload.get("dry_run") is not False:
            raise SourceControlApiError("dry_run_false_required")
        return _success(
            api.codehug_mutation(
                principal=_principal(),
                mutation_intent_id=_required_string(
                    payload, "mutation_intent_id"
                ),
                idempotency_key=_require_idempotency_key(),
            ),
            status=202,
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
    @_boundary
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
    @_boundary
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
    @_boundary
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
    "create_source_control_legacy_alias_blueprint",
    "create_source_control_v1_blueprint",
]
