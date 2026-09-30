"""Admission-time validation of a speech reconciliation budget plan."""

from __future__ import annotations

from typing import Mapping

from agent.repositories.speech_reconciliation_records import SpeechReconciliationRepositoryError
from ananta_contracts.speech_reconciliation import SpeechResourceVector
from ananta_contracts.speech_reconciliation_state import STAGES


def validate_budget_plan(value: Mapping[str, object], *, factor: int) -> dict[str, object]:
    if set(value) != {"compute_factor", "compute_equivalent_ms", "allocated", "stages"}:
        raise SpeechReconciliationRepositoryError(
            "speech_reconciliation_budget_plan_invalid",
            status_code=422,
        )
    if value.get("compute_factor") != factor:
        raise SpeechReconciliationRepositoryError(
            "speech_reconciliation_budget_plan_factor_mismatch",
            status_code=422,
        )
    compute_equivalent_ms = value.get("compute_equivalent_ms")
    if (
        isinstance(compute_equivalent_ms, bool)
        or not isinstance(compute_equivalent_ms, int)
        or not 1 <= compute_equivalent_ms <= 2**63 - 1
    ):
        raise SpeechReconciliationRepositoryError(
            "speech_reconciliation_budget_plan_invalid",
            status_code=422,
        )
    try:
        allocated = SpeechResourceVector.from_mapping(value.get("allocated"), "budget_plan.allocated")
    except Exception as exc:
        raise SpeechReconciliationRepositoryError(
            "speech_reconciliation_budget_plan_invalid",
            status_code=422,
        ) from exc
    raw_stages = value.get("stages")
    if not isinstance(raw_stages, Mapping) or not raw_stages or any(stage not in STAGES for stage in raw_stages):
        raise SpeechReconciliationRepositoryError(
            "speech_reconciliation_budget_plan_invalid",
            status_code=422,
        )
    stages: dict[str, dict[str, int]] = {}
    summed = SpeechResourceVector()
    try:
        for stage, raw in sorted(raw_stages.items()):
            vector = SpeechResourceVector.from_mapping(raw, f"budget_plan.stages.{stage}")
            stages[str(stage)] = vector.to_dict()
            summed = summed.add(vector)
    except Exception as exc:
        raise SpeechReconciliationRepositoryError(
            "speech_reconciliation_budget_plan_invalid",
            status_code=422,
        ) from exc
    if summed != allocated:
        raise SpeechReconciliationRepositoryError(
            "speech_reconciliation_budget_plan_arithmetic_invalid",
            status_code=422,
        )
    return {
        "compute_factor": factor,
        "compute_equivalent_ms": compute_equivalent_ms,
        "allocated": allocated.to_dict(),
        "stages": stages,
    }


__all__ = ["validate_budget_plan"]
