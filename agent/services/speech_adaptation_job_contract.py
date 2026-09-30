"""Hub-authored speech adaptation worker contract assembly.

Extracted from ``agent.services.speech_adaptation_job_service`` (SRP): the job
service owns admission orchestration, while this module owns the pure,
deterministic construction and lease-independent validation of the bounded
worker contract.  Digests and identifiers are derived exactly as before.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from agent.models.speech_adaptation_admission import SpeechCapacityLease, SpeechPrincipal
from agent.services.speech_adaptation_admission_ports import (
    ActiveSpeechConsent,
    AdmittedSpeechDataset,
)
from ananta_contracts.speech_adaptation import (
    CONTRACT_VERSION,
    MAX_DEADLINE_AHEAD_MS,
    TRAIN_JOB_TYPE,
    SpeechAdaptationContractError,
    SpeechBaseModelBinding,
    SpeechConsentBinding,
    SpeechDatasetBinding,
    SpeechResourceBudget,
    SpeechScopeBinding,
    SpeechTrainingConfiguration,
    canonical_sha256,
    speech_attempt_digest,
    speech_fencing_digest,
    speech_job_binding_digest,
)


def build_speech_adaptation_job_payload(
    *,
    principal: SpeechPrincipal,
    job_id: str,
    lease: SpeechCapacityLease,
    dataset: AdmittedSpeechDataset,
    model_id: str,
    model: Mapping[str, str],
    pair_id: str,
    direction: str,
    speaker_digest: str,
    scope_digest: str,
    consent: ActiveSpeechConsent,
    configuration: Mapping[str, Any],
    budget: Mapping[str, Any],
    deadline: int,
) -> dict[str, Any]:
    """Assemble the fenced first-attempt worker contract for one admitted lease."""

    attempt_id = f"speech-attempt-{hashlib.sha256(f'{job_id}:{lease.epoch}'.encode()).hexdigest()[:32]}"
    attempt_digest = speech_attempt_digest(job_id=job_id, attempt_id=attempt_id, attempt_number=1)
    fencing_digest = speech_fencing_digest(
        attempt_id=attempt_id,
        epoch=lease.epoch,
        lease_id=lease.lease_id,
        lease_expires_at_ms=lease.expires_at_ms,
    )
    target_id = f"speech-adapter-{hashlib.sha256(job_id.encode()).hexdigest()[:32]}"
    tenant_ref = hashlib.sha256(principal.tenant_id.encode()).hexdigest()[:32]
    target_ref = f"artifact://speech-adapters/{tenant_ref}/{target_id}"
    target_digest = canonical_sha256({"artifact_ref": target_ref, "target_id": target_id})
    binding_fields = {
        "artifact_target_digest": target_digest,
        "attempt_digest": attempt_digest,
        "budget_digest": budget["budget_digest"],
        "config_digest": configuration["config_digest"],
        "consent_digest": consent.digest,
        "dataset_digest": dataset.dataset_digest,
        "fencing_digest": fencing_digest,
        "lineage_digest": dataset.lineage_digest,
        "model_digest": model["model_digest"],
        "scope_digest": scope_digest,
        "split_digest": dataset.split_digest,
    }
    return {
        "contract_version": CONTRACT_VERSION,
        "job_type": TRAIN_JOB_TYPE,
        "job_id": job_id,
        "dataset": {
            "dataset_id": dataset.dataset_id,
            "dataset_version": dataset.dataset_version,
            "storage_ref": dataset.storage_ref,
            "dataset_digest": dataset.dataset_digest,
            "split_digest": dataset.split_digest,
            "lineage_digest": dataset.lineage_digest,
            "train_sample_count": dataset.train_sample_count,
            "validation_sample_count": dataset.validation_sample_count,
            "immutable": True,
        },
        "base_model": {
            "model_id": model_id,
            "artifact_ref": model["artifact_ref"],
            "model_digest": model["model_digest"],
        },
        "scope": {
            "pair_id": pair_id,
            "direction": direction,
            "speaker_digest": speaker_digest,
            "scope_digest": scope_digest,
        },
        "consent": {
            "consent_id": consent.consent_id,
            "consent_version": consent.version,
            "consent_digest": consent.digest,
            "scope_digest": consent.scope_digest,
            "purpose": consent.purpose,
            "granted": consent.granted,
            "expires_at_ms": consent.expires_at_ms,
            "export_allowed": consent.export_allowed,
        },
        "configuration": configuration,
        "budget": budget,
        "attempt": {"attempt_id": attempt_id, "attempt_number": 1, "attempt_digest": attempt_digest},
        "fencing": {
            "lease_id": lease.lease_id,
            "epoch": lease.epoch,
            "lease_expires_at_ms": lease.expires_at_ms,
            "fencing_digest": fencing_digest,
        },
        "artifact_target": {
            "target_id": target_id,
            "artifact_ref": target_ref,
            "target_digest": target_digest,
        },
        "deadline_at_ms": deadline,
        "binding_digest": speech_job_binding_digest(binding_fields),
        "resume": None,
    }


def validate_prelease_bindings(
    *,
    now_ms: int,
    deadline_at_ms: int,
    dataset: AdmittedSpeechDataset,
    model_id: str,
    model: Mapping[str, str],
    scope: Mapping[str, Any],
    consent: ActiveSpeechConsent,
    configuration: Mapping[str, Any],
    budget: Mapping[str, Any],
) -> None:
    """Validate every lease-independent contract field before capacity policy."""

    SpeechDatasetBinding.from_mapping(
        {
            "dataset_id": dataset.dataset_id,
            "dataset_version": dataset.dataset_version,
            "storage_ref": dataset.storage_ref,
            "dataset_digest": dataset.dataset_digest,
            "split_digest": dataset.split_digest,
            "lineage_digest": dataset.lineage_digest,
            "train_sample_count": dataset.train_sample_count,
            "validation_sample_count": dataset.validation_sample_count,
            "immutable": dataset.immutable,
        }
    )
    SpeechBaseModelBinding.from_mapping(
        {
            "model_id": model_id,
            "artifact_ref": model.get("artifact_ref"),
            "model_digest": model.get("model_digest"),
        }
    )
    parsed_scope = SpeechScopeBinding.from_mapping(scope)
    parsed_consent = SpeechConsentBinding.from_mapping(
        {
            "consent_id": consent.consent_id,
            "consent_version": consent.version,
            "consent_digest": consent.digest,
            "scope_digest": consent.scope_digest,
            "purpose": consent.purpose,
            "granted": consent.granted,
            "expires_at_ms": consent.expires_at_ms,
            "export_allowed": consent.export_allowed,
        }
    )
    parsed_configuration = SpeechTrainingConfiguration.from_mapping(configuration)
    parsed_budget = SpeechResourceBudget.from_mapping(budget)
    del parsed_configuration
    if parsed_consent.scope_digest != parsed_scope.scope_digest:
        raise SpeechAdaptationContractError(
            "speech_consent_scope_mismatch",
            "consent is not bound to the requested pair direction and speaker",
        )
    if isinstance(deadline_at_ms, bool) or deadline_at_ms <= now_ms:
        raise SpeechAdaptationContractError(
            "speech_deadline_stale",
            "speech training deadline has expired",
        )
    if deadline_at_ms - now_ms > MAX_DEADLINE_AHEAD_MS:
        raise SpeechAdaptationContractError(
            "speech_deadline_out_of_bounds",
            "speech training deadline exceeds the maximum admission horizon",
        )
    if parsed_consent.expires_at_ms < deadline_at_ms:
        raise SpeechAdaptationContractError(
            "speech_consent_expires_before_deadline",
            "consent must remain valid through the job deadline",
        )
    if parsed_budget.max_wall_seconds * 1000 > deadline_at_ms - now_ms:
        raise SpeechAdaptationContractError(
            "speech_budget_deadline_mismatch",
            "wall-time budget exceeds the admitted deadline",
        )


__all__ = [
    "build_speech_adaptation_job_payload",
    "validate_prelease_bindings",
]
