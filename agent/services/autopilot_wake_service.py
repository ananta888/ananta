from __future__ import annotations

from typing import Any

from flask import has_app_context

from agent.services.service_registry import get_core_services


def request_autopilot_wake(event_type: str, **details: Any) -> None:
    """Best-effort event wakeup for the hub autopilot loop."""
    if not has_app_context():
        return
    try:
        services = get_core_services()
        services.autopilot_runtime_service._loop().wake()
        task_id = str(details.get("task_id") or "").strip()
        if task_id:
            services.autopilot_support_service.append_trace_event(
                task_id,
                f"autopilot_wake_{event_type}",
                **details,
            )
    except Exception:
        pass
