"""Versioned execution-boundary contracts for model analysis.

These DTOs carry Hub-issued authority to one worker attempt. They do not
create tasks, select workers, persist state, or perform orchestration.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ananta_contracts.model_intelligence import ArtifactRef, ErrorEnvelope

RESOURCE_LEASE_SCHEMA = "ananta.model-intelligence.resource-lease.v1"
CANCELLATION_SIGNAL_SCHEMA = "ananta.model-intelligence.cancellation-signal.v1"
ANALYSIS_COMPLETION_SCHEMA = "ananta.model-intelligence.analysis-completion.v1"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMPLETION_KEY_RE = re.compile(r"^completion_[0-9a-f]{64}$")


class CancellationReason(str, Enum):
    HUB_CANCELLED = "hub_cancelled"
    LEASE_REVOKED = "lease_revoked"
    WORKER_SHUTDOWN = "worker_shutdown"


class CompletionOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class _ClosedExecutionContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    def to_wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


class ResourceLease(_ClosedExecutionContract):
    schema_version: Literal[
        "ananta.model-intelligence.resource-lease.v1"
    ] = Field(
        default=RESOURCE_LEASE_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    lease_id: str
    job_id: str
    tenant_id: str
    worker_id: str
    lease_generation: int = Field(ge=1, le=2**31 - 1)
    acquired_epoch_ms: int = Field(ge=0)
    expires_epoch_ms: int = Field(ge=1)
    max_memory_bytes: int = Field(ge=1, le=1_099_511_627_776)
    max_output_bytes: int = Field(ge=1, le=107_374_182_400)
    completion_key: str
    request_sha256: str

    @field_validator("lease_id", "job_id", "tenant_id", "worker_id")
    @classmethod
    def _validate_identifiers(cls, value: str) -> str:
        normalized = value.strip()
        if not _IDENTIFIER_RE.fullmatch(normalized):
            raise ValueError("model_analysis_lease_identifier_invalid")
        return normalized

    @field_validator("completion_key")
    @classmethod
    def _validate_completion_key(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _COMPLETION_KEY_RE.fullmatch(normalized):
            raise ValueError("model_analysis_completion_key_invalid")
        return normalized

    @field_validator("request_sha256")
    @classmethod
    def _validate_request_digest(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _SHA256_RE.fullmatch(normalized):
            raise ValueError("model_analysis_request_digest_invalid")
        return normalized

    @model_validator(mode="after")
    def _validate_window(self) -> "ResourceLease":
        if self.expires_epoch_ms <= self.acquired_epoch_ms:
            raise ValueError("model_analysis_lease_window_invalid")
        return self


class CancellationSignal(_ClosedExecutionContract):
    schema_version: Literal[
        "ananta.model-intelligence.cancellation-signal.v1"
    ] = Field(
        default=CANCELLATION_SIGNAL_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    job_id: str
    lease_id: str
    lease_generation: int = Field(ge=1, le=2**31 - 1)
    reason_code: CancellationReason
    requested_epoch_ms: int = Field(ge=0)

    @field_validator("job_id", "lease_id")
    @classmethod
    def _validate_identifiers(cls, value: str) -> str:
        normalized = value.strip()
        if not _IDENTIFIER_RE.fullmatch(normalized):
            raise ValueError("model_analysis_cancellation_identifier_invalid")
        return normalized


class AnalysisCompletion(_ClosedExecutionContract):
    schema_version: Literal[
        "ananta.model-intelligence.analysis-completion.v1"
    ] = Field(
        default=ANALYSIS_COMPLETION_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    job_id: str
    lease_id: str
    lease_generation: int = Field(ge=1, le=2**31 - 1)
    completion_key: str
    outcome: CompletionOutcome
    artifacts: tuple[ArtifactRef, ...] = ()
    error: ErrorEnvelope | None = None

    @field_validator("job_id", "lease_id")
    @classmethod
    def _validate_identifiers(cls, value: str) -> str:
        normalized = value.strip()
        if not _IDENTIFIER_RE.fullmatch(normalized):
            raise ValueError("model_analysis_completion_identifier_invalid")
        return normalized

    @field_validator("completion_key")
    @classmethod
    def _validate_completion_key(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _COMPLETION_KEY_RE.fullmatch(normalized):
            raise ValueError("model_analysis_completion_key_invalid")
        return normalized

    @field_validator("artifacts")
    @classmethod
    def _validate_artifact_set(
        cls,
        values: tuple[ArtifactRef, ...],
    ) -> tuple[ArtifactRef, ...]:
        ordered = tuple(sorted(values, key=lambda item: item.artifact_id))
        if len(ordered) > 64 or len({item.artifact_id for item in ordered}) != len(ordered):
            raise ValueError("model_analysis_completion_artifacts_invalid")
        return ordered

    @model_validator(mode="after")
    def _validate_outcome(self) -> "AnalysisCompletion":
        if any(artifact.job_id != self.job_id for artifact in self.artifacts):
            raise ValueError("model_analysis_completion_artifact_job_mismatch")
        if self.outcome is CompletionOutcome.SUCCEEDED:
            if not self.artifacts or self.error is not None:
                raise ValueError("model_analysis_completion_success_invalid")
            return self
        if self.artifacts or self.error is None:
            raise ValueError("model_analysis_completion_failure_invalid")
        cancelled_error = self.error.reason_code.value == "analysis_cancelled"
        if (self.outcome is CompletionOutcome.CANCELLED) is not cancelled_error:
            raise ValueError("model_analysis_completion_cancellation_invalid")
        return self


__all__ = [
    "ANALYSIS_COMPLETION_SCHEMA",
    "CANCELLATION_SIGNAL_SCHEMA",
    "RESOURCE_LEASE_SCHEMA",
    "AnalysisCompletion",
    "CancellationReason",
    "CancellationSignal",
    "CompletionOutcome",
    "ResourceLease",
]
