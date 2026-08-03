"""Scoped HTTP read model and Hub-mediated cross-team handoff transitions."""

from __future__ import annotations

import re
from collections.abc import Mapping

from flask import Blueprint, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.routes.organization_route_support import (
    OrganizationRouteError,
    organization_boundary,
    organization_catalog,
    request_payload,
    require_idempotency_key,
    require_if_match_header,
    require_organization_scope,
)
from agent.services.organization_runtime_application_service import (
    OrganizationRuntimeApplicationService,
)
from agent.services.project_access_authority import ProjectCapability
from agent.services.separation_of_duties_service import SeparationOfDutiesPolicy
from agent.services.team_handoff_service import (
    TeamHandoffAcceptanceCheck,
    TeamHandoffArtifactRef,
    TeamHandoffContract,
)

organization_runtime_bp = Blueprint("organization_runtime", __name__)
_REASON_CODE = re.compile(r"^[a-z][a-z0-9_.:-]{0,190}$")


@organization_runtime_bp.get("/api/organizations/<organization_id>/runtime")
@check_auth
@organization_boundary
def get_organization_runtime(organization_id: str):
    unknown = sorted(set(request.args) - {"event_limit"})
    if unknown:
        raise OrganizationRouteError(
            "organization_query_fields_invalid",
            status_code=400,
            details={"unknown_fields": unknown},
        )
    scope = require_organization_scope(organization_id)
    try:
        event_limit = int(str(request.args.get("event_limit") or "500"))
    except ValueError as exc:
        raise OrganizationRouteError(
            "organization_runtime_event_limit_invalid",
            status_code=400,
        ) from exc
    return api_response(data=_runtime_service(scope).read_runtime(event_limit=event_limit))


@organization_runtime_bp.post("/api/organizations/<organization_id>/handoffs")
@check_auth
@organization_boundary
def submit_organization_handoff(organization_id: str):
    scope = require_organization_scope(organization_id, ProjectCapability.MANAGE)
    payload = request_payload(
        allowed_fields={
            "handoff_id",
            "handoff_definition_ref",
            "correlation_id",
            "goal_id",
            "producer",
            "consumer",
            "artifact_refs",
            "grounding",
            "acceptance_checks",
            "due_at",
            "sla_seconds",
            "assignment_id",
            "dispatch_lease_id",
        }
    )
    idempotency_key = require_idempotency_key()
    contract = _handoff_contract(organization_id=organization_id, payload=payload)
    runtime = _runtime_service(scope)
    assignment_id = _required_string(payload, "assignment_id")
    dispatch_lease_id = _required_string(payload, "dispatch_lease_id")
    decision = runtime.submit_handoff(
        contract=contract,
        assignment_id=assignment_id,
        dispatch_lease_id=dispatch_lease_id,
        idempotency_key=idempotency_key,
    )
    if decision.status == "pending_acceptance" or decision.replayed:
        runtime.emit_event(
            event_type="handoff_submitted",
            correlation_id=contract.correlation_id,
            idempotency_key=f"handoff-submit:{idempotency_key}",
            payload={
                "handoff_id": contract.handoff_id,
                "producer_team_id": contract.producer_team_id,
                "consumer_team_id": contract.consumer_team_id,
                "producer_task_id": contract.producer_task_id,
                "consumer_task_id": contract.consumer_task_id,
            },
        )
    if decision.replayed:
        status_code = 200
    elif decision.status == "pending_acceptance":
        status_code = 201
    else:
        status_code = 422
    if decision.status == "conflict":
        status_code = 409
    return api_response(data=_decision_payload(decision), code=status_code)


@organization_runtime_bp.post("/api/organizations/<organization_id>/handoffs/<handoff_id>/decisions")
@check_auth
@organization_boundary
def decide_organization_handoff(organization_id: str, handoff_id: str):
    scope = require_organization_scope(organization_id, ProjectCapability.MANAGE)
    payload = request_payload(
        allowed_fields={
            "decision",
            "reason_code",
            "decision_assignment_id",
        }
    )
    raw_revision = require_if_match_header()
    if not raw_revision.isdigit() or int(raw_revision) < 1:
        raise OrganizationRouteError(
            "organization_if_match_invalid",
            status_code=400,
        )
    decision_value = _required_string(payload, "decision").lower()
    reason_code = _required_string(payload, "reason_code")
    if _REASON_CODE.fullmatch(reason_code) is None:
        raise OrganizationRouteError(
            "handoff_structured_reason_required",
            status_code=400,
        )
    decision_assignment_id = _required_string(payload, "decision_assignment_id")
    idempotency_key = require_idempotency_key()
    runtime = _runtime_service(scope)
    if decision_value == "accepted":
        runtime.validate_handoff_acceptance_binding(handoff_id=handoff_id)
    assignments = runtime.handoff_decision_assignments(
        handoff_id=handoff_id,
        decision_assignment_id=decision_assignment_id,
        actor_principal_id=scope.principal.subject_id,
    )
    decision = runtime.handoff_service().decide(
        handoff_id=handoff_id,
        decision=decision_value,
        reason_code=reason_code,
        actor_principal_id=scope.principal.subject_id,
        expected_revision=int(raw_revision),
        idempotency_key=idempotency_key,
        duty_assignments=assignments,
        sod_policy=SeparationOfDutiesPolicy.enterprise_default(),
    )
    if decision.status in {"accepted", "rejected", "needs_changes"}:
        current = runtime.handoff_store.get(handoff_id) or {}
        contract = dict(current.get("contract") or {})
        runtime.emit_event(
            event_type=f"handoff_{decision.status}",
            correlation_id=str(contract.get("correlation_id") or handoff_id),
            idempotency_key=f"handoff-decision:{idempotency_key}",
            payload={
                "handoff_id": handoff_id,
                "producer_team_id": contract.get("producer_team_id"),
                "consumer_team_id": contract.get("consumer_team_id"),
                "producer_task_id": contract.get("producer_task_id"),
                "consumer_task_id": contract.get("consumer_task_id"),
                "reason_code": decision.reason_code,
            },
        )
    status_code = 200
    if decision.status == "conflict":
        status_code = 409
    elif decision.status == "blocked":
        status_code = 422
    return api_response(data=_decision_payload(decision), code=status_code)


def _runtime_service(scope) -> OrganizationRuntimeApplicationService:
    return OrganizationRuntimeApplicationService(
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
        organization_id=scope.organization_id,
        catalog=organization_catalog(),
    )


def _handoff_contract(
    *,
    organization_id: str,
    payload: Mapping[str, object],
) -> TeamHandoffContract:
    producer = _endpoint(payload.get("producer"), label="producer")
    consumer = _endpoint(payload.get("consumer"), label="consumer")
    raw_artifacts = payload.get("artifact_refs")
    if not isinstance(raw_artifacts, list) or not 1 <= len(raw_artifacts) <= 100:
        raise OrganizationRouteError("handoff_artifact_refs_empty", status_code=400)
    artifacts: list[TeamHandoffArtifactRef] = []
    for item in raw_artifacts:
        if not isinstance(item, Mapping):
            raise OrganizationRouteError(
                "handoff_artifact_ref_invalid",
                status_code=400,
            )
        legacy_fields = {"artifact_id", "version", "digest"}
        canonical_fields = {
            "artifact_id",
            "artifact_kind",
            "artifact_version_ref",
            "version",
            "content_digest",
            "verification_status",
        }
        item_fields = frozenset(item)
        if item_fields not in {frozenset(legacy_fields), frozenset(canonical_fields)}:
            raise OrganizationRouteError(
                "handoff_artifact_ref_invalid",
                status_code=400,
            )
        canonical = item_fields == frozenset(canonical_fields)
        artifacts.append(
            TeamHandoffArtifactRef(
                artifact_id=_required_string(item, "artifact_id"),
                version=_required_string(item, "version"),
                digest=_required_string(
                    item,
                    "content_digest" if canonical else "digest",
                ),
                artifact_kind=(_required_string(item, "artifact_kind") if canonical else "artifact"),
                artifact_version_ref=(_required_string(item, "artifact_version_ref") if canonical else ""),
                verification_status=(_required_string(item, "verification_status") if canonical else "hub_verified"),
            )
        )
    raw_checks = payload.get("acceptance_checks")
    if not isinstance(raw_checks, list) or not 1 <= len(raw_checks) <= 100:
        raise OrganizationRouteError(
            "handoff_acceptance_checks_empty",
            status_code=400,
        )
    checks: list[str | TeamHandoffAcceptanceCheck] = []
    for value in raw_checks:
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized or len(normalized) > 500:
                raise OrganizationRouteError(
                    "handoff_acceptance_check_invalid",
                    status_code=400,
                )
            checks.append(normalized)
            continue
        if not isinstance(value, Mapping) or set(value) != {
            "check_id",
            "check_kind",
            "expected",
            "status",
        }:
            raise OrganizationRouteError(
                "handoff_acceptance_check_invalid",
                status_code=400,
            )
        checks.append(
            TeamHandoffAcceptanceCheck(
                check_id=_required_string(value, "check_id"),
                check_kind=_required_string(value, "check_kind"),
                expected=_required_string(value, "expected", max_length=500),
                status=_required_string(value, "status"),
            )
        )
    grounding = _grounding(payload.get("grounding"))
    try:
        sla_seconds = int(payload.get("sla_seconds") or 0)
    except (TypeError, ValueError) as exc:
        raise OrganizationRouteError("handoff_sla_invalid", status_code=400) from exc
    return TeamHandoffContract(
        handoff_id=_required_string(payload, "handoff_id"),
        correlation_id=_required_string(payload, "correlation_id"),
        organization_id=organization_id,
        goal_id=_required_string(payload, "goal_id"),
        producer_unit_id=producer["unit_id"],
        producer_team_id=producer["team_id"],
        producer_role_slot_id=producer["role_slot_id"],
        producer_task_id=producer["task_id"],
        consumer_unit_id=consumer["unit_id"],
        consumer_team_id=consumer["team_id"],
        consumer_role_slot_id=consumer["role_slot_id"],
        consumer_task_id=consumer["task_id"],
        artifact_refs=tuple(artifacts),
        acceptance_checks=tuple(checks),
        due_at=_required_string(payload, "due_at"),
        sla_seconds=sla_seconds,
        handoff_definition_ref=_required_string(
            payload,
            "handoff_definition_ref",
        ),
        grounding_policy_ref=grounding["grounding_policy_ref"],
        allowed_source_refs=grounding["allowed_source_refs"],
        allowed_run_refs=grounding["allowed_run_refs"],
        evidence_refs=grounding["evidence_refs"],
        grounding_verification_status=grounding["verification_status"],
    )


def _endpoint(value: object, *, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "unit_id",
        "team_id",
        "role_slot_id",
        "task_id",
    }:
        raise OrganizationRouteError(
            f"handoff_{label}_binding_invalid",
            status_code=400,
        )
    return {key: _required_string(value, key) for key in ("unit_id", "team_id", "role_slot_id", "task_id")}


def _grounding(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {
        "grounding_policy_ref",
        "allowed_source_refs",
        "allowed_run_refs",
        "evidence_refs",
        "verification_status",
    }:
        raise OrganizationRouteError(
            "handoff_grounding_binding_invalid",
            status_code=400,
        )
    result: dict[str, object] = {
        "grounding_policy_ref": _required_string(value, "grounding_policy_ref"),
        "verification_status": _required_string(value, "verification_status"),
    }
    for key in ("allowed_source_refs", "allowed_run_refs", "evidence_refs"):
        raw = value.get(key)
        minimum = 1 if key == "evidence_refs" else 0
        if not isinstance(raw, list) or not minimum <= len(raw) <= 500:
            raise OrganizationRouteError(
                "handoff_grounding_binding_invalid",
                status_code=400,
            )
        normalized = tuple(str(item).strip() for item in raw)
        if any(not item or len(item) > 240 for item in normalized):
            raise OrganizationRouteError(
                "handoff_grounding_binding_invalid",
                status_code=400,
            )
        result[key] = normalized
    return result


def _required_string(
    value: Mapping[str, object],
    key: str,
    *,
    max_length: int = 191,
) -> str:
    normalized = str(value.get(key) or "").strip()
    if not normalized or len(normalized) > max_length:
        raise OrganizationRouteError(
            f"organization_{key}_invalid",
            status_code=400,
        )
    return normalized


def _decision_payload(decision) -> dict[str, object]:
    return {
        "handoff_id": decision.handoff_id,
        "status": decision.status,
        "reason_code": decision.reason_code,
        "revision": decision.revision,
        "event_id": decision.event_id,
        "artifact_digests": list(decision.artifact_digests),
        "replayed": decision.replayed,
    }


__all__ = ["organization_runtime_bp"]
