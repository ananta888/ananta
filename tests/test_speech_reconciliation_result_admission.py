from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent.services.speech_reconciliation_result_admission_service import (
    HubSpeechReconciliationCurrentAuthority,
    HubSpeechReconciliationResultAdmissionService,
    PublishedSpeechReconciliationDataset,
    SpeechReconciliationResultAdmissionError,
)
from agent.services.speech_reconciliation_worker_port import SpeechReconciliationWorkerPoll
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_reconciliation_worker import SpeechReconciliationWorkerOutcome
from tests.speech_reconciliation_support import (
    digest,
    job_contract,
    worker_outcome_contract,
    worker_outcome_payload,
)


class _Authority:
    def __init__(self, error: str | None = None) -> None:
        self.error = error
        self.calls: list[str] = []

    def authorize(self, _principal, _job, *, phase: str) -> None:
        self.calls.append(phase)
        if self.error is not None:
            raise SpeechReconciliationResultAdmissionError(self.error, status_code=403)


class _Repository:
    def __init__(self) -> None:
        self.checkpoints = []
        self.completions = []

    def save_checkpoint(self, **values):
        self.checkpoints.append(values)
        return object()

    def complete(self, **values):
        self.completions.append(values)
        return object()


class _Ledger:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls = []

    def authorize_publication(self, **values) -> bool:
        self.calls.append(values)
        return self.allowed


class _Publisher:
    def __init__(self) -> None:
        self.calls = []

    def publish(self, principal, **values):
        self.calls.append((principal, values))
        return PublishedSpeechReconciliationDataset(
            digest("published-dataset"),
            "artifact://speech-datasets/reconciliation/published-v1",
            1,
            0,
            0,
            0,
        )


def _poll(job, outcome: SpeechReconciliationWorkerOutcome | None = None):
    resolved = outcome or worker_outcome_contract(job)
    return SpeechReconciliationWorkerPoll(
        job.job_id,
        job.attempt_id,
        job.fencing_epoch,
        resolved.status,
        resolved,
    )


def _service(*, authority=None, repository=None, ledger=None, publisher=None):
    components = (
        authority or _Authority(),
        repository or _Repository(),
        ledger or _Ledger(),
        publisher or _Publisher(),
    )
    return (
        HubSpeechReconciliationResultAdmissionService(
            authority=components[0],
            repository=components[1],
            ledger=components[2],
            publisher=components[3],
        ),
        components,
    )


def test_publishable_result_is_checkpointed_validated_and_hub_materialized() -> None:
    job = job_contract()
    principal = VoicePrincipal("tenant-result", "owner-result")
    service, (authority, repository, ledger, publisher) = _service()
    admission = service.accept(principal, job, _poll(job))
    assert admission.disposition == "completed"
    assert admission.result is not None and admission.result.status == "dataset_only_completed"
    assert admission.result.dataset_manifest_digest == digest("published-dataset")
    assert authority.calls == ["admission", "checkpoint", "publication", "commit"]
    assert len(repository.checkpoints) == len(repository.completions) == 1
    assert repository.completions[0]["publication_authorized"] is True
    assert len(ledger.calls) == 2 and len(publisher.calls) == 1


def test_stale_fence_consent_deadline_and_budget_never_reach_publisher() -> None:
    job = job_contract()
    principal = VoicePrincipal("tenant-stale", "owner-stale")
    stale_job = replace(job, attempt_id="speech-reconciliation-attempt-stale")
    stale_outcome = worker_outcome_contract(stale_job)
    publisher = _Publisher()
    service, _ = _service(publisher=publisher)
    with pytest.raises(SpeechReconciliationResultAdmissionError) as stale:
        service.accept(principal, job, _poll(job, stale_outcome))
    assert stale.value.reason_code == "speech_reconciliation_worker_result_binding_mismatch"

    for reason in ("speech_reconciliation_consent_stale", "speech_reconciliation_result_late"):
        authority = _Authority(reason)
        denied, _ = _service(authority=authority, publisher=publisher)
        with pytest.raises(SpeechReconciliationResultAdmissionError) as error:
            denied.accept(principal, job, _poll(job))
        assert error.value.reason_code == reason

    no_budget, _ = _service(ledger=_Ledger(False), publisher=publisher)
    with pytest.raises(SpeechReconciliationResultAdmissionError) as budget:
        no_budget.accept(principal, job, _poll(job))
    assert budget.value.reason_code == "speech_reconciliation_budget_publication_denied"
    assert not publisher.calls


def test_failed_cancelled_and_unresolved_partial_cannot_publish() -> None:
    job = job_contract()
    principal = VoicePrincipal("tenant-terminal", "owner-terminal")
    for status in ("failed", "cancelled"):
        repository = _Repository()
        publisher = _Publisher()
        service, _ = _service(repository=repository, publisher=publisher)
        outcome = SpeechReconciliationWorkerOutcome.failure(
            job,
            status=status,
            reason_code=f"speech_reconciliation_{status}",
        )
        admission = service.accept(principal, job, _poll(job, outcome))
        assert admission.result is not None and admission.result.status == status
        assert repository.completions[0]["publication_authorized"] is False
        assert not publisher.calls

    repository = _Repository()
    publisher = _Publisher()
    service, _ = _service(repository=repository, publisher=publisher)
    partial = worker_outcome_contract(
        job,
        status="partial",
        publishable=False,
        transcript=None,
        unresolved_count=1,
        reason_code="speech_reconciliation_conflicts_unresolved",
    )
    admission = service.accept(principal, job, _poll(job, partial))
    assert admission.disposition == "checkpointed" and admission.result is None
    assert len(repository.checkpoints) == 1 and not repository.completions and not publisher.calls


def test_tampered_transcript_is_rejected_without_content_in_error() -> None:
    job = job_contract()
    payload = worker_outcome_payload(job)
    transcript = dict(payload["transcript"])
    transcript["text"] = "Hallo GEHEIM"
    payload["transcript"] = transcript
    outcome = SpeechReconciliationWorkerOutcome.from_mapping(payload)
    publisher = _Publisher()
    service, _ = _service(publisher=publisher)
    with pytest.raises(SpeechReconciliationResultAdmissionError) as error:
        service.accept(VoicePrincipal("tenant-tamper", "owner-tamper"), job, _poll(job, outcome))
    assert error.value.reason_code == "speech_reconciliation_transcript_token_invented"
    assert "GEHEIM" not in str(error.value) and not publisher.calls


def test_concrete_authority_checks_persisted_attempt_consent_and_deadline() -> None:
    job = job_contract(deadline_at_ms=2_000)
    principal = VoicePrincipal("tenant-authority", "owner-authority")
    record = SimpleNamespace(
        state="running",
        active_attempt_id=job.attempt_id,
        fencing_epoch=job.fencing_epoch,
        ledger_sequence=job.ledger_sequence,
        deadline_at_ms=job.deadline_at_ms,
    )
    consent = SimpleNamespace(
        tenant_id=principal.tenant_id,
        owner_subject=principal.subject,
        state="active",
        expires_at_ms=3_000,
        consent_version=job.consent_version,
        revocation_epoch=job.revocation_epoch,
        purpose="speech_reconciliation",
        grants={"raw_audio_share": True, "dataset_import": True},
        data_classes=("audio",),
    )

    class _Jobs:
        current = record

        def get_job(self, **_values):
            return self.current

    class _Consents:
        def get(self, _principal, _consent_id):
            return consent

    jobs = _Jobs()
    authority = HubSpeechReconciliationCurrentAuthority(
        jobs=jobs,
        consents=_Consents(),
        clock_ms=lambda: 1_000,
    )
    authority.authorize(principal, job, phase="publication")
    jobs.current = SimpleNamespace(**{**record.__dict__, "fencing_epoch": job.fencing_epoch + 1})
    with pytest.raises(SpeechReconciliationResultAdmissionError) as stale:
        authority.authorize(principal, job, phase="publication")
    assert stale.value.reason_code == "speech_reconciliation_result_lost_fence"

    jobs.current = record
    late = HubSpeechReconciliationCurrentAuthority(
        jobs=jobs,
        consents=_Consents(),
        clock_ms=lambda: 2_000,
    )
    with pytest.raises(SpeechReconciliationResultAdmissionError) as expired:
        late.authorize(principal, job, phase="commit")
    assert expired.value.reason_code == "speech_reconciliation_result_late"
