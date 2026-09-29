"""Temporal workflow step and workflow input contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from ananta_contracts.temporal_workflow_primitives import (
    _DIGEST_RE,
    WORKFLOW_INPUT_SCHEMA,
    WORKFLOW_STEP_SCHEMA,
    TemporalContractError,
    _bounded_strings,
    _contains_sensitive_keys,
    _identifier,
    _mapping,
)
from ananta_contracts.temporal_workflow_references import ActivityClass, ArtifactReference, AuthorizationEnvelopeRef

from .workflow_operation import operation_id_for


@dataclass(frozen=True)
class TemporalWorkflowStep:
    step_id: str
    title: str
    operation_id: str
    authorization_envelope: AuthorizationEnvelopeRef
    depends_on: tuple[str, ...] = ()
    artifact_refs: tuple[ArtifactReference, ...] = ()
    activity_class: ActivityClass = ActivityClass.IDEMPOTENT
    gate: bool = False
    task_kind: str = "coding"
    required_capabilities: tuple[str, ...] = ()
    node_type: str = "task"
    parallel_group: str = "default"
    merge_strategy: str = ""
    partial_failure: str = "fail"
    schema: str = WORKFLOW_STEP_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != WORKFLOW_STEP_SCHEMA:
            raise TemporalContractError("unsupported_step_schema", "workflow step schema is unsupported")
        _identifier(self.step_id, field_name="step_id")
        _identifier(self.operation_id, field_name="operation_id")
        _identifier(self.task_kind, field_name="task_kind")
        if not self.title or len(self.title) > 512 or "\x00" in self.title:
            raise TemporalContractError("invalid_step_title", "step title is invalid")
        _bounded_strings(self.depends_on, field_name="depends_on")
        _bounded_strings(self.required_capabilities, field_name="required_capabilities")
        if self.node_type not in {"task", "merge", "checkpoint", "component"}:
            raise TemporalContractError("invalid_step_node_type", "workflow step node type is unsupported")
        _identifier(self.parallel_group, field_name="parallel_group")
        if self.partial_failure not in {"fail", "omit"}:
            raise TemporalContractError(
                "invalid_partial_failure_policy",
                "partial failure policy is unsupported",
            )
        if self.node_type == "merge":
            if self.merge_strategy not in {"ordered_artifact_refs", "object_by_step_id"}:
                raise TemporalContractError(
                    "invalid_merge_strategy",
                    "merge steps require a deterministic merge strategy",
                )
            if not self.depends_on:
                raise TemporalContractError(
                    "merge_dependencies_required",
                    "merge steps require explicit dependencies",
                )
        elif self.merge_strategy:
            raise TemporalContractError(
                "merge_strategy_requires_merge_step",
                "only merge steps may declare a merge strategy",
            )
        elif self.partial_failure != "fail":
            raise TemporalContractError(
                "partial_failure_requires_merge_step",
                "only merge steps may omit failed branches",
            )

    @classmethod
    def from_mapping(
        cls,
        raw: object,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        plan_hash: str,
        inherited_authorization: Mapping[str, Any] | None = None,
    ) -> "TemporalWorkflowStep":
        if not isinstance(raw, Mapping):
            raise TemporalContractError("invalid_workflow_step", "workflow step must be an object")
        step_id = _identifier(raw.get("step_id") or raw.get("id"), field_name="step_id")
        operation_id = str(raw.get("operation_id") or "").strip()
        if not operation_id:
            operation_id = operation_id_for(
                tenant_id=tenant_id,
                run_id=run_id,
                step_id=step_id,
                declared_operation="hub_task",
            )
        auth_raw = raw.get("authorization_envelope") or inherited_authorization
        authorization = AuthorizationEnvelopeRef.from_mapping(auth_raw)
        authorization.validate_binding(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            plan_hash=plan_hash,
        )
        raw_class = str(raw.get("activity_class") or raw.get("side_effect_class") or "long_running").strip()
        try:
            activity_class = ActivityClass(raw_class)
        except ValueError as exc:
            raise TemporalContractError("invalid_activity_class", "activity class is unsupported") from exc
        raw_artifacts = raw.get("artifact_refs") or raw.get("input_artifacts") or ()
        if isinstance(raw_artifacts, (str, bytes)) or not isinstance(raw_artifacts, Sequence):
            raise TemporalContractError("invalid_artifact_references", "artifact references must be a sequence")
        return cls(
            schema=str(raw.get("schema") or WORKFLOW_STEP_SCHEMA),
            step_id=step_id,
            title=str(raw.get("title") or raw.get("label") or step_id).strip(),
            operation_id=operation_id,
            authorization_envelope=authorization,
            depends_on=_bounded_strings(raw.get("depends_on"), field_name="depends_on"),
            artifact_refs=tuple(ArtifactReference.from_mapping(item) for item in raw_artifacts),
            activity_class=activity_class,
            gate=bool(raw.get("gate", False)),
            task_kind=str(raw.get("task_kind") or "coding").strip(),
            required_capabilities=_bounded_strings(
                raw.get("required_capabilities"), field_name="required_capabilities"
            ),
            node_type=str(raw.get("node_type") or "task").strip(),
            parallel_group=str(raw.get("parallel_group") or "default").strip(),
            merge_strategy=str(raw.get("merge_strategy") or "").strip(),
            partial_failure=str(raw.get("partial_failure") or "fail").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["activity_class"] = self.activity_class.value
        payload["authorization_envelope"] = self.authorization_envelope.to_dict()
        payload["depends_on"] = list(self.depends_on)
        payload["artifact_refs"] = [item.to_dict() for item in self.artifact_refs]
        payload["required_capabilities"] = list(self.required_capabilities)
        return payload


@dataclass(frozen=True)
class AnantaWorkflowInput:
    tenant_id: str
    workflow_id: str
    run_id: str
    correlation_id: str
    plan_hash: str
    policy_version: str
    steps: tuple[TemporalWorkflowStep, ...]
    retry_budget_remaining: int
    retry_budget_maximum: int | None = None
    mutable_parameters: tuple[str, ...] = ()
    parameters: Mapping[str, Any] = field(default_factory=dict)
    max_parallel_steps: int = 1
    tenant_parallel_limit: int = 1
    worker_parallel_limit: int = 1
    max_history_events: int = 20_000
    max_state_bytes: int = 512_000
    schema: str = WORKFLOW_INPUT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != WORKFLOW_INPUT_SCHEMA:
            raise TemporalContractError("unsupported_workflow_input_schema", "workflow input schema is unsupported")
        for name, value in (
            ("tenant_id", self.tenant_id),
            ("workflow_id", self.workflow_id),
            ("run_id", self.run_id),
            ("correlation_id", self.correlation_id),
            ("policy_version", self.policy_version),
        ):
            _identifier(value, field_name=name)
        if not _DIGEST_RE.fullmatch(self.plan_hash):
            raise TemporalContractError("invalid_plan_hash", "plan_hash must be sha256")
        if not self.steps or len(self.steps) > 1_000:
            raise TemporalContractError("invalid_steps", "workflow requires between 1 and 1000 steps")
        step_ids = tuple(step.step_id for step in self.steps)
        if len(step_ids) != len(set(step_ids)):
            raise TemporalContractError("duplicate_step_id", "workflow step IDs must be unique")
        known: set[str] = set()
        for step in self.steps:
            if any(dependency not in known for dependency in step.depends_on):
                raise TemporalContractError("invalid_step_order", "dependencies must refer to an earlier workflow step")
            known.add(step.step_id)
        if isinstance(self.retry_budget_remaining, bool) or not 0 <= self.retry_budget_remaining <= 1_000:
            raise TemporalContractError("invalid_retry_budget", "retry budget is outside its bounds")
        maximum = self.retry_budget_remaining if self.retry_budget_maximum is None else self.retry_budget_maximum
        if (
            isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or not self.retry_budget_remaining <= maximum <= 1_000
        ):
            raise TemporalContractError("invalid_retry_budget_maximum", "retry budget maximum is invalid")
        object.__setattr__(self, "retry_budget_maximum", maximum)
        _bounded_strings(self.mutable_parameters, field_name="mutable_parameters", maximum=64)
        parameters = _mapping(self.parameters, field_name="parameters")
        if _contains_sensitive_keys(parameters):
            raise TemporalContractError("embedded_secret_denied", "parameters must contain references, not secrets")
        forbidden = set(parameters) - set(self.mutable_parameters)
        if forbidden:
            raise TemporalContractError("immutable_parameter", "parameters include undeclared mutable keys")
        for field_name, value in (
            ("max_parallel_steps", self.max_parallel_steps),
            ("tenant_parallel_limit", self.tenant_parallel_limit),
            ("worker_parallel_limit", self.worker_parallel_limit),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 128:
                raise TemporalContractError(
                    "invalid_parallel_limit",
                    f"{field_name} is outside its bounds",
                )
        if not 100 <= self.max_history_events <= 1_000_000:
            raise TemporalContractError("invalid_history_limit", "history event limit is outside its bounds")
        if not 16_384 <= self.max_state_bytes <= 16_777_216:
            raise TemporalContractError("invalid_state_limit", "state size limit is outside its bounds")

    @classmethod
    def from_mapping(cls, raw: object, *, runtime_run_id: str = "") -> "AnantaWorkflowInput":
        if not isinstance(raw, Mapping):
            raise TemporalContractError("invalid_workflow_input", "workflow input must be an object")
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), Mapping) else {}
        policy_scope = raw.get("policy_scope") if isinstance(raw.get("policy_scope"), Mapping) else {}
        tenant_id = str(raw.get("tenant_id") or metadata.get("tenant_id") or policy_scope.get("tenant_id") or "")
        workflow_id = str(raw.get("workflow_id") or "")
        run_id = str(raw.get("run_id") or runtime_run_id or "")
        plan_hash = str(raw.get("plan_hash") or metadata.get("plan_hash") or "")
        policy_version = str(raw.get("policy_version") or metadata.get("policy_version") or "")
        inherited_auth = raw.get("authorization_envelope")
        raw_steps = raw.get("steps")
        if isinstance(raw_steps, (str, bytes)) or not isinstance(raw_steps, Sequence):
            raise TemporalContractError("invalid_steps", "steps must be a sequence")
        steps = tuple(
            TemporalWorkflowStep.from_mapping(
                step,
                tenant_id=tenant_id,
                workflow_id=workflow_id,
                run_id=run_id,
                plan_hash=plan_hash,
                inherited_authorization=inherited_auth if isinstance(inherited_auth, Mapping) else None,
            )
            for step in raw_steps
        )
        return cls(
            schema=str(raw.get("schema") or WORKFLOW_INPUT_SCHEMA),
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
            correlation_id=str(raw.get("correlation_id") or ""),
            plan_hash=plan_hash,
            policy_version=policy_version,
            steps=steps,
            retry_budget_remaining=int(raw.get("retry_budget_remaining", 0)),
            retry_budget_maximum=int(raw.get("retry_budget_maximum", raw.get("retry_budget_remaining", 0))),
            mutable_parameters=_bounded_strings(
                raw.get("mutable_parameters"), field_name="mutable_parameters", maximum=64
            ),
            parameters=_mapping(raw.get("parameters"), field_name="parameters"),
            max_parallel_steps=int(raw.get("max_parallel_steps", 1)),
            tenant_parallel_limit=int(raw.get("tenant_parallel_limit", 1)),
            worker_parallel_limit=int(raw.get("worker_parallel_limit", 1)),
            max_history_events=int(raw.get("max_history_events", 20_000)),
            max_state_bytes=int(raw.get("max_state_bytes", 512_000)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "correlation_id": self.correlation_id,
            "plan_hash": self.plan_hash,
            "policy_version": self.policy_version,
            "steps": [step.to_dict() for step in self.steps],
            "retry_budget_remaining": self.retry_budget_remaining,
            "retry_budget_maximum": self.retry_budget_maximum,
            "mutable_parameters": list(self.mutable_parameters),
            "parameters": dict(self.parameters),
            "max_parallel_steps": self.max_parallel_steps,
            "tenant_parallel_limit": self.tenant_parallel_limit,
            "worker_parallel_limit": self.worker_parallel_limit,
            "max_history_events": self.max_history_events,
            "max_state_bytes": self.max_state_bytes,
        }
