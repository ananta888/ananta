"""Hub admission and lifecycle service for offline speech reconciliation."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from agent.repositories.speech_reconciliation import (
    SpeechReconciliationJobCreate,
    SpeechReconciliationJobRecord,
    SpeechReconciliationMutationResult,
    SpeechReconciliationRepository,
    SpeechReconciliationRepositoryError,
)
from agent.services.ml_intern_speech_dataset_build_service import (
    MlInternSpeechDatasetBuildService,
    SpeechDatasetManifestError,
)
from agent.services.semantic_media_audit_service import SemanticMediaAuditPort
from agent.services.speech_evidence_consent_service import SpeechEvidenceConsentService
from agent.services.speech_reconciliation_budget_service import (
    AdmittedSourceDuration,
    SpeechReconciliationBudgetPlan,
    SpeechReconciliationBudgetService,
)
from agent.services.speech_reconciliation_task_port import (
    HubSpeechReconciliationTaskPort,
    SpeechReconciliationTaskPort,
)
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_adaptation import (
    SpeechAdaptationContractError,
    SpeechResourceBudget,
    speech_budget_digest,
)
from ananta_contracts.speech_evidence_governance import (
    SpeechEvidenceConsent,
    SpeechEvidenceGovernanceError,
)
from ananta_contracts.speech_reconciliation import (
    MAX_RESEARCH_FACTOR,
    SpeechReconciliationContractError,
    SpeechResourceVector,
    canonical_sha256,
)
from voice_runtime.speech_reconciliation_policy import SpeechReconciliationPolicy

CREATE_REQUIRED_FIELDS = frozenset(
    {
        "consent_id",
        "consent_version",
        "revocation_epoch",
        "input_manifest_digest",
        "policy_digest",
        "research_policy_ref",
        "max_compute_factor",
        "key_epoch",
        "deadline_at_ms",
        "resource_limits",
    }
)
CREATE_OPTIONAL_FIELDS = frozenset({"training_budget"})
CREATE_FIELDS = CREATE_REQUIRED_FIELDS | CREATE_OPTIONAL_FIELDS


class SpeechReconciliationJobServiceError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 422) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class AdmittedSpeechReconciliationManifest:
    manifest_digest: str
    lineage_digest: str
    artifact_ref: str
    sources: tuple[AdmittedSourceDuration, ...]


@dataclass(frozen=True, slots=True)
class SpeechReconciliationAdmission:
    job: SpeechReconciliationJobRecord
    created: bool
    budget: SpeechReconciliationBudgetPlan


class SpeechReconciliationRepositoryPort(Protocol):
    def create_job(
        self, spec: SpeechReconciliationJobCreate, **kwargs
    ) -> tuple[SpeechReconciliationJobRecord, bool]: ...

    def get_job(self, *, tenant_id: str, owner_subject: str, job_id: str) -> SpeechReconciliationJobRecord | None: ...

    def list_jobs(
        self, *, tenant_id: str, owner_subject: str, offset: int, limit: int
    ) -> Sequence[SpeechReconciliationJobRecord]: ...

    def transition(self, **kwargs) -> SpeechReconciliationMutationResult: ...

    def reduce_factor(self, **kwargs) -> SpeechReconciliationMutationResult: ...


class SpeechReconciliationConsentPort(Protocol):
    def get(self, principal: VoicePrincipal, consent_id: str) -> SpeechEvidenceConsent: ...


class SpeechReconciliationManifestPort(Protocol):
    def resolve(
        self,
        principal: VoicePrincipal,
        *,
        manifest_digest: str,
        consent: SpeechEvidenceConsent,
    ) -> AdmittedSpeechReconciliationManifest | None: ...


class HubSpeechReconciliationManifestAdmission:
    """Resolve immutable governed manifests to an opaque worker artifact."""

    def __init__(self, datasets: MlInternSpeechDatasetBuildService | None = None) -> None:
        self._datasets = datasets or MlInternSpeechDatasetBuildService()

    def resolve(
        self,
        principal: VoicePrincipal,
        *,
        manifest_digest: str,
        consent: SpeechEvidenceConsent,
    ) -> AdmittedSpeechReconciliationManifest | None:
        manifest = self._datasets.get_by_digest(principal, manifest_digest)
        if manifest is None or manifest.get("version") != f"sha256:{manifest_digest}":
            return None
        records = manifest.get("records")
        if not isinstance(records, list) or not records:
            return None
        sources: dict[str, int] = {}
        lineage_rows: list[dict[str, object]] = []
        for raw in records:
            if not isinstance(raw, Mapping):
                return None
            data_classes = raw.get("data_classes")
            if not isinstance(data_classes, list) or "audio" not in data_classes:
                return None
            refs = raw.get("consent_refs")
            if not isinstance(refs, list) or not refs:
                return None
            if any(
                not isinstance(ref, Mapping)
                or ref.get("consent_id") != consent.consent_id
                or ref.get("consent_version") != consent.consent_version
                or ref.get("revocation_epoch") != consent.revocation_epoch
                or ref.get("consent_digest") != consent.consent_digest
                for ref in refs
            ):
                return None
            source_digest = _digest(raw.get("source_digest"), "speech_reconciliation_source_digest_invalid")
            duration = _bounded_int(
                raw.get("duration_ms"), "speech_reconciliation_source_duration_invalid", 1, 8 * 60 * 60 * 1000
            )
            previous = sources.setdefault(source_digest, duration)
            if previous != duration:
                return None
            lineage_rows.append(
                {
                    "record_digest": _digest(raw.get("record_digest"), "speech_reconciliation_record_digest_invalid"),
                    "source_digest": source_digest,
                    "duration_ms": duration,
                }
            )
        return AdmittedSpeechReconciliationManifest(
            manifest_digest=manifest_digest,
            lineage_digest=canonical_sha256(sorted(lineage_rows, key=lambda row: str(row["record_digest"]))),
            artifact_ref=f"artifact://speech-evidence/manifests/{manifest_digest}",
            sources=tuple(
                AdmittedSourceDuration(source_group_digest=digest, duration_ms=duration)
                for digest, duration in sorted(sources.items())
            ),
        )


class SpeechReconciliationJobService:
    """Coordinates admission; every external mutation remains Hub-owned."""

    def __init__(
        self,
        *,
        repository: SpeechReconciliationRepositoryPort,
        consents: SpeechReconciliationConsentPort,
        manifests: SpeechReconciliationManifestPort,
        budgets: SpeechReconciliationBudgetService,
        tasks: SpeechReconciliationTaskPort,
        clock_ms: Callable[[], int] | None = None,
        admission_enabled: Callable[[], bool] | None = None,
        audit: SemanticMediaAuditPort | None = None,
    ) -> None:
        self._repository = repository
        self._consents = consents
        self._manifests = manifests
        self._budgets = budgets
        self._tasks = tasks
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._admission_enabled = admission_enabled or (lambda: True)
        self._audit = audit

    def create(
        self,
        principal: VoicePrincipal,
        raw: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> SpeechReconciliationAdmission:
        self._require_admission_enabled()
        if not CREATE_REQUIRED_FIELDS <= set(raw) or set(raw) - CREATE_REQUIRED_FIELDS - CREATE_OPTIONAL_FIELDS:
            raise SpeechReconciliationJobServiceError("speech_reconciliation_request_shape_invalid")
        key = _idempotency_key(idempotency_key)
        now = self._clock_ms()
        deadline = _bounded_int(
            raw.get("deadline_at_ms"),
            "speech_reconciliation_deadline_invalid",
            now + 60_000,
            now + 30 * 24 * 60 * 60 * 1000,
        )
        consent_id = _identifier(raw.get("consent_id"), "speech_reconciliation_consent_id_invalid")
        try:
            consent = self._consents.get(principal, consent_id)
        except SpeechEvidenceGovernanceError as exc:
            raise SpeechReconciliationJobServiceError(exc.reason_code, status_code=exc.status_code) from exc
        consent_version = _bounded_int(
            raw.get("consent_version"), "speech_reconciliation_consent_version_invalid", 1, 2**31 - 1
        )
        revocation_epoch = _bounded_int(
            raw.get("revocation_epoch"), "speech_reconciliation_revocation_epoch_invalid", 0, 2**31 - 1
        )
        if (
            consent.tenant_id != principal.tenant_id
            or consent.owner_subject != principal.subject
            or consent.state != "active"
            or consent.expires_at_ms < deadline
            or consent.consent_version != consent_version
            or consent.revocation_epoch != revocation_epoch
            or consent.purpose != "speech_reconciliation"
            or consent.grants.get("raw_audio_share") is not True
            or consent.grants.get("dataset_import") is not True
            or "audio" not in consent.data_classes
        ):
            raise SpeechReconciliationJobServiceError("speech_reconciliation_consent_stale_or_narrow", status_code=403)
        manifest_digest = _digest(raw.get("input_manifest_digest"), "speech_reconciliation_manifest_digest_invalid")
        try:
            manifest = self._manifests.resolve(
                principal,
                manifest_digest=manifest_digest,
                consent=consent,
            )
        except SpeechDatasetManifestError as exc:
            raise SpeechReconciliationJobServiceError(exc.reason_code, status_code=exc.status_code) from exc
        if manifest is None:
            raise SpeechReconciliationJobServiceError("speech_reconciliation_manifest_not_admitted", status_code=404)
        policy_digest = _digest(raw.get("policy_digest"), "speech_reconciliation_policy_digest_invalid")
        if policy_digest != default_speech_reconciliation_policy_digest():
            raise SpeechReconciliationJobServiceError("speech_reconciliation_policy_not_admitted", status_code=403)
        research_ref = raw.get("research_policy_ref")
        if research_ref is not None:
            research_ref = str(research_ref)
            if not research_ref.startswith("artifact://speech-policies/") or ".." in research_ref.split("/"):
                raise SpeechReconciliationJobServiceError("speech_reconciliation_research_policy_invalid")
        factor = _bounded_int(
            raw.get("max_compute_factor"), "speech_reconciliation_factor_invalid", 1, MAX_RESEARCH_FACTOR
        )
        training_budget = _training_budget(raw.get("training_budget"))
        try:
            limits = SpeechResourceVector.from_mapping(raw.get("resource_limits"), "resource_limits")
        except SpeechReconciliationContractError as exc:
            raise SpeechReconciliationJobServiceError(exc.reason_code) from exc
        if limits == SpeechResourceVector():
            raise SpeechReconciliationJobServiceError("speech_reconciliation_resource_limits_empty")
        try:
            budget = self._budgets.plan(
                manifest.sources,
                compute_factor=factor,
                research_policy_ref=research_ref,
                requested_limits=limits,
            )
        except SpeechReconciliationContractError as exc:
            raise SpeechReconciliationJobServiceError(exc.reason_code) from exc
        key_epoch = _bounded_int(raw.get("key_epoch"), "speech_reconciliation_key_epoch_invalid", 1, 2**31 - 1)
        request_payload = {**dict(raw), "resource_limits": limits.to_dict()}
        request_digest = canonical_sha256(request_payload)
        idempotency_digest = canonical_sha256(
            {"tenant_id": principal.tenant_id, "owner_subject": principal.subject, "key": key}
        )
        job_digest = hashlib.sha256(f"{idempotency_digest}:{request_digest}".encode()).hexdigest()
        job_id = f"speech-reconciliation-{job_digest[:32]}"
        spec = SpeechReconciliationJobCreate(
            job_id=job_id,
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            pair_scope_digest=consent.scope_digest,
            idempotency_key_digest=idempotency_digest,
            request_digest=request_digest,
            consent_id=consent.consent_id,
            consent_version=consent.consent_version,
            revocation_epoch=consent.revocation_epoch,
            input_manifest_digest=manifest.manifest_digest,
            input_lineage_digest=manifest.lineage_digest,
            input_artifact_ref=manifest.artifact_ref,
            policy_digest=policy_digest,
            research_policy_ref=research_ref,
            source_duration_ms=budget.source_duration_ms,
            max_compute_factor=factor,
            current_compute_factor=SpeechReconciliationPolicy().initial_factor(
                user_limit=factor,
                authorized_factor=factor,
            ),
            training_budget=training_budget,
            budget_plan={
                "compute_factor": budget.compute_factor,
                "compute_equivalent_ms": budget.compute_equivalent_ms,
                "allocated": budget.total.to_dict(),
                "stages": {stage: vector.to_dict() for stage, vector in budget.stages.items()},
            },
            key_epoch=key_epoch,
            deadline_at_ms=deadline,
        )
        try:
            job, created = self._repository.create_job(spec, now_ms=now)
            self._tasks.materialize_parent(
                job_contract(job), tenant_id=principal.tenant_id, owner_subject=principal.subject
            )
        except SpeechReconciliationRepositoryError as exc:
            raise SpeechReconciliationJobServiceError(exc.reason_code, status_code=exc.status_code) from exc
        except SpeechReconciliationJobServiceError:
            raise
        except Exception as exc:
            raise SpeechReconciliationJobServiceError(
                "speech_reconciliation_task_projection_unavailable", status_code=503
            ) from exc
        self._record_audit(
            principal,
            job,
            event_type="semantic_job",
            transition="created",
            reason_code="speech_reconciliation_admitted",
        )
        return SpeechReconciliationAdmission(job, created, budget)

    def get(self, principal: VoicePrincipal, job_id: str) -> SpeechReconciliationJobRecord:
        job = self._repository.get_job(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            job_id=_identifier(job_id, "speech_reconciliation_job_id_invalid"),
        )
        if job is None:
            raise SpeechReconciliationJobServiceError("speech_reconciliation_job_not_found", status_code=404)
        return job

    def list(self, principal: VoicePrincipal, *, offset: int, limit: int) -> tuple[SpeechReconciliationJobRecord, ...]:
        if not 0 <= offset <= 1_000_000 or not 1 <= limit <= 100:
            raise SpeechReconciliationJobServiceError("speech_reconciliation_pagination_invalid")
        try:
            return tuple(
                self._repository.list_jobs(
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    offset=offset,
                    limit=limit,
                )
            )
        except SpeechReconciliationRepositoryError as exc:
            raise SpeechReconciliationJobServiceError(exc.reason_code, status_code=exc.status_code) from exc

    def pause(
        self,
        principal: VoicePrincipal,
        job_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> SpeechReconciliationJobRecord:
        return self._transition(
            principal,
            job_id,
            expected_version,
            "paused",
            "speech_reconciliation_user_paused",
            operation="pause",
            idempotency_key=idempotency_key,
        )

    def resume(
        self,
        principal: VoicePrincipal,
        job_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> SpeechReconciliationJobRecord:
        self._require_admission_enabled()
        return self._transition(
            principal,
            job_id,
            expected_version,
            "queued",
            "speech_reconciliation_user_resumed",
            operation="resume",
            idempotency_key=idempotency_key,
        )

    def cancel(
        self,
        principal: VoicePrincipal,
        job_id: str,
        *,
        expected_version: int,
        idempotency_key: str,
    ) -> SpeechReconciliationJobRecord:
        return self._transition(
            principal,
            job_id,
            expected_version,
            "cancel_requested",
            "speech_reconciliation_user_cancelled",
            operation="cancel",
            idempotency_key=idempotency_key,
        )

    def reduce(
        self,
        principal: VoicePrincipal,
        job_id: str,
        *,
        expected_version: int,
        max_compute_factor: int,
        idempotency_key: str,
    ) -> SpeechReconciliationJobRecord:
        normalized_job_id = _identifier(job_id, "speech_reconciliation_job_id_invalid")
        version = _bounded_int(
            expected_version,
            "speech_reconciliation_version_invalid",
            1,
            2**31 - 1,
        )
        factor = _bounded_int(max_compute_factor, "speech_reconciliation_factor_invalid", 1, MAX_RESEARCH_FACTOR)
        key_digest, request_digest = _mutation_digests(
            principal,
            job_id=normalized_job_id,
            operation="reduce",
            idempotency_key=idempotency_key,
            payload={"expected_version": version, "max_compute_factor": factor},
        )
        try:
            result = self._repository.reduce_factor(
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                job_id=normalized_job_id,
                max_compute_factor=factor,
                expected_version=version,
                reason_code="speech_reconciliation_user_reduced_factor",
                idempotency_key_digest=key_digest,
                request_digest=request_digest,
            )
            if result.applied:
                self._record_audit(
                    principal,
                    result.job,
                    event_type="semantic_budget",
                    transition="reduced",
                    reason_code="speech_reconciliation_user_reduced_factor",
                )
            return result.job
        except SpeechReconciliationRepositoryError as exc:
            raise SpeechReconciliationJobServiceError(exc.reason_code, status_code=exc.status_code) from exc

    def _transition(
        self,
        principal: VoicePrincipal,
        job_id: str,
        expected_version: int,
        target_state: str,
        reason_code: str,
        *,
        operation: str,
        idempotency_key: str,
    ) -> SpeechReconciliationJobRecord:
        normalized_job_id = _identifier(job_id, "speech_reconciliation_job_id_invalid")
        version = _bounded_int(
            expected_version,
            "speech_reconciliation_version_invalid",
            1,
            2**31 - 1,
        )
        key_digest, request_digest = _mutation_digests(
            principal,
            job_id=normalized_job_id,
            operation=operation,
            idempotency_key=idempotency_key,
            payload={"expected_version": version},
        )
        try:
            result = self._repository.transition(
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                job_id=normalized_job_id,
                target_state=target_state,
                stage=self.get(principal, normalized_job_id).stage,
                reason_code=reason_code,
                expected_version=version,
                idempotency_key_digest=key_digest,
                request_digest=request_digest,
            )
        except SpeechReconciliationRepositoryError as exc:
            raise SpeechReconciliationJobServiceError(exc.reason_code, status_code=exc.status_code) from exc
        if result.applied and result.affected_attempt_id is not None:
            task_id = HubSpeechReconciliationTaskPort.attempt_task_id(
                normalized_job_id,
                result.affected_attempt_id,
                int(result.affected_fencing_epoch or 0),
            )
            self._tasks.cancel(task_id, reason_code=reason_code)
        if result.applied:
            self._record_audit(
                principal,
                result.job,
                event_type="semantic_job",
                transition=target_state,
                reason_code=reason_code,
            )
        return result.job

    def _record_audit(
        self,
        principal: VoicePrincipal,
        job: SpeechReconciliationJobRecord,
        *,
        event_type: str,
        transition: str,
        reason_code: str,
    ) -> None:
        if self._audit is None or bool(getattr(self._repository, "transactional_audit", False)):
            return
        try:
            event = self._audit.prepare_transition(
                idempotency_key=f"speech-reconciliation:{event_type}:{job.id}:{job.version}:{transition}",
                tenant_id=principal.tenant_id,
                scope=f"speech-job:{job.id}",
                event_type=event_type,
                transition=transition,
                reason_code=reason_code,
                epoch=max(1, job.key_epoch),
                job_ref=job.id,
            )
            self._audit.append_prepared(event)
        except Exception as exc:
            raise SpeechReconciliationJobServiceError("semantic_audit_unavailable", status_code=503) from exc

    def _require_admission_enabled(self) -> None:
        if not self._admission_enabled():
            raise SpeechReconciliationJobServiceError(
                "speech_reconciliation_feature_disabled",
                status_code=403,
            )


def job_contract(job: SpeechReconciliationJobRecord):
    """Build the pre-claim task projection without inventing worker authority."""
    from ananta_contracts.speech_reconciliation import CONTRACT_VERSION, SpeechReconciliationJob

    return SpeechReconciliationJob.from_mapping(
        {
            "contract_version": CONTRACT_VERSION,
            "job_id": job.id,
            "attempt_id": job.active_attempt_id or "speech-reconciliation-unclaimed",
            "fencing_token_digest": "0" * 64,
            "fencing_epoch": max(1, job.fencing_epoch),
            "consent_id": job.consent_id,
            "consent_version": job.consent_version,
            "revocation_epoch": job.revocation_epoch,
            "input_manifest_digest": job.input_manifest_digest,
            "input_lineage_digest": job.input_lineage_digest,
            "input_artifact_ref": job.input_artifact_ref,
            "policy_digest": job.policy_digest,
            "research_policy_ref": job.research_policy_ref,
            "source_duration_ms": job.source_duration_ms,
            "max_compute_factor": job.max_compute_factor,
            "ledger_sequence": job.ledger_sequence,
            "key_epoch": job.key_epoch,
            "deadline_at_ms": job.deadline_at_ms,
            "stage": job.stage,
        }
    )


def default_speech_reconciliation_policy_digest() -> str:
    return canonical_sha256(
        {
            "policy": "speech-reconciliation-default-v1",
            "normal_max_factor": 20,
            "live_pressure_action": "pause",
            "publication_requires_empty_reservation": True,
        }
    )


def build_speech_reconciliation_job_service(
    *,
    audit: SemanticMediaAuditPort | None = None,
) -> SpeechReconciliationJobService:
    import os

    from agent.services.semantic_media_feature_flags import resolve_semantic_media_feature_flags

    return SpeechReconciliationJobService(
        repository=SpeechReconciliationRepository(audit=audit),
        consents=SpeechEvidenceConsentService(),
        manifests=HubSpeechReconciliationManifestAdmission(),
        budgets=SpeechReconciliationBudgetService(),
        tasks=HubSpeechReconciliationTaskPort(),
        admission_enabled=lambda: resolve_semantic_media_feature_flags(os.environ).get(
            "speech_reconciliation",
            False,
        ),
        audit=audit,
    )


def _idempotency_key(value: Any) -> str:
    normalized = str(value or "").strip()
    if (
        not 8 <= len(normalized) <= 256
        or any(not 0x21 <= ord(character) <= 0x7E for character in normalized)
    ):
        raise SpeechReconciliationJobServiceError("speech_reconciliation_idempotency_key_invalid")
    return normalized


def _mutation_digests(
    principal: VoicePrincipal,
    *,
    job_id: str,
    operation: str,
    idempotency_key: str,
    payload: Mapping[str, object],
) -> tuple[str, str]:
    key = _idempotency_key(idempotency_key)
    binding = {
        "schema": "ananta.speech-reconciliation-mutation.v1",
        "tenant_id": principal.tenant_id,
        "owner_subject": principal.subject,
        "job_id": job_id,
        "operation": operation,
    }
    return (
        canonical_sha256({**binding, "idempotency_key": key}),
        canonical_sha256({**binding, "payload": dict(payload)}),
    )


def _identifier(value: Any, reason_code: str) -> str:
    normalized = str(value or "").strip()
    if not 1 <= len(normalized) <= 192 or any(character.isspace() for character in normalized):
        raise SpeechReconciliationJobServiceError(reason_code)
    return normalized


def _digest(value: Any, reason_code: str) -> str:
    normalized = str(value or "")
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise SpeechReconciliationJobServiceError(reason_code)
    return normalized


def _bounded_int(value: Any, reason_code: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise SpeechReconciliationJobServiceError(reason_code)
    return value


def _training_budget(value: Any) -> dict[str, int] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise SpeechReconciliationJobServiceError("speech_reconciliation_training_budget_invalid")
    raw = dict(value)
    expected = {
        "max_wall_seconds",
        "max_ram_bytes",
        "max_vram_bytes",
        "max_disk_bytes",
        "max_artifact_bytes",
        "max_checkpoints",
        "max_events",
    }
    if set(raw) != expected:
        raise SpeechReconciliationJobServiceError("speech_reconciliation_training_budget_invalid")
    try:
        parsed = SpeechResourceBudget.from_mapping(
            {**raw, "budget_digest": speech_budget_digest(raw)}
        )
    except SpeechAdaptationContractError as exc:
        raise SpeechReconciliationJobServiceError(
            "speech_reconciliation_training_budget_invalid"
        ) from exc
    return {
        field: int(getattr(parsed, field))
        for field in expected
    }


__all__ = [
    "AdmittedSpeechReconciliationManifest",
    "CREATE_FIELDS",
    "HubSpeechReconciliationManifestAdmission",
    "SpeechReconciliationAdmission",
    "SpeechReconciliationJobService",
    "SpeechReconciliationJobServiceError",
    "build_speech_reconciliation_job_service",
    "default_speech_reconciliation_policy_digest",
    "job_contract",
]
