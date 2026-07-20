from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent.services.ml_intern_speech_reconciled_dataset_service import (
    ReconciledDatasetMaterialization,
)
from agent.services.speech_reconciliation_production_composition import (
    ManifestBoundSpeechReconciliationExecutionPlan,
)
from agent.services.speech_reconciliation_quality_controller import (
    HubSpeechReconciliationQualityController,
)
from agent.services.speech_reconciliation_result_admission_service import (
    HubSpeechReconciliationResultAdmissionService,
    PublishedSpeechReconciliationDataset,
)
from agent.services.speech_reconciliation_training_delegate import (
    SpeechAdaptationTrainingAdmissionAdapter,
    SpeechReconciliationTrainingDelegate,
)
from agent.services.speech_reconciliation_worker_port import SpeechReconciliationWorkerPoll
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_reconciliation import (
    CONTRACT_VERSION,
    SpeechReconciliationBudgetLedger,
    SpeechResourceVector,
)
from ananta_contracts.speech_reconciliation_worker import SpeechReconciliationWorkerOutcome
from tests.speech_reconciliation_support import (
    digest,
    job_contract,
    worker_outcome_contract,
    worker_outcome_payload,
)


def _vector(value: int = 100_000) -> SpeechResourceVector:
    return SpeechResourceVector(
        wall_time_ms=value,
        cpu_time_ms=value,
        gpu_time_ms=0,
        memory_byte_ms=value,
        disk_bytes=value,
        checkpoint_bytes=value,
        energy_millijoules=value,
    )


def _training_budget() -> dict[str, int]:
    return {
        "max_wall_seconds": 30,
        "max_ram_bytes": 512 * 1024**2,
        "max_vram_bytes": 0,
        "max_disk_bytes": 64 * 1024**2,
        "max_artifact_bytes": 1024 * 1024,
        "max_checkpoints": 2,
        "max_events": 100,
    }


def _training_profile() -> dict[str, object]:
    return {
        "base_model_id": "speech-mock-base-v1",
        "pair_id": "pair-quality",
        "direction": "sender_to_receiver",
        "speaker_digest": digest("speaker-quality"),
        "backend": "mock",
        "seed": 7,
        "max_steps": 3,
        "batch_size": 1,
        "checkpoint_interval_steps": 2,
        "learning_rate": 0.001,
        "scenario": "success",
        "capacity_policy": "dataset_only",
    }


def _ledger(job, *, available: SpeechResourceVector | None = None):
    resources = available or _vector()
    return SpeechReconciliationBudgetLedger.from_mapping(
        {
            "contract_version": CONTRACT_VERSION,
            "job_id": job.job_id,
            "attempt_id": job.attempt_id,
            "fencing_epoch": job.fencing_epoch,
            "sequence": job.ledger_sequence,
            "stage": job.stage,
            "source_duration_ms": job.source_duration_ms,
            "compute_factor": job.max_compute_factor,
            "allocated": resources.to_dict(),
            "reserved": SpeechResourceVector().to_dict(),
            "consumed": SpeechResourceVector().to_dict(),
            "remaining": resources.to_dict(),
        }
    )


def _partial(
    job,
    *,
    candidates: int = 2,
    successful: int = 2,
    unresolved: int = 1,
    unresolved_high_quality: int = 1,
):
    return worker_outcome_contract(
        job,
        status="partial",
        candidate_count=candidates,
        successful_candidate_count=successful,
        failed_candidate_count=candidates - successful,
        unresolved_count=unresolved,
        unresolved_region_ids=[digest(f"unresolved-region-{index}") for index in range(unresolved)],
        unresolved_high_quality_conflict_count=unresolved_high_quality,
        publishable=False,
        transcript=None,
        reason_code="speech_reconciliation_conflicts_unresolved",
    )


class _QualityRepository:
    def __init__(self, row) -> None:
        self.row = row
        self.calls = []

    def get_job(self, **_values):
        return self.row

    def apply_quality_decision(self, **values):
        self.calls.append(values)
        observation = {
            "quality_score_micros": values["quality_score_micros"],
        }
        self.row = SimpleNamespace(
            **{
                **self.row.__dict__,
                "current_compute_factor": values["next_factor"],
                "quality_history": [*self.row.quality_history, observation],
            }
        )
        return self.row


class _Ledgers:
    def __init__(self, ledger) -> None:
        self.ledger = ledger

    def get(self, **_values):
        return self.ledger


def _quality_row(job, *, factor: int = 10, history=(), resources: SpeechResourceVector | None = None):
    evaluation = resources or SpeechResourceVector(
        wall_time_ms=1_000,
        cpu_time_ms=1_000,
        memory_byte_ms=1_000,
        disk_bytes=1_000,
        checkpoint_bytes=1_000,
        energy_millijoules=1_000,
    )
    return SimpleNamespace(
        state="running",
        active_attempt_id=job.attempt_id,
        fencing_epoch=job.fencing_epoch,
        ledger_sequence=job.ledger_sequence,
        current_compute_factor=factor,
        max_compute_factor=20,
        source_duration_ms=1_000,
        quality_history=list(history),
        budget_plan={"stages": {"evaluation": evaluation.to_dict()}},
    )


def test_hub_quality_controller_extends_once_then_stops_on_plateau() -> None:
    first_job = job_contract(source_duration_ms=1_000, max_compute_factor=20)
    repository = _QualityRepository(_quality_row(first_job))
    controller = HubSpeechReconciliationQualityController(
        repository=repository,
        ledgers=_Ledgers(_ledger(first_job)),
    )
    first = controller.decide(
        VoicePrincipal("tenant-quality", "owner-quality"),
        first_job,
        _partial(first_job),
    )
    assert (first.action, first.current_factor, first.next_factor) == ("extend", 10, 20)

    second_job = replace(
        first_job,
        attempt_id="speech-reconciliation-attempt-wave-2",
        fencing_epoch=2,
    )
    repository.row = SimpleNamespace(
        **{
            **repository.row.__dict__,
            "active_attempt_id": second_job.attempt_id,
            "fencing_epoch": second_job.fencing_epoch,
            "current_compute_factor": 20,
        }
    )
    controller = HubSpeechReconciliationQualityController(
        repository=repository,
        ledgers=_Ledgers(_ledger(second_job)),
    )
    second = controller.decide(
        VoicePrincipal("tenant-quality", "owner-quality"),
        second_job,
        _partial(second_job, candidates=4, successful=4),
    )
    assert second.action == "stop"
    assert second.reason_code == "speech_reconciliation_quality_plateau"
    assert second.materialize_dataset is True


def test_hub_quality_controller_never_invents_a_baseline_for_extension() -> None:
    job = job_contract(source_duration_ms=1_000, max_compute_factor=20)
    repository = _QualityRepository(_quality_row(job))
    controller = HubSpeechReconciliationQualityController(
        repository=repository,
        ledgers=_Ledgers(_ledger(job)),
    )
    outcome = _partial(job)
    outcome = replace(outcome, previous_quality_score_micros=None)
    decision = controller.decide(
        VoicePrincipal("tenant-quality", "owner-quality"),
        job,
        outcome,
    )
    assert decision.action == "stop"
    assert decision.reason_code == "speech_reconciliation_trend_unavailable"


def test_legacy_worker_outcome_without_quality_metrics_remains_parseable_but_cannot_extend() -> None:
    job = job_contract(source_duration_ms=1_000, max_compute_factor=20)
    payload = worker_outcome_payload(
        job,
        status="partial",
        unresolved_count=1,
        publishable=False,
        transcript=None,
    )
    payload.pop("quality_score_micros")
    payload.pop("previous_quality_score_micros")
    payload.pop("unresolved_high_quality_conflict_count")
    outcome = SpeechReconciliationWorkerOutcome.from_mapping(payload)
    repository = _QualityRepository(_quality_row(job))
    decision = HubSpeechReconciliationQualityController(
        repository=repository,
        ledgers=_Ledgers(_ledger(job)),
    ).decide(VoicePrincipal("tenant-quality", "owner-quality"), job, outcome)
    assert decision.action == "stop"
    assert decision.reason_code == "speech_reconciliation_conflicts_resolved"


def test_low_confidence_unresolved_regions_never_extend_compute() -> None:
    job = job_contract(source_duration_ms=1_000, max_compute_factor=20)
    repository = _QualityRepository(_quality_row(job))
    decision = HubSpeechReconciliationQualityController(
        repository=repository,
        ledgers=_Ledgers(_ledger(job)),
    ).decide(
        VoicePrincipal("tenant-quality", "owner-quality"),
        job,
        _partial(job, unresolved=3, unresolved_high_quality=0),
    )
    assert decision.action == "stop"
    assert decision.reason_code == "speech_reconciliation_conflicts_resolved"
    assert repository.calls[0]["unresolved_count"] == 3
    assert repository.calls[0]["unresolved_high_quality_conflicts"] == 0


def test_worker_contract_bounds_high_quality_conflicts_by_total_unresolved() -> None:
    job = job_contract()
    payload = worker_outcome_payload(
        job,
        status="partial",
        unresolved_count=1,
        unresolved_high_quality_conflict_count=2,
        publishable=False,
        transcript=None,
    )
    with pytest.raises(
        ValueError,
        match="speech_reconciliation_high_quality_conflict_count_inconsistent",
    ):
        SpeechReconciliationWorkerOutcome.from_mapping(payload)


def test_hub_quality_controller_stops_on_evaluation_energy_and_evidence_limits() -> None:
    job = job_contract(source_duration_ms=1_000, max_compute_factor=20)
    evaluation = SpeechResourceVector(
        wall_time_ms=1,
        cpu_time_ms=1,
        memory_byte_ms=1,
        disk_bytes=1,
        checkpoint_bytes=1,
        energy_millijoules=50,
    )
    repository = _QualityRepository(_quality_row(job, resources=evaluation))
    low_energy = _vector(10)
    controller = HubSpeechReconciliationQualityController(
        repository=repository,
        ledgers=_Ledgers(_ledger(job, available=low_energy)),
    )
    assert (
        controller.decide(
            VoicePrincipal("tenant-quality", "owner-quality"),
            job,
            _partial(job),
        ).reason_code
        == "speech_reconciliation_energy_limit"
    )

    repository = _QualityRepository(_quality_row(job))
    controller = HubSpeechReconciliationQualityController(
        repository=repository,
        ledgers=_Ledgers(_ledger(job)),
    )
    no_evidence = _partial(job, candidates=1, successful=0, unresolved=1)
    assert (
        controller.decide(
            VoicePrincipal("tenant-quality", "owner-quality"),
            job,
            no_evidence,
        ).reason_code
        == "speech_reconciliation_evidence_insufficient"
    )


class _Catalog:
    def require_model(self, model_id: str):
        return SimpleNamespace(model_id=model_id, revision=f"{model_id}@1", languages=("de",))


def test_production_execution_plan_uses_only_current_hub_wave_factor() -> None:
    models = tuple(f"model-{index:02d}" for index in range(12))
    initial = ManifestBoundSpeechReconciliationExecutionPlan(
        _Catalog(),  # type: ignore[arg-type]
        model_ids=models,
    ).resolve(job_contract(max_compute_factor=20))
    assert len(initial.passes) == 10
    extended = ManifestBoundSpeechReconciliationExecutionPlan(
        _Catalog(),  # type: ignore[arg-type]
        model_ids=models,
        factor_lookup=lambda _job: 12,
    ).resolve(job_contract(max_compute_factor=20))
    assert len(extended.passes) == 12


class _Authority:
    def __init__(self) -> None:
        self.calls = []

    def authorize(self, _principal, _job, *, phase: str):
        self.calls.append(phase)


class _Results:
    def __init__(self, events=None) -> None:
        self.checkpoints = []
        self.completions = []
        self.events = events

    def save_checkpoint(self, **values):
        self.checkpoints.append(values)

    def complete(self, **values):
        self.completions.append(values)
        if self.events is not None:
            self.events.append("commit")


class _PublicationLedger:
    def authorize_publication(self, **_values):
        return True


class _StopQuality:
    def decide(self, _principal, _job, outcome, *, authority="hub"):
        assert authority == "hub" and outcome.unresolved_count == 1
        return SimpleNamespace(
            action="stop",
            reason_code="speech_reconciliation_quality_plateau",
            materialize_dataset=True,
        )


class _Publisher:
    def __init__(self, *, trainable: bool, events=None) -> None:
        self.calls = []
        self.events = events
        self.materialization = ReconciledDatasetMaterialization(
            manifest={
                "dataset_id": "dataset-quality",
                "version": f"sha256:{digest('published-quality')}",
                "manifest_digest": digest("published-quality"),
            },
            created=True,
            resolved_count=2,
            unresolved_count=0 if trainable else 1,
            rejected_count=0,
            quarantined_count=0,
            trainable=trainable,
        )

    def publish(self, _principal, **values):
        self.calls.append(values)
        if self.events is not None:
            self.events.append("dataset")
        return PublishedSpeechReconciliationDataset(
            digest("published-quality"),
            "artifact://speech-datasets/reconciliation/published-quality",
            self.materialization.resolved_count,
            self.materialization.unresolved_count,
            0,
            0,
            self.materialization,
        )


class _Training:
    def __init__(self, events=None) -> None:
        self.calls = []
        self.events = events

    def admit_dataset(self, principal, **values):
        self.calls.append((principal, values))
        if self.events is not None:
            self.events.append("training")
        return "speech-training-quality"


class _Budgets:
    def __init__(self, value) -> None:
        self.value = value

    def resolve(self, _principal, _job):
        return self.value


def _admission(*, publisher, quality=None, training=None, budgets=None, events=None):
    repository = _Results(events)
    return (
        HubSpeechReconciliationResultAdmissionService(
            authority=_Authority(),
            repository=repository,
            ledger=_PublicationLedger(),
            publisher=publisher,
            quality=quality,
            training=training,
            training_budgets=budgets,
        ),
        repository,
    )


def test_terminal_partial_materializes_dataset_but_never_trains_checkpoint() -> None:
    job = job_contract()
    training = _Training()
    publisher = _Publisher(trainable=False)
    service, repository = _admission(
        publisher=publisher,
        quality=_StopQuality(),
        training=SpeechReconciliationTrainingDelegate(training),
        budgets=_Budgets({"max_wall_seconds": 30}),
    )
    outcome = _partial(job)
    admission = service.accept(
        VoicePrincipal("tenant-quality", "owner-quality"),
        job,
        SpeechReconciliationWorkerPoll(job.job_id, job.attempt_id, job.fencing_epoch, "partial", outcome),
    )
    assert admission.result is not None
    assert admission.result.status == "dataset_only_completed"
    assert admission.result.unresolved_count == 1
    assert admission.training is not None
    assert admission.training.status == "dataset_only_completed"
    assert not training.calls
    assert repository.completions[0]["publication_authorized"] is True
    assert publisher.calls[0]["transcript"] is None


def test_terminal_dataset_delegates_training_only_with_positive_explicit_budget() -> None:
    job = job_contract()
    training = _Training()
    publisher = _Publisher(trainable=True)
    delegate = SpeechReconciliationTrainingDelegate(training)
    no_budget, _ = _admission(
        publisher=publisher,
        training=delegate,
        budgets=_Budgets(None),
    )
    outcome = worker_outcome_contract(job)
    denied = no_budget.accept(
        VoicePrincipal("tenant-quality", "owner-quality"),
        job,
        SpeechReconciliationWorkerPoll(job.job_id, job.attempt_id, job.fencing_epoch, "completed", outcome),
    )
    assert denied.training is not None
    assert denied.training.status == "dataset_only_completed"
    assert not training.calls

    events = []
    training = _Training(events)
    publisher = _Publisher(trainable=True, events=events)
    service, _ = _admission(
        publisher=publisher,
        training=SpeechReconciliationTrainingDelegate(training),
        budgets=_Budgets({"max_wall_seconds": 30}),
        events=events,
    )
    accepted = service.accept(
        VoicePrincipal("tenant-quality", "owner-quality"),
        job,
        SpeechReconciliationWorkerPoll(job.job_id, job.attempt_id, job.fencing_epoch, "completed", outcome),
    )
    assert accepted.training is not None
    assert accepted.training.training_job_id == "speech-training-quality"
    assert len(training.calls) == 1
    assert events == ["dataset", "commit", "training"]


class _AdaptationService:
    def __init__(self) -> None:
        self.calls = []

    def admit(self, principal, request, *, idempotency_key):
        self.calls.append((principal, request, idempotency_key))
        return SimpleNamespace(job=object(), status="queued", job_id="speech-training-adapted")


class _DeniedAdaptationService:
    def admit(self, _principal, _request, *, idempotency_key):
        del idempotency_key
        return SimpleNamespace(
            job=None,
            status="denied",
            reason_code="speech_consent_missing",
            job_id="speech-training-denied",
        )


def test_training_adapter_calls_existing_adaptation_admission_with_closed_profile() -> None:
    service = _AdaptationService()
    adapter = SpeechAdaptationTrainingAdmissionAdapter(
        service,
        request_template=_training_profile(),
        clock_ms=lambda: 1_000_000,
    )
    manifest_digest = digest("training-manifest")
    job_id = adapter.admit_dataset(
        VoicePrincipal("tenant-quality", "owner-quality"),
        dataset_id="dataset-quality",
        dataset_version=f"sha256:{manifest_digest}",
        manifest_digest=manifest_digest,
        budget=_training_budget(),
        idempotency_key="quality-training-admission",
    )
    assert job_id == "speech-training-adapted"
    _, request, key = service.calls[0]
    assert request["dataset_version"] == f"sha256:{manifest_digest}"
    assert request["deadline_at_ms"] == 1_030_000
    assert key == "quality-training-admission"

    denied = SpeechAdaptationTrainingAdmissionAdapter(
        _DeniedAdaptationService(),
        request_template=_training_profile(),
        clock_ms=lambda: 1_000_000,
    )
    with pytest.raises(ValueError, match="speech_consent_missing"):
        denied.admit_dataset(
            VoicePrincipal("tenant-quality", "owner-quality"),
            dataset_id="dataset-quality",
            dataset_version=f"sha256:{manifest_digest}",
            manifest_digest=manifest_digest,
            budget=_training_budget(),
            idempotency_key="quality-training-denied",
        )
    with pytest.raises(ValueError, match="training_budget_invalid"):
        adapter.admit_dataset(
            VoicePrincipal("tenant-quality", "owner-quality"),
            dataset_id="dataset-quality",
            dataset_version=f"sha256:{manifest_digest}",
            manifest_digest=manifest_digest,
            budget={"max_wall_seconds": 30},
            idempotency_key="quality-training-invalid-budget",
        )
