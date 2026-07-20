"""Hub-owned quality-wave decisions for offline speech reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agent.repositories.speech_reconciliation import SpeechReconciliationJobRecord
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_reconciliation import (
    SpeechReconciliationBudgetLedger,
    SpeechReconciliationJob,
    SpeechResourceVector,
)
from ananta_contracts.speech_reconciliation_worker import SpeechReconciliationWorkerOutcome
from voice_runtime.speech_reconciliation_policy import (
    SpeechReconciliationPolicy,
    SpeechReconciliationQualitySample,
)


class SpeechReconciliationQualityRepositoryPort(Protocol):
    def get_job(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_id: str,
    ) -> SpeechReconciliationJobRecord | None: ...

    def apply_quality_decision(self, **values) -> SpeechReconciliationJobRecord: ...


class SpeechReconciliationQualityLedgerPort(Protocol):
    def get(self, *, job_id: str) -> SpeechReconciliationBudgetLedger | None: ...


@dataclass(frozen=True, slots=True)
class SpeechReconciliationWaveDecision:
    action: str
    current_factor: int
    next_factor: int
    quality_score_micros: int
    unresolved_high_quality_conflicts: int
    reason_code: str
    materialize_dataset: bool


class HubSpeechReconciliationQualityController:
    """Turn a worker observation into one bounded Hub scheduling decision.

    The worker reports both the total number of unresolved regions and the
    narrower number of unresolved high-quality semantic conflicts.  Only the
    latter can justify another wave.  Legacy workers do not carry that proof,
    so their optional count is treated as zero and cannot extend compute.
    """

    def __init__(
        self,
        *,
        repository: SpeechReconciliationQualityRepositoryPort,
        ledgers: SpeechReconciliationQualityLedgerPort,
        policy: SpeechReconciliationPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._ledgers = ledgers
        self._policy = policy or SpeechReconciliationPolicy()

    def decide(
        self,
        principal: VoicePrincipal,
        job: SpeechReconciliationJob,
        outcome: SpeechReconciliationWorkerOutcome,
        *,
        authority: str = "hub",
    ) -> SpeechReconciliationWaveDecision:
        if authority != "hub":
            raise PermissionError("speech_reconciliation_hub_quality_authority_required")
        current = self._repository.get_job(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            job_id=job.job_id,
        )
        ledger = self._ledgers.get(job_id=job.job_id)
        if current is None or ledger is None:
            raise ValueError("speech_reconciliation_quality_state_unavailable")
        if (
            current.state != "running"
            or current.active_attempt_id != job.attempt_id
            or current.fencing_epoch != job.fencing_epoch
            or current.ledger_sequence != job.ledger_sequence
            or ledger.attempt_id != job.attempt_id
            or ledger.fencing_epoch != job.fencing_epoch
            or ledger.sequence != job.ledger_sequence
        ):
            raise ValueError("speech_reconciliation_quality_fence_stale")

        score_micros = (
            outcome.quality_score_micros
            if outcome.quality_score_micros is not None
            else _legacy_quality_score_micros(outcome)
        )
        history = tuple(
            dict(value)
            for value in current.quality_history
            if isinstance(value, dict) and value.get("quality_score_micros") is not None
        )
        previous_score = (
            None if outcome.previous_quality_score_micros is None else outcome.previous_quality_score_micros / 1_000_000
        )
        if history and previous_score is not None:
            previous_score = max(
                previous_score,
                float(int(history[-1]["quality_score_micros"])) / 1_000_000,
            )
        evaluation = _evaluation_reserve(current)
        evaluation_reserved = evaluation != SpeechResourceVector() and ledger.remaining.covers(evaluation)
        after_evaluation = _subtract_if_covered(ledger.remaining, evaluation)
        energy_limit = evaluation.energy_millijoules > ledger.remaining.energy_millijoules
        resources_remaining = (
            after_evaluation is not None
            and after_evaluation.wall_time_ms >= current.source_duration_ms
            and (
                after_evaluation.cpu_time_ms >= current.source_duration_ms
                or after_evaluation.gpu_time_ms >= current.source_duration_ms
            )
            and after_evaluation.disk_bytes > 0
            and after_evaluation.checkpoint_bytes > 0
            and after_evaluation.energy_millijoules > 0
        )
        unresolved_high_quality_conflicts = (
            outcome.unresolved_high_quality_conflict_count
            if outcome.unresolved_high_quality_conflict_count is not None
            else 0
        )
        policy_decision = self._policy.decide(
            SpeechReconciliationQualitySample(
                current_factor=current.current_compute_factor,
                authorized_factor=current.max_compute_factor,
                unresolved_high_quality_conflicts=unresolved_high_quality_conflicts,
                quality_score=score_micros / 1_000_000,
                previous_quality_score=previous_score,
                evidence_count=outcome.successful_candidate_count,
                resource_remaining=resources_remaining,
                evaluation_budget_reserved=evaluation_reserved,
                energy_limit_reached=energy_limit,
            )
        )
        persisted = self._repository.apply_quality_decision(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            job_contract=job,
            action=policy_decision.action,
            current_factor=current.current_compute_factor,
            next_factor=policy_decision.next_factor,
            quality_score_micros=score_micros,
            unresolved_count=outcome.unresolved_count,
            unresolved_high_quality_conflicts=unresolved_high_quality_conflicts,
            reason_code=policy_decision.reason_code,
        )
        return SpeechReconciliationWaveDecision(
            action=policy_decision.action,
            current_factor=current.current_compute_factor,
            next_factor=persisted.current_compute_factor,
            quality_score_micros=score_micros,
            unresolved_high_quality_conflicts=unresolved_high_quality_conflicts,
            reason_code=policy_decision.reason_code,
            materialize_dataset=policy_decision.materialize_dataset,
        )


def _legacy_quality_score_micros(outcome: SpeechReconciliationWorkerOutcome) -> int:
    if outcome.candidate_count < 1 or outcome.successful_candidate_count < 1:
        return 0
    coverage = outcome.successful_candidate_count / outcome.candidate_count
    conflict_discount = 1 / (1 + outcome.unresolved_count)
    return max(0, min(1_000_000, round(coverage * conflict_discount * 1_000_000)))


def _evaluation_reserve(job: SpeechReconciliationJobRecord) -> SpeechResourceVector:
    stages = dict(job.budget_plan or {}).get("stages")
    if not isinstance(stages, dict):
        return SpeechResourceVector()
    raw = stages.get("evaluation")
    try:
        return SpeechResourceVector.from_mapping(raw, "budget_plan.stages.evaluation")
    except Exception:
        return SpeechResourceVector()


def _subtract_if_covered(
    available: SpeechResourceVector,
    reserve: SpeechResourceVector,
) -> SpeechResourceVector | None:
    if not available.covers(reserve):
        return None
    return available.subtract(reserve)


__all__ = [
    "HubSpeechReconciliationQualityController",
    "SpeechReconciliationWaveDecision",
]
