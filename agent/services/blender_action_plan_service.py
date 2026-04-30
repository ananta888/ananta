from __future__ import annotations

from typing import Any


def build_blender_action_plan(*, capability: str, operations: list[dict[str, Any]], provenance: dict[str, Any]) -> dict[str, Any]:
    return {
        "plan_id": "blend-plan-1",
        "capability": capability,
        "operations": list(operations or []),
        "approval_state": "required" if capability.startswith("blender.") and "read" not in capability else "not_required",
        "provenance": dict(provenance or {}),
    }
