from __future__ import annotations

from typing import Any

from agent.services.planning_metrics_service import get_planning_metrics_service


class PromptImprovementService:
    def propose_improvements(self) -> dict[str, Any]:
        metrics = get_planning_metrics_service().summarize()
        proposals: list[dict[str, Any]] = []
        for group in metrics.get("groups", []):
            if float(group.get("parse_success_rate") or 0.0) < 0.6:
                proposals.append(
                    {
                        "group": group.get("group"),
                        "proposal": "Increase JSON strictness and reduce prompt verbosity.",
                        "reason": "low_parse_success_rate",
                    }
                )
            if float(group.get("materialization_success_rate") or 0.0) < 0.5:
                proposals.append(
                    {
                        "group": group.get("group"),
                        "proposal": "Force expected_artifacts + verification_spec in prompt contract.",
                        "reason": "low_materialization_success_rate",
                    }
                )
        return {"proposals": proposals, "source_run_count": metrics.get("run_count", 0)}


_SERVICE = PromptImprovementService()


def get_prompt_improvement_service() -> PromptImprovementService:
    return _SERVICE
