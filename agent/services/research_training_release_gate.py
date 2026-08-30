"""Fail-closed automatic release decision for completed research runs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.research_training_evaluation_service import ResearchTrainingEvaluationService


class ResearchTrainingReleaseGate:
    def __init__(self, evaluations: ResearchTrainingEvaluationService) -> None:
        self._evaluations = evaluations

    def decide(self, *, run: Mapping[str, Any], evaluation: Mapping[str, Any]) -> dict[str, Any]:
        reasons: list[str] = []
        if run.get("state") != "completed":
            reasons.append("research_run_not_completed")
        if run.get("automatic_release_eligible") is not True:
            reasons.append("research_automatic_release_not_enabled")
        if not self._evaluations.verify(evaluation):
            reasons.append("research_evaluation_attestation_invalid")
        if evaluation.get("run_id") != run.get("run_id"):
            reasons.append("research_evaluation_run_binding_invalid")
        if evaluation.get("dataset_manifest_digest") != dict(run.get("spec") or {}).get("dataset_manifest_digest"):
            reasons.append("research_evaluation_dataset_binding_invalid")
        if evaluation.get("release_eligible") is not True or evaluation.get("reason_codes"):
            reasons.append("research_evaluation_gate_failed")
        return {
            "schema": "ananta.research-training-release-decision.v1",
            "run_id": run.get("run_id"),
            "eligible": not reasons,
            "reason_codes": sorted(set(reasons)),
            "automatic": True,
            "production_eligible": False,
            "human_intervention_required": False,
        }


__all__ = ["ResearchTrainingReleaseGate"]
