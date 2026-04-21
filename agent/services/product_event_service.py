from __future__ import annotations

from typing import Any

from flask import current_app, has_app_context

from agent.common.audit import log_audit
from agent.governance_modes import resolve_governance_mode
from agent.services.hub_event_service import build_product_event
from agent.runtime_profiles import resolve_runtime_profile


def _classify_usage_context(source: str, mode: str, runtime_profile: str) -> str:
    if source == "demo" or mode in {"guided-first-run", "demo"} or runtime_profile == "demo":
        return "demo"
    if runtime_profile in {"team-controlled", "secure-enterprise", "distributed-strict", "review-first"} or source == "api":
        return "production"
    return "trial"


def record_product_event(
    event_type: str,
    *,
    actor: str = "system",
    details: dict[str, Any] | None = None,
    goal_id: str | None = None,
    trace_id: str | None = None,
    plan_id: str | None = None,
) -> dict[str, Any]:
    enriched_details = dict(details or {})
    if has_app_context():
        cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
        runtime_profile = resolve_runtime_profile(cfg).get("effective")
        governance_mode = resolve_governance_mode(cfg).get("effective")
        enriched_details.setdefault("runtime_profile", runtime_profile)
        enriched_details.setdefault("governance_mode", governance_mode)
        enriched_details.setdefault(
            "usage_context",
            _classify_usage_context(
                str(enriched_details.get("source") or "").strip().lower(),
                str(enriched_details.get("mode") or "").strip().lower(),
                str(runtime_profile or "").strip().lower(),
            ),
        )
    event = build_product_event(
        event_type,
        actor=actor,
        details=enriched_details,
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
