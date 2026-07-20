from __future__ import annotations

import time

import pytest

from agent.services.speech_evidence_admission_policy import SpeechEvidenceAdmissionPolicy
from agent.services.speech_evidence_curation_task_service import SpeechEvidenceCurationTaskService
from tests.speech_evidence_support import (
    AcceptResultPublisher,
    AllowAuthority,
    QueueRecorder,
    digest,
    principal,
    stored_evidence,
)


def _admitted(prefix: str):
    consent_service, _store, consent, record = stored_evidence(prefix, b"curation candidate")
    admission = SpeechEvidenceAdmissionPolicy(authority=AllowAuthority(), consent=consent_service).admit(
        principal(prefix),
        record.evidence_id,
        peer_id=consent.speaker_id,
        speaker_id=consent.speaker_id,
        recipient_id=consent.recipient_id,
        direction=consent.direction,
        data_class="transcript",
        purpose=consent.purpose,
        evidence_signature=digest(f"signature-{prefix}"),
        provenance_digest=digest(f"provenance-{prefix}"),
        source_digest=record.source_digest,
        speaker_scope_digest=record.speaker_scope_digest,
        transcript_authority="human_verified",
        quality_metrics={"duration_ms": 1000, "snr_db": 20.0, "clipping_ratio": 0.0, "silence_ratio": 0.1},
    )
    assert admission.decision == "admitted"
    return consent_service, consent, admission


def _result(task, prefix: str, *, completed_at_ms: int | None = None) -> dict[str, object]:
    return {
        "schema": "ananta.speech-evidence-curation-result.v1",
        "task_id": task.task_id,
        "admission_digest": task.admission_digest,
        "artifact_ref": f"artifact://speech-curation/{task.task_id}/result",
        "artifact_digest": digest(f"artifact-{prefix}"),
        "consent_version": task.consent_version,
        "revocation_epoch": task.revocation_epoch,
        "fencing_token": task.fencing_token,
        "completed_at_ms": completed_at_ms or time.time_ns() // 1_000_000,
    }


class _RecordingPublisher:
    def __init__(self) -> None:
        self.calls = []

    def publish(self, result) -> bool:
        self.calls.append(result)
        return True


def test_exactly_one_hub_task_has_only_opaque_bounded_worker_contract() -> None:
    prefix = "curation-task"
    consent_service, _consent, admission = _admitted(prefix)
    queue = QueueRecorder()
    service = SpeechEvidenceCurationTaskService(
        queue=queue,
        result_port=AcceptResultPublisher(),
        consent=consent_service,
    )
    first, created = service.create(principal(prefix), admission_digest=admission.admission_digest)
    replay, replay_created = service.create(principal(prefix), admission_digest=admission.admission_digest)

    assert created and not replay_created and replay.task_id == first.task_id
    assert len(queue.calls) == 1
    worker = queue.calls[0]["extra_fields"]["worker_execution_context"]["speech_evidence_curation"]
    assert set(worker) == {
        "schema",
        "task_id",
        "parent_task_id",
        "admission_digest",
        "evidence_refs",
        "consent_id",
        "consent_version",
        "revocation_epoch",
        "deadline_epoch_ms",
        "limits",
        "artifact_publish_ref",
        "fencing_token",
    }
    assert not any(name in str(worker).lower() for name in ("credential", "worker_url", "endpoint"))


def test_matching_result_is_published_and_fenced_result_is_rejected() -> None:
    prefix = "curation-result"
    consent_service, consent, admission = _admitted(prefix)
    service = SpeechEvidenceCurationTaskService(
        queue=QueueRecorder(), result_port=AcceptResultPublisher(), consent=consent_service
    )
    task, _ = service.create(principal(prefix), admission_digest=admission.admission_digest)
    result = _result(task, prefix)
    assert service.authorize_result(principal(prefix), result).artifact_digest == result["artifact_digest"]

    other_prefix = "curation-fence"
    other_consent_service, _other_consent, other_admission = _admitted(other_prefix)
    other_service = SpeechEvidenceCurationTaskService(
        queue=QueueRecorder(), result_port=AcceptResultPublisher(), consent=other_consent_service
    )
    other_task, _ = other_service.create(principal(other_prefix), admission_digest=other_admission.admission_digest)
    assert other_service.fence(principal(other_prefix), other_task.task_id, reason_code="speech_consent_revoked")
    result.update(
        task_id=other_task.task_id,
        admission_digest=other_task.admission_digest,
        consent_version=other_task.consent_version,
        revocation_epoch=other_task.revocation_epoch,
        fencing_token=other_task.fencing_token,
    )
    result["artifact_ref"] = f"artifact://speech-curation/{other_task.task_id}/result"
    with pytest.raises(Exception, match="speech_curation_task_fenced"):
        other_service.authorize_result(principal(other_prefix), result)


@pytest.mark.parametrize(
    "reason_code",
    [
        "speech_curation_user_cancelled",
        "speech_curation_lease_lost",
        "speech_curation_worker_restart",
    ],
)
def test_cancel_lease_loss_and_restart_fence_late_worker_results(reason_code: str) -> None:
    prefix = f"curation-fence-{reason_code.rsplit('_', 1)[-1]}"
    consent_service, _consent, admission = _admitted(prefix)
    publisher = _RecordingPublisher()
    service = SpeechEvidenceCurationTaskService(
        queue=QueueRecorder(),
        result_port=publisher,
        consent=consent_service,
    )
    task, _ = service.create(principal(prefix), admission_digest=admission.admission_digest)

    assert service.fence(principal(prefix), task.task_id, reason_code=reason_code)
    with pytest.raises(Exception, match="speech_curation_task_fenced"):
        service.authorize_result(principal(prefix), _result(task, prefix))
    assert publisher.calls == []


def test_stale_consent_and_deadline_each_block_publication() -> None:
    stale_prefix = "curation-stale-consent"
    consent_service, consent, admission = _admitted(stale_prefix)
    stale_publisher = _RecordingPublisher()
    stale_service = SpeechEvidenceCurationTaskService(
        queue=QueueRecorder(),
        result_port=stale_publisher,
        consent=consent_service,
    )
    stale_task, _ = stale_service.create(
        principal(stale_prefix), admission_digest=admission.admission_digest
    )
    consent_service.revoke(
        principal(stale_prefix),
        consent.consent_id,
        expected_version=consent.consent_version,
        contributor_id=consent.speaker_id,
    )
    with pytest.raises(Exception, match="speech_curation_consent_stale"):
        stale_service.authorize_result(
            principal(stale_prefix), _result(stale_task, stale_prefix)
        )
    assert stale_publisher.calls == []

    late_prefix = "curation-deadline"
    now = [1_000_000]
    late_consent_service, _late_consent, late_admission = _admitted(late_prefix)
    late_publisher = _RecordingPublisher()
    late_service = SpeechEvidenceCurationTaskService(
        queue=QueueRecorder(),
        result_port=late_publisher,
        consent=late_consent_service,
        clock_ms=lambda: now[0],
    )
    late_task, _ = late_service.create(
        principal(late_prefix), admission_digest=late_admission.admission_digest
    )
    now[0] = late_task.deadline_epoch_ms + 1
    with pytest.raises(Exception, match="speech_curation_result_late"):
        late_service.authorize_result(
            principal(late_prefix),
            _result(late_task, late_prefix, completed_at_ms=late_task.deadline_epoch_ms + 1),
        )
    assert late_publisher.calls == []
