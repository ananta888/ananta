"""Hub-only admission of terminal reconciliation worker outcomes."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from agent.services.ml_intern_speech_reconciled_dataset_service import ReconciledDatasetMaterialization
from agent.services.semantic_media_audit_service import SemanticMediaAuditPort
from agent.services.speech_reconciliation_quality_controller import SpeechReconciliationWaveDecision
from agent.services.speech_reconciliation_training_delegate import (
    SpeechReconciliationTrainingDecision,
    SpeechReconciliationTrainingDelegate,
)
from agent.services.speech_reconciliation_worker_port import (
    SpeechReconciliationWorkerPoll,
    SpeechReconciliationWorkerPort,
)
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_evidence_governance import SpeechEvidenceConsent
from ananta_contracts.speech_reconciliation import (
    CONTRACT_VERSION,
    SpeechReconciliationCheckpoint,
    SpeechReconciliationJob,
    SpeechReconciliationResult,
    assert_result_matches_job,
)
from ananta_contracts.speech_reconciliation_worker import (
    SpeechReconciliationWorkerOutcome,
    assert_worker_outcome_matches_job,
)
from voice_runtime.fusion.alignment import candidate_tokens, normalize_token, tokenize
from voice_runtime.schemas import transcription_result_from_dict

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_DATASET_REF = re.compile(r"^artifact://speech-datasets/[A-Za-z0-9][A-Za-z0-9_./:-]{0,470}$")


class SpeechReconciliationResultAdmissionError(RuntimeError):
    """Content-free admission error safe for task state and logs."""

    def __init__(
        self,
        reason_code: str,
        *,
        status_code: int = 409,
        retryable: bool = False,
    ) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(reason_code)


class SpeechReconciliationCurrentAuthorityPort(Protocol):
    def authorize(
        self,
        principal: VoicePrincipal,
        job: SpeechReconciliationJob,
        *,
        phase: str,
    ) -> None: ...


class SpeechReconciliationCurrentJobPort(Protocol):
    def get_job(self, *, tenant_id: str, owner_subject: str, job_id: str): ...


class SpeechReconciliationCurrentConsentPort(Protocol):
    def get(self, principal: VoicePrincipal, consent_id: str) -> SpeechEvidenceConsent: ...


class HubSpeechReconciliationCurrentAuthority:
    """Revalidate the persisted lease and consent immediately before effects."""

    def __init__(
        self,
        *,
        jobs: SpeechReconciliationCurrentJobPort,
        consents: SpeechReconciliationCurrentConsentPort,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._jobs = jobs
        self._consents = consents
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    def authorize(
        self,
        principal: VoicePrincipal,
        job: SpeechReconciliationJob,
        *,
        phase: str,
    ) -> None:
        if phase not in {"admission", "checkpoint", "publication", "commit"}:
            raise SpeechReconciliationResultAdmissionError("speech_reconciliation_authority_phase_invalid")
        now = self._clock_ms()
        current = self._jobs.get_job(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            job_id=job.job_id,
        )
        if current is None:
            raise SpeechReconciliationResultAdmissionError(
                "speech_reconciliation_job_not_found",
                status_code=404,
            )
        if (
            current.state != "running"
            or current.active_attempt_id != job.attempt_id
            or current.fencing_epoch != job.fencing_epoch
            or current.ledger_sequence != job.ledger_sequence
        ):
            raise SpeechReconciliationResultAdmissionError("speech_reconciliation_result_lost_fence")
        if now >= current.deadline_at_ms or now >= job.deadline_at_ms:
            raise SpeechReconciliationResultAdmissionError("speech_reconciliation_result_late")
        try:
            consent = self._consents.get(principal, job.consent_id)
        except Exception as exc:
            raise SpeechReconciliationResultAdmissionError(
                "speech_reconciliation_consent_stale",
                status_code=403,
            ) from exc
        if (
            consent.tenant_id != principal.tenant_id
            or consent.owner_subject != principal.subject
            or consent.state != "active"
            or consent.expires_at_ms <= now
            or consent.consent_version != job.consent_version
            or consent.revocation_epoch != job.revocation_epoch
            or consent.purpose != "speech_reconciliation"
            or consent.grants.get("raw_audio_share") is not True
            or consent.grants.get("dataset_import") is not True
            or "audio" not in consent.data_classes
        ):
            raise SpeechReconciliationResultAdmissionError(
                "speech_reconciliation_consent_stale",
                status_code=403,
            )


class SpeechReconciliationResultRepositoryPort(Protocol):
    def save_checkpoint(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_contract: SpeechReconciliationJob,
        checkpoint: SpeechReconciliationCheckpoint,
    ): ...

    def complete(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        job_contract: SpeechReconciliationJob,
        result: SpeechReconciliationResult,
        publication_authorized: bool,
    ): ...


class SpeechReconciliationPublicationLedgerPort(Protocol):
    def authorize_publication(self, *, job_id: str, sequence: int, fencing_epoch: int) -> bool: ...


@dataclass(frozen=True, slots=True)
class PublishedSpeechReconciliationDataset:
    manifest_digest: str
    artifact_ref: str
    resolved_count: int
    unresolved_count: int
    rejected_count: int
    quarantined_count: int
    materialization: ReconciledDatasetMaterialization | None = None

    def __post_init__(self) -> None:
        if _DIGEST.fullmatch(self.manifest_digest) is None:
            raise SpeechReconciliationResultAdmissionError("speech_reconciliation_dataset_digest_invalid")
        if _DATASET_REF.fullmatch(self.artifact_ref) is None or ".." in self.artifact_ref.split("/"):
            raise SpeechReconciliationResultAdmissionError("speech_reconciliation_dataset_ref_invalid")
        counts = (
            self.resolved_count,
            self.unresolved_count,
            self.rejected_count,
            self.quarantined_count,
        )
        if any(type(value) is not int or not 0 <= value <= 1_000_000 for value in counts):
            raise SpeechReconciliationResultAdmissionError("speech_reconciliation_dataset_count_invalid")
        if sum(counts) < 1:
            raise SpeechReconciliationResultAdmissionError("speech_reconciliation_dataset_empty")


class SpeechReconciliationDatasetPublicationPort(Protocol):
    """Atomic, idempotent Hub publisher with its own consent write fence."""

    def publish(
        self,
        principal: VoicePrincipal,
        *,
        job: SpeechReconciliationJob,
        outcome: SpeechReconciliationWorkerOutcome,
        transcript,
    ) -> PublishedSpeechReconciliationDataset: ...


class SpeechReconciliationQualityControlPort(Protocol):
    def decide(
        self,
        principal: VoicePrincipal,
        job: SpeechReconciliationJob,
        outcome: SpeechReconciliationWorkerOutcome,
        *,
        authority: str = "hub",
    ) -> SpeechReconciliationWaveDecision: ...


class SpeechReconciliationTrainingBudgetPort(Protocol):
    def resolve(
        self,
        principal: VoicePrincipal,
        job: SpeechReconciliationJob,
    ) -> Mapping[str, int] | None: ...


@dataclass(frozen=True, slots=True)
class SpeechReconciliationResultAdmission:
    disposition: str
    reason_code: str
    result: SpeechReconciliationResult | None
    training: SpeechReconciliationTrainingDecision | None = None


class HubSpeechReconciliationResultAdmissionService:
    """Validate worker output; only the Hub may checkpoint or publish it."""

    def __init__(
        self,
        *,
        authority: SpeechReconciliationCurrentAuthorityPort,
        repository: SpeechReconciliationResultRepositoryPort,
        ledger: SpeechReconciliationPublicationLedgerPort,
        publisher: SpeechReconciliationDatasetPublicationPort,
        quality: SpeechReconciliationQualityControlPort | None = None,
        training: SpeechReconciliationTrainingDelegate | None = None,
        training_budgets: SpeechReconciliationTrainingBudgetPort | None = None,
        audit: SemanticMediaAuditPort | None = None,
    ) -> None:
        self._authority = authority
        self._repository = repository
        self._ledger = ledger
        self._publisher = publisher
        self._quality = quality
        self._training = training
        self._training_budgets = training_budgets
        self._audit = audit

    def accept(
        self,
        principal: VoicePrincipal,
        job: SpeechReconciliationJob,
        poll: SpeechReconciliationWorkerPoll,
        *,
        authority: str = "hub",
    ) -> SpeechReconciliationResultAdmission:
        if authority != "hub":
            raise SpeechReconciliationResultAdmissionError(
                "speech_reconciliation_hub_result_authority_required",
                status_code=403,
            )
        outcome = poll.result
        if outcome is None or poll.status not in {"completed", "partial", "failed", "cancelled"}:
            raise SpeechReconciliationResultAdmissionError("speech_reconciliation_worker_result_missing")
        if (
            poll.job_id != job.job_id
            or poll.attempt_id != job.attempt_id
            or poll.fencing_epoch != job.fencing_epoch
            or poll.status != outcome.status
        ):
            raise SpeechReconciliationResultAdmissionError("speech_reconciliation_worker_result_binding_mismatch")
        try:
            assert_worker_outcome_matches_job(job, outcome)
        except Exception as exc:
            raise SpeechReconciliationResultAdmissionError(
                "speech_reconciliation_worker_result_binding_mismatch"
            ) from exc
        self._authority.authorize(principal, job, phase="admission")
        if outcome.checkpoint is not None:
            self._authority.authorize(principal, job, phase="checkpoint")
            self._repository.save_checkpoint(
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                job_contract=job,
                checkpoint=outcome.checkpoint,
            )
            self._record_audit(
                principal,
                job,
                transition="checkpointed",
                reason_code=outcome.reason_code,
            )

        terminal_reason = "speech_reconciliation_dataset_materialized"
        transcript = None
        if outcome.status == "partial" and not outcome.publishable:
            if self._quality is None:
                # Compatibility for explicitly composed legacy/test adapters.
                # Production always injects the Hub quality controller.
                return SpeechReconciliationResultAdmission(
                    "checkpointed",
                    outcome.reason_code,
                    None,
                )
            try:
                wave = self._quality.decide(principal, job, outcome, authority="hub")
            except (PermissionError, ValueError) as exc:
                raise SpeechReconciliationResultAdmissionError(
                    (
                        str(exc)
                        if str(exc).startswith("speech_reconciliation_")
                        else "speech_reconciliation_quality_unavailable"
                    ),
                    retryable=True,
                ) from exc
            if wave.action == "extend":
                return SpeechReconciliationResultAdmission(
                    "extended",
                    wave.reason_code,
                    None,
                )
            if not wave.materialize_dataset:
                raise SpeechReconciliationResultAdmissionError(
                    "speech_reconciliation_quality_terminal_dataset_required"
                )
            terminal_reason = wave.reason_code
        if outcome.status in {"failed", "cancelled"}:
            result = self._terminal_result(job, outcome)
            self._authority.authorize(principal, job, phase="commit")
            self._repository.complete(
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                job_contract=job,
                result=result,
                publication_authorized=False,
            )
            self._record_audit(
                principal,
                job,
                transition=result.status,
                reason_code=result.reason_code,
            )
            return SpeechReconciliationResultAdmission("completed", result.reason_code, result)

        if outcome.publishable:
            transcript = _validate_transcript(outcome)
        self._authority.authorize(principal, job, phase="publication")
        self._require_publication_budget(job)
        publication = self._publisher.publish(
            principal,
            job=job,
            outcome=outcome,
            transcript=transcript,
        )
        # The publisher must fence consent atomically. These second checks also
        # prevent a concurrent budget update or lost attempt from being linked
        # as the active job result after an idempotent artifact write.
        self._authority.authorize(principal, job, phase="commit")
        self._require_publication_budget(job)
        result = SpeechReconciliationResult.from_mapping(
            {
                **_result_bindings(job),
                "status": "dataset_only_completed",
                "dataset_manifest_digest": publication.manifest_digest,
                "dataset_artifact_ref": publication.artifact_ref,
                "checkpoint_digest": (outcome.checkpoint.checkpoint_digest if outcome.checkpoint is not None else None),
                "evaluation_digest": outcome.resolution_hash,
                "adapter_digest": None,
                "resolved_count": publication.resolved_count,
                "unresolved_count": publication.unresolved_count,
                "rejected_count": publication.rejected_count,
                "quarantined_count": publication.quarantined_count,
                "reason_code": terminal_reason,
            }
        )
        assert_result_matches_job(job, result)
        self._repository.complete(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            job_contract=job,
            result=result,
            publication_authorized=True,
        )
        self._record_audit(
            principal,
            job,
            transition=result.status,
            reason_code=result.reason_code,
        )
        training = self._delegate_training(principal, job, publication)
        return SpeechReconciliationResultAdmission(
            "completed",
            training.reason_code if training is not None else result.reason_code,
            result,
            training,
        )

    def _delegate_training(
        self,
        principal: VoicePrincipal,
        job: SpeechReconciliationJob,
        publication: PublishedSpeechReconciliationDataset,
    ) -> SpeechReconciliationTrainingDecision | None:
        if self._training is None or publication.materialization is None:
            return None
        budget = self._training_budgets.resolve(principal, job) if self._training_budgets is not None else None
        try:
            return self._training.delegate(
                principal,
                publication.materialization,
                training_budget=budget,
                idempotency_key=f"{job.job_id}:training",
                authority="hub",
            )
        except Exception as exc:
            reason = str(getattr(exc, "reason_code", ""))
            if not reason.startswith("speech_") or len(reason) > 128:
                reason = "speech_reconciliation_training_admission_failed"
            return SpeechReconciliationTrainingDecision(
                "dataset_only_completed",
                reason,
                None,
            )

    def _record_audit(
        self,
        principal: VoicePrincipal,
        job: SpeechReconciliationJob,
        *,
        transition: str,
        reason_code: str,
    ) -> None:
        if self._audit is None or bool(getattr(self._repository, "transactional_audit", False)):
            return
        try:
            event = self._audit.prepare_transition(
                idempotency_key=(
                    f"speech-reconciliation-result:{job.job_id}:{job.attempt_id}:{transition}:{reason_code}"
                ),
                tenant_id=principal.tenant_id,
                scope=f"speech-job:{job.job_id}",
                event_type="semantic_job",
                transition=transition,
                reason_code=reason_code,
                epoch=job.fencing_epoch,
                lease_ref=job.attempt_id,
                job_ref=job.job_id,
            )
            self._audit.append_prepared(event)
        except Exception as exc:
            raise SpeechReconciliationResultAdmissionError(
                "semantic_audit_unavailable",
                status_code=503,
                retryable=True,
            ) from exc

    def _require_publication_budget(self, job: SpeechReconciliationJob) -> None:
        if not self._ledger.authorize_publication(
            job_id=job.job_id,
            sequence=job.ledger_sequence,
            fencing_epoch=job.fencing_epoch,
        ):
            raise SpeechReconciliationResultAdmissionError("speech_reconciliation_budget_publication_denied")

    @staticmethod
    def _terminal_result(
        job: SpeechReconciliationJob,
        outcome: SpeechReconciliationWorkerOutcome,
    ) -> SpeechReconciliationResult:
        result = SpeechReconciliationResult.from_mapping(
            {
                **_result_bindings(job),
                "status": outcome.status,
                "dataset_manifest_digest": None,
                "dataset_artifact_ref": None,
                "checkpoint_digest": (outcome.checkpoint.checkpoint_digest if outcome.checkpoint is not None else None),
                "evaluation_digest": None,
                "adapter_digest": None,
                "resolved_count": 0,
                "unresolved_count": outcome.unresolved_count,
                "rejected_count": 0,
                "quarantined_count": 0,
                "reason_code": outcome.reason_code,
            }
        )
        assert_result_matches_job(job, result)
        return result


class HubSpeechReconciliationResultCollector:
    """Poll one Hub-owned attempt; it never lets a worker publish directly."""

    def __init__(
        self,
        *,
        worker: SpeechReconciliationWorkerPort,
        admission: HubSpeechReconciliationResultAdmissionService,
    ) -> None:
        self._worker = worker
        self._admission = admission

    def collect(
        self,
        principal: VoicePrincipal,
        job: SpeechReconciliationJob,
    ) -> SpeechReconciliationResultAdmission:
        poll = self._worker.poll(job)
        if poll.result is None:
            return SpeechReconciliationResultAdmission(
                "pending",
                f"speech_reconciliation_worker_{poll.status}",
                None,
            )
        return self._admission.accept(principal, job, poll)

    def cancel(self, job: SpeechReconciliationJob) -> str:
        return self._worker.cancel(job)


def _validate_transcript(outcome: SpeechReconciliationWorkerOutcome):
    if not outcome.publishable or outcome.transcript is None:
        raise SpeechReconciliationResultAdmissionError("speech_reconciliation_transcript_not_publishable")
    try:
        transcript = transcription_result_from_dict(outcome.transcript)
    except (TypeError, ValueError) as exc:
        raise SpeechReconciliationResultAdmissionError("speech_reconciliation_transcript_invalid") from exc
    if (
        not transcript.provenance_valid
        or not transcript.text.strip()
        or len(transcript.candidates) != outcome.successful_candidate_count
    ):
        raise SpeechReconciliationResultAdmissionError("speech_reconciliation_transcript_unprovenanced")
    trace = transcript.decision_trace.get("token_provenance")
    tokens = tokenize(transcript.text)
    if not isinstance(trace, list | tuple) or len(trace) != len(tokens):
        raise SpeechReconciliationResultAdmissionError("speech_reconciliation_transcript_provenance_mismatch")
    candidates = {candidate.candidate_id: candidate for candidate in transcript.candidates}
    for output_index, raw in enumerate(trace):
        if not isinstance(raw, dict):
            raise SpeechReconciliationResultAdmissionError("speech_reconciliation_transcript_provenance_invalid")
        candidate = candidates.get(str(raw.get("candidate_id") or ""))
        source_index = raw.get("source_token_index")
        if candidate is None or type(source_index) is not int:
            raise SpeechReconciliationResultAdmissionError("speech_reconciliation_transcript_source_unknown")
        source_tokens = candidate_tokens(candidate)
        if not 0 <= source_index < len(source_tokens):
            raise SpeechReconciliationResultAdmissionError("speech_reconciliation_transcript_source_unknown")
        if (
            normalize_token(source_tokens[source_index].text) != normalize_token(tokens[output_index])
            or raw.get("token") != tokens[output_index]
        ):
            raise SpeechReconciliationResultAdmissionError("speech_reconciliation_transcript_token_invented")
    return transcript


def _result_bindings(job: SpeechReconciliationJob) -> dict[str, object]:
    return {
        "contract_version": CONTRACT_VERSION,
        "job_id": job.job_id,
        "attempt_id": job.attempt_id,
        "fencing_token_digest": job.fencing_token_digest,
        "fencing_epoch": job.fencing_epoch,
        "consent_id": job.consent_id,
        "consent_version": job.consent_version,
        "revocation_epoch": job.revocation_epoch,
        "input_manifest_digest": job.input_manifest_digest,
        "policy_digest": job.policy_digest,
        "ledger_sequence": job.ledger_sequence,
        "key_epoch": job.key_epoch,
    }


__all__ = [
    "HubSpeechReconciliationCurrentAuthority",
    "HubSpeechReconciliationResultAdmissionService",
    "HubSpeechReconciliationResultCollector",
    "PublishedSpeechReconciliationDataset",
    "SpeechReconciliationCurrentAuthorityPort",
    "SpeechReconciliationDatasetPublicationPort",
    "SpeechReconciliationPublicationLedgerPort",
    "SpeechReconciliationResultAdmission",
    "SpeechReconciliationResultAdmissionError",
    "SpeechReconciliationResultRepositoryPort",
]
