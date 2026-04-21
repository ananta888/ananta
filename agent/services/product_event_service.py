from __future__ import annotations

from typing import Any

from agent.common.audit import log_audit
from agent.services.hub_event_service import build_product_event


def record_product_event(
    event_type: str,
    *,
    actor: str = "system",
    details: dict[str, Any] | None = None,
    goal_id: str | None = None,
    trace_id: str | None = None,
    plan_id: str | None = None,
) -> dict[str, Any]:
    event = build_product_event(
        event_type,
        actor=actor,
        details=details,
        goal_id=goal_id,
        trace_id=trace_id,
        plan_id=plan_id,
    )
    log_audit(
        f"product_{event_type}",
        {
            "goal_id": goal_id,
            "trace_id": trace_id,
            "plan_id": plan_id,
            "product_event": event,
        },
    )
    return event
