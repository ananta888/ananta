"""Scoped Organization planning and assignment-bound Worker proposal routes."""

from __future__ import annotations

import hashlib
import json
import re

from flask import Blueprint, g, jsonify, request

from agent.auth import check_auth
from agent.routes.organization_planning_request_parsing import (
    bounded_identifier_list,
    closed_json_body,
    closed_query_identifiers,
    expected_precondition,
    optional_identifier,
    optional_worker_hint,
    positive_integer,
    reference_workflow_body,
    required_free_text,
    required_idempotency_header,
    required_identifier,
    required_text,
    resolve_idempotency_key,
    source_catalog_binding,
    strict_bool,
)
from agent.routes.organization_planning_responses import (
    error_details,
    normalize_worker_ingress,
    operator_error,
    worker_error,
    worker_ingress_status,
)
from agent.routes.organization_route_support import (
    organization_boundary,
    request_principal,
    require_organization_scope,
)
from agent.services.organization_membership_service import OrganizationAccessPrincipal
from agent.services.organization_planning_composition import (
    OrganizationPlanningCompositionError,
    get_organization_planning_composition,
)
from agent.services.organization_track_planning_contract_service import (
    validate_track_planning_result_carrier,
)
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
        return operator_error(exc)
    return jsonify(payload)


@organization_planning_bp.post("/api/organizations/<organization_id>/goals/<goal_id>/planning/category-research")
@check_auth
@organization_boundary
def create_organization_category_research(organization_id: str, goal_id: str):
    try:
        body = closed_json_body(
            {
                "unit_id",
                "team_id",
                "role_slot_id",
                "source_catalog_binding",
            }
        )
        catalog_binding = source_catalog_binding(body.get("source_catalog_binding"))
        payload = get_organization_planning_composition().create_category_research(
            principal=_operator_principal(
                organization_id,
                ProjectCapability.MANAGE,
            ),
            organization_id=organization_id,
            goal_id=str(goal_id or "").strip(),
            unit_id=required_identifier(body, "unit_id"),
            team_id=required_identifier(body, "team_id"),
            role_slot_id=required_identifier(body, "role_slot_id"),
            catalog_binding=catalog_binding,
            idempotency_key=required_idempotency_header(),
        )
    except (TypeError, ValueError) as exc:
        return operator_error(exc)
    return jsonify(payload), 200 if payload.get("replayed") else 201


@organization_planning_bp.get(
    "/api/organizations/<organization_id>/goals/<goal_id>/planning/category-research/readiness"
)
@check_auth
@organization_boundary
def get_organization_category_research_readiness(organization_id: str, goal_id: str):
    """Resolve the Category-research selector and project current readiness."""

    try:
        selector = closed_query_identifiers({"unit_id", "team_id", "role_slot_id", "catalog_task_id"})
        payload = get_organization_planning_composition().get_category_research_readiness(
            principal=_operator_principal(
                organization_id,
                ProjectCapability.READ,
            ),
            organization_id=organization_id,
            goal_id=str(goal_id or "").strip(),
            unit_id=selector["unit_id"],
            team_id=selector["team_id"],
            role_slot_id=selector["role_slot_id"],
            catalog_task_id=selector["catalog_task_id"],
        )
    except (TypeError, ValueError) as exc:
        return operator_error(exc)
    return jsonify(payload)


@organization_planning_bp.post("/api/organizations/<organization_id>/planning/<category_revision_id>/derive-tracks")
@check_auth
@organization_boundary
def derive_organization_planning_tracks(
    organization_id: str,
    category_revision_id: str,
):
    try:
        body = closed_json_body(
            {
                "expected_revision",
                "expected_digest",
                "expected_policy_hash",
                "track_candidates",
                "exclusions",
            }
        )
        expected_revision, expected_digest = expected_precondition(body)
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
            expected_policy_hash=required_text(body, "expected_policy_hash", maximum=128),
            track_candidates=candidates,
            exclusions=exclusions,
            idempotency_key=required_idempotency_header(),
        )
    except (TypeError, ValueError) as exc:
        return operator_error(exc)
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
        body = closed_json_body(
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
        expected_revision, expected_digest = expected_precondition(body)
        payload = get_organization_planning_composition().create_track_planning_task(
            principal=_operator_principal(
                organization_id,
                ProjectCapability.MANAGE,
            ),
            organization_id=organization_id,
            category_revision_id=category_revision_id,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            expected_policy_hash=required_text(
                body,
                "expected_policy_hash",
                maximum=128,
            ),
            unit_id=required_identifier(body, "unit_id"),
            team_id=required_identifier(body, "team_id"),
            role_slot_id=required_identifier(body, "role_slot_id"),
            source_category_item_ids=bounded_identifier_list(
                body.get("source_category_item_ids"),
                reason_code="track_planning_category_scope_invalid",
            ),
            idempotency_key=required_idempotency_header(),
        )
    except (TypeError, ValueError) as exc:
        return operator_error(exc)
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
        body = reference_workflow_body(include_exclusions=False)
        expected_revision, expected_digest = expected_precondition(body)
        payload = get_organization_planning_composition().preview_reference_workflow(
            principal=_operator_principal(
                organization_id,
                ProjectCapability.READ,
            ),
            organization_id=organization_id,
            category_revision_id=category_revision_id,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            expected_policy_hash=required_text(
                body,
                "expected_policy_hash",
                maximum=128,
            ),
            workflow_key=str(workflow_key or "").strip(),
            workflow_version=positive_integer(body, "workflow_version"),
            goal=required_free_text(body, "goal", maximum=2000),
            source_category_item_ids=list(body["source_category_item_ids"]),
            target_unit_id=optional_identifier(body, "target_unit_id"),
        )
    except (TypeError, ValueError) as exc:
        return operator_error(exc)
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
        body = reference_workflow_body(include_exclusions=True)
        expected_revision, expected_digest = expected_precondition(body)
        payload = get_organization_planning_composition().derive_reference_workflow(
            principal=_operator_principal(
                organization_id,
                ProjectCapability.MANAGE,
            ),
            organization_id=organization_id,
            category_revision_id=category_revision_id,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            expected_policy_hash=required_text(
                body,
                "expected_policy_hash",
                maximum=128,
            ),
            workflow_key=str(workflow_key or "").strip(),
            workflow_version=positive_integer(body, "workflow_version"),
            goal=required_free_text(body, "goal", maximum=2000),
            source_category_item_ids=list(body["source_category_item_ids"]),
            exclusions=dict(body.get("exclusions") or {}),
            idempotency_key=required_idempotency_header(),
            target_unit_id=optional_identifier(body, "target_unit_id"),
        )
    except (TypeError, ValueError) as exc:
        return operator_error(exc)
    return jsonify(payload), 200 if payload.get("replayed") else 201


@organization_planning_bp.post("/api/organizations/<organization_id>/planning/<track_revision_id>/materialize")
@check_auth
@organization_boundary
def materialize_organization_planning_track(
    organization_id: str,
    track_revision_id: str,
):
    try:
        body = closed_json_body(
            {
                "expected_revision",
                "expected_digest",
                "expected_policy_hash",
                "approval_request_id",
            }
        )
        expected_revision, expected_digest = expected_precondition(body)
        payload, status_code = get_organization_planning_composition().materialize_track(
            principal=_operator_principal(
                organization_id,
                ProjectCapability.MANAGE,
            ),
            organization_id=organization_id,
            track_revision_id=track_revision_id,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
            expected_policy_hash=required_text(body, "expected_policy_hash", maximum=128),
            approval_request_id=(str(body.get("approval_request_id") or "").strip() or None),
            idempotency_key=required_idempotency_header(),
        )
    except (TypeError, ValueError) as exc:
        return operator_error(exc)
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
        body = closed_json_body({"requested_worker_id", "pump"})
        payload, status_code = get_organization_planning_composition().dispatch_next(
            principal=_operator_principal(
                organization_id,
                ProjectCapability.MANAGE,
            ),
            organization_id=organization_id,
            track_revision_id=track_revision_id,
            plan_task_id=plan_task_id,
            idempotency_key=required_idempotency_header(),
            requested_worker_id=optional_worker_hint(body.get("requested_worker_id")),
            pump=strict_bool(body.get("pump"), default=True),
        )
    except (TypeError, ValueError) as exc:
        return operator_error(exc)
    return jsonify(payload), status_code


@organization_planning_bp.post("/api/organizations/<organization_id>/planning/dispatches/<dispatch_intent_id>/retry")
@check_auth
@organization_boundary
def retry_organization_planning_dispatch(
    organization_id: str,
    dispatch_intent_id: str,
):
    try:
        body = closed_json_body({"pump"})
        payload, status_code = get_organization_planning_composition().retry_dispatch(
            principal=_operator_principal(
                organization_id,
                ProjectCapability.MANAGE,
            ),
            organization_id=organization_id,
            dispatch_intent_id=dispatch_intent_id,
            pump=strict_bool(body.get("pump"), default=True),
        )
    except (TypeError, ValueError) as exc:
        return operator_error(exc)
    return jsonify(payload), status_code


@organization_planning_bp.post("/api/organizations/<organization_id>/planning/dispatches/pump")
@check_auth
@organization_boundary
def pump_organization_planning_dispatches(organization_id: str):
    """Replay due and expired-lease outbox rows without creating new Tasks."""

    try:
        body = closed_json_body({"limit"})
        limit = positive_integer(body, "limit") if "limit" in body else 10
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
        return operator_error(exc)
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
        return worker_error("worker_task_proposals_carrier_too_large", 413)
    auth_header = str(request.headers.get("Authorization") or "")
    token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""
    if not token.startswith("wrc1."):
        return worker_error("worker_result_capability_required", 401)
    if not request.is_json:
        return worker_error("worker_task_proposals_json_required", 415)
    carrier = request.get_json(silent=True)
    if not isinstance(carrier, dict):
        return worker_error("worker_task_proposals_carrier_invalid", 400)
    if set(carrier) != {"schema", "payload_digest", "proposals"}:
        return worker_error("worker_task_proposals_carrier_invalid", 422)
    proposals = carrier.get("proposals")
    if (
        carrier.get("schema") != "worker_task_proposals.v1"
        or _SHA256_DIGEST.fullmatch(str(carrier.get("payload_digest") or "")) is None
        or not isinstance(proposals, list)
        or not 1 <= len(proposals) <= 100
        or any(not isinstance(row, dict) for row in proposals)
    ):
        return worker_error("worker_task_proposals_carrier_invalid", 422)
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
        return worker_error("worker_result_capability_invalid", 401)
    except WorkerTaskProposalIngressError as exc:
        status_code = worker_ingress_status(exc.reason_code)
        return worker_error(exc.reason_code, status_code)
    return (
        jsonify(
            {
                "schema": "worker_task_proposal_ingress_receipt.v1",
                "source_task_id": source_task_id,
                "assignment_id": assignment_id,
                "proposals": [normalize_worker_ingress(row) for row in results],
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
        return worker_error("category_research_result_too_large", 413)
    auth_header = str(request.headers.get("Authorization") or "")
    token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""
    if not token.startswith("wrc1."):
        return worker_error("worker_result_capability_required", 401)
    if not request.is_json:
        return worker_error("category_research_result_json_required", 415)
    carrier = request.get_json(silent=True)
    if not isinstance(carrier, dict) or set(carrier) != {
        "schema",
        "payload_digest",
        "raw_output",
        "runtime_artifact_hashes",
    }:
        return worker_error("category_research_result_carrier_invalid", 422)
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
        return worker_error("category_research_result_carrier_invalid", 422)
    if "sha256:" + hashlib.sha256(raw_output.encode("utf-8")).hexdigest() != digest:
        return worker_error("category_research_result_digest_mismatch", 422)
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
            idempotency_key=required_idempotency_header(worker=True),
            runtime_artifact_hashes=artifact_hashes,
        )
    except WorkerResultCapabilityError:
        return worker_error("worker_result_capability_invalid", 401)
    except (TypeError, ValueError) as exc:
        reason_code = str(getattr(exc, "reason_code", "") or str(exc) or "category_research_result_invalid")
        return worker_error(
            reason_code,
            worker_ingress_status(reason_code),
            details=error_details(exc),
        )
    return jsonify(payload), 200 if payload.get("replayed") else 201


@organization_planning_bp.post("/api/worker-results/tasks/<source_task_id>/assignments/<assignment_id>/planning/tracks")
def ingest_assignment_bound_track_planning(
    source_task_id: str,
    assignment_id: str,
):
    """Admit a closed Track candidate carrier; never materialize its tasks."""

    if request.content_length is not None and request.content_length > _MAX_WORKER_PROPOSAL_CARRIER_BYTES:
        return worker_error("track_planning_result_too_large", 413)
    auth_header = str(request.headers.get("Authorization") or "")
    token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""
    if not token.startswith("wrc1."):
        return worker_error("worker_result_capability_required", 401)
    if not request.is_json:
        return worker_error("track_planning_result_json_required", 415)
    raw_carrier = request.get_json(silent=True)
    if not isinstance(raw_carrier, dict):
        return worker_error("track_planning_result_carrier_invalid", 400)
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
            idempotency_key=required_idempotency_header(worker=True),
        )
    except WorkerResultCapabilityError:
        return worker_error("worker_result_capability_invalid", 401)
    except (TypeError, ValueError) as exc:
        reason_code = str(getattr(exc, "reason_code", "") or str(exc) or "track_planning_result_invalid")
        return worker_error(
            reason_code,
            worker_ingress_status(reason_code),
            details=error_details(exc),
        )
    return jsonify(payload), 200 if payload.get("replayed") else 201


def _transition_artifact(
    *,
    organization_id: str,
    artifact_revision_id: str,
    operation: str,
):
    try:
        body = closed_json_body(
            {
                "expected_revision",
                "expected_digest",
                "approval_request_id",
                "approval_id",
                "idempotency_key",
            }
        )
        expected_revision, expected_digest = expected_precondition(body)
        principal = _operator_principal(
            organization_id,
            ProjectCapability.MANAGE,
        )
        idempotency_key = resolve_idempotency_key(
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
        return operator_error(exc)
    return jsonify(payload), status_code


def _decide_proposal(*, organization_id: str, proposal_id: str, operation: str):
    try:
        body = closed_json_body({"expected_revision", "expected_digest"})
        expected_revision, expected_digest = expected_precondition(body)
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
        return operator_error(exc)
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


__all__ = ["organization_planning_bp"]
