"""Scoped Organization planning and assignment-bound Worker proposal routes."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from flask import Blueprint, g, jsonify, request

from agent.auth import check_auth
from agent.routes.organization_route_support import (
    organization_boundary,
    request_principal,
    require_organization_scope,
)
from agent.services.approval_request_service import ApprovalDecisionError
from agent.services.organization_membership_service import OrganizationAccessPrincipal
from agent.services.organization_planning_composition import (
    OrganizationPlanningCompositionError,
    get_organization_planning_composition,
)
from agent.services.organization_track_planning_contract_service import (
    validate_track_planning_result_carrier,
)
from agent.services.planning_artifact_transition_service import PlanningTransitionError
from agent.services.project_access_authority import ProjectCapability
from agent.services.worker_result_capability_service import (
    WorkerResultCapabilityError,
    WorkerResultCapabilityService,
)
from agent.services.worker_task_proposal_ingress_service import (
    WorkerTaskProposalIngressError,
)
from agent.services.worker_task_proposal_result_adapter import (
    ingest_callback_task_proposals,
)

organization_planning_bp = Blueprint("organization_planning", __name__)

_MAX_WORKER_PROPOSAL_CARRIER_BYTES = 2 * 1024 * 1024
_SHA256_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")


@organization_planning_bp.get("/api/organizations/<organization_id>/planning")
@check_auth
@organization_boundary
def get_organization_planning(organization_id: str):
    try:
        raw_page_size = request.args.get("page_size", request.args.get("limit", "20"))
        page_size = int(str(raw_page_size or "20"))
        if page_size < 1 or page_size > 50:
            raise OrganizationPlanningCompositionError(
                "organization_planning_page_size_invalid",
                status_code=400,
            )
        payload = get_organization_planning_composition().get_planning(
            principal=_operator_principal(
                organization_id,
                ProjectCapability.READ,
            ),
            organization_id=organization_id,
            cursor=str(request.args.get("cursor") or "").strip() or None,
            page_size=page_size,
        )
    except (TypeError, ValueError) as exc:
        return _operator_error(exc)
    return jsonify(payload)


@organization_planning_bp.post("/api/organizations/<organization_id>/goals/<goal_id>/planning/category-research")
@check_auth
@organization_boundary
def create_organization_category_research(organization_id: str, goal_id: str):
    try:
        body = _closed_json_body(
            {
                "unit_id",
                "team_id",
                "role_slot_id",
                "source_catalog_binding",
            }
        )
        catalog_binding = _source_catalog_binding(body.get("source_catalog_binding"))
        payload = get_organization_planning_composition().create_category_research(
            principal=_operator_principal(
                organization_id,
                ProjectCapability.MANAGE,
            ),
            organization_id=organization_id,
            goal_id=str(goal_id or "").strip(),
            unit_id=_required_identifier(body, "unit_id"),
            team_id=_required_identifier(body, "team_id"),
            role_slot_id=_required_identifier(body, "role_slot_id"),
            catalog_binding=catalog_binding,
            idempotency_key=_required_idempotency_header(),
        )
    except (TypeError, ValueError) as exc:
        return _operator_error(exc)
    return jsonify(payload), 200 if payload.get("replayed") else 201


@organization_planning_bp.post("/api/organizations/<organization_id>/planning/<category_revision_id>/derive-tracks")
@check_auth
@organization_boundary
def derive_organization_planning_tracks(
    organization_id: str,
    category_revision_id: str,
):
    try:
        body = _closed_json_body(
            {
                "expected_revision",
                "expected_digest",
                "expected_policy_hash",
                "track_candidates",
                "exclusions",
            }
        )
        expected_revision, expected_digest = _expected_precondition(body)
        candidates = body.get("track_candidates")
        exclusions = body.get("exclusions", {})
        if (
            not isinstance(candidates, list)
            or not candidates
            or len(candidates) > 100
            or any(not isinstance(row, dict) for row in candidates)
            or not isinstance(exclusions, dict)
            or any(not isinstance(key, str) or not isinstance(value, str) for key, value in exclusions.items())
        ):
            raise OrganizationPlanningCompositionError(
                "planning_track_derivation_request_invalid",
                status_code=400,
            )
        payload = get_organization_planning_composition().derive_tracks(
            principal=_operator_principal(
                organization_id,
                ProjectCapability.MANAGE,
            ),
            organization_id=organization_id,
            category_revision_id=category_revision_id,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            expected_policy_hash=_required_text(body, "expected_policy_hash", maximum=128),
            track_candidates=candidates,
            exclusions=exclusions,
            idempotency_key=_required_idempotency_header(),
        )
    except (TypeError, ValueError) as exc:
        return _operator_error(exc)
    return jsonify(payload), 200 if payload.get("replayed") else 201


@organization_planning_bp.post("/api/organizations/<organization_id>/planning/<category_revision_id>/track-planning")
@check_auth
@organization_boundary
def create_organization_track_planning_task(
    organization_id: str,
    category_revision_id: str,
):
    """Create the single Worker-delegable Track planner Task for a revision."""

    try:
        body = _closed_json_body(
            {
                "expected_revision",
                "expected_digest",
                "expected_policy_hash",
                "unit_id",
                "team_id",
                "role_slot_id",
                "source_category_item_ids",
            }
        )
        expected_revision, expected_digest = _expected_precondition(body)
        payload = get_organization_planning_composition().create_track_planning_task(
            principal=_operator_principal(
                organization_id,
                ProjectCapability.MANAGE,
            ),
            organization_id=organization_id,
            category_revision_id=category_revision_id,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            expected_policy_hash=_required_text(
                body,
                "expected_policy_hash",
                maximum=128,
            ),
            unit_id=_required_identifier(body, "unit_id"),
            team_id=_required_identifier(body, "team_id"),
            role_slot_id=_required_identifier(body, "role_slot_id"),
            source_category_item_ids=_bounded_identifier_list(
                body.get("source_category_item_ids"),
                reason_code="track_planning_category_scope_invalid",
            ),
            idempotency_key=_required_idempotency_header(),
        )
    except (TypeError, ValueError) as exc:
        return _operator_error(exc)
    return jsonify(payload), 200 if payload.get("replayed") else 201


@organization_planning_bp.post(
    "/api/organizations/<organization_id>/planning/<category_revision_id>/reference-workflows/<workflow_key>/preview"
)
@check_auth
@organization_boundary
def preview_organization_reference_workflow(
    organization_id: str,
    category_revision_id: str,
    workflow_key: str,
):
    try:
        body = _reference_workflow_body(include_exclusions=False)
        expected_revision, expected_digest = _expected_precondition(body)
        payload = get_organization_planning_composition().preview_reference_workflow(
            principal=_operator_principal(
                organization_id,
                ProjectCapability.READ,
            ),
            organization_id=organization_id,
            category_revision_id=category_revision_id,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            expected_policy_hash=_required_text(
                body,
                "expected_policy_hash",
                maximum=128,
            ),
            workflow_key=str(workflow_key or "").strip(),
            workflow_version=_positive_integer(body, "workflow_version"),
            goal=_required_free_text(body, "goal", maximum=2000),
            source_category_item_ids=list(body["source_category_item_ids"]),
        )
    except (TypeError, ValueError) as exc:
        return _operator_error(exc)
    return jsonify(payload)


@organization_planning_bp.post(
    "/api/organizations/<organization_id>/planning/<category_revision_id>/reference-workflows/<workflow_key>/derive"
)
@check_auth
@organization_boundary
def derive_organization_reference_workflow(
    organization_id: str,
    category_revision_id: str,
    workflow_key: str,
):
    try:
        body = _reference_workflow_body(include_exclusions=True)
        expected_revision, expected_digest = _expected_precondition(body)
        payload = get_organization_planning_composition().derive_reference_workflow(
            principal=_operator_principal(
                organization_id,
                ProjectCapability.MANAGE,
            ),
            organization_id=organization_id,
            category_revision_id=category_revision_id,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            expected_policy_hash=_required_text(
                body,
                "expected_policy_hash",
                maximum=128,
            ),
            workflow_key=str(workflow_key or "").strip(),
            workflow_version=_positive_integer(body, "workflow_version"),
            goal=_required_free_text(body, "goal", maximum=2000),
            source_category_item_ids=list(body["source_category_item_ids"]),
            exclusions=dict(body.get("exclusions") or {}),
            idempotency_key=_required_idempotency_header(),
        )
    except (TypeError, ValueError) as exc:
        return _operator_error(exc)
    return jsonify(payload), 200 if payload.get("replayed") else 201


@organization_planning_bp.post("/api/organizations/<organization_id>/planning/<track_revision_id>/materialize")
@check_auth
@organization_boundary
def materialize_organization_planning_track(
    organization_id: str,
    track_revision_id: str,
):
    try:
        body = _closed_json_body(
            {
                "expected_revision",
                "expected_digest",
                "expected_policy_hash",
                "approval_request_id",
            }
        )
        expected_revision, expected_digest = _expected_precondition(body)
        payload, status_code = get_organization_planning_composition().materialize_track(
            principal=_operator_principal(
                organization_id,
                ProjectCapability.MANAGE,
            ),
            organization_id=organization_id,
            track_revision_id=track_revision_id,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            expected_policy_hash=_required_text(body, "expected_policy_hash", maximum=128),
            approval_request_id=(str(body.get("approval_request_id") or "").strip() or None),
            idempotency_key=_required_idempotency_header(),
        )
    except (TypeError, ValueError) as exc:
        return _operator_error(exc)
    return jsonify(payload), status_code


@organization_planning_bp.post(
    "/api/organizations/<organization_id>/planning/<track_revision_id>/tasks/<plan_task_id>/dispatch-next"
)
@check_auth
@organization_boundary
def dispatch_next_organization_planning_task(
    organization_id: str,
    track_revision_id: str,
    plan_task_id: str,
):
    try:
        body = _closed_json_body({"requested_worker_id", "pump"})
        payload, status_code = get_organization_planning_composition().dispatch_next(
            principal=_operator_principal(
                organization_id,
                ProjectCapability.MANAGE,
            ),
            organization_id=organization_id,
            track_revision_id=track_revision_id,
            plan_task_id=plan_task_id,
            idempotency_key=_required_idempotency_header(),
            requested_worker_id=_optional_worker_hint(body.get("requested_worker_id")),
            pump=_strict_bool(body.get("pump"), default=True),
        )
    except (TypeError, ValueError) as exc:
        return _operator_error(exc)
    return jsonify(payload), status_code


@organization_planning_bp.post("/api/organizations/<organization_id>/planning/dispatches/<dispatch_intent_id>/retry")
@check_auth
@organization_boundary
def retry_organization_planning_dispatch(
    organization_id: str,
    dispatch_intent_id: str,
):
    try:
        body = _closed_json_body({"pump"})
        payload, status_code = get_organization_planning_composition().retry_dispatch(
            principal=_operator_principal(
                organization_id,
                ProjectCapability.MANAGE,
            ),
            organization_id=organization_id,
            dispatch_intent_id=dispatch_intent_id,
            pump=_strict_bool(body.get("pump"), default=True),
        )
    except (TypeError, ValueError) as exc:
        return _operator_error(exc)
    return jsonify(payload), status_code


@organization_planning_bp.post("/api/organizations/<organization_id>/planning/dispatches/pump")
@check_auth
@organization_boundary
def pump_organization_planning_dispatches(organization_id: str):
    """Replay due and expired-lease outbox rows without creating new Tasks."""

    try:
        body = _closed_json_body({"limit"})
        limit = _positive_integer(body, "limit") if "limit" in body else 10
        if limit > 50:
            raise OrganizationPlanningCompositionError(
                "organization_planning_dispatch_limit_invalid",
                status_code=400,
            )
        payload = get_organization_planning_composition().pump_dispatches(
            principal=_operator_principal(
                organization_id,
                ProjectCapability.MANAGE,
            ),
            organization_id=organization_id,
            limit=limit,
        )
    except (TypeError, ValueError) as exc:
        return _operator_error(exc)
    return jsonify(payload)


@organization_planning_bp.post("/api/organizations/<organization_id>/planning/<artifact_revision_id>/promote")
@check_auth
@organization_boundary
def promote_organization_planning_artifact(
    organization_id: str,
    artifact_revision_id: str,
):
    return _transition_artifact(
        organization_id=organization_id,
        artifact_revision_id=artifact_revision_id,
        operation="promote",
    )


@organization_planning_bp.post("/api/organizations/<organization_id>/planning/<artifact_revision_id>/adopt")
@check_auth
@organization_boundary
def adopt_organization_planning_artifact(
    organization_id: str,
    artifact_revision_id: str,
):
    return _transition_artifact(
        organization_id=organization_id,
        artifact_revision_id=artifact_revision_id,
        operation="adopt",
    )


@organization_planning_bp.post("/api/organizations/<organization_id>/proposals/<proposal_id>/approve")
@check_auth
@organization_boundary
def approve_organization_worker_proposal(organization_id: str, proposal_id: str):
    return _decide_proposal(
        organization_id=organization_id,
        proposal_id=proposal_id,
        operation="approve",
    )


@organization_planning_bp.post("/api/organizations/<organization_id>/proposals/<proposal_id>/reject")
@check_auth
@organization_boundary
def reject_organization_worker_proposal(organization_id: str, proposal_id: str):
    return _decide_proposal(
        organization_id=organization_id,
        proposal_id=proposal_id,
        operation="reject",
    )


@organization_planning_bp.post("/api/worker-results/tasks/<source_task_id>/assignments/<assignment_id>/proposals")
def ingest_assignment_bound_worker_proposals(source_task_id: str, assignment_id: str):
    """Capability-only result carrier; never accepts user/admin/Hub bearers."""

    if request.content_length is not None and request.content_length > _MAX_WORKER_PROPOSAL_CARRIER_BYTES:
        return _worker_error("worker_task_proposals_carrier_too_large", 413)
    auth_header = str(request.headers.get("Authorization") or "")
    token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""
    if not token.startswith("wrc1."):
        return _worker_error("worker_result_capability_required", 401)
    if not request.is_json:
        return _worker_error("worker_task_proposals_json_required", 415)
    carrier = request.get_json(silent=True)
    if not isinstance(carrier, dict):
        return _worker_error("worker_task_proposals_carrier_invalid", 400)
    if set(carrier) != {"schema", "payload_digest", "proposals"}:
        return _worker_error("worker_task_proposals_carrier_invalid", 422)
    proposals = carrier.get("proposals")
    if (
        carrier.get("schema") != "worker_task_proposals.v1"
        or _SHA256_DIGEST.fullmatch(str(carrier.get("payload_digest") or "")) is None
        or not isinstance(proposals, list)
        or not 1 <= len(proposals) <= 100
        or any(not isinstance(row, dict) for row in proposals)
    ):
        return _worker_error("worker_task_proposals_carrier_invalid", 422)
    try:
        claims = WorkerResultCapabilityService().verify(
            token,
            source_task_id=source_task_id,
            assignment_id=assignment_id,
        )
        results = ingest_callback_task_proposals(
            source_task_id=source_task_id,
            callback_payload={"task_proposals": carrier},
            capability_claims=claims,
        )
    except WorkerResultCapabilityError:
        return _worker_error("worker_result_capability_invalid", 401)
    except WorkerTaskProposalIngressError as exc:
        status_code = _worker_ingress_status(exc.reason_code)
        return _worker_error(exc.reason_code, status_code)
    return (
        jsonify(
            {
                "schema": "worker_task_proposal_ingress_receipt.v1",
                "source_task_id": source_task_id,
                "assignment_id": assignment_id,
                "proposals": [_normalize_worker_ingress(row) for row in results],
                "task_created": False,
                "queue_write": False,
            }
        ),
        202,
    )


@organization_planning_bp.post(
    "/api/worker-results/tasks/<source_task_id>/assignments/<assignment_id>/planning/category"
)
def ingest_assignment_bound_category_research(
    source_task_id: str,
    assignment_id: str,
):
    """Accept one closed Category result under a Worker result capability."""

    if request.content_length is not None and request.content_length > _MAX_WORKER_PROPOSAL_CARRIER_BYTES:
        return _worker_error("category_research_result_too_large", 413)
    auth_header = str(request.headers.get("Authorization") or "")
    token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""
    if not token.startswith("wrc1."):
        return _worker_error("worker_result_capability_required", 401)
    if not request.is_json:
        return _worker_error("category_research_result_json_required", 415)
    carrier = request.get_json(silent=True)
    if not isinstance(carrier, dict) or set(carrier) != {
        "schema",
        "payload_digest",
        "raw_output",
        "runtime_artifact_hashes",
    }:
        return _worker_error("category_research_result_carrier_invalid", 422)
    raw_output_value = carrier.get("raw_output")
    raw_output = (
        json.dumps(raw_output_value, sort_keys=True, separators=(",", ":"))
        if isinstance(raw_output_value, dict)
        else str(raw_output_value or "")
    )
    digest = str(carrier.get("payload_digest") or "")
    artifact_hashes = carrier.get("runtime_artifact_hashes")
    if (
        carrier.get("schema") != "organization_category_research_result.v1"
        or _SHA256_DIGEST.fullmatch(digest) is None
        or not raw_output
        or len(raw_output.encode("utf-8")) > _MAX_WORKER_PROPOSAL_CARRIER_BYTES
        or not isinstance(artifact_hashes, dict)
        or any(not isinstance(key, str) or not isinstance(value, str) for key, value in artifact_hashes.items())
    ):
        return _worker_error("category_research_result_carrier_invalid", 422)
    if "sha256:" + hashlib.sha256(raw_output.encode("utf-8")).hexdigest() != digest:
        return _worker_error("category_research_result_digest_mismatch", 422)
    try:
        claims = WorkerResultCapabilityService().verify(
            token,
            source_task_id=source_task_id,
            assignment_id=assignment_id,
        )
        payload = get_organization_planning_composition().accept_category_research_result(
            source_task_id=source_task_id,
            assignment_id=assignment_id,
            capability_claims=claims,
            raw_output=raw_output,
            raw_output_digest=digest.removeprefix("sha256:"),
            idempotency_key=_required_idempotency_header(worker=True),
            runtime_artifact_hashes=artifact_hashes,
        )
    except WorkerResultCapabilityError:
        return _worker_error("worker_result_capability_invalid", 401)
    except (TypeError, ValueError) as exc:
        reason_code = str(getattr(exc, "reason_code", "") or str(exc) or "category_research_result_invalid")
        return _worker_error(
            reason_code,
            _worker_ingress_status(reason_code),
            details=_error_details(exc),
        )
    return jsonify(payload), 200 if payload.get("replayed") else 201


@organization_planning_bp.post("/api/worker-results/tasks/<source_task_id>/assignments/<assignment_id>/planning/tracks")
def ingest_assignment_bound_track_planning(
    source_task_id: str,
    assignment_id: str,
):
    """Admit a closed Track candidate carrier; never materialize its tasks."""

    if request.content_length is not None and request.content_length > _MAX_WORKER_PROPOSAL_CARRIER_BYTES:
        return _worker_error("track_planning_result_too_large", 413)
    auth_header = str(request.headers.get("Authorization") or "")
    token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""
    if not token.startswith("wrc1."):
        return _worker_error("worker_result_capability_required", 401)
    if not request.is_json:
        return _worker_error("track_planning_result_json_required", 415)
    raw_carrier = request.get_json(silent=True)
    if not isinstance(raw_carrier, dict):
        return _worker_error("track_planning_result_carrier_invalid", 400)
    try:
        carrier = validate_track_planning_result_carrier(raw_carrier)
        claims = WorkerResultCapabilityService().verify(
            token,
            source_task_id=source_task_id,
            assignment_id=assignment_id,
        )
        payload = get_organization_planning_composition().accept_track_planning_result(
            source_task_id=source_task_id,
            assignment_id=assignment_id,
            capability_claims=claims,
            carrier=carrier,
            idempotency_key=_required_idempotency_header(worker=True),
        )
    except WorkerResultCapabilityError:
        return _worker_error("worker_result_capability_invalid", 401)
    except (TypeError, ValueError) as exc:
        reason_code = str(getattr(exc, "reason_code", "") or str(exc) or "track_planning_result_invalid")
        return _worker_error(
            reason_code,
            _worker_ingress_status(reason_code),
            details=_error_details(exc),
        )
    return jsonify(payload), 200 if payload.get("replayed") else 201


def _transition_artifact(
    *,
    organization_id: str,
    artifact_revision_id: str,
    operation: str,
):
    try:
        body = _closed_json_body(
            {
                "expected_revision",
                "expected_digest",
                "approval_request_id",
                "approval_id",
                "idempotency_key",
            }
        )
        expected_revision, expected_digest = _expected_precondition(body)
        principal = _operator_principal(
            organization_id,
            ProjectCapability.MANAGE,
        )
        idempotency_key = _idempotency_key(
            body=body,
            principal=principal,
            organization_id=organization_id,
            object_id=artifact_revision_id,
            operation=operation,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
        )
        payload, status_code = get_organization_planning_composition().transition_artifact(
            principal=principal,
            organization_id=organization_id,
            artifact_revision_id=artifact_revision_id,
            operation=operation,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            approval_request_id=str(body.get("approval_request_id") or body.get("approval_id") or "").strip() or None,
            idempotency_key=idempotency_key,
        )
    except (TypeError, ValueError) as exc:
        return _operator_error(exc)
    return jsonify(payload), status_code


def _decide_proposal(*, organization_id: str, proposal_id: str, operation: str):
    try:
        body = _closed_json_body({"expected_revision", "expected_digest"})
        expected_revision, expected_digest = _expected_precondition(body)
        payload = get_organization_planning_composition().decide_proposal(
            principal=_operator_principal(
                organization_id,
                ProjectCapability.MANAGE,
            ),
            organization_id=organization_id,
            proposal_id=proposal_id,
            operation=operation,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
        )
    except (TypeError, ValueError) as exc:
        return _operator_error(exc)
    return jsonify(payload)


def _operator_principal(
    organization_id: str,
    capability: ProjectCapability,
) -> OrganizationAccessPrincipal:
    route_principal = request_principal()
    scope = require_organization_scope(organization_id, capability)
    identity = (getattr(g, "user", {}) or {}) or (getattr(g, "auth_payload", {}) or {})
    credential_type = str(identity.get("credential_type") or "user")
    return OrganizationAccessPrincipal(
        principal_id=route_principal.subject_id,
        tenant_id=scope.tenant_id,
        credential_type=credential_type,
        project_id=scope.project_id,
    )


def _json_body() -> dict[str, Any]:
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


def _closed_json_body(allowed_fields: set[str]) -> dict[str, Any]:
    body = _json_body()
    unknown = sorted(set(body) - allowed_fields)
    if unknown:
        raise OrganizationPlanningCompositionError(
            "organization_planning_request_fields_invalid",
            status_code=400,
        )
    return body


def _required_identifier(body: dict[str, Any], field: str) -> str:
    return _required_text(body, field, maximum=191)


def _bounded_identifier_list(value: Any, *, reason_code: str) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise OrganizationPlanningCompositionError(reason_code, status_code=400)
    normalized = [str(item or "").strip() if isinstance(item, str) else "" for item in value]
    if any(not item or len(item) > 191 or any(character.isspace() for character in item) for item in normalized) or len(
        set(normalized)
    ) != len(normalized):
        raise OrganizationPlanningCompositionError(reason_code, status_code=400)
    return normalized


def _required_text(body: dict[str, Any], field: str, *, maximum: int) -> str:
    value = str(body.get(field) or "").strip()
    if not value or len(value) > maximum or any(character.isspace() for character in value):
        raise OrganizationPlanningCompositionError(
            f"organization_planning_{field}_invalid",
            status_code=400,
        )
    return value


def _required_idempotency_header(*, worker: bool = False) -> str:
    value = str(request.headers.get("Idempotency-Key") or "").strip()
    if not 8 <= len(value) <= 191 or any(character.isspace() for character in value):
        if worker:
            raise ValueError("planning_idempotency_key_required")
        raise OrganizationPlanningCompositionError(
            "organization_planning_idempotency_key_invalid",
            status_code=400,
        )
    return value


def _strict_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise OrganizationPlanningCompositionError(
            "organization_planning_boolean_invalid",
            status_code=400,
        )
    return value


def _optional_worker_hint(value: Any) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if len(normalized) > 512 or any(character.isspace() for character in normalized):
        raise OrganizationPlanningCompositionError(
            "organization_planning_requested_worker_id_invalid",
            status_code=400,
        )
    return normalized


def _positive_integer(body: dict[str, Any], field: str) -> int:
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


def _required_free_text(body: dict[str, Any], field: str, *, maximum: int) -> str:
    value = str(body.get(field) or "").strip()
    if not value or len(value) > maximum:
        raise OrganizationPlanningCompositionError(
            f"organization_planning_{field}_invalid",
            status_code=400,
        )
    return value


def _reference_workflow_body(*, include_exclusions: bool) -> dict[str, Any]:
    fields = {
        "expected_revision",
        "expected_digest",
        "expected_policy_hash",
        "workflow_version",
        "goal",
        "source_category_item_ids",
    }
    if include_exclusions:
        fields.add("exclusions")
    body = _closed_json_body(fields)
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


def _source_catalog_binding(value: Any) -> dict[str, str]:
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


def _expected_precondition(body: dict[str, Any]) -> tuple[int, str]:
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


def _idempotency_key(
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


def _operator_error(exc: BaseException):
    if isinstance(exc, OrganizationPlanningCompositionError):
        return jsonify({"error": exc.reason_code, "reason_code": exc.reason_code}), exc.status_code
    if isinstance(exc, ApprovalDecisionError):
        if exc.code == "request_not_found" or exc.code in {
            "approval_tool_mismatch",
            "approval_intent_mismatch",
            "approval_tenant_mismatch",
            "approval_project_mismatch",
            "approval_goal_mismatch",
            "approval_organization_mismatch",
        }:
            return jsonify(
                {
                    "error": "organization_planning_not_found",
                    "reason_code": "organization_planning_not_found",
                }
            ), 404
        return jsonify({"error": exc.code, "reason_code": exc.code}), exc.http_status
    if isinstance(exc, WorkerTaskProposalIngressError):
        reason_code = exc.reason_code
        status_code = 404 if reason_code.endswith("not_found") else 409
        return jsonify({"error": reason_code, "reason_code": reason_code}), status_code
    if isinstance(exc, PlanningTransitionError):
        reason_code = exc.reason_code
        if "not_found" in reason_code or reason_code == "planning_scope_forbidden":
            return jsonify(
                {
                    "error": "organization_planning_not_found",
                    "reason_code": "organization_planning_not_found",
                }
            ), 404
        if "precondition" in reason_code or reason_code.endswith("_digest_mismatch"):
            status_code = 412
        elif "approval" in reason_code or "conflict" in reason_code or "stale" in reason_code:
            status_code = 409
        elif "authority" in reason_code or "forbidden" in reason_code or "admin_required" in reason_code:
            status_code = 403
        else:
            status_code = 422
        return jsonify({"error": reason_code, "reason_code": reason_code}), status_code
    reason_code = str(getattr(exc, "reason_code", "") or "")
    if reason_code:
        if "not_found" in reason_code:
            status_code = 404
        elif "digest" in reason_code or "stale" in reason_code:
            status_code = 412
        elif "idempotency" in reason_code or "conflict" in reason_code:
            status_code = 409
        elif "forbidden" in reason_code or "authority" in reason_code:
            status_code = 403
        else:
            status_code = 422
        details = _error_details(exc)
        return (
            jsonify(
                {
                    "error": reason_code,
                    "reason_code": reason_code,
                    **({"details": details} if details else {}),
                }
            ),
            status_code,
        )
    return jsonify(
        {
            "error": "organization_planning_request_invalid",
            "reason_code": "organization_planning_request_invalid",
        }
    ), 400


def _worker_ingress_status(reason_code: str) -> int:
    if "not_found" in reason_code:
        return 404
    if "idempotency_conflict" in reason_code or "lease" in reason_code or "assignment" in reason_code:
        return 409
    if "credential" in reason_code or "worker_mismatch" in reason_code:
        return 403
    return 422


def _error_details(exc: BaseException) -> dict[str, Any]:
    raw = getattr(exc, "details", None)
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, (list, tuple)):
        return {"issues": [str(value) for value in raw]}
    return {}


def _worker_error(
    reason_code: str,
    status_code: int,
    *,
    details: dict[str, Any] | None = None,
):
    return (
        jsonify(
            {
                "error": reason_code,
                "reason_code": reason_code,
                **({"details": details} if details else {}),
            }
        ),
        status_code,
    )


def _normalize_worker_ingress(row: dict[str, Any]) -> dict[str, Any]:
    proposal_revision = int(row.get("proposal_revision") or 0)
    proposal_digest = str(row.get("proposal_digest") or "")
    return {
        **dict(row),
        "revision": str(proposal_revision),
        "digest": proposal_digest,
        "status": "pending" if row.get("state") == "submitted" else row.get("state"),
    }


__all__ = ["organization_planning_bp"]
