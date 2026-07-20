from __future__ import annotations

import pytest

from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.speech_adaptation_job_service import (
    ActiveSpeechConsent,
    AdmittedSpeechDataset,
    InMemorySpeechCapacityLeasePort,
    SpeechAdaptationAdmissionError,
    SpeechAdaptationJobService,
    SpeechPrincipal,
)
from agent.services.speech_adaptation_task_port import SpeechAdaptationTaskReference
from ananta_contracts.speech_adaptation import SpeechAdaptationResult, speech_scope_digest
from tests.speech_adaptation_support import digest


class _Datasets:
    def __init__(self, dataset: AdmittedSpeechDataset) -> None:
        self.dataset = dataset

    def resolve(self, principal, *, dataset_id, dataset_version):
        del principal
        if (dataset_id, dataset_version) == (self.dataset.dataset_id, self.dataset.dataset_version):
            return self.dataset
        return None


class _Consents:
    def __init__(self, consent: ActiveSpeechConsent | None) -> None:
        self.consent = consent

    def current(self, principal, *, scope_digest):
        del principal
        return self.consent if self.consent and self.consent.scope_digest == scope_digest else None


class _Tasks:
    def __init__(self) -> None:
        self.jobs = []
        self.policy_states = []

    def enqueue(self, job, *, tenant_id, owner_subject):
        self.jobs.append((job, tenant_id, owner_subject))
        return SpeechAdaptationTaskReference("speech-task-test", "queued")

    def enqueue_policy_state(self, **values):
        self.policy_states.append(values)
        return SpeechAdaptationTaskReference("speech-task-policy", values["status"])

    def cancel(self, task_id, *, reason_code):
        del task_id, reason_code


class _NoCapacity:
    def try_acquire(self, **values):
        del values
        return None

    def release(self, lease_id):
        del lease_id


class _ToggleCapacity:
    def __init__(self) -> None:
        self.available = False
        self.delegate = InMemorySpeechCapacityLeasePort(capacity=1, lease_seconds=60)

    def try_acquire(self, **values):
        if not self.available:
            return None
        return self.delegate.try_acquire(**values)

    def release(self, lease_id):
        self.delegate.release(lease_id)


def _request(now: int = 1_000_000, *, policy: str = "queued") -> dict:
    return {
        "dataset_id": "speech-dataset-test",
        "dataset_version": "v1",
        "base_model_id": "openvoice-v2-test",
        "pair_id": "pair-test",
        "direction": "sender_to_receiver",
        "speaker_digest": digest("speaker"),
        "backend": "mock",
        "seed": 7,
        "max_steps": 3,
        "batch_size": 1,
        "checkpoint_interval_steps": 2,
        "learning_rate": 0.001,
        "scenario": "success",
        "budget": {
            "max_wall_seconds": 30,
            "max_ram_bytes": 8 * 1024**3,
            "max_vram_bytes": 0,
            "max_disk_bytes": 64 * 1024**2,
            "max_artifact_bytes": 1024 * 1024,
            "max_checkpoints": 4,
            "max_events": 100,
        },
        "deadline_at_ms": now + 60_000,
        "capacity_policy": policy,
    }


def _service(*, dataset=None, consent=None, capacity=None, tasks=None, audit=None):
    now = 1_000_000
    principal = SpeechPrincipal("tenant-test", "owner-test")
    request = _request(now)
    scope = speech_scope_digest(
        pair_id=request["pair_id"],
        direction=request["direction"],
        speaker_digest=request["speaker_digest"],
    )
    admitted = dataset or AdmittedSpeechDataset(
        dataset_id="speech-dataset-test",
        dataset_version="v1",
        tenant_id=principal.tenant_id,
        owner_subject=principal.subject,
        storage_ref="artifact://speech-datasets/test/v1",
        dataset_digest=digest("dataset"),
        split_digest=digest("split"),
        lineage_digest=digest("lineage"),
        train_sample_count=4,
        validation_sample_count=2,
        immutable=True,
    )
    active = consent or ActiveSpeechConsent(
        consent_id="speech-consent-test",
        version=1,
        digest=digest("consent"),
        scope_digest=scope,
        purpose="speech_adaptation_training",
        expires_at_ms=now + 120_000,
        export_allowed=False,
    )
    task_port = tasks or _Tasks()
    service = SpeechAdaptationJobService(
        datasets=_Datasets(admitted),
        consents=_Consents(active),
        capacity=capacity or InMemorySpeechCapacityLeasePort(capacity=1, lease_seconds=60),
        tasks=task_port,
        model_catalog={
            "openvoice-v2-test": {
                "artifact_ref": "artifact://speech-models/openvoice-v2-test",
                "model_digest": digest("model"),
            }
        },
        backend_catalog={"mock": digest("mock-backend-v1")},
        now_ms=lambda: now,
        audit=audit,
    )
    return service, principal, request, task_port


def test_hub_admission_mints_attempt_lease_fence_and_only_hub_task() -> None:
    audit_repository = InMemorySemanticMediaAuditRepository()
    audit = SemanticMediaAuditRecorder(
        SemanticMediaAuditService(audit_repository, clock_ms=lambda: 1_000_000),
        secret=b"speech-training-audit-test-key" * 2,
    )
    service, principal, request, tasks = _service(audit=audit)
    decision = service.admit(principal, request, idempotency_key="speech-admission-0001")

    assert decision.status == "queued"
    assert decision.job is not None
    assert decision.job.attempt.attempt_number == 1
    assert decision.job.fencing.epoch == 1
    assert decision.job.dataset.immutable is True
    assert decision.job.configuration.backend_digest == digest("mock-backend-v1")
    assert tasks.jobs == [(decision.job, principal.tenant_id, principal.subject)]
    assert service.admit(principal, request, idempotency_key="speech-admission-0001") == decision
    rows, _ = audit_repository.page(
        tenant_digest=audit.digest("tenant", principal.tenant_id),
        scope_digest=audit.digest("scope", f"speech-job:{decision.job_id}"),
        after_event_id=None,
        limit=10,
        now_ms=1_000_000,
    )
    assert [(row.event_type, row.transition) for row in rows] == [("speech_training", "queued")]

    changed = {**request, "max_steps": 4}
    with pytest.raises(SpeechAdaptationAdmissionError) as captured:
        service.admit(principal, changed, idempotency_key="speech-admission-0001")
    assert captured.value.reason_code == "speech_idempotency_conflict"


def test_mutable_dataset_or_missing_current_consent_never_creates_task() -> None:
    mutable = AdmittedSpeechDataset(
        dataset_id="speech-dataset-test",
        dataset_version="v1",
        tenant_id="tenant-test",
        owner_subject="owner-test",
        storage_ref="artifact://speech-datasets/test/v1",
        dataset_digest=digest("dataset"),
        split_digest=digest("split"),
        lineage_digest=digest("lineage"),
        train_sample_count=4,
        validation_sample_count=2,
        immutable=False,
    )
    service, principal, request, tasks = _service(dataset=mutable)
    with pytest.raises(SpeechAdaptationAdmissionError) as captured:
        service.admit(principal, request, idempotency_key="speech-admission-0002")
    assert captured.value.reason_code == "speech_dataset_not_admitted"
    assert tasks.jobs == []

    service, principal, request, tasks = _service(
        consent=ActiveSpeechConsent(
            consent_id="missing",
            version=1,
            digest=digest("missing"),
            scope_digest=digest("wrong-scope"),
            purpose="speech_adaptation_training",
            expires_at_ms=2_000_000,
            export_allowed=False,
            granted=False,
        )
    )
    with pytest.raises(SpeechAdaptationAdmissionError) as captured:
        service.admit(principal, request, idempotency_key="speech-admission-0003")
    assert captured.value.reason_code == "speech_consent_missing"
    assert tasks.jobs == []


@pytest.mark.parametrize("policy", ["queued", "dataset_only", "denied"])
def test_capacity_policy_is_explicit_and_never_forces_training(policy: str) -> None:
    tasks = _Tasks()
    service, principal, request, _ = _service(capacity=_NoCapacity(), tasks=tasks)
    request["capacity_policy"] = policy
    decision = service.admit(principal, request, idempotency_key=f"speech-capacity-{policy}")
    assert decision.status == policy
    assert decision.job is None
    assert tasks.jobs == []
    assert tasks.policy_states[0]["status"] == policy


def test_queued_capacity_decision_is_durably_promotable_without_new_job_identity() -> None:
    tasks = _Tasks()
    capacity = _ToggleCapacity()
    service, principal, request, _ = _service(capacity=capacity, tasks=tasks)
    waiting = service.admit(
        principal,
        request,
        idempotency_key="speech-capacity-promote",
    )
    assert waiting.job is None
    assert waiting.admission_request == request

    capacity.available = True
    promoted = service.promote_waiting(principal, waiting.job_id)

    assert promoted.job_id == waiting.job_id
    assert promoted.task_id == waiting.task_id
    assert promoted.job is not None
    assert promoted.reason_code == "speech_training_admitted"
    assert tasks.jobs == [(promoted.job, principal.tenant_id, principal.subject)]


def test_hub_rejects_terminal_result_from_another_fenced_attempt() -> None:
    service, principal, request, _tasks = _service()
    decision = service.admit(
        principal,
        request,
        idempotency_key="speech-result-binding",
    )
    assert decision.job is not None
    result = SpeechAdaptationResult.from_mapping(
        {
            "contract_version": "ananta.speech-adaptation.v1",
            "result_type": "speech_adaptation_result",
            "job_id": "speech-job-foreign",
            "attempt_id": decision.job.attempt.attempt_id,
            "binding_digest": decision.job.binding_digest,
            "fencing_digest": decision.job.fencing.fencing_digest,
            "status": "completed",
            "events_digest": digest("events"),
            "evaluation_report_digest": digest("evaluation"),
            "checkpoint_digest": digest("checkpoint"),
            "artifact": {
                "artifact_id": decision.job.artifact_target.target_id,
                "artifact_ref": decision.job.artifact_target.artifact_ref,
                "sha256": digest("artifact"),
                "size_bytes": 64,
                "media_type": "application/vnd.ananta.speech-adapter",
            },
            "reason_code": None,
        }
    )

    with pytest.raises(SpeechAdaptationAdmissionError) as captured:
        service.accept_result(principal, decision.job_id, result)

    assert captured.value.reason_code == "speech_training_result_binding_mismatch"
