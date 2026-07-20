from __future__ import annotations

from agent.services.ml_intern_speech_reconciled_dataset_service import (
    MlInternSpeechReconciledDatasetService,
    ReconciledDatasetCandidate,
)
from agent.services.speech_reconciliation_training_delegate import SpeechReconciliationTrainingDelegate
from agent.services.voice_governance_domain import VoicePrincipal
from voice_runtime.speech_reconciliation_policy import (
    SpeechReconciliationPolicy,
    SpeechReconciliationQualitySample,
)


def _sample(**changes):
    values = {
        "current_factor": 5,
        "authorized_factor": 20,
        "unresolved_high_quality_conflicts": 3,
        "quality_score": 0.8,
        "previous_quality_score": 0.75,
        "evidence_count": 20,
        "resource_remaining": True,
        "evaluation_budget_reserved": True,
    }
    values.update(changes)
    return SpeechReconciliationQualitySample(**values)


def test_initial_factor_and_positive_trend_extension_are_bounded() -> None:
    policy = SpeechReconciliationPolicy()
    assert policy.initial_factor(user_limit=20, authorized_factor=20) == 10
    decision = policy.decide(_sample())
    assert decision.action == "extend" and decision.next_factor == 10


def test_plateau_regression_resource_and_missing_eval_budget_stop_deterministically() -> None:
    policy = SpeechReconciliationPolicy()
    assert policy.decide(_sample(quality_score=0.751)).reason_code == "speech_reconciliation_quality_plateau"
    assert policy.decide(_sample(quality_score=0.7)).reason_code == "speech_reconciliation_quality_regression"
    assert policy.decide(_sample(resource_remaining=False)).reason_code == "speech_reconciliation_resource_limit"
    no_eval = policy.decide(_sample(evaluation_budget_reserved=False))
    assert no_eval.action == "dataset_only" and no_eval.materialize_dataset


class _Builder:
    def __init__(self) -> None:
        self.records = []

    def build(self, principal, **values):
        del principal
        self.records = list(values["records"])
        return {
            "dataset_id": values["dataset_id"],
            "version": "sha256:test",
            "manifest_digest": "a" * 64,
        }, True


class _Training:
    def __init__(self) -> None:
        self.calls = []

    def admit_dataset(self, principal, **values):
        self.calls.append((principal, values))
        return "speech-training-delegated"


def test_terminal_materialization_counts_all_outcomes_and_training_is_separate() -> None:
    builder = _Builder()
    service = MlInternSpeechReconciledDatasetService(builder)
    candidates = [
        ReconciledDatasetCandidate("resolved", {"record_digest": "a" * 64}),
        ReconciledDatasetCandidate("rejected", {"record_digest": "b" * 64}),
        ReconciledDatasetCandidate("quarantined", {"record_digest": "c" * 64}),
    ]
    principal = VoicePrincipal("tenant-policy", "owner-policy")
    partial = service.materialize(
        principal,
        dataset_id="dataset-policy",
        candidates=candidates,
        reconciliation_digest="d" * 64,
        parent_digest=None,
        terminal=False,
    )
    assert partial.resolved_count == partial.rejected_count == partial.quarantined_count == 1
    assert len(builder.records) == 3 and not partial.trainable
    assert sum(
        int(partial.curation_summary[f"{status}_count"])
        for status in ("resolved", "unresolved", "rejected", "quarantined")
    ) == len(candidates)
    partitions = [
        set(partial.curation_summary[f"{status}_record_digests"])
        for status in ("resolved", "unresolved", "rejected", "quarantined")
    ]
    assert not any(left & right for index, left in enumerate(partitions) for right in partitions[index + 1 :])
    training = _Training()
    delegate = SpeechReconciliationTrainingDelegate(training)
    dataset_only = delegate.delegate(
        principal,
        partial,
        training_budget={"wall_time_ms": 10},
        idempotency_key="reconciliation-policy",
    )
    assert dataset_only.status == "dataset_only_completed" and not training.calls


def test_trainable_terminal_dataset_requires_explicit_training_budget() -> None:
    service = MlInternSpeechReconciledDatasetService(_Builder())
    principal = VoicePrincipal("tenant-training-policy", "owner-training-policy")
    materialization = service.materialize(
        principal,
        dataset_id="dataset-training-policy",
        candidates=[ReconciledDatasetCandidate("resolved", {"record_digest": "e" * 64})],
        reconciliation_digest="f" * 64,
        parent_digest=None,
        terminal=True,
    )
    training = _Training()
    delegate = SpeechReconciliationTrainingDelegate(training)
    assert (
        delegate.delegate(
            principal,
            materialization,
            training_budget=None,
            idempotency_key="no-budget-policy",
        ).status
        == "dataset_only_completed"
    )
    decision = delegate.delegate(
        principal,
        materialization,
        training_budget={"wall_time_ms": 100},
        idempotency_key="with-budget-policy",
    )
    assert decision.training_job_id == "speech-training-delegated" and len(training.calls) == 1
