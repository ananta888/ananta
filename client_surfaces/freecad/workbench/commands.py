from __future__ import annotations

from typing import Any

from client_surfaces.freecad.workbench.client import FreecadHubClient
from client_surfaces.freecad.workbench.context_preview import build_context_preview


GOAL_CAPABILITY_ID = "freecad.model.inspect"


def submit_freecad_goal(client: FreecadHubClient, *, goal: str, context: dict[str, Any]) -> dict[str, Any]:
    preview = build_context_preview(context)
    if preview["oversize"]:
        return {"status": "degraded", "reason": "context_oversize", "preview": preview}
    response = client.submit_goal(goal=goal, context=context, capability_id=GOAL_CAPABILITY_ID)
    return {"status": str(response.get("status") or "degraded"), "preview": preview, "response": response}
