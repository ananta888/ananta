"""Closed, dependency-light contracts for local speech adaptation.

This module deliberately does not import the text LoRA contract.  Speech
adaptation has different privacy, scope and lifecycle bindings and therefore
uses an independent wire version and job type.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any

from ananta_contracts.speech_adaptation_primitives import (
    _artifact_ref,
    canonical_json,
    canonical_sha256,
    _closed,
    CONTRACT_VERSION,
    _digest,
    _identifier,
    _integer,
    MAX_ARTIFACT_BYTES,
    MAX_BATCH_SIZE,
    MAX_CHECKPOINTS,
    MAX_DEADLINE_AHEAD_MS,
    MAX_DISK_BYTES,
    MAX_EVENTS,
    MAX_RAM_BYTES,
    MAX_STEPS,
    MAX_VRAM_BYTES,
    MAX_WALL_SECONDS,
    RESULT_TYPE,
    SpeechAdaptationContractError,
    SUPPORTED_BACKENDS,
    SUPPORTED_DIRECTIONS,
    SUPPORTED_SCENARIOS,
    _text,
    TRAIN_JOB_TYPE,
)
from ananta_contracts.speech_adaptation_bindings import (
    speech_attempt_digest,
    speech_budget_digest,
    speech_configuration_digest,
    speech_fencing_digest,
    speech_job_binding_digest,
    speech_scope_digest,
    SpeechArtifactTarget,
    SpeechAttemptBinding,
    SpeechBaseModelBinding,
    SpeechConsentBinding,
    SpeechDatasetBinding,
    SpeechFencingBinding,
    SpeechResourceBudget,
    SpeechResumeBinding,
    SpeechScopeBinding,
    SpeechTrainingConfiguration,
)


@dataclass(frozen=True)
class SpeechAdaptationJob:
    contract_version: str
    job_type: str
    job_id: str
    dataset: SpeechDatasetBinding
    base_model: SpeechBaseModelBinding
    scope: SpeechScopeBinding
    consent: SpeechConsentBinding
    configuration: SpeechTrainingConfiguration
    budget: SpeechResourceBudget
    attempt: SpeechAttemptBinding
    fencing: SpeechFencingBinding
    artifact_target: SpeechArtifactTarget
    deadline_at_ms: int
    binding_digest: str
    resume: SpeechResumeBinding | None = None

    @classmethod
    def from_mapping(cls, value: Any, *, now_ms: int | None = None) -> "SpeechAdaptationJob":
        data = _closed(
            value,
            "job",
            frozenset(
                {
                    "contract_version",
                    "job_type",
                    "job_id",
                    "dataset",
                    "base_model",
                    "scope",
                    "consent",
                    "configuration",
                    "budget",
                    "attempt",
                    "fencing",
                    "artifact_target",
                    "deadline_at_ms",
                    "binding_digest",
                    "resume",
                }
            ),
        )
        version = _text(data.get("contract_version"), "contract_version", maximum=64)
        if version != CONTRACT_VERSION:
            raise SpeechAdaptationContractError(
                "speech_contract_version_unsupported",
                f"contract_version must be {CONTRACT_VERSION}",
            )
        job_type = _text(data.get("job_type"), "job_type", maximum=64)
        if job_type != TRAIN_JOB_TYPE:
            raise SpeechAdaptationContractError(
                "speech_job_type_unsupported",
                f"job_type must be {TRAIN_JOB_TYPE}",
            )
        job_id = _identifier(data.get("job_id"), "job_id")
        dataset = SpeechDatasetBinding.from_mapping(data.get("dataset"))
        base_model = SpeechBaseModelBinding.from_mapping(data.get("base_model"))
        scope = SpeechScopeBinding.from_mapping(data.get("scope"))
        consent = SpeechConsentBinding.from_mapping(data.get("consent"))
        configuration = SpeechTrainingConfiguration.from_mapping(data.get("configuration"))
        budget = SpeechResourceBudget.from_mapping(data.get("budget"))
        attempt = SpeechAttemptBinding.from_mapping(data.get("attempt"), job_id=job_id)
        fencing = SpeechFencingBinding.from_mapping(data.get("fencing"), attempt_id=attempt.attempt_id)
        artifact_target = SpeechArtifactTarget.from_mapping(data.get("artifact_target"))
        deadline_at_ms = _integer(data.get("deadline_at_ms"), "deadline_at_ms", minimum=1, maximum=2**63 - 1)
        effective_now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        if deadline_at_ms <= effective_now:
            raise SpeechAdaptationContractError("speech_deadline_stale", "speech training deadline has expired")
        if deadline_at_ms - effective_now > MAX_DEADLINE_AHEAD_MS:
            raise SpeechAdaptationContractError(
                "speech_deadline_out_of_bounds",
                "speech training deadline exceeds the maximum admission horizon",
            )
        if consent.scope_digest != scope.scope_digest:
            raise SpeechAdaptationContractError(
                "speech_consent_scope_mismatch",
                "consent is not bound to the requested pair direction and speaker",
            )
        if consent.expires_at_ms < deadline_at_ms:
            raise SpeechAdaptationContractError(
                "speech_consent_expires_before_deadline",
                "consent must remain valid through the job deadline",
            )
        if fencing.lease_expires_at_ms <= effective_now or fencing.lease_expires_at_ms > deadline_at_ms:
            raise SpeechAdaptationContractError(
                "speech_lease_invalid",
                "execution lease must be current and bounded by the job deadline",
            )
        if budget.max_wall_seconds * 1000 > deadline_at_ms - effective_now:
            raise SpeechAdaptationContractError(
                "speech_budget_deadline_mismatch",
                "wall-time budget exceeds the admitted deadline",
            )
        if budget.max_wall_seconds * 1000 > fencing.lease_expires_at_ms - effective_now:
            raise SpeechAdaptationContractError(
                "speech_budget_lease_mismatch",
                "wall-time budget exceeds the immutable execution lease",
            )
        resume = SpeechResumeBinding.from_mapping(data.get("resume")) if data.get("resume") is not None else None
        if resume is not None:
            expected_resume = {
                "dataset_digest": dataset.dataset_digest,
                "split_digest": dataset.split_digest,
                "model_digest": base_model.model_digest,
                "scope_digest": scope.scope_digest,
                "config_digest": configuration.config_digest,
            }
            mismatches = sorted(name for name, expected in expected_resume.items() if getattr(resume, name) != expected)
            if mismatches or resume.checkpoint_step >= configuration.max_steps:
                raise SpeechAdaptationContractError(
                    "speech_resume_binding_mismatch",
                    "resume checkpoint does not match the admitted job bindings",
                )
        digest_fields = {
            "artifact_target_digest": artifact_target.target_digest,
            "attempt_digest": attempt.attempt_digest,
            "budget_digest": budget.budget_digest,
            "config_digest": configuration.config_digest,
            "consent_digest": consent.consent_digest,
            "dataset_digest": dataset.dataset_digest,
            "fencing_digest": fencing.fencing_digest,
            "lineage_digest": dataset.lineage_digest,
            "model_digest": base_model.model_digest,
            "scope_digest": scope.scope_digest,
            "split_digest": dataset.split_digest,
        }
        binding_digest = _digest(data.get("binding_digest"), "binding_digest")
        if binding_digest != speech_job_binding_digest(digest_fields):
            raise SpeechAdaptationContractError(
                "speech_job_binding_digest_mismatch",
                "job binding digest does not match the admitted immutable inputs",
            )
        return cls(
            contract_version=version,
            job_type=job_type,
            job_id=job_id,
            dataset=dataset,
            base_model=base_model,
            scope=scope,
            consent=consent,
            configuration=configuration,
            budget=budget,
            attempt=attempt,
            fencing=fencing,
            artifact_target=artifact_target,
            deadline_at_ms=deadline_at_ms,
            binding_digest=binding_digest,
            resume=resume,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SpeechArtifactDescriptor:
    artifact_id: str
    artifact_ref: str
    sha256: str
    size_bytes: int
    media_type: str

    @classmethod
    def from_mapping(cls, value: Any) -> "SpeechArtifactDescriptor":
        data = _closed(
            value,
            "artifact",
            frozenset({"artifact_id", "artifact_ref", "sha256", "size_bytes", "media_type"}),
        )
        media_type = _text(data.get("media_type"), "artifact.media_type", maximum=128)
        if media_type not in {"application/vnd.ananta.speech-adapter", "application/json"}:
            raise SpeechAdaptationContractError(
                "speech_artifact_media_type_invalid",
                "artifact media type is not supported",
            )
        return cls(
            artifact_id=_identifier(data.get("artifact_id"), "artifact.artifact_id"),
            artifact_ref=_artifact_ref(
                data.get("artifact_ref"),
                "artifact.artifact_ref",
                prefix="artifact://speech-adapters/",
            ),
            sha256=_digest(data.get("sha256"), "artifact.sha256"),
            size_bytes=_integer(
                data.get("size_bytes"),
                "artifact.size_bytes",
                minimum=1,
                maximum=MAX_ARTIFACT_BYTES,
            ),
            media_type=media_type,
        )


@dataclass(frozen=True)
class SpeechAdaptationResult:
    contract_version: str
    result_type: str
    job_id: str
    attempt_id: str
    binding_digest: str
    fencing_digest: str
    status: str
    events_digest: str
    evaluation_report_digest: str | None
    checkpoint_digest: str | None
    artifact: SpeechArtifactDescriptor | None
    reason_code: str | None

    @classmethod
    def from_mapping(cls, value: Any) -> "SpeechAdaptationResult":
        data = _closed(
            value,
            "result",
            frozenset(
                {
                    "contract_version",
                    "result_type",
                    "job_id",
                    "attempt_id",
                    "binding_digest",
                    "fencing_digest",
                    "status",
                    "events_digest",
                    "evaluation_report_digest",
                    "checkpoint_digest",
                    "artifact",
                    "reason_code",
                }
            ),
        )
        version = _text(data.get("contract_version"), "contract_version", maximum=64)
        result_type = _text(data.get("result_type"), "result_type", maximum=64)
        if version != CONTRACT_VERSION or result_type != RESULT_TYPE:
            raise SpeechAdaptationContractError(
                "speech_result_contract_mismatch",
                "speech result version or type is unsupported",
            )
        status = _text(data.get("status"), "status", maximum=32)
        if status not in {"completed", "dataset_only", "cancelled", "failed"}:
            raise SpeechAdaptationContractError("speech_result_status_invalid", "speech result status is invalid")
        artifact = SpeechArtifactDescriptor.from_mapping(data.get("artifact")) if data.get("artifact") else None
        if status == "completed" and artifact is None:
            raise SpeechAdaptationContractError(
                "speech_result_artifact_missing",
                "completed speech training requires an adapter artifact",
            )
        if status != "completed" and artifact is not None:
            raise SpeechAdaptationContractError(
                "speech_result_artifact_forbidden",
                "non-completed speech training must not publish an adapter artifact",
            )
        evaluation_value = data.get("evaluation_report_digest")
        checkpoint_value = data.get("checkpoint_digest")
        if status == "completed" and (evaluation_value is None or checkpoint_value is None):
            raise SpeechAdaptationContractError(
                "speech_result_evidence_missing",
                "completed speech training requires evaluation and checkpoint evidence",
            )
        reason_value = data.get("reason_code")
        reason_code = _identifier(reason_value, "reason_code") if reason_value is not None else None
        if status in {"cancelled", "failed"} and reason_code is None:
            raise SpeechAdaptationContractError(
                "speech_result_reason_missing",
                "cancelled or failed speech training requires a reason code",
            )
        if status in {"completed", "dataset_only"} and reason_code is not None:
            raise SpeechAdaptationContractError(
                "speech_result_reason_forbidden",
                "successful or dataset-only speech results cannot carry an error reason",
            )
        return cls(
            contract_version=version,
            result_type=result_type,
            job_id=_identifier(data.get("job_id"), "job_id"),
            attempt_id=_identifier(data.get("attempt_id"), "attempt_id"),
            binding_digest=_digest(data.get("binding_digest"), "binding_digest"),
            fencing_digest=_digest(data.get("fencing_digest"), "fencing_digest"),
            status=status,
            events_digest=_digest(data.get("events_digest"), "events_digest"),
            evaluation_report_digest=(
                _digest(evaluation_value, "evaluation_report_digest") if evaluation_value is not None else None
            ),
            checkpoint_digest=(
                _digest(checkpoint_value, "checkpoint_digest") if checkpoint_value is not None else None
            ),
            artifact=artifact,
            reason_code=reason_code,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = [
    "CONTRACT_VERSION",
    "TRAIN_JOB_TYPE",
    "RESULT_TYPE",
    "SUPPORTED_BACKENDS",
    "SUPPORTED_DIRECTIONS",
    "SUPPORTED_SCENARIOS",
    "MAX_STEPS",
    "MAX_BATCH_SIZE",
    "MAX_CHECKPOINTS",
    "MAX_WALL_SECONDS",
    "MAX_RAM_BYTES",
    "MAX_VRAM_BYTES",
    "MAX_DISK_BYTES",
    "MAX_ARTIFACT_BYTES",
    "MAX_EVENTS",
    "MAX_DEADLINE_AHEAD_MS",
    "SpeechAdaptationContractError",
    "canonical_json",
    "canonical_sha256",
    "SpeechDatasetBinding",
    "SpeechBaseModelBinding",
    "speech_scope_digest",
    "SpeechScopeBinding",
    "SpeechConsentBinding",
    "speech_configuration_digest",
    "SpeechTrainingConfiguration",
    "speech_budget_digest",
    "SpeechResourceBudget",
    "speech_attempt_digest",
    "SpeechAttemptBinding",
    "speech_fencing_digest",
    "SpeechFencingBinding",
    "SpeechResumeBinding",
    "SpeechArtifactTarget",
    "speech_job_binding_digest",
    "SpeechAdaptationJob",
    "SpeechArtifactDescriptor",
    "SpeechAdaptationResult",
]
