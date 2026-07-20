"""Production Hub composition for isolated, mock-safe speech adaptation.

The real OpenVoice backend remains deliberately unavailable per ADR.  This
module nevertheless provides the durable Hub control plane, current-consent
authority and worker artifact ingress used by the deterministic lifecycle
backend.  No worker can create tasks, mutate consent or write registry state.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Mapping

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_adaptation import (
    SpeechAdaptationArtifactDB,
    SpeechAdaptationJobDB,
)
from agent.db_models.speech_evidence import (
    SpeechDatasetManifestDB,
    SpeechEvidenceConsentDB,
)
from agent.repositories.speech_adaptation import (
    SqlSpeechAdaptationArtifactRepository,
    SqlSpeechAdaptationCapacityLeasePort,
    SqlSpeechAdaptationDecisionStore,
)
from agent.services.ml_intern_speech_dataset_build_service import (
    MlInternSpeechDatasetBuildService,
    SpeechDatasetManifestError,
)
from agent.services.ml_intern_speech_dataset_split_service import (
    MlInternSpeechDatasetSplitService,
    SpeechDatasetSplitError,
)
from agent.services.semantic_media_audit_service import SemanticMediaAuditPort
from agent.services.speech_adaptation_job_service import (
    ActiveSpeechConsent,
    AdmittedSpeechDataset,
    SpeechAdaptationCurrentAuthorityPort,
    SpeechAdaptationJobService,
    SpeechPrincipal,
)
from agent.services.speech_adaptation_task_port import HubSpeechAdaptationTaskPort
from agent.services.speech_adaptation_worker_port import HttpSpeechAdaptationWorkerPort
from ananta_contracts.speech_adaptation import (
    SpeechAdaptationJob,
    canonical_sha256,
)

_MOCK_MODEL_ID = "speech-mock-base-v1"
_MOCK_MODEL_DIGEST = hashlib.sha256(b"ananta-speech-mock-base-v1").hexdigest()
_MOCK_BACKEND_DIGEST = hashlib.sha256(b"ananta-speech-training-mock-backend-v1").hexdigest()
_CALLBACK_PHASES = frozenset(
    {
        "before_audio_access",
        "after_dataset_open",
        "before_checkpoint",
        "before_checkpoint_publish",
        "before_evaluation_publish",
        "before_artifact_export",
        "before_artifact_publish",
        "after_artifact_publish",
        "before_result_accept",
    }
)


class SpeechAdaptationProductionConfigurationError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class SqlSpeechDatasetAdmissionPort:
    """Resolve one immutable manifest and its deterministic leakage-safe split."""

    def __init__(
        self,
        *,
        validator: MlInternSpeechDatasetBuildService | None = None,
        splitter: MlInternSpeechDatasetSplitService | None = None,
    ) -> None:
        self._validator = validator or MlInternSpeechDatasetBuildService()
        self._splitter = splitter or MlInternSpeechDatasetSplitService(manifest_validator=self._validator)

    def resolve(
        self,
        principal: SpeechPrincipal,
        *,
        dataset_id: str,
        dataset_version: str,
    ) -> AdmittedSpeechDataset | None:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechDatasetManifestDB).where(
                    SpeechDatasetManifestDB.tenant_id == principal.tenant_id,
                    SpeechDatasetManifestDB.owner_subject == principal.subject,
                    SpeechDatasetManifestDB.dataset_id == dataset_id,
                    SpeechDatasetManifestDB.version == dataset_version,
                    SpeechDatasetManifestDB.status == "active",
                )
            ).first()
        if row is None:
            return None
        manifest = dict(row.manifest_payload or {})
        try:
            self._validator.validate(manifest)
            curation = manifest.get("curation_summary")
            if isinstance(curation, Mapping) and (
                curation.get("trainable") is not True
                or any(
                    not isinstance(value, Mapping) or value.get("curation_status") != "resolved"
                    for value in manifest.get("records", ())
                )
            ):
                return None
            split = self._splitter.split(
                _voice_principal(principal),
                manifest,
                validation_ratio=0.2,
                seed=3407,
                authority="hub",
                publish_lineage=False,
            )
        except (SpeechDatasetManifestError, SpeechDatasetSplitError):
            return None
        if (
            manifest.get("dataset_id") != dataset_id
            or manifest.get("version") != dataset_version
            or manifest.get("manifest_digest") != row.manifest_digest
            or split.manifest_digest != row.manifest_digest
        ):
            return None
        records = tuple(dict(value) for value in manifest.get("records", ()))
        bindings: set[tuple[str, int, int, str]] = set()
        for record in records:
            refs = record.get("consent_refs")
            if not isinstance(refs, list) or not refs:
                return None
            for raw in refs:
                if not isinstance(raw, Mapping):
                    return None
                bindings.add(
                    (
                        str(raw.get("consent_id") or ""),
                        int(raw.get("consent_version") or 0),
                        int(raw.get("revocation_epoch") or 0),
                        str(raw.get("consent_digest") or ""),
                    )
                )
        lineage_digest = canonical_sha256(
            {
                "manifest_digest": row.manifest_digest,
                "curation_report_digest": manifest.get("curation_report_digest"),
                "records": [
                    {
                        "record_digest": value.get("record_digest"),
                        "source_digest": value.get("source_digest"),
                        "consent_refs": value.get("consent_refs"),
                    }
                    for value in records
                ],
            }
        )
        tenant_digest = hashlib.sha256(principal.tenant_id.encode()).hexdigest()[:32]
        return AdmittedSpeechDataset(
            dataset_id=row.dataset_id,
            dataset_version=row.version,
            tenant_id=row.tenant_id,
            owner_subject=row.owner_subject,
            storage_ref=(f"artifact://speech-datasets/{tenant_digest}/{row.manifest_digest}"),
            dataset_digest=row.manifest_digest,
            split_digest=split.split_digest,
            lineage_digest=lineage_digest,
            train_sample_count=split.train_count,
            validation_sample_count=split.validation_count,
            immutable=True,
            status="admitted",
            consent_bindings=tuple(sorted(bindings)),
            contributor_digests=tuple(sorted(str(value) for value in manifest.get("contributors", ()))),
        )


class SqlSpeechConsentAdmissionPort:
    """Aggregate and revalidate every consent referenced by one dataset."""

    def __init__(self, *, trainer_location: str, clock_ms=None) -> None:
        location = str(trainer_location or "").strip()
        if not location or len(location) > 160:
            raise ValueError("speech trainer location is invalid")
        self._trainer_location = location
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    def current(self, principal: SpeechPrincipal, *, scope_digest: str) -> ActiveSpeechConsent | None:
        del principal, scope_digest
        # Dataset bindings are mandatory in production; never select an
        # arbitrary broad consent by scope alone.
        return None

    def current_for_dataset(
        self,
        principal: SpeechPrincipal,
        *,
        scope_digest: str,
        pair_id: str,
        direction: str,
        speaker_digest: str,
        dataset: AdmittedSpeechDataset,
    ) -> ActiveSpeechConsent | None:
        if not dataset.consent_bindings or speaker_digest not in set(dataset.contributor_digests):
            return None
        now = self._clock_ms()
        resolved: list[SpeechEvidenceConsentDB] = []
        with Session(engine) as session:
            for consent_id, version, revocation_epoch, digest in dataset.consent_bindings:
                row = session.exec(
                    select(SpeechEvidenceConsentDB).where(
                        SpeechEvidenceConsentDB.id == consent_id,
                        SpeechEvidenceConsentDB.tenant_id == principal.tenant_id,
                        SpeechEvidenceConsentDB.owner_subject == principal.subject,
                    )
                ).first()
                scope = dict(row.scope_payload or {}) if row is not None else {}
                grants = dict(scope.get("grants") or {})
                if row is None or (
                    row.state != "active"
                    or row.expires_at_ms <= now
                    or row.consent_version != version
                    or row.revocation_epoch != revocation_epoch
                    or not secrets.compare_digest(row.consent_digest, digest)
                    or row.pair_id != pair_id
                    or row.direction != direction
                    or row.purpose != "speech_adaptation_training"
                    or grants.get("dataset_import") is not True
                    or grants.get("training") is not True
                    or self._trainer_location not in set(scope.get("trainer_locations") or ())
                ):
                    return None
                session.expunge(row)
                resolved.append(row)
        aggregate = [
            {
                "consent_id": row.id,
                "consent_version": row.consent_version,
                "revocation_epoch": row.revocation_epoch,
                "consent_digest": row.consent_digest,
            }
            for row in sorted(resolved, key=lambda value: value.id)
        ]
        aggregate_digest = canonical_sha256(aggregate)
        aggregate_id = resolved[0].id if len(resolved) == 1 else f"speech-training-consent-{aggregate_digest[:32]}"
        return ActiveSpeechConsent(
            consent_id=aggregate_id,
            version=max(row.consent_version for row in resolved),
            digest=aggregate_digest,
            scope_digest=scope_digest,
            purpose="speech_adaptation_training",
            expires_at_ms=min(row.expires_at_ms for row in resolved),
            export_allowed=all(
                dict(row.scope_payload or {}).get("grants", {}).get("export") is True for row in resolved
            ),
            granted=True,
        )


class SqlSpeechAdaptationCurrentAuthority(SpeechAdaptationCurrentAuthorityPort):
    """Re-resolve all current SQL bindings at every sensitive worker phase."""

    def __init__(
        self,
        *,
        datasets: SqlSpeechDatasetAdmissionPort,
        consents: SqlSpeechConsentAdmissionPort,
        model_catalog: Mapping[str, Mapping[str, str]],
        backend_catalog: Mapping[str, str],
        clock_ms=None,
    ) -> None:
        self._datasets = datasets
        self._consents = consents
        self._models = {key: dict(value) for key, value in model_catalog.items()}
        self._backends = dict(backend_catalog)
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    def verify_current(
        self,
        principal: SpeechPrincipal,
        job: SpeechAdaptationJob,
        *,
        phase: str,
    ) -> tuple[bool, str | None]:
        if phase not in _CALLBACK_PHASES:
            return False, "speech_authority_phase_invalid"
        now = self._clock_ms()
        if now >= min(
            job.deadline_at_ms,
            job.fencing.lease_expires_at_ms,
            job.consent.expires_at_ms,
        ):
            return False, "speech_training_binding_expired"
        dataset = self._datasets.resolve(
            principal,
            dataset_id=job.dataset.dataset_id,
            dataset_version=job.dataset.dataset_version,
        )
        if dataset is None or (
            dataset.dataset_digest,
            dataset.split_digest,
            dataset.lineage_digest,
            dataset.train_sample_count,
            dataset.validation_sample_count,
        ) != (
            job.dataset.dataset_digest,
            job.dataset.split_digest,
            job.dataset.lineage_digest,
            job.dataset.train_sample_count,
            job.dataset.validation_sample_count,
        ):
            return False, "speech_dataset_binding_stale"
        consent = self._consents.current_for_dataset(
            principal,
            scope_digest=job.scope.scope_digest,
            pair_id=job.scope.pair_id,
            direction=job.scope.direction,
            speaker_digest=job.scope.speaker_digest,
            dataset=dataset,
        )
        if consent is None or (
            consent.consent_id,
            consent.version,
            consent.digest,
            consent.expires_at_ms,
        ) != (
            job.consent.consent_id,
            job.consent.consent_version,
            job.consent.consent_digest,
            job.consent.expires_at_ms,
        ):
            return False, "speech_consent_binding_stale"
        model = self._models.get(job.base_model.model_id)
        if model is None or (
            model.get("artifact_ref"),
            model.get("model_digest"),
        ) != (job.base_model.artifact_ref, job.base_model.model_digest):
            return False, "speech_model_binding_stale"
        if self._backends.get(job.configuration.backend) != job.configuration.backend_digest:
            return False, "speech_backend_binding_stale"
        return True, None


class SqlSpeechAdapterRegistrationAdmissionPort:
    """Bind registry admission to one committed Hub training result."""

    def __init__(
        self,
        *,
        datasets: SqlSpeechDatasetAdmissionPort | None = None,
        consents: SqlSpeechConsentAdmissionPort | None = None,
        clock_ms=None,
    ) -> None:
        self._datasets = datasets
        self._consents = consents
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    def verify_registration(
        self,
        principal: SpeechPrincipal,
        payload: Mapping[str, object],
        *,
        evaluation_report_digest: str,
    ) -> tuple[bool, str | None]:
        artifact_size = payload.get("artifact_size_bytes")
        if type(artifact_size) is not int:
            return False, "speech_adapter_artifact_size_invalid"
        with Session(engine) as session:
            receipt = session.exec(
                select(SpeechAdaptationArtifactDB).where(
                    SpeechAdaptationArtifactDB.tenant_id == principal.tenant_id,
                    SpeechAdaptationArtifactDB.owner_subject == principal.subject,
                    SpeechAdaptationArtifactDB.id == str(payload.get("adapter_id") or ""),
                    SpeechAdaptationArtifactDB.artifact_ref == str(payload.get("artifact_ref") or ""),
                    SpeechAdaptationArtifactDB.sha256 == str(payload.get("artifact_sha256") or ""),
                    SpeechAdaptationArtifactDB.size_bytes == artifact_size,
                    SpeechAdaptationArtifactDB.media_type == "application/vnd.ananta.speech-adapter",
                    SpeechAdaptationArtifactDB.state == "committed",
                )
            ).first()
            if receipt is None:
                return False, "speech_adapter_artifact_not_committed"
            row = session.get(SpeechAdaptationJobDB, receipt.job_id)
            if row is not None:
                session.expunge(row)
        if (
            row is None
            or row.tenant_id != principal.tenant_id
            or row.owner_subject != principal.subject
            or row.status != "completed"
        ):
            return False, "speech_adapter_training_result_not_completed"
        contract = dict(row.contract_payload or {})
        result = dict(row.result_payload or {})
        artifact = dict(result.get("artifact") or {})
        scope = dict(contract.get("scope") or {})
        model = dict(contract.get("base_model") or {})
        configuration = dict(contract.get("configuration") or {})
        dataset = dict(contract.get("dataset") or {})
        consent = dict(contract.get("consent") or {})
        expected = {
            "adapter_id": artifact.get("artifact_id"),
            "pair_id": scope.get("pair_id"),
            "direction": scope.get("direction"),
            "speaker_digest": scope.get("speaker_digest"),
            "scope_digest": scope.get("scope_digest"),
            "base_model_id": model.get("model_id"),
            "base_model_digest": model.get("model_digest"),
            "backend": configuration.get("backend"),
            "backend_digest": configuration.get("backend_digest"),
            "dataset_digest": dataset.get("dataset_digest"),
            "split_digest": dataset.get("split_digest"),
            "consent_digest": consent.get("consent_digest"),
            "consent_expires_at_ms": consent.get("expires_at_ms"),
            "artifact_ref": artifact.get("artifact_ref"),
            "artifact_sha256": artifact.get("sha256"),
            "artifact_size_bytes": artifact.get("size_bytes"),
        }
        if result.get("status") != "completed" or result.get("evaluation_report_digest") != evaluation_report_digest:
            return False, "speech_adapter_evaluation_binding_mismatch"
        if any(payload.get(key) != value for key, value in expected.items()):
            return False, "speech_adapter_training_binding_mismatch"
        if self._datasets is not None and self._consents is not None:
            try:
                job = _row_job(row)
            except ValueError:
                return False, "speech_job_contract_invalid"
            dataset_binding = self._datasets.resolve(
                principal,
                dataset_id=job.dataset.dataset_id,
                dataset_version=job.dataset.dataset_version,
            )
            if dataset_binding is None:
                return False, "speech_dataset_binding_stale"
            current_consent = self._consents.current_for_dataset(
                principal,
                scope_digest=job.scope.scope_digest,
                pair_id=job.scope.pair_id,
                direction=job.scope.direction,
                speaker_digest=job.scope.speaker_digest,
                dataset=dataset_binding,
            )
            if (
                current_consent is None
                or current_consent.digest != job.consent.consent_digest
                or current_consent.expires_at_ms != job.consent.expires_at_ms
                or self._clock_ms() >= current_consent.expires_at_ms
            ):
                return False, "speech_adapter_consent_stale"
        return True, None


@dataclass(frozen=True, slots=True)
class SpeechAdaptationControlComposition:
    jobs: SqlSpeechAdaptationDecisionStore
    capacity: SqlSpeechAdaptationCapacityLeasePort
    authority: SqlSpeechAdaptationCurrentAuthority
    artifacts: SqlSpeechAdaptationArtifactRepository
    adapter_registration: SqlSpeechAdapterRegistrationAdmissionPort
    service: SpeechAdaptationJobService
    worker: HttpSpeechAdaptationWorkerPort
    callback_token: str = field(repr=False)


class HubSpeechAdaptationWorkerControl:
    """Authenticated callback facade exposed to exactly one worker network."""

    def __init__(self, composition: SpeechAdaptationControlComposition) -> None:
        self._composition = composition

    def authenticate(self, token: str) -> bool:
        supplied = str(token or "")
        return bool(supplied) and secrets.compare_digest(
            supplied,
            self._composition.callback_token,
        )

    def authorize(self, payload: Mapping[str, object]) -> tuple[bool, str | None]:
        expected = {
            "job_id",
            "attempt_id",
            "binding_digest",
            "fencing_digest",
            "phase",
        }
        if set(payload) != expected:
            return False, "speech_authority_shape_invalid"
        row = self._composition.jobs.get_row(str(payload.get("job_id") or ""))
        if row is None or row.status not in {"dispatching", "submitted", "running"}:
            return False, "speech_job_fence_inactive"
        try:
            job = _row_job(row)
        except ValueError:
            return False, "speech_job_contract_invalid"
        if (
            payload.get("attempt_id") != job.attempt.attempt_id
            or payload.get("binding_digest") != job.binding_digest
            or payload.get("fencing_digest") != job.fencing.fencing_digest
        ):
            return False, "speech_job_fence_mismatch"
        return self._composition.authority.verify_current(
            SpeechPrincipal(row.tenant_id, row.owner_subject),
            job,
            phase=str(payload.get("phase") or ""),
        )

    def publish_artifact(
        self,
        metadata: Mapping[str, object],
        stream: BinaryIO,
    ) -> SpeechAdaptationArtifactDB:
        expected = {
            "job_id",
            "attempt_id",
            "fencing_digest",
            "binding_digest",
            "target_id",
            "target_ref",
            "sha256",
            "size_bytes",
            "media_type",
        }
        if set(metadata) != expected:
            raise SpeechAdaptationProductionConfigurationError("speech_artifact_metadata_invalid")
        row = self._composition.jobs.get_row(str(metadata.get("job_id") or ""))
        if row is None or row.status not in {"dispatching", "submitted", "running"}:
            raise SpeechAdaptationProductionConfigurationError("speech_job_fence_inactive")
        try:
            job = _row_job(row)
        except ValueError as exc:
            raise SpeechAdaptationProductionConfigurationError("speech_job_contract_invalid") from exc
        if (
            metadata.get("attempt_id") != job.attempt.attempt_id
            or metadata.get("fencing_digest") != job.fencing.fencing_digest
            or metadata.get("binding_digest") != job.binding_digest
        ):
            raise SpeechAdaptationProductionConfigurationError("speech_artifact_fence_mismatch")
        active, reason = self._composition.authority.verify_current(
            SpeechPrincipal(row.tenant_id, row.owner_subject),
            job,
            phase="before_artifact_publish",
        )
        if not active:
            raise SpeechAdaptationProductionConfigurationError(str(reason or "speech_artifact_authority_denied"))
        target_id = str(metadata.get("target_id") or "")
        target_ref = str(metadata.get("target_ref") or "")
        sha256 = str(metadata.get("sha256") or "")
        if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
            raise SpeechAdaptationProductionConfigurationError("speech_artifact_digest_invalid")
        media_type = str(metadata.get("media_type") or "")
        is_adapter = media_type == "application/vnd.ananta.speech-adapter"
        if is_adapter:
            if (target_id, target_ref) != (
                job.artifact_target.target_id,
                job.artifact_target.artifact_ref,
            ):
                raise SpeechAdaptationProductionConfigurationError("speech_artifact_target_mismatch")
        elif media_type == "application/vnd.ananta.speech-checkpoint":
            expected_prefix = f"artifact://speech-checkpoints/{job.job_id}/{job.attempt.attempt_id}/"
            if target_id != f"speech-checkpoint-{sha256[:32]}" or target_ref != f"{expected_prefix}{sha256}":
                raise SpeechAdaptationProductionConfigurationError("speech_checkpoint_target_mismatch")
        elif media_type == "application/vnd.ananta.speech-evaluation+json":
            expected_ref = f"artifact://speech-evaluations/{job.job_id}/{job.attempt.attempt_id}/{sha256}"
            if target_id != f"speech-evaluation-{sha256[:32]}" or target_ref != expected_ref:
                raise SpeechAdaptationProductionConfigurationError("speech_evaluation_target_mismatch")
        else:
            raise SpeechAdaptationProductionConfigurationError("speech_artifact_media_type_invalid")
        return self._composition.artifacts.publish(
            job=row,
            artifact_id=target_id,
            attempt_id=job.attempt.attempt_id,
            artifact_ref=target_ref,
            sha256=sha256,
            size_bytes=int(metadata.get("size_bytes") or 0),
            media_type=media_type,
            stream=stream,
        )


def build_speech_adaptation_composition(
    source: Mapping[str, str] | None = None,
    *,
    audit: SemanticMediaAuditPort | None = None,
) -> SpeechAdaptationControlComposition:
    values = source or os.environ
    if str(values.get("ANANTA_SPEECH_TRAINING_MOCK_ENABLED") or "").strip().casefold() not in {
        "1",
        "true",
    }:
        raise SpeechAdaptationProductionConfigurationError("speech_training_backend_no_go")
    endpoint = str(values.get("ANANTA_SPEECH_TRAINING_WORKER_URL") or "").strip()
    allowed = tuple(
        item.strip()
        for item in str(values.get("ANANTA_SPEECH_TRAINING_ALLOWED_ENDPOINTS") or "").split(",")
        if item.strip()
    )
    worker_token = str(values.get("ANANTA_SPEECH_TRAINING_TOKEN") or "").strip()
    callback_token = str(values.get("ANANTA_SPEECH_TRAINING_CALLBACK_TOKEN") or "").strip()
    if len(callback_token) < 32 or any(character.isspace() for character in callback_token):
        raise SpeechAdaptationProductionConfigurationError("speech_training_callback_token_invalid")
    worker = HttpSpeechAdaptationWorkerPort(
        endpoint=endpoint,
        allowed_endpoints=allowed,
        bearer_token=worker_token,
    )
    capacity = SqlSpeechAdaptationCapacityLeasePort(
        capacity=_bounded_int(values, "ANANTA_SPEECH_TRAINING_CAPACITY", 1, 1, 128),
        lease_seconds=_bounded_int(values, "ANANTA_SPEECH_TRAINING_LEASE_SECONDS", 300, 10, 3600),
    )
    datasets = SqlSpeechDatasetAdmissionPort()
    consents = SqlSpeechConsentAdmissionPort(
        trainer_location=str(values.get("ANANTA_SPEECH_TRAINER_LOCATION") or "ananta-local-speech-training-worker")
    )
    models = {
        _MOCK_MODEL_ID: {
            "artifact_ref": "artifact://speech-models/mock/speech-mock-base-v1",
            "model_digest": _MOCK_MODEL_DIGEST,
        }
    }
    backends = {"mock": _MOCK_BACKEND_DIGEST}
    authority = SqlSpeechAdaptationCurrentAuthority(
        datasets=datasets,
        consents=consents,
        model_catalog=models,
        backend_catalog=backends,
    )
    jobs = SqlSpeechAdaptationDecisionStore(audit=audit)
    tasks = HubSpeechAdaptationTaskPort()
    artifact_root = Path(str(values.get("ANANTA_SPEECH_TRAINING_ARTIFACT_ROOT") or "data/speech-adaptation/artifacts"))
    artifacts = SqlSpeechAdaptationArtifactRepository(artifact_root)
    adapter_registration = SqlSpeechAdapterRegistrationAdmissionPort(
        datasets=datasets,
        consents=consents,
    )
    # Rebuild the application service with the concrete artifact admission
    # port; the worker can only return descriptors for bytes accepted here.
    service = SpeechAdaptationJobService(
        datasets=datasets,
        consents=consents,
        capacity=capacity,
        tasks=tasks,
        model_catalog=models,
        backend_catalog=backends,
        decisions=jobs,
        current_authority=authority,
        result_artifacts=artifacts,
        audit=audit,
    )
    return SpeechAdaptationControlComposition(
        jobs=jobs,
        capacity=capacity,
        authority=authority,
        artifacts=artifacts,
        adapter_registration=adapter_registration,
        service=service,
        worker=worker,
        callback_token=callback_token,
    )


def _row_job(row):
    from agent.services.speech_adaptation_job_service import restore_speech_adaptation_job

    return restore_speech_adaptation_job(dict(row.contract_payload or {}))


def _voice_principal(principal: SpeechPrincipal):
    from agent.services.voice_governance_domain import VoicePrincipal

    return VoicePrincipal(principal.tenant_id, principal.subject)


def _bounded_int(
    source: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(source.get(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise SpeechAdaptationProductionConfigurationError(f"{name.casefold()}_invalid") from exc
    if not minimum <= value <= maximum:
        raise SpeechAdaptationProductionConfigurationError(f"{name.casefold()}_invalid")
    return value


__all__ = [
    "HubSpeechAdaptationWorkerControl",
    "SpeechAdaptationControlComposition",
    "SpeechAdaptationProductionConfigurationError",
    "SqlSpeechAdaptationCurrentAuthority",
    "SqlSpeechAdapterRegistrationAdmissionPort",
    "SqlSpeechConsentAdmissionPort",
    "SqlSpeechDatasetAdmissionPort",
    "build_speech_adaptation_composition",
]
