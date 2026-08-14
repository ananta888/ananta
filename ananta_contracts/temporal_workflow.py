"""Versioned transport contracts for Ananta's optional Temporal runtime.

The contracts in this module are deliberately free of Temporal SDK, database,
network and hub implementation imports.  They can therefore cross the
hub/Temporal-worker container boundary without making Temporal a control plane.
Only identifiers, signed authorization material and artifact references are
transported; prompts, credentials and artifact payloads do not belong here.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from .provider_execution import (
    ProviderBindingAuthorization,
    ProviderProfileAttemptPlanEntry,
)
from .workflow_operation import operation_id_for

WORKFLOW_INPUT_SCHEMA = "ananta.temporal-workflow-input.v1"
WORKFLOW_STEP_SCHEMA = "ananta.temporal-workflow-step.v1"
ACTIVITY_INPUT_SCHEMA = "ananta.temporal-activity-input.v1"
ACTIVITY_RESULT_SCHEMA = "ananta.temporal-activity-result.v1"
LEGACY_COMMAND_SCHEMA = "ananta.workflow_command.v2"
COMMAND_SCHEMA = "ananta.workflow_command.v3"
COMMAND_RESULT_SCHEMA = "ananta.temporal-workflow-command-result.v2"
COMMAND_AUTHORITY_ACTIVITY = "ananta.temporal.verify-workflow-command.v1"
COMMAND_AUTHORITY_RESULT_SCHEMA = "ananta.temporal-command-authority-result.v1"
STATUS_SCHEMA = "ananta.temporal-workflow-status.v1"
PROBE_SCHEMA = "ananta.temporal-probe.v1"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_DIGEST_RE = re.compile(r"^(?:sha256:)?[a-fA-F0-9]{64}$")
_COMMAND_SIGNATURE_ALGORITHMS = frozenset({"ed25519", "hmac-sha256"})
_COMMAND_SEMANTIC_PAYLOAD_SCHEMA = "ananta.workflow-command-semantic-payload.v1"
_COMMAND_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "cookie",
        "credential",
        "password",
        "private_key",
        "prompt",
        "raw_content",
        "secret",
        "token",
    }
)
_SAFE_TOKEN_KEYS = frozenset(
    {
        "cached_tokens",
        "fencing_token",
        "input_tokens",
        "max_tokens",
        "output_tokens",
        "reasoning_tokens",
        "token_count",
        "token_usage",
    }
)


class TemporalContractError(ValueError):
    """A fail-closed wire-contract validation error with a stable reason."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = str(reason_code or "invalid_temporal_contract")


def _bounded_integer(
    value: object,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
    reason_code: str,
) -> int:
    """Parse a wire integer without bool coercion, truncation or overflow."""

    try:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise TypeError
        if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
            raise ValueError
        normalized = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TemporalContractError(
            reason_code,
            f"{field_name} is invalid",
        ) from exc
    if not minimum <= normalized <= maximum:
        raise TemporalContractError(reason_code, f"{field_name} is invalid")
    return normalized


def _bounded_float(
    value: object,
    *,
    field_name: str,
    minimum: float,
    maximum: float,
    reason_code: str,
) -> float:
    """Parse a finite wire float within an explicit interoperable range."""

    try:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise TypeError
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TemporalContractError(
            reason_code,
            f"{field_name} is invalid",
        ) from exc
    if not math.isfinite(normalized) or not minimum <= normalized <= maximum:
        raise TemporalContractError(reason_code, f"{field_name} is invalid")
    return normalized


def _workflow_command_numeric_fields(
    raw: Mapping[str, Any],
) -> tuple[int, float, float]:
    expected_revision = _bounded_integer(
        raw.get("expected_revision"),
        field_name="expected_revision",
        minimum=0,
        maximum=_COMMAND_MAX_SAFE_INTEGER,
        reason_code="invalid_command_revision",
    )
    issued_at = _bounded_float(
        raw.get("issued_at"),
        field_name="issued_at",
        minimum=0.0,
        maximum=float(_COMMAND_MAX_SAFE_INTEGER),
        reason_code="invalid_command_issued_at",
    )
    expires_at = _bounded_float(
        raw.get("expires_at"),
        field_name="expires_at",
        minimum=0.0,
        maximum=float(_COMMAND_MAX_SAFE_INTEGER),
        reason_code="invalid_command_expires_at",
    )
    if expires_at <= issued_at:
        raise TemporalContractError(
            "invalid_command_expiry",
            "workflow command expiry is invalid",
        )
    return expected_revision, issued_at, expires_at


def _identifier(value: object, *, field_name: str, required: bool = True) -> str:
    normalized = str(value or "").strip()
    if not normalized and not required:
        return ""
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise TemporalContractError("invalid_identifier", f"{field_name} is invalid")
    return normalized


def _bounded_strings(
    value: object,
    *,
    field_name: str,
    maximum: int = 128,
    identifiers: bool = True,
) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TemporalContractError("invalid_sequence", f"{field_name} must be a sequence")
    normalized: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        if identifiers:
            text = _identifier(text, field_name=field_name)
        elif len(text) > 512 or "\x00" in text:
            raise TemporalContractError("invalid_value", f"{field_name} contains an invalid value")
        if text not in normalized:
            normalized.append(text)
    if len(normalized) > maximum:
        raise TemporalContractError("sequence_too_large", f"{field_name} exceeds {maximum} entries")
    return tuple(normalized)


def _bounded_contract_items(
    value: object,
    *,
    field_name: str,
    maximum: int = 8,
) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TemporalContractError(
            "invalid_sequence",
            f"{field_name} must be a sequence",
        )
    items = tuple(value)
    if len(items) > maximum:
        raise TemporalContractError(
            "sequence_too_large",
            f"{field_name} exceeds {maximum} entries",
        )
    return items


def _mapping(value: object, *, field_name: str, maximum_bytes: int = 32_768) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TemporalContractError("invalid_mapping", f"{field_name} must be an object")
    payload = {str(key): item for key, item in value.items()}
    try:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise TemporalContractError("invalid_mapping", f"{field_name} is not JSON serializable") from exc
    if len(encoded.encode("utf-8")) > maximum_bytes:
        raise TemporalContractError("mapping_too_large", f"{field_name} exceeds its size limit")
    return payload


def redact_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a deterministic, recursively redacted diagnostic mapping."""

    def _redact(item: Any, key: str = "") -> Any:
        lowered = key.strip().lower()
        if _is_sensitive_key(lowered):
            return "[REDACTED]"
        if isinstance(item, Mapping):
            return {str(k): _redact(v, str(k)) for k, v in sorted(item.items(), key=lambda pair: str(pair[0]))}
        if isinstance(item, (list, tuple)):
            return [_redact(entry) for entry in item[:128]]
        if isinstance(item, str):
            return item[:512]
        if isinstance(item, (bool, int, float)) or item is None:
            return item
        return str(item)[:128]

    return _redact(dict(value or {}))


def _contains_sensitive_keys(value: Any) -> bool:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).strip().lower()
            if _is_sensitive_key(key):
                return True
            if _contains_sensitive_keys(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_keys(item) for item in value)
    return False


def _is_sensitive_key(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized not in _SAFE_TOKEN_KEYS and any(
        normalized == part or normalized.endswith(f"_{part}") or normalized.startswith(f"{part}_")
        for part in _SENSITIVE_KEYS
    )


class WorkflowPhase(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowCommandType(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"
    REQUEST_CHANGES = "request_changes"
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    RETRY = "retry"
    PARAMETER_UPDATE = "parameter_update"


class ActivityClass(str, Enum):
    READ_ONLY = "read_only"
    IDEMPOTENT = "idempotent"
    NON_IDEMPOTENT = "non_idempotent"
    LONG_RUNNING = "long_running"


@dataclass(frozen=True)
class ArtifactReference:
    artifact_id: str
    kind: str = "workflow_artifact"
    digest: str = ""
    schema: str = "ananta.artifact-reference.v1"

    def __post_init__(self) -> None:
        _identifier(self.artifact_id, field_name="artifact_id")
        _identifier(self.kind, field_name="artifact_kind")
        if self.digest and not _DIGEST_RE.fullmatch(self.digest):
            raise TemporalContractError("invalid_artifact_digest", "artifact digest must be sha256")

    @classmethod
    def from_mapping(cls, raw: object) -> "ArtifactReference":
        if isinstance(raw, str):
            return cls(artifact_id=raw)
        if not isinstance(raw, Mapping):
            raise TemporalContractError("invalid_artifact_reference", "artifact reference must be an object")
        return cls(
            artifact_id=str(raw.get("artifact_id") or raw.get("id") or ""),
            kind=str(raw.get("kind") or "workflow_artifact"),
            digest=str(raw.get("digest") or ""),
            schema=str(raw.get("schema") or "ananta.artifact-reference.v1"),
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class AuthorizationEnvelopeRef:
    """Signed, content-free authority passed to a Temporal Activity.

    Signature verification and revocation are performed by the shared runtime
    authorization service.  This contract only enforces structural integrity
    and binding fields so malformed envelopes never enter workflow state.
    """

    envelope_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    plan_hash: str
    policy_version: str
    allowed_tools: tuple[str, ...]
    allowed_artifacts: tuple[str, ...]
    budgets: Mapping[str, int | float]
    issued_at: float
    expires_at: float
    nonce: str
    key_id: str
    signature: str
    allowed_provider_bindings: tuple[ProviderBindingAuthorization, ...] = ()
    provider_attempt_plan: tuple[ProviderProfileAttemptPlanEntry, ...] = ()
    schema: str = "ananta.runtime_authorization.v1"

    def __post_init__(self) -> None:
        if self.schema != "ananta.runtime_authorization.v1":
            raise TemporalContractError("unsupported_authorization_schema", "authorization schema is unsupported")
        for name, value in (
            ("envelope_id", self.envelope_id),
            ("tenant_id", self.tenant_id),
            ("workflow_id", self.workflow_id),
            ("run_id", self.run_id),
            ("step_id", self.step_id),
            ("policy_version", self.policy_version),
            ("nonce", self.nonce),
            ("key_id", self.key_id),
        ):
            _identifier(value, field_name=name)
        if not _DIGEST_RE.fullmatch(self.plan_hash):
            raise TemporalContractError("invalid_plan_hash", "plan_hash must be sha256")
        if (
            not isinstance(self.issued_at, (int, float))
            or isinstance(self.issued_at, bool)
            or not isinstance(self.expires_at, (int, float))
            or isinstance(self.expires_at, bool)
            or self.issued_at <= 0
            or self.expires_at <= self.issued_at
        ):
            raise TemporalContractError("invalid_authorization_expiry", "authorization expiry is invalid")
        if not self.signature or len(self.signature) > 4096 or "\x00" in self.signature:
            raise TemporalContractError("invalid_authorization_signature", "authorization signature is invalid")
        _bounded_strings(self.allowed_tools, field_name="allowed_tools")
        _bounded_strings(self.allowed_artifacts, field_name="allowed_artifacts")
        budgets = _mapping(self.budgets, field_name="authorization_budgets", maximum_bytes=8_192)
        if any(
            not str(name).strip() or isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
            for name, value in budgets.items()
        ):
            raise TemporalContractError("invalid_authorization_budget", "authorization budget is invalid")
        if len(self.allowed_provider_bindings) > 8 or len(self.provider_attempt_plan) > 8:
            raise TemporalContractError(
                "invalid_provider_authorization",
                "provider authorization is invalid",
            )
        try:
            for item in self.allowed_provider_bindings:
                item.validate()
            for item in self.provider_attempt_plan:
                item.validate()
        except (AttributeError, ValueError) as exc:
            raise TemporalContractError(
                "invalid_provider_authorization",
                "provider authorization is invalid",
            ) from exc
        if self.provider_attempt_plan:
            allowed = {item.binding_id: item for item in self.allowed_provider_bindings}
            planned = {item.binding_id: item.binding_authorization for item in self.provider_attempt_plan}
            if (
                set(allowed) != set(planned)
                or any(allowed[key] != planned[key] for key in planned)
                or self.budgets.get("provider_attempts")
                != sum(item.maximum_attempts for item in self.provider_attempt_plan)
            ):
                raise TemporalContractError(
                    "invalid_provider_attempt_plan",
                    "provider attempt plan is invalid",
                )

    @classmethod
    def from_mapping(cls, raw: object) -> "AuthorizationEnvelopeRef":
        if not isinstance(raw, Mapping):
            raise TemporalContractError("authorization_required", "authorization envelope is required")
        return cls(
            schema=str(raw.get("schema") or ""),
            envelope_id=str(raw.get("envelope_id") or ""),
            tenant_id=str(raw.get("tenant_id") or ""),
            workflow_id=str(raw.get("workflow_id") or ""),
            run_id=str(raw.get("run_id") or ""),
            step_id=str(raw.get("step_id") or ""),
            plan_hash=str(raw.get("plan_hash") or ""),
            policy_version=str(raw.get("policy_version") or ""),
            allowed_tools=_bounded_strings(raw.get("allowed_tools"), field_name="allowed_tools"),
            allowed_artifacts=_bounded_strings(raw.get("allowed_artifacts"), field_name="allowed_artifacts"),
            budgets=_mapping(raw.get("budgets"), field_name="authorization_budgets", maximum_bytes=8_192),
            issued_at=float(raw.get("issued_at") or 0),
            expires_at=float(raw.get("expires_at") or 0),
            nonce=str(raw.get("nonce") or ""),
            key_id=str(raw.get("key_id") or ""),
            signature=str(raw.get("signature") or ""),
            allowed_provider_bindings=tuple(
                ProviderBindingAuthorization.from_mapping(item)
                for item in _bounded_contract_items(
                    raw.get("allowed_provider_bindings"),
                    field_name="allowed_provider_bindings",
                )
            ),
            provider_attempt_plan=tuple(
                ProviderProfileAttemptPlanEntry.from_mapping(item)
                for item in _bounded_contract_items(
                    raw.get("provider_attempt_plan"),
                    field_name="provider_attempt_plan",
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["allowed_tools"] = list(self.allowed_tools)
        payload["allowed_artifacts"] = list(self.allowed_artifacts)
        payload["budgets"] = dict(self.budgets)
        if self.allowed_provider_bindings:
            payload["allowed_provider_bindings"] = [item.to_dict() for item in self.allowed_provider_bindings]
        else:
            payload.pop("allowed_provider_bindings", None)
        if self.provider_attempt_plan:
            payload["provider_attempt_plan"] = [item.to_dict() for item in self.provider_attempt_plan]
        else:
            payload.pop("provider_attempt_plan", None)
        return payload

    def validate_binding(
        self,
        *,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        plan_hash: str,
    ) -> None:
        expected = (tenant_id, workflow_id, run_id, step_id, plan_hash)
        actual = (self.tenant_id, self.workflow_id, self.run_id, self.step_id, self.plan_hash)
        if actual != expected:
            raise TemporalContractError("authorization_binding_mismatch", "authorization envelope binding is stale")


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


@dataclass(frozen=True)
class WorkflowCommand:
    command_id: str
    command_type: WorkflowCommandType
    tenant_id: str
    workflow_id: str
    run_id: str
    step_id: str
    checkpoint_id: str
    expected_revision: int
    plan_hash: str
    policy_version: str
    actor_id: str
    actor_roles: tuple[str, ...]
    payload: Mapping[str, Any] = field(default_factory=dict)
    issued_at: float = 0.0
    expires_at: float = 0.0
    nonce: str = ""
    signature_algorithm: str = ""
    key_id: str = ""
    payload_digest: str = ""
    signature: str = ""
    schema: str = COMMAND_SCHEMA

    def __post_init__(self) -> None:
        if self.schema not in {LEGACY_COMMAND_SCHEMA, COMMAND_SCHEMA}:
            raise TemporalContractError("unsupported_command_schema", "workflow command schema is unsupported")
        try:
            raw_command_type = object.__getattribute__(self, "command_type")
            normalized_type = (
                raw_command_type
                if isinstance(raw_command_type, WorkflowCommandType)
                else WorkflowCommandType(str(raw_command_type))
            )
        except ValueError as exc:
            raise TemporalContractError("invalid_command_type", "workflow command type is unsupported") from exc
        object.__setattr__(self, "command_type", normalized_type)
        for field_name, value in (
            ("command_id", self.command_id),
            ("tenant_id", self.tenant_id),
            ("workflow_id", self.workflow_id),
            ("run_id", self.run_id),
            ("step_id", self.step_id),
            ("checkpoint_id", self.checkpoint_id),
            ("policy_version", self.policy_version),
            ("actor_id", self.actor_id),
            ("nonce", self.nonce),
            ("key_id", self.key_id),
        ):
            _identifier(value, field_name=field_name)
        if not _DIGEST_RE.fullmatch(self.plan_hash):
            raise TemporalContractError("invalid_plan_hash", "workflow command plan_hash must be sha256")
        expected_revision, issued_at, expires_at = _workflow_command_numeric_fields(
            {
                "expected_revision": self.expected_revision,
                "issued_at": self.issued_at,
                "expires_at": self.expires_at,
            }
        )
        object.__setattr__(self, "expected_revision", expected_revision)
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)
        _bounded_strings(self.actor_roles, field_name="actor_roles", maximum=64)
        _mapping(self.payload, field_name="command_payload", maximum_bytes=65_536)
        if _contains_sensitive_keys(self.payload):
            raise TemporalContractError("embedded_secret_denied", "command payload contains a secret")
        if object.__getattribute__(self, "command_type") in {
            WorkflowCommandType.EDIT,
            WorkflowCommandType.REQUEST_CHANGES,
        }:
            if not (self.payload.get("plan_ref") or self.payload.get("replacement_plan")):
                raise TemporalContractError("plan_edit_required", "plan edit payload is required")
            if not _DIGEST_RE.fullmatch(str(self.payload.get("replacement_plan_hash") or "")):
                raise TemporalContractError(
                    "replacement_plan_hash_required",
                    "replacement plan hash must be sha256",
                )
        if self.schema == LEGACY_COMMAND_SCHEMA:
            if self.signature_algorithm or self.payload_digest:
                raise TemporalContractError(
                    "legacy_command_authority_fields_forbidden",
                    "legacy workflow commands cannot carry v3 authority fields",
                )
            if not (_valid_hmac_signature(self.signature) or _valid_ed25519_signature(self.signature)):
                raise TemporalContractError("invalid_command_signature", "workflow command signature is invalid")
            return
        if self.signature_algorithm not in _COMMAND_SIGNATURE_ALGORITHMS:
            raise TemporalContractError(
                "unsupported_command_signature_algorithm",
                "workflow command signature algorithm is unsupported",
            )
        expected_digest = self.computed_payload_digest()
        if not hmac.compare_digest(str(self.payload_digest), expected_digest):
            raise TemporalContractError(
                "invalid_command_payload_digest",
                "workflow command payload digest is invalid",
            )
        if self.signature_algorithm == "ed25519":
            valid_signature = _valid_ed25519_signature(self.signature)
        else:
            valid_signature = _valid_hmac_signature(self.signature)
        if not valid_signature:
            raise TemporalContractError("invalid_command_signature", "workflow command signature is invalid")

    @classmethod
    def from_mapping(cls, raw: object, *, default_type: str = "") -> "WorkflowCommand":
        if not isinstance(raw, Mapping):
            raise TemporalContractError("invalid_command", "workflow command must be an object")
        expected_revision, issued_at, expires_at = cls.parse_numeric_fields(raw)
        try:
            command_type = WorkflowCommandType(str(raw.get("command_type") or default_type or ""))
        except ValueError as exc:
            raise TemporalContractError("invalid_command_type", "workflow command type is unsupported") from exc
        return cls(
            schema=str(raw.get("schema") or ""),
            command_id=str(raw.get("command_id") or ""),
            command_type=command_type,
            tenant_id=str(raw.get("tenant_id") or ""),
            workflow_id=str(raw.get("workflow_id") or ""),
            run_id=str(raw.get("run_id") or ""),
            step_id=str(raw.get("step_id") or ""),
            checkpoint_id=str(raw.get("checkpoint_id") or ""),
            expected_revision=expected_revision,
            plan_hash=str(raw.get("plan_hash") or ""),
            policy_version=str(raw.get("policy_version") or ""),
            actor_id=str(raw.get("actor_id") or ""),
            actor_roles=_bounded_strings(raw.get("actor_roles"), field_name="actor_roles", maximum=64),
            payload=_mapping(raw.get("payload"), field_name="command_payload", maximum_bytes=65_536),
            issued_at=issued_at,
            expires_at=expires_at,
            nonce=str(raw.get("nonce") or ""),
            signature_algorithm=str(raw.get("signature_algorithm") or "").strip().lower(),
            key_id=str(raw.get("key_id") or ""),
            payload_digest=str(raw.get("payload_digest") or ""),
            signature=str(raw.get("signature") or ""),
        )

    @staticmethod
    def parse_numeric_fields(raw: Mapping[str, Any]) -> tuple[int, float, float]:
        """Normalize the three command numerics for neutral and Hub adapters."""

        return _workflow_command_numeric_fields(raw)

    @staticmethod
    def parse_issued_at(value: object) -> float:
        """Normalize a command issuance timestamp before Hub arithmetic."""

        return _bounded_float(
            value,
            field_name="issued_at",
            minimum=0.0,
            maximum=float(_COMMAND_MAX_SAFE_INTEGER),
            reason_code="invalid_command_issued_at",
        )

    @classmethod
    def unsigned_v3_mapping(
        cls,
        *,
        command_id: str,
        command_type: str,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        step_id: str,
        checkpoint_id: str,
        expected_revision: int,
        plan_hash: str,
        policy_version: str,
        actor_id: str,
        actor_roles: Sequence[str],
        payload: Mapping[str, Any],
        issued_at: float,
        expires_at: float,
        nonce: str,
        signature_algorithm: str,
        key_id: str,
    ) -> dict[str, Any]:
        """Build the exact v3 bytes-to-sign without creating an unsigned DTO."""

        try:
            normalized_type = WorkflowCommandType(str(command_type)).value
        except ValueError as exc:
            raise TemporalContractError(
                "invalid_command_type",
                "workflow command type is unsupported",
            ) from exc
        normalized_revision, normalized_issued_at, normalized_expires_at = _workflow_command_numeric_fields(
            {
                "expected_revision": expected_revision,
                "issued_at": issued_at,
                "expires_at": expires_at,
            }
        )
        mapping: dict[str, Any] = {
            "schema": COMMAND_SCHEMA,
            "command_id": str(command_id),
            "command_type": normalized_type,
            "tenant_id": str(tenant_id),
            "workflow_id": str(workflow_id),
            "run_id": str(run_id),
            "step_id": str(step_id),
            "checkpoint_id": str(checkpoint_id),
            "expected_revision": normalized_revision,
            "plan_hash": str(plan_hash),
            "policy_version": str(policy_version),
            "actor_id": str(actor_id),
            "actor_roles": [str(value) for value in actor_roles],
            "payload": _mapping(payload, field_name="command_payload", maximum_bytes=65_536),
            "issued_at": normalized_issued_at,
            "expires_at": normalized_expires_at,
            "nonce": str(nonce),
            "signature_algorithm": str(signature_algorithm).strip().lower(),
            "key_id": str(key_id),
        }
        mapping["payload_digest"] = _workflow_command_payload_digest(mapping)
        return mapping

    def semantic_payload(self) -> dict[str, Any]:
        return _workflow_command_semantic_payload(self.to_dict())

    def computed_payload_digest(self) -> str:
        return _workflow_command_payload_digest(self.to_dict())

    @staticmethod
    def semantic_payload_for_mapping(raw: Mapping[str, Any]) -> dict[str, Any]:
        """Expose the version-neutral semantic body to Hub-side adapters."""

        return _workflow_command_semantic_payload(raw)

    @staticmethod
    def payload_digest_for_mapping(raw: Mapping[str, Any]) -> str:
        """Compute semantic identity without imposing Temporal ID rules."""

        return _workflow_command_payload_digest(raw)

    def signing_payload(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload.pop("signature", None)
        return payload

    def to_dict(self, *, redacted: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": self.schema,
            "command_id": self.command_id,
            "command_type": object.__getattribute__(self, "command_type").value,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "checkpoint_id": self.checkpoint_id,
            "expected_revision": self.expected_revision,
            "plan_hash": self.plan_hash,
            "policy_version": self.policy_version,
            "actor_id": self.actor_id,
            "actor_roles": list(self.actor_roles),
            "payload": dict(self.payload),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "nonce": self.nonce,
            "key_id": self.key_id,
            "signature": self.signature,
        }
        if self.schema == COMMAND_SCHEMA:
            result["signature_algorithm"] = self.signature_algorithm
            result["payload_digest"] = self.payload_digest
        if redacted:
            result["nonce"] = "[REDACTED]"
            result["signature"] = "[REDACTED]"
        return result


def _workflow_command_semantic_payload(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable command body shared across signature renewals."""

    return {
        "schema": _COMMAND_SEMANTIC_PAYLOAD_SCHEMA,
        "command_id": str(raw.get("command_id") or ""),
        "command_type": str(raw.get("command_type") or ""),
        "tenant_id": str(raw.get("tenant_id") or ""),
        "workflow_id": str(raw.get("workflow_id") or ""),
        "run_id": str(raw.get("run_id") or ""),
        "step_id": str(raw.get("step_id") or ""),
        "checkpoint_id": str(raw.get("checkpoint_id") or ""),
        "expected_revision": _bounded_integer(
            raw.get("expected_revision"),
            field_name="expected_revision",
            minimum=0,
            maximum=_COMMAND_MAX_SAFE_INTEGER,
            reason_code="invalid_command_revision",
        ),
        "plan_hash": str(raw.get("plan_hash") or ""),
        "policy_version": str(raw.get("policy_version") or ""),
        "actor_id": str(raw.get("actor_id") or ""),
        "actor_roles": list(raw.get("actor_roles") or ()),
        "payload": dict(raw.get("payload") or {}),
    }


def _workflow_command_payload_digest(raw: Mapping[str, Any]) -> str:
    try:
        canonical = json.dumps(
            _workflow_command_semantic_payload(raw),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TemporalContractError(
            "invalid_command_payload_digest",
            "workflow command payload is not canonical JSON",
        ) from exc
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _valid_hmac_signature(value: object) -> bool:
    return bool(re.fullmatch(r"[a-fA-F0-9]{64}", str(value or "")))


def _valid_ed25519_signature(value: object) -> bool:
    try:
        decoded = base64.b64decode(str(value or "").encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        return False
    return len(decoded) == 64


@dataclass(frozen=True)
class WorkflowCommandAuthorityResult:
    """Deterministic proof returned by the worker's crypto Local Activity."""

    accepted: bool
    command_id: str = ""
    payload_digest: str = ""
    signature_algorithm: str = ""
    key_id: str = ""
    reason_code: str = ""
    schema: str = COMMAND_AUTHORITY_RESULT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != COMMAND_AUTHORITY_RESULT_SCHEMA:
            raise TemporalContractError(
                "command_authority_result_schema_unsupported",
                "command authority result schema is unsupported",
            )
        if self.accepted:
            if (
                not _IDENTIFIER_RE.fullmatch(self.command_id)
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", self.payload_digest)
                or self.signature_algorithm != "ed25519"
                or not _IDENTIFIER_RE.fullmatch(self.key_id)
                or self.reason_code
            ):
                raise TemporalContractError(
                    "command_authority_result_invalid",
                    "accepted command authority result is incomplete",
                )
        elif not re.fullmatch(r"[a-z][a-z0-9_]{0,255}", self.reason_code):
            raise TemporalContractError(
                "command_authority_result_invalid",
                "rejected command authority result requires a stable reason",
            )

    @classmethod
    def from_mapping(cls, raw: object) -> "WorkflowCommandAuthorityResult":
        if not isinstance(raw, Mapping):
            raise TemporalContractError(
                "command_authority_result_invalid",
                "command authority result must be an object",
            )
        return cls(
            schema=str(raw.get("schema") or ""),
            accepted=raw.get("accepted") is True,
            command_id=str(raw.get("command_id") or ""),
            payload_digest=str(raw.get("payload_digest") or ""),
            signature_algorithm=str(raw.get("signature_algorithm") or ""),
            key_id=str(raw.get("key_id") or ""),
            reason_code=str(raw.get("reason_code") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkflowCommandResult:
    command_id: str
    accepted: bool
    revision: int
    status: str
    reason_code: str = ""
    schema: str = COMMAND_RESULT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkflowStatus:
    workflow_id: str
    run_id: str
    status: str
    revision: int
    current_step_id: str
    completed_step_ids: tuple[str, ...]
    retry_budget_remaining: int
    checkpoint_ref: str
    open_gates: tuple[str, ...]
    reason_code: str = ""
    parameters: Mapping[str, Any] = field(default_factory=dict)
    plan_hash: str = ""
    plan_revision: int = 1
    plan_ref: str = ""
    active_step_ids: tuple[str, ...] = ()
    failed_step_ids: tuple[str, ...] = ()
    schema: str = STATUS_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "status": self.status,
            "revision": self.revision,
            "current_step_id": self.current_step_id,
            "completed_step_ids": list(self.completed_step_ids),
            "retry_budget_remaining": self.retry_budget_remaining,
            "checkpoint_ref": self.checkpoint_ref,
            "open_gates": list(self.open_gates),
            "reason_code": self.reason_code,
            "parameters": redact_mapping(self.parameters),
            "plan_hash": self.plan_hash,
            "plan_revision": self.plan_revision,
            "plan_ref": self.plan_ref,
            "active_step_ids": list(self.active_step_ids),
            "failed_step_ids": list(self.failed_step_ids),
        }


@dataclass(frozen=True)
class ProbeRequest:
    request_id: str
    value: str = "probe"
    schema: str = PROBE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != PROBE_SCHEMA:
            raise TemporalContractError("unsupported_probe_schema", "probe schema is unsupported")
        _identifier(self.request_id, field_name="request_id")
        if len(self.value) > 128 or "\x00" in self.value:
            raise TemporalContractError("invalid_probe_value", "probe value is invalid")


__all__ = [
    "ACTIVITY_INPUT_SCHEMA",
    "ACTIVITY_RESULT_SCHEMA",
    "ActivityClass",
    "AnantaWorkflowInput",
    "ArtifactReference",
    "AuthorizationEnvelopeRef",
    "COMMAND_AUTHORITY_ACTIVITY",
    "COMMAND_AUTHORITY_RESULT_SCHEMA",
    "COMMAND_RESULT_SCHEMA",
    "COMMAND_SCHEMA",
    "LEGACY_COMMAND_SCHEMA",
    "ProbeRequest",
    "STATUS_SCHEMA",
    "StepActivityInput",
    "StepActivityResult",
    "TemporalContractError",
    "TemporalWorkflowStep",
    "WorkflowCommand",
    "WorkflowCommandAuthorityResult",
    "WorkflowCommandResult",
    "WorkflowCommandType",
    "WorkflowPhase",
    "WorkflowStatus",
    "redact_mapping",
]
