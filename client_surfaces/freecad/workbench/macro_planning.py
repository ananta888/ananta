from __future__ import annotations

from typing import Any


def format_macro_plan(plan_response: dict[str, Any]) -> dict[str, Any]:
    plan = dict(plan_response.get("plan") or {})
    return {
        "mode": str(plan.get("mode") or "dry_run"),
        "trusted": bool(plan.get("trusted")),
        "objective": str(plan.get("objective") or ""),
        "macro_outline": [str(item) for item in list(plan.get("macro_outline") or [])],
        "safety_notes": [str(item) for item in list(plan.get("safety_notes") or [])],
    }
