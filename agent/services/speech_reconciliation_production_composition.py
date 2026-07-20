"""Production Hub composition for isolated speech reconciliation attempts.

This module is deliberately Hub-only.  It resolves governed database state,
opens admitted evidence inside the Hub trust boundary, creates one canonical
PCM WAV bundle and seals it for an already fenced worker attempt.  Workers
receive no database credentials and cannot discover inputs or models.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence import (
    SpeechDatasetManifestDB,
    SpeechEvidenceAdmissionDB,
    SpeechEvidenceConsentDB,
    SpeechEvidenceDB,
)
from agent.db_models.speech_reconciliation import (
    SpeechReconciliationAttemptDB,
    SpeechReconciliationBudgetLedgerDB,
    SpeechReconciliationJobDB,
)
from agent.repositories.speech_evidence import SpeechEvidenceRepository
from agent.services.ml_intern_speech_dataset_build_service import (
    MlInternSpeechDatasetBuildService,
    SpeechDatasetManifestError,
)
from agent.services.ml_intern_speech_dataset_port import MlInternSpeechDatasetPort
from agent.services.ml_intern_speech_reconciled_dataset_service import (
    MlInternSpeechReconciledDatasetService,
    ReconciledDatasetCandidate,
)
from agent.services.semantic_media_audit_service import SemanticMediaAuditPort
from agent.services.speech_evidence_admission_policy import SpeechEvidenceAdmissionPolicy
from agent.services.speech_evidence_encryption_port import (
    SpeechEvidenceEncryptionPort,
    get_speech_evidence_encryption_port,
)
from agent.services.speech_reconciliation_result_admission_service import (
    PublishedSpeechReconciliationDataset,
    SpeechReconciliationDatasetPublicationPort,
    SpeechReconciliationPublicationLedgerPort,
    SpeechReconciliationResultAdmissionError,
)
from agent.services.speech_reconciliation_worker_port import (
    HubSpeechReconciliationAttemptDispatcher,
    SpeechReconciliationArtifactTransferPort,
    SpeechReconciliationAudioUpload,
    SpeechReconciliationExecutionPlanPort,
    SpeechReconciliationLedgerLookupPort,
    SpeechReconciliationWorkerPort,
    SpeechReconciliationWorkerTransportError,
)
from ananta_contracts.speech_reconciliation import (
    SpeechReconciliationBudgetLedger,
    SpeechReconciliationJob,
    SpeechResourceVector,
    canonical_sha256,
)
from ananta_contracts.speech_reconciliation_crypto import (
    NONCE_BYTES,
    TAG_BYTES,
    SpeechReconciliationCryptoError,
    SpeechReconciliationEpochKeyring,
    seal_speech_reconciliation_audio,
)
from ananta_contracts.speech_reconciliation_worker import (
    MAX_AUDIO_PLAINTEXT_BYTES,
    MAX_DECODED_PCM_BYTES,
    SpeechReconciliationAudioArtifact,
    SpeechReconciliationExecutionPlan,
)
from voice_runtime.model_manifest import VoiceModelCatalog
from voice_runtime.preprocessing.audio_decode import (
    AudioDecodeError,
    AudioDecodeLimits,
    AudioDecoder,
    DecodedPcmAudio,
    SafeAudioDecoder,
)

_MAX_INPUT_RECORDS = 10_000
_MAX_SOURCE_PAYLOAD_BYTES = 16 * 1024 * 1024
_MAX_SOURCE_DURATION_MS = 60 * 60 * 1000
_SAFE_VARIANTS = frozenset({"original", "normalized", "high_pass", "speech_safe"})


class SpeechReconciliationProductionError(SpeechReconciliationWorkerTransportError):
    """Content-free production-composition error."""

    def __init__(self, reason_code: str, *, retryable: bool = False) -> None:
        super().__init__(reason_code, status_code=409, retryable=retryable)


@dataclass(frozen=True, slots=True)
class AdmittedSpeechAudioSource:
    evidence_id: str
    tenant_id: str
    owner_subject: str
    source_digest: str
    declared_duration_ms: int


@dataclass(frozen=True, slots=True)
class AdmittedSpeechAudioScope:
    tenant_id: str
    owner_subject: str
    manifest: Mapping[str, object]
    sources: tuple[AdmittedSpeechAudioSource, ...]


class SpeechReconciliationAudioScopePort(Protocol):
    def resolve(self, job: SpeechReconciliationJob) -> AdmittedSpeechAudioScope: ...


class SqlAdmittedSpeechAudioScope:
    """Resolve exactly one admitted audio row for every deduplicated source."""

    def __init__(self, *, clock_ms=None, manifest_validator=None) -> None:
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._manifest_validator = manifest_validator or MlInternSpeechDatasetBuildService()

    def resolve(self, job: SpeechReconciliationJob) -> AdmittedSpeechAudioScope:
        now = self._clock_ms()
        with Session(engine) as session:
            current = session.exec(
                select(SpeechReconciliationJobDB, SpeechReconciliationAttemptDB)
                .join(
                    SpeechReconciliationAttemptDB,
                    SpeechReconciliationAttemptDB.id == SpeechReconciliationJobDB.active_attempt_id,
                )
                .where(
                    SpeechReconciliationJobDB.id == job.job_id,
                    SpeechReconciliationJobDB.state == "running",
                    SpeechReconciliationJobDB.active_attempt_id == job.attempt_id,
                    SpeechReconciliationJobDB.fencing_epoch == job.fencing_epoch,
                    SpeechReconciliationJobDB.input_manifest_digest == job.input_manifest_digest,
                    SpeechReconciliationAttemptDB.state == "running",
                    SpeechReconciliationAttemptDB.fencing_token_digest == job.fencing_token_digest,
                    SpeechReconciliationAttemptDB.lease_expires_at_ms > now,
                )
            ).first()
            if current is None:
                raise SpeechReconciliationProductionError("speech_reconciliation_input_scope_lost_fence")
            job_row, _attempt = current
            manifest_row = session.exec(
                select(SpeechDatasetManifestDB).where(
                    SpeechDatasetManifestDB.tenant_id == job_row.tenant_id,
                    SpeechDatasetManifestDB.owner_subject == job_row.owner_subject,
                    SpeechDatasetManifestDB.manifest_digest == job.input_manifest_digest,
                    SpeechDatasetManifestDB.status == "active",
                )
            ).first()
            if manifest_row is None:
                raise SpeechReconciliationProductionError("speech_reconciliation_input_manifest_not_found")
            manifest = dict(manifest_row.manifest_payload or {})
            try:
                self._manifest_validator.validate(manifest)
            except SpeechDatasetManifestError as exc:
                raise SpeechReconciliationProductionError("speech_reconciliation_input_manifest_invalid") from exc
            records = manifest.get("records")
            if not isinstance(records, list) or not 1 <= len(records) <= _MAX_INPUT_RECORDS:
                raise SpeechReconciliationProductionError("speech_reconciliation_input_manifest_invalid")
            expected_ref = f"artifact://speech-evidence/manifests/{job.input_manifest_digest}"
            if (
                manifest.get("manifest_digest") != job.input_manifest_digest
                or manifest.get("version") != f"sha256:{job.input_manifest_digest}"
                or job.input_artifact_ref != expected_ref
            ):
                raise SpeechReconciliationProductionError("speech_reconciliation_input_manifest_binding_mismatch")

            source_durations: dict[str, int] = {}
            source_families: dict[str, str] = {}
            lineage_rows: list[dict[str, object]] = []
            consent_digests: set[str] = set()
            for raw in records:
                if not isinstance(raw, Mapping):
                    raise SpeechReconciliationProductionError("speech_reconciliation_input_manifest_invalid")
                data_classes = raw.get("data_classes")
                field_provenance = raw.get("field_provenance")
                audio_provenance = field_provenance.get("audio") if isinstance(field_provenance, Mapping) else None
                refs = raw.get("consent_refs")
                source_digest = str(raw.get("source_digest") or "")
                duration = raw.get("duration_ms")
                family = str(raw.get("utterance_family_id") or "")
                if (
                    not isinstance(data_classes, list)
                    or "audio" not in data_classes
                    or not isinstance(audio_provenance, Mapping)
                    or audio_provenance.get("source_digest") != source_digest
                    or not _digest(source_digest)
                    or type(duration) is not int
                    or not 1 <= duration <= _MAX_SOURCE_DURATION_MS
                    or not family.startswith("utterance-v1:")
                    or not isinstance(refs, list)
                    or not refs
                    or any(
                        not isinstance(ref, Mapping)
                        or ref.get("consent_id") != job.consent_id
                        or ref.get("consent_version") != job.consent_version
                        or ref.get("revocation_epoch") != job.revocation_epoch
                        or not _digest(str(ref.get("consent_digest") or ""))
                        for ref in refs
                    )
                ):
                    raise SpeechReconciliationProductionError("speech_reconciliation_input_manifest_scope_invalid")
                previous = source_durations.setdefault(source_digest, duration)
                previous_family = source_families.setdefault(source_digest, family)
                consent_digests.update(str(ref["consent_digest"]) for ref in refs)
                lineage_rows.append(
                    {
                        "record_digest": str(raw.get("record_digest") or ""),
                        "source_digest": source_digest,
                        "duration_ms": duration,
                    }
                )
                if previous != duration or previous_family != family:
                    raise SpeechReconciliationProductionError("speech_reconciliation_input_manifest_source_conflict")
            if (
                sum(source_durations.values()) != job.source_duration_ms
                or canonical_sha256(sorted(lineage_rows, key=lambda value: str(value["record_digest"])))
                != job.input_lineage_digest
                or len(consent_digests) != 1
            ):
                raise SpeechReconciliationProductionError("speech_reconciliation_input_duration_binding_mismatch")

            consent = session.exec(
                select(SpeechEvidenceConsentDB).where(
                    SpeechEvidenceConsentDB.id == job.consent_id,
                    SpeechEvidenceConsentDB.tenant_id == job_row.tenant_id,
                    SpeechEvidenceConsentDB.owner_subject == job_row.owner_subject,
                )
            ).first()
            scope_payload = dict(consent.scope_payload or {}) if consent is not None else {}
            if consent is None or (
                consent.state != "active"
                or consent.expires_at_ms <= now
                or consent.consent_version != job.consent_version
                or consent.revocation_epoch != job.revocation_epoch
                or consent.consent_digest not in consent_digests
                or consent.purpose != "speech_reconciliation"
                or dict(scope_payload.get("grants") or {}).get("raw_audio_share") is not True
                or dict(scope_payload.get("grants") or {}).get("dataset_import") is not True
                or "audio" not in set(scope_payload.get("data_classes") or ())
            ):
                raise SpeechReconciliationProductionError("speech_reconciliation_consent_stale")

            sources: list[AdmittedSpeechAudioSource] = []
            # Manifest record order is immutable and is the only available
            # utterance ordering contract. Digest sorting would scramble the
            # audio chronology even though it is deterministic.
            for source_digest in source_durations:
                rows = session.exec(
                    select(SpeechEvidenceDB)
                    .join(
                        SpeechEvidenceAdmissionDB,
                        SpeechEvidenceAdmissionDB.evidence_id == SpeechEvidenceDB.id,
                    )
                    .where(
                        SpeechEvidenceDB.tenant_id == job_row.tenant_id,
                        SpeechEvidenceDB.owner_subject == job_row.owner_subject,
                        SpeechEvidenceDB.source_digest == source_digest,
                        SpeechEvidenceDB.utterance_family_id == source_families[source_digest],
                        SpeechEvidenceDB.evidence_class == "audio",
                        SpeechEvidenceDB.purpose == "speech_reconciliation",
                        SpeechEvidenceDB.consent_id == job.consent_id,
                        SpeechEvidenceDB.consent_version == job.consent_version,
                        SpeechEvidenceDB.revocation_epoch == job.revocation_epoch,
                        SpeechEvidenceDB.state == "admitted",
                        SpeechEvidenceDB.expires_at_ms > now,
                        SpeechEvidenceAdmissionDB.tenant_id == job_row.tenant_id,
                        SpeechEvidenceAdmissionDB.owner_subject == job_row.owner_subject,
                        SpeechEvidenceAdmissionDB.evidence_digest == SpeechEvidenceDB.content_digest,
                        SpeechEvidenceAdmissionDB.admission_digest == SpeechEvidenceDB.admission_digest,
                        SpeechEvidenceAdmissionDB.policy_version == SpeechEvidenceAdmissionPolicy.VERSION,
                        SpeechEvidenceAdmissionDB.decision == "admitted",
                        SpeechEvidenceAdmissionDB.consent_version == job.consent_version,
                        SpeechEvidenceAdmissionDB.revocation_epoch == job.revocation_epoch,
                    )
                    .limit(2)
                ).all()
                if len(rows) != 1:
                    raise SpeechReconciliationProductionError(
                        "speech_reconciliation_admitted_audio_ambiguous"
                        if rows
                        else "speech_reconciliation_admitted_audio_not_found"
                    )
                evidence = rows[0]
                sources.append(
                    AdmittedSpeechAudioSource(
                        evidence.id,
                        job_row.tenant_id,
                        job_row.owner_subject,
                        source_digest,
                        source_durations[source_digest],
                    )
                )
        return AdmittedSpeechAudioScope(
            job_row.tenant_id,
            job_row.owner_subject,
            manifest,
            tuple(sources),
        )


class HubSpeechReconciliationArtifactTransfer(SpeechReconciliationArtifactTransferPort):
    """Decrypt governed inputs and seal one deterministic canonical WAV."""

    def __init__(
        self,
        *,
        scopes: SpeechReconciliationAudioScopePort,
        evidence: SpeechEvidenceRepository,
        encryption: SpeechEvidenceEncryptionPort,
        keyring: SpeechReconciliationEpochKeyring,
        decoder_factory=None,
    ) -> None:
        self._scopes = scopes
        self._evidence = evidence
        self._encryption = encryption
        self._keyring = keyring
        self._decoder_factory = decoder_factory or (lambda limits: SafeAudioDecoder(limits=limits))

    def resolve(self, job: SpeechReconciliationJob) -> SpeechReconciliationAudioUpload:
        scope = self._scopes.resolve(job)
        pcm_parts: list[bytes] = []
        actual_duration_ms = 0
        for source in scope.sources:
            try:
                envelope = self._evidence.encrypted(
                    tenant_id=source.tenant_id,
                    owner_subject=source.owner_subject,
                    evidence_id=source.evidence_id,
                )
                plaintext = self._encryption.decrypt(
                    envelope,
                    security_mode="trusted_compute",
                )
                decoder: AudioDecoder = self._decoder_factory(
                    AudioDecodeLimits(
                        max_encoded_bytes=_MAX_SOURCE_PAYLOAD_BYTES,
                        max_decoded_pcm_bytes=min(
                            MAX_DECODED_PCM_BYTES,
                            max(2, source.declared_duration_ms * 16_000 * 2 // 1000),
                        ),
                        max_duration_ms=source.declared_duration_ms,
                        max_channels=2,
                        max_sample_rate_hz=96_000,
                        target_sample_rate_hz=16_000,
                        ffmpeg_timeout_sec=60,
                    )
                )
                decoded = decoder.decode(filename="source", payload=plaintext)
            except AudioDecodeError as exc:
                raise SpeechReconciliationProductionError("speech_reconciliation_admitted_audio_decode_failed") from exc
            except SpeechReconciliationProductionError:
                raise
            except Exception as exc:
                raise SpeechReconciliationProductionError(
                    _safe_reason_code(
                        getattr(exc, "reason_code", None),
                        "speech_reconciliation_admitted_audio_unavailable",
                    ),
                    retryable=bool(getattr(exc, "retryable", False)),
                ) from exc
            if (
                decoded.sample_rate_hz != 16_000
                or decoded.channels != 1
                or decoded.sample_width_bytes != 2
                or not decoded.pcm_s16le
                or len(decoded.pcm_s16le) % 2
                or decoded.duration_ms > source.declared_duration_ms
                or decoded.duration_ms * 100 < source.declared_duration_ms * 95
            ):
                raise SpeechReconciliationProductionError("speech_reconciliation_admitted_audio_duration_mismatch")
            pcm_parts.append(decoded.pcm_s16le)
            actual_duration_ms += decoded.duration_ms
        if not pcm_parts or actual_duration_ms > job.source_duration_ms:
            raise SpeechReconciliationProductionError("speech_reconciliation_audio_bundle_duration_invalid")
        pcm = b"".join(pcm_parts)
        canonical_audio = DecodedPcmAudio(
            filename="speech-reconciliation-input.wav",
            pcm_s16le=pcm,
            sample_rate_hz=16_000,
            duration_ms=len(pcm) * 1000 // (16_000 * 2),
            source_format="wav",
        )
        wav = canonical_audio.to_wav_bytes()
        if len(pcm) > MAX_DECODED_PCM_BYTES or len(wav) > MAX_AUDIO_PLAINTEXT_BYTES:
            raise SpeechReconciliationProductionError("speech_reconciliation_audio_bundle_size_limit")
        # Decryption/normalization can be expensive. Re-read authority after
        # that work so an expired lease, revocation, retention cleanup or
        # manifest supersession cannot be sent to a worker on stale facts.
        current_scope = self._scopes.resolve(job)
        if (
            current_scope.tenant_id != scope.tenant_id
            or current_scope.owner_subject != scope.owner_subject
            or current_scope.sources != scope.sources
        ):
            raise SpeechReconciliationProductionError("speech_reconciliation_input_scope_changed")
        content_digest = hashlib.sha256(wav).hexdigest()
        ciphertext_bytes = len(wav) + NONCE_BYTES + TAG_BYTES
        placeholder = SpeechReconciliationAudioArtifact.from_mapping(
            {
                "artifact_ref": job.input_artifact_ref,
                "transport_digest": "0" * 64,
                "content_digest": content_digest,
                "filename": "speech-reconciliation-input.wav",
                "content_type": "audio/wav",
                "ciphertext_bytes": ciphertext_bytes,
                "plaintext_bytes": len(wav),
                "decoded_pcm_bytes": len(pcm),
                "duration_ms": canonical_audio.duration_ms,
                "key_epoch": job.key_epoch,
            }
        )
        try:
            root_key = self._keyring.resolve(
                key_epoch=job.key_epoch,
                artifact_ref=job.input_artifact_ref,
            )
            ciphertext = seal_speech_reconciliation_audio(
                root_key=root_key,
                artifact=placeholder,
                job=job,
                plaintext=wav,
            )
        except SpeechReconciliationCryptoError as exc:
            raise SpeechReconciliationProductionError(
                exc.reason_code,
                retryable=exc.retryable,
            ) from exc
        artifact = SpeechReconciliationAudioArtifact.from_mapping(
            {
                **placeholder.to_dict(),
                "transport_digest": hashlib.sha256(ciphertext).hexdigest(),
            }
        )
        return SpeechReconciliationAudioUpload(artifact, ciphertext)


class SqlTenantResolvingSpeechReconciliationLedgerLookup(SpeechReconciliationLedgerLookupPort):
    """Resolve the latest ledger through the tenant-bound current job row."""

    def __init__(self, *, clock_ms=None) -> None:
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    def get(self, *, job_id: str) -> SpeechReconciliationBudgetLedger | None:
        now = self._clock_ms()
        with Session(engine) as session:
            current = session.exec(
                select(SpeechReconciliationJobDB, SpeechReconciliationAttemptDB)
                .join(
                    SpeechReconciliationAttemptDB,
                    SpeechReconciliationAttemptDB.id == SpeechReconciliationJobDB.active_attempt_id,
                )
                .where(
                    SpeechReconciliationJobDB.id == job_id,
                    SpeechReconciliationJobDB.state == "running",
                    SpeechReconciliationJobDB.deadline_at_ms > now,
                    SpeechReconciliationJobDB.fencing_epoch == SpeechReconciliationAttemptDB.fencing_epoch,
                    SpeechReconciliationAttemptDB.state == "running",
                    SpeechReconciliationAttemptDB.lease_expires_at_ms > now,
                )
            ).first()
            if current is None:
                return None
            job, attempt = current
            row = session.exec(
                select(SpeechReconciliationBudgetLedgerDB)
                .where(
                    SpeechReconciliationBudgetLedgerDB.job_id == job.id,
                    SpeechReconciliationBudgetLedgerDB.tenant_id == job.tenant_id,
                    SpeechReconciliationBudgetLedgerDB.owner_subject == job.owner_subject,
                    SpeechReconciliationBudgetLedgerDB.attempt_id == attempt.id,
                    SpeechReconciliationBudgetLedgerDB.fencing_epoch == attempt.fencing_epoch,
                    SpeechReconciliationBudgetLedgerDB.sequence == job.ledger_sequence,
                )
                .limit(1)
            ).first()
        if row is None:
            return None
        return SpeechReconciliationBudgetLedger.from_mapping(
            {
                "contract_version": "ananta.speech-reconciliation.v1",
                "job_id": row.job_id,
                "attempt_id": row.attempt_id,
                "fencing_epoch": row.fencing_epoch,
                "sequence": row.sequence,
                "stage": row.stage,
                "source_duration_ms": row.source_duration_ms,
                "compute_factor": row.compute_factor,
                "allocated": dict(row.allocated or {}),
                "reserved": dict(row.reserved or {}),
                "consumed": dict(row.consumed or {}),
                "remaining": dict(row.remaining or {}),
            }
        )


class ManifestBoundSpeechReconciliationExecutionPlan(SpeechReconciliationExecutionPlanPort):
    """Create a closed pass plan from verified local model manifests only."""

    def __init__(
        self,
        catalog: VoiceModelCatalog,
        *,
        model_ids: Sequence[str],
        variant_ids: Sequence[str] = ("original",),
        language: str | None = "de",
        max_parallel_passes: int = 1,
        pass_deadline_ms: int = 600_000,
        factor_lookup: Callable[[SpeechReconciliationJob], int] | None = None,
    ) -> None:
        models = tuple(dict.fromkeys(str(value).strip() for value in model_ids if str(value).strip()))
        variants = tuple(dict.fromkeys(str(value).strip() for value in variant_ids if str(value).strip()))
        if not models or len(models) > 16 or not variants or not set(variants) <= _SAFE_VARIANTS:
            raise SpeechReconciliationProductionError("speech_reconciliation_execution_plan_configuration_invalid")
        self._catalog = catalog
        self._models = tuple(catalog.require_model(model_id) for model_id in models)
        self._variants = variants
        self._language = language
        self._parallel = max_parallel_passes
        self._deadline = pass_deadline_ms
        self._factor_lookup = factor_lookup or (lambda job: min(10, job.max_compute_factor))

    def resolve(self, job: SpeechReconciliationJob) -> SpeechReconciliationExecutionPlan:
        passes = []
        for model in self._models:
            if (
                self._language is not None
                and model.languages
                and not any(value == self._language or value == "multilingual" for value in model.languages)
            ):
                raise SpeechReconciliationProductionError("speech_reconciliation_model_language_not_allowed")
            for variant in self._variants:
                passes.append(
                    {
                        "pass_id": f"pass-{len(passes) + 1:02d}",
                        "model_id": model.model_id,
                        "model_revision": model.revision,
                        "variant_id": variant,
                        "language": self._language,
                    }
                )
        # Only the Hub-persisted current wave factor selects work. The worker
        # still receives the immutable authorization cap in ``job`` and
        # cannot extend this closed plan itself.
        factor = self._factor_lookup(job)
        if type(factor) is not int or not 1 <= factor <= min(job.max_compute_factor, 100):
            raise SpeechReconciliationProductionError("speech_reconciliation_quality_factor_invalid")
        bounded = passes[: max(1, min(len(passes), factor))]
        return SpeechReconciliationExecutionPlan.from_mapping(
            {
                "max_parallel_passes": min(self._parallel, len(bounded)),
                "pass_deadline_ms": self._deadline,
                "passes": bounded,
            }
        )


class SqlSpeechReconciliationWaveFactor:
    """Read the current Hub-owned wave factor under the active fence."""

    def __call__(self, job: SpeechReconciliationJob) -> int:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechReconciliationJobDB).where(
                    SpeechReconciliationJobDB.id == job.job_id,
                    SpeechReconciliationJobDB.state == "running",
                    SpeechReconciliationJobDB.active_attempt_id == job.attempt_id,
                    SpeechReconciliationJobDB.fencing_epoch == job.fencing_epoch,
                    SpeechReconciliationJobDB.ledger_sequence == job.ledger_sequence,
                )
            ).first()
        if row is None:
            raise SpeechReconciliationProductionError("speech_reconciliation_quality_fence_stale")
        return int(row.current_compute_factor)


class SqlTenantResolvingSpeechReconciliationPublicationLedger(SpeechReconciliationPublicationLedgerPort):
    def __init__(self, lookup: SpeechReconciliationLedgerLookupPort | None = None) -> None:
        self._lookup = lookup or SqlTenantResolvingSpeechReconciliationLedgerLookup()

    def authorize_publication(self, *, job_id: str, sequence: int, fencing_epoch: int) -> bool:
        ledger = self._lookup.get(job_id=job_id)
        return bool(
            ledger is not None
            and ledger.sequence == sequence
            and ledger.fencing_epoch == fencing_epoch
            and ledger.reserved == SpeechResourceVector()
        )


class _DatabaseManifestPublisher(MlInternSpeechDatasetPort):
    """The manifest database row is the local content-addressed publication."""

    def publish_manifest(self, **_values: object) -> bool:
        return True


class SqlSpeechReconciliationDatasetPublisher(SpeechReconciliationDatasetPublicationPort):
    """Atomically fence consent and publish one immutable manifest version."""

    def __init__(
        self,
        datasets: MlInternSpeechDatasetBuildService | None = None,
        *,
        materializer: MlInternSpeechReconciledDatasetService | None = None,
        audit: SemanticMediaAuditPort | None = None,
    ) -> None:
        self._datasets = datasets or MlInternSpeechDatasetBuildService(
            publisher=_DatabaseManifestPublisher(),
            audit=audit,
        )
        self._materializer = materializer or MlInternSpeechReconciledDatasetService(self._datasets)

    def publish(self, principal, *, job, outcome, transcript) -> PublishedSpeechReconciliationDataset:
        input_manifest = self._datasets.get_by_digest(principal, job.input_manifest_digest)
        if input_manifest is None:
            raise SpeechReconciliationResultAdmissionError("speech_reconciliation_input_manifest_not_found")
        records = input_manifest.get("records")
        dataset_id = str(input_manifest.get("dataset_id") or "")
        if (
            not dataset_id
            or not isinstance(records, list)
            or not records
            or any(not isinstance(record, Mapping) for record in records)
            or outcome.resolution_hash is None
        ):
            raise SpeechReconciliationResultAdmissionError("speech_reconciliation_dataset_materialization_invalid")
        if outcome.publishable:
            status = "resolved"
            region_ids: tuple[str, ...] = ()
            resolution_id = outcome.resolution_hash
        elif outcome.unresolved_count > 0:
            if outcome.unresolved_region_ids is None or len(outcome.unresolved_region_ids) != outcome.unresolved_count:
                raise SpeechReconciliationResultAdmissionError("speech_reconciliation_unresolved_regions_unbound")
            region_ids = outcome.unresolved_region_ids
            resolution_id = None
            status = "unresolved" if (outcome.unresolved_high_quality_conflict_count or 0) > 0 else "quarantined"
        else:
            status = "rejected"
            region_ids = ()
            resolution_id = None
        try:
            materialized = self._materializer.materialize(
                principal,
                dataset_id=dataset_id,
                candidates=tuple(
                    ReconciledDatasetCandidate(
                        status,
                        dict(record),
                        resolution_id=resolution_id,
                        unresolved_region_ids=region_ids,
                        disposition_reason=outcome.reason_code,
                    )
                    for record in records
                ),
                reconciliation_digest=outcome.resolution_hash,
                parent_digest=job.input_manifest_digest,
                terminal=True,
                authority="hub",
            )
        except (SpeechDatasetManifestError, ValueError) as exc:
            raise SpeechReconciliationResultAdmissionError(
                getattr(exc, "reason_code", "speech_reconciliation_dataset_materialization_invalid"),
                status_code=getattr(exc, "status_code", 422),
            ) from exc
        # Transcript contents have already been provenance-validated by the
        # admission service. They deliberately stay out of the metadata-only
        # manifest; its reconciliation digest binds the resolved output.
        if transcript is not None and not str(transcript.text).strip():
            raise SpeechReconciliationResultAdmissionError("speech_reconciliation_dataset_materialization_invalid")
        manifest = materialized.manifest
        return PublishedSpeechReconciliationDataset(
            manifest_digest=str(manifest["manifest_digest"]),
            artifact_ref=(f"artifact://speech-datasets/reconciliation/{manifest['manifest_digest']}"),
            resolved_count=materialized.resolved_count,
            unresolved_count=materialized.unresolved_count,
            rejected_count=materialized.rejected_count,
            quarantined_count=materialized.quarantined_count,
            materialization=materialized,
        )


def build_default_speech_reconciliation_dispatcher(
    worker: SpeechReconciliationWorkerPort,
    *,
    environment: Mapping[str, str] | None = None,
) -> HubSpeechReconciliationAttemptDispatcher:
    source = os.environ if environment is None else environment
    keyring_path = _required_path(source, "ANANTA_SPEECH_RECONCILIATION_KEYRING_PATH")
    catalog_path = _required_path(source, "ANANTA_SPEECH_RECONCILIATION_MODEL_MANIFEST_PATH")
    model_root = _required_path(source, "ANANTA_SPEECH_RECONCILIATION_MODEL_ROOT")
    keyring = SpeechReconciliationEpochKeyring.from_file(keyring_path)
    catalog = VoiceModelCatalog.load(catalog_path, model_root=model_root, verify_files=True)
    models = _csv(source, "ANANTA_SPEECH_RECONCILIATION_ALLOWED_MODELS")
    variants = _csv(source, "ANANTA_SPEECH_RECONCILIATION_ALLOWED_VARIANTS", default=("original",))
    language_value = str(source.get("ANANTA_SPEECH_RECONCILIATION_LANGUAGE", "de")).strip()
    language = language_value or None
    plan = ManifestBoundSpeechReconciliationExecutionPlan(
        catalog,
        model_ids=models,
        variant_ids=variants,
        language=language,
        max_parallel_passes=_integer(
            source,
            "ANANTA_SPEECH_RECONCILIATION_MAX_PARALLEL",
            1,
            minimum=1,
            maximum=8,
        ),
        pass_deadline_ms=_integer(
            source,
            "ANANTA_SPEECH_RECONCILIATION_PASS_DEADLINE_MS",
            600_000,
            minimum=1_000,
            maximum=3_600_000,
        ),
        factor_lookup=SqlSpeechReconciliationWaveFactor(),
    )
    artifacts = HubSpeechReconciliationArtifactTransfer(
        scopes=SqlAdmittedSpeechAudioScope(),
        evidence=SpeechEvidenceRepository(),
        encryption=get_speech_evidence_encryption_port(),
        keyring=keyring,
    )
    return HubSpeechReconciliationAttemptDispatcher(
        worker=worker,
        artifacts=artifacts,
        ledgers=SqlTenantResolvingSpeechReconciliationLedgerLookup(),
        plans=plan,
    )


def _required_path(source: Mapping[str, str], name: str) -> Path:
    raw = str(source.get(name) or "").strip()
    if not raw:
        raise SpeechReconciliationProductionError("speech_reconciliation_production_path_missing")
    unresolved = Path(raw).absolute()
    try:
        resolved = unresolved.resolve(strict=True)
    except OSError as exc:
        raise SpeechReconciliationProductionError("speech_reconciliation_production_path_invalid") from exc
    if unresolved.is_symlink() or resolved != unresolved:
        raise SpeechReconciliationProductionError("speech_reconciliation_production_path_invalid")
    return resolved


def _csv(
    source: Mapping[str, str],
    name: str,
    *,
    default: tuple[str, ...] = (),
) -> tuple[str, ...]:
    values = tuple(dict.fromkeys(item.strip() for item in str(source.get(name) or "").split(",") if item.strip()))
    return values or default


def _integer(
    source: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(str(source.get(name) or default), 10)
    except (TypeError, ValueError) as exc:
        raise SpeechReconciliationProductionError("speech_reconciliation_production_integer_invalid") from exc
    if not minimum <= value <= maximum:
        raise SpeechReconciliationProductionError("speech_reconciliation_production_integer_invalid")
    return value


def _digest(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _safe_reason_code(value: object, fallback: str) -> str:
    candidate = str(value or "")
    if (
        candidate.startswith("speech_reconciliation_")
        and len(candidate) <= 128
        and all(character.isalnum() or character == "_" for character in candidate)
    ):
        return candidate
    return fallback


__all__ = [
    "AdmittedSpeechAudioScope",
    "AdmittedSpeechAudioSource",
    "HubSpeechReconciliationArtifactTransfer",
    "ManifestBoundSpeechReconciliationExecutionPlan",
    "SpeechReconciliationAudioScopePort",
    "SpeechReconciliationProductionError",
    "SqlAdmittedSpeechAudioScope",
    "SqlSpeechReconciliationDatasetPublisher",
    "SqlTenantResolvingSpeechReconciliationLedgerLookup",
    "SqlTenantResolvingSpeechReconciliationPublicationLedger",
    "SqlSpeechReconciliationWaveFactor",
    "build_default_speech_reconciliation_dispatcher",
]
