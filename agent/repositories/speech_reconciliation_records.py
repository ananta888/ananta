"""Value records and row projections of the speech reconciliation repository.

These immutable records are the public result types of
``agent.repositories.speech_reconciliation``; the projection helpers convert
locked database rows into those records without touching a session.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Mapping

from agent.db_models.speech_reconciliation import (
    SpeechReconciliationAttemptDB,
    SpeechReconciliationJobDB,
)
from ananta_contracts.speech_reconciliation import (
    CONTRACT_VERSION,
    SpeechReconciliationJob,
)


class SpeechReconciliationRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 409) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SpeechReconciliationJobCreate:
    job_id: str
    tenant_id: str
    owner_subject: str
    pair_scope_digest: str
    idempotency_key_digest: str
    request_digest: str
    consent_id: str
    consent_version: int
    revocation_epoch: int
    input_manifest_digest: str
    input_lineage_digest: str
    input_artifact_ref: str
    policy_digest: str
    research_policy_ref: str | None
    budget_plan: Mapping[str, object]
    source_duration_ms: int
    max_compute_factor: int
    key_epoch: int
    deadline_at_ms: int
    current_compute_factor: int | None = None
    training_budget: Mapping[str, int] | None = None


@dataclass(frozen=True)
class SpeechReconciliationJobRecord:
    id: str
    tenant_id: str
    owner_subject: str
    pair_scope_digest: str
    request_digest: str
    state: str
    stage: str
    reason_code: str
    consent_id: str
    consent_version: int
    revocation_epoch: int
    input_manifest_digest: str
    input_lineage_digest: str
    input_artifact_ref: str
    policy_digest: str
    research_policy_ref: str | None
    budget_plan: dict[str, object]
    source_duration_ms: int
    max_compute_factor: int
    ledger_sequence: int
    key_epoch: int
    deadline_at_ms: int
    active_attempt_id: str | None
    fencing_epoch: int
    checkpoint_count: int
    resolved_count: int
    unresolved_count: int
    rejected_count: int
    quarantined_count: int
    version: int
    created_at_ms: int
    updated_at_ms: int
    finished_at_ms: int | None
    current_compute_factor: int = 1
    quality_history: list[dict[str, object]] = field(default_factory=list)
    training_budget: dict[str, int] | None = None


@dataclass(frozen=True)
class SpeechReconciliationMutationResult:
    """Durable outcome of one externally requested Hub mutation.

    ``applied`` is true only for the transaction that changed job state.  It
    lets the service invoke non-database task adapters once without turning an
    idempotent replay into a second cancellation side effect.
    """

    job: SpeechReconciliationJobRecord
    applied: bool
    affected_attempt_id: str | None = None
    affected_fencing_epoch: int | None = None


@dataclass(frozen=True)
class SpeechReconciliationAttemptRecord:
    id: str
    job_id: str
    attempt_number: int
    state: str
    fencing_token_digest: str
    fencing_epoch: int
    lease_expires_at_ms: int
    deadline_at_ms: int
    last_heartbeat_at_ms: int
    checkpoint_sequence: int
    checkpoint_digest: str | None
    version: int


@dataclass(frozen=True)
class SpeechReconciliationCollectibleAttempt:
    """Content-free current-attempt projection for the Hub result collector."""

    tenant_id: str
    owner_subject: str
    job_state: str
    job_contract: SpeechReconciliationJob
    attempt_version: int
    lease_expires_at_ms: int


def job_record(row: SpeechReconciliationJobDB) -> SpeechReconciliationJobRecord:
    return SpeechReconciliationJobRecord(
        **{field: getattr(row, field) for field in SpeechReconciliationJobRecord.__dataclass_fields__}
    )


def attempt_record(row: SpeechReconciliationAttemptDB) -> SpeechReconciliationAttemptRecord:
    return SpeechReconciliationAttemptRecord(
        **{field: getattr(row, field) for field in SpeechReconciliationAttemptRecord.__dataclass_fields__}
    )


def active_job_contract(
    job: SpeechReconciliationJobDB,
    attempt: SpeechReconciliationAttemptDB,
) -> SpeechReconciliationJob:
    return SpeechReconciliationJob.from_mapping(
        {
            "contract_version": CONTRACT_VERSION,
            "job_id": job.id,
            "attempt_id": attempt.id,
            "fencing_token_digest": attempt.fencing_token_digest,
            "fencing_epoch": attempt.fencing_epoch,
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


def resolve_now_ms(value: int | None) -> int:
    return time.time_ns() // 1_000_000 if value is None else int(value)


__all__ = [
    "SpeechReconciliationAttemptRecord",
    "SpeechReconciliationCollectibleAttempt",
    "SpeechReconciliationJobCreate",
    "SpeechReconciliationJobRecord",
    "SpeechReconciliationMutationResult",
    "SpeechReconciliationRepositoryError",
    "active_job_contract",
    "attempt_record",
    "job_record",
    "resolve_now_ms",
]
