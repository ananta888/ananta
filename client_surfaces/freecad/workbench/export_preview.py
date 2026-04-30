from __future__ import annotations

from typing import Any


def format_export_plan(plan_response: dict[str, Any]) -> dict[str, Any]:
    plan = dict(plan_response.get("plan") or {})
    return {
        "format": str(plan.get("format") or ""),
        "target_path": str(plan.get("target_path") or ""),
        "selection_only": bool(plan.get("selection_only")),
        "approval_required": bool(plan.get("approval_required", True)),
        "execution_mode": str(plan.get("execution_mode") or "plan_only"),
    }
