"""Fail-closed error and Worker-ingress response mapping for Organization planning routes."""

from __future__ import annotations

from typing import Any

from flask import jsonify

from agent.services.approval_request_service import ApprovalDecisionError
from agent.services.organization_planning_composition import (
    OrganizationPlanningCompositionError,
)
from agent.services.planning_artifact_transition_service import PlanningTransitionError
from agent.services.worker_task_proposal_ingress_service import (
    WorkerTaskProposalIngressError,
)


def operator_error(exc: BaseException):
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
        details = error_details(exc)
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


def worker_ingress_status(reason_code: str) -> int:
    if "not_found" in reason_code:
        return 404
    if "idempotency_conflict" in reason_code or "lease" in reason_code or "assignment" in reason_code:
        return 409
    if "credential" in reason_code or "worker_mismatch" in reason_code:
        return 403
    return 422


def error_details(exc: BaseException) -> dict[str, Any]:
    raw = getattr(exc, "details", None)
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, (list, tuple)):
        return {"issues": [str(value) for value in raw]}
    return {}


def worker_error(
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


def normalize_worker_ingress(row: dict[str, Any]) -> dict[str, Any]:
    proposal_revision = int(row.get("proposal_revision") or 0)
    proposal_digest = str(row.get("proposal_digest") or "")
    return {
        **dict(row),
        "revision": str(proposal_revision),
        "digest": proposal_digest,
        "status": "pending" if row.get("state") == "submitted" else row.get("state"),
    }


__all__ = [
    "error_details",
    "normalize_worker_ingress",
    "operator_error",
    "worker_error",
    "worker_ingress_status",
]
