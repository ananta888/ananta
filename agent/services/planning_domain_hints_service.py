from __future__ import annotations

from typing import Any

from agent.services.goal_planning_intent_service import get_goal_planning_intent_service


class PlanningDomainHintsService:
    def derive_hints(self, *, goal: str, mode: str, team_id: str | None, planning_policy: dict[str, Any] | None) -> list[str]:
        intent = get_goal_planning_intent_service().classify(goal_text=goal, mode=mode)
        goal_type = str(intent.get("goal_type") or "generic")
        hints: list[str] = []
        if goal_type == "software_project":
            hints.extend(
                [
                    "Use the fixed phases: setup, implementation, execution, verification, summary.",
                    "Include at least one task for each of: analysis, infrastructure, implementation, tests, review.",
                    "The plan is invalid unless it includes a concrete testing task and a concrete review task.",
                    "Include at least one concrete API/interface task when the goal mentions backend, service, or API work.",
                    "Include tests for valid and invalid cases and name the concrete test file or command.",
                    "Include explicit run/build command and verification command tasks with file, endpoint, or artifact output.",
                ]
            )
        elif goal_type == "operations":
            hints.extend(
                [
                    "Include runtime preflight and rollback checkpoints.",
                    "Include command-based verification after each critical step.",
                ]
            )
        elif goal_type == "research":
            hints.extend(
                [
                    "Include evidence collection and source verification tasks.",
                    "Include synthesis and decision-summary task.",
                ]
            )

        policy = dict(planning_policy or {})
        team_overrides = policy.get("team_overrides") if isinstance(policy.get("team_overrides"), dict) else {}
        if team_id and isinstance(team_overrides.get(str(team_id)), dict):
            custom_hints = list((team_overrides.get(str(team_id)) or {}).get("domain_hints") or [])
            for h in custom_hints:
                hs = str(h).strip()
                if hs:
                    hints.append(hs)
        return hints


_SERVICE = PlanningDomainHintsService()


def get_planning_domain_hints_service() -> PlanningDomainHintsService:
    return _SERVICE
