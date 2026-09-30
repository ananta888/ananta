"""Admission value types and ports for Hub speech adaptation jobs.

Extracted from ``agent.services.speech_adaptation_job_service`` so the job
service depends on narrow abstractions (DIP/ISP) that adapters and tests can
implement without importing the orchestration logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from agent.models.speech_adaptation_admission import (
    SpeechAdmissionDecision,
    SpeechCapacityLease,
    SpeechPrincipal,
)
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_adaptation import SpeechAdaptationJob, SpeechAdaptationResult


class SpeechAdaptationAdmissionError(ValueError):
    def __init__(self, reason_code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.status_code = status_code


@dataclass(frozen=True)
class AdmittedSpeechDataset:
    dataset_id: str
    dataset_version: str
    tenant_id: str
    owner_subject: str
    storage_ref: str
    dataset_digest: str
    split_digest: str
    lineage_digest: str
    train_sample_count: int
    validation_sample_count: int
    immutable: bool
    status: str = "admitted"
    consent_bindings: tuple[tuple[str, int, int, str], ...] = ()
    contributor_digests: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActiveSpeechConsent:
    consent_id: str
    version: int
    digest: str
    scope_digest: str
    purpose: str
    expires_at_ms: int
    export_allowed: bool
    granted: bool = True


class SpeechDatasetAdmissionPort(Protocol):
    def resolve(
        self,
        principal: SpeechPrincipal,
        *,
        dataset_id: str,
        dataset_version: str,
    ) -> AdmittedSpeechDataset | None: ...


class SpeechConsentAdmissionPort(Protocol):
    def current(self, principal: SpeechPrincipal, *, scope_digest: str) -> ActiveSpeechConsent | None: ...


class SpeechCapacityLeasePort(Protocol):
    def try_acquire(self, *, job_id: str, deadline_at_ms: int, now_ms: int) -> SpeechCapacityLease | None: ...

    def release(self, lease_id: str) -> None: ...


class SpeechAdaptationLineagePort(Protocol):
    def publish_training_job(self, principal: VoicePrincipal, job: SpeechAdaptationJob) -> str: ...

    def publish_training_result(
        self,
        principal: VoicePrincipal,
        job: SpeechAdaptationJob,
        result: SpeechAdaptationResult,
        *,
        authority: str = "hub",
    ) -> str: ...


class SpeechAdaptationDecisionStorePort(Protocol):
    def by_idempotency(
        self,
        principal: SpeechPrincipal,
        idempotency_digest: str,
    ) -> "SpeechAdmissionDecision | None": ...

    def create(
        self,
        principal: SpeechPrincipal,
        *,
        idempotency_digest: str,
        decision: "SpeechAdmissionDecision",
    ) -> tuple["SpeechAdmissionDecision", bool]: ...

    def get(
        self,
        principal: SpeechPrincipal,
        job_id: str,
    ) -> "SpeechAdmissionDecision | None": ...

    def waiting_admission(
        self,
        principal: SpeechPrincipal,
        job_id: str,
    ) -> tuple[str, Mapping[str, Any]] | None: ...

    def replace(
        self,
        principal: SpeechPrincipal,
        decision: "SpeechAdmissionDecision",
        *,
        expected_statuses: frozenset[str],
        result: SpeechAdaptationResult | None = None,
    ) -> "SpeechAdmissionDecision": ...


class SpeechAdaptationCurrentAuthorityPort(Protocol):
    def verify_current(
        self,
        principal: SpeechPrincipal,
        job: SpeechAdaptationJob,
        *,
        phase: str,
    ) -> tuple[bool, str | None]: ...


class SpeechAdaptationResultArtifactPort(Protocol):
    def verify_and_commit(
        self,
        principal: SpeechPrincipal,
        job: SpeechAdaptationJob,
        result: SpeechAdaptationResult,
    ) -> None: ...

    def read_evaluation(
        self,
        principal: SpeechPrincipal,
        job: SpeechAdaptationJob,
        evaluation_digest: str,
    ) -> Mapping[str, Any]: ...


__all__ = [
    "ActiveSpeechConsent",
    "AdmittedSpeechDataset",
    "SpeechAdaptationAdmissionError",
    "SpeechAdaptationCurrentAuthorityPort",
    "SpeechAdaptationDecisionStorePort",
    "SpeechAdaptationLineagePort",
    "SpeechAdaptationResultArtifactPort",
    "SpeechCapacityLeasePort",
    "SpeechConsentAdmissionPort",
    "SpeechDatasetAdmissionPort",
]
