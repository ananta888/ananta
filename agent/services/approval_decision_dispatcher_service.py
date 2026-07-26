"""Hub-side post-decision dispatch for approval-domain extensions."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class ApprovalDecisionDispatcherService:
    """Route an approval decision to one narrowly scoped domain handler.

    Generic tool-call approvals retain their existing behavior.  Recovery-plan
    materialization is an additive handler and remains owned by the Hub.
    """

    def dispatch(self, approval: Any) -> dict[str, Any]:
        from agent.services.task_recovery_planning_service import (
            RECOVERY_MATERIALIZE_TOOL,
            get_task_recovery_planning_service,
        )

        if str(getattr(approval, "tool_name", "") or "") != RECOVERY_MATERIALIZE_TOOL:
            return {"status": "ignored", "reason_code": "approval_tool_not_handled"}
        try:
            return get_task_recovery_planning_service().handle_approval_decision(
                approval
            )
        except Exception as exc:
            log.exception(
                "approval decision handler failed for request %s",
                getattr(approval, "id", None),
            )
            return {
                "status": "failed",
                "reason_code": "approval_decision_handler_failed",
                "error_type": type(exc).__name__,
            }


_service = ApprovalDecisionDispatcherService()


def get_approval_decision_dispatcher_service() -> ApprovalDecisionDispatcherService:
    return _service
