"""Workflow/command/activity enumerations plus artifact and authorization-envelope references for Temporal contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from ananta_contracts.temporal_workflow_primitives import (
    _DIGEST_RE,
    TemporalContractError,
    _bounded_contract_items,
    _bounded_strings,
    _identifier,
    _mapping,
)

from .provider_execution import ProviderBindingAuthorization, ProviderProfileAttemptPlanEntry


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
    BPMN_MESSAGE = "bpmn_message"


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
