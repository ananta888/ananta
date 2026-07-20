"""Source-duration-based vector allocation for offline reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from ananta_contracts.speech_reconciliation import (
    MAX_RESEARCH_FACTOR,
    NORMAL_MAX_FACTOR,
    RESOURCE_FIELDS,
    SpeechReconciliationContractError,
    SpeechResourceVector,
)

DEFAULT_STAGE_WEIGHTS = {
    "staging": 5,
    "slow_asr": 65,
    "alignment": 10,
    "resolution": 10,
    "dataset": 5,
    "evaluation": 5,
}


@dataclass(frozen=True)
class AdmittedSourceDuration:
    source_group_digest: str
    duration_ms: int


@dataclass(frozen=True)
class SpeechReconciliationBudgetPlan:
    source_duration_ms: int
    compute_factor: int
    compute_equivalent_ms: int
    total: SpeechResourceVector
    stages: Mapping[str, SpeechResourceVector]


class SpeechReconciliationBudgetService:
    def __init__(self, *, absolute_hard_cap: SpeechResourceVector | None = None) -> None:
        self._hard_cap = absolute_hard_cap or SpeechResourceVector(
            wall_time_ms=8 * 60 * 60 * 1000,
            cpu_time_ms=160 * 60 * 60 * 1000,
            gpu_time_ms=80 * 60 * 60 * 1000,
            memory_byte_ms=2**63 - 1,
            disk_bytes=512 * 1024**3,
            checkpoint_bytes=64 * 1024**3,
            energy_millijoules=2**63 - 1,
        )

    def plan(
        self,
        sources: Iterable[AdmittedSourceDuration],
        *,
        compute_factor: int,
        research_policy_ref: str | None = None,
        requested_limits: SpeechResourceVector | None = None,
    ) -> SpeechReconciliationBudgetPlan:
        if not 1 <= compute_factor <= MAX_RESEARCH_FACTOR:
            raise SpeechReconciliationContractError("speech_reconciliation_factor_invalid")
        if compute_factor > NORMAL_MAX_FACTOR and not research_policy_ref:
            raise SpeechReconciliationContractError("speech_reconciliation_research_policy_required")
        deduplicated: dict[str, int] = {}
        for source in sources:
            if source.duration_ms <= 0 or source.duration_ms > 8 * 60 * 60 * 1000:
                raise SpeechReconciliationContractError("speech_reconciliation_source_duration_invalid")
            previous = deduplicated.setdefault(source.source_group_digest, source.duration_ms)
            if previous != source.duration_ms:
                raise SpeechReconciliationContractError("speech_reconciliation_source_binding_conflict")
        source_duration = sum(deduplicated.values())
        if source_duration <= 0 or source_duration > 8 * 60 * 60 * 1000:
            raise SpeechReconciliationContractError("speech_reconciliation_source_duration_invalid")
        compute_ms = source_duration * compute_factor
        if compute_ms > 2**63 - 1:
            raise SpeechReconciliationContractError("speech_reconciliation_budget_overflow")
        proposed = SpeechResourceVector(
            wall_time_ms=min(compute_ms, self._hard_cap.wall_time_ms),
            cpu_time_ms=min(compute_ms, self._hard_cap.cpu_time_ms),
            gpu_time_ms=min(compute_ms, self._hard_cap.gpu_time_ms),
            memory_byte_ms=min(compute_ms * 16 * 1024**3, self._hard_cap.memory_byte_ms),
            disk_bytes=min(max(64 * 1024**2, source_duration * 64), self._hard_cap.disk_bytes),
            checkpoint_bytes=min(max(16 * 1024**2, source_duration * 8), self._hard_cap.checkpoint_bytes),
            energy_millijoules=min(compute_ms * 250, self._hard_cap.energy_millijoules),
        )
        total = _minimum(proposed, requested_limits) if requested_limits is not None else proposed
        stages = _split_vector(total, DEFAULT_STAGE_WEIGHTS)
        return SpeechReconciliationBudgetPlan(source_duration, compute_factor, compute_ms, total, stages)


def _minimum(first: SpeechResourceVector, second: SpeechResourceVector) -> SpeechResourceVector:
    return SpeechResourceVector(
        **{field: min(getattr(first, field), getattr(second, field)) for field in RESOURCE_FIELDS}
    )


def _split_vector(
    total: SpeechResourceVector,
    weights: Mapping[str, int],
) -> dict[str, SpeechResourceVector]:
    denominator = sum(weights.values())
    stages: dict[str, SpeechResourceVector] = {}
    used = {field: 0 for field in RESOURCE_FIELDS}
    names = tuple(weights)
    for index, stage in enumerate(names):
        values: dict[str, int] = {}
        for field in RESOURCE_FIELDS:
            amount = (
                getattr(total, field) - used[field]
                if index == len(names) - 1
                else getattr(total, field) * weights[stage] // denominator
            )
            values[field] = amount
            used[field] += amount
        stages[stage] = SpeechResourceVector(**values)
    return stages


__all__ = [
    "AdmittedSourceDuration",
    "SpeechReconciliationBudgetPlan",
    "SpeechReconciliationBudgetService",
]
