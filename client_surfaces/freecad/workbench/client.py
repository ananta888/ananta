from __future__ import annotations

from collections.abc import Callable
from typing import Any

from client_surfaces.freecad.workbench.settings import FreecadWorkbenchSettings

Transport = Callable[[str, dict[str, Any]], dict[str, Any]]


class FreecadHubClient:
    def __init__(self, settings: FreecadWorkbenchSettings, transport: Transport | None = None) -> None:
        self.settings = settings
        self._transport = transport or self._default_transport

    def configuration_state(self) -> dict[str, Any]:
        problems = self.settings.validate()
        status = "ready" if not problems else "degraded"
        return {
            "status": status,
            "problems": problems,
            "settings": self.settings.to_redacted_dict(),
        }

    def health(self) -> dict[str, Any]:
        return self._transport("health", {"endpoint": self.settings.endpoint, "profile": self.settings.profile})

    def capabilities(self) -> dict[str, Any]:
        return self._transport("capabilities", {"profile": self.settings.profile})

    def submit_goal(self, *, goal: str, context: dict[str, Any], capability_id: str) -> dict[str, Any]:
        return self._transport(
            "submit_goal",
            {
                "goal": str(goal or "").strip(),
                "context": dict(context or {}),
                "capability_id": str(capability_id or "").strip(),
            },
        )

    def submit_approval_decision(self, *, approval_id: str, decision: str) -> dict[str, Any]:
        return self._transport(
            "approval_decision",
            {"approval_id": str(approval_id or "").strip(), "decision": str(decision or "").strip()},
        )

    def request_export_plan(self, *, fmt: str, target_path: str, selection_only: bool = False) -> dict[str, Any]:
        return self._transport(
            "export_plan",
            {"format": fmt, "target_path": target_path, "selection_only": bool(selection_only)},
        )

    def request_macro_plan(self, *, objective: str, context_summary: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._transport(
            "macro_plan",
            {"objective": objective, "context_summary": dict(context_summary or {})},
        )

    def execute_macro(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return self._transport("macro_execute", dict(envelope or {}))

    def _default_transport(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action == "health":
            status = "connected" if self.settings.endpoint and not self.settings.validate() else "degraded"
            return {
                "status": status,
                "endpoint": self.settings.endpoint,
                "auth": "configured" if self.settings.token else "missing",
            }
        if action == "capabilities":
            return {
                "status": "ok" if self.settings.endpoint else "degraded",
                "capabilities": [
                    "freecad.document.read",
                    "freecad.model.inspect",
                    "freecad.export.plan",
                    "freecad.macro.plan",
                    "freecad.macro.execute",
                ],
            }
        if action == "submit_goal":
            goal = str(payload.get("goal") or "").strip()
            if not goal:
                return {"status": "degraded", "reason": "goal_missing"}
            return {"status": "accepted", "goal": goal, "capability_id": payload.get("capability_id"), "task_id": "fc-task-1"}
        if action == "approval_decision":
            approval_id = str(payload.get("approval_id") or "").strip()
            decision = str(payload.get("decision") or "").strip().lower()
            if not approval_id or decision not in {"approve", "reject"}:
                return {"status": "degraded", "reason": "invalid_approval_decision"}
            return {"status": "accepted", "approval_id": approval_id, "decision": decision}
        if action == "export_plan":
            return {
                "status": "accepted",
                "plan": {
                    "format": str(payload.get("format") or "STEP").upper(),
                    "target_path": str(payload.get("target_path") or ""),
                    "selection_only": bool(payload.get("selection_only")),
                    "approval_required": True,
                    "execution_mode": "plan_only",
                },
            }
        if action == "macro_plan":
            objective = str(payload.get("objective") or "").strip()
            return {
                "status": "accepted" if objective else "degraded",
                "plan": {
                    "mode": "dry_run",
                    "trusted": False,
                    "objective": objective,
                    "macro_outline": ["Inspect selection", "Validate references", "Prepare bounded macro"],
                    "safety_notes": ["approval required before execution", "hub remains execution owner"],
                    "context_summary": dict(payload.get("context_summary") or {}),
                },
            }
        if action == "macro_execute":
            if not payload.get("approval_id"):
                return {"status": "blocked", "reason": "approval_required"}
            return {"status": "accepted", "execution": dict(payload)}
        return {"status": "degraded", "reason": f"unsupported_action:{action}"}
