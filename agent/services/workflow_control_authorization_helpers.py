"""Pure binding checks shared by workflow-control infrastructure bridges."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.workflow_authorization_grant_service import (
    WorkflowAuthorizationGrantPort,
)
from agent.services.workflow_backend import WorkflowRequest
from agent.services.workflow_control_service import WorkflowPrincipal
from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from agent.services.workflow_runtime.security import RuntimeAuthorizationEnvelope

ROUTE_CONTROL_AUTHORIZATION_SCHEMA = "ananta.workflow_route_control.v1"


def assert_route_control_envelope(
    envelope: Mapping[str, Any],
    *,
    principal: WorkflowPrincipal,
    workflow_id: str,
    run_id: str,
) -> None:
    expected = {
        "schema": ROUTE_CONTROL_AUTHORIZATION_SCHEMA,
        "tenant_id": principal.tenant_id,
        "subject_id": principal.subject_id,
        "workflow_id": workflow_id,
        "run_id": run_id,
    }
    if any(str(envelope.get(key) or "") != value for key, value in expected.items()):
        raise PermissionError("workflow_control_authorization_binding_mismatch")


def register_bound_authorization_grants(
    grants: WorkflowAuthorizationGrantPort | None,
    *,
    request: WorkflowRequest,
    plan: ExecutionPlan,
) -> None:
    if grants is None:
        return
    by_step = request.metadata.get("authorization_envelopes")
    envelope_by_step = dict(by_step) if isinstance(by_step, dict) else {}
    for step in request.steps:
        raw = step.metadata.get("authorization_envelope") or envelope_by_step.get(
            step.step_id
        )
        if not isinstance(raw, dict):
            continue
        envelope = RuntimeAuthorizationEnvelope.from_mapping(raw)
        if (
            envelope.tenant_id != plan.tenant_id
            or envelope.workflow_id != plan.workflow_id
            or envelope.run_id
            != str(request.metadata.get("run_id") or request.workflow_id)
            or envelope.step_id != step.step_id
            or envelope.plan_hash != plan.plan_hash
            or envelope.policy_version != plan.policy_version
        ):
            raise PermissionError("workflow_authorization_grant_binding_mismatch")
        grants.grant(envelope)


__all__ = [
    "ROUTE_CONTROL_AUTHORIZATION_SCHEMA",
    "assert_route_control_envelope",
    "register_bound_authorization_grants",
]
