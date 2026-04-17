from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from sqlmodel import Field, SQLModel


class EvolutionCapability(str, Enum):
    ANALYZE = "analyze"
    PROPOSE = "propose"
    VALIDATE = "validate"
    APPLY = "apply"
    RISK_SCORING = "risk_scoring"
    REVIEW_HINTS = "review_hints"


class EvolutionTriggerType(str, Enum):
    MANUAL = "manual"
    VERIFICATION_FAILURE = "verification_failure"
    ERROR_THRESHOLD = "error_threshold"
    PERIODIC_REVIEW = "periodic_review"
    POLICY_REQUEST = "policy_request"


class EvolutionTrigger(SQLModel):
    trigger_type: EvolutionTriggerType = EvolutionTriggerType.MANUAL
    source: str = "hub"
    actor: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvolutionProviderDescriptor(SQLModel):
    provider_name: str
    version: str = "unknown"
    status: str = "unknown"
    capabilities: list[EvolutionCapability] = Field(default_factory=list)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class EvolutionContext(SQLModel):
    context_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    objective: str
    task_id: str | None = None
    goal_id: str | None = None
    trace_id: str | None = None
    plan_id: str | None = None
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    signals: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)


class EvolutionProposal(SQLModel):
    proposal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: str
    proposal_type: str = "improvement"
    target_refs: list[dict[str, Any]] = Field(default_factory=list)
    rationale: str | None = None
    risk_level: str = "unknown"
    confidence: float | None = None
    requires_review: bool = True
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    raw_payload: dict[str, Any] | None = None


class ValidationResult(SQLModel):
    validation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    proposal_id: str | None = None
    status: str = "not_run"
    valid: bool = False
    reasons: list[str] = Field(default_factory=list)
    checks: list[dict[str, Any]] = Field(default_factory=list)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    raw_payload: dict[str, Any] | None = None


class ApplyResult(SQLModel):
    apply_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    proposal_id: str | None = None
    status: str = "not_run"
    applied: bool = False
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    raw_payload: dict[str, Any] | None = None


class EvolutionResult(SQLModel):
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    provider_name: str
    status: str = "completed"
    summary: str = ""
    proposals: list[EvolutionProposal] = Field(default_factory=list)
    validation_results: list[ValidationResult] = Field(default_factory=list)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    raw_payload: dict[str, Any] | None = None
    created_at: float = Field(default_factory=time.time)


class PersistedEvolutionAnalysis(SQLModel):
    run_id: str
    provider_name: str
    status: str
    proposal_ids: list[str] = Field(default_factory=list)
    result: EvolutionResult
