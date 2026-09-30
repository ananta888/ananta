"""Request parsing and bounded value validation for the semantic-compute contract routes.

Helpers raise ``SemanticContractServiceError`` with the stable reason codes and
HTTP statuses of the v1 surface; ``_error`` renders them.
"""

from __future__ import annotations

from typing import Any

from flask import g, jsonify, request

from agent.models.semantic_principal import SemanticPrincipal
from agent.services.semantic_contract_service import (
    SemanticContractServiceError,
)


_MAX_REQUEST_BYTES = 128 * 1024


def _body(allowed: set[str], *, required: set[str]) -> dict[str, Any]:
    if request.content_length is not None and request.content_length > _MAX_REQUEST_BYTES:
        raise SemanticContractServiceError("request_too_large", status_code=413)
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise SemanticContractServiceError("json_object_required", status_code=400)
    unknown = set(value) - allowed
    missing = required - set(value)
    if unknown:
        raise SemanticContractServiceError("unknown_field", status_code=400)
    if missing:
        raise SemanticContractServiceError("required_field_missing", status_code=400)
    return value


def _worker_body(allowed: set[str], *, required: set[str], maximum_bytes: int) -> dict[str, Any]:
    if request.content_length is not None and request.content_length > maximum_bytes:
        raise SemanticContractServiceError("request_too_large", status_code=413)
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise SemanticContractServiceError("json_object_required", status_code=400)
    if set(value) - allowed:
        raise SemanticContractServiceError("unknown_field", status_code=400)
    if required - set(value):
        raise SemanticContractServiceError("required_field_missing", status_code=400)
    return value


def _worker_url() -> str:
    identity = dict(getattr(g, "service_identity", {}) or {})
    value = str(identity.get("worker_url") or "").strip().rstrip("/")
    if not value:
        raise SemanticContractServiceError("worker_identity_required", status_code=403)
    return value


def _principal() -> SemanticPrincipal:
    identity = dict(getattr(g, "user", {}) or getattr(g, "auth_payload", {}) or {})
    subject = str(identity.get("sub") or identity.get("username") or "").strip()
    tenant = str(identity.get("tenant_id") or identity.get("tenant") or subject).strip()
    if not subject or not tenant:
        raise SemanticContractServiceError("not_authenticated", status_code=401)
    return SemanticPrincipal(tenant, subject)


def _query_scope() -> dict[str, Any]:
    session_id = _identifier(request.args.get("session_id"), "session_id")
    epoch = _bounded_int(request.args.get("epoch"), "epoch", 1, 2_147_483_647)
    return {"session_id": session_id, "epoch": epoch, "consent_version": 1}


def _idempotency_key() -> str:
    key = str(request.headers.get("Idempotency-Key") or "").strip()
    if not 8 <= len(key) <= 256 or any(character.isspace() for character in key):
        raise SemanticContractServiceError("idempotency_key_invalid", status_code=400)
    return key


def _revision_precondition(body: dict[str, Any]) -> int:
    header = str(request.headers.get("If-Match") or "").strip().strip('"')
    raw = header or body.get("expected_revision")
    if raw is None:
        raise SemanticContractServiceError("revision_precondition_required", status_code=428)
    return _bounded_int(raw, "expected_revision", 1, 2_147_483_647)


def _advertisements(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 16 or any(not isinstance(item, dict) for item in value):
        raise SemanticContractServiceError("advertisements_invalid", status_code=400)
    return list(value)


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SemanticContractServiceError(f"{field}_invalid", status_code=400)
    return value


def _resource_budget(value: Any) -> dict[str, int]:
    row = _mapping(value, "resource_budget")
    if set(row) != {"cpu_ms", "memory_bytes", "artifact_bytes"}:
        raise SemanticContractServiceError("resource_budget_invalid", status_code=400)
    return {
        "cpu_ms": _bounded_int(row["cpu_ms"], "cpu_ms", 1, 60_000),
        "memory_bytes": _bounded_int(row["memory_bytes"], "memory_bytes", 1, 4_294_967_296),
        "artifact_bytes": _bounded_int(row["artifact_bytes"], "artifact_bytes", 1, 4_194_304),
    }


def _bounded_string(value: Any, field: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        raise SemanticContractServiceError(f"{field}_invalid", status_code=400)
    encoded = value.encode("utf-8")
    if not minimum <= len(encoded) <= maximum or "\x00" in value:
        raise SemanticContractServiceError(f"{field}_invalid", status_code=400)
    return value


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise SemanticContractServiceError(f"{field}_invalid", status_code=400)
    return value


def _identifier(value: Any, field: str) -> str:
    rendered = str(value or "").strip()
    if not 1 <= len(rendered) <= 192 or not all(char.isalnum() or char in "-_.:@" for char in rendered):
        raise SemanticContractServiceError(f"{field}_invalid", status_code=400)
    return rendered


def _optional_identifier(value: Any, field: str) -> str | None:
    return None if value in {None, ""} else _identifier(value, field)


def _bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise SemanticContractServiceError(f"{field}_invalid", status_code=400) from exc
    if isinstance(value, bool) or not minimum <= result <= maximum:
        raise SemanticContractServiceError(f"{field}_invalid", status_code=400)
    return result


def _error(exc: SemanticContractServiceError):
    return jsonify(
        {"ok": False, "error": {"code": exc.reason_code, "message": exc.reason_code, "retriable": False}}
    ), exc.status_code
