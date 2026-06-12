"""CCRDS-013: wire cross-domain write violations into the approval lifecycle.

Strict mode keeps blocking outright. In approval mode this module talks to
the existing ``ApprovalRequestService`` (ALWA lifecycle):

  1. An existing digest-bound grant for exactly this tool call turns the
     violation into ``allow`` (``approval_granted_by_request``).
  2. Otherwise a pending ``ApprovalRequest`` is created, bound to the
     concrete ``requested_path`` + arguments digest — never to the tool in
     general — and the decision stays ``approval_required``.

The decision payload carries ``approval_request_id``/``arguments_digest``
so callers (worker, routes) can surface and later resolve the request.
"""
from __future__ import annotations

import logging
from typing import Any

from agent.codecompass.domain_scope import (
    DECISION_ALLOW,
    DECISION_APPROVAL_REQUIRED,
    DomainScopeDecision,
    DomainScopeViolation,
    WRITE_ENFORCEMENT_MODE_STRICT,
    decide_cross_domain_write,
)

APPROVAL_CLASS_CROSS_DOMAIN_WRITE = "cross_domain_write"
DEFAULT_TOOL_NAME = "workspace_write"

logger = logging.getLogger(__name__)


def _call_arguments(violation: DomainScopeViolation, arguments: dict[str, Any] | None) -> dict[str, Any]:
    # requested_path is part of the canonicalized arguments, so the digest
    # (and any grant) is bound to this concrete path.
    return {"requested_path": violation.requested_path, **dict(arguments or {})}


def request_cross_domain_write_approval(
    violation: DomainScopeViolation,
    *,
    mode: str = WRITE_ENFORCEMENT_MODE_STRICT,
    tool_name: str = DEFAULT_TOOL_NAME,
    arguments: dict[str, Any] | None = None,
    task_id: str | None = None,
    goal_id: str | None = None,
    governance_mode: str = "balanced",
    agent_cfg: dict[str, Any] | None = None,
) -> tuple[DomainScopeDecision, dict[str, Any]]:
    """Resolve a cross-domain write violation against the approval lifecycle.

    Returns ``(decision, details)``. ``details`` contains the approval
    request id / digest prefix when a request was found or created, and is
    content-free (no file contents, only path + digest).
    """
    decision = decide_cross_domain_write(violation, mode=mode)
    if decision.decision != DECISION_APPROVAL_REQUIRED:
        return decision, {}

    call_args = _call_arguments(violation, arguments)
    try:
        from agent.services.approval_request_service import (
            digest_prefix,
            get_approval_request_service,
        )

        svc = get_approval_request_service()
        grant = svc.resolve_grant_for_call(
            tool_name=tool_name,
            arguments=call_args,
            task_id=task_id,
            goal_id=goal_id,
            target_fingerprint=None,
        )
        if grant is not None:
            return (
                DomainScopeDecision(
                    decision=DECISION_ALLOW,
                    reason="approval_granted_by_request",
                    violation=violation,
                ),
                {
                    "approval_request_id": grant.id,
                    "digest_prefix": digest_prefix(grant.arguments_digest),
                    "status": grant.status,
                },
            )

        request = svc.create_pending_request(
            task_id=task_id,
            goal_id=goal_id,
            tool_name=tool_name,
            arguments=call_args,
            risk_class="high",
            governance_mode=governance_mode,
            scope={
                "approval_class": APPROVAL_CLASS_CROSS_DOMAIN_WRITE,
                "requested_path": violation.requested_path,
                "matched_domain": violation.matched_domain,
                "allowed_paths": list(violation.allowed_paths),
            },
            agent_cfg=agent_cfg,
        )
        details = {
            "approval_request_id": request.id,
            "digest_prefix": digest_prefix(request.arguments_digest),
            "status": request.status,
        }
        if request.status == "granted":
            # Auto-approval policy may grant immediately; cross_domain_write
            # is not in the auto classes by default, but respect the result.
            return (
                DomainScopeDecision(
                    decision=DECISION_ALLOW,
                    reason="approval_granted_by_request",
                    violation=violation,
                ),
                details,
            )
        return decision, details
    except Exception as exc:
        # Fail closed: without a reachable approval service the write stays
        # approval_required/blocked, never silently allowed.
        logger.warning("cross_domain_write approval lifecycle unavailable: %s", exc)
        return decision, {"error": str(exc)[:160]}
