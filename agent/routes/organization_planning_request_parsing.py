"""Closed request-body, header and precondition parsing for Organization planning routes."""

from __future__ import annotations

import hashlib
from typing import Any

from flask import request

from agent.services.organization_membership_service import OrganizationAccessPrincipal
from agent.services.organization_planning_composition import (
    OrganizationPlanningCompositionError,
)


def json_body() -> dict[str, Any]:
    if not request.is_json:
        raise OrganizationPlanningCompositionError(
            "organization_planning_json_required",
            status_code=415,
        )
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise OrganizationPlanningCompositionError(
            "organization_planning_request_invalid",
            status_code=400,
        )
    return body


def closed_json_body(allowed_fields: set[str]) -> dict[str, Any]:
    body = json_body()
    unknown = sorted(set(body) - allowed_fields)
    if unknown:
        raise OrganizationPlanningCompositionError(
            "organization_planning_request_fields_invalid",
            status_code=400,
        )
    return body


def required_identifier(body: dict[str, Any], field: str) -> str:
    return required_text(body, field, maximum=191)


def optional_identifier(
    body: dict[str, Any],
    field: str,
) -> str | None:
    if field not in body or body[field] is None:
        return None
    return required_identifier(body, field)


def closed_query_identifiers(fields: set[str]) -> dict[str, str]:
    if set(request.args) != fields or any(len(request.args.getlist(field)) != 1 for field in fields):
        raise OrganizationPlanningCompositionError(
            "category_research_readiness_selector_invalid",
            status_code=400,
        )
    values = {field: str(request.args.get(field) or "").strip() for field in fields}
    if any(
        not value or len(value) > 191 or any(character.isspace() for character in value) for value in values.values()
    ):
        raise OrganizationPlanningCompositionError(
            "category_research_readiness_selector_invalid",
            status_code=400,
        )
    return values


def bounded_identifier_list(value: Any, *, reason_code: str) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise OrganizationPlanningCompositionError(reason_code, status_code=400)
    normalized = [str(item or "").strip() if isinstance(item, str) else "" for item in value]
    if any(not item or len(item) > 191 or any(character.isspace() for character in item) for item in normalized) or len(
        set(normalized)
    ) != len(normalized):
        raise OrganizationPlanningCompositionError(reason_code, status_code=400)
    return normalized


def required_text(body: dict[str, Any], field: str, *, maximum: int) -> str:
    value = str(body.get(field) or "").strip()
    if not value or len(value) > maximum or any(character.isspace() for character in value):
        raise OrganizationPlanningCompositionError(
            f"organization_planning_{field}_invalid",
            status_code=400,
        )
    return value


def required_idempotency_header(*, worker: bool = False) -> str:
    value = str(request.headers.get("Idempotency-Key") or "").strip()
    if not 8 <= len(value) <= 191 or any(character.isspace() for character in value):
        if worker:
            raise ValueError("planning_idempotency_key_required")
        raise OrganizationPlanningCompositionError(
            "organization_planning_idempotency_key_invalid",
            status_code=400,
        )
    return value


def strict_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise OrganizationPlanningCompositionError(
            "organization_planning_boolean_invalid",
            status_code=400,
        )
    return value


def optional_worker_hint(value: Any) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if len(normalized) > 512 or any(character.isspace() for character in normalized):
        raise OrganizationPlanningCompositionError(
            "organization_planning_requested_worker_id_invalid",
            status_code=400,
        )
    return normalized


def positive_integer(body: dict[str, Any], field: str) -> int:
    value = body.get(field)
    if isinstance(value, bool):
        value = None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise OrganizationPlanningCompositionError(
            f"organization_planning_{field}_invalid",
            status_code=400,
        ) from exc
    if parsed < 1 or parsed > 2**31 - 1:
        raise OrganizationPlanningCompositionError(
            f"organization_planning_{field}_invalid",
            status_code=400,
        )
    return parsed


def required_free_text(body: dict[str, Any], field: str, *, maximum: int) -> str:
    value = str(body.get(field) or "").strip()
    if not value or len(value) > maximum:
        raise OrganizationPlanningCompositionError(
            f"organization_planning_{field}_invalid",
            status_code=400,
        )
    return value


def reference_workflow_body(*, include_exclusions: bool) -> dict[str, Any]:
    fields = {
        "expected_revision",
        "expected_digest",
        "expected_policy_hash",
        "workflow_version",
        "goal",
        "source_category_item_ids",
        "target_unit_id",
    }
    if include_exclusions:
        fields.add("exclusions")
    body = closed_json_body(fields)
    source_ids = body.get("source_category_item_ids")
    exclusions = body.get("exclusions", {})
    if (
        not isinstance(source_ids, list)
        or len(source_ids) != 1
        or not isinstance(source_ids[0], str)
        or not source_ids[0].strip()
        or len(source_ids[0]) > 191
        or not isinstance(exclusions, dict)
        or any(
            not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not value.strip()
            for key, value in exclusions.items()
        )
    ):
        raise OrganizationPlanningCompositionError(
            "organization_reference_workflow_request_invalid",
            status_code=400,
        )
    body["source_category_item_ids"] = [source_ids[0].strip()]
    return body


def source_catalog_binding(value: Any) -> dict[str, str]:
    fields = {
        "catalog_task_id",
        "catalog_id",
        "catalog_hash",
        "repository_revision",
        "manifest_hash",
        "source_allowlist_version",
        "source_scope",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise OrganizationPlanningCompositionError(
            "category_research_source_catalog_binding_invalid",
            status_code=400,
        )
    normalized = {field: str(value.get(field) or "").strip() for field in fields}
    if any(
        not item or len(item) > 191 or any(character.isspace() for character in item) for item in normalized.values()
    ):
        raise OrganizationPlanningCompositionError(
            "category_research_source_catalog_binding_invalid",
            status_code=400,
        )
    return normalized


def expected_precondition(body: dict[str, Any]) -> tuple[int, str]:
    raw_revision = body.get("expected_revision")
    expected_digest = str(body.get("expected_digest") or "").strip()
    if raw_revision is None or not expected_digest:
        raise OrganizationPlanningCompositionError(
            "organization_planning_precondition_required",
            status_code=428,
        )
    try:
        expected_revision = int(str(raw_revision))
    except (TypeError, ValueError) as exc:
        raise OrganizationPlanningCompositionError(
            "organization_planning_precondition_invalid",
            status_code=400,
        ) from exc
    if expected_revision < 1 or len(expected_digest) > 128:
        raise OrganizationPlanningCompositionError(
            "organization_planning_precondition_invalid",
            status_code=400,
        )
    if_match = str(request.headers.get("If-Match") or "").strip()
    if not if_match:
        raise OrganizationPlanningCompositionError(
            "organization_planning_precondition_required",
            status_code=428,
        )
    normalized = if_match.removeprefix("W/").strip().strip('"')
    accepted = {
        str(expected_revision),
        expected_digest,
        f"{expected_revision}:{expected_digest}",
        f"{expected_revision}@{expected_digest}",
    }
    if normalized not in accepted:
        raise OrganizationPlanningCompositionError(
            "organization_planning_precondition_failed",
            status_code=412,
        )
    return expected_revision, expected_digest


def resolve_idempotency_key(
    *,
    body: dict[str, Any],
    principal: OrganizationAccessPrincipal,
    organization_id: str,
    object_id: str,
    operation: str,
    expected_revision: int,
    expected_digest: str,
) -> str:
    header_key = str(request.headers.get("Idempotency-Key") or "").strip()
    body_key = str(body.get("idempotency_key") or "").strip()
    if header_key and body_key and header_key != body_key:
        raise OrganizationPlanningCompositionError(
            "organization_planning_idempotency_key_conflict",
            status_code=409,
        )
    supplied = header_key or body_key
    if supplied:
        if not 8 <= len(supplied) <= 191 or any(character.isspace() for character in supplied):
            raise OrganizationPlanningCompositionError(
                "organization_planning_idempotency_key_invalid",
                status_code=400,
            )
        return supplied
    seed = ":".join(
        (
            principal.principal_id,
            organization_id,
            operation,
            object_id,
            str(expected_revision),
            expected_digest,
        )
    )
    return f"organization-planning:{hashlib.sha256(seed.encode('utf-8')).hexdigest()}"


__all__ = [
    "bounded_identifier_list",
    "closed_json_body",
    "closed_query_identifiers",
    "expected_precondition",
    "json_body",
    "optional_identifier",
    "optional_worker_hint",
    "positive_integer",
    "reference_workflow_body",
    "required_free_text",
    "required_idempotency_header",
    "required_identifier",
    "required_text",
    "resolve_idempotency_key",
    "source_catalog_binding",
    "strict_bool",
]
