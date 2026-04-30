from __future__ import annotations


def submit_blender_goal(goal: str, context: dict, capability: str) -> dict:
    return {
        "status": "accepted" if goal.strip() else "degraded",
        "goal": goal.strip(),
        "capability": capability,
        "context": context,
    }
