"""CaseFlow Policies — human approval gates for critical actions."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

from agent.caseflow.actions import CRITICAL_ACTIONS, ApprovalRequest

REQUIRE_APPROVAL_ACTIONS = set(CRITICAL_ACTIONS)


@dataclass
class PolicyCheckResult:
    allowed: bool
    requires_approval: bool = False
    error_code: Optional[str] = None
    detail: Optional[str] = None


def check_policy(action: str, context: dict[str, Any] | None = None) -> PolicyCheckResult:
    """Check whether an action is allowed under the current policy."""
    context = context or {}
    if action in REQUIRE_APPROVAL_ACTIONS:
        approved_by = context.get("approved_by")
        if not approved_by:
            return PolicyCheckResult(
                allowed=False,
                requires_approval=True,
                error_code="APPROVAL_REQUIRED",
                detail=f"Action '{action}' requires human approval.",
            )
    return PolicyCheckResult(allowed=True)


def require_human_approval(
    action: str,
    actor: str,
    payload: dict[str, Any] | None = None,
) -> ApprovalRequest:
    """Create an ApprovalRequest for a critical action."""
    payload = payload or {}
    if action not in REQUIRE_APPROVAL_ACTIONS:
        raise ValueError(f"Unknown critical action: {action}")
    return ApprovalRequest(
        case_id=payload.get("case_id", ""),
        critical_action=action,
        requested_by=actor,
        payload_hash=_hash_payload(payload),
    )


def _hash_payload(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
