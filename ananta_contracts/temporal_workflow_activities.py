"""Temporal step activity input and result contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from ananta_contracts.temporal_workflow_primitives import (
    _DIGEST_RE,
    ACTIVITY_INPUT_SCHEMA,
    ACTIVITY_RESULT_SCHEMA,
    TemporalContractError,
    _bounded_strings,
    _contains_sensitive_keys,
    _identifier,
    _mapping,
)
from ananta_contracts.temporal_workflow_references import ActivityClass, ArtifactReference, AuthorizationEnvelopeRef


@dataclass(frozen=True)
class StepActivityInput:
    tenant_id: str
    workflow_id: str
    run_id: str
    correlation_id: str
    step_id: str
    operation_id: str
    plan_hash: str
    task_kind: str
    authorization_envelope: AuthorizationEnvelopeRef
    artifact_refs: tuple[ArtifactReference, ...]
    required_capabilities: tuple[str, ...]
    activity_class: ActivityClass
    retry_budget_remaining: int
    retry_budget_maximum: int | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    node_type: str = "task"
    parallel_group: str = "default"
    merge_strategy: str = ""
    partial_failure: str = "fail"
    schema: str = ACTIVITY_INPUT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != ACTIVITY_INPUT_SCHEMA:
            raise TemporalContractError("unsupported_activity_input_schema", "activity input schema is unsupported")
        for name, value in (
            ("tenant_id", self.tenant_id),
            ("workflow_id", self.workflow_id),
            ("run_id", self.run_id),
            ("correlation_id", self.correlation_id),
            ("step_id", self.step_id),
            ("operation_id", self.operation_id),
            ("task_kind", self.task_kind),
        ):
            _identifier(value, field_name=name)
        if not _DIGEST_RE.fullmatch(self.plan_hash):
            raise TemporalContractError("invalid_plan_hash", "plan_hash must be sha256")
        self.authorization_envelope.validate_binding(
            tenant_id=self.tenant_id,
            workflow_id=self.workflow_id,
            run_id=self.run_id,
            step_id=self.step_id,
            plan_hash=self.plan_hash,
        )
        _mapping(self.parameters, field_name="activity_parameters")
        if _contains_sensitive_keys(self.parameters):
            raise TemporalContractError("embedded_secret_denied", "activity parameters contain a secret")
        if isinstance(self.retry_budget_remaining, bool) or self.retry_budget_remaining < 0:
            raise TemporalContractError("invalid_retry_budget", "retry budget is invalid")
        maximum = self.retry_budget_remaining if self.retry_budget_maximum is None else self.retry_budget_maximum
        if (
            isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or maximum < self.retry_budget_remaining
            or maximum > 1_000
        ):
            raise TemporalContractError("invalid_retry_budget_maximum", "retry budget maximum is invalid")
        object.__setattr__(self, "retry_budget_maximum", maximum)
        if self.node_type not in {"task", "merge", "checkpoint", "component"}:
            raise TemporalContractError("invalid_step_node_type", "activity node type is unsupported")
        _identifier(self.parallel_group, field_name="parallel_group")
        if self.partial_failure not in {"fail", "omit"}:
            raise TemporalContractError(
                "invalid_partial_failure_policy",
                "partial failure policy is unsupported",
            )
        if self.node_type == "merge":
            if self.merge_strategy not in {"ordered_artifact_refs", "object_by_step_id"}:
                raise TemporalContractError("invalid_merge_strategy", "activity merge strategy is unsupported")
        elif self.merge_strategy:
            raise TemporalContractError(
                "merge_strategy_requires_merge_step",
                "only merge activities may declare a merge strategy",
            )
        elif self.partial_failure != "fail":
            raise TemporalContractError(
                "partial_failure_requires_merge_step",
                "only merge activities may omit failed branches",
            )

    @classmethod
    def from_mapping(cls, raw: object) -> "StepActivityInput":
        if not isinstance(raw, Mapping):
            raise TemporalContractError("invalid_activity_input", "activity input must be an object")
        raw_artifacts = raw.get("artifact_refs")
        if raw_artifacts is None:
            raw_artifacts = ()
        if isinstance(raw_artifacts, (str, bytes)) or not isinstance(raw_artifacts, Sequence):
            raise TemporalContractError("invalid_artifact_references", "artifact references must be a sequence")
        try:
            activity_class = ActivityClass(str(raw.get("activity_class") or ""))
        except ValueError as exc:
            raise TemporalContractError("invalid_activity_class", "activity class is unsupported") from exc
        try:
            retry_budget_remaining = int(raw.get("retry_budget_remaining", 0))
        except (TypeError, ValueError) as exc:
            raise TemporalContractError("invalid_retry_budget", "retry budget is invalid") from exc
        return cls(
            schema=str(raw.get("schema") or ""),
            tenant_id=str(raw.get("tenant_id") or ""),
            workflow_id=str(raw.get("workflow_id") or ""),
            run_id=str(raw.get("run_id") or ""),
            correlation_id=str(raw.get("correlation_id") or ""),
            step_id=str(raw.get("step_id") or ""),
            operation_id=str(raw.get("operation_id") or ""),
            plan_hash=str(raw.get("plan_hash") or ""),
            task_kind=str(raw.get("task_kind") or ""),
            authorization_envelope=AuthorizationEnvelopeRef.from_mapping(raw.get("authorization_envelope")),
            artifact_refs=tuple(ArtifactReference.from_mapping(item) for item in raw_artifacts),
            required_capabilities=_bounded_strings(
                raw.get("required_capabilities"), field_name="required_capabilities"
            ),
            activity_class=activity_class,
            retry_budget_remaining=retry_budget_remaining,
            retry_budget_maximum=int(raw.get("retry_budget_maximum", retry_budget_remaining)),
            parameters=_mapping(raw.get("parameters"), field_name="activity_parameters"),
            node_type=str(raw.get("node_type") or "task").strip(),
            parallel_group=str(raw.get("parallel_group") or "default").strip(),
            merge_strategy=str(raw.get("merge_strategy") or "").strip(),
            partial_failure=str(raw.get("partial_failure") or "fail").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["authorization_envelope"] = self.authorization_envelope.to_dict()
        payload["artifact_refs"] = [item.to_dict() for item in self.artifact_refs]
        payload["required_capabilities"] = list(self.required_capabilities)
        payload["activity_class"] = self.activity_class.value
        payload["parameters"] = dict(self.parameters)
        return payload


@dataclass(frozen=True)
class StepActivityResult:
    operation_id: str
    status: str
    hub_task_id: str
    artifact_refs: tuple[ArtifactReference, ...] = ()
    canonical_event_refs: tuple[str, ...] = ()
    attempt: int = 1
    reason_code: str = ""
    schema: str = ACTIVITY_RESULT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != ACTIVITY_RESULT_SCHEMA:
            raise TemporalContractError("unsupported_activity_result_schema", "activity result schema is unsupported")
        _identifier(self.operation_id, field_name="operation_id")
        if self.hub_task_id:
            _identifier(self.hub_task_id, field_name="hub_task_id")
        if self.status not in {"completed", "failed", "cancelled", "uncertain"}:
            raise TemporalContractError("invalid_activity_status", "activity status is unsupported")
        if isinstance(self.attempt, bool) or self.attempt < 1 or self.attempt > 1_000:
            raise TemporalContractError("invalid_activity_attempt", "activity attempt is invalid")
        _bounded_strings(self.canonical_event_refs, field_name="canonical_event_refs")
        if len(self.reason_code) > 256:
            raise TemporalContractError("invalid_reason_code", "reason code is too long")

    @classmethod
    def from_mapping(cls, raw: object) -> "StepActivityResult":
        if not isinstance(raw, Mapping):
            raise TemporalContractError("invalid_activity_result", "activity result must be an object")
        raw_artifacts = raw.get("artifact_refs") or ()
        if isinstance(raw_artifacts, (str, bytes)) or not isinstance(raw_artifacts, Sequence):
            raise TemporalContractError("invalid_artifact_references", "artifact references must be a sequence")
        return cls(
            schema=str(raw.get("schema") or ""),
            operation_id=str(raw.get("operation_id") or ""),
            status=str(raw.get("status") or ""),
            hub_task_id=str(raw.get("hub_task_id") or ""),
            artifact_refs=tuple(ArtifactReference.from_mapping(item) for item in raw_artifacts),
            canonical_event_refs=_bounded_strings(raw.get("canonical_event_refs"), field_name="canonical_event_refs"),
            attempt=int(raw.get("attempt") or 1),
            reason_code=str(raw.get("reason_code") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "operation_id": self.operation_id,
            "status": self.status,
            "hub_task_id": self.hub_task_id,
            "artifact_refs": [item.to_dict() for item in self.artifact_refs],
            "canonical_event_refs": list(self.canonical_event_refs),
            "attempt": self.attempt,
            "reason_code": self.reason_code,
        }
