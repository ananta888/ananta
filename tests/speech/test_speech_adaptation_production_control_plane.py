from __future__ import annotations

import base64
import hashlib
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from flask import Flask
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import SpeechAdaptationArtifactDB
from agent.repositories.speech_adaptation import (
    SqlSpeechAdaptationArtifactRepository,
    SqlSpeechAdaptationCapacityLeasePort,
    SqlSpeechAdaptationDecisionStore,
)
from agent.routes.speech_adaptation_control import speech_adaptation_control_bp
from agent.services.ml_intern_speech_dataset_build_service import (
    MlInternSpeechDatasetBuildService,
)
from agent.services.speech_adaptation_job_service import (
    SpeechAdaptationDecisionConflict,
    SpeechAdmissionDecision,
    SpeechPrincipal,
)
from agent.services.speech_adaptation_production_composition import (
    HubSpeechAdaptationWorkerControl,
    SqlSpeechAdaptationCurrentAuthority,
    SqlSpeechAdapterRegistrationAdmissionPort,
    SqlSpeechConsentAdmissionPort,
    SqlSpeechDatasetAdmissionPort,
)
from agent.services.speech_evidence_consent_service import SpeechEvidenceConsentService
from ananta_contracts.speech_adaptation import SpeechAdaptationResult, speech_scope_digest
from tests.speech_adaptation_support import speech_job
from tests.speech_evidence_support import (
    AcceptPublisher,
    AllowDatasetConsent,
    consent_payload,
    digest,
    manifest_record,
)
from tests.speech_evidence_support import (
    principal as voice_principal,
)


def _decision() -> tuple[SpeechPrincipal, SpeechAdmissionDecision]:
    job = speech_job()
    principal = SpeechPrincipal("tenant-speech-control", "owner-speech-control")
    return principal, SpeechAdmissionDecision(
        job.job_id,
        "speech-adaptation-test-task",
        "queued",
        "speech_training_admitted",
        job,
        digest("speech-control-request"),
    )


def test_sql_decision_store_survives_reconstruction_and_fences_cas(app) -> None:
    del app
    principal, decision = _decision()
    first = SqlSpeechAdaptationDecisionStore()
    saved, replayed = first.create(
        principal,
        idempotency_digest=digest("speech-control-idempotency"),
        decision=decision,
    )
    restored = SqlSpeechAdaptationDecisionStore().get(principal, decision.job_id)
    assert not replayed
    assert restored == saved
    assert restored is not None and restored.job == decision.job
    assert SqlSpeechAdaptationDecisionStore().get(SpeechPrincipal("foreign", "foreign"), decision.job_id) is None

    running = SpeechAdmissionDecision(
        saved.job_id,
        saved.task_id,
        "running",
        "speech_training_running",
        saved.job,
        saved.request_digest,
    )
    transitioned = first.replace(
        principal,
        running,
        expected_statuses=frozenset({"queued"}),
    )
    assert transitioned.status == "running"
    stale = SpeechAdmissionDecision(
        saved.job_id,
        saved.task_id,
        "failed",
        "speech_stale_writer",
        saved.job,
        saved.request_digest,
    )
    with pytest.raises(SpeechAdaptationDecisionConflict, match="speech_job_state_conflict"):
        first.replace(principal, stale, expected_statuses=frozenset({"queued"}))


def test_sql_capacity_is_cluster_bounded_idempotent_and_expiry_reclaimable(app) -> None:
    del app
    port = SqlSpeechAdaptationCapacityLeasePort(capacity=1, lease_seconds=10)
    now = 1_000_000
    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = list(
            pool.map(
                lambda job_id: port.try_acquire(
                    job_id=job_id,
                    deadline_at_ms=now + 20_000,
                    now_ms=now,
                ),
                ("speech-job-capacity-a", "speech-job-capacity-b"),
            )
        )
    assert sum(value is not None for value in leases) == 1
    winner = next(value for value in leases if value is not None)
    winner_job = "speech-job-capacity-a" if leases[0] is not None else "speech-job-capacity-b"
    assert (
        port.try_acquire(
            job_id=winner_job,
            deadline_at_ms=now + 20_000,
            now_ms=now,
        )
        == winner
    )
    reclaimed = port.try_acquire(
        job_id="speech-job-capacity-after-expiry",
        deadline_at_ms=now + 40_000,
        now_ms=now + 10_001,
    )
    assert reclaimed is not None and reclaimed.lease_id != winner.lease_id


def test_hub_artifact_repository_is_digest_bound_and_idempotent(app, tmp_path) -> None:
    del app
    principal, decision = _decision()
    jobs = SqlSpeechAdaptationDecisionStore()
    jobs.create(
        principal,
        idempotency_digest=digest("speech-artifact-idempotency"),
        decision=decision,
    )
    row = jobs.transition_worker_state(
        decision.job_id,
        expected_statuses=frozenset({"queued"}),
        status="dispatching",
        reason_code="speech_training_dispatching",
    )
    assert decision.job is not None
    payload = b"deterministic-adapter-bytes"
    sha256 = hashlib.sha256(payload).hexdigest()
    repository = SqlSpeechAdaptationArtifactRepository(tmp_path / "artifacts")
    values = {
        "job": row,
        "artifact_id": decision.job.artifact_target.target_id,
        "attempt_id": decision.job.attempt.attempt_id,
        "artifact_ref": decision.job.artifact_target.artifact_ref,
        "sha256": sha256,
        "size_bytes": len(payload),
        "media_type": "application/vnd.ananta.speech-adapter",
    }
    created = repository.publish(**values, stream=io.BytesIO(payload))
    replay = repository.publish(**values, stream=io.BytesIO(payload))
    assert replay.id == created.id
    assert replay.storage_ref.startswith("hub-artifact://speech-adaptation/")
    with pytest.raises(SpeechAdaptationDecisionConflict, match="speech_artifact_receipt_conflict"):
        repository.publish(
            **{**values, "sha256": digest("different")},
            stream=io.BytesIO(payload),
        )

    checkpoint_body = b"deterministic-checkpoint"
    checkpoint_digest = hashlib.sha256(checkpoint_body).hexdigest()
    checkpoint_ref = (
        f"artifact://speech-checkpoints/{decision.job.job_id}/{decision.job.attempt.attempt_id}/{checkpoint_digest}"
    )
    repository.publish(
        job=row,
        artifact_id=f"speech-checkpoint-{checkpoint_digest[:32]}",
        attempt_id=decision.job.attempt.attempt_id,
        artifact_ref=checkpoint_ref,
        sha256=checkpoint_digest,
        size_bytes=len(checkpoint_body),
        media_type="application/vnd.ananta.speech-checkpoint",
        stream=io.BytesIO(checkpoint_body),
    )
    second_checkpoint = b"second-checkpoint-is-not-admitted"
    second_checkpoint_digest = hashlib.sha256(second_checkpoint).hexdigest()
    with pytest.raises(
        SpeechAdaptationDecisionConflict,
        match="speech_artifact_receipt_conflict",
    ):
        repository.publish(
            job=row,
            artifact_id=f"speech-checkpoint-{second_checkpoint_digest[:32]}",
            attempt_id=decision.job.attempt.attempt_id,
            artifact_ref=(
                f"artifact://speech-checkpoints/{decision.job.job_id}/"
                f"{decision.job.attempt.attempt_id}/{second_checkpoint_digest}"
            ),
            sha256=second_checkpoint_digest,
            size_bytes=len(second_checkpoint),
            media_type="application/vnd.ananta.speech-checkpoint",
            stream=io.BytesIO(second_checkpoint),
        )
    evaluation_body = json.dumps(
        {"passed": True, "report": "content-free-test"},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    evaluation_digest = hashlib.sha256(evaluation_body).hexdigest()
    repository.publish(
        job=row,
        artifact_id=f"speech-evaluation-{evaluation_digest[:32]}",
        attempt_id=decision.job.attempt.attempt_id,
        artifact_ref=(
            f"artifact://speech-evaluations/{decision.job.job_id}/{decision.job.attempt.attempt_id}/{evaluation_digest}"
        ),
        sha256=evaluation_digest,
        size_bytes=len(evaluation_body),
        media_type="application/vnd.ananta.speech-evaluation+json",
        stream=io.BytesIO(evaluation_body),
    )
    result = SpeechAdaptationResult.from_mapping(
        {
            "contract_version": "ananta.speech-adaptation.v1",
            "result_type": "speech_adaptation_result",
            "job_id": decision.job.job_id,
            "attempt_id": decision.job.attempt.attempt_id,
            "binding_digest": decision.job.binding_digest,
            "fencing_digest": decision.job.fencing.fencing_digest,
            "status": "completed",
            "events_digest": digest("artifact-events"),
            "evaluation_report_digest": evaluation_digest,
            "checkpoint_digest": checkpoint_digest,
            "artifact": {
                "artifact_id": created.id,
                "artifact_ref": created.artifact_ref,
                "sha256": created.sha256,
                "size_bytes": created.size_bytes,
                "media_type": created.media_type,
            },
            "reason_code": None,
        }
    )
    repository.verify_and_commit(principal, decision.job, result)
    repository.verify_and_commit(principal, decision.job, result)
    jobs.replace(
        principal,
        SpeechAdmissionDecision(
            decision.job_id,
            decision.task_id,
            "completed",
            "speech_training_completed",
            decision.job,
            decision.request_digest,
            result=result,
        ),
        expected_statuses=frozenset({"dispatching"}),
        result=result,
    )
    assert SqlSpeechAdaptationDecisionStore().get(principal, decision.job_id).result == result
    assert repository.read_evaluation(
        principal,
        decision.job,
        evaluation_digest,
    ) == {"passed": True, "report": "content-free-test"}
    registration_payload = {
        "adapter_id": result.artifact.artifact_id,
        "pair_id": decision.job.scope.pair_id,
        "direction": decision.job.scope.direction,
        "speaker_digest": decision.job.scope.speaker_digest,
        "scope_digest": decision.job.scope.scope_digest,
        "base_model_id": decision.job.base_model.model_id,
        "base_model_digest": decision.job.base_model.model_digest,
        "backend": decision.job.configuration.backend,
        "backend_digest": decision.job.configuration.backend_digest,
        "dataset_digest": decision.job.dataset.dataset_digest,
        "split_digest": decision.job.dataset.split_digest,
        "consent_digest": decision.job.consent.consent_digest,
        "consent_expires_at_ms": decision.job.consent.expires_at_ms,
        "artifact_ref": result.artifact.artifact_ref,
        "artifact_sha256": result.artifact.sha256,
        "artifact_size_bytes": result.artifact.size_bytes,
    }
    registration = SqlSpeechAdapterRegistrationAdmissionPort()
    assert registration.verify_registration(
        principal,
        registration_payload,
        evaluation_report_digest=result.evaluation_report_digest,
    ) == (True, None)
    assert registration.verify_registration(
        principal,
        {**registration_payload, "pair_id": "foreign-pair"},
        evaluation_report_digest=result.evaluation_report_digest,
    ) == (False, "speech_adapter_training_binding_mismatch")
    with Session(engine) as session:
        states = {
            value.media_type: value.state
            for value in session.exec(
                select(SpeechAdaptationArtifactDB).where(SpeechAdaptationArtifactDB.job_id == decision.job.job_id)
            ).all()
        }
    assert states == {
        "application/vnd.ananta.speech-adapter": "committed",
        "application/vnd.ananta.speech-checkpoint": "checkpointed",
        "application/vnd.ananta.speech-evaluation+json": "evaluated",
    }


def test_internal_callback_auth_authority_and_artifact_ingress(app, tmp_path) -> None:
    del app
    principal, decision = _decision()
    jobs = SqlSpeechAdaptationDecisionStore()
    jobs.create(
        principal,
        idempotency_digest=digest("speech-callback-idempotency"),
        decision=decision,
    )
    jobs.transition_worker_state(
        decision.job_id,
        expected_statuses=frozenset({"queued"}),
        status="dispatching",
        reason_code="speech_training_dispatching",
    )

    class _Authority:
        def verify_current(self, _principal, _job, *, phase):
            assert phase in {"before_audio_access", "before_artifact_publish"}
            return True, None

    class _Composition:
        callback_token = "callback-token-with-at-least-32-characters"
        authority = _Authority()
        artifacts = SqlSpeechAdaptationArtifactRepository(tmp_path / "callback-artifacts")

        def __init__(self):
            self.jobs = jobs

    flask_app = Flask(__name__)
    flask_app.register_blueprint(speech_adaptation_control_bp)
    composition = _Composition()
    flask_app.extensions["speech_adaptation_worker_control"] = HubSpeechAdaptationWorkerControl(composition)
    client = flask_app.test_client()
    assert client.post("/internal/v1/speech-adaptation-control/authority").status_code == 401
    assert decision.job is not None
    headers = {"Authorization": "Bearer callback-token-with-at-least-32-characters"}
    authority_payload = {
        "job_id": decision.job_id,
        "attempt_id": decision.job.attempt.attempt_id,
        "binding_digest": decision.job.binding_digest,
        "fencing_digest": decision.job.fencing.fencing_digest,
        "phase": "before_audio_access",
    }
    response = client.post(
        "/internal/v1/speech-adaptation-control/authority",
        json=authority_payload,
        headers=headers,
    )
    assert response.status_code == 200 and response.get_json() == {
        "active": True,
        "reason_code": None,
    }

    body = b"callback-adapter"
    metadata = {
        "job_id": decision.job_id,
        "attempt_id": decision.job.attempt.attempt_id,
        "fencing_digest": decision.job.fencing.fencing_digest,
        "binding_digest": decision.job.binding_digest,
        "target_id": decision.job.artifact_target.target_id,
        "target_ref": decision.job.artifact_target.artifact_ref,
        "sha256": hashlib.sha256(body).hexdigest(),
        "size_bytes": len(body),
        "media_type": "application/vnd.ananta.speech-adapter",
    }
    encoded = (
        base64.urlsafe_b64encode(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )
    response = client.post(
        "/internal/v1/speech-adaptation-control/artifacts",
        data=body,
        headers={**headers, "X-Ananta-Artifact-Metadata": encoded},
        content_type="application/octet-stream",
    )
    assert response.status_code == 201
    assert response.get_json()["artifact_ref"] == decision.job.artifact_target.artifact_ref
    stored_path = (
        tmp_path / "callback-artifacts" / decision.job.job_id / decision.job.attempt.attempt_id / metadata["sha256"]
    )
    assert stored_path.is_file()
    composition.artifacts.reject_attempt(principal, decision.job)
    assert not stored_path.exists()


def test_sql_dataset_and_consent_adapters_revalidate_every_bound_consent(app) -> None:
    del app
    prefix = "speech-training-production"
    now = time.time_ns() // 1_000_000
    raw = consent_payload(
        prefix,
        grants={"dataset_import": True, "training": True, "export": False},
        now_ms=now,
        expires_in_ms=3_600_000,
    )
    raw["purpose"] = "speech_adaptation_training"
    raw["trainer_locations"] = ["ananta-local-speech-training-worker"]
    consent_service = SpeechEvidenceConsentService(clock_ms=lambda: now)
    consent = consent_service.grant(voice_principal(prefix), raw)
    records = [manifest_record(f"{prefix}-{index}", group_suffix=str(index)) for index in range(4)]
    speaker_digest = digest("training-speaker")
    for record in records:
        record["contributors"] = [speaker_digest]
        for provenance in record["field_provenance"].values():
            provenance["contributor_digest"] = speaker_digest
        record["consent_refs"] = [
            {
                "consent_id": consent.consent_id,
                "consent_version": consent.consent_version,
                "revocation_epoch": consent.revocation_epoch,
                "consent_digest": consent.consent_digest,
            }
        ]
    manifest, _created = MlInternSpeechDatasetBuildService(
        publisher=AcceptPublisher(),
        consent_authority=AllowDatasetConsent(),
    ).build(
        voice_principal(prefix),
        dataset_id=f"dataset-{prefix}",
        records=records,
        curation_report_digest=digest(f"report-{prefix}"),
    )
    principal = SpeechPrincipal(f"tenant-{prefix}", f"owner-{prefix}")
    datasets = SqlSpeechDatasetAdmissionPort()
    dataset = datasets.resolve(
        principal,
        dataset_id=str(manifest["dataset_id"]),
        dataset_version=str(manifest["version"]),
    )
    assert dataset is not None
    assert dataset.train_sample_count > 0 and dataset.validation_sample_count > 0
    scope_digest = speech_scope_digest(
        pair_id=f"pair-{prefix}",
        direction="sender_to_receiver",
        speaker_digest=speaker_digest,
    )
    consents = SqlSpeechConsentAdmissionPort(
        trainer_location="ananta-local-speech-training-worker",
        clock_ms=lambda: now,
    )
    active = consents.current_for_dataset(
        principal,
        scope_digest=scope_digest,
        pair_id=f"pair-{prefix}",
        direction="sender_to_receiver",
        speaker_digest=speaker_digest,
        dataset=dataset,
    )
    assert active is not None and active.granted
    assert consents.current(principal, scope_digest=scope_digest) is None

    authority = SqlSpeechAdaptationCurrentAuthority(
        datasets=datasets,
        consents=consents,
        model_catalog={},
        backend_catalog={},
        clock_ms=lambda: now,
    )
    # A job not bound to the same SQL model/backend catalog fails closed.
    allowed, reason = authority.verify_current(
        principal,
        speech_job(),
        phase="before_audio_access",
    )
    assert not allowed and reason in {
        "speech_dataset_binding_stale",
        "speech_consent_binding_stale",
        "speech_model_binding_stale",
        "speech_training_binding_expired",
    }
