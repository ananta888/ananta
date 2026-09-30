"""HTTP envelope, request parsing and error boundary of the Source Control v1 API.

Response envelopes, strict body/header/query validation, the audited
``check_auth`` wrapper and the ``boundary`` decorator that maps domain
errors onto stable reason codes without leaking implementation details.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import wraps

from flask import Response, current_app, jsonify, make_response, request

from agent.auth import (
    check_auth as _base_check_auth,
)
from agent.routes.source_control_access import (
    record_source_control_route_denial,
)
from agent.services.source_control_connection_binding import (
    SourceControlConnectionBindingError,
    normalize_workspace_relative_path,
)


SUCCESS_SCHEMA = "ananta.source-control.api-response.v1"
ERROR_SCHEMA = "ananta.source-control.error.v1"
SOURCE_GRAPH_MAX_EDGES = 2_000
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
GRAPH_DOMAIN_SCOPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
CONNECTION_FILTERS = frozenset(
    {
        "project_id",
        "cursor",
        "limit",
        "state",
        "connector_type",
        "owner_id",
        "sensitivity",
    }
)
PREVIEW_FIELDS = frozenset(
    {
        "source_revision_id",
        "destination_id",
        "operation",
        "transformation",
        "purpose",
    }
)
MATRIX_FIELDS = frozenset(
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


def success_response(data: Mapping[str, object], *, status: int = 200) -> Response:
    response = jsonify({"schema": SUCCESS_SCHEMA, "data": dict(data)})
    response.status_code = status
    return response


def error_response(reason_code: str, status_code: int) -> tuple[Response, int]:
    return (
        jsonify(
            {
                "schema": ERROR_SCHEMA,
                "error": {"code": reason_code},
            }
        ),
        status_code,
    )


def json_object() -> dict[str, object]:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise SourceControlApiError("request_body_invalid")
    return value


def bounded_int(
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


def query_boolean(value: object, *, field: str, default: bool) -> bool:
    if value is None or value == "":
        return default
    normalized = str(value).strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise SourceControlApiError(f"{field}_invalid")


def required_string(
    payload: Mapping[str, object], field: str, *, maximum: int = 256
) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise SourceControlApiError(f"{field}_invalid")
    return value.strip()


def require_exact_fields(
    payload: Mapping[str, object],
    allowed: frozenset[str],
    *,
    required: frozenset[str],
) -> None:
    if not required.issubset(payload):
        raise SourceControlApiError("request_fields_missing")
    if set(payload) - allowed:
        raise SourceControlApiError("request_fields_forbidden")


def require_execution_contract(payload: Mapping[str, object]) -> tuple[str, str]:
    if payload.get("dry_run") is not False:
        raise SourceControlApiError("dry_run_false_required")
    if_match = request.headers.get("If-Match", "").strip()
    if not if_match:
        raise SourceControlApiError(
            "if_match_required",
            status_code=428,
        )
    idempotency_key = request.headers.get("Idempotency-Key", "").strip()
    if not IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        raise SourceControlApiError(
            "idempotency_key_required",
            status_code=428,
        )
    return if_match, idempotency_key


def require_idempotency_key() -> str:
    value = request.headers.get("Idempotency-Key", "").strip()
    if not IDEMPOTENCY_KEY.fullmatch(value):
        raise SourceControlApiError(
            "idempotency_key_required",
            status_code=428,
        )
    return value


def require_grant_mutation_headers() -> tuple[str, str]:
    if_match = request.headers.get("If-Match", "").strip()
    if not if_match:
        raise SourceControlApiError(
            "if_match_required",
            status_code=428,
        )
    return if_match, require_idempotency_key()


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


def connection_intent_payload() -> dict[str, object]:
    payload = json_object()
    connector_type = required_string(payload, "connector_type")
    common = frozenset(
        {"connector_type", "display_name", "sensitivity", "dry_run"}
    )
    if connector_type in {"registered_workspace", "local_directory"}:
        required = common | {"workspace_id"}
        allowed = required | {"relative_path"}
        required_string(payload, "workspace_id")
        if "relative_path" in payload:
            relative_path = required_string(payload, "relative_path")
            try:
                payload["relative_path"] = (
                    normalize_workspace_relative_path(relative_path)
                )
            except SourceControlConnectionBindingError as exc:
                raise SourceControlApiError(exc.reason_code) from None
    elif connector_type in {"git", "github"}:
        allowed = common | {"remote_id"}
        required = allowed
        required_string(payload, "remote_id")
    else:
        raise SourceControlApiError("connector_type_not_registered")
    require_exact_fields(
        payload,
        frozenset(allowed),
        required=frozenset(required),
    )
    required_string(payload, "display_name")
    required_string(payload, "sensitivity")
    return payload


def boundary(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except SourceControlApiError as exc:
            return error_response(exc.reason_code, exc.status_code)
        except Exception as exc:
            reason_code = getattr(exc, "reason_code", None)
            if isinstance(reason_code, str) and reason_code:
                status_code = int(
                    getattr(
                        exc,
                        "status_code",
                        status_for_reason(reason_code),
                    )
                )
                return error_response(reason_code, status_code)
            current_app.logger.exception(
                "Unhandled Source-Control V1 boundary error"
            )
            return error_response("source_control_internal_error", 500)

    return wrapped


def status_for_reason(reason_code: str) -> int:
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
