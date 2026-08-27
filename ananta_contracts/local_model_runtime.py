"""Closed, content-free contracts for Hub-owned local model runtimes."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

LOCAL_RUNTIME_STATUS_SCHEMA = "ananta.local-model-runtime-status.v1"
LOCAL_RUNTIME_SNAPSHOT_SCHEMA = "ananta.local-model-runtime-snapshot.v1"
LOCAL_RUNTIME_ACTIVATION_DECISION_SCHEMA = "ananta.local-model-runtime-activation-decision.v1"
LOCAL_RUNTIME_CONTROL_RECEIPT_SCHEMA = "ananta.local-model-runtime-control-receipt.v1"
LOCAL_RUNTIME_INVOCATION_SCHEMA = "ananta.local-model-runtime-invocation.v1"

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_REASON_CODE = re.compile(r"^[a-z0-9][a-z0-9_]{0,127}$")


class RuntimeHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class RuntimeReadiness(str, Enum):
    READY = "ready"
    NOT_READY = "not_ready"
    UNKNOWN = "unknown"


class _ClosedContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class LocalRuntimeResourceUsage(_ClosedContract):
    vram_used_bytes: int = Field(default=0, ge=0)
    vram_budget_bytes: int = Field(default=0, ge=0)
    ram_used_bytes: int = Field(default=0, ge=0)
    ram_budget_bytes: int = Field(default=0, ge=0)
    budget_status: Literal["within_budget", "exceeded", "unmeasured"] = "unmeasured"

    @model_validator(mode="after")
    def _usage_must_fit_declared_budgets(self) -> "LocalRuntimeResourceUsage":
        exceeded = (self.vram_budget_bytes and self.vram_used_bytes > self.vram_budget_bytes) or (
            self.ram_budget_bytes and self.ram_used_bytes > self.ram_budget_bytes
        )
        if self.budget_status == "exceeded" and not exceeded:
            raise ValueError("local_runtime_budget_status_invalid")
        if self.budget_status == "within_budget" and exceeded:
            raise ValueError("local_runtime_budget_status_invalid")
        if self.budget_status == "unmeasured" and (self.vram_used_bytes or self.ram_used_bytes):
            if self.vram_budget_bytes and self.vram_used_bytes > self.vram_budget_bytes:
                raise ValueError("local_runtime_vram_budget_exceeded")
            if self.ram_budget_bytes and self.ram_used_bytes > self.ram_budget_bytes:
                raise ValueError("local_runtime_ram_budget_exceeded")
            raise ValueError("local_runtime_budget_status_invalid")
        return self


class LocalRuntimeStatus(_ClosedContract):
    schema_version: Literal["ananta.local-model-runtime-status.v1"] = Field(
        default=LOCAL_RUNTIME_STATUS_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    snapshot_revision: int = Field(ge=1)
    runtime_id: Literal["kat", "lfm", "needle"]
    provider_id: str
    model_id: str
    execution_device: Literal["cuda", "cpu"]
    health: RuntimeHealth
    readiness: RuntimeReadiness
    reason_code: str
    effective_context: int = Field(ge=1, le=100_000_000)
    context_capacity: int = Field(ge=1, le=100_000_000)
    capabilities: tuple[str, ...] = ()
    available_models: tuple[str, ...] = ()
    resources: LocalRuntimeResourceUsage = LocalRuntimeResourceUsage()
    timeout_supported: bool
    cancellation_supported: bool
    candidate_only: bool = False
    orchestration_authority: Literal[False] = False

    @field_validator("provider_id", "model_id")
    @classmethod
    def _identifier(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not _IDENTIFIER.fullmatch(normalized):
            raise ValueError("local_runtime_identifier_invalid")
        return normalized

    @field_validator("reason_code")
    @classmethod
    def _reason_code(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not _REASON_CODE.fullmatch(normalized):
            raise ValueError("local_runtime_reason_code_invalid")
        return normalized

    @field_validator("capabilities", "available_models")
    @classmethod
    def _capabilities(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({str(value).strip().lower() for value in values if str(value).strip()}))
        if len(normalized) > 64 or any(not _IDENTIFIER.fullmatch(value) for value in normalized):
            raise ValueError("local_runtime_capabilities_invalid")
        return normalized

    @model_validator(mode="after")
    def _runtime_invariants(self) -> "LocalRuntimeStatus":
        if self.effective_context > self.context_capacity:
            raise ValueError("local_runtime_context_invalid")
        if self.runtime_id == "needle" and not self.candidate_only:
            raise ValueError("needle_runtime_must_be_candidate_only")
        if self.runtime_id != "needle" and self.candidate_only:
            raise ValueError("provider_runtime_must_not_be_candidate_only")
        if self.execution_device == "cpu" and self.resources.vram_budget_bytes:
            raise ValueError("cpu_runtime_must_not_reserve_vram")
        return self


class LocalRuntimeSnapshot(_ClosedContract):
    schema_version: Literal["ananta.local-model-runtime-snapshot.v1"] = Field(
        default=LOCAL_RUNTIME_SNAPSHOT_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    revision: int = Field(ge=1)
    generated_at: str = Field(min_length=1, max_length=64)
    total_vram_bytes: int = Field(ge=0)
    free_vram_bytes: int = Field(ge=0)
    available_ram_bytes: int = Field(ge=0)
    reserve_vram_bytes: int = Field(ge=0)
    runtimes: tuple[LocalRuntimeStatus, ...]

    @field_validator("runtimes")
    @classmethod
    def _runtime_set(cls, values: tuple[LocalRuntimeStatus, ...]) -> tuple[LocalRuntimeStatus, ...]:
        ordered = tuple(sorted(values, key=lambda value: value.runtime_id))
        if {value.runtime_id for value in ordered} != {"kat", "lfm", "needle"}:
            raise ValueError("local_runtime_snapshot_set_incomplete")
        if len({value.runtime_id for value in ordered}) != len(ordered):
            raise ValueError("local_runtime_snapshot_duplicate")
        return ordered

    @model_validator(mode="after")
    def _resource_totals(self) -> "LocalRuntimeSnapshot":
        if self.free_vram_bytes > self.total_vram_bytes:
            raise ValueError("local_runtime_resource_snapshot_invalid")
        return self

    def to_wire(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.model_dump(mode="json", by_alias=True))


class LocalRuntimeEffectiveContext(_ClosedContract):
    runtime_id: Literal["kat", "lfm", "needle"]
    context_tokens: int = Field(ge=1, le=100_000_000)


class LocalRuntimeActivationDecision(_ClosedContract):
    schema_version: Literal["ananta.local-model-runtime-activation-decision.v1"] = Field(
        default=LOCAL_RUNTIME_ACTIVATION_DECISION_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    decision_id: str
    request_id: str
    revision: int = Field(ge=1)
    admitted: bool
    reason_code: str
    start_order: tuple[Literal["kat", "lfm", "needle"], ...] = ()
    effective_contexts: tuple[LocalRuntimeEffectiveContext, ...] = ()
    total_vram_bytes: int = Field(ge=0)
    free_vram_bytes: int = Field(ge=0)
    available_ram_bytes: int = Field(ge=0)
    required_vram_bytes: int = Field(ge=0)
    reserve_vram_bytes: int = Field(ge=0)
    created_at: str = Field(min_length=1, max_length=64)
    decision_digest: str

    @field_validator("decision_id", "request_id")
    @classmethod
    def _decision_identifier(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not _IDENTIFIER.fullmatch(normalized):
            raise ValueError("local_runtime_decision_identifier_invalid")
        return normalized

    @field_validator("reason_code")
    @classmethod
    def _decision_reason(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not _REASON_CODE.fullmatch(normalized):
            raise ValueError("local_runtime_reason_code_invalid")
        return normalized

    @field_validator("decision_digest")
    @classmethod
    def _decision_digest(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
            raise ValueError("local_runtime_decision_digest_invalid")
        return normalized

    @field_validator("effective_contexts")
    @classmethod
    def _unique_contexts(
        cls,
        values: tuple[LocalRuntimeEffectiveContext, ...],
    ) -> tuple[LocalRuntimeEffectiveContext, ...]:
        ordered = tuple(sorted(values, key=lambda value: value.runtime_id))
        if len({value.runtime_id for value in ordered}) != len(ordered):
            raise ValueError("local_runtime_effective_context_duplicate")
        return ordered

    @model_validator(mode="after")
    def _decision_invariants(self) -> "LocalRuntimeActivationDecision":
        if self.free_vram_bytes > self.total_vram_bytes:
            raise ValueError("local_runtime_resource_snapshot_invalid")
        if self.admitted and set(self.start_order) != {"kat", "lfm", "needle"}:
            raise ValueError("local_runtime_admitted_start_order_invalid")
        if not self.admitted and self.start_order:
            raise ValueError("local_runtime_denied_start_order_invalid")
        return self

    def to_wire(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.model_dump(mode="json", by_alias=True))


class LocalRuntimeControlReceipt(_ClosedContract):
    schema_version: Literal["ananta.local-model-runtime-control-receipt.v1"] = Field(
        default=LOCAL_RUNTIME_CONTROL_RECEIPT_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    decision_id: str
    decision_digest: str
    action: Literal["activate", "deactivate", "restart"]
    status: Literal["accepted", "completed", "failed"]
    reason_code: str
    completed_at: str = Field(min_length=1, max_length=64)

    @field_validator("decision_id")
    @classmethod
    def _control_identifier(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not _IDENTIFIER.fullmatch(normalized):
            raise ValueError("local_runtime_decision_identifier_invalid")
        return normalized

    @field_validator("decision_digest")
    @classmethod
    def _control_digest(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
            raise ValueError("local_runtime_decision_digest_invalid")
        return normalized

    @field_validator("reason_code")
    @classmethod
    def _control_reason(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not _REASON_CODE.fullmatch(normalized):
            raise ValueError("local_runtime_reason_code_invalid")
        return normalized


class LocalRuntimeInvocationObservation(_ClosedContract):
    """Bounded operational facts; request and response content is excluded."""

    schema_version: Literal["ananta.local-model-runtime-invocation.v1"] = Field(
        default=LOCAL_RUNTIME_INVOCATION_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    invocation_id: str
    observed_at: str = Field(min_length=1, max_length=64)
    runtime_id: Literal["kat", "lfm", "needle"]
    provider_id: str
    model_id: str
    profile_id: str
    goal_id: str | None = Field(default=None, max_length=128)
    task_id: str | None = Field(default=None, max_length=128)
    success: bool
    reason_code: str
    latency_ms: int = Field(ge=0, le=86_400_000)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    context_capacity: int = Field(ge=1, le=100_000_000)
    fallback_index: int = Field(ge=0, le=64)
    confidence_available: bool
    candidate_only: bool = False
    candidate_status: str | None = Field(default=None, max_length=64)
    candidate_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    prompt_chars: int = Field(default=0, ge=0, le=10_000_000)
    readiness: RuntimeReadiness
    resource_reason_code: str
    free_vram_bytes: int = Field(ge=0)
    available_ram_bytes: int = Field(ge=0)

    @field_validator("invocation_id", "provider_id", "model_id", "profile_id")
    @classmethod
    def _invocation_identifier(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not _IDENTIFIER.fullmatch(normalized):
            raise ValueError("local_runtime_invocation_identifier_invalid")
        return normalized

    @field_validator("goal_id", "task_id")
    @classmethod
    def _correlation_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().lower()
        if not normalized:
            return None
        if not _IDENTIFIER.fullmatch(normalized):
            raise ValueError("local_runtime_invocation_correlation_invalid")
        return normalized

    @field_validator("reason_code", "resource_reason_code")
    @classmethod
    def _invocation_reason(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not _REASON_CODE.fullmatch(normalized):
            raise ValueError("local_runtime_reason_code_invalid")
        return normalized

    @model_validator(mode="after")
    def _token_total(self) -> "LocalRuntimeInvocationObservation":
        if self.total_tokens and self.total_tokens < self.prompt_tokens + self.completion_tokens:
            raise ValueError("local_runtime_invocation_token_total_invalid")
        if self.runtime_id == "needle":
            if not self.candidate_only or self.prompt_tokens or self.completion_tokens or self.total_tokens:
                raise ValueError("needle_invocation_must_be_candidate_only")
            if self.candidate_status is None:
                raise ValueError("needle_candidate_status_required")
        elif self.candidate_only or self.candidate_status is not None or self.candidate_confidence is not None:
            raise ValueError("provider_invocation_candidate_fields_invalid")
        return self

    def to_wire(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.model_dump(mode="json", by_alias=True))


__all__ = [
    "LOCAL_RUNTIME_SNAPSHOT_SCHEMA",
    "LOCAL_RUNTIME_STATUS_SCHEMA",
    "LOCAL_RUNTIME_ACTIVATION_DECISION_SCHEMA",
    "LOCAL_RUNTIME_CONTROL_RECEIPT_SCHEMA",
    "LOCAL_RUNTIME_INVOCATION_SCHEMA",
    "LocalRuntimeActivationDecision",
    "LocalRuntimeControlReceipt",
    "LocalRuntimeEffectiveContext",
    "LocalRuntimeInvocationObservation",
    "LocalRuntimeResourceUsage",
    "LocalRuntimeSnapshot",
    "LocalRuntimeStatus",
    "RuntimeHealth",
    "RuntimeReadiness",
]
