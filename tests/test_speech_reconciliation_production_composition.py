from __future__ import annotations

import hashlib
import io
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence import (
    SpeechEvidenceAdmissionDB,
    SpeechEvidenceConsentDB,
    SpeechEvidenceDB,
)
from agent.db_models.speech_reconciliation import (
    SpeechReconciliationAttemptDB,
    SpeechReconciliationBudgetLedgerDB,
    SpeechReconciliationJobDB,
)
from agent.repositories.speech_reconciliation import SpeechReconciliationCollectibleAttempt
from agent.services.background.speech_reconciliation_result_collector import (
    SqlSpeechReconciliationResultCollector,
)
from agent.services.ml_intern_speech_dataset_build_service import MlInternSpeechDatasetBuildService
from agent.services.speech_reconciliation_production_composition import (
    AdmittedSpeechAudioScope,
    AdmittedSpeechAudioSource,
    HubSpeechReconciliationArtifactTransfer,
    ManifestBoundSpeechReconciliationExecutionPlan,
    SpeechReconciliationProductionError,
    SqlAdmittedSpeechAudioScope,
    SqlSpeechReconciliationDatasetPublisher,
    SqlTenantResolvingSpeechReconciliationLedgerLookup,
)
from agent.services.speech_reconciliation_result_admission_service import (
    SpeechReconciliationResultAdmission,
    SpeechReconciliationResultAdmissionError,
)
from ananta_contracts.speech_reconciliation import SpeechResourceVector, canonical_sha256
from ananta_contracts.speech_reconciliation_crypto import (
    SpeechReconciliationEpochKeyring,
    open_speech_reconciliation_audio,
)
from tests.speech_evidence_support import (
    AcceptPublisher,
    AllowDatasetConsent,
    manifest_record,
    principal,
)
from tests.speech_reconciliation_support import digest, job_contract, worker_outcome_contract
from voice_runtime.model_manifest import VoiceModelCatalog, VoiceModelManifest


def _wav(duration_ms: int = 100) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16_000)
        target.writeframes(b"\0\0" * (16_000 * duration_ms // 1000))
    return output.getvalue()


class _Scope:
    def __init__(self, job) -> None:
        self.job = job

    def resolve(self, requested):
        assert requested == self.job
        return AdmittedSpeechAudioScope(
            "tenant-production",
            "owner-production",
            {"manifest_digest": requested.input_manifest_digest},
            (
                AdmittedSpeechAudioSource(
                    "speech-evidence-production",
                    "tenant-production",
                    "owner-production",
                    digest("source-production"),
                    requested.source_duration_ms,
                ),
            ),
        )


class _Evidence:
    def encrypted(self, **values):
        assert values["tenant_id"] == "tenant-production"
        return object()


class _Encryption:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def decrypt(self, _envelope, *, security_mode: str) -> bytes:
        assert security_mode == "trusted_compute"
        return self.payload


def test_hub_transfer_builds_canonical_wav_and_shared_attempt_bound_ciphertext() -> None:
    job = job_contract(source_duration_ms=100, deadline_at_ms=time.time_ns() // 1_000_000 + 60_000)
    root_key = b"k" * 32
    transfer = HubSpeechReconciliationArtifactTransfer(
        scopes=_Scope(job),
        evidence=_Evidence(),  # type: ignore[arg-type]
        encryption=_Encryption(_wav()),  # type: ignore[arg-type]
        keyring=SpeechReconciliationEpochKeyring({job.key_epoch: root_key}),
    )
    first = transfer.resolve(job)
    second = transfer.resolve(job)
    first_plaintext = open_speech_reconciliation_audio(
        root_key=root_key,
        artifact=first.artifact,
        job=job,
        ciphertext=first.ciphertext,
    )
    second_plaintext = open_speech_reconciliation_audio(
        root_key=root_key,
        artifact=second.artifact,
        job=job,
        ciphertext=second.ciphertext,
    )
    assert first_plaintext == second_plaintext
    assert first.artifact.content_digest == hashlib.sha256(first_plaintext).hexdigest()
    assert first.artifact.filename == "speech-reconciliation-input.wav"
    with wave.open(io.BytesIO(first_plaintext), "rb") as source:
        assert (source.getframerate(), source.getnchannels(), source.getsampwidth()) == (16_000, 1, 2)


def test_sql_audio_scope_requires_current_tenant_consent_admission_and_lineage() -> None:
    prefix = "reconciliation-sql-scope"
    now = time.time_ns() // 1_000_000
    record = manifest_record(prefix)
    consent_ref = record["consent_refs"][0]
    builder = MlInternSpeechDatasetBuildService(
        publisher=AcceptPublisher(),
        consent_authority=AllowDatasetConsent(),
    )
    manifest, _ = builder.build(
        principal(prefix),
        dataset_id=f"dataset-{prefix}",
        records=[record],
        curation_report_digest=digest(f"report-{prefix}"),
    )
    job = job_contract(
        job_id=f"speech-reconciliation-{prefix}",
        attempt_id=f"speech-reconciliation-attempt-{prefix}",
        consent_id=str(consent_ref["consent_id"]),
        consent_version=int(consent_ref["consent_version"]),
        revocation_epoch=int(consent_ref["revocation_epoch"]),
        input_manifest_digest=str(manifest["manifest_digest"]),
        input_lineage_digest=canonical_sha256(
            [
                {
                    "record_digest": record["record_digest"],
                    "source_digest": record["source_digest"],
                    "duration_ms": record["duration_ms"],
                }
            ]
        ),
        input_artifact_ref=(f"artifact://speech-evidence/manifests/{manifest['manifest_digest']}"),
        source_duration_ms=int(record["duration_ms"]),
        deadline_at_ms=now + 60_000,
    )
    scope_payload = {
        "grants": {"raw_audio_share": True, "dataset_import": True},
        "data_classes": ["audio"],
    }
    evidence_id = f"speech-evidence-{prefix}"
    with Session(engine) as session:
        session.add(
            SpeechEvidenceConsentDB(
                id=job.consent_id,
                tenant_id=f"tenant-{prefix}",
                owner_subject=f"owner-{prefix}",
                speaker_id=f"speaker-{prefix}",
                recipient_id=f"recipient-{prefix}",
                pair_id=f"pair-{prefix}",
                session_id=f"session-{prefix}",
                session_epoch=1,
                direction="sender_to_receiver",
                purpose="speech_reconciliation",
                scope_digest=digest(f"scope-{prefix}"),
                consent_digest=str(consent_ref["consent_digest"]),
                scope_payload=scope_payload,
                required_signers=[f"speaker-{prefix}"],
                signature_digests={f"speaker-{prefix}": digest(f"signature-{prefix}")},
                state="active",
                consent_version=job.consent_version,
                revocation_epoch=job.revocation_epoch,
                issued_at_ms=now - 1_000,
                expires_at_ms=now + 60_000,
            )
        )
        session.add(
            SpeechEvidenceDB(
                id=evidence_id,
                tenant_id=f"tenant-{prefix}",
                owner_subject=f"owner-{prefix}",
                pair_id=f"pair-{prefix}",
                session_id=f"session-{prefix}",
                session_epoch=1,
                speaker_scope_digest=digest(f"speaker-scope-{prefix}"),
                utterance_family_id=str(record["utterance_family_id"]),
                evidence_class="audio",
                purpose="speech_reconciliation",
                consent_id=job.consent_id,
                consent_version=job.consent_version,
                revocation_epoch=job.revocation_epoch,
                content_digest=digest(f"content-{prefix}"),
                cipher_content_digest=digest(f"cipher-content-{prefix}"),
                source_digest=str(record["source_digest"]),
                provenance_digest=digest(f"provenance-{prefix}"),
                key_id=f"speech-key-{prefix}",
                nonce=b"n" * 12,
                ciphertext=b"ciphertext-with-auth-tag",
                byte_count=100,
                retention_seconds=60,
                state="admitted",
                admission_digest=digest(f"admission-{prefix}"),
                expires_at_ms=now + 60_000,
                created_at_ms=now,
                updated_at_ms=now,
            )
        )
        session.add(
            SpeechEvidenceAdmissionDB(
                tenant_id=f"tenant-{prefix}",
                owner_subject=f"owner-{prefix}",
                evidence_id=evidence_id,
                evidence_digest=digest(f"content-{prefix}"),
                admission_digest=digest(f"admission-{prefix}"),
                policy_version="speech-evidence-admission-v1",
                decision="admitted",
                reason_codes=[],
                metrics={},
                consent_version=job.consent_version,
                revocation_epoch=job.revocation_epoch,
                created_at_ms=now,
            )
        )
        session.add(
            SpeechReconciliationJobDB(
                id=job.job_id,
                tenant_id=f"tenant-{prefix}",
                owner_subject=f"owner-{prefix}",
                pair_scope_digest=digest(f"job-scope-{prefix}"),
                idempotency_key_digest=digest(f"idempotency-{prefix}"),
                request_digest=digest(f"request-{prefix}"),
                state="running",
                stage=job.stage,
                consent_id=job.consent_id,
                consent_version=job.consent_version,
                revocation_epoch=job.revocation_epoch,
                input_manifest_digest=job.input_manifest_digest,
                input_lineage_digest=job.input_lineage_digest,
                input_artifact_ref=job.input_artifact_ref,
                policy_digest=job.policy_digest,
                budget_plan={},
                source_duration_ms=job.source_duration_ms,
                max_compute_factor=job.max_compute_factor,
                ledger_sequence=job.ledger_sequence,
                key_epoch=job.key_epoch,
                deadline_at_ms=job.deadline_at_ms,
                active_attempt_id=job.attempt_id,
                fencing_epoch=job.fencing_epoch,
                created_at_ms=now,
                updated_at_ms=now,
            )
        )
        # SQLAlchemy orders inserts by relationship, and these models declare
        # none, so the mappers flush in name order and the attempt would be
        # written before the job it points at. Flush the parent first.
        session.flush()
        session.add(
            SpeechReconciliationAttemptDB(
                id=job.attempt_id,
                job_id=job.job_id,
                tenant_id=f"tenant-{prefix}",
                owner_subject=f"owner-{prefix}",
                attempt_number=1,
                state="running",
                worker_id_digest=digest(f"worker-{prefix}"),
                worker_capability_digest=digest(f"capability-{prefix}"),
                location_digest=digest(f"location-{prefix}"),
                resource_profile_digest=digest(f"resource-{prefix}"),
                fencing_token_digest=job.fencing_token_digest,
                fencing_epoch=job.fencing_epoch,
                lease_expires_at_ms=now + 30_000,
                deadline_at_ms=job.deadline_at_ms,
                last_heartbeat_at_ms=now,
                created_at_ms=now,
                updated_at_ms=now,
            )
        )
        session.commit()
    resolved = SqlAdmittedSpeechAudioScope(clock_ms=lambda: now).resolve(job)
    assert resolved.tenant_id == f"tenant-{prefix}"
    assert [source.evidence_id for source in resolved.sources] == [evidence_id]
    with Session(engine) as session:
        admission = session.exec(
            select(SpeechEvidenceAdmissionDB).where(
                SpeechEvidenceAdmissionDB.evidence_id == evidence_id,
            )
        ).one()
        admission.evidence_digest = digest(f"tampered-admission-{prefix}")
        session.add(admission)
        session.commit()
    with pytest.raises(
        SpeechReconciliationProductionError,
        match="speech_reconciliation_admitted_audio_not_found",
    ):
        SqlAdmittedSpeechAudioScope(clock_ms=lambda: now).resolve(job)


def test_execution_plan_uses_manifest_model_id_and_immutable_revision(tmp_path: Path) -> None:
    model = VoiceModelManifest(
        model_id="speech-model-pinned",
        engine="whisper_cpp",
        revision="engine@0123456789abcdef",
        license="MIT",
        quantization="f16",
        languages=("de",),
        files=(("model.bin", f"sha256:{'1' * 64}"),),
        manifest_digest=f"sha256:{digest('catalog')}",
        model_root=tmp_path,
    )
    catalog = VoiceModelCatalog({"whisper_cpp": model}, digest=digest("catalog"))
    resolver = ManifestBoundSpeechReconciliationExecutionPlan(
        catalog,
        model_ids=(model.model_id,),
        variant_ids=("original", "normalized"),
        max_parallel_passes=2,
    )
    plan = resolver.resolve(job_contract(max_compute_factor=1))
    assert len(plan.passes) == 1
    assert plan.passes[0].model_id == model.model_id
    assert plan.passes[0].model_revision == model.revision


def test_tenant_resolving_ledger_requires_current_attempt_and_sequence() -> None:
    now = time.time_ns() // 1_000_000
    job = job_contract(
        job_id="speech-reconciliation-ledger-production",
        attempt_id="speech-reconciliation-attempt-ledger-production",
        source_duration_ms=60_000,
        ledger_sequence=2,
        deadline_at_ms=now + 60_000,
    )
    zero = SpeechResourceVector()
    allocated = SpeechResourceVector(wall_time_ms=60_000, cpu_time_ms=60_000)
    with Session(engine) as session:
        session.add(
            SpeechReconciliationJobDB(
                id=job.job_id,
                tenant_id="tenant-ledger-production",
                owner_subject="owner-ledger-production",
                pair_scope_digest=digest("scope-ledger-production"),
                idempotency_key_digest=digest("idempotency-ledger-production"),
                request_digest=digest("request-ledger-production"),
                state="running",
                stage=job.stage,
                consent_id=job.consent_id,
                consent_version=job.consent_version,
                revocation_epoch=job.revocation_epoch,
                input_manifest_digest=job.input_manifest_digest,
                input_lineage_digest=job.input_lineage_digest,
                input_artifact_ref=job.input_artifact_ref,
                policy_digest=job.policy_digest,
                budget_plan={},
                source_duration_ms=job.source_duration_ms,
                max_compute_factor=job.max_compute_factor,
                ledger_sequence=job.ledger_sequence,
                key_epoch=job.key_epoch,
                deadline_at_ms=job.deadline_at_ms,
                active_attempt_id=job.attempt_id,
                fencing_epoch=job.fencing_epoch,
                created_at_ms=now,
                updated_at_ms=now,
            )
        )
        # SQLAlchemy orders inserts by relationship, and these models declare
        # none, so the mappers flush in name order and the attempt would be
        # written before the job it points at. Flush the parent first.
        session.flush()
        session.add(
            SpeechReconciliationAttemptDB(
                id=job.attempt_id,
                job_id=job.job_id,
                tenant_id="tenant-ledger-production",
                owner_subject="owner-ledger-production",
                attempt_number=1,
                state="running",
                worker_id_digest=digest("worker-ledger-production"),
                worker_capability_digest=digest("capability-ledger-production"),
                location_digest=digest("location-ledger-production"),
                resource_profile_digest=digest("resource-ledger-production"),
                fencing_token_digest=job.fencing_token_digest,
                fencing_epoch=job.fencing_epoch,
                lease_expires_at_ms=now + 30_000,
                deadline_at_ms=job.deadline_at_ms,
                last_heartbeat_at_ms=now,
                created_at_ms=now,
                updated_at_ms=now,
            )
        )
        session.add(
            SpeechReconciliationBudgetLedgerDB(
                job_id=job.job_id,
                attempt_id=job.attempt_id,
                tenant_id="tenant-ledger-production",
                owner_subject="owner-ledger-production",
                fencing_epoch=job.fencing_epoch,
                sequence=job.ledger_sequence,
                stage=job.stage,
                source_duration_ms=job.source_duration_ms,
                compute_factor=job.max_compute_factor,
                allocated=allocated.to_dict(),
                reserved=zero.to_dict(),
                consumed=zero.to_dict(),
                remaining=allocated.to_dict(),
            )
        )
        session.commit()
    ledger = SqlTenantResolvingSpeechReconciliationLedgerLookup().get(job_id=job.job_id)
    assert ledger is not None
    assert (ledger.attempt_id, ledger.sequence, ledger.fencing_epoch) == (
        job.attempt_id,
        job.ledger_sequence,
        job.fencing_epoch,
    )
    assert (
        SqlTenantResolvingSpeechReconciliationLedgerLookup(
            clock_ms=lambda: now + 31_000,
        ).get(job_id=job.job_id)
        is None
    )


class _AttemptStore:
    def __init__(self, row: SpeechReconciliationCollectibleAttempt) -> None:
        self.row = row
        self.heartbeats = []
        self.pauses = []
        self.cancellations = []

    def list_collectible_attempts(self, **_values):
        return (self.row,)

    def heartbeat(self, **values):
        self.heartbeats.append(values)
        return object()

    def pause_active_attempt(self, **values):
        self.pauses.append(values)
        return True

    def cancel_active_attempt(self, **values):
        self.cancellations.append(values)
        return True


class _Worker:
    def __init__(self) -> None:
        self.cancelled = []

    def cancel(self, job):
        self.cancelled.append(job.job_id)
        return "cancel_requested"


class _Collector:
    def __init__(self, disposition: str) -> None:
        self.disposition = disposition

    def collect(self, _principal, _job):
        return SpeechReconciliationResultAdmission(
            self.disposition,
            "speech_reconciliation_test",
            None,
        )


class _Tasks:
    def __init__(self) -> None:
        self.finished: list[tuple[str, str, str]] = []

    @staticmethod
    def parent_task_id(job_id: str) -> str:
        return f"parent:{job_id}"

    @staticmethod
    def attempt_task_id(job_id: str, attempt_id: str, fencing_epoch: int) -> str:
        return f"attempt:{job_id}:{attempt_id}:{fencing_epoch}"

    def finish(self, task_id: str, *, status: str, reason_code: str) -> None:
        self.finished.append((task_id, status, reason_code))


def test_db_collector_heartbeats_pending_and_fences_disabled_attempts() -> None:
    now = time.time_ns() // 1_000_000
    job = job_contract(deadline_at_ms=now + 60_000)
    row = SpeechReconciliationCollectibleAttempt(
        "tenant-collector",
        "owner-collector",
        "running",
        job,
        3,
        now + 30_000,
    )
    attempts = _AttemptStore(row)
    clock = iter((now, now + 1_000))
    pending = SqlSpeechReconciliationResultCollector(
        attempts=attempts,
        worker=_Worker(),  # type: ignore[arg-type]
        collector=_Collector("pending"),  # type: ignore[arg-type]
        feature_enabled=lambda: True,
        clock_ms=lambda: next(clock),
    )
    assert pending.run_once().pending == 1
    assert attempts.heartbeats[0]["expected_version"] == 3
    assert attempts.heartbeats[0]["now_ms"] == now + 1_000
    assert attempts.heartbeats[0]["lease_expires_at_ms"] == now + 31_000

    worker = _Worker()
    attempts = _AttemptStore(row)
    disabled = SqlSpeechReconciliationResultCollector(
        attempts=attempts,
        worker=worker,  # type: ignore[arg-type]
        collector=_Collector("pending"),  # type: ignore[arg-type]
        feature_enabled=lambda: False,
        clock_ms=lambda: now,
    )
    assert disabled.run_once().paused == 1
    assert worker.cancelled == [job.job_id]
    assert attempts.pauses[0]["reason_code"] == "speech_reconciliation_feature_disabled"


def test_db_collector_finishes_explicitly_cancelled_attempts() -> None:
    now = time.time_ns() // 1_000_000
    job = job_contract(deadline_at_ms=now + 60_000)
    attempts = _AttemptStore(
        SpeechReconciliationCollectibleAttempt(
            "tenant-collector-cancel",
            "owner-collector-cancel",
            "cancel_requested",
            job,
            4,
            now + 30_000,
        )
    )
    worker = _Worker()
    service = SqlSpeechReconciliationResultCollector(
        attempts=attempts,
        worker=worker,  # type: ignore[arg-type]
        collector=_Collector("pending"),  # type: ignore[arg-type]
        feature_enabled=lambda: True,
        clock_ms=lambda: now,
    )

    summary = service.run_once()

    assert summary.cancelled == 1
    assert worker.cancelled == [job.job_id]
    assert attempts.pauses == []
    assert attempts.cancellations[0]["reason_code"] == "speech_reconciliation_cancelled"


def test_db_collector_closes_only_old_attempt_task_when_hub_extends_wave() -> None:
    now = time.time_ns() // 1_000_000
    job = job_contract(deadline_at_ms=now + 60_000)
    attempts = _AttemptStore(
        SpeechReconciliationCollectibleAttempt(
            "tenant-collector-extension",
            "owner-collector-extension",
            "running",
            job,
            5,
            now + 30_000,
        )
    )
    tasks = _Tasks()
    service = SqlSpeechReconciliationResultCollector(
        attempts=attempts,
        worker=_Worker(),  # type: ignore[arg-type]
        collector=_Collector("extended"),  # type: ignore[arg-type]
        feature_enabled=lambda: True,
        tasks=tasks,  # type: ignore[arg-type]
        clock_ms=lambda: now,
    )

    summary = service.run_once()

    assert summary.extended == 1
    assert tasks.finished == [
        (
            tasks.attempt_task_id(job.job_id, job.attempt_id, job.fencing_epoch),
            "completed",
            "speech_reconciliation_test",
        )
    ]
    assert all(task_id != tasks.parent_task_id(job.job_id) for task_id, _, _ in tasks.finished)


class _Datasets:
    def __init__(self) -> None:
        self.calls = []

    def get_by_digest(self, _principal, digest_value):
        return {
            "dataset_id": "dataset-production",
            "manifest_digest": digest_value,
            "records": [{"record_digest": digest("record-production")}],
        }

    def build(self, _principal, **values):
        self.calls.append(values)
        return {"manifest_digest": digest("output-production")}, True


def test_dataset_publisher_extends_input_manifest_without_training_side_effect() -> None:
    datasets = _Datasets()
    publisher = SqlSpeechReconciliationDatasetPublisher(datasets=datasets)  # type: ignore[arg-type]
    job = job_contract()
    outcome = worker_outcome_contract(job)
    result = publisher.publish(
        SimpleNamespace(),
        job=job,
        outcome=outcome,
        transcript=SimpleNamespace(text="Hallo Welt"),
    )
    assert result.manifest_digest == digest("output-production")
    assert (result.resolved_count, result.unresolved_count, result.rejected_count) == (1, 0, 0)
    assert datasets.calls[0]["parent_digest"] == job.input_manifest_digest
    assert datasets.calls[0]["authority"] == "hub"
    summary = datasets.calls[0]["curation_summary"]
    assert summary["trainable"] is True
    assert summary["resolved_count"] == summary["input_count"] == 1


def test_dataset_publisher_binds_partial_regions_without_false_resolved_records() -> None:
    datasets = _Datasets()
    publisher = SqlSpeechReconciliationDatasetPublisher(datasets=datasets)  # type: ignore[arg-type]
    job = job_contract()
    region_id = digest("unresolved-production-region")
    outcome = worker_outcome_contract(
        job,
        status="partial",
        unresolved_count=1,
        unresolved_region_ids=[region_id],
        unresolved_high_quality_conflict_count=1,
        publishable=False,
        transcript=None,
        reason_code="speech_reconciliation_conflicts_unresolved",
    )
    result = publisher.publish(SimpleNamespace(), job=job, outcome=outcome, transcript=None)
    assert (result.resolved_count, result.unresolved_count) == (0, 1)
    assert result.materialization is not None and result.materialization.trainable is False
    record = datasets.calls[0]["records"][0]
    assert record["curation_status"] == "unresolved"
    assert record["reconciliation_outcome"]["unresolved_region_ids"] == [region_id]
    summary = datasets.calls[0]["curation_summary"]
    assert summary["resolved_count"] == 0
    assert summary["unresolved_count"] == summary["input_count"] == 1


def test_dataset_publisher_rejects_unbound_legacy_partial_regions() -> None:
    publisher = SqlSpeechReconciliationDatasetPublisher(datasets=_Datasets())  # type: ignore[arg-type]
    job = job_contract()
    outcome = worker_outcome_contract(
        job,
        status="partial",
        unresolved_count=1,
        unresolved_high_quality_conflict_count=1,
        publishable=False,
        transcript=None,
        reason_code="speech_reconciliation_conflicts_unresolved",
    )
    with pytest.raises(
        SpeechReconciliationResultAdmissionError,
        match="speech_reconciliation_unresolved_regions_unbound",
    ):
        publisher.publish(SimpleNamespace(), job=job, outcome=outcome, transcript=None)
