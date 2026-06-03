from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PlanningOrchestrationDecision:
    strategy_order: list[str]
    rationale: str


class LLMFirstPlanningOrchestratorService:
    def decide_strategy_order(self, *, mode: str, use_template: bool, use_repo_context: bool, planning_policy: dict[str, Any] | None = None) -> PlanningOrchestrationDecision:
        policy = dict(planning_policy or {})
        llm_first = bool(policy.get("llm_first", False))
        allow_template = bool(use_template)

        if mode == "new_software_project":
            llm_first = True

        if mode != "new_software_project" and allow_template:
            order = ["template", "llm", "hub_copilot"]
            rationale = "template_priority_policy"
        elif llm_first:
            order = ["llm", "hub_copilot"]
            if allow_template:
                order.append("template")
            rationale = "llm_first_policy"
        else:
            order = ["template"] if allow_template else []
            order.extend(["llm", "hub_copilot"])
            rationale = "template_first_policy"

        if not use_repo_context:
            rationale = f"{rationale}_repo_context_disabled"

        return PlanningOrchestrationDecision(strategy_order=order, rationale=rationale)


_SERVICE = LLMFirstPlanningOrchestratorService()


def get_llm_first_planning_orchestrator_service() -> LLMFirstPlanningOrchestratorService:
    return _SERVICE
