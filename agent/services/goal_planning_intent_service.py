from __future__ import annotations

from typing import Any


class GoalPlanningIntentService:
    _SOFTWARE_HINTS = ("project", "software", "backend", "frontend", "api", "service", "app", "repo")
    _RESEARCH_HINTS = ("research", "analyse", "analyze", "investigate", "compare")
    _OPS_HINTS = ("deploy", "infra", "docker", "kubernetes", "ops", "pipeline", "ci")

    def classify(self, goal_text: str, mode: str = "generic") -> dict[str, Any]:
        text = str(goal_text or "").strip().lower()
        if mode == "new_software_project" or any(h in text for h in self._SOFTWARE_HINTS):
            return {"goal_type": "software_project", "planning_intent": "build_and_verify", "confidence": "medium"}
        if any(h in text for h in self._RESEARCH_HINTS):
            return {"goal_type": "research", "planning_intent": "investigation", "confidence": "medium"}
        if any(h in text for h in self._OPS_HINTS):
            return {"goal_type": "operations", "planning_intent": "runbook_execution", "confidence": "medium"}
        return {"goal_type": "generic", "planning_intent": "decompose", "confidence": "low"}


_SERVICE = GoalPlanningIntentService()


def get_goal_planning_intent_service() -> GoalPlanningIntentService:
    return _SERVICE
