"""Runtime-neutral wire contracts for one Hub-delegated Native graph node."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agent.services.workflow_runtime._serialization import contains_sensitive_keys, redact_json
from agent.services.workflow_runtime.execution_plan import ExecutionNode
from agent.services.workflow_runtime.security import RuntimeAuthorizationEnvelope
from ananta_contracts.provider_execution import (
    ProviderBindingAuthorization,
    ProviderExecutionBinding,
    ProviderProfileAttemptPlanEntry,
    ProviderProfileExecutionBinding,
)
from ananta_contracts.provider_invocation import (
    ProviderInvocationBlocked,
    ProviderInvocationContext,
)

NATIVE_NODE_COMMAND_SCHEMA = "ananta.native_node_command.v1"
NATIVE_NODE_RESULT_SCHEMA = "ananta.native_node_result.v1"
_REASON_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:,-]{0,159}$")


@dataclass(frozen=True)
class NativeNodeCommand:
    command_id: str
    control_task_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    plan_hash: str
    policy_version: str
    node: ExecutionNode
    authorization: RuntimeAuthorizationEnvelope
    attempt_id: str
    fencing_token: int
    input_data: dict[str, Any] = field(default_factory=dict)
    artifact_refs: dict[str, str] = field(default_factory=dict)
    operation_id: str = ""
    side_effect_revision: int = 0
    provider_binding: ProviderExecutionBinding | None = None
    primary_profile_id: str = ""
    provider_profile_bindings: tuple[ProviderProfileExecutionBinding, ...] = ()
    provider_attempt_plan: tuple[ProviderProfileAttemptPlanEntry, ...] = ()
    provider_maximum_attempts: int = 0
    provider_context: dict[str, Any] = field(default_factory=dict)
    provider_contexts_by_profile_id: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )
    schema: str = NATIVE_NODE_COMMAND_SCHEMA

    def assert_valid(self) -> None:
        required = (
            self.command_id,
            self.control_task_id,
            self.tenant_id,
            self.workflow_id,
            self.run_id,
            self.plan_hash,
            self.policy_version,
            self.node.node_id,
            self.attempt_id,
        )
        if any(not value for value in required):
            raise ValueError("native_node_command_binding_required")
        if self.schema != NATIVE_NODE_COMMAND_SCHEMA:
            raise ValueError("native_node_command_schema_unsupported")
        if self.fencing_token < 1:
            raise ValueError("native_node_command_fencing_invalid")
        if contains_sensitive_keys(self.input_data):
            raise ValueError("native_node_command_embedded_secret_denied")
        if native_node_requires_provider(self.node) and self.provider_binding is None:
            raise ValueError("native_node_provider_binding_required")
        if self.provider_binding is not None:
            self.provider_binding.validate()
        self._assert_provider_profiles_valid()
        if self.node.side_effect_class in {"none", "read"}:
            if self.operation_id or self.side_effect_revision:
                raise ValueError("native_node_command_unexpected_side_effect")
        elif not self.operation_id or self.side_effect_revision < 2:
            raise ValueError("native_node_command_side_effect_authorization_required")

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "NativeNodeCommand":
        value = cls(
            command_id=str(raw.get("command_id") or ""),
            control_task_id=str(raw.get("control_task_id") or ""),
            tenant_id=str(raw.get("tenant_id") or ""),
            workflow_id=str(raw.get("workflow_id") or ""),
            run_id=str(raw.get("run_id") or ""),
            plan_hash=str(raw.get("plan_hash") or ""),
            policy_version=str(raw.get("policy_version") or ""),
            node=ExecutionNode.from_mapping(dict(raw.get("node") or {})),
            authorization=RuntimeAuthorizationEnvelope.from_mapping(
                dict(raw.get("authorization") or {})
            ),
            attempt_id=str(raw.get("attempt_id") or ""),
            fencing_token=int(raw.get("fencing_token") or 0),
            input_data=dict(raw.get("input_data") or {}),
            artifact_refs={
                str(key): str(item) for key, item in dict(raw.get("artifact_refs") or {}).items()
            },
            operation_id=str(raw.get("operation_id") or ""),
            side_effect_revision=int(raw.get("side_effect_revision") or 0),
            provider_binding=(
                ProviderExecutionBinding.from_mapping(raw["provider_binding"])
                if raw.get("provider_binding") is not None
                else None
            ),
            primary_profile_id=str(
                raw.get("primary_profile_id") or ""
            ).strip(),
            provider_profile_bindings=tuple(
                ProviderProfileExecutionBinding.from_mapping(item)
                for item in _profile_binding_items(
                    raw.get("provider_profile_bindings")
                )
            ),
            provider_attempt_plan=tuple(
                ProviderProfileAttemptPlanEntry.from_mapping(item)
                for item in _profile_binding_items(
                    raw.get("provider_attempt_plan")
                )
            ),
            provider_maximum_attempts=int(
                raw.get("provider_maximum_attempts") or 0
            ),
            provider_context=dict(raw.get("provider_context") or {}),
            provider_contexts_by_profile_id={
                str(key): dict(item)
                for key, item in _profile_context_items(
                    raw.get("provider_contexts_by_profile_id")
                ).items()
            },
            schema=str(raw.get("schema") or NATIVE_NODE_COMMAND_SCHEMA),
        )
        value.assert_valid()
        return value

    def to_dict(self, *, redacted: bool = False) -> dict[str, Any]:
        self.assert_valid()
        return {
            "schema": self.schema,
            "command_id": self.command_id,
            "control_task_id": self.control_task_id,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "plan_hash": self.plan_hash,
            "policy_version": self.policy_version,
            "node": self.node.to_dict(),
            "authorization": self.authorization.to_dict(redacted=redacted),
            "attempt_id": self.attempt_id,
            "fencing_token": self.fencing_token,
            "input_data": dict(redact_json(self.input_data)),
            "artifact_refs": dict(sorted(self.artifact_refs.items())),
            "operation_id": self.operation_id,
            "side_effect_revision": self.side_effect_revision,
            "provider_binding": (
                self.provider_binding.to_dict() if self.provider_binding else None
            ),
            "primary_profile_id": self.primary_profile_id,
            "provider_profile_bindings": [
                item.to_dict() for item in self.provider_profile_bindings
            ],
            "provider_attempt_plan": [
                item.to_dict() for item in self.provider_attempt_plan
            ],
            "provider_maximum_attempts": self.provider_maximum_attempts,
            "provider_context": dict(self.provider_context),
            "provider_contexts_by_profile_id": {
                key: dict(item)
                for key, item in sorted(
                    self.provider_contexts_by_profile_id.items()
                )
            },
        }

    def _assert_provider_profiles_valid(self) -> None:
        if len(self.provider_profile_bindings) > 8:
            raise ValueError("provider_profile_binding_limit_exceeded")
        if not self.provider_profile_bindings:
            if (
                self.primary_profile_id
                or self.provider_attempt_plan
                or self.provider_context
                or self.provider_contexts_by_profile_id
                or self.provider_maximum_attempts
            ):
                raise ValueError("native_provider_profile_transport_unbound")
            return
        if self.provider_binding is None or not self.primary_profile_id:
            raise ValueError("provider_primary_profile_binding_missing")
        if (
            len(self.provider_attempt_plan)
            != len(self.provider_profile_bindings)
            or sum(
                item.maximum_attempts
                for item in self.provider_attempt_plan
            )
            != self.provider_maximum_attempts
            or not (
                len(self.provider_profile_bindings)
                <= self.provider_maximum_attempts
                <= 33
            )
        ):
            raise ValueError("provider_profile_retry_budget_invalid")
        bindings: dict[str, ProviderExecutionBinding] = {}
        for item in self.provider_profile_bindings:
            item.validate()
            if item.profile_id in bindings:
                raise ValueError("provider_profile_binding_duplicate")
            bindings[item.profile_id] = item.binding
        if bindings.get(self.primary_profile_id) != self.provider_binding:
            raise ValueError("provider_primary_profile_binding_mismatch")
        if tuple(
            item.profile_id for item in self.provider_attempt_plan
        ) != tuple(
            item.profile_id for item in self.provider_profile_bindings
        ):
            raise ValueError("provider_attempt_plan_order_mismatch")
        for item in self.provider_attempt_plan:
            item.validate()
            binding = bindings.get(item.profile_id)
            if (
                binding is None
                or item.binding_id != binding.binding_id
                or item.provider_id != binding.provider_id
                or item.model_id != binding.model_id
                or item.endpoint_identity != binding.endpoint_identity
            ):
                raise ValueError("provider_attempt_plan_binding_mismatch")
        expected_authorizations = tuple(
            sorted(
                (
                    ProviderBindingAuthorization.from_binding(item.binding)
                    for item in self.provider_profile_bindings
                ),
                key=lambda item: (
                    item.binding_id,
                    item.provider_id,
                    item.model_id,
                ),
            )
        )
        if (
            self.authorization.allowed_provider_bindings
            != expected_authorizations
            or self.authorization.provider_attempt_plan
            != self.provider_attempt_plan
        ):
            raise ValueError("native_provider_authorization_mismatch")
        if set(self.provider_contexts_by_profile_id) != set(bindings):
            raise ValueError("native_provider_profile_contexts_mismatch")
        try:
            primary = ProviderInvocationContext.from_value(
                self.provider_context
            )
            primary.assert_valid()
        except ProviderInvocationBlocked as exc:
            raise ValueError(exc.reason_code) from exc
        if (
            primary.tenant_id != self.tenant_id
            or primary.workflow_id != self.workflow_id
            or primary.run_id != self.run_id
            or primary.step_id != self.node.node_id
            or primary.plan_hash != self.plan_hash
            or primary.policy_version != self.policy_version
            or primary.attempt_id != self.attempt_id
            or primary.fencing_token != self.fencing_token
            or primary.authorization_envelope != self.authorization.to_dict()
            or primary.retry_attempt != 0
            or primary.retry_id
            or primary.max_attempts != self.provider_maximum_attempts
            or primary.provider_profile_id != self.primary_profile_id
            or not primary.require_hub_provider_attempt_budget
            or primary.require_hub_retry_budget
            or primary.combined_retry_maximum != 0
        ):
            raise ValueError("native_provider_context_scope_mismatch")
        parsed: dict[str, ProviderInvocationContext] = {}
        for profile_id, binding in bindings.items():
            try:
                context = ProviderInvocationContext.from_value(
                    self.provider_contexts_by_profile_id[profile_id]
                )
                context.assert_valid()
            except ProviderInvocationBlocked as exc:
                raise ValueError(exc.reason_code) from exc
            if (
                context.selected_provider_id != binding.provider_id
                or context.selected_model_id != binding.model_id
                or context.provider_binding_id != binding.binding_id
                or context.provider_endpoint_identity
                != binding.endpoint_identity
                or context.provider_profile_id != profile_id
                or not context.require_hub_provider_budget
                or context.provider_transport_mode != "hub_bound"
            ):
                raise ValueError(
                    "native_provider_profile_binding_mismatch"
                )
            _assert_same_provider_scope(primary, context)
            parsed[profile_id] = context
        if parsed.get(self.primary_profile_id) != primary:
            raise ValueError("native_primary_provider_context_mismatch")


def _profile_binding_items(raw: object) -> tuple[object, ...]:
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise ValueError("provider_profile_bindings_invalid")
    return tuple(raw)


def _profile_context_items(raw: object) -> dict[object, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or len(raw) > 8:
        raise ValueError("provider_profile_contexts_invalid")
    if any(not isinstance(item, dict) for item in raw.values()):
        raise ValueError("provider_profile_contexts_invalid")
    return dict(raw)


def _assert_same_provider_scope(
    primary: ProviderInvocationContext,
    candidate: ProviderInvocationContext,
) -> None:
    fields = (
        "tenant_id",
        "run_id",
        "workflow_id",
        "step_id",
        "plan_hash",
        "attempt_id",
        "fencing_token",
        "policy_version",
        "prompt_version",
        "correlation_id",
        "external_egress_allowed",
        "secret_refs",
        "max_attempts",
        "max_total_tokens",
        "max_cost_micros",
        "deadline_epoch_seconds",
        "max_completion_tokens_per_call",
        "estimated_cost_micros_per_1000_tokens",
        "cache_enabled",
        "require_hub_retry_budget",
        "require_hub_provider_attempt_budget",
        "combined_retry_maximum",
        "retry_attempt",
        "retry_id",
        "authorization_envelope",
    )
    if any(
        getattr(primary, field) != getattr(candidate, field)
        for field in fields
    ):
        raise ValueError("native_provider_profile_context_scope_mismatch")


def native_node_requires_provider(node: ExecutionNode) -> bool:
    """Provider transport is explicit in the Hub-approved plan contract."""

    metadata = dict(node.metadata or {})
    mode = str(metadata.get("provider_transport") or "").strip().lower()
    if mode == "required" or metadata.get("provider_required") is True:
        return True
    return bool(
        set(node.required_capabilities)
        & {"llm", "model_inference", "text_generation"}
    )


@dataclass(frozen=True)
class NativeNodeResult:
    result_id: str
    command_id: str
    hub_task_id: str
    tenant_id: str
    workflow_id: str
    run_id: str
    node_id: str
    attempt_id: str
    fencing_token: int
    status: str
    output_data: dict[str, Any] = field(default_factory=dict)
    artifact_refs: dict[str, str] = field(default_factory=dict)
    budget_usage: dict[str, int | float] = field(default_factory=dict)
    reason_code: str = ""
    side_effect_status: str = ""
    schema: str = NATIVE_NODE_RESULT_SCHEMA

    def assert_valid(self) -> None:
        required = (
            self.result_id,
            self.command_id,
            self.hub_task_id,
            self.tenant_id,
            self.workflow_id,
            self.run_id,
            self.node_id,
            self.attempt_id,
        )
        if any(not value for value in required):
            raise ValueError("native_node_result_binding_required")
        if self.schema != NATIVE_NODE_RESULT_SCHEMA:
            raise ValueError("native_node_result_schema_unsupported")
        if self.status not in {"completed", "failed", "cancelled"}:
            raise ValueError("native_node_result_status_invalid")
        if self.fencing_token < 1:
            raise ValueError("native_node_result_fencing_invalid")
        if contains_sensitive_keys(self.output_data):
            raise ValueError("native_node_result_embedded_secret_denied")
        if self.reason_code and not _REASON_CODE.fullmatch(self.reason_code):
            raise ValueError("native_node_result_reason_code_invalid")
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
            for value in self.budget_usage.values()
        ):
            raise ValueError("native_node_result_budget_invalid")

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "NativeNodeResult":
        value = cls(
            result_id=str(raw.get("result_id") or ""),
            command_id=str(raw.get("command_id") or ""),
            hub_task_id=str(raw.get("hub_task_id") or ""),
            tenant_id=str(raw.get("tenant_id") or ""),
            workflow_id=str(raw.get("workflow_id") or ""),
            run_id=str(raw.get("run_id") or ""),
            node_id=str(raw.get("node_id") or ""),
            attempt_id=str(raw.get("attempt_id") or ""),
            fencing_token=int(raw.get("fencing_token") or 0),
            status=str(raw.get("status") or ""),
            output_data=dict(raw.get("output_data") or {}),
            artifact_refs={
                str(key): str(item) for key, item in dict(raw.get("artifact_refs") or {}).items()
            },
            budget_usage={
                str(key): item for key, item in dict(raw.get("budget_usage") or {}).items()
            },
            reason_code=str(raw.get("reason_code") or ""),
            side_effect_status=str(raw.get("side_effect_status") or ""),
            schema=str(raw.get("schema") or NATIVE_NODE_RESULT_SCHEMA),
        )
        value.assert_valid()
        return value

    def to_dict(self) -> dict[str, Any]:
        self.assert_valid()
        return {
            "schema": self.schema,
            "result_id": self.result_id,
            "command_id": self.command_id,
            "hub_task_id": self.hub_task_id,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "node_id": self.node_id,
            "attempt_id": self.attempt_id,
            "fencing_token": self.fencing_token,
            "status": self.status,
            "output_data": dict(redact_json(self.output_data)),
            "artifact_refs": dict(sorted(self.artifact_refs.items())),
            "budget_usage": dict(sorted(self.budget_usage.items())),
            "reason_code": self.reason_code,
            "side_effect_status": self.side_effect_status,
        }


@dataclass(frozen=True)
class HubTaskReceipt:
    hub_task_id: str
    command_id: str
    accepted: bool
    reason_code: str = ""
